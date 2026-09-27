"""L3 任务持久化仓储层（P2-B）。

只负责 `runs` / `run_events` / `run_artifacts` 三张表的读写，不做业务编排：

- 状态机合法性由 SQL CHECK + `update_status(allowed_from=...)` 乐观迁移双重把关；
- 创建幂等由部分唯一索引 `(user_id, idempotency_key)` 兜底（并发安全）；
- 事件序号在数据库内分配（`MAX(sequence)+1`，主键 `(run_id, sequence)` 防重放）。

口径见 `docs/requirements/10-l3-production.md` §5.9 与 `migrations/0001_runs_and_events.sql`。

连接策略（P2-3）：进程内 `psycopg_pool.ConnectionPool` 复用连接（懒初始化，
`DR_PG_POOL_MIN/MAX` 可调），每连接以 `options` 固定 `statement_timeout`
（`DR_PG_STATEMENT_TIMEOUT_MS`，默认 15s，防单条慢 SQL 挂死请求路径）；
逐条 SQL 计时，超过 `DR_PG_SLOW_QUERY_MS`（默认 500ms）记 WARNING（只记
SQL 模板不含参数，避免 PII 入日志）。`with store._connect()` 的事务语义与
短连接一致（退出提交 / 异常回滚）。
"""
from __future__ import annotations

import logging
import os
import threading
import time
from datetime import UTC, datetime
from typing import Any, Iterable, Optional

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from psycopg_pool import ConnectionPool

from .persistence import ACTIVE_STATUSES

_log = logging.getLogger("deepresearch.pg")


def _env_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 0 else default


def pool_settings() -> dict[str, Any]:
    """连接池 / 超时 / 慢查询阈值（每次建池时读取，测试可 monkeypatch 环境变量）。"""
    max_size = max(1, _env_int("DR_PG_POOL_MAX", 8))
    min_size = max(0, _env_int("DR_PG_POOL_MIN", 1))
    return {
        "min_size": min(min_size, max_size),
        "max_size": max_size,
        "timeout": float(_env_int("DR_PG_POOL_TIMEOUT_S", 10)),
        "statement_timeout_ms": max(1, _env_int("DR_PG_STATEMENT_TIMEOUT_MS", 15000)),
        "slow_ms": _env_int("DR_PG_SLOW_QUERY_MS", 500),
        "application_name": (os.getenv("DR_PG_APP_NAME") or "deepresearch").strip(),
    }


def _sql_snippet(query: Any, limit: int = 200) -> str:
    """慢查询日志用的 SQL 模板摘要：压缩空白 + 截断；**不包含参数值**。"""
    text = " ".join(str(query).split())
    return text if len(text) <= limit else text[: limit - 3] + "..."


class _TimedCursor(psycopg.Cursor):
    """逐条 SQL 计时（P2-3）：超过连接上的慢查询阈值记 WARNING。"""

    def _timed(self, method: Any, query: Any, params: Any, kwargs: dict) -> Any:
        started = time.perf_counter()
        try:
            return method(query, params, **kwargs)
        finally:
            threshold = getattr(self.connection, "_dr_slow_ms", 0)
            if threshold:
                elapsed_ms = (time.perf_counter() - started) * 1000
                if elapsed_ms >= threshold:
                    _log.warning(
                        "slow query %.0fms (threshold %dms): %s",
                        elapsed_ms, threshold, _sql_snippet(query),
                    )

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        return self._timed(super().execute, query, params, kwargs)

    def executemany(self, query: Any, params_seq: Any, **kwargs: Any) -> Any:
        return self._timed(super().executemany, query, params_seq, kwargs)

    def copy(self, statement: Any, params: Any = None, **kwargs: Any) -> Any:
        return self._timed(super().copy, statement, params, kwargs)


def _configure_connection(conn: psycopg.Connection) -> None:
    """池内连接初始化：挂慢查询阈值；statement_timeout 由池 kwargs options 固定。"""
    conn._dr_slow_ms = pool_settings()["slow_ms"]  # type: ignore[attr-defined]

#: 终局状态（不得再迁移）；活跃状态见 `persistence.ACTIVE_STATUSES`（单一来源，此处再导出）
TERMINAL_STATUSES = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST")

#: SSE 事件唤醒通道（P2-4）：事件写入在**同一事务**内 `pg_notify`（提交后送达），
#: API 进程的 LISTEN 连接借此即时唤醒尾随循环；仅作延迟优化，正确性仍靠轮询兜底。
EVENTS_CHANNEL = "dr_run_events"


def notify_events(cur: psycopg.Cursor, run_id: str) -> None:
    """事务内事件唤醒（同事务提交才送达，回滚不发）。"""
    cur.execute("SELECT pg_notify(%s, %s)", (EVENTS_CHANNEL, run_id))

#: `update_status` 允许写的列白名单（防注入与误写主键/记账列）
_UPDATABLE_FIELDS = frozenset({
    "research_status", "stop_reason", "current_node", "token_used", "cost_estimate_cny",
    "budget_used_cny", "attempt", "retry_of", "worker_id", "worker_status",
    "lease_expires_at", "queued_at", "started_at", "finished_at", "cancel_requested_at",
    "timeout_at", "hard_deadline_at", "moderation_status",
})


def _now() -> datetime:
    return datetime.now(UTC)


class QuotaExceeded(Exception):
    """准入检查失败（P0-3）：kind ∈ global_concurrency / user_concurrency / daily_runs / monthly_budget。"""

    def __init__(self, kind: str, detail: str):
        super().__init__(detail)
        self.kind = kind
        self.detail = detail


