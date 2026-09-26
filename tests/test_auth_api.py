"""P4-A 鉴权 API 测试（FakeStore，零 PostgreSQL）。

覆盖：邀请制注册（一次性/无效/邮箱重复）、登录/登出/会话、CSRF 双提交、
运行接口的 401（未登录）与 403（缺 CSRF）、跨用户越权一律 404（不泄露存在性）。
"""
from __future__ import annotations

import time

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, token_hash
from web.backend.runner import RunManager

PASSWORD = "password-123456"


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    fake.create_invite(token_hash("invite-1"), created_by="test")
    fake.create_invite(token_hash("invite-2"), created_by="test")
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=2, delay=0.01),
                                   store=store, max_concurrent_runs=8))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", True)
    return TestClient(api.app)


def _register(client: TestClient, email: str, code: str = "invite-1"):
    return client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "invite_code": code})


def _login(client: TestClient, email: str):
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def test_register_requires_valid_one_time_invite(client: TestClient):
    assert _register(client, "a@example.com", code="").status_code == 400
    assert _register(client, "a@example.com", code="wrong-code").status_code == 400

    assert _register(client, "a@example.com").status_code == 200
    assert client.cookies.get(SESSION_COOKIE)
    assert client.cookies.get(CSRF_COOKIE)

    # 邀请码一次性：同一码不能再注册
    assert _register(client, "b@example.com").status_code == 400
    # 邮箱重复
    assert _register(client, "A@EXAMPLE.COM", code="invite-2").status_code == 409
    # 上一条失败不应消耗 invite-2
    assert _register(client, "c@example.com", code="invite-2").status_code == 200


def test_login_logout_and_session(client: TestClient):
    assert _register(client, "a@example.com").status_code == 200
    assert client.post("/api/auth/logout").json()["ok"] is True
    assert client.get("/api/auth/session").status_code == 401

    bad = client.post("/api/auth/login",
                      json={"email": "a@example.com", "password": "wrong-password"})
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "invalid_credentials"

    ok = _login(client, "a@example.com")
    assert ok.status_code == 200
    session = client.get("/api/auth/session").json()["user"]
    assert session["email"] == "a@example.com"

    assert client.post("/api/auth/logout").json()["ok"] is True
    assert client.get("/api/auth/session").status_code == 401


def test_run_endpoints_require_auth_and_csrf(client: TestClient):
    assert client.post("/api/research", json={"topic": "t"}).status_code == 401

    assert _register(client, "a@example.com").status_code == 200
    without_csrf = client.post("/api/research", json={"topic": "t"})
    assert without_csrf.status_code == 403
    assert without_csrf.json()["detail"]["code"] == "csrf_failed"

    mismatched = client.post("/api/research", json={"topic": "t"},
                             headers={CSRF_HEADER: "not-the-cookie"})
    assert mismatched.status_code == 403

    started = client.post("/api/research", json={"topic": "t"}, headers=_csrf(client))
    assert started.status_code == 200
    run_id = started.json()["run_id"]
    assert api.store.get_run(run_id)["user_id"] is not None  # 归属已写入任务库


def test_cross_user_access_is_404(client: TestClient, store: FakeStore):
    assert _register(client, "owner@example.com", code="invite-1").status_code == 200
    run_id = client.post("/api/research", json={"topic": "t"}, headers=_csrf(client)).json()["run_id"]

    # 换第二个用户（清掉 cookie 再注册）
    client.cookies.clear()
    assert _register(client, "other@example.com", code="invite-2").status_code == 200

    assert client.get(f"/api/research/{run_id}").status_code == 404
    assert client.get(f"/api/research/{run_id}/report").status_code == 404
    assert client.post(f"/api/research/{run_id}/cancel", headers=_csrf(client)).status_code == 404
    assert client.get(f"/api/research/{run_id}/stream").status_code == 404
    assert client.post(f"/api/research/{run_id}/cancel", headers=_csrf(client)).status_code == 404
    # 历史列表只看得到自己的
    assert client.get("/api/runs").json()["runs"] == []


def test_owner_can_read_and_cancel(client: TestClient):
    assert _register(client, "owner@example.com").status_code == 200
    run_id = client.post("/api/research", json={"topic": "t"}, headers=_csrf(client)).json()["run_id"]

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        snap = client.get(f"/api/research/{run_id}").json()
        if snap["status"] == "finished":
            break
        time.sleep(0.02)
    assert client.get(f"/api/research/{run_id}").status_code == 200
    assert client.get(f"/api/research/{run_id}/stream").status_code == 200
    assert client.post(f"/api/research/{run_id}/cancel", headers=_csrf(client)).status_code == 200
    assert client.get("/api/runs").json()["runs"][0]["run_id"] == run_id


def test_register_without_invite_when_not_invite_only(monkeypatch, client: TestClient):
    monkeypatch.setattr(api, "INVITE_ONLY", False)
    response = client.post("/api/auth/register",
                           json={"email": "free@example.com", "password": PASSWORD})
    assert response.status_code == 200


def test_change_password_revokes_other_sessions(client: TestClient):
    assert _register(client, "a@example.com").status_code == 200
    other = TestClient(api.app)
    assert other.post("/api/auth/login",
                      json={"email": "a@example.com", "password": PASSWORD}).status_code == 200

    changed = client.post("/api/auth/password",
                          json={"current_password": PASSWORD, "new_password": "new-password-999"},
                          headers=_csrf(client))
    assert changed.status_code == 200
    assert other.get("/api/auth/session").status_code == 401   # 其它设备被吊销
    assert client.get("/api/auth/session").status_code == 200  # 当前设备保持登录

    fresh = TestClient(api.app)
    assert fresh.post("/api/auth/login", json={
        "email": "a@example.com", "password": PASSWORD}).status_code == 401
    assert fresh.post("/api/auth/login", json={
        "email": "a@example.com", "password": "new-password-999"}).status_code == 200


def test_change_password_requires_login_and_current_password(client: TestClient):
    anon = TestClient(api.app)
    assert anon.post("/api/auth/password", json={
        "current_password": "whatever", "new_password": "new-password-999"}).status_code == 401

    assert _register(client, "a@example.com").status_code == 200
    wrong = client.post("/api/auth/password",
                        json={"current_password": "wrong-password",
                              "new_password": "new-password-999"},
                        headers=_csrf(client))
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_credentials"
