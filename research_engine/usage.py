"""逐调用 usage 记账的运行时 sink（P1-4 / P0-8）。

核心链路（LLMClient / embedding / 搜索）只负责 `emit_usage`；**落库由 web 层注入**
（contextvar sink），保持 `research_engine` 不依赖任务库：

- CLI / eval 不设置 sink ⇒ emit 为 no-op，行为与历史一致；
- RunManager / Worker / 摄取执行器在每次 run（或每个文档）前 `push_usage_sink(...)`，
  结束 `pop_usage_sink(token)`；线程/协程隔离由 contextvars 保证；
- **P0-8：sink 异常不再静默**——记 ERROR 日志并累加 `sink_failure_count()`；
  `DR_USAGE_STRICT=true` 时上抛 `UsageSinkError`（预算/计费场景可将任务标记为
  accounting_degraded 或中止）。web 层 sink 还会写审计与 Prometheus 计数（见
  `web/backend/usage.py`）。
"""
from __future__ import annotations

import logging
import os
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator, Optional

_log = logging.getLogger("deepresearch.usage")

#: 业务关键用量落账失败（`DR_USAGE_STRICT=true` 时由 emit_usage 上抛）
class UsageSinkError(RuntimeError):
    pass


_sink_failures = 0


def sink_failure_count() -> int:
    """进程内 sink 失败累计（监控/测试用）。"""
    return _sink_failures


def _strict() -> bool:
    return (os.getenv("DR_USAGE_STRICT") or "false").strip().lower() in {
        "1", "true", "yes", "on"}


@dataclass(frozen=True)
class UsageRecord:
    """一次外部调用的用量事实（不含 prompt / PII）。"""

    kind: str                          # llm | embedding | search
    provider: str
    model: str = ""
    role: str = ""                     # llm 职责桶（planner/critic/...）；search 工具名
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    request_id: Optional[str] = None   # provider 响应 id（对账 / 去重用）


_sink: ContextVar[Optional[Callable[[UsageRecord], None]]] = ContextVar(
    "dr_usage_sink", default=None)


def emit_usage(record: UsageRecord) -> None:
    """向当前 sink 发送一条用量；无 sink 时 no-op，sink 失败**必留痕**（P0-8）。"""
    global _sink_failures
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(record)
    except Exception as exc:  # noqa: BLE001 —— 记账旁路：默认不打断主链路，但不许静默
        _sink_failures += 1
        _log.error("usage sink failed (kind=%s provider=%s request_id=%s): %s: %s",
                   record.kind, record.provider, record.request_id,
                   type(exc).__name__, exc)
        if _strict():
            raise UsageSinkError(f"usage sink failed: {type(exc).__name__}: {exc}") from exc


def push_usage_sink(sink: Callable[[UsageRecord], None]) -> object:
    """设置当前 sink，返回 token（配合 :func:`pop_usage_sink` 复位）。"""
    return _sink.set(sink)


def pop_usage_sink(token: object) -> None:
    _sink.reset(token)  # type: ignore[arg-type]


@contextmanager
def use_usage_sink(sink: Callable[[UsageRecord], None]) -> Iterator[None]:
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)
