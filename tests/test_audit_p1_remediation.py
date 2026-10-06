# -*- coding: utf-8 -*-
"""审计 P1 整改回归（R01–R05、R10，2026-10-06 复核报告）。

全部离线：mock LLMClient / 内存 FakeStore，零 API key、零真实 PG。

- R01：verdict 消费式对齐 —— 同来源不同论断（数值/否定词/重复句）不得复用裁决；
- R02：返工边界 —— 连续引用共享 span、句末引用不误删前句、UNKNOWN 不删除；
- R03：事实覆盖闭环 —— 句末引用计为已引用、未引用事实进入降格策略；
- R04：origin 链缺失/部分缺失 ⇒ UNKNOWN（禁止摘要当原始依据）；
- R05：预算预占 —— 并发/耗尽不越闸；显式 timeout 服从任务时限；
- R10：owner 守卫续租/显式序号路径携带 attempt。
"""
from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from research_engine.agents.validator import Validator
from research_engine.budget import TokenBudgetExceeded
from research_engine.evidence import build_evidence_index, ensure_evidence_identity, resolve_evidence_text
from research_engine.llm.client import LLMClient
from research_engine.repair import (
    REPAIR_UNCITED_NOTE,
    plan_repairs,
    repair_report,
)
from research_engine.state import Citation, ResearchFinding, ResearchState
from web.backend.store import LeaseLostError, RunOwnership


def _finding(content: str, source: str = "https://a.example/x", **kw) -> ResearchFinding:
    return ResearchFinding(content=content, source=source, source_type="web",
                           sq_id="q1", **kw)


def _verdict(fid: str, claim: str, **kw) -> dict:
    payload = dict(finding_id=fid, claim=claim, claim_echo=claim, faithful=True,
                   supported=False, confidence=0.9,
                   supporting_evidence_ids=[], contradicting_evidence_ids=[])
    payload.update(kw)
    return payload


def _validate(report, findings, verdicts, *, evidence=None):
    def fake(self, messages, state=None, schema=None, **kw):
        return {"citations": verdicts}

    v = Validator()
    with patch("research_engine.llm.client.LLMClient.chat_json", fake):
        citations = v.validate(report, findings, evidence=evidence)
    return citations, v.last_validation_stats


def _citation(claim, start, end, verified, *, verification_failed=False, source="https://a"):
    return Citation(claim=claim, source=source, verified=verified, existence=True,
                    verification_failed=verification_failed,
                    claim_start=start, claim_end=end)


# ---------------------------------------------------------------- R01 裁决对齐

def test_r01_changed_number_does_not_inherit_verdict():
    good = "该公司年度营收达到一百亿元"
    bad = "该公司年度营收达到九百亿元"
    citations, stats = _validate(
        f"{good} [来源: 1]\n{bad} [来源: 1]",
        [_finding(good)],
        [_verdict("1", good)],
    )
    assert len(citations) == 2
    assert citations[0].verified is True
    assert citations[1].verified is False
    assert citations[1].verification_failed is True
    assert "未获对应裁决" in citations[1].note
    assert stats["verdict_missing_for_citation_count"] == 1


def test_r01_verdict_consumed_once_for_duplicate_claim():
    claim = "该公司年度营收达到一百亿元"
    citations, _ = _validate(
        f"{claim} [来源: 1]\n{claim} [来源: 1]",
        [_finding(claim)],
        [_verdict("1", claim)],
    )
    assert citations[0].verified is True
    assert citations[1].verified is False and citations[1].verification_failed is True


def test_r01_similarity_variant_same_numbers_still_matches():
    """标点/前缀差异但数值与否定签名一致 ⇒ 仍算对应裁决（容忍回显风格差异）。"""
    citations, _ = _validate(
        "系统召回率提升 12% [来源: 1]",
        [_finding("召回率提升 12%")],
        [_verdict("1", "召回率提升 12%")],
    )
    assert citations[0].verified is True


def test_r01_negation_change_blocks_reuse():
    good = "公司实现盈利增长"
    bad = "公司未实现盈利增长"
    citations, _ = _validate(
        f"{good} [来源: 1]\n{bad} [来源: 1]",
        [_finding(good)],
        [_verdict("1", good)],
    )
    assert citations[0].verified is True
    assert citations[1].verification_failed is True and citations[1].verified is False


