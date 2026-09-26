"""P3-A Worker 单元测试（FakeStore + FakeQueue，零 PostgreSQL / Redis）。

真实 PostgreSQL + Redis 的队列/Worker/API 端到端见 `test_worker_integration.py`。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

from fakes import BoomGraph, FakeQueue, FakeStore, TinyGraph, event_types, wait_terminal

from web.backend.worker import Worker


def _queued(store: FakeStore, *, timeout_seconds: float = 3600) -> str:
    run_id = "run-" + uuid.uuid4().hex[:10]
    store.create_run(run_id, "测试主题", {"instructions": ""}, status="QUEUED",
                     timeout_at=datetime.now(UTC) + timedelta(seconds=timeout_seconds))
    return run_id


def _worker(store: FakeStore, graph, *, heartbeat_seconds: float = 5) -> Worker:
    return Worker(store, FakeQueue(), graph_factory=lambda: graph,
                  worker_id="test-worker", lease_seconds=60,
                  heartbeat_seconds=heartbeat_seconds, poll_seconds=0)


def test_worker_claims_executes_and_persists():
    store = FakeStore()
    run_id = _queued(store)
    worker = _worker(store, TinyGraph(report="# 队列报告"))
    assert worker.run_once(run_id) is True

    row = wait_terminal(store, run_id)
    assert row["status"] == "SUCCEEDED"
    assert row["worker_id"] == "test-worker"
    assert row["stop_reason"] == "completed"
    assert row["started_at"] is not None and row["finished_at"] is not None
    sequences = [item["sequence"] for item in store.get_events(run_id)]
    assert sequences == list(range(len(sequences)))
    assert event_types(store, run_id)[0] == "RUN_STARTED"
    assert event_types(store, run_id)[-1] == "RUN_FINISHED"
    assert store.get_artifact(run_id, "report_md") == "# 队列报告"
    assert store.has_artifact(run_id, "export_json") is True


def test_worker_skips_already_claimed_run():
    store = FakeStore()
    run_id = _queued(store)
    assert store.claim_run(run_id, "other-worker", 60) is not None
    assert _worker(store, TinyGraph()).run_once(run_id) is False
    assert store.get_run(run_id)["worker_id"] == "other-worker"


def test_worker_cancel_at_node_boundary():
    store = FakeStore()
    run_id = _queued(store)
    worker = _worker(store, TinyGraph(steps=50, delay=0.02))

    def cancel_soon() -> None:
        time.sleep(0.1)
        store.request_cancel(run_id)

    threading.Thread(target=cancel_soon, daemon=True).start()
    assert worker.run_once(run_id) is True

    row = wait_terminal(store, run_id)
    assert row["status"] == "CANCELLED"
    assert row["stop_reason"] == "user_cancelled"
    assert event_types(store, run_id).count("STEP_FINISHED") < 50


def test_worker_timeout_maps_to_timed_out():
    store = FakeStore()
    run_id = _queued(store, timeout_seconds=-1)
    worker = _worker(store, TinyGraph(steps=5, delay=0.01))
    assert worker.run_once(run_id) is True

    row = wait_terminal(store, run_id)
    assert row["status"] == "TIMED_OUT"
    assert row["stop_reason"] == "timeout"


def test_worker_crash_marks_failed_with_error_event():
    store = FakeStore()
    run_id = _queued(store)
    worker = _worker(store, BoomGraph())
    assert worker.run_once(run_id) is True

    row = wait_terminal(store, run_id)
    assert row["status"] == "FAILED"
    assert event_types(store, run_id)[-1] == "RUN_ERROR"


def test_worker_renews_lease_during_run():
    store = FakeStore()
    run_id = _queued(store)
    renewals: list[float] = []
    original = store.renew_lease

    def counting(run_id_: str, worker_id: str, lease_seconds: int) -> bool:
        renewals.append(time.monotonic())
        return original(run_id_, worker_id, lease_seconds)

    store.renew_lease = counting  # type: ignore[method-assign]
    worker = _worker(store, TinyGraph(steps=5, delay=0.05), heartbeat_seconds=0.02)
    assert worker.run_once(run_id) is True

    assert len(renewals) >= 1
    assert store.get_run(run_id)["worker_status"] == "alive"
