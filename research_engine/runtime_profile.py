"""运行档位的运行时载体（P0 profile 固化）。

背景（需求 10 §5.6 / §3.1 第 20 项）：``max_total_hops`` / ``max_subquestions`` /
``token_budget`` / 模型 / 超时 / 单次预算由**服务端档位**固定，请求体不得覆盖。
P3 之前执行器靠改全局 ``config`` 实现「本场 run 的参数」——同一进程并发两个 run
时会互相污染（A 的档位漏到 B），串行时也会把覆盖值留给下一场。

这里改用 contextvar（与 ``research_engine.rag.scope`` 同构）：

- 执行器（RunManager 工作线程 / Queue Worker）在**建图前** :func:`set_profile`；
- 核心链路只经 :func:`effective_research_config` / :func:`effective_llm_model`
  读取，不再直接改全局 config；
- CLI / eval 不设置 ⇒ ``current_profile()`` 为 None ⇒ 读取回落全局 config，
  行为与历史完全一致（W8 冻结基线不受影响）。
"""
from __future__ import annotations

import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import asdict, dataclass, replace
from typing import Any, Dict, Iterator, Optional

from config import config

#: 档位快照的字段（写进 runs.request 与从库回读共用；新增字段必须同步 version）
_PROFILE_FIELDS = (
    "name", "version", "max_total_hops", "max_subquestions", "token_budget",
    "timeout_seconds", "model", "run_budget_cny", "max_replan", "per_subq_hop_cap",
)

#: profile 只覆盖这些 LLM 角色；fast（压缩/摘要）保持全局配置（默认 qwen-turbo）
_PROFILE_MODEL_ROLES = frozenset({"smart", "strategic", "critic", "validator"})


@dataclass(frozen=True)
class RuntimeProfile:
    """一次 run 的不可变执行档位（服务端解析后固定，随 run 快照入库）。"""

    name: str
    version: str
    max_total_hops: int
    max_subquestions: int
    token_budget: int
    timeout_seconds: int
    model: str
    run_budget_cny: float
    max_replan: int
    per_subq_hop_cap: int

    def snapshot(self) -> Dict[str, Any]:
        """可 JSON 序列化的快照（写进 ``runs.request["profile"]``）。"""
        return asdict(self)

    @classmethod
    def from_snapshot(cls, data: Dict[str, Any]) -> RuntimeProfile:
        """从库内快照回读；缺字段（旧行）直接抛 KeyError，由调用方兜底。"""
        return cls(**{key: data[key] for key in _PROFILE_FIELDS})


_current: ContextVar[Optional[RuntimeProfile]] = ContextVar("dr_run_profile", default=None)


def current_profile() -> Optional[RuntimeProfile]:
    return _current.get()


def set_profile(profile: Optional[RuntimeProfile]) -> None:
    """直接设置当前档位（执行器在建图前调用；线程结束作用域自然消失）。"""
    _current.set(profile)


@contextmanager
def use_profile(profile: Optional[RuntimeProfile]) -> Iterator[Optional[RuntimeProfile]]:
    """在 with 块内设定档位（库函数 / 测试用）。"""
    token = _current.set(profile)
    try:
        yield _current.get()
    finally:
        _current.reset(token)


def effective_research_config():
    """本场 run 生效的研究配置：有档位则覆盖预算类字段，否则全局 config。"""
    base = config.research
    profile = current_profile()
    if profile is None:
        return base
    return replace(
        base,
        max_total_hops=profile.max_total_hops,
        max_subquestions=profile.max_subquestions,
        token_budget=profile.token_budget,
        max_replan=profile.max_replan,
        per_subq_hop_cap=profile.per_subq_hop_cap,
    )


#: F13（审计）：任务时限载体（单调时钟，避免墙钟漂移影响超时判定）。
#: 执行器在开跑前 `set_task_deadline(剩余秒数)`，LLM 调用按剩余时限收窄 SDK timeout；
#: CLI / eval 不设置 ⇒ 仅使用 `config.llm.request_timeout_seconds` 默认超时。
_task_deadline: ContextVar[Optional[float]] = ContextVar("dr_task_deadline_monotonic", default=None)


def set_task_deadline(seconds_remaining: Optional[float]) -> object:
    """设置任务剩余时限（秒；None = 清除）；返回 token 供 :func:`reset_task_deadline`。"""
    value = None if seconds_remaining is None else time.monotonic() + max(0.0, float(seconds_remaining))
    return _task_deadline.set(value)


def reset_task_deadline(token: object) -> None:
    _task_deadline.reset(token)  # type: ignore[arg-type]


def task_deadline_remaining() -> Optional[float]:
    """任务剩余秒数（可能为负 = 已过期）；未设置时限返回 None。"""
    value = _task_deadline.get()
    if value is None:
        return None
    return value - time.monotonic()


def effective_llm_model(role: str) -> str:
    """本场 run 生效的模型名；无档位时回落全局 config（逐角色默认）。"""
    defaults = {
        "fast": config.llm.fast_model,
        "smart": config.llm.smart_model,
        "strategic": config.llm.strategic_model,
        "critic": config.llm.critic_model,
        "validator": config.llm.validator_model,
    }
    profile = current_profile()
    if profile is None or role not in _PROFILE_MODEL_ROLES:
        return defaults[role]
    return profile.model
