"""P1-5 安全审计日志单测（FakeStore，零 PostgreSQL）。

覆盖最小事件集：注册 / 登录成败 / 登出 / 改密 / 注销申请 / CSRF 失败 /
越权访问 / 输入预检拦截；并断言审计中不出现明文密码或会话 token。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, token_hash
from web.backend.moderation_providers import ModerationResult

PASSWORD = "password-123456"


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


def _client(monkeypatch, store: FakeStore, *, auth: bool = True) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", auth)
    monkeypatch.setattr(api, "INVITE_ONLY", False)
    return TestClient(api.app)


def _register(client: TestClient, email: str) -> None:
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def test_auth_lifecycle_is_audited(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client, "audit@example.com")

    client.post("/api/auth/logout")
    bad = client.post("/api/auth/login", json={"email": "audit@example.com", "password": "wrong-pass-xyz"})
    assert bad.status_code == 401
    ok = client.post("/api/auth/login", json={"email": "audit@example.com", "password": PASSWORD})
    assert ok.status_code == 200

    changed = client.post("/api/auth/password",
                          json={"current_password": PASSWORD, "new_password": "new-password-456"},
                          headers=_csrf(client))
    assert changed.status_code == 200

    deleted = client.request("DELETE", "/api/auth/account",
                             json={"password": "new-password-456"}, headers=_csrf(client))
    assert deleted.status_code == 200

    actions = [row["action"] for row in store.list_audit(limit=50)]
    for expected in ("register_success", "logout", "login_failed", "login_success",
                     "password_changed", "account_deletion_requested"):
        assert expected in actions, f"missing audit action: {expected}"

    failed = store.list_audit(action="login_failed")[0]
    assert "email_hash" in failed["detail"]
    assert failed["request_id"]  # X-Request-ID 关联


def test_csrf_and_authz_failures_are_audited(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client, "a@example.com")

    denied_csrf = client.post("/api/research", json={"topic": "t"})  # 无 CSRF 头
    assert denied_csrf.status_code == 403
    assert store.list_audit(action="csrf_failed")

    # 越权：另一个用户访问 a 的 run
    store.create_user("user-b", "b@example.com", "hash")
    store.create_run("run-of-a", "t", {}, user_id=store.get_user_by_email("a@example.com")["user_id"])
    store.create_session(token_hash("tok-b"), "user-b", datetime.now(UTC) + timedelta(hours=1))
    client.cookies.set("dr_session", "tok-b")
    missing = client.get("/api/research/run-of-a")
    assert missing.status_code == 404
    denied = store.list_audit(action="authz_denied")
    assert denied and denied[0]["target_id"] == "run-of-a"


def test_input_blocked_is_audited(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store, auth=False)
    monkeypatch.setattr(api, "scan_text",
                        lambda text: ModerationResult(provider="test", matches=["badword"]))

    response = client.post("/api/research", json={"topic": "t"})
    assert response.status_code == 400
    assert store.list_audit(action="input_blocked")


def test_audit_never_contains_plaintext_secrets(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client, "secret@example.com")
    client.post("/api/auth/login", json={"email": "secret@example.com", "password": PASSWORD})

    serialized = str(store.audits)
    assert PASSWORD not in serialized
    assert "dr_session" not in serialized
    assert "secret@example.com" not in serialized  # 失败事件只记 email_hash
