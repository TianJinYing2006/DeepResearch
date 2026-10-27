"""需求 20 P1-3：TOTP MFA + 恢复码 + 登录 step-up（FakeStore；零 PostgreSQL / 网络）。

覆盖：密钥未配置 ⇒ 显式 503；绑定（begin/confirm）与恢复码一次性；登录 step-up
（半程会话不得访问受保护接口）；恢复码登录与单次消费；关闭 MFA；**密钥丢失 fail-closed**；
TOTP / 恢复码纯函数（漂移窗口、格式、归一化）。
"""
from __future__ import annotations

import time

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend import secretbox
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER
from web.backend.runner import RunManager
from web.backend.totp import (
    decode_secret,
    hash_recovery_code,
    new_recovery_codes,
    new_totp_secret,
    normalize_recovery_code,
    totp_at,
    verify_totp,
)

PASSWORD = "password-123456"
EMAIL = "a@example.com"


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
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
    monkeypatch.setenv("DR_MFA_SECRET_KEY", secretbox.generate_key_b64())
    return TestClient(api.app)


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def _register(client: TestClient):
    return client.post("/api/auth/register",
                       json={"email": EMAIL, "password": PASSWORD,
                             "agree_terms": True})


def _login(client: TestClient):
    return client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})


def _enable_mfa(client: TestClient) -> tuple[list[str], bytes]:
    started = client.post("/api/auth/mfa/totp/begin", headers=_csrf(client))
    assert started.status_code == 200, started.text
    assert started.json()["otpauth_uri"].startswith("otpauth://totp/")
    secret = decode_secret(started.json()["secret"])
    confirmed = client.post("/api/auth/mfa/totp/confirm",
                            json={"code": totp_at(secret, time.time())},
                            headers=_csrf(client))
    assert confirmed.status_code == 200, confirmed.text
    return confirmed.json()["recovery_codes"], secret


def _actions(store: FakeStore) -> list[str]:
    return [row["action"] for row in store.audits]


# ----------------------------------------------------------------- 纯函数

def test_totp_verify_window_and_format():
    secret = new_totp_secret()
    now = time.time()
    code = totp_at(secret, now)
    assert len(code) == 6 and code.isdigit()
    assert verify_totp(secret, code, now)              # 当前步
    assert verify_totp(secret, code, now + 30)         # ±1 步（时钟漂移）
    assert not verify_totp(secret, code, now + 120)    # 超窗口
    assert not verify_totp(secret, "abcdef", now)      # 非数字
    assert not verify_totp(secret, "12345", now)       # 位数不符
    assert not verify_totp(new_totp_secret(), code, now)  # 别的 secret


def test_recovery_code_normalization():
    codes = new_recovery_codes(2)
    assert len(codes) == 2 and all("-" in code for code in codes)
    assert normalize_recovery_code(" ab-cd ") == "ABCD"
    # 大小写 / 连字符 / 空格归一后同摘要
    assert hash_recovery_code(codes[0].lower().replace("-", " ")) == hash_recovery_code(codes[0])


# ----------------------------------------------------------------- 接口

def test_unconfigured_key_is_explicit(monkeypatch, client: TestClient):
    monkeypatch.delenv("DR_MFA_SECRET_KEY", raising=False)
    assert _register(client).status_code == 200
    status = client.get("/api/auth/mfa").json()
    assert status["available"] is False and status["enabled"] is False
    resp = client.post("/api/auth/mfa/totp/begin", headers=_csrf(client))
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "mfa_unconfigured"


def test_enroll_then_step_up_login(client: TestClient, store: FakeStore):
    assert _register(client).status_code == 200
    codes, secret = _enable_mfa(client)
    assert len(codes) == 10
    status = client.get("/api/auth/mfa").json()
    assert status["enabled"] is True and status["recovery_codes_remaining"] == 10
    assert "mfa_enabled" in _actions(store)

    # 密码登录只拿到「半程会话」
    assert client.post("/api/auth/logout").json()["ok"] is True
    login = _login(client)
    assert login.status_code == 200 and login.json() == {"mfa_required": True}
    assert client.get("/api/auth/session").status_code == 401     # 半程不得访问

    # 第二步：TOTP
    verified = client.post("/api/auth/mfa/verify",
                           json={"code": totp_at(secret, time.time())},
                           headers=_csrf(client))
    assert verified.status_code == 200, verified.text
    assert verified.json()["user"]["email"] == EMAIL
    assert client.get("/api/auth/session").status_code == 200     # 完成第二步后可访问
    assert "mfa_verified" in _actions(store)

    # 错误的第二步验证码 ⇒ 401，且会话仍未完成
    fresh = TestClient(api.app)
    assert fresh.post("/api/auth/login",
                      json={"email": EMAIL, "password": PASSWORD}).json() == {"mfa_required": True}
    bad = fresh.post("/api/auth/mfa/verify", json={"code": "000000"},
                     headers={CSRF_HEADER: fresh.cookies.get(CSRF_COOKIE)})
    assert bad.status_code == 401
    assert bad.json()["detail"]["code"] == "mfa_code_invalid"
    assert fresh.get("/api/auth/session").status_code == 401


