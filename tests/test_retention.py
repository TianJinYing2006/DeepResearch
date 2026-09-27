"""P2-2 数据保留期清理测试。

- FakeStore 路径（默认运行）：政策 / dry-run / 护栏 / 级联语义。
- 真实 PostgreSQL 契约（`DR_TEST_DATABASE_URL`，CI `infra` job）：`purge_before`
  批量子句 + 终态过滤 + 级联删除 + 白名单防呆。
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore

from web.backend.retention import policies, run_retention

TEST_USER = "test-retention-user"
TEST_EMAIL = "test-retention@test-retention.local"
DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()


def _seed_run(store: FakeStore, run_id: str, *, status: str,
              finished_days_ago: float | None = None) -> dict:
    row, _ = store.create_run(run_id, "t", user_id=TEST_USER, status="CREATED")
    row["status"] = status
    if finished_days_ago is not None:
        row["finished_at"] = datetime.now(UTC) - timedelta(days=finished_days_ago)
    return row


def _seed_event(store: FakeStore, run_id: str, event_type: str, days_ago: float) -> None:
    store.append_event(run_id, event_type, {})
    store.events[run_id][-1]["created_at"] = datetime.now(UTC) - timedelta(days=days_ago)


def test_retention_purges_only_expired_terminal_runs_and_their_children():
    store = FakeStore()
    old_ok = _seed_run(store, "oldok0000001", status="SUCCEEDED", finished_days_ago=100)
    old_running = _seed_run(store, "oldrun000001", status="RUNNING")
    recent_ok = _seed_run(store, "recent000001", status="SUCCEEDED", finished_days_ago=5)
    _seed_event(store, old_ok["run_id"], "run_finished", 100)
    _seed_event(store, old_running["run_id"], "node_started", 40)  # 事件超 30 天：删
    _seed_event(store, old_running["run_id"], "node_started", 1)   # 事件未超期：留
    _seed_event(store, recent_ok["run_id"], "run_finished", 2)
    store.put_artifact(old_ok["run_id"], "report_md", "旧报告")

    summary = run_retention(store)

    assert store.get_run(old_ok["run_id"]) is None                    # 终态超 90 天：级联删除
    assert old_ok["run_id"] not in store.events
    assert old_ok["run_id"] not in store.artifacts
    assert store.get_run(old_running["run_id"]) is not None           # 非终态：绝不按 runs 删
    assert store.count_events(old_running["run_id"]) == 1             # 仅超 30 天的事件被删
    assert store.get_run(recent_ok["run_id"]) is not None
    assert summary["deleted_total"] >= 3
    assert store.list_audit(action="retention_purge")


def test_retention_dry_run_counts_without_deleting():
    store = FakeStore()
    row = _seed_run(store, "dryrun000001", status="SUCCEEDED", finished_days_ago=200)
    _seed_event(store, row["run_id"], "run_finished", 200)

    summary = run_retention(store, dry_run=True)

    assert summary["dry_run"] is True
    assert summary["deleted_total"] >= 2
    assert store.get_run(row["run_id"]) is not None
    assert store.count_events(row["run_id"]) == 1
    # dry-run 同样留痕，便于演练审计
    purge_records = [r for r in store.audits if r["action"] == "retention_purge"]
    assert purge_records and all(r["detail"]["dry_run"] is True for r in purge_records)


def test_retention_guardrail_aborts_when_most_of_table_expired():
    store = FakeStore()
    row = _seed_run(store, "guard0000001", status="RUNNING")
    for index in range(1200):  # > guard_min_rows 且 > 50% 总量 ⇒ 中止
        _seed_event(store, row["run_id"], f"node-{index}", 40)

    summary = run_retention(store)

    events_result = next(r for r in summary["results"] if r["table"] == "run_events")
    assert events_result["aborted"] is True
    assert events_result["deleted"] == 0
    assert store.count_events(row["run_id"]) == 1200
    guard = store.list_audit(action="retention_guardrail")
    assert guard and guard[0]["detail"]["table"] == "run_events"


def test_retention_env_override(monkeypatch):
    monkeypatch.setenv("DR_RETENTION_EVENTS_DAYS", "7")
    by_table = {policy.table: policy for policy in policies()}
    assert by_table["run_events"].days == 7
    assert by_table["runs"].days == 90
    assert by_table["audit_logs"].days == 180


def test_purge_targets_are_whitelisted():
    store = FakeStore()
    with pytest.raises(ValueError):
        store.count_table("users")
    with pytest.raises(ValueError):
        store.purge_before("runs; DROP TABLE users", "finished_at", datetime.now(UTC))


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_purge_before_pg_batches_cascades_and_keeps_recent():
    import psycopg

    from web.backend.store import RunStore

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE user_id = %s", (TEST_USER,))
        cur.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (%s, %s, 'x') "
            "ON CONFLICT (user_id) DO NOTHING",
            (TEST_USER, TEST_EMAIL),
        )

    store = RunStore(DSN)
    try:
        old = datetime.now(UTC) - timedelta(days=100)
        recent = datetime.now(UTC) - timedelta(days=1)
        old_ids = [uuid.uuid4().hex[:12] for _ in range(2)]
        new_id = uuid.uuid4().hex[:12]
        # runs 表有 CHECK (finished_at >= created_at)：造历史数据需同步回拨 created_at
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            for run_id, created in ((old_ids[0], old), (old_ids[1], old), (new_id, recent)):
                store.create_run(run_id, "retention-pg", {}, user_id=TEST_USER)
                cur.execute("UPDATE runs SET created_at = %s WHERE run_id = %s",
                            (created, run_id))
        for run_id in old_ids:
            assert store.update_status(run_id, "SUCCEEDED", allowed_from=("CREATED",),
                                       finished_at=old, started_at=old)
            store.append_event(run_id, "run_finished", {})
        assert store.update_status(new_id, "SUCCEEDED", allowed_from=("CREATED",),
                                   finished_at=recent, started_at=recent)

        runs_policy = next(p for p in policies() if p.table == "runs")
        before = datetime.now(UTC) - timedelta(days=90)
        assert store.count_before("runs", "finished_at", before,
                                  where_extra=runs_policy.where_extra) >= 2

        remaining = store.count_before("runs", "finished_at", before,
                                       where_extra=runs_policy.where_extra)
        deleted = 0
        while remaining:
            purged = store.purge_before("runs", "finished_at", before,
                                        where_extra=runs_policy.where_extra, batch=1)
            if not purged:
                break
            deleted += purged
            remaining = store.count_before("runs", "finished_at", before,
                                           where_extra=runs_policy.where_extra)
        assert deleted >= 2

        assert store.get_run(new_id) is not None
        for run_id in old_ids:
            assert store.get_run(run_id) is None
            assert store.count_events(run_id) == 0  # FK CASCADE：事件随运行删除
    finally:
        store.close()
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE user_id = %s", (TEST_USER,))
