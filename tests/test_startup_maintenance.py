"""API 启动维护的执行模式分流（P3-B 补丁，零 PostgreSQL）。

背景：`mark_stale_as_lost` 只对 `inprocess` 模式成立 —— 执行器是 API 进程内的线程，
进程重启后非终局任务确实无人接管。`queue` 模式下执行权在独立 Worker（租约 + 心跳 +
`sweep_stale_runs`），API 重启**不得**把 Worker 正在执行的 RUNNING 与仍在 Redis 里的
QUEUED 误判为 `LOST`。

被测对象：`web.backend.main._startup_store_maintenance`。
"""
from __future__ import annotations

from fakes import FakeStore

from web.backend import main as api


class _StartupSpy:
    """记录启动维护调用序列的最小假仓储（只实现被调用的两个方法）。"""

    def __init__(self) -> None:
        self.calls: list[str] = []

    def mark_stale_as_lost(self) -> None:
        self.calls.append("mark_stale_as_lost")

    def purge_expired_sessions(self) -> None:
        self.calls.append("purge_expired_sessions")


def test_startup_maintenance_queue_mode_keeps_active_runs():
    store = FakeStore()
    store.create_run("stale-run-1", "旧任务", status="RUNNING")
    store.create_run("queued-run-1", "排队任务", status="QUEUED")

    api._startup_store_maintenance(store, "queue")

    assert store.get_run("stale-run-1")["status"] == "RUNNING"
    assert store.get_run("queued-run-1")["status"] == "QUEUED"


def test_startup_maintenance_inprocess_marks_lost_and_always_purges():
    queue_spy = _StartupSpy()
    api._startup_store_maintenance(queue_spy, "queue")
    assert queue_spy.calls == ["purge_expired_sessions"]

    inprocess_spy = _StartupSpy()
    api._startup_store_maintenance(inprocess_spy, "inprocess")
    assert inprocess_spy.calls == ["mark_stale_as_lost", "purge_expired_sessions"]
