"""需求 36 Phase 1：HITL 交互协议（离线单测）。

不触网、不触库：单点映射一致性 / 回答校验 / 卡片与载荷 schema /
快照视图 / 事件脱敏 / 状态集合口径。真 PG 原子协议见 `test_hitl_interaction_pg.py`。
"""
from __future__ import annotations

from datetime import UTC, datetime

from web.backend import hitl
from web.backend.agui import INTERACTION_EVENT_TYPES
from web.backend.persistence import ACTIVE_STATUSES, AWAITING_STATUSES, OPEN_STATUSES


def test_status_and_event_maps_are_consistent():
    """交互类型 ↔ 状态 ↔ 事件名的单点映射必须自洽（防半接线）。"""
    assert hitl.AWAITING_STATUSES == tuple(hitl.STATUS_BY_KIND.values())
    assert set(hitl.KIND_BY_STATUS) == set(hitl.STATUS_BY_KIND.values())
    for kind in hitl.KINDS:
        assert kind in hitl.STATUS_BY_KIND
        assert kind in hitl.REQUEST_EVENT_BY_KIND
        assert kind in hitl.ANSWER_EVENT_BY_KIND
        assert kind in hitl.EXPIRED_EVENT_BY_KIND
        assert kind in hitl.MAX_ROUNDS
        assert kind in hitl.ALLOWED_ACTIONS


def test_interaction_events_all_registered():
    """所有交互事件名必须登记进 agui.INTERACTION_EVENT_TYPES（回放脱敏依赖该集合）。"""
    mapped = (set(hitl.REQUEST_EVENT_BY_KIND.values())
              | set(hitl.ANSWER_EVENT_BY_KIND.values())
              | set(hitl.EXPIRED_EVENT_BY_KIND.values()))
    assert mapped <= set(INTERACTION_EVENT_TYPES)
    assert len(INTERACTION_EVENT_TYPES) == len(set(INTERACTION_EVENT_TYPES))


def test_status_sets_disjoint_and_open_is_union():
    """等待态与执行/队列活跃态互斥；OPEN = ACTIVE + AWAITING（并发口径不含等待态）。"""
    assert set(ACTIVE_STATUSES) & set(AWAITING_STATUSES) == set()
    assert OPEN_STATUSES == ACTIVE_STATUSES + AWAITING_STATUSES
    assert "AWAITING_CLARIFICATION" not in ACTIVE_STATUSES


def test_validate_answer_matrix():
    assert hitl.validate_answer("clarification", "reply", "补充范围：近三个月") is None
    assert hitl.validate_answer("clarification", "reply", "   ") is not None
    assert hitl.validate_answer("clarification", "approve", "x") is not None
    assert hitl.validate_answer("plan_approval", "approve", "") is None
    assert hitl.validate_answer("plan_approval", "revise", "把范围收敛到近三个月") is None
    assert hitl.validate_answer("plan_approval", "revise", "  ") is not None
    assert hitl.validate_answer("plan_approval", "reply", "x") is not None
    too_long = "x" * (hitl.ANSWER_TEXT_LIMIT + 1)
    assert hitl.validate_answer("clarification", "reply", too_long) is not None


def test_build_card_schema():
    card = hitl.build_card(
        "clarification", interaction_id="itx-1", round_no=1,
        expires_at_iso="2026-10-14T00:00:00+00:00",
        content={"questions": ["研究范围？"]})
    assert card["interaction_id"] == "itx-1"
    assert card["kind"] == "clarification"
    assert card["round"] == 1
    assert card["max_rounds"] == 2
    assert card["allowed_actions"] == ["reply"]
    assert card["expires_at"].startswith("2026-10-14")
    assert card["content"]["questions"] == ["研究范围？"]


def test_resume_payloads():
    manual = hitl.build_resume_payload("clarification", action="reply", text="范围=近三个月")
    assert manual == {"kind": "clarification", "action": "reply",
                      "text": "范围=近三个月", "auto": False}
    auto = hitl.build_auto_continue_payload()
    assert auto["auto"] is True and auto["reason"] == "clarification_expired"
    revised = hitl.build_resume_payload("plan_approval", action="revise", text="减一条子问题")
    assert revised["kind"] == "plan_approval" and revised["action"] == "revise"


def test_interaction_view_only_for_awaiting():
    now = datetime.now(UTC)
    row = {
        "status": "AWAITING_CLARIFICATION",
        "interaction_id": "itx-9",
        "interaction_round": 1,
        "awaiting_since": now,
        "awaiting_expires_at": now,
        "interaction_payload": {"kind": "clarification"},
    }
    view = hitl.interaction_view(row)
    assert view is not None
    assert view["interaction_id"] == "itx-9"
    assert view["kind"] == "clarification"
    assert hitl.interaction_view({**row, "status": "RUNNING"}) is None
    assert hitl.interaction_view({**row, "status": "SUCCEEDED"}) is None
    assert hitl.interaction_view({**row, "status": "CANCELLED"}) is None


def test_redact_interaction_payload_keeps_protocol_fields():
    """flagged 回放：协议字段保留、正文清空、标记 under_review。"""
    from web.backend.main import _redact_interaction_payload

    scrubbed = _redact_interaction_payload({
        "interaction_id": "itx-1", "kind": "clarification", "action": "reply",
        "round": 1, "text": "机密回答", "content": {"questions": ["机密问题"]},
    })
    assert scrubbed["interaction_id"] == "itx-1"
    assert scrubbed["round"] == 1
    assert scrubbed["text"] == ""
    assert scrubbed["content"] == {}
    assert scrubbed["output_under_review"] is True
