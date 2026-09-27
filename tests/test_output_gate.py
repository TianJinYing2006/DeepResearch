"""P0-4 输出闸单测（FakeStore，零 PostgreSQL / 零 LLM）。

- `apply_output_gate`：命中词表 ⇒ 事件 payload 脱敏 + `output_under_review`；
- 导出接口：`flagged` ⇒ 403 `output_under_review`；正常 ⇒ 200；
- 历史回放：修复前落库的完整正文在 `flagged` 状态下也不回放（脱敏）；
- 终局同事务写审核状态（`persist_terminal(moderation_status=...)`）；
- Worker 路径：事件脱敏但产物保留原文，供人工复核。
"""
from __future__ import annotations

from datetime import UTC, datetime

from fakes import FakeQueue, FakeStore
from fastapi.testclient import TestClient

from research_engine.state import ResearchState
from web.backend import main as api
from web.backend.moderation import apply_output_gate
from web.backend.persistence import persist_terminal
from web.backend.worker import Worker


def _seed_run(store: FakeStore, run_id: str, *, body: str, flagged: bool) -> None:
    store.create_run(run_id, "t", {}, status="RUNNING")
    store.append_event(run_id, "RUN_FINISHED", {
        "stop_reason": "completed", "has_report": True,
        "result": {"report": body},
    }, sequence=0)
    store.put_artifact(run_id, "report_md", body)
    assert store.update_status(run_id, "SUCCEEDED", allowed_from=("RUNNING",),
                               stop_reason="completed", finished_at=datetime.now(UTC))
    if flagged:
        store.set_moderation_status(run_id, "flagged")


def test_apply_output_gate_redacts_and_flags(monkeypatch):
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", "badword")
    payload = {"result": {"report": "含 badword 正文"}, "has_report": True}
    matches = apply_output_gate(payload, "含 badword 正文")
    assert matches == ["badword"]
    assert payload["result"]["report"] == ""
    assert payload["output_under_review"] is True

    clean = {"result": {"report": "干净的报告"}}
    assert apply_output_gate(clean, "干净的报告") == []
    assert clean["result"]["report"] == "干净的报告"
    assert "output_under_review" not in clean


def test_export_flagged_returns_403(monkeypatch):
    store = FakeStore()
    _seed_run(store, "flagged001", body="# 含 badword 的报告", flagged=True)
    monkeypatch.setattr(api, "store", store)

    response = TestClient(api.app).get("/api/research/flagged001/report?format=md")
    assert response.status_code == 403
    assert response.json()["detail"]["code"] == "output_under_review"
    assert "badword" not in response.text


def test_export_unflagged_still_works(monkeypatch):
    store = FakeStore()
    _seed_run(store, "clean0001", body="# 正常报告", flagged=False)
    monkeypatch.setattr(api, "store", store)

    response = TestClient(api.app).get("/api/research/clean0001/report?format=md")
    assert response.status_code == 200
    assert response.text == "# 正常报告"


def test_stored_stream_redacts_flagged_payload(monkeypatch):
    """历史行（修复前落库）即使事件带完整正文，flagged 回放也必须脱敏。"""
    store = FakeStore()
    _seed_run(store, "legacyflag1", body="# 含 badword 的报告", flagged=True)
    monkeypatch.setattr(api, "store", store)

    with TestClient(api.app).stream("GET", "/api/research/legacyflag1/stream") as response:
        body = "".join(response.iter_lines())
    assert "badword" not in body
    assert "output_under_review" in body


def test_persist_terminal_writes_moderation_status_atomically():
    store = FakeStore()
    store.create_run("modatom01", "t", {}, status="RUNNING")
    finalized = persist_terminal(
        store, "modatom01", 0, "RUN_FINISHED",
        {"stop_reason": "completed", "has_report": True},
        report="# 正文", moderation_status="flagged")
    assert finalized is True
    assert store.get_run("modatom01")["moderation_status"] == "flagged"
    assert store.get_artifact("modatom01", "report_md") == "# 正文"


def test_worker_redacts_event_but_keeps_artifact(monkeypatch):
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", "badword")
    store = FakeStore()
    store.create_run("workerflag1", "t", {}, status="QUEUED")
    assert store.claim_run("workerflag1", "w1", 120) is not None
    worker = Worker(store, FakeQueue(), graph_factory=lambda: None)

    state = ResearchState(topic="t")
    state.report = "含 badword 的报告正文"
    worker._persist_result("workerflag1", store.get_run("workerflag1"), state,
                           "completed", cancelled=False)

    payload = store.get_events("workerflag1")[-1]["payload"]
    assert payload["result"]["report"] == ""
    assert payload["output_under_review"] is True
    assert "badword" in (store.get_artifact("workerflag1", "report_md") or "")
    assert store.get_run("workerflag1")["moderation_status"] == "flagged"