def test_r01_unmatched_verdict_is_counted_unused():
    citations, stats = _validate(
        "完全无关的论断句需要足够长 [来源: 1]",
        [_finding("别的材料内容")],
        [_verdict("1", "另一条根本对不上的论断内容")],
    )
    assert citations[0].verification_failed is True
    assert stats["verdict_missing_for_citation_count"] == 1
    assert stats["verdict_unused_count"] == 1


# ---------------------------------------------------------------- R02 返工边界

def test_r02_adjacent_citations_share_claim_span():
    claim = "该公司年度营收达到一百亿元"
    report = f"{claim} [来源: 1][来源: 2]"
    extracted = Validator()._extract_citations(report)
    assert len(extracted) == 2
    assert extracted[0]["claim_start"] == extracted[1]["claim_start"]
    assert extracted[0]["claim_end"] == extracted[1]["claim_end"] == len(report)
    assert extracted[0]["claim"] == extracted[1]["claim"] == claim


def test_r02_adjacent_pass_keeps_sentence_intact():
    claim = "该公司年度营收达到一百亿元"
    report = f"{claim} [来源: 1][来源: 2]"
    first = _verdict("1", claim, faithful=False)
    citations, _ = _validate(
        report,
        [_finding("无关材料", "https://a.example/x"), _finding(claim, "https://b.example/y")],
        [first, _verdict("2", claim)],
    )
    assert citations[0].verified is False and citations[1].verified is True
    repaired, stats = repair_report(report, citations)
    assert repaired == report, "一条引用通过 ⇒ 整句保留，不得删除/留下孤立引用"
    assert stats["removed_claims"] == 0


def test_r02_sentence_final_citation_span_starts_at_sentence():
    background = "这是应该保留的背景说明。"
    claim = "该公司年度营收达到九百亿元"
    report = f"{background}{claim}。[来源: 1]"
    extracted = Validator()._extract_citations(report)
    assert extracted[0]["claim_start"] == len(background), "span 必须指向本句而非上一句"
    # F4 主语兜底可能前置上一句（显示层行为），但 claim 必须包含目标论断本身
    assert extracted[0]["claim"].endswith(claim)


def test_r02_sentence_final_repair_removes_only_the_claim():
    background = "这是应该保留的背景说明。"
    claim = "该公司年度营收达到九百亿元"
    report = f"{background}{claim}。[来源: 1]"
    extracted = Validator()._extract_citations(report)
    citation = _citation(claim, extracted[0]["claim_start"], extracted[0]["claim_end"], False)
    repaired, stats = repair_report(report, [citation])
    assert background in repaired, "背景材料不得被误删"
    assert claim not in repaired
    assert stats["removed_claims"] == 1


def test_r02_unknown_is_kept_not_removed():
    claim = "该公司年度营收达到一百亿元"
    report = f"{claim} [来源: 1]"
    unknown = _citation(claim, 0, len(report), False, verification_failed=True)
    assert plan_repairs([unknown]) == []
    repaired, stats = repair_report(report, [unknown])
    assert repaired == report
    assert stats["kept_unknown"] == 1 and stats["removed_claims"] == 0


def test_r02_provider_failure_does_not_delete_fact():
    claim = "该公司年度营收达到一百亿元"
    report = f"{claim} [来源: 1]"

    def boom(self, messages, state=None, schema=None, **kw):
        raise RuntimeError("offline outage")

    validator = Validator()
    with patch("research_engine.llm.client.LLMClient.chat_json", boom):
        citations = validator.validate(report, [_finding(claim)])
    assert citations[0].verification_failed is True
    repaired, stats = repair_report(report, citations)
    assert repaired == report and stats["removed_claims"] == 0
    assert stats["kept_unknown"] == 1


# ---------------------------------------------------------------- R03 事实闭环

