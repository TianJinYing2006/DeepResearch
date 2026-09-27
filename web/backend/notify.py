"""P2-4：PostgreSQL LISTEN/NOTIFY 事件唤醒（SSE 尾随的延迟优化）。

设计口径：

- **正确性不依赖本模块**：事件写入（`RunStore.append_event` / `finalize_run`）在
  同一事务内 `pg_notify(dr_run_events, run_id)`，提交后送达；API 侧 SSE 尾随
  循环用本模块等待"有新事件"信号，无论收到与否都最多轮询 `poll_seconds` 兜底
  （连接断开、LISTEN 丢失、通知竞态都不会造成丢事件）。
- **专连接**：LISTEN 必须使用会话级长连接，不能走连接池（池会 `DISCARD ALL`）；
  独立 daemon 线程 + 自动重连（1s→30s 退避），失败期间退化为纯轮询。
- 通知 payload 只放 `run_id`（12 字符），不携带正文（防大 payload / PII）。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Optional

import psycopg

from .store import EVENTS_CHANNEL

_log = logging.getLogger("deepresearch.notify")

#: 等待表最大条目与过期时间（防长时间运行的进程里字典无界增长）
_MAX_WAITERS = 512
_WAITER_TTL_SECONDS = 300.0


class RunEventNotifier:
    """单进程一个实例；`wait_for` 线程安全。"""

    def __init__(self, dsn: str, *, poll_seconds: float = 5.0):
        self._dsn = dsn
        self._poll_seconds = max(0.5, poll_seconds)
        self._lock = threading.Lock()
        self._waiters: dict[str, tuple[threading.Event, float]] = {}
        self._thread: Optional[threading.Thread] = None
        self._conn: Optional[psycopg.Connection] = None
        self._running = False
        #: 首次 LISTEN 就绪（测试用；重连期间保持置位）
        self.ready = threading.Event()

    @property
    def dsn(self) -> str:
        return self._dsn

    # ---- 对外接口 ----

    def wait_for(self, run_id: str, timeout: Optional[float] = None) -> bool:
        """等待该 run 的事件通知；收到 True，超时 False（超时后调用方轮询兜底）。"""
        self._ensure_started()
        limit = self._poll_seconds if timeout is None else max(0.1, timeout)
        with self._lock:
            now = time.monotonic()
            event = self._waiters.get(run_id)
            if event is None or event[0].is_set():
                event = (threading.Event(), now)
                self._waiters[run_id] = event
            self._prune_locked(now)
        return event[0].wait(limit)

    def close(self) -> None:
        """停止监听线程并关闭专连接（幂等；close 后可再次 `wait_for` 重新拉起）。"""
        self._running = False
        with self._lock:
            conn, self._conn = self._conn, None
        if conn is not None:
            try:
                conn.close()
            except Exception:  # noqa: BLE001 —— 关闭失败不影响进程退出
                pass
        thread = self._thread
        if thread is not None and thread.is_alive():
            thread.join(timeout=2.0)
        with self._lock:
            self._thread = None
            self.ready.clear()

    # ---- 内部 ----

    def _ensure_started(self) -> None:
        if self._thread is not None:
            return
        with self._lock:
            if self._thread is not None:
                return
            self._running = True
            thread = threading.Thread(target=self._run, name="pg-event-listener",
                                      daemon=True)
            self._thread = thread
        thread.start()

    def _run(self) -> None:
        backoff = 1.0
        while self._running:
            conn: Optional[psycopg.Connection] = None
            try:
                conn = psycopg.connect(self._dsn, autocommit=True, connect_timeout=3)
                with self._lock:
                    if not self._running:
                        break
                    self._conn = conn
                conn.execute(f"LISTEN {EVENTS_CHANNEL}")
                self.ready.set()
                _log.info("listening on %s", EVENTS_CHANNEL)
                backoff = 1.0
                while self._running:
                    got_any = False
                    for notify in conn.notifies(timeout=1.0, stop_after=None):
                        got_any = True
                        self._dispatch(notify.payload)
                    if not got_any:
                        continue
            except Exception as exc:  # noqa: BLE001 —— 断线重连，不拖垮 API 进程
                if self._running:
                    _log.warning("listener disconnected (%s: %s), retry in %.0fs",
                                 type(exc).__name__, exc, backoff)
            finally:
                with self._lock:
                    if self._conn is conn:
                        self._conn = None
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:  # noqa: BLE001
                        pass
            if self._running:
                time.sleep(backoff)
                backoff = min(backoff * 2, 30.0)

    def _dispatch(self, run_id: str) -> None:
        with self._lock:
            event = self._waiters.get(run_id)
        if event is not None:
            event[0].set()

    def _prune_locked(self, now: float) -> None:
        if len(self._waiters) <= _MAX_WAITERS:
            return
        stale = [key for key, (_, used) in self._waiters.items()
                 if now - used > _WAITER_TTL_SECONDS]
        for key in stale[:_MAX_WAITERS]:
            self._waiters.pop(key, None)
