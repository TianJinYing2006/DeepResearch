"""审核 provider 抽象（P2-5a）：统一接口 + 本地规则实现 + 注册与降级。

**诚实边界**：本地关键词预检只拦显式命中词表；正式对外（L3-B/C）必须接有资质的
审核服务并保留人工复核。本模块只固化接口与降级语义，外部 provider（如阿里云
内容安全）留到 P2-5b/接入阶段实现，不在本 PR 引入任何 SDK 或网络调用。

降级原则（fail-safe，宁可多拦不乱放行）：

- `DR_MODERATION_PROVIDER` 选择已注册 provider（默认 `local_rules`）；
- 名字未知（含未实现的 `aliyun`）或 provider 抛异常 ⇒ 记 WARNING 并**回退
  `local_rules`**（启动不崩、请求不 5xx）；
- 统一返回 `ModerationResult`（provider / matches / detail），留痕到
  `moderation_records.detail.provider`，供审计与申诉溯源。
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Iterable, Optional, Protocol

_log = logging.getLogger("deepresearch.moderation")

#: 默认空词表：宁可少拦，也不误伤；实际词表由部署方按属地要求配置
DEFAULT_BLOCKLIST: tuple[str, ...] = ()


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


def active_provider() -> ModerationProvider:
    if _override is not None:
        return _override
    name = (os.getenv("DR_MODERATION_PROVIDER") or "local_rules").strip().lower()
    provider = _REGISTRY.get(name)
    if provider is None:
        _log.warning("unknown moderation provider %r, falling back to local_rules", name)
        return _REGISTRY["local_rules"]
    return provider


def scan_text(text: str) -> ModerationResult:
    """统一入口：选择 provider 扫描；失败/未配置一律回退本地词表。"""
    provider = active_provider()
    try:
        return provider.scan(text or "")
    except Exception as exc:  # noqa: BLE001 —— 外部服务失败不得影响主流程
        _log.warning("moderation provider %s failed (%s: %s), falling back to local_rules",
                     provider.name, type(exc).__name__, exc)
        return _REGISTRY["local_rules"].scan(text or "")


register_provider(LocalRulesProvider())
