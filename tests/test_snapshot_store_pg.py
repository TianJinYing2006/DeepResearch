"""需求 34 E1：PgSnapshotStore 真 PostgreSQL 契约（CI infra job；本地无 DSN 自动跳过）。

覆盖：内容寻址幂等（同 sha 去重）/ run 关联去重 / 删除 run 级联解除关联且快照本体保留。
"""
from __future__ import annotations

import os

import pytest

from research_engine.net.webtext import EXTRACTOR_VERSION
from research_engine.snapshots import snapshot_id_for

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_snapshot_store_roundtrip_dedup_and_cascade():
    from web.backend.snapshots import PgSnapshotStore
    from web.backend.store import RunStore

    store = RunStore(DSN)
    snapshots = PgSnapshotStore(store)
    run_id = "__snap_contract_run__"
    text = "契约测试正文 hello"
    sha = snapshot_id_for(text, EXTRACTOR_VERSION)
    try:
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO runs (run_id, topic) VALUES (%s, %s) ON CONFLICT DO NOTHING",
                (run_id, "snapshot contract"),
            )

        got = snapshots.save_snapshot(
            run_id=run_id, url="https://example.com/x", content_type="text/html",
            source_bytes=42, text=text, text_truncated=False,
            extractor_version=EXTRACTOR_VERSION)
        assert got == sha

        # 幂等：同内容二次写入不重复（snapshots 与 run_snapshots 均去重）
        again = snapshots.save_snapshot(
            run_id=run_id, url="https://example.com/x", content_type="text/html",
            source_bytes=42, text=text, text_truncated=False,
            extractor_version=EXTRACTOR_VERSION)
        assert again == sha

        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM snapshots WHERE sha256 = %s", (sha,))
            assert cur.fetchone()["n"] == 1
            cur.execute(
                "SELECT count(*) AS n FROM run_snapshots WHERE run_id = %s AND sha256 = %s",
                (run_id, sha))
            assert cur.fetchone()["n"] == 1
            cur.execute(
                "SELECT text, extractor_version, text_truncated FROM snapshots WHERE sha256 = %s",
                (sha,))
            row = cur.fetchone()
            assert row["text"] == text
            assert row["extractor_version"] == EXTRACTOR_VERSION
            assert row["text_truncated"] is False

        # 删除 run：关联级联解除，快照本体保留（TBD-1 口径）
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
            cur.execute("SELECT count(*) AS n FROM run_snapshots WHERE run_id = %s", (run_id,))
            assert cur.fetchone()["n"] == 0
            cur.execute("SELECT count(*) AS n FROM snapshots WHERE sha256 = %s", (sha,))
            assert cur.fetchone()["n"] == 1
            cur.execute("DELETE FROM snapshots WHERE sha256 = %s", (sha,))
    finally:
        store.close()
