"""申诉/复核编排（P2-5b / P0-5）。

- 提交：API 入口调用 `submit_appeal` —— 状态机行（`moderation_appeals`）+
  append-only 证据（`moderation_records.kind='appeal'`）由
  `store.create_appeal` **同一事务**写入；
- 复核：管理员 CLI 调用 `review_appeal`（claim → decide）—— `store.decide_appeal`
  是单事务：锁定申诉行、校验 reviewer 归属（必须是领取者）、更新申诉、
  accepted 时同事务把任务 `moderation_status` 置 `cleared`（导出闸放行；事件流
  历史仍按落库时脱敏）、写审核证据与审计。rejected 维持 `flagged`。
- SLA：`sla_due_at = now + DR_APPEAL_SLA_HOURS`（默认 72h），超期未决进入
  `appeal_sla_overdue` 告警判定（`count_appeals_overdue`）。
"""
from __future__ import annotations

import os
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

#: 复核决策集合（状态机终态）
DECISIONS = ("accepted", "rejected")


def sla_due_at(now: Optional[datetime] = None) -> datetime:
    try:
        hours = int(float(os.getenv("DR_APPEAL_SLA_HOURS", "72")))
    except ValueError:
        hours = 72
    return (now or datetime.now(UTC)) + timedelta(hours=max(1, hours))


def submit_appeal(store, *, user_id: Optional[str], message: str,
                  run_id: Optional[str] = None,
                  now: Optional[datetime] = None) -> dict[str, Any]:
    """创建申诉（调用方已完成鉴权 / 归属 / 重复校验）；证据与状态行同事务。"""
    appeal_id = uuid.uuid4().hex[:12]
    return store.create_appeal(
        appeal_id, message=message, run_id=run_id, user_id=user_id,
        sla_due_at=sla_due_at(now),
    )


def review_appeal(store, appeal_id: str, *, decision: str,
                  note: Optional[str] = None,
                  reviewer: Optional[str] = None) -> dict[str, Any]:
    """复核决策（CLI 用）；返回 `{ok, reason, appeal, run_cleared}`。

    先尝试领取 pending（并发下只有一个管理员成功）；若已被领取：
    - 同一 reviewer 已领取（claim → decide 分步场景）⇒ 继续决策；
    - 他人领取 / 已决策 / 不存在 ⇒ `ok=False`，不写任何状态。
    真正的决策与证据落库由 `store.decide_appeal` 单事务完成。
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision 仅支持 {DECISIONS}，收到：{decision!r}")
    claimed = store.claim_appeal(appeal_id, reviewer=reviewer)
    if claimed is None:
        current = store.get_appeal(appeal_id)
        if current is None:
            return {"ok": False, "reason": "not_found", "appeal": None, "run_cleared": False}
        if not (current["status"] == "reviewing"
                and current.get("reviewed_by") == reviewer):
            reason = (f"already_{current['status']}"
                      if current["status"] in DECISIONS else "claimed_by_other")
            return {"ok": False, "reason": reason, "appeal": current, "run_cleared": False}
    row = store.decide_appeal(appeal_id, decision=decision, note=note,
                              reviewer=reviewer)
    if row is None:
        current = store.get_appeal(appeal_id)
        reason = "not_found" if current is None else f"already_{current['status']}"
        return {"ok": False, "reason": reason, "appeal": current, "run_cleared": False}
    run_cleared = bool(decision == "accepted" and row.get("run_id"))
    return {"ok": True, "reason": decision, "appeal": row, "run_cleared": run_cleared}
