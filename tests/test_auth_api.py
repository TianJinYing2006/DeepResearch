"""P4-A 鉴权 API 测试（FakeStore，零 PostgreSQL）。

覆盖：开放注册（无需邀请码/邮箱重复）、登录/登出/会话、CSRF 双提交、
运行接口的 401（未登录）与 403（缺 CSRF）、跨用户越权一律 404（不泄露存在性）。
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE, token_hash
from web.backend.runner import RunManager

PASSWORD = "password-123456"


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=2, delay=0.01),
                                   store=store, max_concurrent_runs=8))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    return TestClient(api.app)


def _register(client: TestClient, email: str):
    return client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "agree_terms": True})


def _login(client: TestClient, email: str):
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD})


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def test_register_is_open_without_invite(client: TestClient):
    assert _register(client, "a@example.com").status_code == 200
    assert client.cookies.get(SESSION_COOKIE)
    assert client.cookies.get(CSRF_COOKIE)

    assert _register(client, "b@example.com").status_code == 200
    # 邮箱重复（大小写归一后）仍拒绝
    assert _register(client, "A@EXAMPLE.COM").status_code == 409



def test_login_logout_and_session(client: TestClient):
    assert _register(client, "a@example.com").status_code == 200
    assert client.post("/api/auth/logout", headers=_csrf(client)).json()["ok"] is True
    assert client.get("/api/auth/session").status_code == 401

    bad = client.post("/api/auth/login",
                      json={"email": "a@example.com", "password": "wrong-password"})
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "invalid_credentials"

    ok = _login(client, "a@example.com")
    assert ok.status_code == 200
    session = client.get("/api/auth/session").json()["user"]
    assert session["email"] == "a@example.com"

    assert client.post("/api/auth/logout", headers=_csrf(client)).json()["ok"] is True
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
    assert _register(client, "owner@example.com").status_code == 200
    run_id = client.post("/api/research", json={"topic": "t"}, headers=_csrf(client)).json()["run_id"]

    # 换第二个用户（清掉 cookie 再注册）
    client.cookies.clear()
    assert _register(client, "other@example.com").status_code == 200

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


def test_register_requires_consent_and_records_versions(client: TestClient, store: FakeStore):
    """需求 25：注册必须勾选同意；成功后按文档 hash 留档 terms/privacy 两行。"""
    without = client.post("/api/auth/register",
                          json={"email": "noconsent@example.com", "password": PASSWORD})
    assert without.status_code == 422
    assert without.json()["detail"]["code"] == "invalid_request"
    assert store.users == {}  # 未勾选不建用户、不消耗邀请码

    response = _register(client, "consent@example.com")
    assert response.status_code == 200
    user_id = response.json()["user"]["user_id"]
    rows = store.list_user_consents(user_id)
    assert {row["doc_type"] for row in rows} == {"terms", "privacy"}
    assert all(len(row["version"]) == 12 for row in rows)


def test_login_unknown_user_still_runs_password_hash(client: TestClient, monkeypatch):
    """防计时枚举：用户不存在也要做一次 Argon2 校验（与密码错误耗时同量级）。"""
    import web.backend.auth as auth_mod
    calls: list[str] = []
    real = auth_mod.verify_password
    monkeypatch.setattr(auth_mod, "verify_password",
                        lambda h, p: calls.append(h) or real(h, p))
    resp = client.post("/api/auth/login",
                       json={"email": "nobody@example.com", "password": "whatever-123456"})
    assert resp.status_code == 401
    assert resp.json()["detail"]["code"] == "invalid_credentials"
    assert len(calls) == 1 and calls[0].startswith("$argon2")


def test_store_failure_does_not_leak_exception_text(client: TestClient, monkeypatch):
    """异常原文（可能含 DB 内部信息）不得进入响应体，只返回固定文案。"""
    from web.backend import main as api_mod

    def boom(*args, **kwargs):
        raise RuntimeError("password authentication failed for user dr_prod host=10.0.0.5")

    monkeypatch.setattr(api_mod.store, "get_user_by_email", boom)
    resp = client.post("/api/auth/login",
                       json={"email": "a@example.com", "password": "whatever-123456"})
    body = resp.text
    assert resp.status_code >= 500 or resp.status_code == 503
    assert "dr_prod" not in body and "10.0.0.5" not in body and "RuntimeError" not in body


# ------------------------------------------------- 需求 42：会话与重认证加固

class _DenyLimiter:
    def allow(self, _key: str) -> bool:
        return False


def test_logout_requires_csrf(client: TestClient):
    """需求 42：登出是写路由，必须带 CSRF 双提交头（防强制登出）。"""
    assert _register(client, "a@example.com").status_code == 200
    denied = client.post("/api/auth/logout")                     # 无 CSRF 头
    assert denied.status_code == 403
    assert denied.json()["detail"]["code"] == "csrf_failed"
    assert client.get("/api/auth/session").status_code == 200    # 未被登出

    ok = client.post("/api/auth/logout", headers=_csrf(client))
    assert ok.json()["ok"] is True
    assert client.get("/api/auth/session").status_code == 401


def test_logout_deletes_cookies_with_matching_attributes(client: TestClient, monkeypatch):
    """需求 42：Secure 模式删除 `__Host-` Cookie 必须带 Secure，否则浏览器忽略删除。"""
    assert _register(client, "a@example.com").status_code == 200
    monkeypatch.setattr(api, "COOKIE_SECURE", True)
    response = client.post("/api/auth/logout", headers=_csrf(client))
    assert response.status_code == 200
    set_cookies = response.headers.get_list("set-cookie")
    session_cookie = next(c for c in set_cookies if c.startswith(f"{api.SESSION_COOKIE}="))
    csrf_cookie = next(c for c in set_cookies if c.startswith(f"{api.CSRF_COOKIE}="))
    for header in (session_cookie, csrf_cookie):
        assert "Secure" in header and "Path=/" in header and "Max-Age=0" in header
    assert "HttpOnly" in session_cookie        # 会话 Cookie 仍 httpOnly
    assert "HttpOnly" not in csrf_cookie       # CSRF 需前端可读


def test_reauth_password_failure_is_audited(client: TestClient, store: FakeStore):
    """需求 42：改密 / 注销账号的密码错误必须留审计（修复前直接 401、无审计）。"""
    assert _register(client, "a@example.com").status_code == 200

    changed = client.post("/api/auth/password",
                          json={"current_password": "wrong-password",
                                "new_password": "new-password-999"},
                          headers=_csrf(client))
    assert changed.status_code == 401
    deleted = client.request("DELETE", "/api/auth/account",
                             json={"password": "wrong-password"}, headers=_csrf(client))
    assert deleted.status_code == 401

    rows = store.list_audit(action="reauth_failed")
    actions = [row["detail"]["action"] for row in rows]
    assert "password_change" in actions and "account_delete" in actions
    assert all(row["detail"]["reason"] == "bad_password" for row in rows)
    assert "wrong-password" not in str(store.audits)   # 审计不落密码原文


def test_reauth_rate_limited(client: TestClient, monkeypatch, store: FakeStore):
    """需求 42：重认证按用户限流（超限 429 + 审批审计；正确密码也不放行）。"""
    assert _register(client, "a@example.com").status_code == 200
    monkeypatch.setattr(api, "REAUTH_LIMITER", _DenyLimiter())
    changed = client.post("/api/auth/password",
                          json={"current_password": PASSWORD,
                                "new_password": "new-password-999"},
                          headers=_csrf(client))
    assert changed.status_code == 429
    assert changed.json()["detail"]["code"] == "rate_limited"

    revoked = client.request("DELETE", "/api/auth/sessions",
                             json={"password": PASSWORD}, headers=_csrf(client))
    assert revoked.status_code == 429
    assert store.list_audit(action="reauth_rate_limited")


# ------------------------------------------------- 需求 42：改密作废旧重置 token

def test_change_password_invalidates_pending_reset_tokens(client: TestClient, store: FakeStore):
    """需求 42：改密必须作废未使用的重置 token（同事务）。

    修复前 `update_password` 不清理 `password_reset_tokens`（`delete_password_resets`
    全仓 0 调用），改密前泄露/申请过的 reset 链接在改密后仍可用于重置密码 ——
    攻击者拿到旧链接即可覆盖新密码，改密的「止损」语义失效。
    """
    assert _register(client, "a@example.com").status_code == 200
    user = store.get_user_by_email("a@example.com")
    raw_token = "reset-token-before-change-1"
    store.create_password_reset(token_hash(raw_token), user["user_id"],
                                datetime.now(UTC) + timedelta(minutes=30))
    assert token_hash(raw_token) in store.password_resets   # 前置：token 确实待用

    changed = client.post("/api/auth/password",
                          json={"current_password": PASSWORD,
                                "new_password": "new-password-999"},
                          headers=_csrf(client))
    assert changed.status_code == 200
    assert store.password_resets == {}                      # 未使用 token 已随之作废

    # 旧 reset 链接不再可用（422 = token 无效/已消费，而非密码强度问题）
    stale = client.post("/api/auth/reset",
                        json={"token": raw_token, "new_password": "reset-password-777"})
    assert stale.status_code == 422
    assert stale.json()["detail"]["code"] == "invalid_request"
