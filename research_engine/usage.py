"""逐调用 usage 记账的运行时 sink（P1-4）。

核心链路（LLMClient / embedding / 搜索）只负责 `emit_usage`；**落库由 web 层注入**
（contextvar sink），保持 `research_engine` 不依赖任务库：

- CLI / eval 不设置 sink ⇒ emit 为 no-op，行为与历史一致；
- RunManager / Worker / 摄取执行器在每次 run（或每个文档）前 `push_usage_sink(...)`，
  结束 `pop_usage_sink(token)`；线程/协程隔离由 contextvars 保证；
- sink 异常绝不打断主链路（best-effort）。
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Callable, Iterator, Optional


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
    """向当前 sink 发送一条用量；无 sink / sink 异常都静默（best-effort）。"""
    sink = _sink.get()
    if sink is None:
        return
    try:
        sink(record)
    except Exception:  # noqa: BLE001 —— 记账旁路，绝不打断主链路
        pass


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
