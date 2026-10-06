"""数据保留自动清理（P2-2）。

政策（与 `docs/legal/privacy-policy.md` 对齐，可用环境变量收紧/放宽）：

| 表 | 时间列 | 默认保留 | 说明 |
|---|---|---|---|
| `run_events` | `created_at` | 30 天 | 事件明细（回放/审计溯源依赖 30 天） |
| `runs` | `finished_at` | 90 天 | 仅终态运行；`run_events` / `run_artifacts` 级联删除；S3 报告由桶生命周期 90 天删除 |
| `usage_ledger` | `created_at` | 180 天 | 成本对账 / 计费追溯 |
| `moderation_records` | `created_at` | 180 天 | 审核留痕（申诉窗口） |
| `audit_logs` | `at` | 180 天 | 安全审计 |

护栏：单表单次候选量 > `max(1000, 50% × 表总量)` 时**中止该表**并留审计
（`retention_guardrail`），防止时间列语义错误 / 时钟漂移导致误删全表；
支持 dry-run；删除按批（默认 5000 行/批，最多 20 批/表/日）避免长事务锁表。

每一步都写 `audit_logs`（`retention_purge`），dry-run 同样留痕。
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from .store import TERMINAL_STATUSES


def _env_days(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= 1 else default


def _terminal_where() -> str:
    quoted = ", ".join(f"'{status}'" for status in TERMINAL_STATUSES)
    return f"AND status IN ({quoted})"


@dataclass(frozen=True)
class RetentionPolicy:
    table: str
    time_column: str
    days: int
    where_extra: str = ""
    note: str = field(default="")


def policies() -> list[RetentionPolicy]:
    return [
        RetentionPolicy(
            "run_events", "created_at", _env_days("DR_RETENTION_EVENTS_DAYS", 30),
            note="事件明细 30 天（隐私政策）",
        ),
        RetentionPolicy(
            "runs", "finished_at", _env_days("DR_RETENTION_RUNS_DAYS", 90),
            where_extra=_terminal_where(),
            note="仅终态运行；事件/产物级联删除（隐私政策 90 天）",
        ),
        RetentionPolicy(
            "usage_ledger", "created_at", _env_days("DR_RETENTION_USAGE_DAYS", 180),
            note="成本对账 180 天",
        ),
        RetentionPolicy(
            "moderation_records", "created_at", _env_days("DR_RETENTION_MODERATION_DAYS", 180),
            note="审核留痕 180 天",
        ),
        RetentionPolicy(
            "moderation_appeals", "created_at", _env_days("DR_RETENTION_MODERATION_DAYS", 180),
            note="申诉/复核留痕 180 天（与审核记录同档；超期未决由 SLA 告警暴露）",
        ),
        RetentionPolicy(
            "audit_logs", "at", _env_days("DR_RETENTION_AUDIT_DAYS", 180),
            note="安全审计 180 天",
        ),
    ]


def run_retention(
    store,
    *,
    dry_run: bool = False,
    now: datetime | None = None,
    batch: int = 5000,
    max_batches: int = 20,
    guard_ratio: float = 0.5,
    guard_min_rows: int = 1000,
    guard_min_total: int = 100,
) -> dict:
    """执行一轮保留期清理；返回 `{dry_run, deleted_total, results: [...]}`。

    `store` 需实现 `count_table` / `count_before` / `purge_before` / `record_audit`
    （`RunStore` 与 `tests/fakes.FakeStore` 均满足）。
    """
    moment = now or datetime.now(UTC)
    results: list[dict] = []
    deleted_total = 0
    for policy in policies():
        total = store.count_table(policy.table)
        if total == 0:
            results.append({"table": policy.table, "total": 0, "candidates": 0,
                            "deleted": 0, "aborted": False})
            continue
        before = moment - timedelta(days=policy.days)
        candidates = store.count_before(
            policy.table, policy.time_column, before, where_extra=policy.where_extra)
        aborted = False
        if (
            total > guard_min_total
            and candidates > guard_min_rows
            and candidates > total * guard_ratio
        ):
            aborted = True
            store.record_audit(
                "retention_guardrail",
                detail={
                    "table": policy.table,
                    "candidates": candidates,
                    "total": total,
                    "retention_days": policy.days,
                    "ratio": round(candidates / max(1, total), 3),
                },
            )
        deleted = 0
        if not aborted:
            if dry_run:
                deleted = candidates
            else:
                for _ in range(max_batches):
                    purged = store.purge_before(
                        policy.table, policy.time_column, before,
                        where_extra=policy.where_extra, batch=batch,
                    )
                    deleted += purged
                    if purged < batch:
                        break
            store.record_audit(
                "retention_purge",
                detail={
                    "table": policy.table,
                    "deleted": deleted,
                    "dry_run": dry_run,
                    "retention_days": policy.days,
                },
            )
        deleted_total += deleted
        results.append({
            "table": policy.table,
            "total": total,
            "candidates": candidates,
            "deleted": deleted,
            "aborted": aborted,
            "retention_days": policy.days,
        })
    return {"dry_run": dry_run, "deleted_total": deleted_total, "results": results}
