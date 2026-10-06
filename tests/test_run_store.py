"""P2-B RunStore 持久化测试（需要真实 PostgreSQL）。

默认跳过：仅当 `DR_TEST_DATABASE_URL` 设置时运行（CI 的 `infra` job 在 compose 的 PG 上跑）。
前置：目标库已执行 `tools/migrate.sh`（表结构来自 `migrations/`）。

零 LLM、零外部网络（只连本机 / CI 的 PostgreSQL）；测试数据用 `test-store-` 前缀隔离，
每个用例开始前清理。
"""
from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from research_engine.state import ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED, RunStep
from web.backend.auth import token_hash
from web.backend.runner import RunManager
from web.backend.store import QuotaExceeded, RunStore

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")

TEST_USER = "test-store-user"
OTHER_USER = "test-store-other"


@pytest.fixture()
def store() -> RunStore:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE user_id LIKE 'test-store-%'")
        cur.execute("DELETE FROM invites WHERE created_by = 'test-store'")
        # 需求 23：三层表按测试前缀清理（无外键，顺序无关）
        cur.execute("DELETE FROM rag_ingestions WHERE user_id LIKE 'test-store-%'")
        cur.execute("DELETE FROM rag_parse_snapshots WHERE doc_id LIKE 'test-store-%'")
        cur.execute("DELETE FROM rag_chunks WHERE doc_id LIKE 'test-store-%'")
        cur.execute("DELETE FROM rag_index_generations WHERE doc_id LIKE 'test-store-%'")
        cur.execute("DELETE FROM users WHERE email LIKE '%@test-store.local'")
        # 0003 起 runs.user_id 有外键 ⇒ 预置本文件使用的两个固定用户
        cur.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES "
            "('test-store-user', 'test-store-user@test-store.local', 'x'), "
            "('test-store-other', 'test-store-other@test-store.local', 'x')"
        )
    instance = RunStore(DSN)
    yield instance
    instance.close()  # P2-3：归还连接池，避免用例间连接堆积


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
    first_id, _, created_first = _create(store, idempotency_key="key-1",
                                         request_hash="hash-1")
    second_id, row, created_second = _create(store, idempotency_key="key-1",
                                             request_hash="hash-2")
    assert created_first is True
    assert created_second is False
    assert second_id != first_id
    assert row["run_id"] == first_id
    assert row["request_hash"] == "hash-1"  # P1-1：指纹随行保存，供冲突判定

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


# ---------------------------------------------------------------- 需求 22 增补


def _create_topic(store: RunStore, topic: str) -> str:
    run_id = uuid.uuid4().hex[:12]
    store.create_run(run_id, topic, {"instructions": "x"}, user_id=TEST_USER)
    return run_id


def test_list_runs_search_archive_and_pin(store: RunStore):
    a = _create_topic(store, "量子计算综述")
    b = _create_topic(store, "蛋白质折叠")
    c = _create_topic(store, "量子退火")

    assert {r["run_id"] for r in store.list_runs(user_id=TEST_USER, q="量子")} == {a, c}
    # 通配符转义：`%` 按字面匹配，不作为 LIKE 通配符
    assert store.list_runs(user_id=TEST_USER, q="%") == []

    assert store.set_pinned(b, True, user_id=TEST_USER) is True
    assert store.list_runs(user_id=TEST_USER)[0]["run_id"] == b
    assert store.set_pinned(b, False, user_id=TEST_USER) is True

    assert store.set_archived(c, True, user_id=TEST_USER) is True
    assert {r["run_id"] for r in store.list_runs(user_id=TEST_USER)} == {a, b}
    assert [r["run_id"] for r in store.list_runs(user_id=TEST_USER, archived=True)] == [c]
    assert store.set_archived(c, False, user_id=TEST_USER) is True


def test_update_topic_and_flags_ownership(store: RunStore):
    run_id = _create_topic(store, "旧主题")
    assert store.update_topic(run_id, "新主题", user_id=TEST_USER) is True
    assert store.get_run(run_id)["topic"] == "新主题"
    assert store.update_topic(run_id, "越权", user_id=OTHER_USER) is False
    assert store.set_archived(run_id, True, user_id=OTHER_USER) is False
    assert store.set_pinned(run_id, True, user_id=OTHER_USER) is False
    assert store.get_run(run_id)["archived_at"] is None
    assert store.get_run(run_id)["pinned_at"] is None


def test_create_run_persists_retry_of(store: RunStore):
    base, _, _ = _create(store)
    retry_id = uuid.uuid4().hex[:12]
    store.create_run(retry_id, "测试主题", {"instructions": "x"},
                     user_id=TEST_USER, retry_of=base)
    assert store.get_run(retry_id)["retry_of"] == base


