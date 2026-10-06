"""token 预算准入与预留（审计 F13）。

两层预算语义：

- ``token_budget``（**绝对准入线**）：每次外部 LLM 调用前经 :func:`admit_call` 检查，
  耗尽即抛 :class:`TokenBudgetExceeded`（不发起调用），由各 agent 既有降级路径优雅收口；
- ``token_budget_reserve``（**预留额度**）：研究循环（critic 硬闸）在
  ``token_budget - token_budget_reserve`` 处提前停止，保证报告写作与引用校验仍有预算。

任务时限（deadline）由执行器经 :mod:`research_engine.runtime_profile` 注入（单调时钟），
LLM 调用按剩余时限收窄超时；已过期时 :class:`DeadlineExceeded` 阻止新调用。
"""
from __future__ import annotations

from typing import Any

from research_engine.runtime_profile import effective_research_config


class CallAdmissionDenied(RuntimeError):
    """外部调用准入失败基类（预算耗尽 / 任务时限已过）。"""


class TokenBudgetExceeded(CallAdmissionDenied):
    """token 预算已耗尽：不发起外部 LLM 调用。"""


class DeadlineExceeded(CallAdmissionDenied):
    """任务时限已过：不发起外部调用（协作式取消在节点边界兜底）。"""


def research_token_ceiling(rc: Any = None) -> int:
    """研究循环的 token 上限（= 绝对预算 - 预留额度，>=0）。

    critic 硬闸与 eval 反思口径共用本函数，保证「研究何时停」只有一处定义。
    """
    c = rc if rc is not None else effective_research_config()
    return max(0, int(c.token_budget) - int(getattr(c, "token_budget_reserve", 0) or 0))


def budget_remaining(state: Any, rc: Any = None) -> int:
    """绝对预算剩余（>=0）；``state`` 为空视为 0 已用。"""
    c = rc if rc is not None else effective_research_config()
    used = int(getattr(state, "token_used", 0) or 0)
    return max(0, int(c.token_budget) - used)


def admit_call(state: Any, rc: Any = None) -> None:
    """LLM 调用准入：``state`` 为 None（CLI / 工具直调）不检查；耗尽抛 TokenBudgetExceeded。"""
    if state is None:
        return
    if budget_remaining(state, rc) <= 0:
        c = rc if rc is not None else effective_research_config()
        raise TokenBudgetExceeded(
            f"token budget exhausted: used={int(getattr(state, 'token_used', 0) or 0)} "
            f"budget={int(c.token_budget)}")
