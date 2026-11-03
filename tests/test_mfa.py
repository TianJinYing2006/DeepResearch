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
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, SESSION_COOKIE
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


def _logout(client: TestClient):
    """需求 42：登出必须带 CSRF 双提交头。"""
    return client.post("/api/auth/logout", headers=_csrf(client))
def _begin_mfa(client: TestClient, password: str = PASSWORD):
    """开始绑定 TOTP：P0-2c 起必须携带当前密码。"""
    return client.post("/api/auth/mfa/totp/begin",
                       json={"current_password": password}, headers=_csrf(client))


def _enable_mfa(client: TestClient) -> tuple[list[str], bytes]:
    started = _begin_mfa(client)
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
    resp = _begin_mfa(client)
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
    assert _logout(client).json()["ok"] is True
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


def test_mfa_step_up_rotates_session_token(client: TestClient, store: FakeStore):
    """需求 42：第二步成功后轮换会话 token，旧半程 token 立即失效（防会话固定）。

    修复前 step-up 只在原 token 上置 `mfa_verified=true`，预置的半程会话可借受害者
    的验证码「转正」；现在与登录一致：吊销旧会话 + 重签 Cookie。
    """
    assert _register(client).status_code == 200
    _codes, secret = _enable_mfa(client)
    assert _logout(client).json()["ok"] is True
    assert _login(client).json() == {"mfa_required": True}

    old_token = client.cookies.get(SESSION_COOKIE)
    old_csrf = client.cookies.get(CSRF_COOKIE)
    assert old_token
    verified = client.post("/api/auth/mfa/verify",
                           json={"code": totp_at(secret, time.time() + 30)},
                           headers=_csrf(client))
    assert verified.status_code == 200, verified.text

    new_token = client.cookies.get(SESSION_COOKIE)
    assert new_token and new_token != old_token
    assert client.cookies.get(CSRF_COOKIE) != old_csrf   # CSRF 同步轮换
    assert client.get("/api/auth/session").status_code == 200

    # 旧半程 token 已被吊销：单独回放它访问受保护接口 → 401
    stale = TestClient(api.app)
    stale.cookies.set(SESSION_COOKIE, old_token)
    assert stale.get("/api/auth/session").status_code == 401


def test_recovery_code_login_is_single_use(client: TestClient, store: FakeStore):
    assert _register(client).status_code == 200
    codes, _secret = _enable_mfa(client)
    assert _logout(client).json()["ok"] is True

    assert _login(client).json() == {"mfa_required": True}
    ok = client.post("/api/auth/mfa/verify", json={"code": codes[0]}, headers=_csrf(client))
    assert ok.status_code == 200, ok.text
    assert client.get("/api/auth/mfa").json()["recovery_codes_remaining"] == 9

    # 同一个恢复码不能再用
    assert _logout(client).json()["ok"] is True
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
    assert _logout(client).json()["ok"] is True
    assert _login(client).status_code == 200
    assert client.get("/api/auth/session").status_code == 200


def test_key_lost_with_factor_fails_closed(monkeypatch, client: TestClient, store: FakeStore):
    """有因子但服务端密钥不可用 ⇒ 拒绝登录（防「删密钥即绕过第二步」）。"""
    assert _register(client).status_code == 200
    _enable_mfa(client)
    assert _logout(client).json()["ok"] is True

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
    assert _logout(client).json()["ok"] is True
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
    resp = _begin_mfa(client)
    assert resp.status_code == 429
    assert resp.json()["detail"]["code"] == "rate_limited"
    assert "mfa_enroll_rate_limited" in _actions(store)


def test_totp_begin_requires_current_password(client: TestClient, store: FakeStore) -> None:
    """P0-2c：绑定第二因子必须重新输入当前密码，且失败不得产生待确认因子。

    攻击前提：会话 Cookie 被窃（恶意软件 / 备份泄漏）。若 begin 只凭会话即可调用，
    攻击者可拿到新 secret → 绑定自己的 TOTP → 取走 10 枚恢复码；受害者仅凭密码
    既登不进去（半程会话被拒）也关不掉（需 code），账号自锁且攻击者获得持久因子。
    """
    assert _register(client).status_code == 200
    user_id = client.get("/api/auth/session").json()["user"]["user_id"]

    # 缺 current_password ⇒ 请求层 422（连业务逻辑都进不去）
    missing = client.post("/api/auth/mfa/totp/begin", headers=_csrf(client))
    assert missing.status_code == 422

    # 错误密码 ⇒ 401 invalid_credentials；**不产生 pending 因子**（拿不到 secret）
    wrong = _begin_mfa(client, "wrong-password-123456")
    assert wrong.status_code == 401
    assert wrong.json()["detail"]["code"] == "invalid_credentials"
    assert store.get_mfa_factor(user_id, confirmed=False) is None
    assert client.get("/api/auth/mfa").json()["enabled"] is False
    failed = [row for row in store.audits if row["action"] == "mfa_enroll_failed"]
    assert failed and failed[-1]["detail"] == {"reason": "bad_password"}
    assert failed[-1]["actor_user_id"] == user_id

    # 正确密码 ⇒ 200 且暂存待确认因子（既有绑定路径不回归，随后可 confirm）
    ok = _begin_mfa(client)
    assert ok.status_code == 200, ok.text
    assert ok.json()["secret"]
    pending = store.get_mfa_factor(user_id, confirmed=False)
    assert pending is not None
