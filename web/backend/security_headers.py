"""全局安全响应头（P0-3，2026-10-26）。

背景：评审发现全站没有统一安全响应头，仅分享下载单响应有 Referrer-Policy。
本模块在应用层统一注入 CSP / HSTS / nosniff / frame-ancestors / Referrer / Permissions，
不依赖 Caddy 热重载；使用纯 ASGI 中间件，不改动流式（SSE）语义，且**不覆盖**应用已设置的
header（例如分享页的 `Referrer-Policy: no-referrer`）。

CSP 口径（按实际前端资产）：
- 脚本/样式/字体均自托管；`script-src 'self'`（构建产物无 inline script）。
- React 组件存在 `style={...}` 内联样式 ⇒ `style-src` 必须含 `'unsafe-inline'`。
- API/SSE 同源 ⇒ `connect-src 'self'`；启用前端 Sentry 等外部上报时由
  `DR_CSP_CONNECT_SRC` 追加（空格分隔）。
- 报告中的外链锚点不受 CSP 限制；外部图片默认被阻断（隐私优先）。
- HSTS 与 `upgrade-insecure-requests` 只在 `DR_ENV=staging|production` 注入，
  避免本地 http 调试被浏览器升级。
"""

from __future__ import annotations

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURE_ENVS = {"staging", "production"}


def build_security_headers(env: str, connect_src_extra: str = "") -> dict[str, str]:
    """按运行环境返回安全响应头；纯函数，便于单测。"""
    extra = (connect_src_extra or "").strip()
    connect_src = "connect-src 'self'" + (f" {extra}" if extra else "")
    csp = "; ".join(
        [
            "default-src 'self'",
            "script-src 'self'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self'",
            connect_src,
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )
    headers = {
        "Content-Security-Policy": csp,
        "X-Content-Type-Options": "nosniff",
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
    }
    if env in SECURE_ENVS:
        headers["Content-Security-Policy"] = csp + "; upgrade-insecure-requests"
        headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return headers


class SecurityHeadersMiddleware:
    """纯 ASGI 中间件：在 `http.response.start` 追加缺失的安全 header。"""

    def __init__(self, app: ASGIApp, env: str = "local", connect_src_extra: str = "") -> None:
        self.app = app
        self._headers = [
            (key.lower().encode("latin-1"), value.encode("latin-1"))
            for key, value in build_security_headers(env, connect_src_extra).items()
        ]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                existing = {key.lower() for key, _ in headers}
                headers.extend((key, value) for key, value in self._headers if key not in existing)
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_headers)
