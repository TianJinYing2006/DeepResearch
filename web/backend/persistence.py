"""终局落库的唯一实现（P3 起由 RunManager 与 Worker 共享）。

为什么抽出来：内存态执行（P1/P2）与 Worker 执行（P3）必须写出**同一套**状态映射与产物，
否则同一种停止原因会在库里产生两种含义（例如取消记成 `cancelled` 与 `user_cancelled` 并存）。

调用方负责错误处理：本模块的函数会抛异常（数据库错误），由调用方按各自语义处理
（RunManager 记入运行画像 `persistence_error`；Worker 记录日志并让租约兜底）。
"""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Optional

from .agui import RUN_ERROR
from .export import build_export_payload
from .moderation import ModerationDecision

if TYPE_CHECKING:  # 仅类型标注：store 运行期 import 本模块，运行期互相 import 会成环
    from .store import RunOwnership

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
    moderation: Optional[ModerationDecision] = None,
    moderation_status: Optional[str] = None,
    egress: Optional[dict[str, Any]] = None,
    owner: Optional[RunOwnership] = None,
) -> bool:
    """写终局事件 + 状态 + 产物 + 审核证据（**同一事务**，P0-6 / P0-4）；任一失败即抛异常。

    `seq` 传 None 时由数据库分配序号（Worker 是单 run 唯一写者）；
    传显式序号时与内存帧号对齐（RunManager）。

    `moderation`（P0-4）：`ModerationDecision` 是本次运行**唯一一次**审核调用的不可变
    结果；其 `runs.moderation_status` 与 `moderation_records`（kind='output_decision'）
    与终局同事务写入，关闭「终局已落但审核状态/证据未落」的导出窗口。
    `moderation_status` 为 legacy 参数（仅写状态，不写证据），供旧调用兼容。

    `owner`（F04）：Worker 传入本次执行所有权（`worker_id + attempt`）——终局写入
    与归属校验同条件提交；旧执行者的迟到终局整体回滚（返回 `False`）。产物对象键
    同时按 attempt 版本化，避免事务提交前对共享对象键的覆盖。

    Returns:
        是否完成迁移。``False`` = 状态已被清扫 / 强制收口抢先 / 所有权已失效
        （整体回滚，不写半成品；调用方据此跳过输出标记等后续动作）。
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
    moderation_record: Optional[dict[str, Any]] = None
    if moderation is not None:
        status = moderation.run_status
        if status:
            fields["moderation_status"] = status
        moderation_record = {"kind": "output_decision", "detail": moderation.to_dict()}
    elif moderation_status:
        fields["moderation_status"] = moderation_status
    # F04：产物对象键按 attempt 版本化（`runs/{run_id}/attempt-{n}/{kind}`）——
    # 旧执行者在事务提交前上传的对象不会覆盖新执行者的合法产物（读取走库内 object_key）。
    attempt = owner.attempt if owner is not None else 1
    artifacts: dict[str, dict[str, Any]] = {}
    if report:
        artifacts["report_md"] = _artifact_payload(
            run_id, "report_md", report, "text/markdown; charset=utf-8", attempt=attempt)
    if result is not None and meta is not None:
        export = build_export_payload(run_id=run_id, topic=topic, meta=meta, result=result,
                                      egress=egress)
        artifacts["export_json"] = _artifact_payload(
            run_id, "export_json", json.dumps(export, ensure_ascii=False), "application/json",
            attempt=attempt)
    return store.finalize_run(
        run_id,
        event_type=event_type,
        payload=payload,
        sequence=seq,
        new_status=new_status,
        allowed_from=ACTIVE_STATUSES,
        fields={key: value for key, value in fields.items() if value is not None},
        artifacts=artifacts,
        moderation=moderation_record,
        owner=owner,
    )


def _artifact_payload(run_id: str, kind: str, body: str,
                      content_type: str, attempt: int = 1) -> dict[str, Any]:
    """P1-6：配置对象存储时先写 S3（元数据随终局事务落库）；失败回落 PG（报告仍可用）。

    F04：对象键含 attempt（不可变键）——同一 run 的不同执行者各写各的键，晚到的
    旧执行者上传不会覆盖新执行者的产物；读取端用库内 `object_key`，不依赖键格式。
    """
    from .objectstore import get_object_store

    object_store = get_object_store()
    if object_store is not None:
        try:
            info = object_store.put_text(f"runs/{run_id}/attempt-{attempt}/{kind}", body,
                                         content_type=content_type)
            return {"body": "", "storage": "s3", "object_key": info["key"],
                    "sha256": info["sha256"], "size_bytes": info["size_bytes"]}
        except Exception as exc:  # noqa: BLE001 —— 对象存储抖动不丢报告
            print(f"[objectstore] put failed, fallback to db: "
                  f"{type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
    return {"body": body, "storage": "db"}


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
