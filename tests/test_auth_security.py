# -*- coding: utf-8 -*-
"""需求 20 P0：`__Host-` Cookie 前缀 + NIST 口令策略单测（FakeStore，零 PostgreSQL）。"""
from __future__ import annotations

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import (
    CSRF_COOKIE,
    MIN_PASSWORD_LENGTH,
    SESSION_COOKIE,
    check_password_strength,
    host_prefix_cookie_name,
    token_hash,
)

# ---------------------------------------------------------------- cookie 前缀


def test_host_prefix_only_applied_when_secure():
    assert host_prefix_cookie_name("dr_session", True) == "__Host-dr_session"
    assert host_prefix_cookie_name("dr_csrf", True) == "__Host-dr_csrf"
    # HTTP 本地调试（非 Secure）退回裸名：__Host- 要求 Secure，浏览器会拒收
    assert host_prefix_cookie_name("dr_session", False) == "dr_session"
    assert host_prefix_cookie_name("dr_csrf", False) == "dr_csrf"


def test_effective_cookie_names_follow_cookie_secure_flag():
    # 测试环境 DR_COOKIE_SECURE 未开启（DR_ENV=local）⇒ 保持裸名，行为与历史一致
    assert SESSION_COOKIE == "dr_session"
    assert CSRF_COOKIE == "dr_csrf"
    assert api.SESSION_COOKIE == SESSION_COOKIE
    assert api.CSRF_COOKIE == CSRF_COOKIE


# ---------------------------------------------------------------- 口令策略


@pytest.mark.parametrize("weak", ["password", "password123", "qwerty123", "123456789"])
def test_common_passwords_rejected(weak):
    assert check_password_strength(weak) is not None


def test_repetition_rejected():
    assert check_password_strength("aaaaaaaaaaaa") is not None
    assert check_password_strength("abababababab") is not None  # 去重后仅 2 个字符


def test_context_words_rejected():
    assert check_password_strength("deepresearch-2026") is not None
    assert check_password_strength("alice-strong-pass", email="alice@example.com") is not None
    # 邮箱本地部分过短（<4）不做上下文判定，避免误伤
    assert check_password_strength("a-strong-pass-2026", email="a@example.com") is None


def test_reasonable_passwords_accepted():
    assert check_password_strength("password-123456") is None  # 长口令不被精确黑名单误伤
    assert check_password_strength("Qing-Shan-2026!") is None
    assert MIN_PASSWORD_LENGTH == 12


# ---------------------------------------------------------------- 接口行为


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    fake = FakeStore()
    fake.create_invite(token_hash("invite-1"), created_by="test")
    monkeypatch.setattr(api, "store", fake)
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", True)
    return TestClient(api.app)


def _register(client: TestClient, email: str, password: str, code: str = "invite-1"):
    return client.post("/api/auth/register",
                       json={"email": email, "password": password, "invite_code": code,
                             "agree_terms": True})


def test_register_rejects_weak_password(client: TestClient):
    resp = _register(client, "weak@example.com", "password1234")  # 12 位且命中弱口令库
    assert resp.status_code == 422  # invalid_request 约定 422（errors.py ErrorSpec）
    assert "常见" in resp.json()["detail"]["message"]

    # 上下文词（邮箱本地部分 ≥4 字符）命中；失败发生在落库前，不消耗邀请码
    resp = _register(client, "context@example.com", "context-pass-2026")
    assert resp.status_code == 422


def test_register_rejects_too_short_password(client: TestClient):
    resp = _register(client, "short@example.com", "short-1234")  # 10 位
    assert resp.status_code == 422  # Pydantic min_length


def test_register_accepts_strong_password(client: TestClient):
    resp = _register(client, "zhenyu@example.com", "Strong-Pass-2026!")
    assert resp.status_code == 200
