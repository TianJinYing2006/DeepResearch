# -*- coding: utf-8 -*-
"""Validator Agent：引用校验（存在性 + 论断忠实度）+ 多源印证 + 置信度分级。

主卖点（防幻觉）的核心实现：
1. 引用存在性校验：报告中的每个引用是否真实存在于检索结果
2. 论断忠实度校验（W2 R2.3）：论断是否忠实于其所引用的 finding 内容
3. 多源交叉印证：关键论断需多个独立来源支持
4. 置信度分级：输出每条论断的置信度
5. 自指复核（W2 R2.4 Q5=A）：verdict 顺带输出 is_meta，兜底启发式漏标

W2 grill 落地（Q2/Q3/Q5/Q6）：
- _build_index 返回 {编号: (source, source_type)}，编号供 citation.finding_id 锚定（Q2=A）
- verified = 本地存在性 AND LLM 忠实度；存在性 False 短路不进 LLM（Q3=A）
- LLM 输入全量 findings（废 [:20] 截断，编号 >20 也可判忠实度）（Q3=A）
- LLM 输出按 finding_id 对齐构造 Citation，不再按序 zip（Q3=A，修错位隐患）
- verdict 增 is_meta 字段（R2.4 第二层复核兜底）
- citation 保留 existence 字段，供 CLI/DoD 双口径统计（Q6=A）

引用协议：Writer 用 [来源: N] 编号引用（N 是研究发现列表中的编号），
Validator 将编号映射回真实来源再做校验（ADR-0002/0005 不变）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from pydantic import BaseModel, Field

from config import config
from research_engine.llm.client import LLMClient
from research_engine.state import Citation, ResearchFinding

VALIDATOR_SYSTEM = """你是研究事实核查员。你的任务是校验报告中的论断与引用。

请以 JSON 格式输出校验结果：
{{
  "citations": [
    {{
      "finding_id": "引用编号（对应输入中的编号，非数字来源传空字符串）",
      "claim": "论断原文",
      "faithful": true/false,
      "supported": true/false,
      "confidence": 0.0-1.0,
      "is_meta": true/false,
      "note": "说明"
    }}
  ]
}}

