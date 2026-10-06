"""token 预算准入与预留（审计 F13；R05 预占加固）。

三层预算语义：

- ``token_budget``（**绝对准入线**）：每次外部 LLM 调用前经 :func:`admit_call`
  检查，耗尽即抛 :class:`TokenBudgetExceeded`（不发起调用）；
- ``token_budget_reserve``（**预留额度**）：研究循环（critic 硬闸）在
  ``token_budget - token_budget_reserve`` 处提前停止，保证报告写作与引用校验仍有预算；
- **R05 调用预占**：:func:`reserve_call` 在发起调用前按「输入估计 + 输出上限」
  原子预占额度（写入 ``state.token_used``），并发调用无法同时越过准入线；
  调用结束后 :func:`settle_call` 按真实用量结算释放（供应商缺 usage 时按预占保守计入）。
  预占与结算共用模块级锁，与 ``admit_call`` / LLMClient 的用量累计互斥。

任务时限（deadline）由执行器经 :mod:`research_engine.runtime_profile` 注入（单调时钟），
LLM 调用按剩余时限收窄超时（**显式 timeout 也不得超过剩余时限**）；已过期时
:class:`DeadlineExceeded` 阻止新调用。
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Any, Iterable, Optional

from research_engine.runtime_profile import effective_research_config

#: 预算准入 / 预占 / 结算的共享锁。所有读改写 ``state.token_used`` 的路径必须经它，
#: 否则「预占检查」与「用量结算」之间会重新出现并发越闸（R05）。
_BUDGET_LOCK = threading.Lock()


def _iter_content(messages: Iterable[Any]) -> Iterable[str]:
    for message in messages or []:
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", "")
        if isinstance(content, str):
            yield content


def estimate_text_tokens(text: str) -> int:
    """保守的 token 估计（CJK 按 1 token/字，其余按 4 字符/token）。

    预占只需要「估计值 ≥ 实际输入」来保证不越闸，宁可略保守。
    """
    cjk = other = 0
    for ch in text or "":
        code = ord(ch)
        if (0x3400 <= code <= 0x4DBF or 0x4E00 <= code <= 0x9FFF or 0xF900 <= code <= 0xFAFF
                or 0x3000 <= code <= 0x303F or 0xFF00 <= code <= 0xFFEF):
            cjk += 1
        else:
            other += 1
    return cjk + (other + 3) // 4 + 1


def estimate_messages_tokens(messages: Iterable[Any]) -> int:
    """消息列表的输入 token 估计（逐条求和，空列表也给 1 保底）。"""
    return max(1, sum(estimate_text_tokens(text) for text in _iter_content(messages)))


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
    """LLM 调用准入：``state`` 为 None（CLI / 工具直调）不检查；耗尽抛 TokenBudgetExceeded。

    R05：检查在共享锁内进行，与 :func:`reserve_call` 的预占互斥。
    """
    if state is None:
        return
    with _BUDGET_LOCK:
        if budget_remaining(state, rc) <= 0:
            c = rc if rc is not None else effective_research_config()
            raise TokenBudgetExceeded(
                f"token budget exhausted: used={int(getattr(state, 'token_used', 0) or 0)} "
                f"budget={int(c.token_budget)}")


@dataclass(frozen=True)
class CallReservation:
    """一次外部 LLM 调用的预算预占凭据（R05）。

    ``reserved_tokens`` 已临时计入 ``state.token_used``（并发准入因此看不到这笔额度）；
    ``output_tokens`` = 本次允许的最大输出（写入请求 ``max_tokens``，保证实际消费 ≤ 预占）。
    """

    state: Any
    reserved_tokens: int
    output_tokens: int


def reserve_call(
    state: Any,
    *,
    input_tokens: int,
    desired_output: Optional[int] = None,
    rc: Any = None,
) -> Optional[CallReservation]:
    """原子预占一次调用的预算（R05）；``state`` 为 None 时返回 None（不检查）。

    语义：

    - 输入估计（``input_tokens``）先扣；剩余额度不足以容纳至少 1 个输出 token 即拒绝；
    - 输出上限 = min(调用方期望, 剩余额度 - 输入估计) —— 请求会把它写进 ``max_tokens``，
      因此本次调用的实际消费不可能超过预占；
    - 预占立即计入 ``state.token_used``，并发调用按预占后的余额做准入。
    """
    if state is None:
        return None
    c = rc if rc is not None else effective_research_config()
    from config import config as _config  # 局部导入避免配置循环依赖

    budget = max(0, int(c.token_budget))
    want_in = max(1, int(input_tokens))
    default_out = int(getattr(_config.llm, "max_tokens", 4096) or 4096)
    want_out = int(desired_output) if desired_output and desired_output > 0 else default_out
    with _BUDGET_LOCK:
        used = int(getattr(state, "token_used", 0) or 0)
        remaining = budget - used
        if remaining <= want_in:
            raise TokenBudgetExceeded(
                f"insufficient budget for call: remaining={remaining}, "
                f"input_estimate={want_in}, budget={budget}")
        output_tokens = min(want_out, remaining - want_in)
        reserved = want_in + output_tokens
        state.token_used = used + reserved
    return CallReservation(state=state, reserved_tokens=reserved, output_tokens=output_tokens)


def add_charged_usage(state: Any, tokens: int) -> None:
    """把一次已发生的用量原子计入 ``state.token_used``（无预占的兼容路径）。"""
    if state is None:
        return
    with _BUDGET_LOCK:
        state.token_used = int(getattr(state, "token_used", 0) or 0) + max(0, int(tokens))


def settle_call(reservation: Optional[CallReservation], actual_tokens: Optional[int] = None) -> None:
    """结算一次预占：按真实用量替换预占额度（``actual_tokens=None`` ⇒ 按预占保守计入）。

    为什么缺 usage 时按预占计入而不是释放：调用可能已经消耗了输入 token，
    释放会产生「账面上没花钱、实际花了钱」的越闸缺口；保守记账保证预算硬边界。
    """
    if reservation is None:
        return
    actual = (reservation.reserved_tokens if actual_tokens is None
              else max(0, int(actual_tokens)))
    with _BUDGET_LOCK:
        state = reservation.state
        used = int(getattr(state, "token_used", 0) or 0)
        state.token_used = max(0, used - reservation.reserved_tokens + actual)