# ---------------------------------------------------------------- P2-C 增补


def test_append_event_explicit_sequence_and_idempotency(store: RunStore):
    run_id, _, _ = _create(store)
    assert store.append_event(run_id, "A", {"n": 1}, sequence=5) == 5
    assert store.append_event(run_id, "A", {"n": 1}, sequence=5) == 5  # 重复写入幂等
    assert store.append_event(run_id, "B", {"n": 2}, sequence=0) == 0
    assert [item["sequence"] for item in store.get_events(run_id)] == [0, 5]
    assert store.count_events(run_id) == 2
    assert store.count_events(run_id, "A") == 1
    assert store.count_events(run_id, "DEGRADATION") == 0
    assert store.last_event_type(run_id) == "A"
    with pytest.raises(LookupError):
        store.append_event("no-such-run", "X", {}, sequence=0)


def test_has_artifact_and_list_has_report(store: RunStore):
    run_id, _, _ = _create(store)
    assert store.has_artifact(run_id, "report_md") is False
    store.put_artifact(run_id, "report_md", "# r")
    assert store.has_artifact(run_id, "report_md") is True

    rows = store.list_runs(user_id=TEST_USER)
    assert rows and rows[0]["run_id"] == run_id
    assert rows[0]["has_report"] is True


def test_mark_stale_as_lost(store: RunStore):
    created_id, _, _ = _create(store)
    running_id, _, _ = _create(store)
    assert store.update_status(running_id, "RUNNING", allowed_from=("CREATED",)) is True
    done_id, _, _ = _create(store)
    assert store.update_status(done_id, "SUCCEEDED", allowed_from=("CREATED",)) is True

    assert store.mark_stale_as_lost() == 2
    assert store.get_run(created_id)["status"] == "LOST"
    assert store.get_run(running_id)["stop_reason"] == "lost"
    assert store.get_run(running_id)["finished_at"] is not None
    assert store.get_run(done_id)["status"] == "SUCCEEDED"


def _queued_and_claimed(store: RunStore, worker_id: str = "dead-worker",
                        lease_seconds: int = 0) -> str:
    run_id, _, _ = _create(store)
    assert store.update_status(run_id, "QUEUED", allowed_from=("CREATED",)) is True
    assert store.claim_run(run_id, worker_id, lease_seconds) is not None
    return run_id


def test_sweep_stale_runs_requeue_then_lost(store: RunStore):
    run_id = _queued_and_claimed(store)

    assert store.sweep_stale_runs(max_attempts=2) == [
        {"run_id": run_id, "action": "requeued", "attempt": 2}]
    row = store.get_run(run_id)
    assert row["status"] == "QUEUED"
    assert row["worker_id"] is None and row["lease_expires_at"] is None

    assert store.claim_run(run_id, "dead-worker-2", 0) is not None
    assert store.sweep_stale_runs(max_attempts=2) == [{"run_id": run_id, "action": "lost"}]
    row = store.get_run(run_id)
    assert row["status"] == "LOST" and row["stop_reason"] == "lost"


def test_sweep_stale_runs_respects_cancel_intent(store: RunStore):
    run_id = _queued_and_claimed(store)
    assert store.request_cancel(run_id) == "CANCEL_REQUESTED"

    assert store.sweep_stale_runs() == [{"run_id": run_id, "action": "cancelled"}]
    row = store.get_run(run_id)
    assert row["status"] == "CANCELLED" and row["stop_reason"] == "user_cancelled"


def test_sweep_stale_runs_ignores_fresh_lease(store: RunStore):
    run_id = _queued_and_claimed(store, worker_id="alive-worker", lease_seconds=600)
    assert store.sweep_stale_runs() == []
    assert store.get_run(run_id)["status"] == "RUNNING"


