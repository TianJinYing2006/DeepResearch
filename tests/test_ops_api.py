"""P8-A 指标与告警 API 测试（FakeStore，零外部服务）。

覆盖：`/api/metrics` 形状（有库/无库两态）、`/api/ops/alerts` 阈值触发与静默、
HTTP 状态分类中间件计数、SSE 连接计量进出平衡。
"""
from __future__ import annotations

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.metrics import METRICS
from web.backend.runner import RunManager


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    return TestClient(api.app)


def test_metrics_with_store(client: TestClient):
    store: FakeStore = api.store
    store.create_run("metrics001", "t", {})
    assert store.update_status("metrics001", "SUCCEEDED", allowed_from=("CREATED",),
                               cost_estimate_cny=0.5) is True

    body = client.get("/api/metrics").json()
    assert body["persistence"] is True
    assert body["runs_24h"]["total"] == 1
    assert body["runs_24h"]["success_rate"] == 1.0
    assert body["month_cost_cny"] == 0.5
    assert body["month_budget_cny"] == 1500.0
    assert set(body["http"]) == {"2xx", "4xx", "5xx", "latency_ms_avg"}
    assert body["sse_connections"] >= 0


def test_metrics_without_store(monkeypatch, client: TestClient):
    monkeypatch.setattr(api, "store", None)
    body = TestClient(api.app).get("/api/metrics").json()
    assert body["persistence"] is False
    assert body["runs_24h"] == {}
    assert body["stale_leases"] is None


def test_alerts_fire_on_stale_lease(client: TestClient):
    store: FakeStore = api.store
    store.create_run("stale001", "t", {}, status="QUEUED")
    assert store.claim_run("stale001", "dead-worker", -1) is not None

    body = client.get("/api/ops/alerts").json()
    assert body["alert_count"] >= 1
    assert "stale_leases" in {alert["code"] for alert in body["alerts"]}


def test_alerts_silent_when_clean(monkeypatch, client: TestClient):
    monkeypatch.setattr(api, "ALERT_5XX_RATE_PCT", 10**9)
    monkeypatch.setattr(api, "ALERT_STALE_RUNS", 10**9)
    monkeypatch.setattr(api, "ALERT_QUEUE_DEPTH", 10**9)
    body = client.get("/api/ops/alerts").json()
    assert body["alert_count"] == 0


def test_http_middleware_counts_status_classes(client: TestClient):
    before = METRICS.snapshot()["http"]["4xx"]
    assert client.get("/api/research/does-not-exist").status_code == 404
    after = METRICS.snapshot()["http"]["4xx"]
    assert after >= before + 1


def test_sse_gauge_balanced_after_stream(monkeypatch, client: TestClient):
    store: FakeStore = api.store
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=2, delay=0.01),
                                   store=store, max_concurrent_runs=8))
    run_id = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    with client.stream("GET", f"/api/research/{run_id}/stream") as response:
        for _ in response.iter_lines():
            pass
    assert METRICS.snapshot()["sse_connections"] == 0
