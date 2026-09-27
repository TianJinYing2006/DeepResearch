"""P0-9 启动硬校验单测（零外部依赖）。

- `DR_ENV` 解析：默认 local、未知值拒绝；
- local 永不失败；staging 对不安全默认 fail fast；显式配置后通过；
- production 拒绝明文 HTTP（信任 X-Forwarded-Proto），HTTPS 放行。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from web.backend import main as api


def test_parse_env_defaults_to_local(monkeypatch):
    monkeypatch.delenv("DR_ENV", raising=False)
    assert api._parse_env() == "local"


def test_parse_env_rejects_unknown(monkeypatch):
    monkeypatch.setenv("DR_ENV", "prod")
    with pytest.raises(RuntimeError):
        api._parse_env()


def test_validate_runtime_config_local_never_fails(monkeypatch):
    monkeypatch.setattr(api, "ENV", "local")
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "COOKIE_SECURE", False)
    monkeypatch.setenv("DR_CORS_ORIGINS", "")
    api._validate_runtime_config()  # 不抛


def test_validate_runtime_config_staging_fails_on_unsafe_defaults(monkeypatch):
    monkeypatch.setattr(api, "ENV", "staging")
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "COOKIE_SECURE", False)
    monkeypatch.setenv("DR_CORS_ORIGINS", "")
    monkeypatch.setattr(api.config.llm, "api_key", "")

    with pytest.raises(RuntimeError) as exc:
        api._validate_runtime_config()
    message = str(exc.value)
    assert "DR_AUTH_REQUIRED" in message
    assert "DR_COOKIE_SECURE" in message
    assert "DR_CORS_ORIGINS" in message
    assert "DASHSCOPE_API_KEY" in message


def test_validate_runtime_config_staging_passes_with_explicit_config(monkeypatch):
    monkeypatch.setattr(api, "ENV", "staging")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "COOKIE_SECURE", True)
    monkeypatch.setenv("DR_CORS_ORIGINS", "https://dr.example.cn")
    monkeypatch.setattr(api.config.llm, "api_key", "fake-key")
    api._validate_runtime_config()  # 不抛


def test_production_rejects_plain_http(monkeypatch):
    monkeypatch.setattr(api, "ENV", "production")
    client = TestClient(api.app, base_url="http://testserver")

    response = client.get("/api/health/live")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "https_required"

    forwarded = client.get("/api/health/live", headers={"X-Forwarded-Proto": "https"})
    assert forwarded.status_code == 200