class _TinyGraph:
    """RunManager 端到端用的最小假 graph（零 LLM）。"""

    def __init__(self, steps: int = 3, delay: float = 0.01, report: str = "# 端到端报告"):
        self.steps = steps
        self.delay = delay
        self.report = report

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        state = ResearchState(topic=topic)
        for i in range(self.steps):
            time.sleep(self.delay)
            state.progress.append({"stage": f"n{i}", "msg": "m"})
            yield RunStep(index=i, node=f"n{i}", state=state, duration_ms=1)
            if should_cancel is not None and should_cancel():
                yield RunStep(index=i + 1, node=None, state=state,
                              terminal=True, stop_reason=STOP_CANCELLED)
                return
        state.report = self.report
        state.report_display = self.report
        yield RunStep(index=self.steps, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


def test_manager_write_through_end_to_end(store: RunStore):
    manager = RunManager(graph_factory=lambda: _TinyGraph(), store=store, max_concurrent_runs=8)
    run_id = manager.start("端到端", idempotency_key="e2e-key")
    assert manager.start("端到端", idempotency_key="e2e-key") == run_id  # 幂等：不重复执行

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        snap = manager.snapshot(run_id)
        if snap is not None and snap["status"] == "finished":
            break
        time.sleep(0.02)
    else:
        raise AssertionError("manager E2E did not finish in time")

    try:
        # 落库在内存终局之后完成（锁外 I/O）⇒ 等库内终局再断言
        row = None
        while time.monotonic() < deadline:
            row = store.get_run(run_id)
            if row is not None and row["status"] != "RUNNING":
                break
            time.sleep(0.02)
        assert row is not None and row["status"] == "SUCCEEDED"
        assert row["research_status"] == "success"
        events = store.get_events(run_id)
        assert [item["sequence"] for item in events] == list(range(len(events)))
        assert events[0]["event_type"] == "RUN_STARTED"
        assert events[-1]["event_type"] == "RUN_FINISHED"
        assert store.get_artifact(run_id, "report_md") == "# 端到端报告"
        assert store.has_artifact(run_id, "export_json") is True
    finally:
        # manager 建的行 user_id 为 NULL，不在 fixture 的 test-store- 前缀清理范围内，单独删。
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))


def test_quota_reservation_and_moderation_atomic_contract(store: RunStore):
    """P0-2 / P0-4：预留式预算、终局结算与审核决定同事务（真实 PG SQL 契约）。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            """
            SELECT COALESCE((
                       SELECT SUM(r.cost_estimate_cny) FROM runs r
                        WHERE r.created_at >= date_trunc('month', now())
                   ), 0)
                 + COALESCE((
                       SELECT SUM(GREATEST(qr.reserved_cny
                                           - COALESCE(r.cost_estimate_cny, 0), 0))
                         FROM quota_reservations qr
                         LEFT JOIN runs r ON r.run_id = qr.run_id
                        WHERE qr.status = 'reserved'
                          AND qr.period = date_trunc('month', now())::date
                   ), 0)
            """
        )
        committed = float(cur.fetchone()[0])
    budget = committed + 1.5

    run_id = f"test-store-resv-{uuid.uuid4().hex[:6]}"
    store.create_run_admitted(run_id, "预留", {}, user_id=TEST_USER, status="QUEUED",
                              monthly_budget_cny=budget, reserve_cny=1.5)
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, reserved_cny FROM quota_reservations WHERE run_id = %s",
                    (run_id,))
        hold = cur.fetchone()
        assert hold[0] == "reserved" and float(hold[1]) == 1.5

    # 已预留 → 第二个任务看到 committed=budget ⇒ 拒绝（旧实现会一起放行）
    with pytest.raises(QuotaExceeded) as exc:
        store.create_run_admitted(f"test-store-resv-{uuid.uuid4().hex[:6]}", "预留2", {},
                                  user_id=TEST_USER, status="QUEUED",
                                  monthly_budget_cny=budget, reserve_cny=1.5)
    assert exc.value.kind == "monthly_budget"

    # 终局：审核决定 + 预留结算同事务
    assert store.finalize_run(
        run_id, event_type="RUN_FINISHED", payload={"stop_reason": "completed"},
        sequence=0, new_status="SUCCEEDED", allowed_from=("QUEUED",),
        fields={"stop_reason": "completed", "cost_estimate_cny": 0.4},
        moderation={"kind": "output_decision",
                    "detail": {"decision": "allow", "decision_id": "dec-pg-1"}},
    ) is True
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("SELECT status, actual_cny, released_cny FROM quota_reservations "
                    "WHERE run_id = %s", (run_id,))
        settled = cur.fetchone()
        assert settled[0] == "settled"
        assert float(settled[1]) == 0.4 and float(settled[2]) == 1.1
        cur.execute("SELECT detail FROM moderation_records "
                    "WHERE run_id = %s AND kind = 'output_decision'", (run_id,))
        record = cur.fetchone()
        assert record is not None and record[0]["decision_id"] == "dec-pg-1"
        cur.execute("DELETE FROM moderation_records WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))


def test_rag_ingestion_active_unique_contract(store: RunStore):
    """P0-11：活跃 doc_id 唯一 + ON CONFLICT DO NOTHING 的真实 PG 语义。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM rag_ingestions WHERE doc_id LIKE 'test-store:%'")
    doc_id = f"{TEST_USER}:dedupe"
    first = store.create_ingestion(f"ing{uuid.uuid4().hex[:8]}", doc_id,
                                   user_id=TEST_USER, source="a.md", sha256="dedupe",
                                   size_bytes=1, stored_name="a.md")
    assert first is not None
    second = store.create_ingestion(f"ing{uuid.uuid4().hex[:8]}", doc_id,
                                    user_id=TEST_USER, source="b.md", sha256="dedupe",
                                    size_bytes=1, stored_name="b.md")
    assert second is None  # 冲突：调用方回查既有记录
    found = store.find_ingestion_by_doc(TEST_USER, doc_id)
    assert found["ingestion_id"] == first["ingestion_id"]

    store.mark_ingestion_deleted(first["ingestion_id"])
    third = store.create_ingestion(f"ing{uuid.uuid4().hex[:8]}", doc_id,
                                   user_id=TEST_USER, source="c.md", sha256="dedupe",
                                   size_bytes=1, stored_name="c.md")
    assert third is not None  # deleted 后可重传

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM rag_ingestions WHERE doc_id LIKE 'test-store:%'")

