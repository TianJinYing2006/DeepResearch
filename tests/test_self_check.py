"""需求 34 E4：交付自检单测（零模型、离线；store 注入）。"""
from __future__ import annotations

from research_engine.net.web_fetch import FetchBudget
from research_engine.repair import REPAIR_REMOVED_NOTE, repair_report
from research_engine.self_check import run_delivery_self_check
from research_engine.snapshots import FetchContext, use_fetch_context
from research_engine.state import Citation


class StubStore:
    def __init__(self, snapshots: dict):
        self._snapshots = snapshots

    def load_snapshot(self, sha256: str):
        return self._snapshots.get(sha256)


def _ctx(store):
    return FetchContext(store=store, run_id="run-1",
                        budget=FetchBudget(max_fetches=15, max_bytes=1, max_seconds=1.0),
                        fetcher=None)


def _citation(**kw) -> Citation:
    defaults = dict(claim="量子计算取得突破", source="https://e.com/a", finding_id="1",
                    existence=True, verified=True)
    defaults.update(kw)
    return Citation(**defaults)


# ---- 1) quote_locatable ----

def test_quote_locatable_ok():
    snap = "导语。量子计算取得突破。结尾。"
    quote = "量子计算取得突破。"
    start = snap.index(quote)
    store = StubStore({"sha-1": snap})
    citation = _citation(quote=quote, char_start=start, char_end=start + len(quote),
                         snapshot_id="sha-1", quote_status="located")
    with use_fetch_context(_ctx(store)):
        result = run_delivery_self_check("报告", [citation])
    check = result["checks"][0]
    assert check["checked"] == 1 and check["violations"] == 0


def test_quote_offset_mismatch_flagged():
    snap = "导语。量子计算取得突破。结尾。"
    quote = "量子计算取得突破。"
    store = StubStore({"sha-1": snap})
    citation = _citation(quote=quote, char_start=0, char_end=len(quote),
                         snapshot_id="sha-1", quote_status="located")
    with use_fetch_context(_ctx(store)):
        result = run_delivery_self_check("报告", [citation])
    assert result["counts"]["quote_locatable"] == 1
    assert result["checks"][0]["samples"][0]["reason"] == "offset_mismatch"


def test_quote_snapshot_unreadable_flagged():
    citation = _citation(quote="量子计算取得突破。", char_start=0, char_end=9,
                         snapshot_id="sha-404", quote_status="located")
    with use_fetch_context(_ctx(StubStore({}))):
        result = run_delivery_self_check("报告", [citation])
    assert result["counts"]["quote_locatable"] == 1
    assert result["checks"][0]["samples"][0]["reason"] == "snapshot_unreadable"


def test_no_snapshot_citations_skipped():
    result = run_delivery_self_check("报告", [_citation()])
    assert result["checks"][0]["checked"] == 0
    assert result["checks"][0]["violations"] == 0


# ---- 2) quote_in_dropped_span ----

def test_dropped_span_flags_passing_but_info_for_failed():
    report = f"开头。{REPAIR_REMOVED_NOTE}后续。"
    note_start = report.index(REPAIR_REMOVED_NOTE)
    passing = _citation(claim_start=note_start, claim_end=note_start + 5, verified=True)
    failed = _citation(finding_id="2", claim_start=note_start, claim_end=note_start + 5,
                       verified=False, verification_failed=False)
    result = run_delivery_self_check(report, [passing, failed])
    check = result["checks"][1]
    assert check["violations"] == 1      # 通过校验的引用落在移除区 → 需复核
    assert check["info"] == 1            # 失败引用 → 返工正常结果


def test_dropped_span_clean_report():
    citation = _citation(claim_start=0, claim_end=4)
    result = run_delivery_self_check("普通报告文本，无移除占位。", [citation])
    assert result["checks"][1]["violations"] == 0


def test_repair_stats_expose_spans():
    citation = _citation(verified=False, verification_failed=False,
                         claim_start=0, claim_end=5)
    _, stats = repair_report("甲句内容。后续。", [citation])
    assert stats["removed_spans"] == [[0, 5]]
    _, stats2 = repair_report("事实句。", [], uncited_facts=[{"claim": "x", "start": 0, "end": 4}])
    assert stats2["downgraded_spans"] == [[0, 4]]


# ---- 3) version_claim_has_source ----

def test_version_claims_require_citation_marker():
    report = (
        "本项目基于 Python 3.11 实现，性能提升明显。\n"
        "框架已升级至 v2.0.1 版本 [来源: 1]。\n"
        "发布于 2024 年 3 月版，未附来源。\n"
        "会议在 2024 年举办。\n"  # 普通年份非版本断言 → 不检查
    )
    result = run_delivery_self_check(report, [])
    check = result["checks"][2]
    assert check["checked"] == 3
    assert check["violations"] == 2


def test_version_claim_chinese_edition():
    result = run_delivery_self_check("这是第三版手册的内容。", [])
    assert result["checks"][2]["violations"] == 1


# ---- 输出形态 ----

def test_result_is_deterministic_and_serializable():
    report = "报告正文。"
    first = run_delivery_self_check(report, [_citation()])
    second = run_delivery_self_check(report, [_citation()])
    assert first == second
    assert set(first["counts"]) == {"quote_locatable", "quote_in_dropped_span",
                                    "version_claim_has_source"}


def test_renderer_appendix_renders():
    from research_engine.render import ReportRenderer

    check = run_delivery_self_check("普通报告。", [])
    text = ReportRenderer().build_delivery_self_check(check)
    assert "交付自检" in text and "✅ 通过" in text
    assert ReportRenderer().build_delivery_self_check({}) == ""
