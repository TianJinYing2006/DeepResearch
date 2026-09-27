"""申诉/复核编排（P2-5b）。

- 提交：API 入口调用 `submit_appeal` —— 状态机行（`moderation_appeals`）+
  append-only 证据（`moderation_records.kind='appeal'`）同一入口写入；
- 复核：管理员 CLI 调用 `review_appeal`（claim → decide）—— accepted 时把
  任务 `moderation_status` 置 `cleared`（导出闸放行；事件流历史仍按落库时脱敏），
  rejected 维持 `flagged`；两个方向都留 `moderation_records` 与 `audit_logs`。
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
    """创建申诉（调用方已完成鉴权 / 归属 / 重复校验）。"""
    appeal_id = uuid.uuid4().hex[:12]
    row = store.create_appeal(
        appeal_id, message=message, run_id=run_id, user_id=user_id,
        sla_due_at=sla_due_at(now),
    )
    store.record_moderation(
        "appeal", user_id=user_id, run_id=run_id,
        detail={"message": message, "appeal_id": appeal_id},
    )
    return row


def review_appeal(store, appeal_id: str, *, decision: str,
                  note: Optional[str] = None,
                  reviewer: Optional[str] = None) -> dict[str, Any]:
    """复核决策（CLI 用）；返回 `{ok, reason, appeal, run_cleared}`。

    `claim` 与 `decide` 的组合：先尝试领取 pending（竞争下只有一个管理员成功），
    再决策；已决策/不存在返回 `ok=False` 与原因，不抛异常。
    """
    if decision not in DECISIONS:
        raise ValueError(f"decision 仅支持 {DECISIONS}，收到：{decision!r}")
    store.claim_appeal(appeal_id, reviewer=reviewer)
    row = store.decide_appeal(appeal_id, decision=decision, note=note,
                              reviewer=reviewer)
    if row is None:
        current = store.get_appeal(appeal_id)
        reason = "not_found" if current is None else f"already_{current['status']}"
        return {"ok": False, "reason": reason, "appeal": current, "run_cleared": False}
    run_cleared = False
    if decision == "accepted" and row.get("run_id"):
        store.set_moderation_status(row["run_id"], "cleared")
        run_cleared = True
    store.record_moderation(
        f"appeal_{decision}", user_id=row.get("user_id"), run_id=row.get("run_id"),
        detail={"appeal_id": appeal_id, "note": note, "reviewed_by": reviewer},
    )
    try:  # best-effort 审计（与其它管理动作一致：审计失败不打断复核）
        store.record_audit(
            f"appeal_{decision}", target_type="moderation_appeal", target_id=appeal_id,
            detail={"run_id": row.get("run_id"), "reviewed_by": reviewer},
        )
    except Exception:  # noqa: BLE001
        pass
    return {"ok": True, "reason": decision, "appeal": row, "run_cleared": run_cleared}
