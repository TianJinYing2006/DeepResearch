"""上下文管理：隔离 + 压缩。

借鉴 LangChain open_deep_research 的上下文隔离与渐进式压缩思路。
管理海量检索结果，避免长任务上下文溢出，同时保留引用溯源。
"""
from __future__ import annotations

from typing import Any, List

from research_engine.llm.router import get_router
from research_engine.state import ResearchFinding


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

        # 按来源分组，每组压缩
        by_source: dict = {}
        for f in findings:
            by_source.setdefault(f.source, []).append(f)

        compressed: List[ResearchFinding] = []
        router = get_router()
        for source, group in by_source.items():
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
                compressed.append(
                    ResearchFinding(
                        content=summary,
                        source=source,
                        source_type=group[0].source_type,
                        confidence=max(f.confidence for f in group),
                        is_meta=any(f.is_meta for f in group),  # R2.4 Q5=A：压缩后不透传会丢标
                        metadata=meta,  # W4 Q5：metadata 不透传会丢 citation_count/retry_history
                    )
                )
            except Exception:  # noqa: BLE001
                compressed.extend(group)

        return compressed

    def format_for_writer(self, findings: List[ResearchFinding]) -> str:
        """将研究发现格式化为 Writer 可用的上下文文本。

        每个发现分配一个编号 [N]，Writer 用 [来源: N] 引用，Validator 再映射回真实来源。
        """
        lines = []
        for i, f in enumerate(findings, 1):
            meta = "，自指/方法论" if f.is_meta else ""  # R2.4：is_meta 仅用于方法论说明小节
            lines.append(f"[{i}] 来源: {f.source} (类型: {f.source_type}, 置信度: {f.confidence:.2f}{meta})")
            lines.append(f"    {f.content}")
        return "\n".join(lines)
