# -*- coding: utf-8 -*-
"""图内异常归因（#308）：provider 429/5xx/网络类必须归为 provider_error。

背景（staging 实测 2026-10-31）：百炼首跳返回 HTTP 429 `limit_requests`，
`classify_exception` 只认递归/token，其余一律 `internal` ⇒ 前端显示
「internal / 未知错误」且不可重试。本文件锁定三类行为：

1. classify_exception 的映射（429 / 5xx / SDK 网络异常名 / 限流文案）；
2. describe_exception 不回显 provider 原始响应体（用户可见 message 可行动）；
3. error_payload 的规格（provider_error 可重试 + provider 归因；internal 不再落入 unknown）。
"""
from __future__ import annotations

from research_engine.failure_reasons import classify_exception, describe_exception
from web.backend.errors import error_payload

RAW_429 = (
    'Error code: 429 - {"error": {"message": "You have exceeded your current request '
    'limit. For details, see: https://help.aliyun.com/zh/model-studio/error-code'
    '#rate-limit", "type": "limit_requests", "param": None, "code": "limit_requests"}, '
    '"request_id": "f9c8f81d-fec1-99c2-beeb-e454d2866fa3"}'
)


class _ProviderError(Exception):
    """模拟 OpenAI SDK 的 APIStatusError（带 status_code）。"""

    def __init__(self, status_code: int, message: str) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(Exception):
    """类名即线索：OpenAI SDK 的 RateLimitError。"""


class APITimeoutError(Exception):
    pass


class GraphRecursionError(Exception):
    pass


def test_rate_limit_status_code_maps_to_provider_error() -> None:
    assert classify_exception(_ProviderError(429, RAW_429)) == "provider_error"


def test_rate_limit_without_status_maps_by_message_or_class_name() -> None:
    assert classify_exception(RateLimitError(RAW_429)) == "provider_error"
    assert classify_exception(Exception("429 too many requests")) == "provider_error"


def test_server_error_status_maps_to_provider_error() -> None:
    assert classify_exception(_ProviderError(503, "service unavailable")) == "provider_error"


def test_sdk_network_exception_names_map_to_provider_error() -> None:
    assert classify_exception(APITimeoutError("read timeout")) == "provider_error"


def test_describe_exception_hides_raw_provider_body() -> None:
    code, message = describe_exception(_ProviderError(429, RAW_429))
    assert code == "provider_error"
    assert "429" in message
    assert "{" not in message  # 不回显整段 provider JSON
    assert "limit_requests" not in message


def test_describe_exception_5xx_and_network_messages() -> None:
    code_5xx, message_5xx = describe_exception(_ProviderError(502, "bad gateway"))
    assert code_5xx == "provider_error"
    assert "502" in message_5xx
    code_net, message_net = describe_exception(APITimeoutError("timed out"))
    assert code_net == "provider_error"
    assert "网络" in message_net or "超时" in message_net


def test_recursion_and_token_limit_keep_existing_classification() -> None:
    assert classify_exception(GraphRecursionError("too many")) == "recursion_limit"
    assert (
        classify_exception(_ProviderError(400, "maximum context length is 128000 token"))
        == "token_limit"
    )


def test_unknown_exception_still_internal_with_raw_message() -> None:
    code, message = describe_exception(RuntimeError("boom"))
    assert code == "internal"
    assert "boom" in message


def test_error_payload_provider_spec_is_retryable_and_attributed_to_provider() -> None:
    payload = error_payload("provider_error", "模型服务请求频率超限")
    assert payload["retryable"] is True
    assert payload["component"] == "provider"
    assert "未知错误" not in payload["hint"]
    assert "重试" in payload["hint"]


def test_error_payload_internal_spec_no_longer_falls_back_to_unknown() -> None:
    payload = error_payload("internal", "internal detail")
    assert payload["retryable"] is True
    assert "未知错误" not in payload["hint"]


def test_error_payload_token_and_recursion_are_not_retryable() -> None:
    assert error_payload("token_limit", "x")["retryable"] is False
    assert error_payload("recursion_limit", "x")["retryable"] is False


def test_recover_from_exception_maps_provider_error_and_hides_raw_body() -> None:
    """图异常落点（#308）：429 走 provider_error + 可行动 message + degradation 留原文。"""
    from types import SimpleNamespace

    from research_engine.graph import DeepResearchGraph
    from research_engine.state import ResearchState

    graph = object.__new__(DeepResearchGraph)  # 跳过 __init__ 的图构建，只测异常落点
    graph.graph = SimpleNamespace(get_state=lambda _cfg: SimpleNamespace(values={}))
    initial = ResearchState(topic="t")

    out = graph._recover_from_exception(
        _ProviderError(429, RAW_429),
        {"configurable": {"thread_id": "t"}},
        initial,
    )

    assert out.error["code"] == "provider_error"
    assert "429" in out.error["message"]
    assert "{" not in out.error["message"]
    assert out.run_status == "failed"
    assert out.degradation_log[0].reason == "provider_error"
    assert "limit_requests" in out.degradation_log[0].detail  # 原始文本留给复核
