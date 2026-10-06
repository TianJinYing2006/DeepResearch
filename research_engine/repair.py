"""有界返工：确定性降格/移除未通过校验的论断（审计 F11 3b；R02/R03 加固）。

**为什么选择确定性删除/降格，而不是 LLM 重写或自动补检索**：

- 调研（Jina DeepSearch 的「预算不可分配 ⇒ 放弃递归选 FIFO」、LangChain ODR 的
  「截断重试只是 lossy 兜底」）显示：返工必须带**明确不变量与上限**，否则不可控；
- LLM 重写会引入新的、未经来源支撑的文本 —— 恰好是引用校验要防的问题；
- 自动补检索需要新的研究回环与预算分配（3b 未建），成本与风险都更高；
- 删除/降格是唯一**可证收敛**的手段：重跑校验后失败数必然下降（或保持），
  且不引入任何新事实。

移除粒度（R02）：同一 ``(claim_start, claim_end)`` 区间（一句话，含连续引用标记组）
里的引用**全部是被确证失败**（``verified=False`` 且``verification_failed=False``）
才删除该句；多编号引用（[来源: 5, 6]）拆出的多条 Citation 共享同一区间，只要
有一条通过则整句保留；**UNKNOWN（校验未完成 / provider 故障 / origin 缺失 /
未获对应裁决）不触发删除**——真实事实不因短暂故障被整批误删，改由渲染层显式
标注未核验。区间缺失（``claim_start < 0``，旧快照/手工构造）不参与。

未引用事实（R03）：``uncited_facts`` 的 ``{claim, start, end}`` 明细由 Validator
从正文抽取；返工对其做**确定性降格标注**（保留原句 + 追加「未提供来源」占位），
使「整篇无引用事实」不再零成本绕过质量闭环。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from research_engine.state import Citation

#: 被移除论断的降格占位文案（非事实句、不需要引用；长度刻意低于覆盖率统计阈值）
REPAIR_REMOVED_NOTE = "（该论断未通过来源校验，已移除）"
#: R03：未引用事实的降格标注（保留原句，只追加不确定性声明）
REPAIR_UNCITED_NOTE = "（未提供来源，未经核验）"


def _definitively_failed(citation: Citation) -> bool:
    """R02：只有「确证失败」才可删除；UNKNOWN（verification_failed）必须保留。"""
    if citation.verified:
        return False
    return not bool(getattr(citation, "verification_failed", False))


def plan_repairs(citations: List[Citation]) -> List[Tuple[int, int]]:
    """返回可删除的区间列表（按起点倒序，便于安全回收）。

    仅保留「同区间全部确证失败」的区间；多编号引用只要有一条通过则整句保留；
    含 UNKNOWN（``verification_failed=True``）的区间一律保留。
    """
    groups: Dict[Tuple[int, int], List[Citation]] = {}
    for citation in citations:
        start = int(getattr(citation, "claim_start", -1))
        end = int(getattr(citation, "claim_end", -1))
        if start < 0 or end <= start:
            continue
        groups.setdefault((start, end), []).append(citation)
    spans = [span for span, items in groups.items()
             if items and all(_definitively_failed(c) for c in items)]
    spans.sort(reverse=True)
    return spans


def repair_report(
    report: str,
    citations: List[Citation],
    uncited_facts: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[str, Dict[str, Any]]:
    """把确证失败的论断句替换为降格占位、对未引用事实追加降格标注。

    返回 ``(repaired_report, stats)``。``stats``：

    - ``removed_claims``：删除句数；
    - ``failed_before``：返工前失败引用数（含 UNKNOWN）；
    - ``kept_unknown``：因 UNKNOWN（校验未完成）而保留、未删除的引用数；
    - ``downgraded_uncited``：被追加降格标注的未引用事实句数。
    """
    before_failed = sum(1 for c in citations if not c.verified)
    kept_unknown = sum(1 for c in citations if not c.verified and not _definitively_failed(c))
    spans = plan_repairs(citations)
    uncited_facts = list(uncited_facts or [])
    stats: Dict[str, Any] = {
        "removed_claims": 0,
        "failed_before": before_failed,
        "kept_unknown": kept_unknown,
        "downgraded_uncited": 0,
    }
    if not report or (not spans and not uncited_facts):
        return report, stats

    # 删除与插入合并为同一倒序操作流，防止插入位移破坏删除区间坐标
    operations: List[Tuple[int, str, Any]] = []
    for start, end in spans:
        operations.append((start, "remove", end))
    for fact in uncited_facts:
        try:
            end = int(fact.get("end", -1))
            start = int(fact.get("start", -1))
        except (TypeError, ValueError):
            continue
        if start < 0 or end <= start or end > len(report):
            continue
        operations.append((end, "insert", fact))
    operations.sort(key=lambda op: op[0], reverse=True)

    text = report
    removed = downgraded = 0
    floor = len(text) + 1  # 已处理区间的最小起点（倒序处理）
    for pos, kind, payload in operations:
        if pos > floor:
            continue
        if kind == "remove":
            end = int(payload)
            text = text[:pos] + REPAIR_REMOVED_NOTE + text[end:]
            removed += 1
            floor = pos
        else:
            if text.startswith(REPAIR_UNCITED_NOTE, pos):
                floor = pos  # 已标注（幂等，含旧 span 重放）：不重复追加
                continue
            text = text[:pos] + REPAIR_UNCITED_NOTE + text[pos:]
            downgraded += 1
            floor = pos
    stats["removed_claims"] = removed
    stats["downgraded_uncited"] = downgraded
    return text, stats
