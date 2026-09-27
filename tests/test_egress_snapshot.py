"""P1-9 数据流向快照单测（FakeStore + 一步假图，零 PostgreSQL / LLM）。"""
from __future__ import annotations

import json
import time

from fakes import FakeStore
from fastapi.testclient import TestClient

from config import config
from web.backend import main as api
from web.backend.egress import build_egress_snapshot
from web.backend.profiles import resolve_profile
from web.backend.runner import RunManager


class _OneStepGraph:
    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        from research_engine.state import ResearchState
        from research_engine.streaming import STOP_COMPLETED, RunStep
        state = ResearchState(topic=topic)
        state.report = "# 报告"
        yield RunStep(index=0, node=None, state=state, terminal=True,
                      stop_reason=STOP_COMPLETED)


def test_snapshot_has_no_secrets_and_follows_profile(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "secret-key-xyz")
    monkeypatch.setattr(config.llm, "base_url",
                        "https://dashscope.aliyuncs.com/compatible-mode/v1")
    monkeypatch.setattr(config.search, "provider", "bocha")
    monkeypatch.setattr(config.search, "enable_arxiv", False)

    snapshot = build_egress_snapshot(resolve_profile("standard"))

    assert snapshot["profile"] == "standard"
    assert snapshot["llm"]["model"] == "qwen-plus"
    assert snapshot["llm"]["endpoint_host"] == "dashscope.aliyuncs.com"
    assert snapshot["search"]["provider"] == "bocha"
    assert snapshot["egress"]["arxiv_enabled"] is False
    assert "secret-key-xyz" not in str(snapshot)


def test_run_snapshot_persisted_and_exported(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=_OneStepGraph, store=store,
                                   max_concurrent_runs=4))
    client = TestClient(api.app)

    response = client.post("/api/research", json={"topic": "t", "profile": "quick"})
    run_id = response.json()["run_id"]
    row = store.get_run(run_id)
    assert row["request"]["egress"]["profile"] == "quick"
    assert row["request"]["egress"]["llm"]["model"] == "qwen-turbo"

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and not store.has_artifact(run_id, "export_json"):
        time.sleep(0.01)
    assert store.has_artifact(run_id, "export_json")
    payload = json.loads(store.get_artifact(run_id, "export_json"))
    assert payload["egress"]["profile"] == "quick"

    live = client.get(f"/api/research/{run_id}/report?format=json").json()
    assert live["egress"]["llm"]["model"] == "qwen-turbo"


def test_snapshot_endpoint_falls_back_to_store(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    store.create_run("egress-seed-1", "t", {
        "egress": {"profile": "quick", "llm": {"model": "qwen-turbo"}},
    }, status="RUNNING")

    body = TestClient(api.app).get("/api/research/egress-seed-1").json()
    assert body["egress"]["profile"] == "quick"
    assert body["egress"]["llm"]["model"] == "qwen-turbo"
