# -*- coding: utf-8 -*-
"""评测 B：引用准确率评测。

评测报告中的引用是否真实存在（存在性）以及是否被多源支持（印证）。
这是主卖点（防幻觉）的直接量化。
"""
from __future__ import annotations

from typing import List

from research_engine.agents.validator import Validator
from research_engine.state import Citation, ResearchFinding


class CitationEvaluator:
    """引用准确率评测器。"""

    def __init__(self):
        self.validator = Validator()

    def evaluate(self, report: str, findings: List[ResearchFinding]) -> dict:
        """评测报告引用准确率。"""
        citations: List[Citation] = self.validator.validate(report, findings)
        total = len(citations)
        verified = sum(1 for c in citations if c.verified)
        supported = sum(1 for c in citations if c.supported)

        return {
            "total_citations": total,
            "verified": verified,
            "supported": supported,
            "existence_rate": round(verified / total, 4) if total else 0,
            "crosscheck_rate": round(supported / total, 4) if total else 0,
            "citations": citations,
        }
