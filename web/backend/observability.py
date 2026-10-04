"""需求 25：错误追踪（Sentry 协议，DSN 空 = 关闭）。

- 只依赖 Sentry 协议：DSN 可指向自托管 GlitchTip（默认，数据不出境）/
  阿里云 ARMS RUM（DSN 指向 SLS）/ Sentry Cloud（需数据出境评审）；
- PII 最小化：``send_default_pii=False`` + :func:`scrub_event` 清洗
  （Cookie / Authorization / CSRF / query 中的 token/reset）；
- 默认不外发内容：错误事件只带技术上下文（request_id / run_id / release / environment）；
- 初始化失败只记日志、不阻断主链路（观测是旁路）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from config import config

_SENSITIVE_HEADERS = {"authorization", "cookie", "set-cookie", "x-csrf-token", "proxy-authorization"}
_TOKEN_QUERY_RE = re.compile(r"(token|reset)=[^&]*", re.IGNORECASE)

_enabled = False


def scrub_event(event: dict[str, Any], hint: Optional[dict[str, Any]] = None) -> dict[str, Any]:
    """``before_send`` 清洗：去凭据与 token，保留技术上下文（纯函数，可单测）。"""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            for key in list(headers):
                if key.lower() in _SENSITIVE_HEADERS:
                    headers.pop(key, None)
        if request.get("cookies"):
            request.pop("cookies", None)
        query = request.get("query_string")
        if isinstance(query, str):
            request["query_string"] = _TOKEN_QUERY_RE.sub(r"\1=***", query)
    return event


def init_error_tracking() -> bool:
    """初始化后端错误追踪；返回是否启用（DSN 空 / 依赖缺失 / 异常 → False）。"""
    global _enabled
    obs = config.observability
    if not obs.enabled:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.fastapi import FastApiIntegration
        from sentry_sdk.integrations.starlette import StarletteIntegration

        sentry_sdk.init(
            dsn=obs.sentry_dsn,
            environment=obs.sentry_environment or None,
            release=obs.sentry_release or None,
            traces_sample_rate=max(0.0, min(1.0, obs.sentry_traces_sample_rate)),
            send_default_pii=False,
            before_send=scrub_event,
            integrations=[StarletteIntegration(), FastApiIntegration()],
        )
        _enabled = True
        print(f"[observability] error tracking enabled (env={obs.sentry_environment})", flush=True)
    except Exception as exc:  # noqa: BLE001 —— 观测旁路，不阻断启动
        print(f"[observability] error tracking init failed: {type(exc).__name__}: {exc}", flush=True)
        _enabled = False
    return _enabled


def set_request_context(request_id: str) -> None:
    """把 request_id 挂到错误事件 tags（未启用时 no-op）。"""
    if not _enabled or not request_id:
        return
    try:
        import sentry_sdk

        sentry_sdk.set_tag("request_id", request_id)
    except Exception:  # noqa: BLE001
        pass


def error_tracking_enabled() -> bool:
    return _enabled
