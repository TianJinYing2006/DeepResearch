"""L3 任务持久化仓储层（P2-B）。

只负责 `runs` / `run_events` / `run_artifacts` 三张表的读写，不做业务编排：

- 状态机合法性由 SQL CHECK + `update_status(allowed_from=...)` 乐观迁移双重把关；
- 创建幂等由部分唯一索引 `(user_id, idempotency_key)` 兜底（并发安全）；
- 事件序号在数据库内分配（`MAX(sequence)+1`，主键 `(run_id, sequence)` 防重放）。

口径见 `docs/requirements/10-l3-production.md` §5.9 与 `migrations/0001_runs_and_events.sql`。

连接策略：每次调用开一个短连接（psycopg 3 的 `connect()` 上下文负责提交/回滚）。
L3-A 规模（≤5 用户、2 并发）足够；连接池留到 P3 与 Worker 一起定。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from .persistence import ACTIVE_STATUSES

#: 终局状态（不得再迁移）；活跃状态见 `persistence.ACTIVE_STATUSES`（单一来源，此处再导出）
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST")

#: `update_status` 允许写的列白名单（防注入与误写主键/记账列）
_UPDATABLE_FIELDS = frozenset({
    "research_status", "stop_reason", "current_node", "token_used", "cost_estimate_cny",
    "budget_used_cny", "attempt", "retry_of", "worker_id", "worker_status",
    "lease_expires_at", "queued_at", "started_at", "finished_at", "cancel_requested_at",
    "timeout_at", "hard_deadline_at",
})


def _now() -> datetime:
    return datetime.now(UTC)


class RunStore:
    """runs / run_events / run_artifacts 的最小仓储实现（同步）。"""

    def __init__(self, dsn: str, *, connect_timeout: int = 3):
        self._dsn = dsn
        self._connect_timeout = connect_timeout

    def _connect(self) -> psycopg.Connection:
        return psycopg.connect(
            self._dsn, row_factory=dict_row, connect_timeout=self._connect_timeout
        )

    def ping(self) -> None:
        """连接可用性探针（readiness 升级用；失败直接抛异常）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT 1")
            cur.fetchone()

    # ---- runs ----

    def create_run(
        self,
        run_id: str,
        topic: str,
        request: Optional[dict[str, Any]] = None,
        *,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        status: str = "CREATED",
        timeout_at: Optional[datetime] = None,
        budget_limit_cny: Optional[float] = None,
    ) -> tuple[dict[str, Any], bool]:
        """插入新 run；带幂等键且已存在时返回既有行（`created=False`）。

        并发竞态由部分唯一索引兜底：`UniqueViolation` 时回查既有行。
        """
        if idempotency_key is not None:
            existing = self.get_run_by_idempotency(user_id, idempotency_key)
            if existing is not None:
                return existing, False
        try:
            with self._connect() as conn, conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO runs (run_id, user_id, tenant_id, status, topic, request,
                                      idempotency_key, timeout_at, budget_limit_cny, queued_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (run_id, user_id, tenant_id, status, topic, Jsonb(request or {}),
                     idempotency_key, timeout_at, budget_limit_cny,
                     _now() if status == "QUEUED" else None),
                )
                row = cur.fetchone()
            return row, True
        except psycopg.errors.UniqueViolation:
            if idempotency_key is None:
                raise
            existing = self.get_run_by_idempotency(user_id, idempotency_key)
            if existing is None:
                raise
            return existing, False

    def get_run(self, run_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM runs WHERE run_id = %s", (run_id,))
            return cur.fetchone()

    def get_run_by_idempotency(
        self, user_id: Optional[str], idempotency_key: str
    ) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM runs WHERE user_id IS NOT DISTINCT FROM %s AND idempotency_key = %s",
                (user_id, idempotency_key),
            )
            return cur.fetchone()

    def list_runs(
        self,
        *,
        user_id: Optional[str] = None,
        statuses: Optional[Iterable[str]] = None,
        limit: int = 20,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("r.user_id = %s")
            params.append(user_id)
        if statuses:
            clauses.append("r.status = ANY(%s)")
            params.append(list(statuses))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.extend([limit, offset])
        sql = (
            "SELECT r.*, EXISTS (SELECT 1 FROM run_artifacts a WHERE a.run_id = r.run_id) AS has_report "
            f"FROM runs r {where} ORDER BY r.created_at DESC, r.run_id DESC LIMIT %s OFFSET %s"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def update_status(
        self,
        run_id: str,
        new_status: str,
        *,
        allowed_from: Iterable[str],
        **fields: Any,
    ) -> bool:
        """乐观状态迁移：仅当当前状态在 `allowed_from` 内才生效。

        返回是否发生迁移（`False` = 状态已被别处改变或已终局）。
        """
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        assignments = ["status = %s"]
        values: list[Any] = [new_status]
        for key in sorted(fields):
            assignments.append(f"{key} = %s")
            values.append(fields[key])
        values.extend([run_id, list(allowed_from)])
        sql = (
            f"UPDATE runs SET {', '.join(assignments)} "
            "WHERE run_id = %s AND status = ANY(%s) RETURNING run_id"
        )
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, values)
            return cur.fetchone() is not None

    def request_cancel(self, run_id: str) -> Optional[str]:
        """幂等取消请求，返回 run 的当前状态；run 不存在返回 `None`。

        - `CREATED` / `QUEUED` → `CANCELLED`（还没跑，直接终局）
        - `RUNNING` → `CANCEL_REQUESTED`（执行者负责在节点边界收口，不在库里直接杀）
        - 其余状态原样返回（含重复取消）
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status FROM runs WHERE run_id = %s FOR UPDATE", (run_id,))
            row = cur.fetchone()
            if row is None:
                return None
            status = row["status"]
            now = _now()
            if status in ("CREATED", "QUEUED"):
                cur.execute(
                    "UPDATE runs SET status = 'CANCELLED', stop_reason = 'user_cancelled', "
                    "cancel_requested_at = %s, finished_at = %s WHERE run_id = %s",
                    (now, now, run_id),
                )
                return "CANCELLED"
            if status == "RUNNING":
                cur.execute(
                    "UPDATE runs SET status = 'CANCEL_REQUESTED', cancel_requested_at = %s "
                    "WHERE run_id = %s AND status = 'RUNNING'",
                    (now, run_id),
                )
                return "CANCEL_REQUESTED"
            return status

    # ---- run_events ----

    def append_event(
        self, run_id: str, event_type: str, payload: Optional[dict[str, Any]] = None,
        *, sequence: Optional[int] = None,
    ) -> int:
        """追加事件并返回 `sequence`；run 不存在时抛 `LookupError`。

        - 不传 `sequence`：由数据库分配（`MAX(sequence)+1`，单写者场景）；
        - 传 `sequence`（P2-C 接线用）：与内存态帧号**逐帧对齐**，重复写入按幂等处理
          （`ON CONFLICT DO NOTHING`）—— 传输层强制收口与工作线程可能并发持久化，
          显式序号避免「库内顺序 ≠ 内存顺序」。
        """
        if sequence is not None:
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO run_events (run_id, sequence, event_type, payload)
                        VALUES (%s, %s, %s, %s)
                        ON CONFLICT (run_id, sequence) DO NOTHING
                        RETURNING sequence
                        """,
                        (run_id, sequence, event_type, Jsonb(payload or {})),
                    )
                    cur.fetchone()
                return sequence
            except psycopg.errors.ForeignKeyViolation as exc:
                raise LookupError(f"run not found: {run_id}") from exc
        for _ in range(3):
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO run_events (run_id, sequence, event_type, payload)
                        SELECT %s, COALESCE(MAX(sequence), -1) + 1, %s, %s
                        FROM run_events WHERE run_id = %s
                        RETURNING sequence
                        """,
                        (run_id, event_type, Jsonb(payload or {}), run_id),
                    )
                    row = cur.fetchone()
                if row is None:
                    raise LookupError(f"run not found: {run_id}")
                return row["sequence"]
            except psycopg.errors.ForeignKeyViolation as exc:
                # 聚合子查询对不存在的 run 也会返回一行（MAX 为 NULL ⇒ 0），
                # 于是插入撞上外键 —— 语义上就是「run 不存在」，转换为 LookupError。
                raise LookupError(f"run not found: {run_id}") from exc
            except psycopg.errors.UniqueViolation:
                continue
        raise RuntimeError(f"append_event: sequence conflict persisted for run {run_id}")

    def get_events(self, run_id: str, after: Optional[int] = None) -> list[dict[str, Any]]:
        """按 `sequence` 升序读取事件；`after` 用于 SSE 续传（只取更大序号）。"""
        sql = "SELECT * FROM run_events WHERE run_id = %s"
        params: list[Any] = [run_id]
        if after is not None:
            sql += " AND sequence > %s"
            params.append(after)
        sql += " ORDER BY sequence"
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def count_events(self, run_id: str, event_type: Optional[str] = None) -> int:
        sql = "SELECT count(*) AS n FROM run_events WHERE run_id = %s"
        params: list[Any] = [run_id]
        if event_type is not None:
            sql += " AND event_type = %s"
            params.append(event_type)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()["n"]

    def last_event_type(self, run_id: str) -> Optional[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT event_type FROM run_events WHERE run_id = %s "
                "ORDER BY sequence DESC LIMIT 1",
                (run_id,),
            )
            row = cur.fetchone()
            return row["event_type"] if row else None

    # ---- 启动恢复 / 运维 ----

    def claim_run(self, run_id: str, worker_id: str, lease_seconds: int) -> Optional[dict[str, Any]]:
        """原子领取 QUEUED 任务（→ RUNNING + 写租约）；已被领走或非 QUEUED 返回 `None`。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE runs
                   SET status = 'RUNNING',
                       worker_id = %s,
                       worker_status = 'alive',
                       started_at = COALESCE(started_at, now()),
                       queued_at = COALESCE(queued_at, created_at),
                       lease_expires_at = now() + make_interval(secs => %s)
                 WHERE run_id = %s AND status = 'QUEUED'
                RETURNING *
                """,
                (worker_id, lease_seconds, run_id),
            )
            return cur.fetchone()

    def renew_lease(self, run_id: str, worker_id: str, lease_seconds: int) -> bool:
        """续租（Worker 心跳）；任务已终局或已换主时返回 False。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET lease_expires_at = now() + make_interval(secs => %s), "
                "worker_status = 'alive' "
                "WHERE run_id = %s AND worker_id = %s "
                "AND status IN ('RUNNING','CANCEL_REQUESTED') RETURNING run_id",
                (lease_seconds, run_id, worker_id),
            )
            return cur.fetchone() is not None

    def count_active(self, user_id: Optional[str] = None) -> int:
        """活跃任务数（并发闸与配额用）。"""
        sql = "SELECT count(*) AS n FROM runs WHERE status = ANY(%s)"
        params: list[Any] = [list(ACTIVE_STATUSES)]
        if user_id is not None:
            sql += " AND user_id = %s"
            params.append(user_id)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()["n"]

    def mark_stale_as_lost(self, reason: str = "lost") -> int:
        """把非终局任务标记为 `LOST`（单实例内存态执行的既有事实：进程重启即失联）。

        返回被标记的行数；供 API 启动时调用（P3 引入租约后由 Worker 接管）。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET status = 'LOST', stop_reason = %s, finished_at = now() "
                "WHERE status = ANY(%s) RETURNING run_id",
                (reason, list(ACTIVE_STATUSES)),
            )
            return len(cur.fetchall())

    # ---- run_artifacts ----

    def put_artifact(self, run_id: str, kind: str, body: str) -> None:
        """写入/覆盖终局产物（`report_md` / `export_json` 等）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO run_artifacts (run_id, kind, body) VALUES (%s, %s, %s)
                ON CONFLICT (run_id, kind)
                DO UPDATE SET body = EXCLUDED.body, updated_at = now()
                """,
                (run_id, kind, body),
            )

    def get_artifact(self, run_id: str, kind: str) -> Optional[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT body FROM run_artifacts WHERE run_id = %s AND kind = %s",
                (run_id, kind),
            )
            row = cur.fetchone()
            return row["body"] if row else None

    def has_artifact(self, run_id: str, kind: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM run_artifacts WHERE run_id = %s AND kind = %s",
                (run_id, kind),
            )
            return cur.fetchone() is not None
