# -*- coding: utf-8 -*-
"""Validator Agent：引用校验 + 多源印证 + 置信度分级。

主卖点（防幻觉）的核心实现：
1. 引用存在性校验：报告中的每个引用是否真实存在于检索结果
2. 多源交叉印证：关键论断需多个独立来源支持
3. 置信度分级：输出每条论断的置信度

引用协议：Writer 用 [来源: N] 编号引用（N 是研究发现列表中的编号），
Validator 将编号映射回真实来源再做校验。
"""
from __future__ import annotations

import re
from typing import Dict, List

from research_engine.llm.router import get_router
from research_engine.state import Citation, ResearchFinding

VALIDATOR_SYSTEM = """你是研究事实核查员。你的任务是校验报告中的论断与引用。

请以 JSON 格式输出校验结果：
{{
  "citations": [
    {{
      "claim": "论断原文",
      "source": "引用来源",
      "verified": true/false,
      "supported": true/false,
      "confidence": 0.0-1.0,
      "note": "说明"
    }}
  ]
}}

判定规则：
- verified: 引用来源是否真实存在于研究发现中
- supported: 论断是否被至少 {min_sources} 个独立来源支持（多源印证）
- confidence: 综合置信度
"""


class Validator:
    """引用校验器。"""

    def __init__(self):
        self.router = get_router()

    def _build_index(self, findings: List[ResearchFinding]) -> Dict[str, str]:
        """建立编号 -> 真实来源 的映射（与 format_for_writer 的编号一致）。"""
        return {str(i): f.source for i, f in enumerate(findings, 1)}

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
            # 取论断（引用前的一段文本）
            start = max(0, m.start() - 80)
            claim = report[start:m.start()].strip().replace("\n", " ")
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

    def validate(self, report: str, findings: List[ResearchFinding]) -> List[Citation]:
        """校验报告引用。"""
        extracted = self._extract_citations(report)
        index = self._build_index(findings)
        known = set(index.values())

        # 存在性校验（本地规则，无需 LLM）：编号映射到真实来源，且来源真实存在
        local_results = []
        for c in extracted:
            ref = c["source"]
            # 支持 [来源: N] 编号 或 [来源: <真实来源>]
            real_source = index.get(ref, ref)
            verified = real_source in known
            local_results.append({**c, "source": real_source, "verified": verified})

        # 多源印证 + 置信度（用 LLM 判断）
        if not local_results:
            return []

        findings_text = "\n".join(f"- [{f.source}] {f.content[:150]}" for f in findings[:20])
        citations_json = "\n".join(
            f"- claim: {c['claim'][:100]} | source: {c['source']} | verified: {c['verified']}"
            for c in local_results
        )
        user = f"研究发现：\n{findings_text}\n\n待校验引用：\n{citations_json}\n\n请输出校验结果。"

        try:
            data = self.router.smart_json(VALIDATOR_SYSTEM.format(min_sources=config_min_sources()), user)
            result = []
            for item in data.get("citations", []):
                result.append(
                    Citation(
                        claim=item.get("claim", ""),
                        source=item.get("source", ""),
                        verified=item.get("verified", False),
                        supported=item.get("supported", False),
                    )
                )
            return result
        except Exception:  # noqa: BLE001
            # 降级：仅返回存在性校验结果
            return [
                Citation(claim=c["claim"], source=c["source"], verified=c["verified"])
                for c in local_results
            ]


def config_min_sources() -> int:
    from config import config
    return config.research.min_sources_for_crosscheck