判定规则：
- faithful: 论断是否被所引 finding 的内容真实支持（忠实于来源：不夸大、不曲解、不张冠李戴）
- supported: 论断是否被至少 {min_sources} 个独立来源支持（多源印证）
- is_meta: 该论断/来源是否在描述 DeepResearch 系统自身（自指/元描述，如"本系统""本Agent""Planner→Researcher→Writer→Validator"四节点编排等）——是则 true
- confidence: 综合置信度 0-1
- note: 说明；faithful=false 时必须给出具体原因
"""


class CitationVerdictItem(BaseModel):
    """单条引用的 LLM 校验 verdict（Q3=A 按 finding_id 对齐，Q5=A 增 is_meta 复核）。"""

    finding_id: str = Field(description="引用编号；非数字来源可传空字符串")
    claim: str = Field(description="论断原文")
    faithful: bool = Field(description="论断是否忠实于该 finding 内容")
    supported: bool = Field(default=False, description="是否通过多源印证")
    confidence: float = Field(default=0.5, ge=0.0, le=1.0, description="校验置信度 0-1")
    is_meta: bool = Field(default=False, description="是否自指/元描述（描述本系统自身）")
    note: str = Field(default="", description="说明；faithful=false 时必须写原因")


class CitationVerdict(BaseModel):
    """校验结果 schema（防模型漏 key 被静默默认值误判）。"""

    citations: List[CitationVerdictItem] = Field(default_factory=list)


class Validator:
    """引用校验器。"""

    def __init__(self):
        from research_engine.llm.router import get_router
        self.router = get_router()

    def _build_index(self, findings: List[ResearchFinding]) -> Dict[str, Tuple[str, str]]:
        """建立编号 -> (真实来源, 来源类型) 映射（Q2=A：带出 source_type，编号供 finding_id 锚定）。"""
        return {str(i): (f.source, f.source_type) for i, f in enumerate(findings, 1)}

    _CLAIM_SEP = "。！？；\n"          # 句子边界字符（W2.1）
    _CLAIM_MAX = 200                  # 引用前最多取 200 字符的窗口（W2.1）

    def _claim_text(self, report: str, end: int) -> str:
        """取引用前的一段文本做 claim（W2.1 待办：清理 markdown 残留、按句子边界截断）。

        - 优先对齐最近句子边界（。！？；\n），避免从单词中间硬截断；
        - 退化为 200 字符窗口；再退化 80 字符（保原行为兜底）；
        - 清理行内 markdown 残留（** ` # 行首 - | 等），纯展示层，不影响校验。
        """
        window = report[max(0, end - self._CLAIM_MAX):end]
        rel = max(window.rfind(c) for c in self._CLAIM_SEP)
        claim = window[rel + 1:] if rel >= 0 else window
        claim = re.sub(r"[*_`#]{1,3}", "", claim)                                  # ** 加粗/`代码`/# 标题
        claim = re.sub(r"^\s*[-|]\s*", "", claim, flags=re.M)                      # 行首 - 列表 / | 表格碎片
        claim = re.sub(r"\s{2,}", " ", claim).strip().replace("\n", " ")
        if not claim.strip():  # 边界切分到空（窗口尾恰为句号等）才退回 80 字符硬截断兜底
            start = max(0, end - 80)
            claim = report[start:end].strip().replace("\n", " ")
        return claim

    def _extract_citations(self, report: str) -> List[dict]:
        """从报告中提取 [来源: N] 形式的引用。

        支持多编号引用：[来源: 5, 72, 77] 会被拆分为 3 条独立引用，
        每条单独校验（见 ADR-0005）。仅当引用内容全部为数字 token 时
        才拆分；含非数字内容时视为单一来源字符串原样保留，保持对
        [来源: <真实URL>] 协议的兼容。
        """
        citations = []
        pattern = r"\[来源:\s*([^\]]+)\]"
        for m in re.finditer(pattern, report):
            # 取论断（引用前的一段文本，W2.1 按句子边界 + 清理 markdown）
            claim = self._claim_text(report, m.start())
            for ref in self._split_ref(m.group(1)):
                citations.append({"claim": claim, "source": ref})
        return citations

    @staticmethod
    def _split_ref(ref: str) -> List[str]:
        """拆分多编号引用：'5, 72, 77' -> ['5', '72', '77']。

        LLM 实际输出中常见 [来源: 5, 72, 77]、[来源: 5、8]、[来源: 3和7]
        等变体，统一按分隔符拆分。分隔符覆盖中英文逗号、顿号、分号、
        斜杠、空格及"和"字。只有当拆出的所有 token 都是纯数字时才视为
        多编号引用，否则整体作为单一来源字符串返回（兼容 URL 协议）。
        """
        tokens = [t for t in re.split(r"[,，、;；/和\s]+", ref.strip()) if t]
        if len(tokens) > 1 and all(re.fullmatch(r"\d+", t) for t in tokens):
            return tokens
        return [ref.strip()]

    def validate(self, report: str, findings: List[ResearchFinding], state: Any = None) -> List[Citation]:
        """校验报告引用。

        双段式（Q3=A）：
        1. 本地存在性（无 LLM）：编号映射到真实来源 + 来源存在性判定，存在性 False 短路；
        2. LLM 忠实度：仅存在性 True 的引用进 LLM，判定"论断是否忠实于被引 finding 内容"；
           verified = 存在性 AND 忠实度，输出按 finding_id 对齐。
        """
        extracted = self._extract_citations(report)
        index = self._build_index(findings)
        known = {src for src, _ in index.values()}

        # ---- 阶段 1：本地存在性（短路，不需要 LLM）----
        local_results: List[Dict[str, Any]] = []
        for c in extracted:
            ref = c["source"]
            # 支持 [来源: N] 编号 或 [来源: <真实来源>]（ADR-0002 协议）
            hit = index.get(ref)
            if hit is not None:
                real_source, source_type = hit
                finding_id = ref
            else:
                real_source, source_type = ref, ""
                # 越界编号（如 999）保留数字 finding_id 供附录展示；URL 等非数字来源留空（Q2）
                finding_id = ref if re.fullmatch(r"\d+", ref) else ""
            existence = real_source in known
            local_results.append({
                **c,
                "source": real_source,
                "source_type": source_type,
                "finding_id": finding_id,
                "existence": existence,
                "note": "" if existence else "来源不存在于研究发现（编号越界或来源未命中）",
            })

        if not local_results:
            return []

        # ---- 阶段 2：LLM 忠实度判定（仅存在性 True 的引用；Q3=A 短路）----
        to_check = [r for r in local_results if r["existence"]]
        # Q3=A：全量 findings 进 LLM（废 [:20] 截断），保证编号 21-30 的引用也能判忠实度
        findings_text = "\n".join(
            f"- [{i}] 来源: {f.source} (类型: {f.source_type}) {f.content[:300]}"
            for i, f in enumerate(findings, 1)
        )
        citations_json = "\n".join(
            f"- finding_id: {r['finding_id']} | claim: {r['claim'][:100]} | source: {r['source']}"
            for r in to_check
        )
        user = f"研究发现：\n{findings_text}\n\n待校验引用（仅列存在性已通过的）：\n{citations_json}\n\n请输出校验结果。"

        verdicts: Dict[str, Dict[str, Any]] = {}
        unused_verdicts: List[Dict[str, Any]] = []
        llm_failed = False
        if to_check:  # 全部存在性失败时零 LLM 调用（Q3 短路完整落地）
            try:
                # 与 critic.py 同款：Pydantic schema + 纠错重试，防漏 key 静默默认
                # W5（Q2）：role="validator" 进职责桶（直建实例不传 state 的漏计由类级差值补全）
                client = LLMClient(model=config.llm.smart_model, role="validator")
                data = client.chat_json(
                    [
                        {"role": "system", "content": VALIDATOR_SYSTEM.format(min_sources=config_min_sources())},
                        {"role": "user", "content": user},
                    ],
                    state=state,
                    schema=CitationVerdict,
                )
                # Q3=A：按 finding_id 对齐，不再按序 zip
                for item in data.get("citations", []):
                    fid = item.get("finding_id", "")
                    if fid and fid in {r["finding_id"] for r in to_check}:
                        verdicts[fid] = item
                    else:
                        unused_verdicts.append(item)
            except Exception:  # noqa: BLE001
                # 降级：仅返回存在性校验结果（保 W1 行为）
                llm_failed = True

        result: List[Citation] = []
        for r in local_results:
            if not r["existence"]:
                # 短路：存在性 False 不进 LLM，直接判未通过（Q3=A）
                result.append(Citation(
                    claim=r["claim"], source=r["source"],
                    verified=False, supported=False,
                    finding_id=r["finding_id"], source_type=r["source_type"],
                    confidence=0.0, note=r["note"], existence=False,
                ))
                continue

            verdict = verdicts.get(r["finding_id"])
            if verdict is None:
                # 空 finding_id（URL 协议）或 LLM 未按 id 返回：用未消费 verdict 按 claim 兜底匹配
                for j, item in enumerate(unused_verdicts):
                    if item.get("claim", "").strip() == r["claim"].strip():
                        verdict = item
                        unused_verdicts.pop(j)
                        break

            if llm_failed:
                # 整体降级：忠实度未知，按存在性通过（保 W1 行为）
                verified, confidence, note = True, 0.5, "LLM 校验失败，降级为存在性判定"
            elif verdict is not None:
                faithful = bool(verdict.get("faithful", True))
                verified = faithful
                confidence = float(verdict.get("confidence", 0.5))
                note = verdict.get("note", "") or ("" if faithful else "faithful=false 但未附原因")
            else:
                # 单条缺失：来源存在但未获 LLM 反馈 → 保守通过 + 提示（贴近 W1 行为）
                verified, confidence, note = True, 0.5, "忠实度未获 LLM 反馈（按存在性通过）"

            result.append(Citation(
                claim=r["claim"], source=r["source"],
                verified=verified,
                supported=bool(verdict.get("supported", False)) if verdict is not None else False,
                finding_id=r["finding_id"], source_type=r["source_type"],
                confidence=confidence, note=note, existence=True,
                is_meta=bool(verdict.get("is_meta", False)) if verdict is not None else False,
            ))
        return result


def config_min_sources() -> int:
    return config.research.min_sources_for_crosscheck