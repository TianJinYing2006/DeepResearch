"""P1-3 Worker 注册表 / draining / readiness 单测（FakeStore，零 PostgreSQL / Redis）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fakes import FakeQueue, FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.worker import Worker


def _worker(store: FakeStore) -> Worker:
    return Worker(store, FakeQueue(), graph_factory=lambda: TinyGraph(),
                  worker_id="reg-worker-1", lease_seconds=60, heartbeat_seconds=5)


def test_registry_register_heartbeat_and_status():
    store = FakeStore()
    worker = _worker(store)

    worker.register()
    row = store.list_workers()[0]
    assert row["worker_id"] == "reg-worker-1" and row["status"] == "active"
    assert row["in_flight"] == 0 and row["current_run_id"] is None

    worker._current_run_id = "run-123"
    worker._registry_beat()
    row = store.list_workers()[0]
    assert row["in_flight"] == 1 and row["current_run_id"] == "run-123"

    worker._current_run_id = None
    worker._registry_beat()
    assert store.list_workers()[0]["in_flight"] == 0

    worker.stop()
    assert store.list_workers()[0]["status"] == "draining"
    worker._mark_status("stopped")
    assert store.list_workers()[0]["status"] == "stopped"


def test_heartbeat_re_registers_missing_row():
    store = FakeStore()
    worker = _worker(store)
    worker.register()
    store.workers.clear()  # 模拟行被清理

    worker._registry_beat()  # 心跳失败 ⇒ 自动重注册

    assert store.count_live_workers() == 1


def test_stale_worker_rows_are_purged():
    store = FakeStore()
    store.register_worker("old-worker")
    store.workers["old-worker"]["status"] = "stopped"
    store.workers["old-worker"]["last_heartbeat_at"] = datetime.now(UTC) - timedelta(days=10)

    assert store.purge_stale_workers(days=7) == 1
    assert store.list_workers() == []


def test_readiness_requires_live_worker_in_queue_mode(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "EXECUTION_MODE", "queue")
    monkeypatch.delenv("DR_DATABASE_URL", raising=False)
    monkeypatch.delenv("DR_REDIS_URL", raising=False)
    client = TestClient(api.app)

    missing = client.get("/api/health/ready")
    assert missing.status_code == 503
    assert missing.json()["checks"]["worker"]["status"] == "unavailable"

    store.register_worker("w-live")
    ready = client.get("/api/health/ready")
    assert ready.status_code == 200
    assert ready.json()["checks"]["worker"] == {
        "status": "ok", "live": 1, "max_age_seconds": 90}


def test_ops_alerts_reports_missing_worker(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "EXECUTION_MODE", "queue")
    client = TestClient(api.app)

    alerts = client.get("/api/ops/alerts").json()["alerts"]
    assert any(item["code"] == "worker_heartbeat_missing" for item in alerts)

    store.register_worker("w-live")
    alerts = TestClient(api.app).get("/api/ops/alerts").json()["alerts"]
    assert not any(item["code"] == "worker_heartbeat_missing" for item in alerts)