def test_users_sessions_invites_contract(store: RunStore):
    """P4-A 账号契约（真实 PG）：邮箱唯一（大小写不敏感）/ 邀请码一次性 / 会话有效期与封禁。"""
    suffix = uuid.uuid4().hex[:8]
    user = store.create_user(f"u{suffix}a", f"U{suffix}@test-store.local", "hash")
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.create_user(f"u{suffix}b", f"u{suffix}@TEST-STORE.local", "hash")

    # 邀请码一次性 + 邮箱重复时邀请码不被消耗
    store.create_invite(token_hash("code-1"), created_by="test-store",
                        expires_at=datetime.now(timezone.utc) + timedelta(days=1))
    invitee = store.register_with_invite(f"u{suffix}c", f"c{suffix}@test-store.local", "hash",
                                         token_hash("code-1"))
    with pytest.raises(ValueError):
        store.register_with_invite(f"u{suffix}d", f"d{suffix}@test-store.local", "hash",
                                   token_hash("code-1"))
    store.create_invite(token_hash("code-2"), created_by="test-store")
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.register_with_invite(f"u{suffix}e", f"c{suffix}@test-store.local", "hash",
                                   token_hash("code-2"))
    store.register_with_invite(f"u{suffix}f", f"f{suffix}@test-store.local", "hash",
                               token_hash("code-2"))

    # 过期邀请码
    store.create_invite(token_hash("code-3"), created_by="test-store",
                        expires_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    with pytest.raises(ValueError):
        store.register_with_invite(f"u{suffix}g", f"g{suffix}@test-store.local", "hash",
                                   token_hash("code-3"))

    # 会话有效期 / 封禁即时生效
    store.create_session(token_hash("tok-1"), user["user_id"],
                         datetime.now(timezone.utc) + timedelta(seconds=60))
    assert store.get_session_user(token_hash("tok-1"))["user_id"] == user["user_id"]
    store.set_user_status(user["user_id"], "banned")
    assert store.get_session_user(token_hash("tok-1")) is None
    store.set_user_status(invitee["user_id"], "active")

    store.create_session(token_hash("tok-2"), invitee["user_id"],
                         datetime.now(timezone.utc) - timedelta(seconds=1))
    assert store.get_session_user(token_hash("tok-2")) is None
    assert store.purge_expired_sessions() >= 1
    assert store.revoke_session(token_hash("tok-2")) is False

    # 撤销邀请码后不可再注册
    store.create_invite(token_hash("code-4"), created_by="test-store")
    assert store.revoke_invite(token_hash("code-4")) is True
    with pytest.raises(ValueError):
        store.register_with_invite(f"u{suffix}h", f"h{suffix}@test-store.local", "hash",
                                   token_hash("code-4"))


def test_user_quota_and_password_helpers(store: RunStore):
    """P4-B 仓储支撑：每日计数 / 月度成本 / 改密与全量吊销会话。"""
    uid = TEST_USER
    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    before = store.count_user_runs_since(uid, since)
    run_id, _, _ = _create(store, user_id=uid)
    assert store.count_user_runs_since(uid, since) == before + 1
    assert store.count_user_runs_since(OTHER_USER, since) == 0

    assert store.update_status(run_id, "SUCCEEDED", allowed_from=("CREATED",),
                               cost_estimate_cny=0.25) is True
    assert store.month_cost_cny() >= 0.25

    assert store.update_password(uid, "new-hash") is True
    assert store.get_user(uid)["password_hash"] == "new-hash"

    store.create_session(token_hash("q-tok"), uid,
                         datetime.now(timezone.utc) + timedelta(minutes=5))
    assert store.get_session_user(token_hash("q-tok")) is not None
    assert store.revoke_user_sessions(uid) >= 1
    assert store.get_session_user(token_hash("q-tok")) is None


def test_moderation_contract_and_user_deletion(store: RunStore):
    """P7-A：审核记录只追加；注销删用户并脱钩（记录/任务匿名保留、会话级联删除）。"""
    uid = TEST_USER
    run_id, _, _ = _create(store, user_id=uid)
    record_id = store.record_moderation("output_flagged", user_id=uid, run_id=run_id,
                                        detail={"matches": ["x"]})
    assert record_id >= 1
    assert store.set_moderation_status(run_id, "flagged") is True
    assert store.get_run(run_id)["moderation_status"] == "flagged"

    store.create_session(token_hash("mod-tok"), uid,
                         datetime.now(timezone.utc) + timedelta(hours=1))
    assert store.delete_user(uid) is True
    assert store.get_user(uid) is None
    assert store.get_session_user(token_hash("mod-tok")) is None
    assert store.get_run(run_id)["user_id"] is None  # SET NULL：任务保留但匿名
    flagged = [r for r in store.list_moderation(kind="output_flagged") if r["id"] == record_id]
    assert flagged and flagged[0]["user_id"] is None and flagged[0]["run_id"] == run_id

    # 注销后该 run 的 user_id 已置空 ⇒ fixture 的前缀清理覆盖不到它；
    # 必须显式清掉，否则会以 CREATED 形态留在库里污染 count_active（实测踩坑）。
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
        cur.execute("DELETE FROM moderation_records WHERE id = %s", (record_id,))


def test_status_counts_and_stale_leases(store: RunStore):
    """P8-A 指标支撑：状态分布计数与过期租约计数。"""
    uid = TEST_USER
    stale_id, _, _ = _create(store, user_id=uid)
    assert store.update_status(stale_id, "QUEUED", allowed_from=("CREATED",)) is True
    assert store.claim_run(stale_id, "dead-worker", 0) is not None  # 租约立即过期

    fresh_id, _, _ = _create(store, user_id=uid)
    assert store.update_status(fresh_id, "QUEUED", allowed_from=("CREATED",)) is True
    assert store.claim_run(fresh_id, "alive-worker", 600) is not None

    since = datetime.now(timezone.utc) - timedelta(minutes=5)
    counts = store.status_counts_since(since)
    assert counts.get("RUNNING", 0) == 2
    assert store.count_stale_leases() == 1  # 只有 stale_id 过期

    # 显式清理：本测试留下两条 RUNNING（一条过期）会污染后续集成测试的
    # count_active 与 sweep 断言（与 P7-A 注销测试同类坑，D 盘实测教训）。
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id IN (%s, %s)", (stale_id, fresh_id))


def test_create_run_admitted_serializes_concurrency(store: RunStore):
    """P0-3：并发准入下全局并发闸不得被突破（真实 PostgreSQL advisory lock）。"""
    results: list[tuple] = []
    lock = threading.Lock()

    def worker(index: int) -> None:
        try:
            row, created = store.create_run_admitted(
                f"race{index:08d}", "t", {"instructions": "x"},
                user_id=TEST_USER, status="QUEUED",
                global_active_limit=1, user_active_limit=1,
                daily_limit=None, monthly_budget_cny=None)
            with lock:
                results.append(("created", row["run_id"], created))
        except QuotaExceeded as exc:
            with lock:
                results.append(("quota", exc.kind, None))

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(5)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    created = [item for item in results if item[0] == "created"]
    quota = [item for item in results if item[0] == "quota"]
    assert len(created) == 1
    assert len(quota) == 4
    assert all(item[1] in ("global_concurrency", "user_concurrency") for item in quota)

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id LIKE 'race%'")


def test_claim_next_queued_atomic(store: RunStore):
    """P0-2：SKIP LOCKED 领取互不重复；过期 QUEUED 不可领、由清扫收口为 TIMED_OUT。"""
    ids = []
    for _ in range(2):
        rid, _, _ = _create(store, status="QUEUED")
        ids.append(rid)
    expired_id, _, _ = _create(store, status="QUEUED",
                               timeout_at=datetime.now(timezone.utc) - timedelta(seconds=1))

    first = store.claim_next_queued("w1", 120)
    second = store.claim_next_queued("w2", 120)
    assert first is not None and second is not None
    assert {first["run_id"], second["run_id"]} == set(ids)
    assert store.claim_next_queued("w3", 120) is None  # 过期行不可领
    assert store.count_queued() == 1  # 只剩过期那条

    swept = store.sweep_stale_runs()
    assert {"run_id": expired_id, "action": "timed_out"} in swept
    assert store.get_run(expired_id)["status"] == "TIMED_OUT"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id = ANY(%s)", (ids + [expired_id],))


def test_account_deletion_outbox_contract(store: RunStore):
    """P0-7：注销登记同事务（台账 + outbox + 删用户）；领取 / 退避 / 完成流转。"""
    uid = "test-store-del-user"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (%s, %s, 'x') "
            "ON CONFLICT DO NOTHING",
            (uid, "test-store-del@test-store.local"),
        )
    request_id = uuid.uuid4().hex[:12]
    store.request_account_deletion(request_id, uid)

    assert store.get_user(uid) is None
    record = store.deletion_status(request_id)
    assert record["status"] == "pending"
    assert [item["target"] for item in record["targets"]] == ["qdrant"]

    claimed = store.claim_deletion_outbox("w1", 60, limit=1)
    assert len(claimed) == 1 and claimed[0]["attempts"] == 1
    assert store.claim_deletion_outbox("w2", 60, limit=1) == []  # 租约未过期

    assert store.mark_deletion_retry(claimed[0]["id"], "boom",
                                     backoff_seconds=60, max_attempts=5) == "pending"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE deletion_outbox SET next_attempt_at = now() WHERE id = %s",
                    (claimed[0]["id"],))
    again = store.claim_deletion_outbox("w2", 60, limit=1)
    assert len(again) == 1 and again[0]["attempts"] == 2

    store.mark_deletion_done(again[0]["id"])
    record = store.deletion_status(request_id)
    assert record["status"] == "completed" and record["completed_at"] is not None

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM account_deletions WHERE request_id = %s", (request_id,))
        cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


