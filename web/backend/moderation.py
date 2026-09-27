"""内容安全最小实现（P7-A）：本地关键词预检 + 输出标记。

**诚实边界（必须与文档一致）**：这是**规则预检**，不是内容安全服务。
它只能拦住显式命中词表的内容；正式对外（L3-B/C）必须接入有资质的审核服务并保留人工复核，
本模块的价值在于：① 输入侧的硬拒绝入口；② 输出侧留痕，让申诉/复核有证据。

词表来源：`DR_MODERATION_BLOCKLIST`（逗号分隔）。默认**空表**（不误伤），
需要在 staging/生产显式配置；本地与 CI 默认可通过测试注入。
"""
from __future__ import annotations

import os
from typing import Iterable, Optional

#: 默认空词表：宁可少拦，也不误伤；实际词表由部署方按属地要求配置
DEFAULT_BLOCKLIST: tuple[str, ...] = ()

MAX_TOPIC_LENGTH = 1000
MAX_INSTRUCTIONS_LENGTH = 2000
MAX_APPEAL_LENGTH = 2000


def load_blocklist() -> tuple[str, ...]:
    raw = os.getenv("DR_MODERATION_BLOCKLIST", "").strip()
    if not raw:
        return DEFAULT_BLOCKLIST
    return tuple(term.strip().lower() for term in raw.split(",") if term.strip())


def scan(text: str, blocklist: Optional[Iterable[str]] = None) -> list[str]:
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


def flag_report(store, run_id: str, user_id: Optional[str], report: Optional[str]) -> list[str]:
    """输出侧标记（P7-A）：报告命中词表 ⇒ 标记 `flagged` 并留审核记录；不删除正文。

    调用方负责异常隔离（这里让异常上抛，由 runner/worker 的持久化失败语义处理）。
    """
    matches = scan(report or "")
    if not matches:
        return []
    store.record_moderation(
        "output_flagged", user_id=user_id, run_id=run_id,
        detail={"matches": matches[:10]},
    )
    store.set_moderation_status(run_id, "flagged")
    return matches


def apply_output_gate(payload: dict, report: Optional[str]) -> list[str]:
    """输出侧统一闸（P0-4）：命中词表 ⇒ 事件流 / 传输层脱敏，**产物原文不删**。

    必须在**发帧与落库之前**调用：这样 `run_events`（SSE 回放的权威来源）
    与实时帧都不携带完整正文；完整原文只留在 `run_artifacts.report_md`，
    供管理员 / 审核人员经 CLI 复核（普通用户导出由 API 闸拒绝）。

    返回命中词；无命中返回空列表（payload 原样不动）。
    """
    matches = scan(report or "")
    if not matches:
        return []
    result = payload.get("result")
    if isinstance(result, dict) and result.get("report"):
        result = dict(result)
        result["report"] = ""
        payload["result"] = result
    payload["output_under_review"] = True
    return matches