def test_recovery_code_login_is_single_use(client: TestClient, store: FakeStore):
    assert _register(client).status_code == 200
    codes, _secret = _enable_mfa(client)
    assert client.post("/api/auth/logout").json()["ok"] is True

    assert _login(client).json() == {"mfa_required": True}
    ok = client.post("/api/auth/mfa/verify", json={"code": codes[0]}, headers=_csrf(client))
    assert ok.status_code == 200, ok.text
    assert client.get("/api/auth/mfa").json()["recovery_codes_remaining"] == 9

    # 同一个恢复码不能再用
    assert client.post("/api/auth/logout").json()["ok"] is True
    assert _login(client).json() == {"mfa_required": True}
    dup = client.post("/api/auth/mfa/verify", json={"code": codes[0]}, headers=_csrf(client))
    assert dup.status_code == 401


def test_disable_requires_password_and_code(client: TestClient, store: FakeStore):
    assert _register(client).status_code == 200
    _codes, secret = _enable_mfa(client)

    bad = client.post("/api/auth/mfa/disable",
                      json={"password": "wrong-password", "code": "000000"},
                      headers=_csrf(client))
    assert bad.status_code == 401

    done = client.post("/api/auth/mfa/disable",
                       json={"password": PASSWORD,
                             "code": totp_at(secret, time.time())},
                       headers=_csrf(client))
    assert done.status_code == 200 and done.json()["ok"] is True
    status = client.get("/api/auth/mfa").json()
    assert status["enabled"] is False and status["recovery_codes_remaining"] == 0

    # 关闭后登录不再要求第二步
    assert client.post("/api/auth/logout").json()["ok"] is True
    assert _login(client).status_code == 200
    assert client.get("/api/auth/session").status_code == 200


def test_key_lost_with_factor_fails_closed(monkeypatch, client: TestClient, store: FakeStore):
    """有因子但服务端密钥不可用 ⇒ 拒绝登录（防「删密钥即绕过第二步」）。"""
    assert _register(client).status_code == 200
    _enable_mfa(client)
    assert client.post("/api/auth/logout").json()["ok"] is True

    monkeypatch.delenv("DR_MFA_SECRET_KEY", raising=False)
    resp = _login(client)
    assert resp.status_code == 503
    assert resp.json()["detail"]["code"] == "mfa_unavailable"
    assert "mfa_unavailable" in _actions(store)


def test_recovery_code_hash_is_stored_not_plaintext(client: TestClient, store: FakeStore):
    """持久化只存摘要（不落明文恢复码）。"""
    assert _register(client).status_code == 200
    codes, _secret = _enable_mfa(client)
    stored = {row["code_hash"] for row in store.recovery_codes}
    assert stored and all(code not in stored for code in codes)
    assert hash_recovery_code(codes[0]) in stored


class _DenyLimiter:
    def allow(self, _key: str) -> bool:
        return False


def test_mfa_verify_rate_limited(client: TestClient, monkeypatch, store: FakeStore) -> None:
    """P0-2：登录 step-up 的验证码尝试必须限流（含正确验证码也不放行）。"""
    assert _register(client).status_code == 200
    _codes, secret = _enable_mfa(client)
    assert client.post("/api/auth/logout").json()["ok"] is True
    assert _login(client).json() == {"mfa_required": True}

    monkeypatch.setattr(api, "MFA_VERIFY_LIMITER", _DenyLimiter())
    resp = client.post("/api/auth/mfa/verify",
                       json={"code": totp_at(secret, time.time())},
                       headers=_csrf(client))
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "rate_limited"
    assert client.get("/api/auth/session").status_code == 401
    assert "mfa_verify_rate_limited" in _actions(store)


def test_mfa_enroll_rate_limited(client: TestClient, monkeypatch, store: FakeStore) -> None:
    """P0-2：MFA 绑定/关闭接口同样限流。"""
    assert _register(client).status_code == 200
    monkeypatch.setattr(api, "MFA_ENROLL_LIMITER", _DenyLimiter())
    resp = client.post("/api/auth/mfa/totp/begin", headers=_csrf(client))
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "rate_limited"
    assert "mfa_enroll_rate_limited" in _actions(store)