def test_rag_ingestion_contract(store: RunStore):
    """P0-8b：摄取台账 claim/ready/retention 流转（真实 PG）。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM rag_ingestions")
    ingestion_id = f"ing{uuid.uuid4().hex[:8]}"
    store.create_ingestion(ingestion_id, f"{TEST_USER}:abc", user_id=TEST_USER,
                           source="a.md", sha256="abc", size_bytes=3, stored_name="x.md")

    row = store.claim_next_ingestion("w1", 60)
    assert row is not None and row["ingestion_id"] == ingestion_id and row["attempts"] == 1
    assert store.claim_next_ingestion("w2", 60) is None  # 租约未过期

    store.mark_ingestion_ready(ingestion_id, 2)
    assert store.get_ingestion(ingestion_id)["status"] == "ready"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE rag_ingestions SET created_at = now() - interval '100 days' "
                    "WHERE ingestion_id = %s", (ingestion_id,))
    expired = store.list_expired_ingestions(datetime.now(timezone.utc) - timedelta(days=90))
    assert any(item["ingestion_id"] == ingestion_id for item in expired)
    store.mark_ingestion_deleted(ingestion_id)
    assert store.get_ingestion(ingestion_id)["status"] == "deleted"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM rag_ingestions WHERE ingestion_id = %s", (ingestion_id,))


def test_audit_log_contract(store: RunStore):
    """P1-5：审计只追加；按 action / actor 过滤；detail 保留。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs")
    store.record_audit("login_failed", detail={"email_hash": "abc"}, ip="127.0.0.1")
    store.record_audit("login_success", actor_user_id=TEST_USER, request_id="req-1")

    failed = store.list_audit(action="login_failed")
    assert len(failed) == 1 and failed[0]["detail"]["email_hash"] == "abc"
    mine = store.list_audit(actor_user_id=TEST_USER)
    assert len(mine) == 1 and mine[0]["request_id"] == "req-1"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM audit_logs")


