# -*- coding: utf-8 -*-
"""全局安全响应头（P0-3）单测。"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from web.backend.security_headers import SecurityHeadersMiddleware, build_security_headers


def test_local_env_has_csp_but_no_hsts() -> None:
    headers = build_security_headers("local")
    csp = headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    assert "upgrade-insecure-requests" not in csp
    assert headers["X-Content-Type-Options"] == "nosniff"
    assert headers["X-Frame-Options"] == "DENY"
    assert headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "camera=()" in headers["Permissions-Policy"]
    assert "Strict-Transport-Security" not in headers


@pytest.mark.parametrize("env", ["staging", "production"])
def test_secure_env_adds_hsts_and_upgrade(env: str) -> None:
    headers = build_security_headers(env)
    assert "upgrade-insecure-requests" in headers["Content-Security-Policy"]
    assert headers["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"


def test_connect_src_extra_is_appended() -> None:
    csp = build_security_headers("local", "https://errors.example.cn")["Content-Security-Policy"]
    assert "connect-src 'self' https://errors.example.cn" in csp
    # 未配置时只允许同源，不留空尾随
    assert "connect-src 'self';" in build_security_headers("local")["Content-Security-Policy"]


def test_middleware_adds_headers_and_preserves_existing() -> None:
    async def endpoint(request):  # noqa: ANN001
        return PlainTextResponse("ok", headers={"Referrer-Policy": "no-referrer"})

    app = Starlette(routes=[Route("/", endpoint)])
    app.add_middleware(
        SecurityHeadersMiddleware, env="production", connect_src_extra="https://errors.example.cn"
    )
    response = TestClient(app).get("/")

    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "https://errors.example.cn" in response.headers["content-security-policy"]
    assert response.headers["strict-transport-security"].startswith("max-age=31536000")
    # 应用已设置的 header 不被覆盖（分享下载依赖 no-referrer）
    assert response.headers["referrer-policy"] == "no-referrer"


def test_fastapi_app_has_security_headers() -> None:
    """接线测试：真实 app（local env）的响应必须带 CSP / nosniff，且不加 HSTS。"""
    from web.backend.main import app

    response = TestClient(app).get("/api/health/live")

    assert response.status_code == 200
    assert response.headers["x-content-type-options"] == "nosniff"
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert "Strict-Transport-Security" not in response.headers
