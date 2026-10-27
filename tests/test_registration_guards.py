# -*- coding: utf-8 -*-
"""开放注册防滥用第一层：总开关 / 全局每日上限 / 一次性邮箱域名（FakeStore）。"""

from __future__ import annotations

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api

PASSWORD = "password-123456"


@pytest.fixture()
def store() -> FakeStore:
    return FakeStore()


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    return TestClient(api.app)


def _register(client: TestClient, email: str, password: str = PASSWORD):
    return client.post("/api/auth/register",
                       json={"email": email, "password": password, "agree_terms": True})


def test_registration_can_be_disabled(monkeypatch, client: TestClient, store: FakeStore):
    monkeypatch.setattr(api, "REGISTRATION_ENABLED", False)
    resp = _register(client, "a@example.com")
    assert resp.status_code == 403
    assert resp.json()["detail"]["code"] == "registration_disabled"
    assert store.users == {}
    assert "register_blocked" in [row["action"] for row in store.audits]


def test_disposable_email_domain_is_rejected(client: TestClient, store: FakeStore):
    resp = _register(client, "spam@mailinator.com")
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "email_domain_blocked"
    assert store.users == {}

    # 子域名同样拦截
    resp = _register(client, "spam@sub.mailinator.com")
    assert resp.status_code == 422


def test_daily_registration_limit(monkeypatch, client: TestClient):
    monkeypatch.setattr(api, "REGISTRATION_DAILY_LIMIT", 1)
    assert _register(client, "first@example.com").status_code == 200
    second = _register(client, "second@example.com")
    assert second.status_code == 429
    assert second.json()["detail"]["code"] == "registration_limit_reached"


def test_normal_registration_still_open(client: TestClient, store: FakeStore):
    resp = _register(client, "normal@example.com")
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "normal@example.com"
    assert len(store.users) == 1