def test_worker_registry_contract(store: RunStore):
    """P1-3：注册 / 心跳 / 存活计数 / draining→stopped / 过期清理（真实 PG）。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM workers")
    store.register_worker("w-test", version="v1", hostname="h1")
    assert store.count_live_workers(within_seconds=90) == 1

    assert store.heartbeat_worker("w-test", in_flight=1, current_run_id="r1") is True
    row = store.list_workers()[0]
    assert row["in_flight"] == 1 and row["current_run_id"] == "r1"

    store.mark_worker_status("w-test", "draining")
    assert store.count_live_workers(within_seconds=90) == 0
    store.mark_worker_status("w-test", "stopped")
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE workers SET last_heartbeat_at = now() - interval '10 days' "
                    "WHERE worker_id = 'w-test'")
    assert store.purge_stale_workers(days=7) == 1
    assert store.list_workers() == []


def test_session_governance_and_reset_contract(store: RunStore):
    """P1-10：会话元数据 / 远程终止 / 重置 token 单次消费 + 会话吊销（真实 PG）。"""
    uid = "test-store-sess-user"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (user_id, email, password_hash) VALUES (%s, %s, 'x') "
                    "ON CONFLICT DO NOTHING", (uid, "test-store-sess@test-store.local"))
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (uid,))

    sid = store.create_session(token_hash("sess-a"), uid,
                               datetime.now(timezone.utc) + timedelta(hours=1),
                               ip="203.0.113.1", user_agent="UA/1")
    assert sid
    user = store.get_session_user(token_hash("sess-a"))
    assert user["session_id"] == sid and user["session_ip"] == "203.0.113.1"
    store.touch_session(token_hash("sess-a"))
    assert store.list_sessions(uid)[0]["last_seen_at"] is not None

    store.create_password_reset(token_hash("reset-a"), uid,
                                datetime.now(timezone.utc) + timedelta(minutes=30))
    store.create_session(token_hash("sess-b"), uid,
                         datetime.now(timezone.utc) + timedelta(hours=1))
    assert store.complete_password_reset(token_hash("reset-a"), "new-hash") == uid
    assert store.get_user(uid)["password_hash"] == "new-hash"
    assert store.list_sessions(uid) == []  # 全部会话吊销
    assert store.consume_password_reset(token_hash("reset-a")) is None  # 单次消费

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM sessions WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


def test_password_reset_cooldown_contract(store: RunStore):
    """需求 24：冷却检查 —— 未消费 token 在窗口内命中，消费后不再命中。"""
    uid = "test-store-cooldown-user"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO users (user_id, email, password_hash) VALUES (%s, %s, 'x') "
                    "ON CONFLICT DO NOTHING", (uid, "test-store-cooldown@test-store.local"))
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (uid,))

    assert store.has_recent_password_reset(uid, 60) is False
    store.create_password_reset(token_hash("reset-cooldown"), uid,
                                datetime.now(timezone.utc) + timedelta(minutes=30),
                                created_by="system")
    assert store.has_recent_password_reset(uid, 60) is True
    assert store.has_recent_password_reset(uid, 0) is False  # 窗口为 0 时不再命中

    store.consume_password_reset(token_hash("reset-cooldown"))
    assert store.has_recent_password_reset(uid, 3600) is False  # 已消费不算

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (uid,))
        cur.execute("DELETE FROM users WHERE user_id = %s", (uid,))


def test_usage_ledger_contract(store: RunStore):
    """P1-4：逐调用账本写入 / 汇总（先对请求数再对钱）。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM usage_ledger")
    store.record_usage(run_id="test-usage-run", attempt=1, kind="llm", provider="dashscope",
                       model="qwen-plus", role="critic", input_tokens=100,
                       output_tokens=20, total_tokens=120, cost_estimate_cny=0.00024,
                       cost_source="estimate", request_id="cmpl-1")
    store.record_usage(run_id="test-usage-run", attempt=2, kind="search", provider="bocha",
                       role="web", cost_source="per_call")

    summary = store.usage_summary(run_id="test-usage-run")
    assert summary["calls"] == 2 and summary["tokens"] == 120
    rows = store.list_usage(run_id="test-usage-run")
    assert rows[0]["kind"] == "search"  # 倒序
    assert rows[1]["request_id"] == "cmpl-1"

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM usage_ledger WHERE run_id = 'test-usage-run'")


