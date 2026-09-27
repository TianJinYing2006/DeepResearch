"""服务端运行档位表（需求 10 §5.6，P0 profile 固化）。

请求体只能选档位名（``profile``），不能提交底层参数；档位在创建 run 时解析为
:class:`research_engine.runtime_profile.RuntimeProfile` 快照并写进 ``runs.request``，
执行器据此设置运行作用域 —— 客户端不再有任何放大跳数 / 子问题 / 预算的入口。

数值口径：需求 10 §5.6 初始封顶（Quick 6/2/60k/15min/¥0.75、Standard 12/3/120k/30min/¥1.25、
Deep 20/4/200k/60min/¥1.50）。校准只能收紧，放宽必须重新评审（§5.6 原文）。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from research_engine.runtime_profile import RuntimeProfile

PROFILE_VERSION = "v1"
DEFAULT_PROFILE = "quick"

#: 档位可用性（需求 10 §5.6）：Quick 全员；Standard L3-A 手动可用；Deep 仅管理员审批。
#: 当前还没有角色系统，Deep 不进入前端可选列表，但服务端保留定义供管理员 CLI 使用。
PROFILES: Dict[str, RuntimeProfile] = {
    "quick": RuntimeProfile(
        name="quick", version=PROFILE_VERSION,
        max_total_hops=6, max_subquestions=2, token_budget=60_000,
        timeout_seconds=15 * 60, model="qwen-turbo",
        run_budget_cny=0.75, max_replan=1, per_subq_hop_cap=3,
    ),
    "standard": RuntimeProfile(
        name="standard", version=PROFILE_VERSION,
        max_total_hops=12, max_subquestions=3, token_budget=120_000,
        timeout_seconds=30 * 60, model="qwen-plus",
        run_budget_cny=1.25, max_replan=1, per_subq_hop_cap=4,
    ),
    "deep": RuntimeProfile(
        name="deep", version=PROFILE_VERSION,
        max_total_hops=20, max_subquestions=4, token_budget=200_000,
        timeout_seconds=60 * 60, model="qwen-plus",
        run_budget_cny=1.50, max_replan=1, per_subq_hop_cap=5,
    ),
}

#: 前端可选档位（Deep 需管理员审批，暂不开放）
SELECTABLE_PROFILES: tuple[str, ...] = ("quick", "standard")

_LABELS = {"quick": "快速", "standard": "标准", "deep": "深度"}


def resolve_profile(name: str) -> Optional[RuntimeProfile]:
    """按名解析档位；未知返回 None（由 API 层转结构化 400）。"""
    return PROFILES.get((name or "").strip().lower())


def profile_options() -> List[Dict[str, Any]]:
    """``/api/options`` 下发的档位列表（只含可选档位，附展示用参数）。"""
    options: List[Dict[str, Any]] = []
    for name in SELECTABLE_PROFILES:
        profile = PROFILES[name]
        options.append({
            "value": name,
            "label": _LABELS.get(name, name),
            "max_total_hops": profile.max_total_hops,
            "max_subquestions": profile.max_subquestions,
            "token_budget": profile.token_budget,
            "timeout_seconds": profile.timeout_seconds,
            "run_budget_cny": profile.run_budget_cny,
        })
    return options
