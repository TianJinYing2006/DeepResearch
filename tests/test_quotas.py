"""P4-B 配额与限流 API 测试（FakeStore，零 PostgreSQL）。

覆盖：每日运行次数 / 单用户并发 / 全局月度预算三条闸、幂等提交不受闸、`/api/quota` 快照、
登录限流。默认值来自推荐基线 v2（1 次/日、单用户并发 1、单次 ¥1.50、月度 ¥1500）。
"""
from __future__ import annotations

import time

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, token_hash
from web.backend.ratelimit import FixedWindowLimiter
from web.backend.runner import RunManager

PASSWORD = "password-123456"


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    fake.create_invite(token_hash("invite-1"), created_by="test")
    return fake


def _client(monkeypatch, store: FakeStore, *, steps: int = 2, delay: float = 0.01, **overrides) -> TestClient:
    manager = RunManager(graph_factory=lambda: TinyGraph(steps=steps, delay=delay),
                         store=store, max_concurrent_runs=8)
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager", manager)
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", True)
    monkeypatch.setattr(api, "DAILY_RUNS_PER_USER", 1)
    monkeypatch.setattr(api, "MAX_USER_CONCURRENT", 1)
    monkeypatch.setattr(api, "RUN_BUDGET_CNY", 1.5)
    monkeypatch.setattr(api, "MONTHLY_BUDGET_CNY", 1500.0)
    monkeypatch.setattr(api, "LOGIN_LIMITER", FixedWindowLimiter(100))
    monkeypatch.setattr(api, "SUBMIT_LIMITER", FixedWindowLimiter(100))
    for key, value in overrides.items():
        monkeypatch.setattr(api, key, value)
    client = TestClient(api.app)
    register = client.post("/api/auth/register", json={
        "email": "quota@example.com", "password": PASSWORD, "invite_code": "invite-1"})
    assert register.status_code == 200
    return client


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def _start(client: TestClient, topic: str = "t", **extra):
    return client.post("/api/research", json={"topic": topic, **extra}, headers=_csrf(client))


def _wait_finished(client: TestClient, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = client.get(f"/api/research/{run_id}").json()
        if snap.get("status") == "finished":
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_daily_quota_blocks_second_run(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    first = _start(client, "t1")
    assert first.status_code == 200
    _wait_finished(client, first.json()["run_id"])

    second = _start(client, "t2")
    assert second.status_code == 429
    detail = second.json()["detail"]
    assert detail["code"] == "quota_exceeded"
    assert "今日" in detail["message"]


def test_idempotent_submit_bypasses_daily_quota(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    first = _start(client, "t", idempotency_key="k-1")
    assert first.status_code == 200
    again = _start(client, "t", idempotency_key="k-1")
    assert again.status_code == 200
    assert again.json()["run_id"] == first.json()["run_id"]


def test_per_user_concurrency_quota(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store, steps=50, delay=0.05, DAILY_RUNS_PER_USER=0)
    first = _start(client, "long")
    assert first.status_code == 200
    run_id = first.json()["run_id"]
    time.sleep(0.15)

    blocked = _start(client, "second")
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "quota_exceeded"

    assert client.post(f"/api/research/{run_id}/cancel", headers=_csrf(client)).status_code == 200
    _wait_finished(client, run_id)

    allowed = _start(client, "third")
    assert allowed.status_code == 200


def test_monthly_budget_blocks_new_runs(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store, DAILY_RUNS_PER_USER=0, MONTHLY_BUDGET_CNY=0.0001)
    first = _start(client, "spend")
    assert first.status_code == 200
    run_id = first.json()["run_id"]
    _wait_finished(client, run_id)
    store.runs[run_id]["cost_estimate_cny"] = 0.5  # 人为记账，模拟已产生成本

    blocked = _start(client, "next")
    assert blocked.status_code == 429
    detail = blocked.json()["detail"]
    assert detail["code"] == "quota_exceeded"
    assert "预算" in detail["message"]


def test_quota_endpoint_reports_usage(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store, DAILY_RUNS_PER_USER=3)
    first = _start(client, "t")
    _wait_finished(client, first.json()["run_id"])

    body = client.get("/api/quota").json()
    assert body["daily_runs_used"] == 1
    assert body["daily_runs_limit"] == 3
    assert body["user_concurrent_limit"] == 1
    assert body["run_budget_cny"] == 1.5
    assert body["monthly_budget_cny"] == 1500.0


def test_login_rate_limit(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store, LOGIN_LIMITER=FixedWindowLimiter(2))
    client.post("/api/auth/logout")
    bad = {"email": "quota@example.com", "password": "wrong-password"}
    assert client.post("/api/auth/login", json=bad).status_code == 401
    assert client.post("/api/auth/login", json=bad).status_code == 401
    third = client.post("/api/auth/login", json=bad)
    assert third.status_code == 429
    assert third.json()["detail"]["code"] == "rate_limited"
