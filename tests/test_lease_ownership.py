"""审计 F04 回归：Worker 租约所有权隔离（FakeStore，零 PostgreSQL / Redis）。

旧执行者（attempt 失效后）的**一切**执行写入必须被拒绝：
- store 层：`append_event` / `update_usage` / `finalize_run` 携带 `RunOwnership`
  条件校验（`worker_id + attempt + 执行态`），旧写入抛 `LeaseLostError` / 返回 False；
- worker 层：续租被拒 ⇒ `lease_lost` 在节点边界停止，不再写终局（等新执行者收口）；
- persistence 层：旧 owner 的迟到终局整体回滚（不写事件 / 产物 / 状态）。

真实 PostgreSQL 的所有权 SQL 由 `test_worker_integration.py`（CI infra）覆盖。
"""
from __future__ import annotations

import threading
import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeQueue, FakeStore, TinyGraph, event_types

from web.backend.persistence import ACTIVE_STATUSES, persist_terminal
from web.backend.store import LeaseLostError, RunOwnership
from web.backend.worker import Worker


def _queued(store: FakeStore, *, lease_seconds: float = 3600) -> str:
    run_id = "run-" + uuid.uuid4().hex[:10]
    store.create_run(run_id, "所有权测试", {"instructions": ""}, status="QUEUED",
                     timeout_at=datetime.now(UTC) + timedelta(seconds=lease_seconds))
    return run_id


def _takeover(store: FakeStore, run_id: str) -> None:
    """模拟租约接管：租约置过期 → 清扫 requeue（attempt+1）→ worker-b 认领。"""
    store.runs[run_id]["lease_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert store.sweep_stale_runs() == [
        {"run_id": run_id, "action": "requeued", "attempt": 2}]
    claimed = store.claim_run(run_id, "worker-b", 60)
    assert claimed is not None and claimed["attempt"] == 2


# ---------------------------------------------------------------- store 层

def test_old_attempt_event_append_rejected_after_takeover():
    store = FakeStore()
    run_id = _queued(store)
    assert store.claim_run(run_id, "worker-a", 60) is not None
    old = RunOwnership("worker-a", 1)
    store.append_event(run_id, "RUN_STARTED", {"topic": "t"}, owner=old)  # 接管前合法

    _takeover(store, run_id)
    with pytest.raises(LeaseLostError):
        store.append_event(run_id, "STEP_FINISHED", {"node": "n1"}, owner=old)
    assert event_types(store, run_id) == ["RUN_STARTED"], "旧 attempt 的事件不得落库"


def test_old_attempt_usage_update_rejected_and_does_not_overwrite():
    store = FakeStore()
    run_id = _queued(store)
    assert store.claim_run(run_id, "worker-a", 60) is not None
    _takeover(store, run_id)

    new = RunOwnership("worker-b", 2)
    store.update_usage(run_id, token_used=999, cost_estimate_cny=9.9,
                       budget_used_cny=9.9, owner=new)
    with pytest.raises(LeaseLostError):
        store.update_usage(run_id, token_used=1, cost_estimate_cny=0.1,
                           budget_used_cny=0.1, owner=RunOwnership("worker-a", 1))
    row = store.get_run(run_id)
    assert row["token_used"] == 999 and row["cost_estimate_cny"] == 9.9, "旧用量不得覆盖"


def test_old_attempt_finalize_rejected_new_owner_succeeds():
    store = FakeStore()
    run_id = _queued(store)
    assert store.claim_run(run_id, "worker-a", 60) is not None
    _takeover(store, run_id)

    ok = store.finalize_run(
        run_id, event_type="RUN_FINISHED", payload={"stop_reason": "completed"},
        sequence=None, new_status="SUCCEEDED", allowed_from=ACTIVE_STATUSES,
        fields={"stop_reason": "completed"}, owner=RunOwnership("worker-a", 1))
    assert ok is False, "旧 attempt 的迟到终局必须整体回滚"
    row = store.get_run(run_id)
    assert row["status"] == "RUNNING" and row["worker_id"] == "worker-b"

    ok = store.finalize_run(
        run_id, event_type="RUN_FINISHED", payload={"stop_reason": "completed"},
        sequence=None, new_status="SUCCEEDED", allowed_from=ACTIVE_STATUSES,
        fields={"stop_reason": "completed"}, owner=RunOwnership("worker-b", 2))
    assert ok is True
    assert store.get_run(run_id)["status"] == "SUCCEEDED"


def test_persist_terminal_old_owner_rolls_back_artifacts():
    """F04：旧 owner 经 persist_terminal 的迟到终局不写产物、不改状态。"""
    store = FakeStore()
    run_id = _queued(store)
    assert store.claim_run(run_id, "worker-a", 60) is not None
    _takeover(store, run_id)

    finalized = persist_terminal(
        store, run_id, None, "RUN_FINISHED",
        {"stop_reason": "completed", "has_report": True},
        report="# 迟到的旧报告", topic="所有权测试",
        owner=RunOwnership("worker-a", 1))

    assert finalized is False
    assert store.get_run(run_id)["status"] == "RUNNING"
    assert store.get_artifact(run_id, "report_md") is None
    assert event_types(store, run_id).count("RUN_FINISHED") == 0


# ---------------------------------------------------------------- worker 层

def test_worker_abandons_run_after_lease_takeover():
    """A 执行中被 B 接管：A 在节点边界停止，不写终局、不覆盖 B 的所有权。"""
    store = FakeStore()
    run_id = _queued(store)
    worker_a = Worker(
        store, FakeQueue(),
        graph_factory=lambda: TinyGraph(steps=50, delay=0.05, report="# 迟到报告"),
        worker_id="worker-a", lease_seconds=60, heartbeat_seconds=0.01, poll_seconds=0)
    result: dict = {}

    thread = threading.Thread(
        target=lambda: result.update(claimed=worker_a.run_once(run_id)), daemon=True)
    thread.start()

    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if event_types(store, run_id):
            break
        time.sleep(0.01)
    assert event_types(store, run_id), "A 应已开始执行并写入事件"

    _takeover(store, run_id)
    thread.join(timeout=5)
    assert not thread.is_alive(), "A 必须在租约丢失后收口退出"
    assert result["claimed"] is True

    row = store.get_run(run_id)
    assert row["status"] == "RUNNING", "A 不得写终局（由新执行者收口）"
    assert row["worker_id"] == "worker-b"
    assert "RUN_FINISHED" not in event_types(store, run_id)


def test_worker_still_finalizes_with_ownership_when_healthy():
    """所有权正路径：租约未丢时 Worker 带 owner 正常收口（防止守卫误伤）。"""
    store = FakeStore()
    run_id = _queued(store)
    worker = Worker(store, FakeQueue(),
                    graph_factory=lambda: TinyGraph(steps=2, delay=0.01, report="# 正常"),
                    worker_id="worker-ok", lease_seconds=60, heartbeat_seconds=5,
                    poll_seconds=0)
    assert worker.run_once(run_id) is True

    row = store.get_run(run_id)
    assert row["status"] == "SUCCEEDED"
    assert store.get_artifact(run_id, "report_md") == "# 正常"
    assert event_types(store, run_id)[-1] == "RUN_FINISHED"


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