class RunStore:
    """runs / run_events / run_artifacts 的最小仓储实现（同步，P2-3 连接池）。"""

    def __init__(self, dsn: str, *, connect_timeout: int = 3):
        self._dsn = dsn
        self._connect_timeout = connect_timeout
        self._pool: Optional[ConnectionPool] = None
        self._pool_lock = threading.Lock()

    def _get_pool(self) -> ConnectionPool:
        if self._pool is None:
            with self._pool_lock:
                if self._pool is None:
                    settings = pool_settings()
                    options = (
                        f"-c statement_timeout={settings['statement_timeout_ms']} "
                        f"-c application_name={settings['application_name']}"
                    )
                    pool = ConnectionPool(
                        self._dsn,
                        min_size=settings["min_size"],
                        max_size=settings["max_size"],
                        timeout=settings["timeout"],
                        kwargs={
                            "row_factory": dict_row,
                            "connect_timeout": self._connect_timeout,
                            "options": options,
                            "cursor_factory": _TimedCursor,
                        },
                        configure=_configure_connection,
                        open=False,
                    )
                    pool.open()
                    self._pool = pool
        return self._pool

    def _connect(self) -> psycopg.Connection:
        """从此进程的连接池取连接（上下文退出：正常提交 / 异常回滚，归还池）。"""
        return self._get_pool().connection()

    def close(self) -> None:
        """关闭连接池（进程退出 / 测试清理用；幂等）。"""
        pool, self._pool = self._pool, None
        if pool is not None:
            pool.close()

    @property
    def dsn(self) -> str:
        """连接串（P2-4：API 侧 LISTEN 专连接复用同一 DSN）。"""
        return self._dsn

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
        request_hash: Optional[str] = None,
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
                                      idempotency_key, request_hash, timeout_at,
                                      budget_limit_cny, queued_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (run_id, user_id, tenant_id, status, topic, Jsonb(request or {}),
                     idempotency_key, request_hash, timeout_at, budget_limit_cny,
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

    def create_run_admitted(
        self,
        run_id: str,
        topic: str,
        request: Optional[dict[str, Any]] = None,
        *,
        user_id: Optional[str] = None,
        tenant_id: Optional[str] = None,
        idempotency_key: Optional[str] = None,
        request_hash: Optional[str] = None,
        status: str = "CREATED",
        timeout_at: Optional[datetime] = None,
        budget_limit_cny: Optional[float] = None,
        global_active_limit: Optional[int] = None,
        user_active_limit: Optional[int] = None,
        daily_limit: Optional[int] = None,
        monthly_budget_cny: Optional[float] = None,
        daily_since: Optional[datetime] = None,
    ) -> tuple[dict[str, Any], bool]:
        """原子准入 + 创建（P0-3）：检查与插入在**同一事务**，多 API 实例并发安全。

        串行化手段：`pg_advisory_xact_lock`（全局配额一把、用户配额一把），
        锁随事务提交/回滚自动释放；检查失败抛 :class:`QuotaExceeded`（事务回滚，
        不插入半成品）。

        与 :meth:`create_run` 的幂等语义一致：幂等命中返回既有行（`created=False`）。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("dr:quota:global",))
            if monthly_budget_cny is not None and monthly_budget_cny > 0:
                cur.execute(
                    "SELECT COALESCE(SUM(cost_estimate_cny), 0) AS total FROM runs "
                    "WHERE created_at >= date_trunc('month', now())"
                )
                spent = float(cur.fetchone()["total"])
                if spent >= monthly_budget_cny:
                    raise QuotaExceeded(
                        "monthly_budget", f"spent={spent:.4f}; limit={monthly_budget_cny}")
            if global_active_limit is not None and global_active_limit > 0:
                cur.execute("SELECT count(*) AS n FROM runs WHERE status = ANY(%s)",
                            (list(ACTIVE_STATUSES),))
                active = int(cur.fetchone()["n"])
                if active >= global_active_limit:
                    raise QuotaExceeded(
                        "global_concurrency", f"active={active}; limit={global_active_limit}")
            if user_id is not None:
                cur.execute("SELECT pg_advisory_xact_lock(hashtext(%s))",
                            (f"dr:quota:user:{user_id}",))
                if user_active_limit is not None and user_active_limit > 0:
                    cur.execute(
                        "SELECT count(*) AS n FROM runs WHERE status = ANY(%s) AND user_id = %s",
                        (list(ACTIVE_STATUSES), user_id),
                    )
                    active = int(cur.fetchone()["n"])
                    if active >= user_active_limit:
                        raise QuotaExceeded(
                            "user_concurrency", f"active={active}; limit={user_active_limit}")
                if daily_limit is not None and daily_limit > 0 and daily_since is not None:
                    cur.execute(
                        "SELECT count(*) AS n FROM runs WHERE user_id = %s AND created_at >= %s",
                        (user_id, daily_since),
                    )
                    used = int(cur.fetchone()["n"])
                    if used >= daily_limit:
                        raise QuotaExceeded(
                            "daily_runs", f"used={used}; limit={daily_limit}")
            if idempotency_key is not None:
                cur.execute(
                    "SELECT * FROM runs WHERE user_id IS NOT DISTINCT FROM %s "
                    "AND idempotency_key = %s",
                    (user_id, idempotency_key),
                )
                existing = cur.fetchone()
                if existing is not None:
                    return existing, False
            try:
                cur.execute(
                    """
                    INSERT INTO runs (run_id, user_id, tenant_id, status, topic, request,
                                      idempotency_key, request_hash, timeout_at,
                                      budget_limit_cny, queued_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    RETURNING *
                    """,
                    (run_id, user_id, tenant_id, status, topic, Jsonb(request or {}),
                     idempotency_key, request_hash, timeout_at, budget_limit_cny,
                     _now() if status == "QUEUED" else None),
                )
                row = cur.fetchone()
            except psycopg.errors.UniqueViolation:
                if idempotency_key is None:
                    raise
                cur.execute(
                    "SELECT * FROM runs WHERE user_id IS NOT DISTINCT FROM %s "
                    "AND idempotency_key = %s",
                    (user_id, idempotency_key),
                )
                existing = cur.fetchone()
                if existing is None:
                    raise
                return existing, False
            return row, True

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

    def update_usage(self, run_id: str, *, token_used: int, cost_estimate_cny: float,
                     budget_used_cny: float) -> None:
        """更新计量列（token / 成本估算 / 已用预算），**不改状态**。

        为什么单独一条：Worker 每完成一个节点会回写计量，如果顺手把 status 写成 RUNNING，
        会把执行期间落下的 CANCEL_REQUESTED 覆盖掉（P3-B 修）。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET token_used = %s, cost_estimate_cny = %s, budget_used_cny = %s "
                "WHERE run_id = %s",
                (token_used, cost_estimate_cny, budget_used_cny, run_id),
            )

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
                    notify_events(cur, run_id)  # P2-4：SSE 唤醒（同事务）
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
                    if row is not None:
                        notify_events(cur, run_id)  # P2-4：SSE 唤醒（同事务）
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

    def claim_next_queued(self, worker_id: str, lease_seconds: int) -> Optional[dict[str, Any]]:
        """原子领取**下一条** QUEUED（P0-2）：`FOR UPDATE SKIP LOCKED`，多 Worker 并发安全。

        派发权威在 PostgreSQL —— 不存在「先出队、后认领」的丢失窗口：
        领取失败/崩溃只会留下 QUEUED 行，下一轮或下一个 Worker 继续领。

        跳过已过 `timeout_at` 的行（由 `sweep_stale_runs` 收口为 `TIMED_OUT`）。
        返回 `None` = 当前没有可领取任务。
        """
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
                 WHERE run_id = (
                     SELECT run_id FROM runs
                      WHERE status = 'QUEUED'
                        AND (timeout_at IS NULL OR timeout_at > now())
                      ORDER BY queued_at NULLS FIRST, created_at
                      FOR UPDATE SKIP LOCKED
                      LIMIT 1
                 )
                RETURNING *
                """,
                (worker_id, lease_seconds),
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

    def count_queued(self) -> int:
        """QUEUED 任务数（P0-2：队列深度以任务库为准；Redis 只是唤醒信号）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT count(*) AS n FROM runs WHERE status = 'QUEUED'")
            return int(cur.fetchone()["n"])

    def count_user_runs_since(self, user_id: str, since: datetime) -> int:
        """某用户自 `since` 起创建的 run 数（每日运行次数配额用；`since` 由调用方按 UTC 日界给出）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM runs WHERE user_id = %s AND created_at >= %s",
                (user_id, since),
            )
            return cur.fetchone()["n"]

    def month_cost_cny(self) -> float:
        """本自然月（UTC）全部运行的成本估算合计（全局月度预算闸用）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT COALESCE(SUM(cost_estimate_cny), 0) AS total FROM runs "
                "WHERE created_at >= date_trunc('month', now())"
            )
            return float(cur.fetchone()["total"])

    def status_counts_since(self, since: datetime) -> dict[str, int]:
        """自 `since` 起创建的任务按状态计数（P8-A 指标端点用）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT status, count(*) AS n FROM runs WHERE created_at >= %s GROUP BY status",
                (since,),
            )
            return {row["status"]: row["n"] for row in cur.fetchall()}

    def count_stale_leases(self) -> int:
        """租约已过期但仍处于 RUNNING/CANCEL_REQUESTED 的任务数（P8-A 告警用）。

        >0 说明可能已有 Worker 崩溃且尚未被清扫周期接管。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM runs "
                "WHERE status IN ('RUNNING','CANCEL_REQUESTED') "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < now()"
            )
            return cur.fetchone()["n"]

    def sweep_stale_runs(self, max_attempts: int = 2) -> list[dict[str, Any]]:
        """租约超时清扫（P3-B）+ 过期 QUEUED 收口（P0-2）：接管停滞的活跃任务。

        规则（需求 10 §5.9.3）：
        - `QUEUED` 且 `timeout_at` 已过（排队等到超时）→ `TIMED_OUT`（不执行、不占额度）；
        - 有取消意图（`cancel_requested_at` 非空）→ `CANCELLED`（尊重用户，不重跑）；
        - `attempt < max_attempts` → 回 `QUEUED`（`attempt+1`，清空 worker/租约），调用方负责唤醒；
        - 重试耗尽 → `LOST`。

        原子性：`SELECT ... FOR UPDATE SKIP LOCKED` + 同一事务更新 ⇒ 多 Worker 并发清扫只接管一次。
        返回处理明细（供 Worker 唤醒与日志）。
        """
        results: list[dict[str, Any]] = []
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT run_id FROM runs "
                "WHERE status = 'QUEUED' AND timeout_at IS NOT NULL AND timeout_at < now() "
                "FOR UPDATE SKIP LOCKED"
            )
            for row in cur.fetchall():
                cur.execute(
                    "UPDATE runs SET status = 'TIMED_OUT', stop_reason = 'timeout', "
                    "finished_at = now() WHERE run_id = %s",
                    (row["run_id"],),
                )
                results.append({"run_id": row["run_id"], "action": "timed_out"})
            cur.execute(
                "SELECT run_id, attempt, cancel_requested_at FROM runs "
                "WHERE status IN ('RUNNING','CANCEL_REQUESTED') "
                "AND lease_expires_at IS NOT NULL AND lease_expires_at < now() "
                "FOR UPDATE SKIP LOCKED"
            )
            for row in cur.fetchall():
                run_id = row["run_id"]
                if row["cancel_requested_at"] is not None:
                    cur.execute(
                        "UPDATE runs SET status = 'CANCELLED', stop_reason = 'user_cancelled', "
                        "finished_at = now() WHERE run_id = %s",
                        (run_id,),
                    )
                    results.append({"run_id": run_id, "action": "cancelled"})
                elif row["attempt"] < max_attempts:
                    cur.execute(
                        "UPDATE runs SET status = 'QUEUED', attempt = attempt + 1, "
                        "worker_id = NULL, worker_status = NULL, lease_expires_at = NULL, "
                        "queued_at = now() WHERE run_id = %s",
                        (run_id,),
                    )
                    results.append({"run_id": run_id, "action": "requeued",
                                    "attempt": row["attempt"] + 1})
                else:
                    cur.execute(
                        "UPDATE runs SET status = 'LOST', stop_reason = 'lost', "
                        "finished_at = now() WHERE run_id = %s",
                        (run_id,),
                    )
                    results.append({"run_id": run_id, "action": "lost"})
        return results

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

    def finalize_run(
        self,
        run_id: str,
        *,
        event_type: str,
        payload: Optional[dict[str, Any]],
        sequence: Optional[int],
        new_status: str,
        allowed_from: Iterable[str],
        fields: Optional[dict[str, Any]] = None,
        artifacts: Optional[dict[str, dict[str, Any]]] = None,
    ) -> bool:
        """终局原子落库（P0-6）：状态迁移 + 终局事件 + 产物在**同一事务**提交。

        - 状态迁移失败（已被清扫 / 强制收口抢先）⇒ 整体回滚并返回 ``False``，
          不产生「状态未迁移但事件/产物已写」的半成品（完成先落终局，之后不得改判）；
        - `sequence=None`（Worker 单写者）由数据库分配 ``MAX(sequence)+1``，
          冲突时整体重试；显式序号按幂等处理（``ON CONFLICT DO NOTHING``）；
        - `artifacts`（P1-6）：`{kind: {body, storage, object_key, sha256, size_bytes}}`，
          `storage='s3'` 时正文在对象存储、本表只留元数据。
        """
        fields = fields or {}
        unknown = set(fields) - _UPDATABLE_FIELDS
        if unknown:
            raise ValueError(f"unknown fields: {sorted(unknown)}")
        assignments = ["status = %s"]
        values: list[Any] = [new_status]
        for key in sorted(fields):
            assignments.append(f"{key} = %s")
            values.append(fields[key])
        update_sql = (
            f"UPDATE runs SET {', '.join(assignments)} "
            "WHERE run_id = %s AND status = ANY(%s) RETURNING run_id"
        )
        update_values = [*values, run_id, list(allowed_from)]
        attempts = 3 if sequence is None else 1
        for _ in range(attempts):
            try:
                with self._connect() as conn, conn.cursor() as cur:
                    cur.execute(update_sql, update_values)
                    if cur.fetchone() is None:
                        conn.rollback()
                        return False
                    if sequence is None:
                        cur.execute(
                            """
                            INSERT INTO run_events (run_id, sequence, event_type, payload)
                            SELECT %s, COALESCE(MAX(sequence), -1) + 1, %s, %s
                            FROM run_events WHERE run_id = %s
                            RETURNING sequence
                            """,
                            (run_id, event_type, Jsonb(payload or {}), run_id),
                        )
                    else:
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
                    notify_events(cur, run_id)  # P2-4：SSE 唤醒（与终局事件/产物同事务）
                    for kind, meta in (artifacts or {}).items():
                        cur.execute(
                            """
                            INSERT INTO run_artifacts (run_id, kind, body, storage, object_key,
                                                       sha256, size_bytes)
                            VALUES (%s, %s, %s, %s, %s, %s, %s)
                            ON CONFLICT (run_id, kind)
                            DO UPDATE SET body = EXCLUDED.body, storage = EXCLUDED.storage,
                                          object_key = EXCLUDED.object_key,
                                          sha256 = EXCLUDED.sha256,
                                          size_bytes = EXCLUDED.size_bytes,
                                          updated_at = now()
                            """,
                            (run_id, kind, meta.get("body", ""), meta.get("storage", "db"),
                             meta.get("object_key"), meta.get("sha256"),
                             meta.get("size_bytes")),
                        )
                return True
            except psycopg.errors.UniqueViolation:
                continue  # 序号竞争：整体重试（事务已回滚）
        raise RuntimeError(f"finalize_run: sequence conflict persisted for run {run_id}")

    def get_artifact(self, run_id: str, kind: str) -> Optional[str]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT body FROM run_artifacts WHERE run_id = %s AND kind = %s",
                (run_id, kind),
            )
            row = cur.fetchone()
            return row["body"] if row else None

    def get_artifact_row(self, run_id: str, kind: str) -> Optional[dict[str, Any]]:
        """产物完整行（P1-6：读取侧据 `storage` 决定走 PG body 还是对象存储）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM run_artifacts WHERE run_id = %s AND kind = %s",
                (run_id, kind),
            )
            return cur.fetchone()

    def has_artifact(self, run_id: str, kind: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM run_artifacts WHERE run_id = %s AND kind = %s",
                (run_id, kind),
            )
            return cur.fetchone() is not None

    # ---- 账号 / 会话 / 邀请（P4-A）----

    def create_user(self, user_id: str, email: str, password_hash: str) -> dict[str, Any]:
        """建用户（邮箱重复会抛 `UniqueViolation`，由 API 转 `email_taken`）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO users (user_id, email, password_hash) VALUES (%s, %s, %s) RETURNING *",
                (user_id, email, password_hash),
            )
            return cur.fetchone()

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE user_id = %s", (user_id,))
            return cur.fetchone()

    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM users WHERE lower(email) = lower(%s)", (email,))
            return cur.fetchone()

    def touch_last_login(self, user_id: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET last_login_at = now(), updated_at = now() WHERE user_id = %s",
                (user_id,),
            )

    def set_user_status(self, user_id: str, status: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET status = %s, updated_at = now() WHERE user_id = %s RETURNING user_id",
                (status, user_id),
            )
            return cur.fetchone() is not None

    def register_with_invite(self, user_id: str, email: str, password_hash: str,
                             invite_hash: str) -> dict[str, Any]:
        """单事务注册：校验邀请码（`FOR UPDATE`，一次性/未撤销/未过期）→ 建用户 → 标记已用。

        邀请码无效时抛 `ValueError("invite_invalid")`；邮箱重复时抛 `UniqueViolation`
        并由事务回滚（邀请码不被消耗）。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT expires_at, used_at, revoked_at FROM invites "
                "WHERE code_hash = %s FOR UPDATE",
                (invite_hash,),
            )
            invite = cur.fetchone()
            if invite is None or invite["used_at"] is not None or invite["revoked_at"] is not None:
                raise ValueError("invite_invalid")
            if invite["expires_at"] is not None and invite["expires_at"] < _now():
                raise ValueError("invite_invalid")
            cur.execute(
                "INSERT INTO users (user_id, email, password_hash) VALUES (%s, %s, %s) RETURNING *",
                (user_id, email, password_hash),
            )
            user = cur.fetchone()
            cur.execute(
                "UPDATE invites SET used_by = %s, used_at = now() WHERE code_hash = %s",
                (user_id, invite_hash),
            )
            return user

    def create_invite(self, code_hash: str, *, created_by: Optional[str] = None,
                      expires_at: Optional[datetime] = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO invites (code_hash, created_by, expires_at) VALUES (%s, %s, %s)",
                (code_hash, created_by, expires_at),
            )

    def revoke_invite(self, code_hash: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE invites SET revoked_at = now() "
                "WHERE code_hash = %s AND used_at IS NULL AND revoked_at IS NULL "
                "RETURNING code_hash",
                (code_hash,),
            )
            return cur.fetchone() is not None

    def list_invites(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT code_hash, created_by, created_at, expires_at, used_by, used_at, revoked_at "
                "FROM invites ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()

    def create_session(self, session_hash: str, user_id: str, expires_at: datetime,
                       *, ip: Optional[str] = None,
                       user_agent: Optional[str] = None) -> str:
        """建会话（P1-10：记录 IP / UA / last_seen；返回对外可见 `session_id`）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO sessions (token_hash, user_id, expires_at, ip, user_agent) "
                "VALUES (%s, %s, %s, %s, %s) RETURNING session_id",
                (session_hash, user_id, expires_at, ip, (user_agent or "")[:300] or None),
            )
            return cur.fetchone()["session_id"]

    def get_session_user(self, session_hash: str, *, idle_seconds: int = 0) -> Optional[dict[str, Any]]:
        """按令牌摘要取**有效**会话对应的用户（过期 / 空闲超时 / 封禁即无效）。

        返回含会话元数据（`session_id` / `session_last_seen_at` / `session_ip` /
        `session_user_agent`），供会话治理接口使用；`idle_seconds<=0` 表示不启用空闲超时。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT u.*, s.session_id, s.created_at AS session_created_at, "
                "       s.last_seen_at AS session_last_seen_at, s.ip AS session_ip, "
                "       s.user_agent AS session_user_agent, s.token_hash AS session_token_hash "
                "FROM sessions s JOIN users u ON u.user_id = s.user_id "
                "WHERE s.token_hash = %s AND s.expires_at > now() AND u.status = 'active' "
                "  AND (%s = 0 OR s.last_seen_at > now() - make_interval(secs => %s))",
                (session_hash, idle_seconds, idle_seconds),
            )
            return cur.fetchone()

    def touch_session(self, session_hash: str) -> None:
        """刷新 last_seen（节流：60s 内不重复写）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE sessions SET last_seen_at = now() WHERE token_hash = %s "
                "AND last_seen_at < now() - interval '60 seconds'",
                (session_hash,),
            )

    def list_sessions(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT session_id, created_at, last_seen_at, ip, user_agent, token_hash "
                "FROM sessions WHERE user_id = %s ORDER BY last_seen_at DESC",
                (user_id,),
            )
            return cur.fetchall()

    def revoke_session_by_id(self, user_id: str, session_id: str) -> bool:
        """按对外 `session_id` 终止本用户的一个会话（越权不可见）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE user_id = %s AND session_id = %s "
                "RETURNING token_hash",
                (user_id, session_id),
            )
            return cur.fetchone() is not None

    def revoke_other_sessions(self, user_id: str, keep_token_hash: str) -> int:
        """吊销该用户除当前会话外的全部会话（P1-10「退出其他设备」）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE user_id = %s AND token_hash <> %s "
                "RETURNING token_hash",
                (user_id, keep_token_hash),
            )
            return len(cur.fetchall())

    def revoke_session(self, session_hash: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE token_hash = %s RETURNING token_hash",
                (session_hash,),
            )
            return cur.fetchone() is not None

    def revoke_user_sessions(self, user_id: str) -> int:
        """吊销某用户的**全部**会话（改密 / 管理员重置后使用）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM sessions WHERE user_id = %s RETURNING token_hash",
                (user_id,),
            )
            return len(cur.fetchall())

    def update_password(self, user_id: str, password_hash: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE users SET password_hash = %s, updated_at = now() "
                "WHERE user_id = %s RETURNING user_id",
                (password_hash, user_id),
            )
            return cur.fetchone() is not None

    def purge_expired_sessions(self) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM sessions WHERE expires_at <= now() RETURNING token_hash")
            return len(cur.fetchall())

    # ---- 密码重置 token（P1-10）----

    def create_password_reset(self, token_hash: str, user_id: str, expires_at: datetime,
                              *, created_by: Optional[str] = None) -> None:
        """发放重置 token：先失效该用户所有未消费 token（兄弟互斥），再写新 token。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM password_reset_tokens WHERE user_id = %s AND consumed_at IS NULL",
                (user_id,),
            )
            cur.execute(
                "INSERT INTO password_reset_tokens (token_hash, user_id, expires_at, created_by) "
                "VALUES (%s, %s, %s, %s)",
                (token_hash, user_id, expires_at, created_by),
            )

    def consume_password_reset(self, token_hash: str) -> Optional[str]:
        """原子消费：有效且未消费才返回 `user_id`（单次；并发只有一个成功）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE password_reset_tokens SET consumed_at = now() "
                "WHERE token_hash = %s AND consumed_at IS NULL AND expires_at > now() "
                "RETURNING user_id",
                (token_hash,),
            )
            row = cur.fetchone()
            return row["user_id"] if row else None

    def delete_password_resets(self, user_id: str) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM password_reset_tokens WHERE user_id = %s RETURNING token_hash",
                (user_id,),
            )
            return len(cur.fetchall())

    def purge_expired_password_resets(self) -> int:
        """存储卫生：清理已消费或过期超过 1 天的 token。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM password_reset_tokens "
                "WHERE consumed_at IS NOT NULL OR expires_at < now() - interval '1 day' "
                "RETURNING token_hash",
            )
            return len(cur.fetchall())

    def complete_password_reset(self, token_hash: str, password_hash: str) -> Optional[str]:
        """原子完成重置（P1-10）：消费 token + 改密 + 吊销全部会话 + 清理其余 token。

        返回 `user_id`；token 无效/过期/已消费返回 `None`（单次消费，并发只有一个成功）。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE password_reset_tokens SET consumed_at = now() "
                "WHERE token_hash = %s AND consumed_at IS NULL AND expires_at > now() "
                "RETURNING user_id",
                (token_hash,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            user_id = row["user_id"]
            cur.execute(
                "UPDATE users SET password_hash = %s, updated_at = now() WHERE user_id = %s",
                (password_hash, user_id),
            )
            cur.execute("DELETE FROM sessions WHERE user_id = %s", (user_id,))
            cur.execute("DELETE FROM password_reset_tokens WHERE user_id = %s", (user_id,))
            return user_id

    # ---- 用量账本（P1-4）----

    def record_usage(self, *, run_id: Optional[str] = None, attempt: int = 1, kind: str,
                     provider: str = "", model: str = "", role: str = "",
                     input_tokens: int = 0, output_tokens: int = 0, total_tokens: int = 0,
                     cost_estimate_cny: float = 0.0, cost_source: str = "estimate",
                     request_id: Optional[str] = None,
                     detail: Optional[dict[str, Any]] = None) -> int:
        """追加一条逐调用用量（P1-4；不含 prompt / PII）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO usage_ledger (run_id, attempt, kind, provider, model, role,
                                          input_tokens, output_tokens, total_tokens,
                                          cost_estimate_cny, cost_source, request_id, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (run_id, attempt, kind, provider, model, role, input_tokens, output_tokens,
                 total_tokens, cost_estimate_cny, cost_source, request_id, Jsonb(detail or {})),
            )
            return cur.fetchone()["id"]

    def list_usage(self, *, run_id: Optional[str] = None,
                   limit: int = 200) -> list[dict[str, Any]]:
        params: list[Any] = []
        sql = "SELECT * FROM usage_ledger"
        if run_id is not None:
            sql += " WHERE run_id = %s"
            params.append(run_id)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def usage_summary(self, *, run_id: Optional[str] = None,
                      since: Optional[datetime] = None) -> dict[str, Any]:
        """按 kind/model/cost_source 聚合（先对请求数再对钱；金额为估算上界）。"""
        clauses: list[str] = []
        params: list[Any] = []
        if run_id is not None:
            clauses.append("run_id = %s")
            params.append(run_id)
        if since is not None:
            clauses.append("created_at >= %s")
            params.append(since)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"""
                SELECT kind, model, cost_source, count(*) AS calls,
                       COALESCE(SUM(total_tokens), 0) AS tokens,
                       COALESCE(SUM(cost_estimate_cny), 0) AS cost_cny
                FROM usage_ledger {where}
                GROUP BY kind, model, cost_source
                ORDER BY kind, model
                """,
                params,
            )
            rows = [
                {**row, "calls": int(row["calls"]), "tokens": int(row["tokens"]),
                 "cost_cny": round(float(row["cost_cny"]), 6)}
                for row in cur.fetchall()
            ]
        return {
            "rows": rows,
            "calls": sum(row["calls"] for row in rows),
            "tokens": sum(row["tokens"] for row in rows),
            "cost_cny": round(sum(row["cost_cny"] for row in rows), 6),
        }

    # ---- 内容安全（P7-A）----

    def record_moderation(self, kind: str, *, user_id: Optional[str] = None,
                          run_id: Optional[str] = None,
                          detail: Optional[dict[str, Any]] = None) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO moderation_records (user_id, run_id, kind, detail) "
                "VALUES (%s, %s, %s, %s) RETURNING id",
                (user_id, run_id, kind, Jsonb(detail or {})),
            )
            return cur.fetchone()["id"]

    def list_moderation(self, *, kind: Optional[str] = None,
                        limit: int = 50) -> list[dict[str, Any]]:
        sql = "SELECT * FROM moderation_records"
        params: list[Any] = []
        if kind is not None:
            sql += " WHERE kind = %s"
            params.append(kind)
        sql += " ORDER BY id DESC LIMIT %s"
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()

    def has_appeal(self, user_id: Optional[str], run_id: str) -> bool:
        """该用户对该 run 是否已提交过申诉（P0-5 防重复）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT 1 FROM moderation_records "
                "WHERE kind = 'appeal' AND user_id IS NOT DISTINCT FROM %s AND run_id = %s "
                "LIMIT 1",
                (user_id, run_id),
            )
            return cur.fetchone() is not None

    def set_moderation_status(self, run_id: str, status: str) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE runs SET moderation_status = %s WHERE run_id = %s RETURNING run_id",
                (status, run_id),
            )
            return cur.fetchone() is not None

    def delete_user(self, user_id: str) -> bool:
        """删除用户（P7-A 注销）：会话级联删除、runs/记录脱钩（SET NULL）、邀请 used_by 置空。

        任务与审核记录**保留但匿名**（审计需要）；RAG 向量由调用方另行清理。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM users WHERE user_id = %s RETURNING user_id", (user_id,))
            return cur.fetchone() is not None

    # ---- 注销台账与 outbox（P0-7）----

    def request_account_deletion(self, request_id: str, user_id: str, *,
                                 targets: Iterable[str] = ("qdrant",),
                                 payload: Optional[dict[str, Any]] = None) -> None:
        """注销登记（P0-7）：**同一事务**写台账 + outbox + 删用户（会话级联、任务匿名）。

        外部系统清理（Qdrant）不再同步做：由 `deletion_outbox` 承载，Worker 带退避重试。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO account_deletions (request_id, user_id) VALUES (%s, %s)",
                (request_id, user_id),
            )
            for target in targets:
                cur.execute(
                    """
                    INSERT INTO deletion_outbox (request_id, target, payload)
                    VALUES (%s, %s, %s)
                    ON CONFLICT (request_id, target) DO NOTHING
                    """,
                    (request_id, target, Jsonb(payload or {"user_id": user_id})),
                )
            cur.execute("DELETE FROM users WHERE user_id = %s", (user_id,))

    def claim_deletion_outbox(self, claimed_by: str, lease_seconds: int,
                              limit: int = 5) -> list[dict[str, Any]]:
        """领取到期 / 租约过期的 outbox（`FOR UPDATE SKIP LOCKED`），`attempts+1`。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE deletion_outbox
                   SET status = 'in_progress',
                       attempts = attempts + 1,
                       claimed_by = %s,
                       lease_expires_at = now() + make_interval(secs => %s),
                       updated_at = now()
                 WHERE id IN (
                     SELECT id FROM deletion_outbox
                      WHERE (status = 'pending' AND next_attempt_at <= now())
                         OR (status = 'in_progress' AND lease_expires_at IS NOT NULL
                             AND lease_expires_at < now())
                      ORDER BY id
                      FOR UPDATE SKIP LOCKED
                      LIMIT %s
                 )
                RETURNING *
                """,
                (claimed_by, lease_seconds, limit),
            )
            rows = cur.fetchall()
            for row in rows:
                cur.execute(
                    "UPDATE account_deletions SET status = 'in_progress', updated_at = now() "
                    "WHERE request_id = %s AND status = 'pending'",
                    (row["request_id"],),
                )
            return rows

    def mark_deletion_done(self, outbox_id: int) -> None:
        """outbox 成功：置 `done`；该请求全部 target `done` ⇒ 台账 `completed`。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE deletion_outbox SET status = 'done', lease_expires_at = NULL, "
                "claimed_by = NULL, last_error = NULL, updated_at = now() "
                "WHERE id = %s RETURNING request_id",
                (outbox_id,),
            )
            row = cur.fetchone()
            if row is None:
                return
            cur.execute(
                """
                UPDATE account_deletions
                   SET status = CASE
                         WHEN NOT EXISTS (
                             SELECT 1 FROM deletion_outbox
                              WHERE request_id = %s AND status <> 'done')
                         THEN 'completed' ELSE 'in_progress' END,
                       completed_at = CASE
                         WHEN NOT EXISTS (
                             SELECT 1 FROM deletion_outbox
                              WHERE request_id = %s AND status <> 'done')
                         THEN now() ELSE completed_at END,
                       last_error = NULL,
                       updated_at = now()
                 WHERE request_id = %s
                """,
                (row["request_id"], row["request_id"], row["request_id"]),
            )

    def mark_deletion_retry(self, outbox_id: int, error: str, *,
                            backoff_seconds: int, max_attempts: int) -> str:
        """outbox 失败：未耗尽 ⇒ `pending` + 退避；耗尽 ⇒ `abandoned` + 台账 `abandoned`。

        返回新状态（`pending` / `abandoned` / `missing`）。绝不静默丢弃。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT request_id, attempts FROM deletion_outbox WHERE id = %s",
                (outbox_id,),
            )
            row = cur.fetchone()
            if row is None:
                return "missing"
            exhausted = row["attempts"] >= max_attempts
            new_status = "abandoned" if exhausted else "pending"
            cur.execute(
                """
                UPDATE deletion_outbox
                   SET status = %s, lease_expires_at = NULL, claimed_by = NULL,
                       next_attempt_at = now() + make_interval(secs => %s),
                       last_error = %s, updated_at = now()
                 WHERE id = %s
                """,
                (new_status, max(1, backoff_seconds), error[:500], outbox_id),
            )
            if exhausted:
                cur.execute(
                    "UPDATE account_deletions SET status = 'abandoned', last_error = %s, "
                    "updated_at = now() WHERE request_id = %s",
                    (error[:500], row["request_id"]),
                )
            return new_status

    def retry_deletion(self, request_id: str) -> int:
        """人工重试（CLI）：把该请求的 outbox 复位为 pending、attempts 归零。返回复位条数。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE deletion_outbox
                   SET status = 'pending', attempts = 0, next_attempt_at = now(),
                       lease_expires_at = NULL, claimed_by = NULL, last_error = NULL,
                       updated_at = now()
                 WHERE request_id = %s AND status <> 'done'
                RETURNING id
                """,
                (request_id,),
            )
            count = len(cur.fetchall())
            cur.execute(
                "UPDATE account_deletions SET status = 'pending', last_error = NULL, "
                "updated_at = now() WHERE request_id = %s AND status = 'abandoned'",
                (request_id,),
            )
            return count

    def deletion_status(self, request_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM account_deletions WHERE request_id = %s", (request_id,))
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute(
                "SELECT target, status, attempts, next_attempt_at, last_error "
                "FROM deletion_outbox WHERE request_id = %s ORDER BY id",
                (request_id,),
            )
            row["targets"] = cur.fetchall()
            return row

    def list_deletions(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM account_deletions ORDER BY requested_at DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()

    def count_deletions_by_status(self) -> dict[str, int]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, count(*) AS n FROM account_deletions GROUP BY status")
            return {row["status"]: int(row["n"]) for row in cur.fetchall()}

    # ---- RAG 摄取台账（P0-8b）----

    def create_ingestion(self, ingestion_id: str, doc_id: str, *, user_id: Optional[str] = None,
                         source: str, sha256: str, size_bytes: int,
                         stored_name: str) -> dict[str, Any]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO rag_ingestions (ingestion_id, doc_id, user_id, source, sha256,
                                            size_bytes, stored_name)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                RETURNING *
                """,
                (ingestion_id, doc_id, user_id, source, sha256, size_bytes, stored_name),
            )
            return cur.fetchone()

    def get_ingestion(self, ingestion_id: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM rag_ingestions WHERE ingestion_id = %s", (ingestion_id,))
            return cur.fetchone()

    def find_ingestion_by_doc(self, user_id: Optional[str],
                              doc_id: str) -> Optional[dict[str, Any]]:
        """该用户该内容最近一条未删除摄取记录（内容寻址去重用）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM rag_ingestions "
                "WHERE user_id IS NOT DISTINCT FROM %s AND doc_id = %s AND status <> 'deleted' "
                "ORDER BY created_at DESC LIMIT 1",
                (user_id, doc_id),
            )
            return cur.fetchone()

    def claim_next_ingestion(self, claimed_by: str,
                             lease_seconds: int) -> Optional[dict[str, Any]]:
        """原子领取下一条待处理摄取（SKIP LOCKED + 租约），`attempts+1`。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE rag_ingestions
                   SET status = 'processing',
                       attempts = attempts + 1,
                       claimed_by = %s,
                       lease_expires_at = now() + make_interval(secs => %s),
                       updated_at = now()
                 WHERE ingestion_id = (
                     SELECT ingestion_id FROM rag_ingestions
                      WHERE (status = 'pending' AND next_attempt_at <= now())
                         OR (status = 'processing' AND lease_expires_at IS NOT NULL
                             AND lease_expires_at < now())
                      ORDER BY created_at
                      FOR UPDATE SKIP LOCKED
                      LIMIT 1
                 )
                RETURNING *
                """,
                (claimed_by, lease_seconds),
            )
            return cur.fetchone()

    def mark_ingestion_ready(self, ingestion_id: str, chunks: int,
                             scan_status: str = "skipped") -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rag_ingestions SET status = 'ready', chunks = %s, scan_status = %s, "
                "lease_expires_at = NULL, claimed_by = NULL, last_error = NULL, "
                "processed_at = now(), updated_at = now() WHERE ingestion_id = %s",
                (chunks, scan_status, ingestion_id),
            )

    def mark_ingestion_rejected(self, ingestion_id: str, error: str, *,
                                scan_status: Optional[str] = None) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rag_ingestions SET status = 'rejected', last_error = %s, "
                "scan_status = COALESCE(%s, scan_status), lease_expires_at = NULL, "
                "claimed_by = NULL, processed_at = now(), updated_at = now() "
                "WHERE ingestion_id = %s",
                (error[:500], scan_status, ingestion_id),
            )

    def mark_ingestion_retry(self, ingestion_id: str, error: str, *,
                             backoff_seconds: int, max_attempts: int) -> str:
        """供应商类失败：未耗尽 ⇒ pending + 退避；耗尽 ⇒ rejected。返回新状态。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT attempts FROM rag_ingestions WHERE ingestion_id = %s",
                        (ingestion_id,))
            row = cur.fetchone()
            if row is None:
                return "missing"
            exhausted = row["attempts"] >= max_attempts
            if exhausted:
                cur.execute(
                    "UPDATE rag_ingestions SET status = 'rejected', last_error = %s, "
                    "lease_expires_at = NULL, claimed_by = NULL, processed_at = now(), "
                    "updated_at = now() WHERE ingestion_id = %s",
                    (error[:500], ingestion_id),
                )
                return "rejected"
            cur.execute(
                "UPDATE rag_ingestions SET status = 'pending', last_error = %s, "
                "next_attempt_at = now() + make_interval(secs => %s), "
                "lease_expires_at = NULL, claimed_by = NULL, updated_at = now() "
                "WHERE ingestion_id = %s",
                (error[:500], max(1, backoff_seconds), ingestion_id),
            )
            return "pending"

    def list_ingestions(self, *, user_id: Optional[str] = None, status: Optional[str] = None,
                        limit: int = 50) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if user_id is not None:
            clauses.append("user_id IS NOT DISTINCT FROM %s")
            params.append(user_id)
        if status is not None:
            clauses.append("status = %s")
            params.append(status)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM rag_ingestions {where} ORDER BY created_at DESC LIMIT %s",
                params,
            )
            return cur.fetchall()

    def list_ingestion_files_for_user(self, user_id: Optional[str]) -> list[str]:
        """该用户仍占用的隔离区文件名（账号注销时清理）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT stored_name FROM rag_ingestions "
                "WHERE user_id IS NOT DISTINCT FROM %s AND status <> 'deleted' "
                "AND stored_name <> ''",
                (user_id,),
            )
            return [row["stored_name"] for row in cur.fetchall()]

    def mark_ingestions_deleted_for_user(self, user_id: Optional[str]) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rag_ingestions SET status = 'deleted', stored_name = '', "
                "source = '(deleted)', updated_at = now() "
                "WHERE user_id IS NOT DISTINCT FROM %s AND status <> 'deleted' RETURNING ingestion_id",
                (user_id,),
            )
            return len(cur.fetchall())

    def delete_ingestion_by_doc(self, user_id: Optional[str], doc_id: str) -> list[str]:
        """按 doc_id 标记删除，返回需清理的隔离区文件名（Qdrant 由调用方删除）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT stored_name FROM rag_ingestions "
                "WHERE user_id IS NOT DISTINCT FROM %s AND doc_id = %s AND status <> 'deleted' "
                "AND stored_name <> ''",
                (user_id, doc_id),
            )
            names = [row["stored_name"] for row in cur.fetchall()]
            cur.execute(
                "UPDATE rag_ingestions SET status = 'deleted', stored_name = '', "
                "source = '(deleted)', updated_at = now() "
                "WHERE user_id IS NOT DISTINCT FROM %s AND doc_id = %s AND status <> 'deleted'",
                (user_id, doc_id),
            )
            return names

    def list_expired_ingestions(self, before: datetime,
                                limit: int = 50) -> list[dict[str, Any]]:
        """保留期到期（隐私政策 90 天）的 ready 摄取；由调用方清向量/文件后置 deleted。

        先列后删（不在 SQL 里直接改状态）：向量删除失败时行保持 ready，下轮清扫重试。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT ingestion_id, doc_id, user_id, stored_name FROM rag_ingestions "
                "WHERE status = 'ready' AND created_at < %s ORDER BY created_at LIMIT %s",
                (before, limit),
            )
            return cur.fetchall()

    def mark_ingestion_deleted(self, ingestion_id: str, reason: str = "expired") -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE rag_ingestions SET status = 'deleted', stored_name = '', "
                "source = %s, updated_at = now() WHERE ingestion_id = %s",
                (f"({reason})", ingestion_id),
            )

    def count_ingestions_by_status(self) -> dict[str, int]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT status, count(*) AS n FROM rag_ingestions GROUP BY status")
            return {row["status"]: int(row["n"]) for row in cur.fetchall()}

    # ---- 保留期清理（P2-2）----

    #: 允许清理的表与时间列白名单（防注入；只接受代码内常量，不接受调用方自由拼接）
    PURGE_TARGETS: frozenset[tuple[str, str]] = frozenset({
        ("run_events", "created_at"),
        ("runs", "finished_at"),
        ("usage_ledger", "created_at"),
        ("moderation_records", "created_at"),
        ("audit_logs", "at"),
    })

    def _check_purge_target(self, table: str, time_column: str) -> None:
        if (table, time_column) not in self.PURGE_TARGETS:
            raise ValueError(f"purge target not allowed: {table}.{time_column}")

    def count_table(self, table: str) -> int:
        if table not in {name for name, _ in self.PURGE_TARGETS}:
            raise ValueError(f"count target not allowed: {table}")
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(f"SELECT count(*) AS n FROM {table}")  # noqa: S608 —— 标识符来自白名单
            return int(cur.fetchone()["n"])

    def count_before(self, table: str, time_column: str, before: datetime, *,
                     where_extra: str = "") -> int:
        self._check_purge_target(table, time_column)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT count(*) AS n FROM {table} "  # noqa: S608 —— 标识符来自白名单
                f"WHERE {time_column} < %s {where_extra}",
                (before,),
            )
            return int(cur.fetchone()["n"])

    def purge_before(self, table: str, time_column: str, before: datetime, *,
                     where_extra: str = "", batch: int = 5000) -> int:
        """按时间批量硬删除（`ctid` + `LIMIT`，无需主键假设）；返回删除行数。

        批量循环由调用方控制（避免长事务锁表）；标识符只来自 `PURGE_TARGETS` 白名单。
        """
        self._check_purge_target(table, time_column)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"DELETE FROM {table} WHERE ctid IN ("  # noqa: S608 —— 标识符来自白名单
                f"SELECT ctid FROM {table} WHERE {time_column} < %s {where_extra} "
                f"ORDER BY {time_column} LIMIT %s) RETURNING 1",
                (before, max(1, batch)),
            )
            return len(cur.fetchall())

    # ---- 安全审计日志（P1-5）----

    def record_audit(self, action: str, *, actor_user_id: Optional[str] = None,
                     target_type: Optional[str] = None, target_id: Optional[str] = None,
                     ip: Optional[str] = None, user_agent: Optional[str] = None,
                     request_id: Optional[str] = None,
                     detail: Optional[dict[str, Any]] = None) -> int:
        """追加一条安全审计（append-only；不落密码 / token / PII）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_logs (action, actor_user_id, target_type, target_id,
                                        ip, user_agent, request_id, detail)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id
                """,
                (action, actor_user_id, target_type, target_id,
                 ip, (user_agent or "")[:300] or None, request_id, Jsonb(detail or {})),
            )
            return cur.fetchone()["id"]

    def list_audit(self, *, action: Optional[str] = None, actor_user_id: Optional[str] = None,
                   limit: int = 100) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if action is not None:
            clauses.append("action = %s")
            params.append(action)
        if actor_user_id is not None:
            clauses.append("actor_user_id = %s")
            params.append(actor_user_id)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM audit_logs {where} ORDER BY at DESC LIMIT %s", params)
            return cur.fetchall()

    # ---- 告警状态与外部交付（P2-6）----

    def get_alert_state(self, fingerprint: str) -> Optional[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM alert_states WHERE fingerprint = %s", (fingerprint,))
            return cur.fetchone()

    def list_alert_states(self, *, status: Optional[str] = None,
                          limit: int = 200) -> list[dict[str, Any]]:
        clause = "WHERE status = %s" if status is not None else ""
        params: list[Any] = [status] if status is not None else []
        params.append(limit)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM alert_states {clause} ORDER BY last_seen_at DESC LIMIT %s",
                params)
            return cur.fetchall()

    def upsert_alert_state(self, fingerprint: str, *, severity: str, status: str,
                           detail: Optional[dict[str, Any]] = None,
                           notified_at: Optional[datetime] = None) -> dict[str, Any]:
        """置状态并返回当前行；`notified_at` 非空时刷新 `last_notified_at`（否则保留旧值）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alert_states (fingerprint, severity, status, detail,
                                          last_notified_at)
                VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO UPDATE
                   SET severity = EXCLUDED.severity,
                       status = EXCLUDED.status,
                       detail = EXCLUDED.detail,
                       last_seen_at = now(),
                       last_notified_at = COALESCE(EXCLUDED.last_notified_at,
                                                   alert_states.last_notified_at),
                       updated_at = now()
                RETURNING *
                """,
                (fingerprint, severity, status, Jsonb(detail or {}), notified_at),
            )
            return cur.fetchone()

    def enqueue_alert_delivery(self, fingerprint: str, kind: str, severity: str, *,
                               payload: Optional[dict[str, Any]] = None,
                               max_attempts: int = 5) -> int:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alert_deliveries (fingerprint, kind, severity, payload,
                                              max_attempts)
                VALUES (%s, %s, %s, %s, %s)
                RETURNING delivery_id
                """,
                (fingerprint, kind, severity, Jsonb(payload or {}), max(1, max_attempts)),
            )
            return int(cur.fetchone()["delivery_id"])

    def claim_due_alert_deliveries(self, *, limit: int = 20,
                                   lease_seconds: int = 120) -> list[dict[str, Any]]:
        """原子领取到期交付（`SKIP LOCKED`，attempts+1 并把下次尝试推到租约后）。

        领取即计数：HTTP 外送在事务外执行，进程中途崩溃最多在租约到期后重试。
        """
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE alert_deliveries d
                   SET attempts = d.attempts + 1,
                       next_attempt_at = now() + make_interval(secs => %s)
                 WHERE d.delivery_id IN (
                     SELECT delivery_id FROM alert_deliveries
                      WHERE delivered_at IS NULL AND next_attempt_at <= now()
                      ORDER BY delivery_id
                      LIMIT %s
                      FOR UPDATE SKIP LOCKED
                 )
                RETURNING d.*
                """,
                (max(1, lease_seconds), max(1, limit)),
            )
            return cur.fetchall()

    def finish_alert_delivery(self, delivery_id: int) -> bool:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE alert_deliveries
                   SET delivered_at = now(), last_error = NULL
                 WHERE delivery_id = %s AND delivered_at IS NULL
                """,
                (delivery_id,),
            )
            return cur.rowcount > 0

    def fail_alert_delivery(self, delivery_id: int, *, delay_seconds: int,
                            error: Optional[str] = None) -> bool:
        """记录失败并按退避重排；`attempts >= max_attempts` 时置 infinity（放弃）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE alert_deliveries
                   SET last_error = %s,
                       next_attempt_at = CASE
                           WHEN attempts >= max_attempts THEN 'infinity'::timestamptz
                           ELSE now() + make_interval(secs => %s)
                       END
                 WHERE delivery_id = %s AND delivered_at IS NULL
                """,
                (error, max(0, delay_seconds), delivery_id),
            )
            return cur.rowcount > 0

    def retry_alert_delivery(self, delivery_id: int, *, delay_seconds: int = 0) -> bool:
        """人工重试（CLI）：把放弃/失败的行重新排期，attempts 清零。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE alert_deliveries
                   SET attempts = 0, last_error = NULL,
                       next_attempt_at = now() + make_interval(secs => %s)
                 WHERE delivery_id = %s AND delivered_at IS NULL
                """,
                (max(0, delay_seconds), delivery_id),
            )
            return cur.rowcount > 0

    def list_alert_deliveries(self, *, undelivered_only: bool = False,
                              limit: int = 100) -> list[dict[str, Any]]:
        clause = "WHERE delivered_at IS NULL" if undelivered_only else ""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT * FROM alert_deliveries {clause} "
                "ORDER BY delivery_id DESC LIMIT %s",
                (limit,),
            )
            return cur.fetchall()

    # ---- Worker 注册表（P1-3）----

    def register_worker(self, worker_id: str, *, version: Optional[str] = None,
                        hostname: Optional[str] = None) -> None:
        """注册 / 复活 worker（心跳 best-effort 的落点）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO workers (worker_id, version, hostname)
                VALUES (%s, %s, %s)
                ON CONFLICT (worker_id) DO UPDATE
                   SET version = EXCLUDED.version,
                       hostname = EXCLUDED.hostname,
                       status = 'active',
                       last_heartbeat_at = now(),
                       stopped_at = NULL,
                       updated_at = now()
                """,
                (worker_id, version, hostname),
            )

    def heartbeat_worker(self, worker_id: str, *, in_flight: int = 0,
                         current_run_id: Optional[str] = None,
                         status: str = "active") -> bool:
        """心跳（含在飞任务数 / 当前 run）；行不存在返回 False（调用方重注册）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                """
                UPDATE workers
                   SET last_heartbeat_at = now(),
                       in_flight = %s,
                       current_run_id = %s,
                       status = %s,
                       updated_at = now()
                 WHERE worker_id = %s
                RETURNING worker_id
                """,
                (in_flight, current_run_id, status, worker_id),
            )
            return cur.fetchone() is not None

    def mark_worker_status(self, worker_id: str, status: str) -> None:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE workers SET status = %s, "
                "stopped_at = CASE WHEN %s = 'stopped' THEN now() ELSE stopped_at END, "
                "updated_at = now() WHERE worker_id = %s",
                (status, status, worker_id),
            )

    def count_live_workers(self, *, within_seconds: int = 90) -> int:
        """最近 `within_seconds` 秒内有 active 心跳的 worker 数（readiness / 告警用）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT count(*) AS n FROM workers WHERE status = 'active' "
                "AND last_heartbeat_at > now() - make_interval(secs => %s)",
                (within_seconds,),
            )
            return int(cur.fetchone()["n"])

    def list_workers(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM workers ORDER BY last_heartbeat_at DESC LIMIT %s", (limit,))
            return cur.fetchall()

    def purge_stale_workers(self, *, days: int = 7) -> int:
        """清理 stopped/draining 且心跳早于 `days` 天的行（保留期供排障）。"""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM workers WHERE status IN ('stopped', 'draining') "
                "AND last_heartbeat_at < now() - make_interval(days => %s) RETURNING worker_id",
                (days,),
            )
            return len(cur.fetchall())
