"""需求 38 F2：export_jobs PG 契约（认领 SKIP LOCKED / 租约回收 / 幂等 / 结果 TTL）。"""
from __future__ import annotations

import os
import uuid

import psycopg
import pytest

from web.backend.store import RunStore

DSN = os.environ.get("DR_TEST_DATABASE_URL")
pytestmark = pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")


@pytest.fixture
def store() -> RunStore:
    return RunStore(DSN)


def _run_id() -> str:
    rid = f"pgjob-{uuid.uuid4().hex[:10]}"
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO runs (run_id, topic, status) VALUES (%s, 't', 'SUCCEEDED')",
                    (rid,))
    return rid


def _cleanup(*run_ids: str) -> None:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id = ANY(%s)", (list(run_ids),))


def _clear_active_jobs() -> None:
    """清空残留的 QUEUED/RUNNING 作业（认领是全局扫描，避免历史残留干扰断言）。"""
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM export_jobs WHERE status IN ('QUEUED', 'RUNNING')")


def _expire_lease(job_id: str) -> None:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("UPDATE export_jobs SET lease_expires_at = now() - interval '1 second' "
                    "WHERE job_id = %s", (job_id,))


def test_claim_and_reclaim_and_exhaustion(store: RunStore):
    rid = _run_id()
    _clear_active_jobs()
    try:
        job = store.create_export_job("j-claim-1", run_id=rid, user_id="u1",
                                      request_hash="h-claim-1")
        assert job["status"] == "QUEUED"

        claimed = store.claim_export_job("owner-A", lease_seconds=60)
        assert claimed is not None and claimed["job_id"] == "j-claim-1"
        assert claimed["status"] == "RUNNING" and claimed["attempt"] == 1
        assert claimed["lease_owner"] == "owner-A"
        assert store.claim_export_job("owner-A") is None  # 无第二个 QUEUED

        # 租约过期 ⇒ 回收重排（attempt 1 < 2）
        _expire_lease("j-claim-1")
        assert store.reclaim_expired_export_leases() >= 1
        row = store.get_export_job("j-claim-1")
        assert row["status"] == "QUEUED" and row["lease_owner"] is None

        # 第二次认领后再次过期 ⇒ 重试耗尽 ⇒ FAILED
        claimed2 = store.claim_export_job("owner-B")
        assert claimed2 is not None and claimed2["attempt"] == 2
        _expire_lease("j-claim-1")
        store.reclaim_expired_export_leases()
        row2 = store.get_export_job("j-claim-1")
        assert row2["status"] == "FAILED"
        assert row2["error_code"] == "export_lease_expired"
    finally:
        _cleanup(rid)


def test_dedup_unique_active_and_reuse(store: RunStore):
    rid = _run_id()
    _clear_active_jobs()
    try:
        store.create_export_job("j-dedup-1", run_id=rid, user_id="u1",
                                request_hash="h-dedup")
        with pytest.raises(psycopg.errors.UniqueViolation):
            store.create_export_job("j-dedup-2", run_id=rid, user_id="u1",
                                    request_hash="h-dedup")
        reusable = store.find_reusable_export_job("h-dedup")
        assert reusable is not None and reusable["job_id"] == "j-dedup-1"

        # 完成后（结果未过期）仍可复用；失败后不再复用
        store.claim_export_job("owner-A")
        from datetime import UTC, datetime, timedelta
        assert store.finish_export_job_success(
            "j-dedup-1", "owner-A", result_key="k", result_bytes=10,
            result_hash="sha", result_expires_at=datetime.now(UTC) + timedelta(minutes=15))
        assert store.find_reusable_export_job("h-dedup")["status"] == "SUCCEEDED"

        # 结果过期 ⇒ EXPIRED ⇒ 不再复用；可再建同指纹
        with psycopg.connect(DSN) as conn, conn.cursor() as cur:
            cur.execute("UPDATE export_jobs SET result_expires_at = now() - interval '1 second' "
                        "WHERE job_id = 'j-dedup-1'")
        assert store.expire_export_jobs() >= 1
        assert store.get_export_job("j-dedup-1")["status"] == "EXPIRED"
        assert store.find_reusable_export_job("h-dedup") is None
        store.create_export_job("j-dedup-3", run_id=rid, user_id="u1", request_hash="h-dedup")
    finally:
        _cleanup(rid)


def test_requeue_and_failure_paths(store: RunStore):
    rid = _run_id()
    _clear_active_jobs()
    try:
        store.create_export_job("j-rq-1", run_id=rid, user_id="u1", request_hash="h-rq")
        store.claim_export_job("owner-A")
        # attempt 1 < 2 ⇒ 回 QUEUED
        assert store.requeue_export_job("j-rq-1", "owner-A", error_code="export_render_timeout") \
            == "QUEUED"
        store.claim_export_job("owner-A")
        # attempt 2 == max ⇒ FAILED
        assert store.requeue_export_job("j-rq-1", "owner-A",
                                        error_code="export_render_failed") == "FAILED"
        row = store.get_export_job("j-rq-1")
        assert row["error_code"] == "export_render_failed" and row["finished_at"] is not None

        # 确定性失败（直接 FAILED）
        store.create_export_job("j-rq-2", run_id=rid, user_id="u1", request_hash="h-rq-2")
        store.claim_export_job("owner-A")
        assert store.finish_export_job_failure("j-rq-2", "owner-A",
                                               error_code="export_too_many_pages")
        assert store.get_export_job("j-rq-2")["status"] == "FAILED"

        # 租约续期：非持有者不能续
        store.create_export_job("j-rq-3", run_id=rid, user_id="u1", request_hash="h-rq-3")
        store.claim_export_job("owner-A")
        assert store.renew_export_job_lease("j-rq-3", "owner-A") is True
        assert store.renew_export_job_lease("j-rq-3", "owner-B") is False
    finally:
        _cleanup(rid)