def test_r03_sentence_final_citation_counts_as_cited():
    stats = Validator._fact_coverage_stats("这是一条足够长的已引用事实句。[来源: 1]")
    assert stats["fact_sentence_count"] == 1
    assert stats["uncited_fact_sentence_count"] == 0
    assert stats["citation_coverage"] == 1.0


def test_r03_uncited_fact_downgraded_in_repair():
    report = "该公司在二零二五年度的营业收入已经达到九百亿元。"
    snapshots = []
    citations, stats = _validate(report, [], [])
    snapshots.append(citations)
    assert citations == []
    assert stats["uncited_fact_sentence_count"] == 1
    spans = stats["uncited_fact_spans"]
    assert spans and spans[0]["claim"].startswith("该公司")
    repaired, repair_stats = repair_report(report, citations, spans)
    assert REPAIR_UNCITED_NOTE in repaired
    assert spans[0]["claim"] in repaired, "降格标注保留原句（不删除未引用事实）"
    assert repair_stats["downgraded_uncited"] == 1
    # 幂等：再次降格不重复追加
    again, again_stats = repair_report(repaired, citations, spans)
    assert again.count(REPAIR_UNCITED_NOTE) == 1
    assert again_stats["downgraded_uncited"] == 0


def test_r03_route_repair_triggers_on_uncited_facts():
    from research_engine.graph import route_repair

    state = ResearchState(topic="t", report="含未引用事实的报告" * 3,
                          validator_stats={"uncited_fact_sentence_count": 1})
    assert route_repair(state) == "repair"
    state.validator_stats = {"uncited_fact_sentence_count": 0}
    assert route_repair(state) == "render"


def test_r03_render_trust_statement_reports_uncited():
    from research_engine.render import ReportRenderer

    state = SimpleNamespace(validator_stats={
        "fact_sentence_count": 4, "uncited_fact_sentence_count": 2})
    stmt = ReportRenderer().build_trust_statement([_citation("甲", 0, 1, True)], state)
    assert "无引用事实句：2/4" in stmt


# ---------------------------------------------------------------- R04 origin 链

def test_r04_missing_origin_cannot_pass_on_summary():
    claim = "该公司年度营收达到九百亿元"
    summary = _finding(claim, metadata={"origin_evidence_ids": ["missing"]})
    citations, stats = _validate(f"{claim} [来源: 1]", [summary],
                                 [_verdict("1", claim, faithful=True)], evidence=[])
    assert citations[0].verification_failed is True
    assert citations[0].verified is False
    assert "原文证据缺失" in citations[0].note
    assert stats["evidence_missing_count"] == 1
    assert stats["evidence_unknown_citation_count"] == 1


def test_r04_partial_missing_origin_reports_partial_and_unknown():
    raw = _finding("存在的原文内容")
    ensure_evidence_identity([raw])
    index = build_evidence_index([raw])
    summary = _finding("摘要", source=raw.source,
                       metadata={"origin_evidence_ids": [raw.evidence_id, "missing"]})
    text, status = resolve_evidence_text(summary, index)
    assert text == raw.content and status == "partial"

    citations, stats = _validate("[来源: 1] 的论断内容要足够长才能校验", [summary],
                                 [_verdict("1", "的论断内容要足够长才能校验")],
                                 evidence=[raw])
    assert citations[0].verification_failed is True
    assert stats["evidence_partial_count"] == 1
    assert stats["evidence_unknown_citation_count"] == 1


def test_r04_full_origin_chain_judges_normally():
    claim = "召回率提升 12%"
    raw = _finding("前缀" * 50 + "关键事实：召回率提升 12%")
    ensure_evidence_identity([raw])
    working = _finding(claim, source=raw.source,
                       metadata={"origin_evidence_ids": [raw.evidence_id]})
    citations, stats = _validate(f"系统{claim} [来源: 1]", [working],
                                 [_verdict("1", f"系统{claim}")], evidence=[raw])
    assert citations[0].verified is True
    assert stats["evidence_missing_count"] == 0
    assert stats["evidence_partial_count"] == 0


# ---------------------------------------------------------------- R05 预算预占

def _sdk_stub(calls: list, tokens: int = 20):
    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(total_tokens=tokens, prompt_tokens=5,
                                  completion_tokens=tokens - 5),
            id="offline",
        )

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


