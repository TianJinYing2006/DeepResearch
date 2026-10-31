# -*- coding: utf-8 -*-
"""LLM 客户端有界重试（#310）：任务时限存在时补偿被关闭的 SDK 重试。

背景：`LLMClient.chat` 在任务 deadline 存在时 `max_retries=0`（保证不越过整场时限），
但 provider 的瞬时 429/5xx/网络错误会把整轮 run 直接打死（staging 2026-10-31 实测
qwen-turbo 429 `limit_requests`）。本文件锁定：

1. 429/5xx/网络类错误在 deadline 内会重试（默认 2 次）；
2. 非 provider 错误（400 等）不重试；
3. 剩余时限不足时不再重试（不得把调用推到 deadline 之后）；
4. 无 deadline（CLI/eval）时保持「SDK 自带重试」口径，不叠加本层重试；
5. 重试次数可用 `DR_LLM_RETRY_MAX` 覆盖。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from research_engine.llm import client as client_module
from research_engine.llm.client import LLMClient
from research_engine.runtime_profile import reset_task_deadline, set_task_deadline


class _RetryableError(Exception):
    """模拟 OpenAI SDK 的 429（带 status_code，classify 会识别为 provider 异常）。"""

    status_code = 429


class _BadRequest(Exception):
    status_code = 400


class _FakeClient:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0
        self.max_retries_options = []
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def with_options(self, **kwargs):
        self.max_retries_options.append(kwargs)
        return self

    def _create(self, **_kwargs):
        self.calls += 1
        item = self.outcomes.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


def _ok(text: str = "ok"):
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=text))],
        usage=None,
        id="chatcmpl-test",
        model="qwen-flash",
    )


@pytest.fixture()
def deadline_60():
    token = set_task_deadline(60)
    try:
        yield
    finally:
        reset_task_deadline(token)


@pytest.fixture()
def no_sleep(monkeypatch):
    """重试退避不应让单测真的等待。"""
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)


def _client_with(monkeypatch, outcomes) -> tuple[LLMClient, _FakeClient]:
    fake = _FakeClient(outcomes)
    monkeypatch.setattr(LLMClient, "_get_client", lambda self: fake)
    return LLMClient(model="qwen-flash", role="test"), fake


def test_retries_429_and_then_succeeds(deadline_60, no_sleep, monkeypatch):
    client, fake = _client_with(monkeypatch, [_RetryableError("limit_requests"), _ok("hello")])
    assert client.chat([{"role": "user", "content": "x"}]) == "hello"
    assert fake.calls == 2
    assert fake.max_retries_options[-1] == {"max_retries": 0}  # SDK 重试仍关闭，由本层补偿


def test_non_provider_error_is_not_retried(deadline_60, no_sleep, monkeypatch):
    client, fake = _client_with(monkeypatch, [_BadRequest("invalid request")])
    with pytest.raises(_BadRequest):
        client.chat([{"role": "user", "content": "x"}])
    assert fake.calls == 1


def test_retry_exhausted_raises_last_error(deadline_60, no_sleep, monkeypatch):
    client, fake = _client_with(
        monkeypatch,
        [_RetryableError("a"), _RetryableError("b"), _RetryableError("c")],
    )
    with pytest.raises(_RetryableError):
        client.chat([{"role": "user", "content": "x"}])
    assert fake.calls == 3  # 1 次 + 默认 2 次重试


def test_insufficient_deadline_does_not_retry(no_sleep, monkeypatch):
    token = set_task_deadline(3)  # 退避+最小余量 > 3s ⇒ 不重试
    try:
        client, fake = _client_with(monkeypatch, [_RetryableError("late")])
        with pytest.raises(_RetryableError):
            client.chat([{"role": "user", "content": "x"}])
        assert fake.calls == 1
    finally:
        reset_task_deadline(token)


def test_without_deadline_custom_retry_is_disabled(no_sleep, monkeypatch):
    """无 deadline（CLI/eval）：保留 SDK 自带重试，本层不额外重试（避免双重放大）。"""
    client, fake = _client_with(monkeypatch, [_RetryableError("x")])
    with pytest.raises(_RetryableError):
        client.chat([{"role": "user", "content": "x"}])
    assert fake.calls == 1
    assert fake.max_retries_options == []  # 未调用 with_options


def test_retry_count_env_override(deadline_60, no_sleep, monkeypatch):
    monkeypatch.setenv("DR_LLM_RETRY_MAX", "1")
    client, fake = _client_with(
        monkeypatch,
        [_RetryableError("a"), _RetryableError("b"), _RetryableError("c")],
    )
    with pytest.raises(_RetryableError):
        client.chat([{"role": "user", "content": "x"}])
    assert fake.calls == 2  # 1 次 + env 指定的 1 次
