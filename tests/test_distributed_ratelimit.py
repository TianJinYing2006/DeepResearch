"""P1-2 分布式限流单测（零 Redis：工厂回落 / 双维度 / 可信代理）。

真实 Redis 的滑动窗口原子性见 `test_worker_integration.py`（CI `infra` 执行）。
"""
from __future__ import annotations

from fakes import FakeStore
from fastapi.testclient import TestClient
from starlette.requests import Request

from web.backend import main as api
from web.backend.auth import hash_password
from web.backend.ratelimit import (
    FixedWindowLimiter,
    RedisSlidingWindowLimiter,
    make_limiter,
)

PASSWORD = "password-123456"


def _request_with(headers: dict | None = None, host: str = "127.0.0.1") -> Request:
    scope = {
        "type": "http", "method": "GET", "path": "/", "query_string": b"",
        "headers": [(key.lower().encode(), value.encode()) for key, value in (headers or {}).items()],
        "client": (host, 12345),
    }
    return Request(scope)


def test_make_limiter_falls_back_without_redis():
    assert isinstance(make_limiter("test", 5), FixedWindowLimiter)


def test_make_limiter_uses_redis_when_configured():
    limiter = make_limiter("test", 5, redis_url="redis://127.0.0.1:6379/0")
    assert isinstance(limiter, RedisSlidingWindowLimiter)


def test_client_key_trusts_forwarded_header_only_when_enabled(monkeypatch):
    request = _request_with({"X-Forwarded-For": "203.0.113.7, 10.0.0.1"})

    monkeypatch.setattr(api, "TRUSTED_PROXY_CIDRS", ())
    monkeypatch.setattr(api, "TRUST_PROXY", False)
    assert api._client_key(request) == "127.0.0.1"

    monkeypatch.setattr(api, "TRUST_PROXY", True)
    assert api._client_key(request) == "203.0.113.7"


def test_client_key_trusted_proxy_cidr_and_hops(monkeypatch):
    """P0-10：只有可信代理 CIDR 内的对端才采信 XFF；按 hops 取跳数。"""
    import ipaddress

    monkeypatch.setattr(api, "TRUSTED_PROXY_CIDRS",
                        (ipaddress.ip_network("10.0.0.0/8"),))
    monkeypatch.setattr(api, "TRUST_PROXY", False)
    # 客户端伪造首跳 + 代理追加真实客户端：取倒数第 1 跳
    request = _request_with({"X-Forwarded-For": "1.2.3.4, 203.0.113.7"},
                            host="10.0.0.5")
    monkeypatch.setattr(api, "PROXY_HOPS", 1)
    assert api._client_key(request) == "203.0.113.7"
    monkeypatch.setattr(api, "PROXY_HOPS", 2)
    assert api._client_key(request) == "1.2.3.4"

    # 非可信对端即使配置了 CIDR 也不采信 XFF
    untrusted = _request_with({"X-Forwarded-For": "203.0.113.7"}, host="127.0.0.1")
    assert api._client_key(untrusted) == "127.0.0.1"


def _client(monkeypatch, store: FakeStore, *, ip_limit: int, account_limit: int) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "LOGIN_LIMITER", FixedWindowLimiter(ip_limit))
    monkeypatch.setattr(api, "LOGIN_ACCOUNT_LIMITER", FixedWindowLimiter(account_limit))
    return TestClient(api.app)


def test_login_account_dimension_blocks_targeted_attack(monkeypatch):
    store = FakeStore()
    store.create_user("u1", "target@example.com", hash_password(PASSWORD))
    client = _client(monkeypatch, store, ip_limit=100, account_limit=2)

    for _ in range(2):
        response = client.post("/api/auth/login",
                               json={"email": "target@example.com", "password": "wrong-pass-xyz"})
        assert response.status_code == 401
    blocked = client.post("/api/auth/login",
                          json={"email": "target@example.com", "password": "wrong-pass-xyz"})
    assert blocked.status_code == 429
    assert blocked.json()["detail"]["code"] == "rate_limited"
    assert store.list_audit(action="login_rate_limited")

    # 其他账号不受影响（账号维度键 = 邮箱哈希）
    store.create_user("u2", "other@example.com", hash_password(PASSWORD))
    ok = client.post("/api/auth/login",
                     json={"email": "other@example.com", "password": PASSWORD})
    assert ok.status_code == 200


def test_login_ip_dimension_blocks_across_accounts(monkeypatch):
    store = FakeStore()
    store.create_user("u1", "a@example.com", hash_password(PASSWORD))
    store.create_user("u2", "b@example.com", hash_password(PASSWORD))
    client = _client(monkeypatch, store, ip_limit=1, account_limit=100)

    first = client.post("/api/auth/login", json={"email": "a@example.com", "password": PASSWORD})
    assert first.status_code == 200
    second = client.post("/api/auth/login", json={"email": "b@example.com", "password": PASSWORD})
    assert second.status_code == 429
