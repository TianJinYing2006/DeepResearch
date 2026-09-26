"""P2-B RunStore 持久化测试（需要真实 PostgreSQL）。

默认跳过：仅当 `DR_TEST_DATABASE_URL` 设置时运行（CI 的 `infra` job 在 compose 的 PG 上跑）。
前置：目标库已执行 `tools/migrate.sh`（表结构来自 `migrations/`）。

零 LLM、零外部网络（只连本机 / CI 的 PostgreSQL）；测试数据用 `test-store-` 前缀隔离，
每个用例开始前清理。
"""
from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import psycopg
import pytest

from web.backend.store import RunStore

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")

TEST_USER = "test-store-user"
OTHER_USER = "test-store-other"


@pytest.fixture()
def store() -> RunStore:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE user_id LIKE 'test-store-%'")
    return RunStore(DSN)


def _create(store: RunStore, **kwargs) -> tuple[str, dict, bool]:
    run_id = uuid.uuid4().hex[:12]
    kwargs.setdefault("user_id", TEST_USER)
    row, created = store.create_run(run_id, "测试主题", {"instructions": "x"}, **kwargs)
    return run_id, row, created


def _now() -> datetime:
    return datetime.now(timezone.utc)


def test_create_and_get_roundtrip(store: RunStore):
    run_id, row, created = _create(store)
    assert created is True
    assert row["run_id"] == run_id
    assert row["status"] == "CREATED"
    assert row["topic"] == "测试主题"
    assert row["request"] == {"instructions": "x"}

    fetched = store.get_run(run_id)
    assert fetched is not None
    assert fetched["run_id"] == run_id
    assert store.get_run("no-such-run") is None


def test_idempotent_create_returns_existing(store: RunStore):
    first_id, _, created_first = _create(store, idempotency_key="key-1")
    second_id, row, created_second = _create(store, idempotency_key="key-1")
    assert created_first is True
    assert created_second is False
    assert second_id != first_id
    assert row["run_id"] == first_id

    # 幂等键按用户隔离：另一个用户可用同一个键
    _, _, created_other = _create(store, user_id=OTHER_USER, idempotency_key="key-1")
    assert created_other is True

    # 无幂等键的重复主题不互相影响
    _, _, a = _create(store)
    _, _, b = _create(store)
    assert a is True and b is True


def test_status_machine_optimistic_guard(store: RunStore):
    run_id, _, _ = _create(store)

    assert store.update_status(run_id, "QUEUED", allowed_from=("CREATED",)) is True
    assert store.update_status(run_id, "RUNNING", allowed_from=("QUEUED",), started_at=_now()) is True
    assert store.update_status(
        run_id, "SUCCEEDED", allowed_from=("RUNNING", "CANCEL_REQUESTED"),
        finished_at=_now(), research_status="success", stop_reason="completed",
    ) is True

    # 终局后不得再迁移（乐观守卫：当前状态已不在 allowed_from 内）
    assert store.update_status(run_id, "RUNNING", allowed_from=("QUEUED",)) is False
    fetched = store.get_run(run_id)
    assert fetched is not None and fetched["status"] == "SUCCEEDED"


def test_update_status_rejects_unknown_fields(store: RunStore):
    run_id, _, _ = _create(store)
    with pytest.raises(ValueError):
        store.update_status(run_id, "QUEUED", allowed_from=("CREATED",), drop_table=True)


def test_request_cancel_semantics(store: RunStore):
    # 未开始 ⇒ 直接终局取消
    created_id, _, _ = _create(store)
    assert store.request_cancel(created_id) == "CANCELLED"
    row = store.get_run(created_id)
    assert row is not None
    assert row["stop_reason"] == "user_cancelled"
    assert row["cancel_requested_at"] is not None
    assert row["finished_at"] is not None

    # 排队中 ⇒ 直接终局取消
    queued_id, _, _ = _create(store)
    assert store.update_status(queued_id, "QUEUED", allowed_from=("CREATED",)) is True
    assert store.request_cancel(queued_id) == "CANCELLED"

    # 运行中 ⇒ 只落取消请求（由执行者在节点边界收口）
    running_id, _, _ = _create(store)
    assert store.update_status(running_id, "RUNNING", allowed_from=("CREATED",)) is True
    assert store.request_cancel(running_id) == "CANCEL_REQUESTED"
    assert store.request_cancel(running_id) == "CANCEL_REQUESTED"  # 幂等
    row = store.get_run(running_id)
    assert row is not None and row["cancel_requested_at"] is not None

    # 已终局 ⇒ 原样返回，不产生取消痕迹
    assert store.update_status(
        running_id, "SUCCEEDED", allowed_from=("CANCEL_REQUESTED",), finished_at=_now()
    ) is True
    assert store.request_cancel(running_id) == "SUCCEEDED"
    assert store.request_cancel("no-such-run") is None


def test_append_event_monotonic_and_replay(store: RunStore):
    run_id, _, _ = _create(store)
    assert store.append_event(run_id, "RUN_STARTED", {"topic": "t"}) == 0
    assert store.append_event(run_id, "STEP_FINISHED", {"node": "plan"}) == 1
    assert store.append_event(run_id, "RUN_FINISHED", {}) == 2

    events = store.get_events(run_id)
    assert [e["sequence"] for e in events] == [0, 1, 2]
    assert events[0]["event_type"] == "RUN_STARTED"
    assert events[1]["payload"] == {"node": "plan"}

    resumed = store.get_events(run_id, after=0)
    assert [e["sequence"] for e in resumed] == [1, 2]
    assert store.get_events(run_id, after=2) == []


def test_append_event_unknown_run_raises(store: RunStore):
    with pytest.raises(LookupError):
        store.append_event("no-such-run", "RUN_STARTED")


def test_events_cascade_on_run_delete(store: RunStore):
    run_id, _, _ = _create(store)
    store.append_event(run_id, "RUN_STARTED")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
    assert store.get_events(run_id) == []


def test_artifacts_upsert_and_cascade(store: RunStore):
    run_id, _, _ = _create(store)
    assert store.get_artifact(run_id, "report_md") is None

    store.put_artifact(run_id, "report_md", "# v1")
    store.put_artifact(run_id, "report_md", "# v2")
    store.put_artifact(run_id, "export_json", '{"run_id": "x"}')
    assert store.get_artifact(run_id, "report_md") == "# v2"
    assert store.get_artifact(run_id, "export_json") == '{"run_id": "x"}'

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
    assert store.get_artifact(run_id, "report_md") is None


def test_list_runs_filter_and_pagination(store: RunStore):
    for _ in range(3):
        _create(store, user_id=TEST_USER)
    _create(store, user_id=OTHER_USER)

    mine = store.list_runs(user_id=TEST_USER)
    assert len(mine) == 3
    assert all(row["user_id"] == TEST_USER for row in mine)

    page = store.list_runs(user_id=TEST_USER, limit=2, offset=0)
    page2 = store.list_runs(user_id=TEST_USER, limit=2, offset=2)
    assert len(page) == 2 and len(page2) == 1
    assert {r["run_id"] for r in page}.isdisjoint({r["run_id"] for r in page2})

    queued = store.list_runs(user_id=TEST_USER, statuses=("QUEUED",))
    assert queued == []
    assert len(store.list_runs(user_id=OTHER_USER)) == 1
