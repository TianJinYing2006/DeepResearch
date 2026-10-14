"""需求 36 Phase 1：HITL 交互原子协议（真实 PostgreSQL）。

默认跳过：仅当 `DR_TEST_DATABASE_URL` 设置时运行（CI 的 `infra` job 在 compose 的 PG 上跑）。
前置：目标库已执行 `tools/migrate.sh`（表结构来自 `migrations/`，最新 0023）。

覆盖 §12.4.1 契约清单：挂起释放租约 / 回答幂等 / 旧卡片冲突 / 到期（澄清自动继续、
审批不自动批准）/ 等待态取消 / 租约清扫豁免 / 计数口径 / 事件重放 / API 回答端点。
零 LLM、零外部网络；测试数据用 `test-hitl-` 前缀隔离，每个用例开始前清理。
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime

import psycopg
import pytest
from fastapi.testclient import TestClient

from web.backend.store import LeaseLostError, RunOwnership, RunStore

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")

TEST_USER = "test-hitl-user"

TTL_SECONDS = 3600


@pytest.fixture()
def store() -> RunStore:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE user_id LIKE 'test-hitl-%'")
        cur.execute("DELETE FROM users WHERE user_id LIKE 'test-hitl-%'")
        cur.execute(
            "INSERT INTO users (user_id, email, password_hash) VALUES (%s, %s, 'x')",
            (TEST_USER, "test-hitl@test-hitl.local"),
        )
    instance = RunStore(DSN)
    yield instance
    instance.close()


def _running(store: RunStore, *, worker_id: str = "worker-hitl", attempt: int = 1):
    """建一个已被 worker 领取（RUNNING + 租约）的任务。"""
    run_id = uuid.uuid4().hex[:12]
    row, created = store.create_run(
        run_id, "HITL 测试主题", {"instructions": "x"},
        user_id=TEST_USER, status="QUEUED")
    assert created is True
    claimed = store.claim_run(run_id, worker_id, 120)
    assert claimed is not None
    return run_id, RunOwnership(worker_id, attempt)


def _suspend(store: RunStore, run_id: str, owner: RunOwnership, *,
             kind: str = "clarification", interaction_id: str = "itx-t1",
             round_no: int = 1, ttl_seconds: int = TTL_SECONDS):
    return store.suspend_run(
        run_id, owner=owner, kind=kind, interaction_id=interaction_id,
        round_no=round_no, content={"questions": ["范围？"]}, ttl_seconds=ttl_seconds)


def _force_expired(run_id: str) -> None:
    """把等待态到期时间拨到过去（模拟 TTL 到点；不改其它列）。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute(
            "UPDATE runs SET awaiting_expires_at = now() - interval '1 second' "
            "WHERE run_id = %s", (run_id,))


def _answer(store: RunStore, run_id: str, *, interaction_id: str = "itx-t1",
            event_type: str = "CLARIFY_ANSWERED", action: str = "reply",
            text: str = "范围=近三个月"):
    return store.answer_interaction(
        run_id,
        interaction_id=interaction_id,
        answer={"kind": "clarification", "action": action, "text": text, "auto": False},
        event_type=event_type,
        event_payload={"interaction_id": interaction_id, "action": action, "text": text},
    )


# ---- 挂起事务 ---------------------------------------------------------------

def test_suspend_atomic_releases_lease_and_writes_card(store: RunStore):
    run_id, owner = _running(store)
    updated = _suspend(store, run_id, owner)

    assert updated["status"] == "AWAITING_CLARIFICATION"
    assert updated["worker_id"] is None
    assert updated["worker_status"] is None
    assert updated["lease_expires_at"] is None
    assert updated["interaction_id"] == "itx-t1"
    assert updated["interaction_round"] == 1
    assert updated["awaiting_since"] is not None
    assert updated["awaiting_expires_at"] is not None
    assert updated["interaction_answer"] is None
    assert updated["interaction_payload"]["kind"] == "clarification"
    assert updated["interaction_payload"]["allowed_actions"] == ["reply"]

    events = store.get_events(run_id)
    assert events[-1]["event_type"] == "CLARIFY_REQUESTED"
    assert events[-1]["payload"]["interaction_id"] == "itx-t1"

    # 计数口径：等待态不占并发（count_active），计入 count_awaiting
    assert store.count_active(TEST_USER) == 0
    assert store.count_awaiting(TEST_USER) == 1


