"""交付自检：零模型三项（需求 34 E4 / TBD-6 拍板口径，2026-10-11）。

纯 Python、离线可跑（无网络、无 key）；**只标注，不改交付状态**；**不写 ``degradation_log``**（N5）。

三项（TBD-6）：

1. ``quote_locatable``：每条带快照引文的 ``quote`` 能在其 ``snapshot_id`` 对应文本中
   按 L0~L2 定位，且记录的偏移与引文切片一致（``snapshot[char_start:char_end] == quote``）；
2. ``quote_in_dropped_span``：引用的 claim 区间与 repair 删除 / 降格区间**有交集**。
   坐标口径：与**最终报告**对齐——区间取最终文本中的 ``REPAIR_REMOVED_NOTE`` /
   ``REPAIR_UNCITED_NOTE`` 占位区（repair 回写的原始区间明细见 ``repair_log``，供审计）；
   已确证失败的引用（其句被移除是返工的正常结果，引用保留供失败附录）只计 ``info``，
   不计违例；**通过/未完成校验**的引用落在占位区才是需要复核的不一致；
3. ``version_claim_has_source``：命中版本号断言（``v?\\d+(\\.\\d+){1,3}`` / 「第 X 版」/
   「20XX 年 X 月版」）的**句**必须带引用标记（``[来源: …]``）。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from research_engine.quote_locate import locate_quote
from research_engine.repair import (
    REPAIR_REMOVED_NOTE,
    REPAIR_UNCITED_NOTE,
    _definitively_failed,
)
from research_engine.snapshots import load_snapshot_text
from research_engine.state import Citation

#: 版本号类断言（TBD-6；刻意窄口径——普通年份/纯数字不算版本断言）
_VERSION_PATTERNS = (
    re.compile(r"\bv?\d+(?:\.\d+){1,3}\b"),
    re.compile(r"第\s*[\d一二三四五六七八九十百]+\s*版"),
    re.compile(r"\d{4}\s*年\s*\d{1,2}\s*月\s*版"),
)
_CITATION_MARK = re.compile(r"\[来源[:：]")
_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*|(?<=\.\s)|\n")

#: 单项 samples 上限（只标注，不刷屏）
MAX_SAMPLES = 5


def _note_regions(report: str, note: str) -> List[Tuple[int, int]]:
    regions: List[Tuple[int, int]] = []
    index = report.find(note)
    while index >= 0:
        regions.append((index, index + len(note)))
        index = report.find(note, index + 1)
    return regions


def _overlaps(span: Tuple[int, int], regions: List[Tuple[int, int]]) -> bool:
    start, end = span
    return any(start < region_end and region_start < end
               for region_start, region_end in regions)


def _check_quote_locatable(citations: List[Citation]) -> Dict[str, Any]:
    checked = 0
    violations: List[Dict[str, Any]] = []
    for citation in citations:
        if not citation.snapshot_id or not citation.quote:
            continue
        checked += 1
        entry = {"finding_id": citation.finding_id or citation.source,
                 "quote": citation.quote[:80]}
        snapshot_text = load_snapshot_text(citation.snapshot_id)
        if not snapshot_text:
            violations.append({**entry, "reason": "snapshot_unreadable"})
            continue
        if (citation.char_start >= 0 and citation.char_end > citation.char_start
                and snapshot_text[citation.char_start:citation.char_end] == citation.quote):
            continue  # 偏移切片与引文一致（生产口径：原文切片）
        if locate_quote(snapshot_text, citation.quote) is not None:
            violations.append({**entry, "reason": "offset_mismatch"})
        else:
            violations.append({**entry, "reason": "not_locatable"})
    return {"checked": checked, "violations": violations}


def _check_dropped_span(report: str, citations: List[Citation]) -> Dict[str, Any]:
    regions = _note_regions(report, REPAIR_REMOVED_NOTE) + _note_regions(
        report, REPAIR_UNCITED_NOTE)
    checked = 0
    violations: List[Dict[str, Any]] = []
    info = 0
    for citation in citations:
        start = int(getattr(citation, "claim_start", -1))
        end = int(getattr(citation, "claim_end", -1))
        if start < 0 or end <= start:
            continue
        checked += 1
        if not regions or not _overlaps((start, end), regions):
            continue
        if _definitively_failed(citation):
            info += 1  # 失败句被移除是返工正常结果（引用保留供失败附录）
        else:
            violations.append({"finding_id": citation.finding_id or citation.source,
                               "span": [start, end]})
    return {"checked": checked, "violations": violations, "info": info}


def _check_version_claims(report: str) -> Dict[str, Any]:
    checked = 0
    violations: List[Dict[str, Any]] = []
    for raw in _SENTENCE_SPLIT.split(report or ""):
        sentence = (raw or "").strip()
        if not sentence:
            continue
        if not any(pattern.search(sentence) for pattern in _VERSION_PATTERNS):
            continue
        checked += 1
        if _CITATION_MARK.search(sentence):
            continue
        violations.append({"sentence": sentence[:120]})
    return {"checked": checked, "violations": violations}


def run_delivery_self_check(report: str, citations: List[Citation]) -> Dict[str, Any]:
    """执行三项自检；返回 ``{"checks": [...], "counts": {...}}``（确定性、可序列化）。

    **只标注**：调用方不得据此改 ``run_status`` / 交付状态；本函数不写降级日志。
    """
    items = list(citations or [])
    quote_check = _check_quote_locatable(items)
    dropped_check = _check_dropped_span(report or "", items)
    version_check = _check_version_claims(report or "")
    checks = [
        {"id": "quote_locatable", "label": "引文可定位",
         "checked": quote_check["checked"], "violations": len(quote_check["violations"]),
         "samples": quote_check["violations"][:MAX_SAMPLES]},
        {"id": "quote_in_dropped_span", "label": "引文落在被删/降格段落",
         "checked": dropped_check["checked"], "violations": len(dropped_check["violations"]),
         "info": dropped_check.get("info", 0),
         "samples": dropped_check["violations"][:MAX_SAMPLES]},
        {"id": "version_claim_has_source", "label": "版本号断言缺来源",
         "checked": version_check["checked"], "violations": len(version_check["violations"]),
         "samples": version_check["violations"][:MAX_SAMPLES]},
    ]
    return {"checks": checks, "counts": {item["id"]: item["violations"] for item in checks}}
