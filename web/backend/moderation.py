"""内容安全最小实现（P7-A / P2-5a / P0-3 / P0-4）：provider 化预检 + 不可变审核决定。

**诚实边界（必须与文档一致）**：默认 provider 是**本地规则预检**，不是内容安全服务。
它只能拦住显式命中词表的内容；正式对外（L3-B/C）必须接入有资质的审核服务并保留人工复核，
本模块的价值在于：① 输入侧的硬拒绝入口；② 输出侧留痕，让申诉/复核有证据。

P0-3/P0-4（一次扫描、一个不可变决定）：

- `evaluate_output(report)` 只调用一次 `scan_text` + 泄漏检测，产出
  :class:`ModerationDecision`（含 provider / policy / degraded / failure_reason /
  matches / leaks / decision_id）；终局落库与事件脱敏都消费这**同一份决定**，
  不再「终局一次、flag_report 再一次」重复扫描/重复计费；
- provider 不可用时按 `DR_MODERATION_DEGRADED_POLICY` 决定语义：
  `quarantine`（默认）⇒ `under_review` 隔离待审；`fail_closed` ⇒ `blocked`；
  `allow` ⇒ 放行但必须留 degraded 记录（运维告警），绝不静默；
- `apply_output_gate(payload, decision)` 仅做**递归脱敏**（不再扫描）：
  任何承载正文的键（report / raw_report / markdown / body …）都被清空，
  防止「新增字段漏脱敏」；
- 兼容旧接口：`scan` / `flag_report` 保留（后者为 legacy 包装，内部生成决定）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Optional, Union
from uuid import uuid4

from .injection_guard import scan_output_leak
from .moderation_providers import (  # noqa: F401 —— 兼容旧导入路径（load_blocklist / scan）
    DEFAULT_BLOCKLIST,
    ModerationResult,
    active_provider,
    degraded_policy,
    keyword_scan,
    load_blocklist,
    register_provider,
    scan_text,
    set_provider,
)

MAX_TOPIC_LENGTH = 1000
MAX_INSTRUCTIONS_LENGTH = 2000
MAX_APPEAL_LENGTH = 2000

#: 决策 → `runs.moderation_status`（allow 不写）
STATUS_BY_DECISION = {
    "flag": "flagged",
    "quarantine": "under_review",
    "block": "blocked",
}

#: 可能承载报告正文的键名（P0-12：公开 payload 递归脱敏；新增字段必须在此登记）
REPORT_PAYLOAD_KEYS = frozenset({
    "report", "raw_report", "report_md", "report_text", "full_text",
    "markdown", "body", "summary",
})


def redact_public_payload(value: Any) -> Any:
    """递归清空公开载荷中的正文键（返回新对象，不原地改）。

    只对字符串值生效：`{"report": ""}` 这类占位保留；结构 / 数字 / None 原样。
    """
    if isinstance(value, dict):
        return {
            key: ("" if key in REPORT_PAYLOAD_KEYS and isinstance(item, str)
                  else redact_public_payload(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact_public_payload(item) for item in value]
    return value


@dataclass(frozen=True)
class ModerationDecision:
    """不可变审核决定（P0-4）：一次 provider 调用的完整证据，供落库 / 导出 / 申诉溯源。

    不重新扫描正文：终局、回放、导出闸只读取这份决定（或 `runs.moderation_status`）。
    """

    decision: str                       # allow | flag | quarantine | block
    provider: str
    source: str                         # input | output
    matches: tuple[str, ...] = ()
    leaks: tuple[str, ...] = ()
    risk_level: str = "none"            # none | medium | high
    degraded: bool = False
    failure_reason: str = ""
    policy: str = ""                    # provider 降级时生效的策略
    requested_provider: str = ""
    policy_version: str = "p2-5a+0015"
    decision_id: str = field(default_factory=lambda: uuid4().hex[:12])
    created_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    @property
    def run_status(self) -> Optional[str]:
        """映射到 `runs.moderation_status`；`allow` 返回 None（不写状态）。"""
        return STATUS_BY_DECISION.get(self.decision)

    @property
    def blocked(self) -> bool:
        return self.decision != "allow"

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision_id": self.decision_id,
            "decision": self.decision,
            "source": self.source,
            "provider": self.provider,
            "requested_provider": self.requested_provider,
            "policy": self.policy,
            "policy_version": self.policy_version,
            "risk_level": self.risk_level,
            "degraded": self.degraded,
            "failure_reason": self.failure_reason,
            "matches": list(self.matches),
            "leaks": list(self.leaks),
            "created_at": self.created_at,
        }


def scan(text: str, blocklist=None) -> list[str]:
    """本地词表命中（兼容旧接口）：显式 `blocklist` 直查，否则走当前 provider。"""
    if blocklist is not None:
        return keyword_scan(text, blocklist=blocklist)
    return scan_text(text).matches


def _degraded_decision(result: ModerationResult, decision: str, *,
                       source: str) -> ModerationDecision:
    return ModerationDecision(
        decision=decision,
        provider=result.provider,
        source=source,
        matches=tuple(result.matches),
        risk_level="high",
        degraded=True,
        failure_reason=result.failure_reason,
        policy=result.policy or degraded_policy(),
        requested_provider=result.requested_provider,
    )


def evaluate_output(report: Optional[str]) -> ModerationDecision:
    """输出侧**唯一一次**判定：词表命中 / 系统提示泄漏 / provider 降级 → 不可变决定。"""
    text = report or ""
    result = scan_text(text)
    leaks = tuple(scan_output_leak(text))
    matches = tuple(result.matches)
    if matches or leaks:
        return ModerationDecision(
            decision="flag", provider=result.provider, source="output",
            matches=matches, leaks=leaks,
            risk_level="high" if result.degraded else "medium",
            degraded=result.degraded, failure_reason=result.failure_reason,
            policy=result.policy, requested_provider=result.requested_provider,
        )
    if result.degraded:
        policy = result.policy or degraded_policy()
        decision = {"quarantine": "quarantine", "fail_closed": "block",
                    "allow": "allow"}[policy]
        return _degraded_decision(result, decision, source="output")
    return ModerationDecision(decision="allow", provider=result.provider, source="output")


def evaluate_input(text: str) -> ModerationDecision:
    """输入侧判定：命中 ⇒ flag（调用方拒绝）；provider 降级 ⇒ 按策略隔离/阻断。"""
    result = scan_text(text or "")
    if result.matches:
        return ModerationDecision(
            decision="flag", provider=result.provider, source="input",
            matches=tuple(result.matches),
            risk_level="high" if result.degraded else "medium",
            degraded=result.degraded, failure_reason=result.failure_reason,
            policy=result.policy, requested_provider=result.requested_provider,
        )
    if result.degraded:
        policy = result.policy or degraded_policy()
        decision = {"quarantine": "quarantine", "fail_closed": "block",
                    "allow": "allow"}[policy]
        return _degraded_decision(result, decision, source="input")
    return ModerationDecision(decision="allow", provider=result.provider, source="input")


def apply_output_gate(
    payload: dict, report_or_decision: Union[Optional[str], ModerationDecision],
) -> list[str]:
    """输出侧统一闸（P0-4 / P0-12）：按决定递归脱敏事件流 / 传输层，**产物原文不删**。

    必须在**发帧与落库之前**调用：这样 `run_events`（SSE 回放的权威来源）
    与实时帧都不携带完整正文；完整原文只留在 `run_artifacts.report_md`，
    供管理员 / 审核人员经 CLI 复核（普通用户导出由 API 闸拒绝）。

    兼容旧调用：第二参数传字符串时内部生成决定（仅限调用方没有 decision 的场景）。
    返回命中项（命中词 / 泄漏标记 / `moderation_degraded`）；放行返回空列表。
    """
    decision = (report_or_decision if isinstance(report_or_decision, ModerationDecision)
                else evaluate_output(report_or_decision))
    if not decision.blocked:
        return []
    sanitized = redact_public_payload(payload)
    payload.clear()
    payload.update(sanitized)
    payload["output_under_review"] = True
    payload["moderation_decision_id"] = decision.decision_id
    result = list(decision.matches) + list(decision.leaks)
    if decision.degraded and not result:
        result.append("moderation_degraded")
    return result


def flag_report(store, run_id: str, user_id: Optional[str],
                report: Optional[str]) -> list[str]:
    """legacy 输出标记（保留兼容）：内部生成决定后落记录 / 置状态。

    新链路（runner / worker）统一走 `evaluate_output` + `persist_terminal(moderation=...)`，
    本函数仅供旧调用与回归测试；非命中且未降级时不写任何记录。
    """
    decision = evaluate_output(report)
    if not decision.blocked:
        return []
    store.record_moderation(
        "output_flagged", user_id=user_id, run_id=run_id,
        detail={"matches": list(decision.matches)[:10],
                "output_leaks": list(decision.leaks)[:5],
                "provider": decision.provider,
                "decision_id": decision.decision_id},
    )
    store.set_moderation_status(run_id, decision.run_status or "flagged")
    return list(decision.matches) + list(decision.leaks) or ["moderation_degraded"]
