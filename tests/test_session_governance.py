"""P1-10 会话治理 + 密码重置单测（FakeStore，零 PostgreSQL）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, hash_password, token_hash

PASSWORD = "password-123456"
NEW_PASSWORD = "new-password-456"


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


def _client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", False)
    return TestClient(api.app)


def _register(client: TestClient, email: str = "sess@example.com") -> None:
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def test_sessions_list_shows_metadata_and_current(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client)
    user = store.get_user_by_email("sess@example.com")

    store.create_session(token_hash("other-token"), user["user_id"],
                         datetime.now(UTC) + timedelta(hours=1), ip="198.51.100.9",
                         user_agent="OtherDevice/1.0")

    body = client.get("/api/auth/sessions").json()
    assert len(body["sessions"]) == 2
    current = [item for item in body["sessions"] if item["current"]]
    other = [item for item in body["sessions"] if not item["current"]]
    assert len(current) == 1 and len(other) == 1
    assert other[0]["ip"] == "198.51.100.9" and other[0]["user_agent"] == "OtherDevice/1.0"
    assert current[0]["session_id"]


def test_revoke_other_session_requires_password(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client)
    user = store.get_user_by_email("sess@example.com")
    other_id = store.create_session(token_hash("other-token"), user["user_id"],
                                    datetime.now(UTC) + timedelta(hours=1))

    denied = client.request("DELETE", f"/api/auth/sessions/{other_id}",
                            json={"password": "wrong-pass-xyz"}, headers=_csrf(client))
    assert denied.status_code == 401
    assert store.list_audit(action="session_revoke_denied")

    ok = client.request("DELETE", f"/api/auth/sessions/{other_id}",
                        json={"password": PASSWORD}, headers=_csrf(client))
    assert ok.status_code == 200
    assert len(store.list_sessions(user["user_id"])) == 1  # 当前会话保留


def test_revoke_current_session_logs_out(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client)
    user = store.get_user_by_email("sess@example.com")
    current_id = store.list_sessions(user["user_id"])[0]["session_id"]

    ok = client.request("DELETE", f"/api/auth/sessions/{current_id}",
                        json={}, headers=_csrf(client))
    assert ok.status_code == 200
    assert store.list_sessions(user["user_id"]) == []
    assert client.get("/api/auth/session").status_code == 401


def test_revoke_other_sessions_keeps_current(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client)
    user = store.get_user_by_email("sess@example.com")
    store.create_session(token_hash("other-token"), user["user_id"],
                         datetime.now(UTC) + timedelta(hours=1))

    ok = client.request("DELETE", "/api/auth/sessions", json={"password": PASSWORD},
                        headers=_csrf(client))
    assert ok.status_code == 200 and ok.json()["revoked"] == 1
    assert len(store.list_sessions(user["user_id"])) == 1
    assert client.get("/api/auth/session").status_code == 200


def test_password_reset_flow_single_use_and_revokes_sessions(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client)
    user = store.get_user_by_email("sess@example.com")
    raw_token = "reset-token-abc123456"
    store.create_password_reset(token_hash(raw_token), user["user_id"],
                                datetime.now(UTC) + timedelta(minutes=30))

    assert client.get("/api/auth/session").status_code == 200  # 旧会话存在

    ok = client.post("/api/auth/reset", json={"token": raw_token, "new_password": NEW_PASSWORD})
    assert ok.status_code == 200
    assert client.get("/api/auth/session").status_code == 401  # 全部会话被吊销

    login = client.post("/api/auth/login",
                        json={"email": "sess@example.com", "password": NEW_PASSWORD})
    assert login.status_code == 200

    again = client.post("/api/auth/reset", json={"token": raw_token, "new_password": PASSWORD})
    assert again.status_code == 422  # token 单次消费
    assert store.list_audit(action="password_reset_completed")


def test_expired_reset_token_rejected(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    _register(client)
    user = store.get_user_by_email("sess@example.com")
    store.create_password_reset(token_hash("expired-token-123456"), user["user_id"],
                                datetime.now(UTC) - timedelta(minutes=1))

    response = client.post("/api/auth/reset",
                           json={"token": "expired-token-123456", "new_password": NEW_PASSWORD})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_request"


def test_idle_timeout_invalidates_stale_session():
    store = FakeStore()
    store.create_user("u-idle", "idle@example.com", hash_password(PASSWORD))
    store.create_session(token_hash("idle-token"), "u-idle",
                         datetime.now(UTC) + timedelta(days=7))
    store.sessions[token_hash("idle-token")]["last_seen_at"] = (
        datetime.now(UTC) - timedelta(hours=2))

    assert store.get_session_user(token_hash("idle-token"), idle_seconds=3600) is None
    assert store.get_session_user(token_hash("idle-token"), idle_seconds=0) is not None