def test_finalize_run_atomic_contract(store: RunStore):
    """P0-6：状态迁移 + 终局事件 + 产物同一事务；迁移失败不得写半成品。"""
    run_id, _, _ = _create(store, user_id=TEST_USER, status="RUNNING")

    ok = store.finalize_run(
        run_id, event_type="RUN_FINISHED", payload={"stop_reason": "completed"},
        sequence=None, new_status="SUCCEEDED",
        allowed_from=("RUNNING", "CANCEL_REQUESTED"),
        fields={"stop_reason": "completed", "finished_at": _now()},
        artifacts={"report_md": {"body": "# 正文"}, "export_json": {"body": "{}"}},
    )
    assert ok is True
    assert store.get_run(run_id)["status"] == "SUCCEEDED"
    artifact = store.get_artifact_row(run_id, "report_md")
    assert artifact["storage"] == "db" and artifact["body"] == "# 正文"
    assert [e["event_type"] for e in store.get_events(run_id)] == ["RUN_FINISHED"]

    # 已终局的任务再次终局：整体回滚（事件不追加、产物不覆盖、状态不改判）
    assert store.finalize_run(
        run_id, event_type="RUN_FINISHED", payload={}, sequence=None,
        new_status="FAILED", allowed_from=("RUNNING",),
        fields={"stop_reason": "error"}, artifacts={"report_md": {"body": "# 迟到报告"}},
    ) is False
    assert store.get_run(run_id)["status"] == "SUCCEEDED"
    assert store.get_artifact(run_id, "report_md") == "# 正文"
    assert len(store.get_events(run_id)) == 1

    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))


