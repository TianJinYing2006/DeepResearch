"""需求 20 P1-2：Passkey（WebAuthn）注册 / 登录 / 管理（FakeStore；零 PostgreSQL / 网络）。

覆盖：注册选项与挑战一次性；注册成功落库；登录建会话并更新 sign_count；
计数器不递增（克隆/重放）拒绝；删除需密码、最后一把仍可删（密码为备用方式）；
未配置 RP ID/Origin 时 fail-closed；未知邮箱 begin 不泄露账号存在性。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend import passkeys
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, token_hash
from web.backend.runner import RunManager

PASSWORD = "password-123456"
EMAIL = "passkey@example.com"
CREDENTIAL_ID = b"credential-1"
CREDENTIAL_ID_B64 = passkeys.credential_id_b64(CREDENTIAL_ID)


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    fake.create_invite(token_hash("invite-1"), created_by="test")
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=1),
                                   store=store, max_concurrent_runs=4))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", True)
    return TestClient(api.app)


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def _register(client: TestClient):
    return client.post("/api/auth/register",
                       json={"email": EMAIL, "password": PASSWORD,
                             "invite_code": "invite-1", "agree_terms": True})


def _begin_registration(client: TestClient):
    return client.post("/api/auth/webauthn/register/begin", headers=_csrf(client))


def _fake_registration(*_args, **_kwargs):
    return SimpleNamespace(
        credential_id=CREDENTIAL_ID,
        credential_public_key=b"public-key",
        sign_count=0,
        aaguid="00000000-0000-0000-0000-000000000000",
        credential_backed_up=True,
        credential_device_type="multi_device",
    )


def _register_passkey(monkeypatch, client: TestClient) -> None:
    assert _register(client).status_code == 200
    begun = _begin_registration(client)
    assert begun.status_code == 200, begun.text
    body = begun.json()
    assert body["options"]["rp"]["id"] == passkeys.rp_id()
    assert body["options"]["challenge"]
    monkeypatch.setattr(passkeys, "verify_registration", _fake_registration)
    completed = client.post("/api/auth/webauthn/register/complete",
                            json={"challenge_id": body["challenge_id"],
                                  "credential": {"id": CREDENTIAL_ID_B64,
                                                 "rawId": CREDENTIAL_ID_B64,
                                                 "response": {"transports": ["internal"]}},
                                  "name": "工作笔记本"},
                            headers=_csrf(client))
    assert completed.status_code == 200, completed.text
    assert completed.json()["credential"]["credential_id"] == CREDENTIAL_ID_B64


def _actions(store: FakeStore) -> list[str]:
    return [row["action"] for row in store.audits]


def test_register_begin_and_complete_persists_credential(monkeypatch, client: TestClient,
                                                         store: FakeStore):
    _register_passkey(monkeypatch, client)
    row = store.get_webauthn_credential(CREDENTIAL_ID)
    assert row is not None
    assert row["user_id"] == store.get_user_by_email(EMAIL)["user_id"]
    assert row["sign_count"] == 0
    assert row["name"] == "工作笔记本"
    assert row["transports"] == ["internal"]
    assert row["backup_state"] is True
    status = client.get("/api/auth/webauthn")
    assert status.status_code == 200
    assert [item["credential_id"] for item in status.json()["credentials"]] == [CREDENTIAL_ID_B64]
    # 再次 begin 时应把已绑定凭据放入 excludeCredentials
    again = _begin_registration(client)
    assert again.status_code == 200
    assert again.json()["options"]["excludeCredentials"][0]["id"] == CREDENTIAL_ID_B64
    assert "passkey_registered" in _actions(store)


def test_registration_challenge_is_single_use(monkeypatch, client: TestClient):
    assert _register(client).status_code == 200
    begun = _begin_registration(client).json()
    monkeypatch.setattr(passkeys, "verify_registration", _fake_registration)
    payload = {"challenge_id": begun["challenge_id"],
               "credential": {"id": CREDENTIAL_ID_B64, "rawId": CREDENTIAL_ID_B64}}
    first = client.post("/api/auth/webauthn/register/complete", json=payload,
                        headers=_csrf(client))
    assert first.status_code == 200
    replay = client.post("/api/auth/webauthn/register/complete", json=payload,
                         headers=_csrf(client))
    assert replay.status_code == 400
    assert replay.json()["detail"]["code"] == "webauthn_challenge_invalid"


def test_passkey_login_creates_session_and_updates_sign_count(monkeypatch, client: TestClient,
                                                              store: FakeStore):
    _register_passkey(monkeypatch, client)
    assert client.post("/api/auth/logout").status_code == 200
    begun = client.post("/api/auth/webauthn/authenticate/begin", json={"email": EMAIL})
    assert begun.status_code == 200
    assert begun.json()["options"]["allowCredentials"][0]["id"] == CREDENTIAL_ID_B64
    monkeypatch.setattr(passkeys, "verify_authentication",
                        lambda *_a, **_k: SimpleNamespace(credential_id=CREDENTIAL_ID,
                                                          new_sign_count=1,
                                                          credential_backed_up=True))
    completed = client.post("/api/auth/webauthn/authenticate/complete",
                            json={"challenge_id": begun.json()["challenge_id"],
                                  "credential": {"id": CREDENTIAL_ID_B64,
                                                 "rawId": CREDENTIAL_ID_B64}})
    assert completed.status_code == 200, completed.text
    assert completed.json()["user"]["email"] == EMAIL
    assert client.get("/api/auth/session").status_code == 200
    assert store.get_webauthn_credential(CREDENTIAL_ID)["sign_count"] == 1
    assert "passkey_login_success" in _actions(store)


def test_passkey_login_rejects_non_increasing_sign_count(monkeypatch, client: TestClient,
                                                         store: FakeStore):
    _register_passkey(monkeypatch, client)
    store.bump_webauthn_sign_count(CREDENTIAL_ID, 0, 5)
    client.post("/api/auth/logout")
    begun = client.post("/api/auth/webauthn/authenticate/begin", json={"email": EMAIL}).json()
    monkeypatch.setattr(passkeys, "verify_authentication",
                        lambda *_a, **_k: SimpleNamespace(credential_id=CREDENTIAL_ID,
                                                          new_sign_count=5,
                                                          credential_backed_up=True))
    completed = client.post("/api/auth/webauthn/authenticate/complete",
                            json={"challenge_id": begun["challenge_id"],
                                  "credential": {"id": CREDENTIAL_ID_B64,
                                                 "rawId": CREDENTIAL_ID_B64}})
    assert completed.status_code == 401
    assert "passkey_clone_suspected" in _actions(store)


def test_delete_requires_password_and_last_credential_uses_password_fallback(
        monkeypatch, client: TestClient, store: FakeStore):
    _register_passkey(monkeypatch, client)
    wrong = client.request("DELETE", f"/api/auth/webauthn/{CREDENTIAL_ID_B64}",
                           json={"password": "wrong-password"}, headers=_csrf(client))
    assert wrong.status_code == 401
    deleted = client.request("DELETE", f"/api/auth/webauthn/{CREDENTIAL_ID_B64}",
                             json={"password": PASSWORD}, headers=_csrf(client))
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["remaining"] == 0
    assert store.get_webauthn_credential(CREDENTIAL_ID) is None
    assert store.audits[-1]["action"] == "passkey_deleted"
    assert store.audits[-1]["detail"]["last_credential"] is True


def test_unconfigured_passkeys_fail_closed(monkeypatch, client: TestClient):
    monkeypatch.setattr(passkeys, "enabled", lambda: False)
    assert _register(client).status_code == 200
    begun = _begin_registration(client)
    assert begun.status_code == 503
    assert begun.json()["detail"]["code"] == "webauthn_unavailable"


def test_unknown_email_begin_does_not_leak_account(monkeypatch, client: TestClient):
    response = client.post("/api/auth/webauthn/authenticate/begin",
                           json={"email": "nobody@example.com"})
    assert response.status_code == 200
    assert response.json()["options"]["allowCredentials"] == []


class _DenyLimiter:
    def allow(self, _key: str) -> bool:
        return False


def test_passkey_manage_rate_limited(monkeypatch, client: TestClient, store: FakeStore):
    """P0-2：Passkey 注册/删除按用户限流（挑战刷取防护）。"""
    assert _register(client).status_code == 200
    monkeypatch.setattr(api, "PASSKEY_MANAGE_LIMITER", _DenyLimiter())
    response = _begin_registration(client)
    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limited"
    assert "passkey_manage_rate_limited" in _actions(store)
