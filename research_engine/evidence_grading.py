"""证据链分级处置（需求 34 E5 / TBD-7 拍板口径，2026-10-11）。

**清单化（致命 vs 可修）+ 明确动作**；本模块**只分级、只标注**——不新增任何
删除/改写路径（唯一的「阻断」复用 repair R02 的既有删除：确证失败且同句全部失败）。

分级表（TBD-7）：

- **致命 · `unknown_source_id`**：引用编号不在 findings ⇒ **阻断**（repair R02 已删除该句）；
- **致命 · `quote_not_found` 且 `snapshot_status=ok`**：**降格标注、不删除**
  （快照在、引文找不到，可能是 claim 措辞问题，**不等于事实错误**）；
- **可修 · `snapshot_status=missing`**：标注「来源正文未取得」，不删除；
- **可修 · `quote_status=stale`**：标注「引文定位待重新提取」，不删除、不算失败；
- **可修 · 正文含裸 URL 且缺引用标记**：repair R03 降格（占位已加）。

🚨 **红线①**（ADR-0011）：**不得**把「校验未完成 / 快照缺失 / offset 过期」判成
「事实错误」——本模块不参与 `repair._definitively_failed` 的判定（UNKNOWN 不删除口径保持）。
🚨 **红线②**：不新增 `FailureReason` 枚举值（分级词表独立于工具层 5 值）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from research_engine.state import Citation, ResearchFinding

GRADE_FATAL = "fatal"
GRADE_REPAIRABLE = "repairable"

_URL_RE = re.compile(r"https?://\S+")
_SENT_BOUNDARY = "。！？；!?;\n"

#: samples 上限（只标注，不刷屏）
MAX_SAMPLES = 5


@dataclass(frozen=True)
class GradingItem:
    """一条分级处置项。"""

    grade: str          # fatal | repairable
    reason: str         # unknown_source_id / quote_not_found / snapshot_missing / ...
    action: str         # 明确动作文案（阻断 / 降格标注（不删除）/ …）
    finding_id: str = ""
    source: str = ""
    snippet: str = ""


def _finding_for(citation: Citation, findings: List[ResearchFinding]) -> Optional[ResearchFinding]:
    if citation.finding_id and str(citation.finding_id).isdigit():
        index = int(citation.finding_id) - 1
        if 0 <= index < len(findings):
            return findings[index]
    if citation.source:
        return next((f for f in findings if f.source == citation.source), None)
    return None


def _snapshot_status(finding: Optional[ResearchFinding]) -> str:
    if finding is None:
        return ""
    return str((getattr(finding, "metadata", None) or {}).get("snapshot_status") or "")


def _uncited_url_snippets(report: str) -> List[str]:
    """最终报告中的「裸 URL 且缺引用标记」句子（可修 · R03 类）。"""
    text = report or ""
    snippets: List[str] = []
    for match in _URL_RE.finditer(text):
        start = match.start()
        while start > 0 and text[start - 1] not in _SENT_BOUNDARY:
            start -= 1
        end = match.end()
        while end < len(text) and text[end] not in _SENT_BOUNDARY:
            end += 1
        sentence = text[start:end].strip()
        if "[来源" in sentence:
            continue
        snippets.append(sentence[:120])
    return snippets


def grade_evidence_chain(citations: List[Citation], findings: List[ResearchFinding],
                         report: str) -> Dict[str, Any]:
    """对最终引用清单分级；返回 ``{"items": [...], "counts": {...}}``（确定性、可序列化）。

    与 ``run_status`` 无关：抓取失败/快照缺失**不推导** ``degraded``（N5 口径）。
    """
    items: List[GradingItem] = []
    for citation in citations or []:
        finding = _finding_for(citation, findings)
        status = _snapshot_status(finding)
        if not citation.existence:
            items.append(GradingItem(
                GRADE_FATAL, "unknown_source_id",
                "已阻断（repair R02 删除该句）",
                finding_id=str(citation.finding_id), source=citation.source))
            continue
        if citation.quote_status == "not_found" and status == "ok":
            items.append(GradingItem(
                GRADE_FATAL, "quote_not_found",
                "降格标注（不删除；快照在、引文未定位，可能是 claim 措辞问题）",
                finding_id=str(citation.finding_id), source=citation.source,
                snippet=(citation.quote or "")[:80]))
            continue
        if citation.quote_status == "stale":
            items.append(GradingItem(
                GRADE_REPAIRABLE, "quote_stale",
                "标注「引文定位待重新提取」（不删除、不算失败）",
                finding_id=str(citation.finding_id), source=citation.source))
            continue
        if status == "missing":
            items.append(GradingItem(
                GRADE_REPAIRABLE, "snapshot_missing",
                "标注「来源正文未取得」（不删除）",
                finding_id=str(citation.finding_id), source=citation.source))
            continue
        if status and status != "ok" and not citation.quote_status:
            # disabled / skipped_budget / skipped_robots / skipped_pdf / unsupported / not_attempted
            items.append(GradingItem(
                GRADE_REPAIRABLE, f"snapshot_{status}",
                "标注（不删除）",
                finding_id=str(citation.finding_id), source=citation.source))
    for snippet in _uncited_url_snippets(report):
        items.append(GradingItem(
            GRADE_REPAIRABLE, "bare_url_uncited", "R03 降格（占位已加）", snippet=snippet))

    fatal = [item for item in items if item.grade == GRADE_FATAL]
    repairable = [item for item in items if item.grade == GRADE_REPAIRABLE]
    by_reason: Dict[str, int] = {}
    for item in items:
        by_reason[item.reason] = by_reason.get(item.reason, 0) + 1
    return {
        "items": [
            {"grade": item.grade, "reason": item.reason, "action": item.action,
             "finding_id": item.finding_id, "source": item.source, "snippet": item.snippet}
            for item in items
        ],
        "counts": {
            "total": len(items),
            "fatal": len(fatal),
            "repairable": len(repairable),
            "by_reason": by_reason,
        },
    }