def test_suspend_rejects_wrong_owner_or_attempt(store: RunStore):
    run_id, owner = _running(store)
    with pytest.raises(LeaseLostError):
        _suspend(store, run_id, RunOwnership("other-worker", 1))
    with pytest.raises(LeaseLostError):
        _suspend(store, run_id, RunOwnership(owner.worker_id, owner.attempt + 1))
    row = store.get_run(run_id)
    assert row["status"] == "RUNNING"  # 两次拒绝都不得改状态
    assert len(store.get_events(run_id)) == 0


def test_suspend_on_non_executing_status_returns_none(store: RunStore):
    run_id, owner = _running(store)
    assert store.update_status(
        run_id, "SUCCEEDED", allowed_from=("RUNNING",),
        stop_reason="completed", finished_at=datetime.now(UTC)) is True
    assert _suspend(store, run_id, owner) is None
    assert store.get_run(run_id)["status"] == "SUCCEEDED"


def test_suspend_refuses_when_cancel_requested(store: RunStore):
    """用户已请求停止：绝不挂起（不写卡片、不改状态），执行侧照常走取消收口。"""
    run_id, owner = _running(store)
    assert store.request_cancel(run_id) == "CANCEL_REQUESTED"
    assert _suspend(store, run_id, owner) is None
    assert store.get_run(run_id)["status"] == "CANCEL_REQUESTED"
    assert store.get_events(run_id) == []


# ---- 回答事务 ---------------------------------------------------------------

def test_answer_roundtrip_then_idempotent_repeat(store: RunStore):
    run_id, owner = _running(store)
    _suspend(store, run_id, owner, kind="plan_approval", interaction_id="itx-p1")

    result = _answer(store, run_id, interaction_id="itx-p1",
                     event_type="PLAN_DECISION", action="approve", text="")
    assert result["outcome"] == "accepted"

    row = store.get_run(run_id)
    assert row["status"] == "QUEUED"
    assert row["resumed_at"] is not None
    assert row["awaiting_expires_at"] is None
    assert row["interaction_answer"]["action"] == "approve"
    events = store.get_events(run_id)
    assert events[-1]["event_type"] == "PLAN_DECISION"

    # 重复提交（同 interaction_id）⇒ already_answered，不重复写事件
    events_before = len(store.get_events(run_id))
    again = _answer(store, run_id, interaction_id="itx-p1",
                    event_type="PLAN_DECISION", action="approve", text="")
    assert again["outcome"] == "already_answered"
    assert len(store.get_events(run_id)) == events_before


def test_answer_stale_card_conflict_then_correct_card_succeeds(store: RunStore):
    run_id, owner = _running(store)
    _suspend(store, run_id, owner, interaction_id="itx-a")

    stale = _answer(store, run_id, interaction_id="itx-b")
    assert stale["outcome"] == "conflict"
    assert stale["reason"] == "stale_interaction"
    row = store.get_run(run_id)
    assert row["status"] == "AWAITING_CLARIFICATION"  # 冲突不改变状态
    assert len(store.get_events(run_id)) == 1  # 只保留 CLARIFY_REQUESTED

    good = _answer(store, run_id, interaction_id="itx-a")
    assert good["outcome"] == "accepted"


def test_answer_when_not_pending_conflict(store: RunStore):
    run_id, _owner = _running(store)
    result = _answer(store, run_id, interaction_id="itx-none")
    assert result["outcome"] == "conflict"
    assert result["reason"] == "not_pending"
    assert store.get_run(run_id)["status"] == "RUNNING"


# ---- 取消 / 清扫 / 到期 ------------------------------------------------------

def test_cancel_from_awaiting_is_direct_and_idempotent(store: RunStore):
    run_id, owner = _running(store)
    _suspend(store, run_id, owner)

    assert store.request_cancel(run_id) == "CANCELLED"
    row = store.get_run(run_id)
    assert row["status"] == "CANCELLED"
    assert row["stop_reason"] == "user_cancelled"
    assert row["finished_at"] is not None
    assert store.request_cancel(run_id) == "CANCELLED"  # 幂等


