"""PG checkpoint（需求 35 R3 / TBD-5 拍板，2026-10-12）。

官方 ``langgraph-checkpoint-postgres``（``thread_id=run_id``）：
- **进程级单例**（按 DSN 缓存；Worker 单进程单 saver）；
- ``setup()`` 幂等建表（``checkpoints`` / ``checkpoint_blobs`` / ``checkpoint_writes`` 等——
  **库自管 schema**（自带 ``checkpoint_migrations`` 版本表），不入本项目 ``migrations/``；
  兼容策略：升级库版本时按其迁移机制前向兼容）；
- 未配置 DSN / 依赖不可用 / 连接失败 ⇒ **None**（调用方回落 MemorySaver；执行模式退化为
  ``fresh`` / ``replayed``，不阻塞主链路——fail-silent 口径）。
"""
from __future__ import annotations

import atexit
import logging
import threading
from typing import Any, Optional

_log = logging.getLogger("deepresearch.checkpoint")

_lock = threading.Lock()
_cached: Any = None
_cached_dsn: Optional[str] = None
_tried = False


def _close_pool(pool: Any) -> None:
    try:
        pool.close()
    except Exception:  # noqa: BLE001 —— 退出清理失败不影响进程退出
        pass


def get_pg_checkpointer(dsn: Optional[str] = None) -> Any:
    """返回进程级 PostgresSaver（不可用时 None）；幂等、fail-silent。"""
    global _cached, _cached_dsn, _tried
    dsn = (dsn or "").strip()
    if not dsn:
        return None
    with _lock:
        if _tried and _cached_dsn == dsn:
            return _cached
        _tried = True
        _cached_dsn = dsn
        _cached = None
        try:
            from langgraph.checkpoint.postgres import PostgresSaver
            from psycopg_pool import ConnectionPool

            pool = ConnectionPool(
                conninfo=dsn, min_size=1, max_size=4, open=True,
                kwargs={"autocommit": True},
            )
            saver = PostgresSaver(pool)
            saver.setup()  # 幂等（库自带迁移表）；首启建表
            _cached = saver
            atexit.register(_close_pool, pool)  # 进程退出优雅关池（避免线程等待告警）
            _log.info("PG checkpoint 已启用（langgraph-checkpoint-postgres）")
        except Exception as exc:  # noqa: BLE001 —— fail-silent：恢复能力缺失不阻塞执行
            _log.warning("PG checkpoint 初始化失败（回落 MemorySaver / 重跑语义）：%s", exc)
            _cached = None
        return _cached
