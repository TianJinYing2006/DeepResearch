"""注销 outbox 执行器（P0-7）：把外部系统清理从请求路径移到 Worker。

设计（业内实践：erasure saga + transactional outbox）：

- 注销登记（`request_account_deletion`）在同一事务里写台账 + outbox + 删用户；
- 本模块负责 outbox 的**执行半场**：领取（`FOR UPDATE SKIP LOCKED` + 租约）→
  执行目标（当前仅 Qdrant）→ **验证**（点数必须归零）→ done；
- 失败指数退避（30s 起、封顶 1h）；重试耗尽 ⇒ `abandoned`（**大声**记录，
  由 `/api/ops/alerts` 与 CLI `deletion-list` 暴露，绝不静默丢弃）；
- 幂等：重复执行 = 重复删同一 user 的向量 + 验证；"已经不存在"视为成功。

挂在 Worker 主循环的清扫周期里执行（`process_deletions_once`），
也可用 `python -m web.backend.admin process-deletions` 手工跑一批。
"""
from __future__ import annotations

import sys
from typing import Any, Optional

from .store import RunStore

DEFAULT_LEASE_SECONDS = 120
DEFAULT_MAX_ATTEMPTS = 5
DEFAULT_BATCH = 5
DEFAULT_BACKOFF_CAP_SECONDS = 3600


def _log(message: str) -> None:
    print(f"[deletion] {message}", file=sys.stderr, flush=True)


def process_deletions_once(
    store: RunStore,
    *,
    vector_store: Optional[Any] = None,
    claimed_by: str = "deletion-worker",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    batch: int = DEFAULT_BATCH,
) -> dict[str, int]:
    """处理一批到期的注销 outbox。

    Returns:
        `{claimed, done, retried, abandoned}`（供日志 / 测试断言）。
    """
    claimed = store.claim_deletion_outbox(claimed_by, lease_seconds, batch)
    summary = {"claimed": len(claimed), "done": 0, "retried": 0, "abandoned": 0}
    for item in claimed:
        try:
            _execute_target(item, vector_store)
        except Exception as exc:  # noqa: BLE001 —— 失败必须进入退避/放弃流程
            error = f"{type(exc).__name__}: {exc}"[:300]
            backoff = min(
                30 * (2 ** max(0, int(item["attempts"]) - 1)),
                DEFAULT_BACKOFF_CAP_SECONDS,
            )
            new_status = store.mark_deletion_retry(
                item["id"], error, backoff_seconds=backoff, max_attempts=max_attempts)
            if new_status == "abandoned":
                summary["abandoned"] += 1
                _log(f"ABANDONED outbox={item['id']} request={item['request_id']} "
                     f"target={item['target']}: {error}")
            else:
                summary["retried"] += 1
                _log(f"retry outbox={item['id']} attempt={item['attempts']}: {error}")
            continue
        store.mark_deletion_done(item["id"])
        summary["done"] += 1
    return summary


def _execute_target(item: dict[str, Any], vector_store: Optional[Any]) -> None:
    """执行单条 outbox；失败抛异常，由调用方进入重试流程。"""
    target = item["target"]
    if target != "qdrant":
        raise RuntimeError(f"unknown deletion target: {target}")
    user_id = (item.get("payload") or {}).get("user_id")
    if not user_id:
        raise RuntimeError("outbox payload missing user_id")

    store = vector_store
    if store is None:
        from research_engine.rag.store import VectorStore

        store = VectorStore()
    reason = store.unavailable_reason
    if reason:
        raise RuntimeError(f"qdrant unavailable: {reason}")
    store.delete_by_user(user_id, wait=True)
    remaining = store.count_by_user(user_id)
    if remaining:
        raise RuntimeError(f"qdrant still has {remaining} points for user {user_id}")
