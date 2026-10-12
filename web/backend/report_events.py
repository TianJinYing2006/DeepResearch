"""报告流式事件的缓冲与发帧（需求 35 R4 / TBD-6 拍板，2026-10-12）。

Writer 的增量经 ``research_engine.report_stream`` 的 sink 到达本层；本层负责：

- **持久化粒度**：每累计 ``flush_chars`` 字符合并为一条 ``REPORT_DELTA`` 事件
  （进 ``run_events`` / 内存帧，支持 ``Last-Event-ID`` 断线重连；收尾与 reset 前强制冲刷）；
- **协议顺序**：``on_reset`` 先冲刷未发增量、再发 ``REPORT_RESET``（客户端以最近一次
  reset 后的增量为准；半截失败 ⇒ reset 清预览，终局正文整段替换）；
- **offset 语义**：本轮（上次 reset 之后）累计字符数——重连回放可据此校验连续性。

发帧回调由调用方提供（Worker：``store.append_event(..., owner=...)``；Runner：``_emit``）；
发帧失败（如租约丢失）由回调侧自行收敛（本层不感知业务错误类型）。
"""
from __future__ import annotations

import threading

from .agui import REPORT_DELTA, REPORT_RESET


class ReportEventBuffer:
    """把流式增量合并为 ``REPORT_DELTA`` / ``REPORT_RESET`` 事件。"""

    def __init__(self, emit, *, flush_chars: int = 400):
        """``emit(event_type, payload)``：把一条事件交给 Worker/Runner 的发帧通道。"""
        self._emit = emit
        self._flush_chars = max(1, int(flush_chars))
        self._lock = threading.Lock()
        self._parts: list[str] = []
        self._chars = 0
        self._offset = 0
        self._flushes = 0

    # ---- sink 回调 --------------------------------------------------------

    def on_delta(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            self._parts.append(text)
            self._chars += len(text)
            if self._chars >= self._flush_chars:
                self._flush_locked()

    def on_reset(self) -> None:
        with self._lock:
            self._flush_locked()          # 先冲刷未发增量（回放顺序：增量 → reset）
            self._offset = 0              # 本轮重新计数
            self._emit(REPORT_RESET, {})

    # ---- 收尾 -------------------------------------------------------------

    def flush(self) -> None:
        """冲刷未发增量（终局前调用：保证 RUN_FINISHED 之前所有正文都已入事件流）。"""
        with self._lock:
            self._flush_locked()

    # ---- 内部 -------------------------------------------------------------

    def _flush_locked(self) -> None:
        if not self._parts:
            return
        text = "".join(self._parts)
        self._parts = []
        self._chars = 0
        self._offset += len(text)
        self._flushes += 1
        self._emit(REPORT_DELTA, {"text": text, "offset": self._offset})
