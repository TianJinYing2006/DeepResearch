"""P2-5b 申诉/复核状态机测试（状态机 / 决策语义 / SLA 告警 / API / PG 契约）。"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.appeals import review_appeal, sla_due_at, submit_appeal

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()


def _client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "INVITE_ONLY", False)
    return TestClient(api.app)


def test_state_machine_claim_then_accept_clears_run():
    store = FakeStore()
    store.create_run("appealrun001", "t", {})
    store.set_moderation_status("appealrun001", "flagged")

    row = submit_appeal(store, user_id="u1", message="我觉得没问题",
                        run_id="appealrun001")
    assert row["status"] == "pending" and row["sla_due_at"] is not None
    record = store.list_moderation(kind="appeal")[0]
    assert record["detail"]["appeal_id"] == row["appeal_id"]

    claimed = store.claim_appeal(row["appeal_id"], reviewer="admin")
    assert claimed["status"] == "reviewing"
    assert store.claim_appeal(row["appeal_id"], reviewer="admin2") is None  # 已被领取

    result = review_appeal(store, row["appeal_id"], decision="accepted",
                           note="人工确认无问题", reviewer="admin")
    assert result["ok"] is True and result["run_cleared"] is True
    assert store.get_appeal(row["appeal_id"])["status"] == "accepted"
    assert store.get_run("appealrun001")["moderation_status"] == "cleared"
    assert store.list_audit(action="appeal_accepted")

    again = review_appeal(store, row["appeal_id"], decision="rejected")
    assert again["ok"] is False and again["reason"] == "already_accepted"


def test_reject_keeps_run_flagged():
    store = FakeStore()
    store.create_run("appealrun002", "t", {})
    store.set_moderation_status("appealrun002", "flagged")
    row = submit_appeal(store, user_id="u1", message="m", run_id="appealrun002")

    result = review_appeal(store, row["appeal_id"], decision="rejected",
                           note="维持原判", reviewer="admin")

    assert result["ok"] is True and result["run_cleared"] is False
    assert store.get_run("appealrun002")["moderation_status"] == "flagged"
    assert store.list_audit(action="appeal_rejected")


def test_review_missing_appeal_reports_reason():
    store = FakeStore()
    result = review_appeal(store, "no-such-appeal", decision="accepted")
    assert result["ok"] is False and result["reason"] == "not_found"


def test_sla_overdue_count():
    store = FakeStore()
    submit_appeal(store, user_id="u1", message="m", run_id="fresh",
                  now=datetime.now(UTC))
    overdue_id = submit_appeal(
        store, user_id="u2", message="m", run_id="old",
        now=datetime.now(UTC) - timedelta(hours=100),
    )["appeal_id"]

    assert store.count_appeals_overdue(datetime.now(UTC)) == 1
    assert store.claim_appeal(overdue_id, reviewer=None)["status"] == "reviewing"
    store.decide_appeal(overdue_id, decision="rejected", note="x")
    assert store.count_appeals_overdue(datetime.now(UTC)) == 0
    assert (sla_due_at(datetime(2026, 1, 1, tzinfo=UTC))
            == datetime(2026, 1, 4, tzinfo=UTC))  # 默认 72h


def test_api_creates_appeal_and_lists_status(monkeypatch):
    store = FakeStore()
    store.create_run("apiflagrun01", "t", {})
    store.set_moderation_status("apiflagrun01", "flagged")
    client = _client(monkeypatch, store)

    response = client.post("/api/moderation/appeal",
                           json={"message": "误判申诉", "run_id": "apiflagrun01"})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "pending" and body["appeal_id"]

    listing = client.get("/api/moderation/appeals").json()
    assert listing["appeals"][0]["appeal_id"] == body["appeal_id"]
    assert listing["appeals"][0]["status"] == "pending"
    assert listing["appeals"][0]["run_id"] == "apiflagrun01"


def test_ops_alerts_include_overdue_appeals(monkeypatch):
    store = FakeStore()
    client = _client(monkeypatch, store)
    submit_appeal(store, user_id=None, message="m", run_id=None,
                  now=datetime.now(UTC) - timedelta(hours=100))

    body = client.get("/api/ops/alerts").json()

    assert "appeal_sla_overdue" in {alert["code"] for alert in body["alerts"]}


def test_decide_requires_claim_and_owner_reviewer():
    """P0-5：未领取不可决策；他人领取后不能越权决策；领取者决策原子清 run。"""
    store = FakeStore()
    store.create_run("appealrun003", "t", {})
    store.set_moderation_status("appealrun003", "flagged")
    row = submit_appeal(store, user_id="u1", message="m", run_id="appealrun003")

    assert store.decide_appeal(row["appeal_id"], decision="accepted",
                               reviewer="admin") is None
    assert store.claim_appeal(row["appeal_id"], reviewer="admin-a") is not None
    assert store.decide_appeal(row["appeal_id"], decision="accepted",
                               reviewer="admin-b") is None
    assert store.get_appeal(row["appeal_id"])["status"] == "reviewing"
    assert store.get_run("appealrun003")["moderation_status"] == "flagged"

    decided = store.decide_appeal(row["appeal_id"], decision="accepted",
                                  reviewer="admin-a")
    assert decided["status"] == "accepted"
    assert store.get_run("appealrun003")["moderation_status"] == "cleared"
    assert store.list_moderation(kind="appeal_accepted")
    assert store.list_audit(action="appeal_accepted")


def test_review_appeal_other_reviewer_gets_claimed_by_other():
    """P0-5：已由他人领取的申诉，review_appeal 不得继续决策。"""
    store = FakeStore()
    store.create_run("appealrun004", "t", {})
    store.set_moderation_status("appealrun004", "flagged")
    row = submit_appeal(store, user_id="u1", message="m", run_id="appealrun004")
    store.claim_appeal(row["appeal_id"], reviewer="admin-a")

    result = review_appeal(store, row["appeal_id"], decision="accepted",
                           reviewer="admin-b")

    assert result["ok"] is False and result["reason"] == "claimed_by_other"
    assert store.get_run("appealrun004")["moderation_status"] == "flagged"


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_appeals_pg_accepted_clears_run_and_unblocks_export(monkeypatch):
    """P0-1 回归：真实 PG 上 flagged → 申诉 accepted → cleared 导出放行。

    旧 0004 约束只允许 flagged/blocked，本用例会在 `decide_appeal` 写 cleared 时
    触发 CHECK 违例 —— 0015 修复后必须全绿（含证据与审计同事务）。
    """
    import psycopg
    from fastapi import HTTPException

    from web.backend.store import RunStore

    store = RunStore(DSN)
    run_id = f"appealpg{uuid.uuid4().hex[:6]}"
    appeal_id = uuid.uuid4().hex[:12]
    try:
        store.create_run(run_id, "appeal-pg-export", {}, user_id=None)
        assert store.set_moderation_status(run_id, "flagged") is True
        monkeypatch.setattr(api, "store", store)
        with pytest.raises(HTTPException):
            api._enforce_output_policy(run_id)  # flagged ⇒ 导出阻断

        store.create_appeal(appeal_id, message="m", run_id=run_id, user_id=None,
                            sla_due_at=datetime.now(UTC) + timedelta(hours=1))
        assert store.claim_appeal(appeal_id, reviewer="pg-admin")["status"] == "reviewing"
        decided = store.decide_appeal(appeal_id, decision="accepted", note="ok",
                                      reviewer="pg-admin")
        assert decided["status"] == "accepted"
        assert store.get_run(run_id)["moderation_status"] == "cleared"
        api._enforce_output_policy(run_id)  # 不再抛：导出闸放行

        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) FROM moderation_records "
                "WHERE run_id = %s AND kind = 'appeal_accepted'", (run_id,))
            assert cur.fetchone()[0] == 1
            cur.execute(
                "SELECT count(*) FROM audit_logs "
                "WHERE action = 'appeal_accepted' AND target_id = %s", (appeal_id,))
            assert cur.fetchone()[0] == 1
    finally:
        store.close()
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM moderation_appeals WHERE appeal_id = %s",
                        (appeal_id,))
            cur.execute("DELETE FROM moderation_records WHERE run_id = %s", (run_id,))
            cur.execute("DELETE FROM audit_logs WHERE target_id = %s", (appeal_id,))
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_appeals_pg_roundtrip():
    import psycopg

    from web.backend.store import RunStore

    store = RunStore(DSN)
    run_id = f"appealpg{uuid.uuid4().hex[:6]}"
    appeal_id = uuid.uuid4().hex[:12]
    try:
        store.create_run(run_id, "appeal-pg", {}, user_id=None)
        row = store.create_appeal(appeal_id, message="m", run_id=run_id,
                                  user_id=None,
                                  sla_due_at=datetime.now(UTC) - timedelta(hours=1))
        assert row["status"] == "pending"
        assert store.count_appeals_overdue(datetime.now(UTC)) >= 1

        claimed = store.claim_appeal(appeal_id, reviewer="pg-admin")
        assert claimed["status"] == "reviewing"
        assert store.claim_appeal(appeal_id, reviewer="pg-admin") is None

        decided = store.decide_appeal(appeal_id, decision="accepted",
                                      note="ok", reviewer="pg-admin")
        assert decided["status"] == "accepted"
        assert store.decide_appeal(appeal_id, decision="rejected") is None
        assert store.count_appeals_overdue(datetime.now(UTC)) == 0
    finally:
        store.close()
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM moderation_appeals WHERE appeal_id = %s",
                        (appeal_id,))
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