# ---------------------------------------------------------------- 需求 23 增补（三层数据 / 版本化重建）


def test_rag_three_layers_and_generation_switch(store: RunStore):
    doc_id = f"test-store-{uuid.uuid4().hex[:8]}:doc"
    assert store.replace_parse_snapshot(doc_id, [
        {"block_index": 0, "kind": "heading", "title_path": ["标题"], "locator": {}, "text": "标题"},
        {"block_index": 1, "kind": "paragraph", "title_path": ["标题"],
         "locator": {"page": 1}, "text": "正文"},
    ]) == 2
    assert store.count_parse_snapshot(doc_id) == 2

    generation = store.create_index_generation(
        doc_id, chunker_version="v2", embedding_model="text-embedding-v3", embedding_dim=1024)
    store.insert_rag_chunks(doc_id, generation, [
        {"chunk_index": 0, "chunk_id": f"{doc_id}:g{generation}:0", "text": "正文",
         "embed_text": "标题\n\n正文", "title_path": ["标题"], "locator": {"page": 1}},
    ])
    assert store.count_rag_chunks(doc_id, generation) == 1
    rows, total = store.list_rag_chunks(doc_id, generation, offset=0, limit=10)
    assert total == 1 and rows[0]["chunk_id"].endswith(":0")
    assert store.activate_index_generation(doc_id, generation) == []
    assert store.get_active_generation(doc_id) == generation
    revision_after_first = store.get_rag_revision()

    # 二代构建 → 切换后一代退役；提交条件校验拒绝重复激活
    generation2 = store.create_index_generation(
        doc_id, chunker_version="v2", embedding_model="text-embedding-v3", embedding_dim=1024)
    store.insert_rag_chunks(doc_id, generation2, [
        {"chunk_index": 0, "chunk_id": f"{doc_id}:g{generation2}:0", "text": "正文二",
         "embed_text": "标题\n\n正文二", "title_path": ["标题"], "locator": {"page": 1}},
    ])
    assert store.activate_index_generation(doc_id, generation2) == [generation]
    assert store.get_active_generation(doc_id) == generation2
    assert store.get_rag_revision() == revision_after_first + 1
    assert any(row["doc_id"] == doc_id and row["generation"] == generation
               for row in store.list_retired_generations())
    with pytest.raises(ValueError):
        store.activate_index_generation(doc_id, generation2)

    # 清理退役代 → 行与产物消失；活动代不受影响
    store.mark_generation_cleaned(doc_id, generation)
    assert store.count_rag_chunks(doc_id, generation) == 0
    assert not any(row["doc_id"] == doc_id and row["generation"] == generation
                   for row in store.list_retired_generations())

    # 删除三层
    store.delete_rag_layers(doc_id)
    assert store.count_parse_snapshot(doc_id) == 0
    assert store.get_active_generation(doc_id) is None


def test_rag_meta_usage_and_requeue(store: RunStore):
    ingestion_id = uuid.uuid4().hex[:12]
    doc_id = f"test-store-{uuid.uuid4().hex[:8]}:doc"
    store.create_ingestion(ingestion_id, doc_id, user_id=TEST_USER, source="a.md",
                           sha256="x", size_bytes=100, stored_name="s.md")
    assert store.get_rag_doc_for_user(doc_id, TEST_USER)["doc_id"] == doc_id
    assert store.update_rag_doc_meta(doc_id, TEST_USER, display_name="手册", tags=["x"]) is True
    row = store.get_rag_doc_for_user(doc_id, TEST_USER)
    assert row["display_name"] == "手册" and row["tags"] == ["x"]
    assert store.rag_usage_bytes(TEST_USER) >= 100
    assert doc_id in store.list_rag_doc_ids_for_user(TEST_USER)

    store.mark_ingestion_ready(ingestion_id, 1)
    assert store.requeue_ingestion_for_task(ingestion_id, "rechunk") is True
    requeued = store.get_ingestion(ingestion_id)
    assert requeued["task"] == "rechunk" and requeued["status"] == "pending"

    assert store.delete_ingestion_by_doc(TEST_USER, doc_id) == ["s.md"]
    assert store.get_rag_doc_for_user(doc_id, TEST_USER) is None
