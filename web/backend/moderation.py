"""内容安全最小实现（P7-A / P2-5a）：provider 化预检 + 输出标记。

**诚实边界（必须与文档一致）**：默认 provider 是**本地规则预检**，不是内容安全服务。
它只能拦住显式命中词表的内容；正式对外（L3-B/C）必须接入有资质的审核服务并保留人工复核，
本模块的价值在于：① 输入侧的硬拒绝入口；② 输出侧留痕，让申诉/复核有证据。

P2-5a：判定统一走 `moderation_providers.scan_text`（`DR_MODERATION_PROVIDER`
选择实现；未知/未实现/异常一律回退 `local_rules`，见该模块 docstring）。
"""
from __future__ import annotations

from typing import Optional

from .injection_guard import scan_output_leak
from .moderation_providers import (  # noqa: F401 —— 兼容旧导入路径（load_blocklist / scan）
    DEFAULT_BLOCKLIST,
    ModerationResult,
    active_provider,
    keyword_scan,
    load_blocklist,
    register_provider,
    scan_text,
    set_provider,
)

MAX_TOPIC_LENGTH = 1000
MAX_INSTRUCTIONS_LENGTH = 2000
MAX_APPEAL_LENGTH = 2000


def scan(text: str, blocklist=None) -> list[str]:
    """本地词表命中（兼容旧接口）：显式 `blocklist` 直查，否则走当前 provider。"""
    if blocklist is not None:
        return keyword_scan(text, blocklist=blocklist)
    return scan_text(text).matches


def flag_report(store, run_id: str, user_id: Optional[str], report: Optional[str]) -> list[str]:
    """输出侧标记（P7-A / P2-1a）：命中词表或系统提示泄漏 ⇒ 标记 `flagged` 并留记录。

    调用方负责异常隔离（这里让异常上抛，由 runner/worker 的持久化失败语义处理）。
    """
    result = scan_text(report or "")
    leaks = scan_output_leak(report or "")
    if not result.matches and not leaks:
        return []
    store.record_moderation(
        "output_flagged", user_id=user_id, run_id=run_id,
        detail={"matches": result.matches[:10], "output_leaks": leaks[:5],
                "provider": result.provider},
    )
    store.set_moderation_status(run_id, "flagged")
    return result.matches or leaks


def apply_output_gate(payload: dict, report: Optional[str]) -> list[str]:
    """输出侧统一闸（P0-4 / P2-1a）：命中词表或系统提示泄漏 ⇒ 事件流 / 传输层脱敏，
    **产物原文不删**。

    必须在**发帧与落库之前**调用：这样 `run_events`（SSE 回放的权威来源）
    与实时帧都不携带完整正文；完整原文只留在 `run_artifacts.report_md`，
    供管理员 / 审核人员经 CLI 复核（普通用户导出由 API 闸拒绝）。

    返回命中项（命中词或泄漏标记）；无命中返回空列表（payload 原样不动）。
    """
    result = scan_text(report or "")
    leaks = scan_output_leak(report or "")
    if not result.matches and not leaks:
        return []
    result_payload = payload.get("result")
    if isinstance(result_payload, dict) and result_payload.get("report"):
        result_payload = dict(result_payload)
        result_payload["report"] = ""
        payload["result"] = result_payload
    payload["output_under_review"] = True
    return result.matches or leaks
