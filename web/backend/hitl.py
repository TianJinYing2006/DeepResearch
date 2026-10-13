"""需求 36：HITL 交互协议单一来源（Phase 1）。

本模块集中定义：**交互类型 ↔ 等待态 ↔ 事件名 ↔ 轮次上限 ↔ 载荷 schema**。
store（PG 原子协议）、API（回答校验）、worker（挂起/恢复，Phase 2）三处都从这里取口径，
不得各自手写字符串（沿用 failure_reasons / ACTIVE_STATUSES 的「单点定义」纪律）。

Phase 1 边界：只落协议与 schema（迁移 0023 + store 原子协议 + 回答接口）；
图的 interrupt 接入在 Phase 2，因此本模块当前由 store / API / 测试消费。
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Optional

from .agui import (
    CLARIFY_ANSWERED,
    CLARIFY_EXPIRED,
    CLARIFY_REQUESTED,
    PLAN_DECISION,
    PLAN_EXPIRED,
    PLAN_PROPOSED,
)

KIND_CLARIFICATION = "clarification"
KIND_PLAN_APPROVAL = "plan_approval"
KINDS = (KIND_CLARIFICATION, KIND_PLAN_APPROVAL)

#: 交互类型 ↔ 等待态（runs.status）
STATUS_BY_KIND = {
    KIND_CLARIFICATION: "AWAITING_CLARIFICATION",
    KIND_PLAN_APPROVAL: "AWAITING_PLAN_APPROVAL",
}
KIND_BY_STATUS = {status: kind for kind, status in STATUS_BY_KIND.items()}
AWAITING_STATUSES = tuple(STATUS_BY_KIND.values())

#: 交互类型 ↔ 事件名
REQUEST_EVENT_BY_KIND = {
    KIND_CLARIFICATION: CLARIFY_REQUESTED,
    KIND_PLAN_APPROVAL: PLAN_PROPOSED,
}
ANSWER_EVENT_BY_KIND = {
    KIND_CLARIFICATION: CLARIFY_ANSWERED,
    KIND_PLAN_APPROVAL: PLAN_DECISION,
}
EXPIRED_EVENT_BY_KIND = {
    KIND_CLARIFICATION: CLARIFY_EXPIRED,
    KIND_PLAN_APPROVAL: PLAN_EXPIRED,
}

#: 轮次上限（澄清 ≤2 轮 / 审批修改 ≤2 轮；需求 36 §3）
MAX_ROUNDS = {KIND_CLARIFICATION: 2, KIND_PLAN_APPROVAL: 2}

#: 回答动作 → 允许的交互类型（API 校验；approve/revise 仅审批，reply 仅澄清）
ALLOWED_ACTIONS = {
    KIND_CLARIFICATION: ("reply",),
    KIND_PLAN_APPROVAL: ("approve", "revise"),
}

#: 载荷长度上限（§12.2「设长度上限」；超限由 API 拒绝，不静默截断用户输入）
ANSWER_TEXT_LIMIT = 2000
#: 卡片内容总量上限（字符；超限降级为 preview —— 挂起事务的持久化护栏）
CARD_TEXT_LIMIT = 8000
#: 卡片内容单个字符串叶子上限（问题/摘要等长文本先按叶截断）
CARD_STRING_LIMIT = 1000


def sanitize_card_content(content: Any, *, limit: int = CARD_TEXT_LIMIT) -> dict:
    """卡片内容长度上限（§12.2）：字符串叶子截断 + 容器限量；总量仍超限 ⇒ 降级 preview。

    单一入口：`build_card` 调用本函数 —— 挂起事务无论由谁发起，落库卡片都受同一上限约束。
    """
    def _walk(value: Any) -> Any:
        if isinstance(value, str):
            return value[:CARD_STRING_LIMIT]
        if isinstance(value, list):
            return [_walk(item) for item in value[:20]]
        if isinstance(value, dict):
            return {str(key)[:100]: _walk(val) for key, val in list(value.items())[:20]}
        return value

    walked = _walk(content or {})
    try:
        raw = json.dumps(walked, ensure_ascii=False)
    except (TypeError, ValueError):
        return {"truncated": True}
    if len(raw) > limit:
        return {"truncated": True, "preview": raw[:limit]}
    return walked if isinstance(walked, dict) else {"value": walked}


def new_interaction_id() -> str:
    """交互稳定 ID（回答幂等键 + 旧卡片冲突判定；一次挂起对应一个）。"""
    return f"itx-{uuid.uuid4().hex[:12]}"


def expires_at(ttl_seconds: int, *, now: Optional[datetime] = None) -> datetime:
    """等待态到期时间（TTL 由调用方按配置给出；默认值在 Phase 2 随开关落地）。"""
    return (now or datetime.now(UTC)) + timedelta(seconds=ttl_seconds)


def build_card(
    kind: str,
    *,
    interaction_id: str,
    round_no: int,
    expires_at_iso: str,
    content: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    """交互卡片载荷（挂起事务写入 `interaction_payload` 与请求事件，二者同一份）。

    字段（§12.2：稳定 ID / 轮次 / 状态版本 / 过期时间 / 允许动作）：
      interaction_id / kind / round / max_rounds / expires_at / allowed_actions / content
    `content` 为图在 interrupt 时给出的展示载荷（问题清单 / 计划摘要），不含可执行内容；
    落库前经 `sanitize_card_content` 统一施加长度上限。
    """
    return {
        "interaction_id": interaction_id,
        "kind": kind,
        "round": int(round_no),
        "max_rounds": MAX_ROUNDS.get(kind, 1),
        "expires_at": expires_at_iso,
        "allowed_actions": list(ALLOWED_ACTIONS.get(kind, ())),
        "content": sanitize_card_content(content),
    }


def validate_answer(kind: str, action: str, text: str) -> Optional[str]:
    """回答合法性校验；返回错误消息（None = 合法）。API 层直接转 4xx。"""
    allowed = ALLOWED_ACTIONS.get(kind, ())
    if action not in allowed:
        return f"action 不适用于 {kind}：allowed={list(allowed)}"
    if len(text) > ANSWER_TEXT_LIMIT:
        return f"回答文本超长（上限 {ANSWER_TEXT_LIMIT} 字符）"
    if kind == KIND_CLARIFICATION and action == "reply" and not text.strip():
        return "澄清回答不能为空"
    if kind == KIND_PLAN_APPROVAL and action == "revise" and not text.strip():
        return "修改计划的反馈不能为空"
    return None


def build_answer_event_payload(
    kind: str, *, interaction_id: str, round_no: int, action: str, text: str,
) -> dict[str, Any]:
    """回答事件载荷（落 run_events；flagged 回放时由 main 侧脱敏）。"""
    return {
        "interaction_id": interaction_id,
        "kind": kind,
        "round": int(round_no),
        "action": action,
        "text": text[:ANSWER_TEXT_LIMIT],
    }


def build_resume_payload(kind: str, *, action: str, text: str) -> dict[str, Any]:
    """resume payload（存 `runs.interaction_answer`；Phase 2 由 worker 注入图恢复）。

    - clarification：`{"action": "reply", "text": ...}`——作为 user_scope 并入 research_spec；
    - plan_approval：`{"action": "approve"|"revise", "text": ...}`——approve 直接放行，
      revise 的 text 作为计划修订反馈。
    """
    return {
        "kind": kind,
        "action": action,
        "text": text[:ANSWER_TEXT_LIMIT],
        "auto": False,
    }


def build_auto_continue_payload() -> dict[str, Any]:
    """澄清到期自动继续的 resume payload（§12.2 / TBD-3：显式产品策略 + 留痕）。"""
    return {
        "kind": KIND_CLARIFICATION,
        "action": "reply",
        "text": "",
        "auto": True,
        "reason": "clarification_expired",
    }


def interaction_view(row: dict[str, Any]) -> Optional[dict[str, Any]]:
    """任务库行 → 快照里的「当前交互」（非等待态返回 None）。

    只透出协议字段；展示内容在 `card.content`（挂起时写入的卡片原件）。
    """
    status = row.get("status")
    kind = KIND_BY_STATUS.get(status)
    if kind is None:
        return None
    return {
        "interaction_id": row.get("interaction_id"),
        "kind": kind,
        "round": row.get("interaction_round"),
        "awaiting_since": _iso(row.get("awaiting_since")),
        "expires_at": _iso(row.get("awaiting_expires_at")),
        "card": row.get("interaction_payload") or {},
    }


def _iso(value: Any) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None
