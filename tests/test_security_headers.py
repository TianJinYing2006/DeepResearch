# -*- coding: utf-8 -*-
"""全局安全响应头（P0-3）单测。"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from web.backend.security_headers import (
    SecurityHeadersMiddleware,
    StaticCacheMiddleware,
    build_cache_control,
    build_security_headers,
    dsn_origin,
    merge_connect_src,
)


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


# ---------------------------------------------------------------------------
# 审计 #334：CSP ↔ 前端 Sentry 自动推导 + 静态资源缓存策略
# ---------------------------------------------------------------------------


def test_dsn_origin_extracts_ingest_origin() -> None:
    assert dsn_origin("https://abc123@errors.example.cn/2") == "https://errors.example.cn"
    assert dsn_origin("http://key@127.0.0.1:8080/1") == "http://127.0.0.1:8080"
    assert dsn_origin("") == ""
    assert dsn_origin("not-a-dsn") == ""
    assert dsn_origin("ftp://example.com/1") == ""


def test_merge_connect_src_dedupes_and_keeps_order() -> None:
    merged = merge_connect_src(
        "", "https://errors.example.cn https://cdn.example.cn", "https://errors.example.cn"
    )
    assert merged == "https://errors.example.cn https://cdn.example.cn"
    assert merge_connect_src("", "") == ""


def test_frontend_sentry_dsn_auto_extends_csp_connect_src(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """配置 DR_SENTRY_DSN_FRONTEND 而未配 DR_CSP_CONNECT_SRC 时，CSP 也必须放行上报域。"""
    import importlib

    retried = False
    while True:
        monkeypatch.setenv("DR_SENTRY_DSN_FRONTEND", "https://key@errors.example.cn/2")
        monkeypatch.delenv("DR_CSP_CONNECT_SRC", raising=False)
        monkeypatch.delenv("DR_ENV", raising=False)
        try:
            module = importlib.import_module("web.backend.main")
            module = importlib.reload(module)
            break
        except Exception:  # noqa: BLE001 - 仅用于在异常状态下重试一次导入
            if retried:
                raise
            retried = True
    csp = module.app.user_middleware
    # 从中间件构造参数里取 env/connect_src_extra（Starlette 以 Middleware(**kwargs) 存储）
    kwargs = next(m.kwargs for m in csp if m.cls is SecurityHeadersMiddleware)
    assert "https://errors.example.cn" in kwargs["connect_src_extra"]


def test_build_cache_control_policy() -> None:
    assert build_cache_control("/assets/index-abc123.js") == "public, max-age=31536000, immutable"
    assert build_cache_control("/") == "no-cache"
    assert build_cache_control("/index.html") == "no-cache"
    assert build_cache_control("/s/share-token") == "no-cache"
    assert build_cache_control("/api/health/live") is None
    assert build_cache_control("/api/research/abc/stream") is None


def test_static_cache_middleware_sets_headers_and_preserves_existing() -> None:
    async def asset(request):  # noqa: ANN001
        return PlainTextResponse("js")

    async def shell(request):  # noqa: ANN001
        return PlainTextResponse("<html/>")

    async def api(request):  # noqa: ANN001
        return PlainTextResponse("ok", headers={"Cache-Control": "no-store"})

    app = Starlette(
        routes=[
            Route("/assets/index-abc.js", asset),
            Route("/", shell),
            Route("/api/thing", api),
        ]
    )
    app.add_middleware(StaticCacheMiddleware)
    client = TestClient(app)

    assert client.get("/assets/index-abc.js").headers["cache-control"] == (
        "public, max-age=31536000, immutable"
    )
    assert client.get("/").headers["cache-control"] == "no-cache"
    # 应用自己设置的 Cache-Control 不被覆盖（API 仍是 no-store）
    assert client.get("/api/thing").headers["cache-control"] == "no-store"
