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
    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None,
                 resume=False):
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


# ---------------------------------------------------- 审计 #338：created=False 竞态窗口


def _miss_next_lookups(monkeypatch, store, times: int) -> None:
    """让前 `times` 次 get_run_by_idempotency 返回 None（模拟预检查错过并发插入）。

    `create_run` 内部的回查走 FakeStore 行扫描（不经过该方法），因此下一次真实调用
    仍能找到既有行 ⇒ `created=False`——这正是审计发现的 `main._start_queued` /
    `runner.start` 直接 `return row["run_id"]` 的窗口。
    """
    original = store.get_run_by_idempotency
    remaining = {"n": times}

    def flaky(*args, **kwargs):
        if remaining["n"] > 0:
            remaining["n"] -= 1
            return None
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "get_run_by_idempotency", flaky)


def test_race_created_false_same_payload_replays(client: TestClient, monkeypatch):
    """竞态窗口里同 key 同 payload 仍必须复用既有 run（负向对照：守卫不得误伤）。"""
    first = _start(client, topic="A", idempotency_key="k8")
    assert first.status_code == 200

    _miss_next_lookups(monkeypatch, api.store, 2)  # 预检查 + runner 预检查都错过
    replay = _start(client, topic="A", idempotency_key="k8")

    assert replay.status_code == 200
    assert replay.json()["run_id"] == first.json()["run_id"]


def test_race_created_false_different_payload_conflicts(client: TestClient, monkeypatch):
    """审计 #338：created=False 且 request_hash 不同 ⇒ 409，不得静默复用旧 run。"""
    assert _start(client, topic="A", idempotency_key="k9").status_code == 200

    _miss_next_lookups(monkeypatch, api.store, 2)
    conflict = _start(client, topic="B", idempotency_key="k9")

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"


def test_race_created_false_queue_mode_conflicts(client: TestClient, monkeypatch):
    """队列模式（`main._start_queued` 的 created=False 分支）同口径 409。"""
    monkeypatch.setattr(api, "EXECUTION_MODE", "queue")
    assert _start(client, topic="A", idempotency_key="k10").status_code == 200

    # 绕过 _start_queued 的预检查，模拟「预检查与插入之间被并发插入」的窗口
    monkeypatch.setattr(api, "_idempotent_existing", lambda *args, **kwargs: None)
    conflict = _start(client, topic="B", idempotency_key="k10")

    assert conflict.status_code == 409
    assert conflict.json()["detail"]["code"] == "idempotency_conflict"
