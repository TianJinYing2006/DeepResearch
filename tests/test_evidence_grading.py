"""需求 34 E5：证据链分级处置单测（只分级/标注；红线锁定）。"""
from __future__ import annotations

from research_engine.evidence_grading import grade_evidence_chain
from research_engine.render import ReportRenderer
from research_engine.repair import plan_repairs, repair_report
from research_engine.state import Citation, ResearchFinding


def _finding(number: int, *, snapshot_status: str = "ok") -> ResearchFinding:
    return ResearchFinding(content=f"证据 {number}", source=f"https://e{number}.com/a",
                           source_type="web", confidence=0.6,
                           metadata={"snapshot_status": snapshot_status})


def _citation(**kw) -> Citation:
    defaults = dict(claim="量子计算取得突破", source="https://e1.com/a", finding_id="1",
                    existence=True, verified=True)
    defaults.update(kw)
    return Citation(**defaults)


def _by_reason(result):
    return {item["reason"]: item for item in result["items"]}


# ---- 分类正确性 ----

def test_unknown_source_id_is_fatal_blocked():
    citation = _citation(finding_id="9", source="https://nope.com", existence=False)
    result = grade_evidence_chain([citation], [_finding(1)], "")
    item = _by_reason(result)["unknown_source_id"]
    assert item["grade"] == "fatal" and "阻断" in item["action"]
    assert result["counts"]["fatal"] == 1


def test_quote_not_found_with_snapshot_ok_is_fatal_flagged():
    citation = _citation(quote_status="not_found")
    result = grade_evidence_chain([citation], [_finding(1, snapshot_status="ok")], "")
    item = _by_reason(result)["quote_not_found"]
    assert item["grade"] == "fatal" and "不删除" in item["action"]


def test_snapshot_missing_and_stale_are_repairable():
    missing = _citation(quote_status="", finding_id="1")
    stale = _citation(quote_status="stale", finding_id="2", source="https://e2.com/a")
    result = grade_evidence_chain(
        [missing, stale],
        [_finding(1, snapshot_status="missing"), _finding(2, snapshot_status="ok")], "")
    grades = _by_reason(result)
    assert grades["snapshot_missing"]["grade"] == "repairable"
    assert grades["quote_stale"]["grade"] == "repairable"
    assert result["counts"]["repairable"] == 2


def test_snapshot_unavailable_statuses_listed():
    citation = _citation(quote_status="", finding_id="1")
    result = grade_evidence_chain([citation], [_finding(1, snapshot_status="not_attempted")], "")
    assert "snapshot_not_attempted" in _by_reason(result)


def test_bare_url_uncited_flagged_and_cited_excluded():
    report = ("详见 https://example.com/report 的结论。\n"
              "另一处引用 https://example.com/data 有来源 [来源: 1]。\n")
    result = grade_evidence_chain([], [], report)
    reasons = [item["reason"] for item in result["items"]]
    assert reasons.count("bare_url_uncited") == 1


# ---- 红线①：UNKNOWN / 快照缺失 / offset 过期 一律不删除（不判事实错误）----

def test_red_line_no_deletion_for_flagged_categories():
    flagged = [
        _citation(quote_status="not_found"),                                    # 快照在、引文未定位
        _citation(finding_id="2", source="https://e2.com/a", quote_status=""),  # 抓取失败
        _citation(finding_id="3", source="https://e3.com/a", quote_status="stale"),
        _citation(finding_id="4", source="https://e4.com/a",
                  verified=False, verification_failed=True),                    # UNKNOWN
    ]
    for citation in flagged:
        citation.claim_start, citation.claim_end = 0, 5
    assert plan_repairs(flagged) == []  # 全部保留
    _, stats = repair_report("甲句内容。", flagged)
    assert stats["removed_claims"] == 0


def test_red_line_unknown_source_id_is_blocked_by_repair():
    blocked = _citation(finding_id="9", existence=False, verified=False,
                        claim_start=0, claim_end=5)
    assert plan_repairs([blocked]) == [(0, 5)]  # 阻断（复用 R02 删除）
    _, stats = repair_report("甲句内容。", [blocked])
    assert stats["removed_claims"] == 1


# ---- 输出形态 ----

def test_deterministic_and_serializable():
    args = ([_citation(quote_status="not_found")], [_finding(1)], "报告 https://x.com 未引用。")
    first = grade_evidence_chain(*args)
    assert first == grade_evidence_chain(*args)
    assert set(first["counts"]) == {"total", "fatal", "repairable", "by_reason"}


def test_render_appendix():
    result = grade_evidence_chain([_citation(quote_status="not_found")], [_finding(1)], "")
    text = ReportRenderer().build_evidence_grading(result)
    assert "证据链分级处置" in text and "致命" in text
    assert ReportRenderer().build_evidence_grading({}) == ""
