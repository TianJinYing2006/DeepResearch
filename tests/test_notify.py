"""P2-4 SSE LISTEN/NOTIFY 唤醒测试。

- 纯逻辑（默认运行，不连库）：dispatch 唤醒 / 超时 / close 幂等。
- 真实 PostgreSQL（`DR_TEST_DATABASE_URL`，CI `infra` job）：`append_event` 与
  `finalize_run` 提交后唤醒 LISTEN 等待者；失败的终局迁移（回滚）不发通知。
"""
from __future__ import annotations

import os
import threading
import time
import uuid

import pytest

from web.backend.notify import RunEventNotifier

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

FAKE_DSN = "postgresql://user:pass@127.0.0.1:1/none"


def _silent_notifier(monkeypatch, dsn: str = FAKE_DSN) -> RunEventNotifier:
    """不启动真实 LISTEN 线程的通知器（纯逻辑用例）。"""
    notifier = RunEventNotifier(dsn, poll_seconds=0.2)
    monkeypatch.setattr(notifier, "_ensure_started", lambda: None)
    return notifier


def test_dispatch_wakes_waiter(monkeypatch):
    notifier = _silent_notifier(monkeypatch)
    received: list[bool] = []
    thread = threading.Thread(
        target=lambda: received.append(notifier.wait_for("run123", timeout=5.0)),
        daemon=True,
    )
    thread.start()
    time.sleep(0.1)
    notifier._dispatch("run123")
    thread.join(5)
    assert received == [True]


def test_wait_for_times_out_without_notification(monkeypatch):
    notifier = _silent_notifier(monkeypatch)
    started = time.monotonic()
    assert notifier.wait_for("run123", timeout=0.2) is False
    assert time.monotonic() - started < 3


def test_close_is_idempotent_and_restartable(monkeypatch):
    notifier = _silent_notifier(monkeypatch)
    notifier.close()
    notifier.close()  # 幂等
    assert notifier._thread is None  # close 后可再次懒启动
    assert notifier.wait_for("run123", timeout=0.1) is False


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_append_event_and_finalize_wake_listener():
    import psycopg

    from web.backend.store import RunStore

    store = RunStore(DSN)
    notifier = RunEventNotifier(DSN, poll_seconds=1.0)
    run_id = uuid.uuid4().hex[:12]
    try:
        store.create_run(run_id, "notify-test", {})

        wake: dict = {}

        def waiter():
            started = time.monotonic()
            wake["ok"] = notifier.wait_for(run_id, timeout=10.0)
            wake["elapsed"] = time.monotonic() - started

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        assert notifier.ready.wait(5.0), "LISTEN 未能建立"

        # 失败的状态迁移（无事件写入、无提交）⇒ 不应唤醒
        assert store.update_status(run_id, "SUCCEEDED", allowed_from=("QUEUED",)) is False
        thread.join(0.5)
        assert thread.is_alive(), "回滚的事务不应发通知"

        # append_event 提交 ⇒ 唤醒
        store.append_event(run_id, "node_started", {})
        thread.join(10)
        assert wake.get("ok") is True
        assert wake.get("elapsed", 99) < 9.0

        # finalize_run 提交 ⇒ 再次唤醒（新等待者）
        wake.clear()
        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        time.sleep(0.3)
        assert store.finalize_run(
            run_id, event_type="RUN_FINISHED", payload={}, sequence=None,
            new_status="SUCCEEDED", allowed_from=("CREATED",),
        ) is True
        thread.join(10)
        assert wake.get("ok") is True
    finally:
        notifier.close()
        store.close()
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
