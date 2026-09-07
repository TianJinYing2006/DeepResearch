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
        """评测报告引用准确率（W2：输出按 source_type 拆分 + 失败原因分布 + is_meta 命中数）。"""
        citations: List[Citation] = self.validator.validate(report, findings)
        total = len(citations)
        verified = sum(1 for c in citations if c.verified)
        supported = sum(1 for c in citations if c.supported)

        # W2（Q6=A 双口径）：存在性 / 忠实度分开统计
        existence = sum(1 for c in citations if c.existence)

        # W2：按 source_type 拆分通过率
        by_type: dict = {}
        for c in citations:
            t = c.source_type or "unknown"
            by_type.setdefault(t, {"total": 0, "verified": 0, "existence": 0})
            by_type[t]["total"] += 1
            by_type[t]["verified"] += 1 if c.verified else 0
            by_type[t]["existence"] += 1 if c.existence else 0

        # W2：失败原因分布 + is_meta 命中数
        failed_notes: dict = {}
        for c in citations:
            if not c.verified:
                key = (c.note or "未说明")[:40]
                failed_notes[key] = failed_notes.get(key, 0) + 1
        meta_hits = sum(1 for c in citations if c.is_meta)

        return {
            "total_citations": total,
            "verified": verified,
            "supported": supported,
            "existence_rate": round(existence / total, 4) if total else 0,
            "fidelity_rate": round(verified / existence, 4) if existence else 0,  # W2：忠实度口径
            "crosscheck_rate": round(supported / total, 4) if total else 0,
            "by_source_type": by_type,       # W2：web/rag 拆分
            "failed_note_distribution": failed_notes,  # W2：失败原因分布
            "is_meta_hits": meta_hits,       # W2：自指复核命中数
            "citations": citations,
        }
