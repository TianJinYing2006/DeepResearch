"""需求 35 R4：报告流式协议单测（零网络）——chat_stream / sink / Writer 降级路径。"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from research_engine.llm.client import LLMClient
from research_engine.report_stream import (
    emit_report_delta,
    emit_report_reset,
    pop_report_sink,
    push_report_sink,
    report_stream_active,
    sink_failure_count,
)
from research_engine.state import ResearchState


def _chunk(content=None, usage=None):
    choices = []
    if content is not None:
        choices = [SimpleNamespace(delta=SimpleNamespace(content=content))]
    return SimpleNamespace(choices=choices, usage=usage, id="c1", model="fake")


class _FakeCompletions:
    def __init__(self, chunks, fail_at=None):
        self._chunks = chunks
        self._fail_at = fail_at
        self.kwargs = None

    def create(self, **kwargs):
        self.kwargs = kwargs
        assert kwargs.get("stream") is True  # chat_stream 必须走流式
        chunks, fail_at = self._chunks, self._fail_at

        def gen():
            for index, chunk in enumerate(chunks):
                if fail_at is not None and index == fail_at:
                    raise RuntimeError("stream broken")
                yield chunk

        return gen()


class _FakeClient:
    def __init__(self, chunks, fail_at=None):
        self.completions = _FakeCompletions(chunks, fail_at)
        self.chat = SimpleNamespace(completions=self.completions)

    def with_options(self, **kwargs):  # noqa: ARG002
        return self


def _client(chunks, fail_at=None) -> tuple[LLMClient, _FakeCompletions]:
    client = LLMClient(model="fake-model", role="test")
    fake = _FakeClient(chunks, fail_at)
    client._client = fake
    return client, fake.completions


# ---------------------------------------------------------------- chat_stream


def test_chat_stream_assembles_text_reports_deltas_and_accumulates_usage():
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    chunks = [_chunk("你"), _chunk("好"), _chunk(None, usage=usage)]  # 末帧仅带 usage
    seen: list[str] = []
    state = ResearchState(topic="t")
    client, _ = _client(chunks)
    text = client.chat_stream([{"role": "user", "content": "hi"}], state=state,
                              on_delta=seen.append)
    assert text == "你好"
    assert seen == ["你", "好"]
    assert state.token_used == 15


def test_chat_stream_midway_failure_raises_and_keeps_deltas_so_far():
    chunks = [_chunk("前"), _chunk("半")]
    seen: list[str] = []
    client, _ = _client(chunks, fail_at=1)
    with pytest.raises(RuntimeError):
        client.chat_stream([{"role": "user", "content": "hi"}], on_delta=seen.append)
    assert seen == ["前"]  # 半截文本不返回，但增量已如实上报（由 reset 协议收口）


# ------------------------------------------------------------------ sink 语义


def test_sink_no_sink_noop_and_push_pop_roundtrip():
    assert report_stream_active() is False
    emit_report_delta("x")   # 无 sink ⇒ no-op，不抛
    emit_report_reset()

    deltas: list[str] = []
    resets: list[bool] = []
    token = push_report_sink(deltas.append, lambda: resets.append(True))
    try:
        assert report_stream_active() is True
        emit_report_delta("a")
        emit_report_delta("")   # 空文本不上报
        emit_report_reset()
    finally:
        pop_report_sink(token)
    assert deltas == ["a"] and resets == [True]
    assert report_stream_active() is False


def test_sink_failure_is_fail_silent_and_counted():
    before = sink_failure_count()

    def boom_delta(_text):
        raise RuntimeError("sink down")

    def boom_reset():
        raise RuntimeError("reset down")

    token = push_report_sink(boom_delta, boom_reset)
    try:
        emit_report_delta("x")  # 不抛
        emit_report_reset()     # 不抛
    finally:
        pop_report_sink(token)
    assert sink_failure_count() == before + 2


# ------------------------------------------------------------------ Writer 路径


class _FakeRouter:
    def __init__(self, stream_text=None, stream_error=None, full="# 完整"):
        self.stream_text = stream_text
        self.stream_error = stream_error
        self.full = full
        self.stream_calls = 0
        self.chat_calls = 0

    def smart_chat_stream(self, system, user, state=None, on_delta=None):  # noqa: ARG002
        self.stream_calls += 1
        if self.stream_error is not None:
            if self.stream_text and on_delta is not None:
                on_delta(self.stream_text)  # 半截后失败
            raise self.stream_error
        for char in (self.stream_text or ""):
            if on_delta is not None:
                on_delta(char)
        return self.stream_text or ""

    def smart_chat(self, system, user, state=None):  # noqa: ARG002
        self.chat_calls += 1
        return self.full


def _writer_with(router):
    from research_engine.agents.writer import Writer

    writer = Writer()
    writer.router = router
    return writer


def test_writer_streams_when_sink_active():
    router = _FakeRouter(stream_text="# 报告")
    writer = _writer_with(router)
    deltas: list[str] = []
    resets: list[bool] = []
    token = push_report_sink(deltas.append, lambda: resets.append(True))
    try:
        report = writer.write("主题", [], [])
    finally:
        pop_report_sink(token)
    assert report == "# 报告"
    assert resets == [True]                 # 协议不变式 1：开始先 reset
    assert "".join(deltas) == "# 报告"
    assert router.chat_calls == 0           # 流式成功 ⇒ 不落非流式


def test_writer_midway_failure_resets_and_retries_non_stream():
    router = _FakeRouter(stream_text="半截", stream_error=RuntimeError("boom"),
                         full="# 重试完整")
    writer = _writer_with(router)
    deltas: list[str] = []
    resets: list[bool] = []
    token = push_report_sink(deltas.append, lambda: resets.append(True))
    try:
        report = writer.write("主题", [], [])
    finally:
        pop_report_sink(token)
    assert report == "# 重试完整"
    assert resets == [True, True]           # 开始 + 半截失败各一次（清预览）
    assert router.chat_calls == 1           # 降级非流式重试
    degradations = writer.drain_degradations()
    assert any(d.fallback_action == "non_stream_retry" for d in degradations)


def test_writer_without_sink_keeps_legacy_non_stream_path():
    router = _FakeRouter()
    writer = _writer_with(router)
    report = writer.write("主题", [], [])
    assert report == "# 完整"
    assert router.stream_calls == 0 and router.chat_calls == 1