def test_r05_concurrent_calls_cannot_overspend():
    from config import config

    barrier = threading.Barrier(3)
    calls: list = []

    def create(**kwargs):
        calls.append(kwargs)
        barrier.wait(timeout=5)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=SimpleNamespace(total_tokens=20, prompt_tokens=10, completion_tokens=10),
            id="offline",
        )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client = LLMClient(model="offline", role="review")
    state = SimpleNamespace(token_used=config.research.token_budget - 1)
    errors: list = []

    def invoke():
        try:
            client.chat([{"role": "user", "content": "hi"}], state=state)
        except TokenBudgetExceeded as exc:
            errors.append(exc)

    with patch.object(client, "_get_client", return_value=fake):
        threads = [threading.Thread(target=invoke) for _ in range(3)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)
    barrier.abort()  # 若（错误地）有调用通过准入，避免线程死等

    assert state.token_used <= config.research.token_budget, "预占后不得越过绝对预算"
    assert state.token_used == config.research.token_budget - 1
    assert len(errors) == 3 and calls == [], "余额不足时全部拒绝，且不发起 SDK 调用"


def test_r05_call_caps_output_and_settles_to_actual():
    from config import config

    calls: list = []
    fake = _sdk_stub(calls, tokens=7)
    client = LLMClient(model="offline", role="review")
    remaining = 100
    state = SimpleNamespace(token_used=config.research.token_budget - remaining)
    with patch.object(client, "_get_client", return_value=fake):
        assert client.chat([{"role": "user", "content": "hi"}], state=state) == "ok"
    assert "max_tokens" in calls[0], "预占调用必须显式设置输出上限"
    assert calls[0]["max_tokens"] <= remaining
    # 结算按真实用量：token_used = 预算 - 剩余 + 实际 7
    assert state.token_used == config.research.token_budget - remaining + 7


def test_r05_missing_usage_keeps_reservation_conservatively():
    from config import config

    calls: list = []

    def create(**kwargs):
        calls.append(kwargs)
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
            usage=None, id="offline",
        )

    fake = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    client = LLMClient(model="offline", role="review")
    state = SimpleNamespace(token_used=config.research.token_budget - 50)
    with patch.object(client, "_get_client", return_value=fake):
        client.chat([{"role": "user", "content": "hi"}], state=state)
    assert state.token_used > config.research.token_budget - 50, "缺 usage 时按预占保守计入"


# ---------------------------------------------------------------- R10 owner 守卫

def test_r10_sequence_path_rejects_old_owner_after_takeover():
    from fakes import FakeStore

    store = FakeStore()
    store.create_run("r10-seq", "t", {}, status="QUEUED",
                     timeout_at=datetime.now(UTC) + timedelta(seconds=60))
    assert store.claim_run("r10-seq", "worker-a", 60) is not None
    old = RunOwnership("worker-a", 1)
    assert store.append_event("r10-seq", "RUN_STARTED", {}, sequence=0, owner=old) == 0

    store.runs["r10-seq"]["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert store.sweep_stale_runs() == [{"run_id": "r10-seq", "action": "requeued",
                                         "attempt": 2}]
    with pytest.raises(LeaseLostError):
        store.append_event("r10-seq", "STEP_FINISHED", {}, sequence=1, owner=old)
    events = store.get_events("r10-seq")
    assert [e["sequence"] for e in events] == [0], "旧 attempt 的显式序号事件不得落库"


def test_r10_renew_lease_requires_matching_attempt():
    from fakes import FakeStore

    store = FakeStore()
    store.create_run("r10-lease", "t", {}, status="QUEUED",
                     timeout_at=datetime.now(UTC) + timedelta(seconds=60))
    assert store.claim_run("r10-lease", "worker-a", 60) is not None
    assert store.renew_lease("r10-lease", "worker-a", 60, attempt=1) is True
    assert store.renew_lease("r10-lease", "worker-a", 60, attempt=2) is False
    # 不带 attempt 的兼容签名仍可用（旧调用方）
    assert store.renew_lease("r10-lease", "worker-a", 60) is True
