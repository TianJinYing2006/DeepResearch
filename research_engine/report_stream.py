"""报告流式增量的事件旁路（需求 35 R4 / TBD-6 拍板，2026-10-12）。

与 :mod:`research_engine.usage` 同构：**核心链路只 emit，web 层注入 sink**（contextvar），
保持 ``research_engine`` 不依赖任务库 / SSE：

- 无 sink（CLI / eval）⇒ ``emit_*`` 为 no-op，Writer 走原非流式路径（行为与历史一致）；
- 有 sink（Web Runner / Worker）⇒ Writer 走 ``chat_stream`` 并把增量经 sink 上报，
  sink 负责缓冲、落 ``run_events``（``REPORT_DELTA`` / ``REPORT_RESET``）。

协议不变式（与前端契约一致）：

1. **每次流式开始先 reset**（清空客户端预览缓冲）——重试 / 重跑 / 接管后重放安全；
2. 增量文本按到达顺序拼接，``offset`` = 本次流（上次 reset 之后）累计字符数；
3. **半截失败**：已发出增量后失败 ⇒ 再发一次 reset（清预览），终局报告整段替换
   （``RUN_FINISHED.result.report``），客户端**绝不把终局正文追加到半截文本后**；
4. sink 异常 fail-silent（流式是体感旁路，不得打断主链路），累计失败计数供监控。
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

_log = logging.getLogger("deepresearch.report_stream")

_sink_failures = 0


def sink_failure_count() -> int:
    """进程内 sink 失败累计（监控 / 测试用）。"""
    return _sink_failures


@dataclass(frozen=True)
class ReportSink:
    """流式旁路的两条通路：增量文本 / 重置信号（均为同步回调）。"""

    on_delta: Callable[[str], None]
    on_reset: Callable[[], None]


_sink: ContextVar[Optional[ReportSink]] = ContextVar("dr_report_sink", default=None)


def report_stream_active() -> bool:
    """当前上下文是否有流式 sink（Writer 据此决定是否走 chat_stream）。"""
    return _sink.get() is not None


def emit_report_delta(text: str) -> None:
    """向当前 sink 发送一段增量文本；无 sink ⇒ no-op；sink 失败 fail-silent。"""
    global _sink_failures
    sink = _sink.get()
    if sink is None or not text:
        return
    try:
        sink.on_delta(text)
    except Exception as exc:  # noqa: BLE001 —— 体感旁路：不打断写作主链路
        _sink_failures += 1
        _log.error("report delta sink failed: %s: %s", type(exc).__name__, exc)


def emit_report_reset() -> None:
    """向当前 sink 发送「清空预览」信号；无 sink ⇒ no-op；sink 失败 fail-silent。"""
    global _sink_failures
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink.on_reset()
    except Exception as exc:  # noqa: BLE001 —— 同上
        _sink_failures += 1
        _log.error("report reset sink failed: %s: %s", type(exc).__name__, exc)


def push_report_sink(on_delta: Callable[[str], None],
                     on_reset: Callable[[], None]) -> object:
    """设置当前 sink，返回 token（配合 :func:`pop_report_sink` 复位）。"""
    return _sink.set(ReportSink(on_delta=on_delta, on_reset=on_reset))


def pop_report_sink(token: object) -> None:
    _sink.reset(token)  # type: ignore[arg-type]


@contextmanager
def use_report_sink(on_delta: Callable[[str], None],
                    on_reset: Callable[[], None]) -> Iterator[None]:
    token = _sink.set(ReportSink(on_delta=on_delta, on_reset=on_reset))
    try:
        yield
    finally:
        _sink.reset(token)
