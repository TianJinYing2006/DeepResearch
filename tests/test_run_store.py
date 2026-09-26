"""P2-B RunStore 持久化测试（需要真实 PostgreSQL）。

默认跳过：仅当 `DR_TEST_DATABASE_URL` 设置时运行（CI 的 `infra` job 在 compose 的 PG 上跑）。
前置：目标库已执行 `tools/migrate.sh`（表结构来自 `migrations/`）。

零 LLM、零外部网络（只连本机 / CI 的 PostgreSQL）；测试数据用 `test-store-` 前缀隔离，
每个用例开始前清理。
"""
from __future__ import annotations

import os
import time
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest

from research_engine.state import ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED, RunStep
from web.backend.auth import token_hash
from web.backend.runner import RunManager
from web.backend.store import RunStore

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")

TEST_USER = "test-store-user"
OTHER_USER = "test-store-other"


@pytest.fixture()
def store() -> RunStore:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE user_id LIKE 'test-store-%'")
        cur.execute("DELETE FROM invites WHERE created_by = 'test-store'")
        cur.execute("DELETE FROM users WHERE email LIKE '%@test-store.local'")
        # 0003 起 runs.user_id 有外键 ⇒ 预置本文件使用的两个固定用户
        cur.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES "
            "('test-store-user', 'test-store-user@test-store.local', 'x'), "
            "('test-store-other', 'test-store-other@test-store.local', 'x')"
        )
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
