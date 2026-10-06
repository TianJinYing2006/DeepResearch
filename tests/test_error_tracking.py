"""需求 25：错误追踪单测（无网络；sentry_sdk 以假模块注入）。"""
from __future__ import annotations

import sys
import types

from config import config
from web.backend import observability


def test_scrub_event_removes_credentials_keeps_context():
    event = {
        "request": {
            "headers": {"Authorization": "Bearer x", "Cookie": "a=b",
                        "X-Request-ID": "req-123", "User-Agent": "UA/1"},
            "cookies": {"dr_session": "x"},
            "query_string": "email=a&token=secret&reset=xyz",
        },
        "tags": {"request_id": "req-123"},
    }

    out = observability.scrub_event(event)

    headers = out["request"]["headers"]
    assert "Authorization" not in headers and "Cookie" not in headers
    assert headers["X-Request-ID"] == "req-123"
    assert "cookies" not in out["request"]
    query = out["request"]["query_string"]
    assert "email=a" in query and "secret" not in query and "xyz" not in query
    assert out["tags"]["request_id"] == "req-123"


def test_init_noop_without_dsn(monkeypatch):
    monkeypatch.setattr(config.observability, "sentry_dsn", "")
    monkeypatch.setattr(observability, "_enabled", False)

    assert observability.init_error_tracking() is False


def test_init_enables_with_dsn_and_pii_off(monkeypatch):
    monkeypatch.setattr(config.observability, "sentry_dsn", "http://key@errors.example/1")
    monkeypatch.setattr(config.observability, "sentry_environment", "staging")
    monkeypatch.setattr(config.observability, "sentry_release", "abc123")
    monkeypatch.setattr(observability, "_enabled", False)

    fake = types.ModuleType("sentry_sdk")
    calls: dict = {}
    fake.init = lambda **kwargs: calls.update(kwargs)
    fastapi_mod = types.ModuleType("sentry_sdk.integrations.fastapi")
    fastapi_mod.FastApiIntegration = lambda: "fastapi"
    starlette_mod = types.ModuleType("sentry_sdk.integrations.starlette")
    starlette_mod.StarletteIntegration = lambda: "starlette"
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", types.ModuleType("sentry_sdk.integrations"))
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", fastapi_mod)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.starlette", starlette_mod)

    assert observability.init_error_tracking() is True
    assert calls["dsn"].startswith("http://key@")
    assert calls["send_default_pii"] is False
    assert calls["before_send"] is observability.scrub_event
    assert calls["release"] == "abc123" and calls["environment"] == "staging"
    observability._enabled = False


def test_init_failure_does_not_raise(monkeypatch):
    monkeypatch.setattr(config.observability, "sentry_dsn", "http://key@errors.example/1")
    monkeypatch.setattr(observability, "_enabled", False)
    fake = types.ModuleType("sentry_sdk")

    def _boom(**kwargs):
        raise RuntimeError("sdk broken")

    fake.init = _boom
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", types.ModuleType("sentry_sdk.integrations"))
    fastapi_mod = types.ModuleType("sentry_sdk.integrations.fastapi")
    fastapi_mod.FastApiIntegration = lambda: "fastapi"
    starlette_mod = types.ModuleType("sentry_sdk.integrations.starlette")
    starlette_mod.StarletteIntegration = lambda: "starlette"
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", fastapi_mod)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.starlette", starlette_mod)

    assert observability.init_error_tracking() is False
