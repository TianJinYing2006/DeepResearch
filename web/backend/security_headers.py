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

from urllib.parse import urlsplit

from starlette.types import ASGIApp, Message, Receive, Scope, Send

SECURE_ENVS = {"staging", "production"}


def dsn_origin(dsn: str) -> str:
    """从 Sentry DSN 提取上报 origin（``scheme://host[:port]``）；空/非法返回空串。

    DSN 形如 ``https://<key>@errors.example.cn/2`` ⇒ ``https://errors.example.cn``。
    用途：自动并入 CSP ``connect-src``，避免「前端开了 Sentry 却忘配 DR_CSP_CONNECT_SRC
    被 CSP 静默拦截」的配置耦合（审计 M-18）。
    """
    raw = (dsn or "").strip()
    if not raw:
        return ""
    try:
        parts = urlsplit(raw)
        port = parts.port  # 非法端口会抛 ValueError，一并视为非法 DSN
    except ValueError:
        return ""
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return ""
    host = parts.hostname
    if ":" in host and not host.startswith("["):  # IPv6 字面量需加回方括号
        host = f"[{host}]"
    netloc = host if port is None else f"{host}:{port}"
    return f"{parts.scheme}://{netloc}"


def merge_connect_src(*candidates: str) -> str:
    """空格分隔去重合并多个 connect-src 扩展串（顺序稳定，便于测试）。"""
    seen: list[str] = []
    for candidate in candidates:
        for token in (candidate or "").split():
            if token and token not in seen:
                seen.append(token)
    return " ".join(seen)


def build_cache_control(path: str) -> str | None:
    """静态资源缓存策略（返回 None = 不干预）。

    - 带内容哈希的 ``/assets/*``：一年 immutable（发版换哈希，天然失效）；
    - ``index.html`` / ``/`` 与分享壳 ``/s/<token>``：``no-cache``（必须每次回源，
      否则发版后旧 index 引用已删除的旧哈希资源 ⇒ 白屏）；
    - 其它路径不干预（API/SSE/下载各自已有头）。
    """
    path = path or ""
    if path.startswith("/assets/"):
        return "public, max-age=31536000, immutable"
    if path in ("/", "/index.html") or path.startswith("/s/"):
        return "no-cache"
    return None


class StaticCacheMiddleware:
    """纯 ASGI：给静态资源与 SPA 壳补缓存头；应用已设 Cache-Control 时不覆盖。"""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        cache = build_cache_control(scope.get("path", ""))
        if cache is None:
            await self.app(scope, receive, send)
            return
        value = cache.encode("latin-1")

        async def send_with_cache(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers") or [])
                if not any(key.lower() == b"cache-control" for key, _ in headers):
                    headers.append((b"cache-control", value))
                message = {**message, "headers": headers}
            await send(message)

        await self.app(scope, receive, send_with_cache)


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