def test_sweep_ignores_awaiting_and_clarification_expiry_auto_continues(store: RunStore):
    run_id, owner = _running(store)
    _suspend(store, run_id, owner, interaction_id="itx-exp1")
    _force_expired(run_id)

    # 租约清扫只扫执行态：等待态**不得**被判 LOST / requeued
    swept = store.sweep_stale_runs()
    assert all(item["run_id"] != run_id for item in swept)
    assert store.get_run(run_id)["status"] == "AWAITING_CLARIFICATION"

    # 到期收口：澄清超时 = 空回答自动继续（显式产品策略，留痕）
    processed = store.expire_awaiting_runs()
    assert {"run_id": run_id, "action": "clarified_auto"} in processed
    row = store.get_run(run_id)
    assert row["status"] == "QUEUED"
    assert row["resumed_at"] is not None
    assert row["interaction_answer"]["auto"] is True
    assert row["interaction_answer"]["reason"] == "clarification_expired"
    assert store.get_events(run_id)[-1]["event_type"] == "CLARIFY_EXPIRED"
    assert store.count_awaiting(TEST_USER) == 0


def test_plan_approval_expiry_is_timed_out_not_auto_approved(store: RunStore):
    run_id, owner = _running(store)
    _suspend(store, run_id, owner, kind="plan_approval", interaction_id="itx-exp2")
    _force_expired(run_id)

    processed = store.expire_awaiting_runs()
    assert {"run_id": run_id, "action": "approval_expired"} in processed
    row = store.get_run(run_id)
    assert row["status"] == "TIMED_OUT"
    assert row["stop_reason"] == "approval_expired"  # 不伪装成 worker 执行超时
    assert row["finished_at"] is not None
    assert row["interaction_answer"] is None
    assert store.get_events(run_id)[-1]["event_type"] == "PLAN_EXPIRED"

    # 已终局：再次收口不得改动
    assert store.expire_awaiting_runs() == []


def test_expire_skips_non_expired_awaiting(store: RunStore):
    run_id, owner = _running(store)
    _suspend(store, run_id, owner)
    assert store.expire_awaiting_runs() == []
    assert store.get_run(run_id)["status"] == "AWAITING_CLARIFICATION"


def test_mark_stale_as_lost_leaves_awaiting(store: RunStore):
    run_id, owner = _running(store)
    _suspend(store, run_id, owner)
    store.mark_stale_as_lost()
    assert store.get_run(run_id)["status"] == "AWAITING_CLARIFICATION"


def test_pending_run_not_claimed_as_queued(store: RunStore):
    """等待态不是 QUEUED：claim_next_queued 不得领取（等待的是人，不是 worker）。"""
    run_id, owner = _running(store)
    _suspend(store, run_id, owner)
    claimed = store.claim_next_queued("worker-hitl-2", 120)
    assert claimed is None or claimed["run_id"] != run_id


# ---- API 回答端点（真 PG） ---------------------------------------------------

def test_answer_endpoint_via_api(store: RunStore, monkeypatch):
    from fakes import FakeQueue

    from web.backend import main as api

    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "queue", FakeQueue())

    run_id, owner = _running(store)
    _suspend(store, run_id, owner, interaction_id="itx-api1")

    client = TestClient(api.app)
    ok = client.post(f"/api/research/{run_id}/interaction", json={
        "interaction_id": "itx-api1", "action": "reply", "text": "范围=近三个月"})
    assert ok.status_code == 200, ok.text
    body = ok.json()
    assert body["outcome"] == "accepted" and body["kind"] == "clarification"
    assert store.get_run(run_id)["status"] == "QUEUED"
    assert api.queue.items == [run_id]  # Redis 唤醒信号（可丢，PG 轮询兜底）

    # 非等待态再答 ⇒ 409 interaction_not_pending
    again = client.post(f"/api/research/{run_id}/interaction", json={
        "interaction_id": "itx-api1", "action": "reply", "text": "再来一次"})
    assert again.status_code == 409
    assert again.json()["detail"]["code"] in ("interaction_not_pending",
                                              "interaction_conflict")

    # 再挂起（worker 重新领取）后用错 action（审批卡收到 reply）⇒ 422 invalid_request
    assert store.claim_run(run_id, owner.worker_id, 120) is not None
    _suspend(store, run_id, owner, kind="plan_approval", interaction_id="itx-api2")
    wrong = client.post(f"/api/research/{run_id}/interaction", json={
        "interaction_id": "itx-api2", "action": "reply", "text": "x"})
    assert wrong.status_code == 422
    assert wrong.json()["detail"]["code"] == "invalid_request"

    # 旧卡片 ⇒ 409 interaction_conflict
    stale = client.post(f"/api/research/{run_id}/interaction", json={
        "interaction_id": "itx-api1", "action": "approve", "text": ""})
    assert stale.status_code == 409
    assert stale.json()["detail"]["code"] == "interaction_conflict"
