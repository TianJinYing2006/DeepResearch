"""需求 24：自助找回 API 测试（FakeStore + FakeMailer，零 PostgreSQL / 网络）。

覆盖：防枚举恒 200、全链路（申请 → 邮件捕获 → 重置 → 新密码登录）、冷却期、
邮件通道未配置 503（无存在性预言机）、投递失败审计、限流审计（仅 email_hash）。
"""
from __future__ import annotations

import json
import re

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import token_hash
from web.backend.runner import RunManager

PASSWORD = "password-123456"
NEW_PASSWORD = "new-password-654321"


class FakeMailer:
    def __init__(self):
        self.sent: list[dict] = []
        self.fail_with: Exception | None = None

    def send(self, to, subject, text, html=None):
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})


@pytest.fixture()
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    fake.create_invite(token_hash("invite-1"), created_by="test")
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore, mailer: FakeMailer) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=1),
                                   store=store, max_concurrent_runs=4))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", True)
    monkeypatch.setattr(api, "get_mailer", lambda: mailer)
    return TestClient(api.app)


def _register(client: TestClient, email: str):
    return client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "invite_code": "invite-1"})


def _extract_token(text: str) -> str:
    match = re.search(r"#reset=([A-Za-z0-9_\-]+)", text)
    assert match, f"邮件正文缺少重置链接: {text!r}"
    return match.group(1)


def test_forgot_unknown_email_is_neutral_200(client: TestClient, store: FakeStore,
                                             mailer: FakeMailer):
    response = client.post("/api/auth/forgot", json={"email": "nobody@example.com"})

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert mailer.sent == []
    assert store.list_audit(action="forgot_requested") == []


def test_forgot_full_flow_resets_password(client: TestClient, store: FakeStore,
                                          mailer: FakeMailer):
    _register(client, "alice@example.com")

    response = client.post("/api/auth/forgot", json={"email": "Alice@Example.com"})
    assert response.status_code == 200
    assert len(mailer.sent) == 1
    token = _extract_token(mailer.sent[0]["text"])
    assert "#reset=" in mailer.sent[0]["html"]
    assert store.list_audit(action="forgot_requested")

    reset = client.post("/api/auth/reset", json={"token": token, "new_password": NEW_PASSWORD})
    assert reset.status_code == 200 and reset.json() == {"ok": True}

    assert client.post("/api/auth/login",
                       json={"email": "alice@example.com", "password": NEW_PASSWORD}
                       ).status_code == 200
    assert client.post("/api/auth/login",
                       json={"email": "alice@example.com", "password": PASSWORD}
                       ).status_code == 401
    # 单次消费：同 token 再用 → 422
    assert client.post("/api/auth/reset",
                       json={"token": token, "new_password": NEW_PASSWORD}
                       ).status_code == 422


def test_forgot_cooldown_skips_resend(client: TestClient, mailer: FakeMailer):
    _register(client, "alice@example.com")

    assert client.post("/api/auth/forgot", json={"email": "alice@example.com"}).status_code == 200
    assert client.post("/api/auth/forgot", json={"email": "alice@example.com"}).status_code == 200

    assert len(mailer.sent) == 1  # 冷却期内不重复发送


def test_forgot_mail_unavailable_503_for_everyone(client: TestClient, monkeypatch):
    monkeypatch.setattr(api, "get_mailer", lambda: None)

    for email in ("alice@example.com", "nobody@example.com"):
        response = client.post("/api/auth/forgot", json={"email": email})
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "mail_unavailable"


def test_forgot_mail_send_failure_audited_but_neutral(client: TestClient, store: FakeStore,
                                                      mailer: FakeMailer):
    _register(client, "alice@example.com")
    mailer.fail_with = RuntimeError("relay down")

    response = client.post("/api/auth/forgot", json={"email": "alice@example.com"})

    assert response.status_code == 200 and response.json() == {"ok": True}
    assert store.list_audit(action="mail_send_failed")
    assert store.list_audit(action="forgot_requested") == []


def test_forgot_rate_limited_audits_email_hash_only(client: TestClient, store: FakeStore,
                                                    monkeypatch):
    class _DenyLimiter:
        def allow(self, key):
            return False

    monkeypatch.setattr(api, "LOGIN_LIMITER", _DenyLimiter())
    response = client.post("/api/auth/forgot", json={"email": "alice@example.com"})

    assert response.status_code == 429
    rows = store.list_audit(action="forgot_rate_limited")
    assert rows
    detail = json.dumps(rows[0]["detail"])
    assert "alice@example.com" not in detail and "email_hash" in detail


def test_forgot_invalid_email_rejected(client: TestClient):
    assert client.post("/api/auth/forgot", json={"email": "not-an-email"}).status_code == 422
