"""PG 快照存储（需求 34 E1 / TBD-1 拍板：内容寻址两表，2026-10-11）。

- ``snapshots``：全局内容寻址（sha256 主键）存一份；同文（同提取器版本）跨 run 去重；
- ``run_snapshots``：run ↔ 快照 关联（run 内去重）；删除 run 级联解除（迁移 0022）；
- 文本上限由应用层控制（``text_truncated`` 标记）；本体清理靠保留期任务（无引用 + 超期）。
"""
from __future__ import annotations

from research_engine.snapshots import snapshot_id_for
from web.backend.store import RunStore


class PgSnapshotStore:
    """基于任务库连接池的快照存储（线程安全：每次写入取一条池连接）。"""

    def __init__(self, store: RunStore) -> None:
        self._store = store

    def save_snapshot(self, *, run_id: str, url: str, content_type: str,
                      source_bytes: int, text: str, text_truncated: bool,
                      extractor_version: str) -> str:
        """落盘 + 关联 run；返回内容寻址 id（幂等：同 id 不重复写）。"""
        sha = snapshot_id_for(text, extractor_version)
        with self._store._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO snapshots (sha256, url, content_type, source_bytes, text,
                                       text_truncated, extractor_version)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (sha256) DO NOTHING
                """,
                (sha, url, content_type or "", max(0, int(source_bytes)), text,
                 bool(text_truncated), extractor_version),
            )
            cur.execute(
                """
                INSERT INTO run_snapshots (run_id, sha256) VALUES (%s, %s)
                ON CONFLICT DO NOTHING
                """,
                (run_id, sha),
            )
        return sha
