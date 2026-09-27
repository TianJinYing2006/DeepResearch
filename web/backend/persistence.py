"""终局落库的唯一实现（P3 起由 RunManager 与 Worker 共享）。

为什么抽出来：内存态执行（P1/P2）与 Worker 执行（P3）必须写出**同一套**状态映射与产物，
否则同一种停止原因会在库里产生两种含义（例如取消记成 `cancelled` 与 `user_cancelled` 并存）。

调用方负责错误处理：本模块的函数会抛异常（数据库错误），由调用方按各自语义处理
（RunManager 记入运行画像 `persistence_error`；Worker 记录日志并让租约兜底）。
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any, Optional

from .agui import RUN_ERROR
from .export import build_export_payload

#: 允许迁移到终局的当前状态
ACTIVE_STATUSES = ("CREATED", "QUEUED", "RUNNING", "CANCEL_REQUESTED")

#: 传输层 stop_reason → 任务状态（§5.9.1；预算停止不伪装成 TIMED_OUT）
_STATUS_BY_STOP_REASON = {
    "completed": "SUCCEEDED",
    "cancelled": "CANCELLED",
    "user_cancelled": "CANCELLED",
    "timeout": "TIMED_OUT",
    "budget_exceeded": "CANCELLED",
}


def persist_terminal(
    store,
    run_id: str,
    seq: Optional[int],
    event_type: str,
    payload: dict[str, Any],
    *,
    result: Optional[dict[str, Any]] = None,
    report: Optional[str] = None,
    meta: Optional[dict[str, Any]] = None,
    topic: str = "",
) -> bool:
    """写终局事件 + 状态 + 产物（**同一事务**，P0-6）；任一失败即抛异常。

    `seq` 传 None 时由数据库分配序号（Worker 是单 run 唯一写者）；
    传显式序号时与内存帧号对齐（RunManager）。

    Returns:
        是否完成迁移。``False`` = 状态已被清扫 / 强制收口抢先（整体回滚，
        不写半成品；调用方据此跳过输出标记等后续动作）。
    """
    if event_type == RUN_ERROR:
        new_status, stop_reason = "FAILED", "error"
    else:
        stop_reason = str(payload.get("stop_reason") or "completed")
        if stop_reason == "cancelled":
            # 传输层值是 cancelled；库里按 §5.9.1 记 user_cancelled（区分停止原因）。
            stop_reason = "user_cancelled"
        new_status = _STATUS_BY_STOP_REASON.get(stop_reason, "SUCCEEDED")
    fields: dict[str, Any] = {"stop_reason": stop_reason, "finished_at": datetime.now(UTC)}
    if meta:
        fields["research_status"] = meta.get("run_status")
        fields["token_used"] = meta.get("token_used")
        fields["cost_estimate_cny"] = meta.get("cost_estimate_cny")
        fields["budget_used_cny"] = meta.get("budget_used_cny")
    artifacts: dict[str, str] = {}
    if report:
        artifacts["report_md"] = report
    if result is not None and meta is not None:
        export = build_export_payload(run_id=run_id, topic=topic, meta=meta, result=result)
        artifacts["export_json"] = json.dumps(export, ensure_ascii=False)
    return store.finalize_run(
        run_id,
        event_type=event_type,
        payload=payload,
        sequence=seq,
        new_status=new_status,
        allowed_from=ACTIVE_STATUSES,
        fields={key: value for key, value in fields.items() if value is not None},
        artifacts=artifacts,
    )


def persist_forced(store, run_id: str, seq: Optional[int], payload: dict[str, Any],
                   reason: str) -> bool:
    """传输层强制收口落库（同一事务；与内存语义一致：不写 research_status）。"""
    return store.finalize_run(
        run_id,
        event_type=RUN_ERROR,
        payload=payload,
        sequence=seq,
        new_status="CANCELLED" if reason == "cancel" else "TIMED_OUT",
        allowed_from=ACTIVE_STATUSES,
        fields={
            "stop_reason": "user_cancelled" if reason == "cancel" else "timeout",
            "finished_at": datetime.now(UTC),
        },
    )
