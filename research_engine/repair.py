"""有界返工：确定性降格/移除未通过校验的论断（审计 F11，3b）。

**为什么选择确定性删除/降格，而不是 LLM 重写或自动补检索**：

- 调研（Jina DeepSearch 的「预算不可分配 ⇒ 放弃递归选 FIFO」、LangChain ODR 的
  「截断重试只是 lossy 兜底」）显示：返工必须带**明确不变量与上限**，否则不可控；
- LLM 重写会引入新的、未经来源支撑的文本 —— 恰好是引用校验要防的问题；
- 自动补检索需要新的研究回环与预算分配（3b 未建），成本与风险都更高；
- 删除/降格是唯一**可证收敛**的手段：重跑校验后失败数必然下降（或保持），
  且不引入任何新事实。

移除粒度：同一 ``(claim_start, claim_end)`` 区间（一句话）里的引用**全部**未通过
才删除该句——多编号引用（[来源: 5, 6]）拆出的多条 Citation 共享同一区间，只要
有一条通过则整句保留。区间缺失（``claim_start < 0``，旧快照/手工构造）不参与。
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from research_engine.state import Citation

#: 被移除论断的降格占位文案（非事实句、不需要引用；长度刻意低于覆盖率统计阈值）
REPAIR_REMOVED_NOTE = "（该论断未通过来源校验，已移除）"


def plan_repairs(citations: List[Citation]) -> List[Tuple[int, int]]:
    """返回可删除的区间列表（按起点倒序，便于安全回收）。

    仅保留「同区间全部 ``verified=False``」的区间；多编号引用只要有一条通过则整句保留。
    """
    groups: Dict[Tuple[int, int], List[Citation]] = {}
    for citation in citations:
        start = int(getattr(citation, "claim_start", -1))
        end = int(getattr(citation, "claim_end", -1))
        if start < 0 or end <= start:
            continue
        groups.setdefault((start, end), []).append(citation)
    spans = [span for span, items in groups.items() if all(not c.verified for c in items)]
    spans.sort(reverse=True)
    return spans


def repair_report(report: str, citations: List[Citation]) -> Tuple[str, Dict[str, Any]]:
    """把未通过校验的论断句替换为降格占位；返回 ``(repaired_report, stats)``。

    ``stats``：``removed_claims``（删除句数）/ ``failed_before``（返工前失败引用数）。
    文本不可用（空报告 / 无区间）时原样返回，stats 全零。
    """
    before_failed = sum(1 for c in citations if not c.verified)
    spans = plan_repairs(citations)
    stats: Dict[str, Any] = {
        "removed_claims": 0,
        "failed_before": before_failed,
    }
    if not report or not spans:
        return report, stats

    text = report
    removed = 0
    floor = len(text) + 1  # 已删除区间的最小起点（区间按起点倒序处理）
    for start, end in spans:
        if end > floor:  # 与上一个（更靠后的）删除区间重叠 → 跳过，避免二次切割
            continue
        text = text[:start] + REPAIR_REMOVED_NOTE + text[end:]
        removed += 1
        floor = start
    stats["removed_claims"] = removed
    return text, stats
