"""P1-1 幂等键请求指纹单测（FakeStore + 一步假图，零 PostgreSQL / LLM）。

同键同载荷 ⇒ 复用；同键不同载荷（topic / instructions / profile 任一不同）⇒ 409；
旧行（`request_hash` 为 NULL）按遗留口径放行；不同键 ⇒ 各自创建。
"""
from __future__ import annotations

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.runner import RunManager


class _OneStepGraph:
    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        from research_engine.state import ResearchState
        from research_engine.streaming import STOP_COMPLETED, RunStep
        state = ResearchState(topic=topic)
        yield RunStep(index=0, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=_OneStepGraph, store=store,
                                   max_concurrent_runs=8))
    return TestClient(api.app)


def _start(client: TestClient, **payload) -> object:
    return client.post("/api/research", json=payload)


def test_same_key_same_payload_replays(client: TestClient):
    first = _start(client, topic="A", idempotency_key="k1")
    second = _start(client, topic="A", idempotency_key="k1")
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["run_id"] == second.json()["run_id"]


def test_same_key_different_topic_conflicts(client: TestClient):
    assert _start(client, topic="A", idempotency_key="k2").status_code == 200
    conflict = _start(client, topic="B", idempotency_key="k2")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_same_key_different_profile_conflicts(client: TestClient):
    assert _start(client, topic="A", profile="quick", idempotency_key="k3").status_code == 200
    conflict = _start(client, topic="A", profile="standard", idempotency_key="k3")
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_same_key_different_instructions_conflicts(client: TestClient):
    assert _start(client, topic="A", instructions="x", idempotency_key="k4").status_code == 200
    conflict = _start(client, topic="A", instructions="y", idempotency_key="k4")
    assert conflict.status_code == 409


def test_legacy_null_hash_replays(client: TestClient, monkeypatch):
    store = api.store
    row, created = store.create_run("legacy-run-1", "旧主题", {}, status="QUEUED",
                                    idempotency_key="k5")
    assert created and row["request_hash"] is None

    replayed = _start(client, topic="完全不同", idempotency_key="k5")
    assert replayed.status_code == 200
    assert replayed.json()["run_id"] == "legacy-run-1"


def test_different_keys_create_distinct_runs(client: TestClient):
    first = _start(client, topic="A", idempotency_key="k6")
    second = _start(client, topic="A", idempotency_key="k7")
    assert first.json()["run_id"] != second.json()["run_id"]
