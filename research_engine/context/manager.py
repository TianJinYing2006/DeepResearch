"""上下文管理：隔离 + 压缩。

借鉴 LangChain open_deep_research 的上下文隔离与渐进式压缩思路。
管理海量检索结果，避免长任务上下文溢出，同时保留引用溯源。
"""
from __future__ import annotations

from typing import Any, List

from config import config
from research_engine.llm.router import get_router
from research_engine.state import ResearchFinding, SubQuestion
from research_engine.usage import UsageSinkError


class ContextManager:
    """管理研究发现，提供压缩与去重。"""

    def __init__(self, max_findings: int = 30):
        self.max_findings = max_findings

    def dedupe(self, findings: List[ResearchFinding]) -> List[ResearchFinding]:
        """按来源去重，保留置信度高的。"""
        seen: dict = {}
        for f in findings:
            key = f.source
            if key not in seen or f.confidence > seen[key].confidence:
                seen[key] = f
        return list(seen.values())

    def compress(self, findings: List[ResearchFinding], topic: str, state: Any = None) -> List[ResearchFinding]:
        """当发现过多时，用 fast LLM 压缩为保留引用的摘要。

        借鉴 ODR 的 compress_research：压缩但保留引用，供 Writer 使用。
        """
        if len(findings) <= self.max_findings:
            return findings

        # 审计 P1#3：按 (来源, 子问题) 分组压缩 —— 同一文件下不同子问题的材料
        # 不得混合（否则摘要跨子问题串接属性，并被错误归因到首个 sq_id）
        by_group: dict = {}
        for f in findings:
            by_group.setdefault((f.source, f.sq_id), []).append(f)

        compressed: List[ResearchFinding] = []
        router = get_router()
        for (source, group_sq_id), group in by_group.items():
            texts = "\n".join(f"- {f.content}" for f in group)
            system = "你是研究信息压缩助手。将以下关于同一来源的研究发现压缩为简洁摘要，保留关键事实与数字，不要丢失重要信息。"
            user = f"研究主题：{topic}\n\n来源：{source}\n\n内容：\n{texts}"
            try:
                summary = router.fast_chat(system, user, state=state)
                # W4 Q5：构造时透传 metadata（合并语义：citation_count 取 max / retry_history 拼接 / 其余键并集）
                meta: dict = {}
                for f in group:
                    for k, v in (f.metadata or {}).items():
                        if k == "citation_count" and isinstance(v, (int, float)):
                            meta[k] = max(meta.get(k, 0), v)
                        elif k == "retry_history" and isinstance(v, list):
                            meta[k] = meta.get(k, []) + v
                        else:
                            meta.setdefault(k, v)
                # Bug-3 兜底：组内 sq_id 已一致（P1#3 分组键），首个非空值即组归属
                merged_sq_id = next((f.sq_id for f in group if f.sq_id), group_sq_id)
                # F08（审计）：摘要携带原文证据链——Validator 据此回到原文层校验
                # （不再只读摘要；origin 缺失时回退摘要自身正文）
                origin_ids = [f.evidence_id for f in group if getattr(f, "evidence_id", "")]
                if origin_ids:
                    meta["origin_evidence_ids"] = origin_ids
                compressed.append(
                    ResearchFinding(
                        content=summary,
                        source=source,
                        source_type=group[0].source_type,
                        confidence=max(f.confidence for f in group),
                        is_meta=any(f.is_meta for f in group),  # R2.4 Q5=A：压缩后不透传会丢标
                        sq_id=merged_sq_id,
                        metadata=meta,  # W4 Q5：metadata 不透传会丢 citation_count/retry_history
                    )
                )
            except UsageSinkError:
                # F06：strict 记账失败必须上抛（不得静默改用未压缩原文）
                raise
            except Exception:  # noqa: BLE001
                compressed.extend(group)

        return compressed

    def format_for_writer(
        self,
        findings: List[ResearchFinding],
        subquestions: List[SubQuestion] | None = None,
    ) -> str:
        """将研究发现格式化为 Writer 可用的上下文文本（W7 Arm4 G2：按子问题分节）。

        每个发现以 "Finding N:" 标记编号，Writer 用 [来源: N] 引用，Validator 再映射回真实来源。
        关键约束：编号顺序与 ``findings`` 列表完全一致，禁止重排/删除，否则引用编号会错位。
        """
        subquestions = subquestions or []
        sq_map = {s.id: s.question for s in subquestions}

        # W7 Arm4：分节喂料可通过 WRITER_SECTIONED_FEED_ENABLED 关闭（TBD-8 基线对照）
        sectioned = config.experiment.writer_sectioned_feed_enabled

        lines: List[str] = []
        current_sq: str | None = None
        for i, f in enumerate(findings, 1):
            # L3 渲染防御（需求 16 / bug #75）：未知 sq_id 不再冒充「子问题」标题
            # （归「未分类材料」）；连续未知 id 合并为一组，避免每个幽灵 id 各开一节。
            group_key = f.sq_id if f.sq_id in sq_map else ""
            if sectioned and group_key != current_sq:
                header = sq_map.get(group_key) or "未分类材料"
                lines.append(f"\n--- 子问题：{header} ---")
                current_sq = group_key
            meta = "，自指/方法论" if f.is_meta else ""  # R2.4：is_meta 仅用于方法论说明小节
            # P0 引用协议统一：用 Finding N: 编号，彻底避免 #、[] 等符号被 LLM 模仿到引用中
            lines.append(f"Finding {i}: 来源: {f.source} (类型: {f.source_type}, 置信度: {f.confidence:.2f}{meta})")
            lines.append(f"    {f.content}")

        # G4 系统级兜底：无材料的子问题显式列出，强制 writer 看到"信息不足"义务
        if sectioned:
            found_sq = {f.sq_id for f in findings}
            missing = [s for s in subquestions if s.id not in found_sq]
            if missing:
                lines.append("\n--- 以下子问题暂无研究发现 ---")
                for s in missing:
                    lines.append(f"--- 子问题：{s.question} ---")
                    lines.append("（暂无研究发现）")

        return "\n".join(lines)
