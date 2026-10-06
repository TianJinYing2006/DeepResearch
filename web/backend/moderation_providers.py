"""审核 provider 抽象（P2-5a）：统一接口 + 本地规则实现 + 注册与降级。

**诚实边界**：本地关键词预检只拦显式命中词表；正式对外（L3-B/C）必须接有资质的
审核服务并保留人工复核。本模块只固化接口与降级语义，外部 provider（如阿里云
内容安全）留到接入阶段实现，不在本 PR 引入任何 SDK 或网络调用。

降级策略（P0-3，`DR_MODERATION_DEGRADED_POLICY`）——provider 不可用时**不再一律放行**：

- `quarantine`（默认，生产推荐）：回退本地词表继续扫描，但结果标记 `degraded`，
  调用方按「隔离待审」处理（输出不对外、任务置 `under_review`、告警）；
- `fail_closed`：结果标记 `degraded`，调用方直接阻断（输出置 `blocked`，输入 503）；
- `allow`：仅限低风险内测；必须留 degraded 决策记录 + 运维告警，不得静默。

`DR_MODERATION_PROVIDER` 选择已注册 provider（默认 `local_rules`）；名字未知
（含未实现的 `aliyun`）或 provider 抛异常 ⇒ 记 WARNING、回退 `local_rules`，
并把 `degraded=True` / `failure_reason` / `requested_provider` 带进结果，供上层
生成**不可变决策**（`moderation.evaluate_output`）与审计留痕。
统一返回 `ModerationResult`（provider / matches / detail / degraded），provider 与
degraded 留痕到 `moderation_records`，供审计与申诉溯源。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol

_log = logging.getLogger("deepresearch.moderation")

#: 默认空词表：宁可少拦，也不误伤；实际词表由部署方按属地要求配置
DEFAULT_BLOCKLIST: tuple[str, ...] = ()

#: provider 不可用时的处理策略（P0-3）；未知取值按最保守的 quarantine 处理
DEGRADED_POLICIES = ("quarantine", "fail_closed", "allow")
DEFAULT_DEGRADED_POLICY = "quarantine"


def degraded_policy() -> str:
    """当前生效的降级策略（环境变量非法时回落到 quarantine）。"""
    policy = (os.getenv("DR_MODERATION_DEGRADED_POLICY") or "").strip().lower()
    if policy in DEGRADED_POLICIES:
        return policy
    if policy:
        _log.warning("unknown DR_MODERATION_DEGRADED_POLICY %r, using %s",
                     policy, DEFAULT_DEGRADED_POLICY)
    return DEFAULT_DEGRADED_POLICY


def load_blocklist() -> tuple[str, ...]:
    raw = os.getenv("DR_MODERATION_BLOCKLIST", "").strip()
    if not raw:
        return DEFAULT_BLOCKLIST
    return tuple(term.strip().lower() for term in raw.split(",") if term.strip())


def keyword_scan(text: str, blocklist: Optional[Iterable[str]] = None) -> list[str]:
    """返回命中的词（去重、保持词表顺序）；大小写不敏感的子串匹配。"""
    if not text:
        return []
    terms = tuple(blocklist) if blocklist is not None else load_blocklist()
    lowered = text.lower()
    matches: list[str] = []
    for term in terms:
        if term and term in lowered and term not in matches:
            matches.append(term)
    return matches


@dataclass(frozen=True)
class ModerationResult:
    provider: str
    matches: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    #: P0-3：provider 不可用（未知 / 未实现 / 抛异常）时为 True
    degraded: bool = False
    #: 降级原因（异常类型 + 摘要），非 degraded 时为空
    failure_reason: str = ""
    #: 降级时生效的策略（quarantine / fail_closed / allow）
    policy: str = ""
    #: 环境显式配置但不可用的 provider 名（溯源用）
    requested_provider: str = ""

    @property
    def flagged(self) -> bool:
        return bool(self.matches)


class ModerationProvider(Protocol):
    name: str

    def scan(self, text: str) -> ModerationResult: ...


class LocalRulesProvider:
    """本地词表预检（`DR_MODERATION_BLOCKLIST`，逗号分隔；默认空表）。"""

    name = "local_rules"

    def scan(self, text: str) -> ModerationResult:
        return ModerationResult(provider=self.name, matches=keyword_scan(text))


class UnavailableProvider:
    """未实现的外部 provider 占位：调用即抛，由 `scan_text` 统一降级。"""

    def __init__(self, name: str) -> None:
        self.name = name

    def scan(self, text: str) -> ModerationResult:
        raise NotImplementedError(f"moderation provider not configured: {self.name}")


_REGISTRY: dict[str, ModerationProvider] = {}
_override: Optional[ModerationProvider] = None


def register_provider(provider: ModerationProvider) -> None:
    _REGISTRY[provider.name] = provider


def set_provider(provider: Optional[ModerationProvider]) -> None:
    """测试注入口（None 恢复环境选择）。"""
    global _override
    _override = provider


def _configured_name() -> str:
    return (os.getenv("DR_MODERATION_PROVIDER") or "local_rules").strip().lower()


def active_provider() -> ModerationProvider:
    if _override is not None:
        return _override
    name = _configured_name()
    provider = _REGISTRY.get(name)
    if provider is None:
        _log.warning("unknown moderation provider %r, falling back to local_rules", name)
        return _REGISTRY["local_rules"]
    return provider


def _degraded_result(fallback: ModerationResult, *, reason: str,
                     requested: str) -> ModerationResult:
    return ModerationResult(
        provider=fallback.provider,
        matches=list(fallback.matches),
        detail={**fallback.detail, "degraded": True, "failure_reason": reason,
                "requested_provider": requested},
        degraded=True,
        failure_reason=reason[:300],
        policy=degraded_policy(),
        requested_provider=requested,
    )


def scan_text(text: str) -> ModerationResult:
    """统一入口：选择 provider 扫描；不可用（未知/抛异常）回退本地词表并标记 degraded。

    回退保留本地命中（能拦就拦），但 degraded 让上层按策略处置，不再静默放行。
    """
    configured = _configured_name()
    provider = active_provider()  # 名字未知时在这里记 WARNING 并回落 local_rules
    if _override is None and configured not in _REGISTRY:
        fallback = _REGISTRY["local_rules"].scan(text or "")
        return _degraded_result(fallback, reason=f"unknown provider: {configured}",
                                requested=configured)
    try:
        result = provider.scan(text or "")
        return result if isinstance(result, ModerationResult) else ModerationResult(
            provider=provider.name, matches=list(result))
    except Exception as exc:  # noqa: BLE001 —— 外部服务失败不得影响主流程，但必须留痕
        _log.warning("moderation provider %s failed (%s: %s), falling back to local_rules",
                     provider.name, type(exc).__name__, exc)
        fallback = _REGISTRY["local_rules"].scan(text or "")
        return _degraded_result(
            fallback, reason=f"{type(exc).__name__}: {exc}", requested=provider.name)


register_provider(LocalRulesProvider())
