"""需求 36 Phase 3：报告后追问（离线单测；FakeStore + TestClient + 假 LLM 单点）。

覆盖 §12.3 契约：证据池 grounding（引用 ID 白名单）/ 上下文与回答长度上限 /
记账（usage sink + run 成本增量）/ 审计事件 / 各前置闸 / 失败不影响原 run。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from config import config
from research_engine.usage import UsageRecord, emit_usage
from web.backend import followup as followup_mod
from web.backend import main as api

MANIFEST = {
    "manifest_version": 1,
    "run_id": "fu-run-0001",
    "working_findings": [
        {"n": 1, "evidence_id": "ev_abc", "source": "https://a.example",
         "source_type": "web", "sq_id": "q1"},
        {"n": 2, "evidence_id": "ev_def", "source": "rag:report.pdf",
         "source_type": "rag", "sq_id": "q1"},
    ],
    "citations": [],
}


def _seed_finished(store: FakeStore, run_id: str = "fu-run-0001", *, flagged: bool = False,
                   report: str = "# 报告\n结论甲 [来源: 1]") -> None:
    store.create_run(run_id, "t", {}, status="RUNNING")
    assert store.update_status(run_id, "SUCCEEDED", allowed_from=("RUNNING",),
                               stop_reason="completed", finished_at=datetime.now(UTC))
    store.put_artifact(run_id, "report_md", report)
    store.put_artifact(run_id, "evidence_manifest", json.dumps(MANIFEST, ensure_ascii=False))
    if flagged:
        store.set_moderation_status(run_id, "flagged")


def _fake_llm(monkeypatch, *, data, tokens=30, raise_exc=None):
    def fake(system, user):
        if raise_exc is not None:
            raise raise_exc
        if tokens:
            emit_usage(UsageRecord(kind="llm", provider="dashscope", model="qwen-plus",
                                   role="follow_up", input_tokens=tokens - 10,
                                   output_tokens=10, total_tokens=tokens))
        return data, tokens

    monkeypatch.setattr(followup_mod, "_ask_llm", fake)


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(config.hitl, "followup_enabled", True)
    return TestClient(api.app)


def test_build_context_caps_and_pool():
    pool = followup_mod.pool_ids(MANIFEST)
    assert pool == {"ev_abc", "ev_def"}
    assert followup_mod.pool_ids(None) == set()
    assert followup_mod.pool_ids({"working_findings": [{"evidence_id": ""}]}) == set()

    context = followup_mod.build_context("报告正文", MANIFEST)
    assert "【报告】" in context and "报告正文" in context
    assert "ev_abc" in context and "ev_def" in context
    assert "【证据清单（可引用 ID）】" in context

    long_context = followup_mod.build_context(
        "x" * (followup_mod.MAX_CONTEXT_REPORT_CHARS + 100), None)
    assert "已截断" in long_context
    assert "（无证据清单）" in long_context


def test_validate_response_whitelist_and_caps():
    result = followup_mod.validate_response(
        {"answer": "结论", "citations": ["ev_abc", "ev_abc", "ev_fake", ""]},
        {"ev_abc", "ev_def"})
    assert result["citations"] == ["ev_abc"]  # 去重 + 白名单通过
    assert result["unverified_ids"] == ["ev_fake"]  # 池外 ID 如实标注
    assert result["grounded"] is False  # 存在未核实引用

    clean = followup_mod.validate_response(
        {"answer": "结论", "citations": ["ev_abc"]}, {"ev_abc"})
    assert clean["grounded"] is True

    no_cite = followup_mod.validate_response({"answer": "结论", "citations": []}, {"ev_abc"})
    assert no_cite["grounded"] is False  # 无引用 = 未按材料作答

    capped = followup_mod.validate_response(
        {"answer": "y" * (followup_mod.MAX_ANSWER_CHARS + 10),
         "citations": [f"ev_{i}" for i in range(followup_mod.MAX_CITATIONS + 5)]},
        {f"ev_{i}" for i in range(followup_mod.MAX_CITATIONS + 5)})
    assert len(capped["answer"]) == followup_mod.MAX_ANSWER_CHARS
    assert len(capped["citations"]) == followup_mod.MAX_CITATIONS


def test_followup_endpoint_happy_path(client, monkeypatch):
    store = FakeStore()
    _seed_finished(store)
    monkeypatch.setattr(api, "store", store)
    _fake_llm(monkeypatch, data={"answer": "依据材料，结论成立。",
                                 "citations": ["ev_abc", "ev_fake"]}, tokens=30)

    response = client.post("/api/research/fu-run-0001/follow-up",
                           json={"question": "结论的证据是什么？"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["answer"] == "依据材料，结论成立。"
    assert body["citations"] == ["ev_abc"]
    assert body["unverified_ids"] == ["ev_fake"]
    assert body["grounded"] is False

    # 记账：usage_ledger（sink）+ run 成本增量（与展示同口径的上界）
    assert len(store.usage) == 1 and store.usage[0]["role"] == "follow_up"
    row = store.get_run("fu-run-0001")
    assert row["token_used"] == 30
    assert row["cost_estimate_cny"] > 0

    # 审计事件（含追问与回答）
    events = [e["event_type"] for e in store.get_events("fu-run-0001")]
    assert "FOLLOWUP_ASKED" in events and "FOLLOWUP_ANSWERED" in events
    assert any(a["action"] == "followup_answered" for a in store.audits)


def test_followup_endpoint_gates(client, monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)

    # 未知 run → 404
    assert client.post("/api/research/no-such/follow-up",
                       json={"question": "q"}).status_code == 404

    # 未终局 → 409 report_not_ready
    store.create_run("fu-open-0001", "t", {}, status="RUNNING")
    resp = client.post("/api/research/fu-open-0001/follow-up", json={"question": "q"})
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "report_not_ready"

    # 终局但无报告 → 404 report_unavailable
    store.update_status("fu-open-0001", "SUCCEEDED", allowed_from=("RUNNING",),
                        stop_reason="completed",
                        finished_at=__import__("datetime").datetime.now(
                            __import__("datetime").UTC))
    resp = client.post("/api/research/fu-open-0001/follow-up", json={"question": "q"})
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "report_unavailable"

    # flagged → 403 output_under_review（与导出同口径）
    _seed_finished(store, "fu-flag-0001", flagged=True)
    resp = client.post("/api/research/fu-flag-0001/follow-up", json={"question": "q"})
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "output_under_review"

    # 开关关闭 → 422 invalid_request
    monkeypatch.setattr(config.hitl, "followup_enabled", False)
    _seed_finished(store, "fu-off-0001")
    resp = client.post("/api/research/fu-off-0001/follow-up", json={"question": "q"})
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_request"


def test_followup_llm_failure_keeps_run(client, monkeypatch):
    store = FakeStore()
    _seed_finished(store, "fu-fail-0001")
    monkeypatch.setattr(api, "store", store)
    before = dict(store.get_run("fu-fail-0001"))
    _fake_llm(monkeypatch, data={}, raise_exc=RuntimeError("upstream down"))

    resp = client.post("/api/research/fu-fail-0001/follow-up", json={"question": "q"})
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "followup_unavailable"

    row = store.get_run("fu-fail-0001")
    assert row["status"] == before["status"]  # 原 run 终态与账目不变
    assert row["token_used"] == before["token_used"]
    events = [e["event_type"] for e in store.get_events("fu-fail-0001")]
    assert events.count("FOLLOWUP_ASKED") == 1 and "FOLLOWUP_ANSWERED" not in events
