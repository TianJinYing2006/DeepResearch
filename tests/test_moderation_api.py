"""P7-A 内容安全与隐私 API 测试（FakeStore / FakeVectorStore，零外部服务）。

覆盖：输入预检拒绝并留痕、长度上限（422）、申诉落库、合规文本接口、
账号注销（验密 + CSRF + RAG 清理的三态回报）、密码不回显。
"""
from __future__ import annotations

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

import research_engine.rag.store as store_module
from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER

PASSWORD = "password-123456"


class _FakeVectorStore:
    reason: str | None = "not_configured"
    deleted: list[str] = []

    @property
    def unavailable_reason(self):
        return type(self).reason

    @property
    def last_error(self):
        return "n/a"

    def delete_by_user(self, user_id: str) -> None:
        type(self).deleted.append(user_id)


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


def _client(monkeypatch, store: FakeStore, *, auth: bool = False) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", auth)
    monkeypatch.setattr(api, "INVITE_ONLY", False)
    return TestClient(api.app)


def _register(client: TestClient, email: str) -> None:
    response = client.post("/api/auth/register", json={"email": email, "password": PASSWORD})
    assert response.status_code == 200


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def test_input_blocked_and_recorded(monkeypatch, store: FakeStore):
    monkeypatch.setattr(api, "scan", lambda text: ["badword"])
    client = _client(monkeypatch, store)

    response = client.post("/api/research", json={"topic": "t"})
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "content_blocked"
    records = store.list_moderation(kind="input_blocked")
    assert len(records) == 1
    assert records[0]["detail"]["matches"] == ["badword"]


def test_input_length_caps(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    assert client.post("/api/research", json={"topic": "x" * 1001}).status_code == 422
    assert client.post(
        "/api/research", json={"topic": "ok", "instructions": "y" * 2001}
    ).status_code == 422


def test_appeal_records_and_requires_login_when_auth_on(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    response = client.post("/api/moderation/appeal", json={"message": "我认为是误判", "run_id": "r1"})
    assert response.status_code == 200
    assert store.list_moderation(kind="appeal")[0]["detail"]["message"] == "我认为是误判"

    auth_client = _client(monkeypatch, store, auth=True)
    assert auth_client.post("/api/moderation/appeal", json={"message": "x"}).status_code == 401


def test_legal_documents(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store)
    privacy = client.get("/api/legal/privacy")
    assert privacy.status_code == 200
    assert "隐私政策" in privacy.json()["markdown"]
    assert client.get("/api/legal/terms").json()["markdown"].startswith("# 用户协议")
    assert client.get("/api/legal/unknown").status_code == 422


def test_password_never_echoed_in_errors(monkeypatch, store: FakeStore):
    client = _client(monkeypatch, store, auth=True)
    _register(client, "a@example.com")
    bad = client.post("/api/auth/login",
                      json={"email": "a@example.com", "password": "wrong-secret-xyz"})
    assert bad.status_code == 401
    assert "wrong-secret-xyz" not in bad.text


def test_delete_account_reports_skipped_rag_cleanup(monkeypatch, store: FakeStore):
    monkeypatch.setattr(store_module, "VectorStore", _FakeVectorStore)
    _FakeVectorStore.reason = "not_configured"
    _FakeVectorStore.deleted = []
    client = _client(monkeypatch, store, auth=True)
    _register(client, "gone@example.com")

    wrong = client.request("DELETE", "/api/auth/account",
                           json={"password": "not-the-password"}, headers=_csrf(client))
    assert wrong.status_code == 401
    assert store.get_user_by_email("gone@example.com") is not None

    response = client.request("DELETE", "/api/auth/account",
                              json={"password": PASSWORD}, headers=_csrf(client))
    assert response.status_code == 200
    assert response.json()["rag_cleanup"].startswith("skipped")
    assert store.get_user_by_email("gone@example.com") is None
    assert client.get("/api/auth/session").status_code == 401


def test_delete_account_cleans_rag_when_available(monkeypatch, store: FakeStore):
    monkeypatch.setattr(store_module, "VectorStore", _FakeVectorStore)
    _FakeVectorStore.reason = None
    _FakeVectorStore.deleted = []
    client = _client(monkeypatch, store, auth=True)
    _register(client, "cleanup@example.com")
    user = store.get_user_by_email("cleanup@example.com")

    response = client.request("DELETE", "/api/auth/account",
                              json={"password": PASSWORD}, headers=_csrf(client))
    assert response.status_code == 200
    assert response.json()["rag_cleanup"] == "done"
    assert _FakeVectorStore.deleted == [user["user_id"]]
