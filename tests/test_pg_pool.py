"""P2-3 连接池 / statement_timeout / 慢查询日志测试。

配置解析与 SQL 摘要为纯函数（默认运行）；连接复用、statement_timeout 生效与
慢查询告警需要真实 PostgreSQL（CI `infra` job）。
"""
from __future__ import annotations

import logging
import os

import pytest

from web.backend.store import RunStore, _sql_snippet, pool_settings

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()


def test_pool_settings_defaults_and_overrides(monkeypatch):
    for name in ("DR_PG_POOL_MIN", "DR_PG_POOL_MAX", "DR_PG_POOL_TIMEOUT_S",
                 "DR_PG_STATEMENT_TIMEOUT_MS", "DR_PG_SLOW_QUERY_MS", "DR_PG_APP_NAME"):
        monkeypatch.delenv(name, raising=False)
    defaults = pool_settings()
    assert defaults["min_size"] == 1 and defaults["max_size"] == 8
    assert defaults["statement_timeout_ms"] == 15000 and defaults["slow_ms"] == 500
    assert defaults["application_name"] == "deepresearch"

    monkeypatch.setenv("DR_PG_POOL_MAX", "3")
    monkeypatch.setenv("DR_PG_POOL_MIN", "99")
    monkeypatch.setenv("DR_PG_STATEMENT_TIMEOUT_MS", "2000")
    monkeypatch.setenv("DR_PG_SLOW_QUERY_MS", "invalid")
    monkeypatch.setenv("DR_PG_APP_NAME", "dr-api")
    updated = pool_settings()
    assert updated["max_size"] == 3
    assert updated["min_size"] == 3  # min 不得超过 max（建池前收敛）
    assert updated["statement_timeout_ms"] == 2000
    assert updated["slow_ms"] == 500  # 非法值回落默认
    assert updated["application_name"] == "dr-api"


def test_sql_snippet_compresses_and_truncates_params_never_included():
    assert _sql_snippet("SELECT\n  *\nFROM runs\nWHERE run_id = %s") == \
        "SELECT * FROM runs WHERE run_id = %s"
    long = _sql_snippet("SELECT " + "x" * 500)
    assert len(long) == 200 and long.endswith("...")


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_pool_reuses_connection_and_sets_statement_timeout():
    store = RunStore(DSN)
    try:
        pids = set()
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("SHOW statement_timeout")
            assert cur.fetchone()["statement_timeout"] == "15s"
        for _ in range(4):
            with store._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_backend_pid() AS pid")
                pids.add(cur.fetchone()["pid"])
        # 池复用：4 次取用只落在 1~2 个后端连接（min_size 预热与首个请求允许各建一条）
        stats = store._get_pool().get_stats()
        assert len(pids) <= 2
        assert stats["requests_num"] >= 5
        assert stats["connections_num"] <= 2
    finally:
        store.close()


@pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")
def test_slow_query_logged_without_params(monkeypatch, caplog):
    monkeypatch.setenv("DR_PG_SLOW_QUERY_MS", "1")
    store = RunStore(DSN)
    try:
        with caplog.at_level(logging.WARNING, logger="deepresearch.pg"):
            with store._connect() as conn, conn.cursor() as cur:
                cur.execute("SELECT pg_sleep(%s)", (0.05,))
        assert "slow query" in caplog.text
        assert "pg_sleep" in caplog.text
        assert "0.05" not in caplog.text  # 只记 SQL 模板，参数（可能含 PII）不入日志
    finally:
        store.close()
