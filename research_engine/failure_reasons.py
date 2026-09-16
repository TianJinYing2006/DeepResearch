"""失败原因枚举 —— 全局唯一真相源（W8 Arm 1 前置 / Q4 优化点 ② / Q8 B4 收口）。

背景
----
在 W8 之前，项目里有**两套互不认识的失败记录**：

* Arm 4 的 ``SearchResponse.failure_reason`` —— 面向**下游节点**（planner 当轮决策：换源？改查询？），
  **同步、一次调用即覆盖**；
* Arm 1 的 ``degradation_log[].reason`` —— 面向**事后审计**（回答「这次 run 哪里降级了」），
  **异步、run 级追加**。

它们**不是同一份数据的两份拷贝，而是两个消费方**（同构于 OTel 的
``Span.Status``（单值后写覆盖）与 ``Span.add_event()``（追加流）—— OTel 两者并存不合并）。
所以**不合并**，改为立**单向派生契约**：

1. **工具类 5 值**的**唯一产生点是工具层**（``researcher.py`` / ``arxiv.py`` / ``store.py``
   的 ``except`` 分支），写入 ``SearchResponse.failure_reason``；
2. ``degradation_log`` 条目的 ``reason`` **必须由 ``failure_reason`` 派生**
   （``DegradationEntry(reason=resp.failure_reason, ...)``），**禁止在同一处手写第二个字面量**
   —— 违反则两处对同一事件的描述会**静默分叉**，比记两次更糟；
3. **非工具类 4 值**没有工具层来源，由 Arm 1 各产生点直接构造条目。

⇒ **一张枚举表、两个产生点、按来源分组**（见 :data:`TOOL_REASONS` / :data:`NON_TOOL_REASONS`）。

用法
----
::

    from research_engine.failure_reasons import FailureReason, is_tool_reason

    resp.failure_reason = FailureReason.TIMEOUT.value      # 工具层产生
    DegradationEntry(reason=resp.failure_reason, ...)      # 单向派生，不重写字面量
"""

from __future__ import annotations

from enum import Enum
from typing import FrozenSet


class FailureReason(str, Enum):
    """失败原因枚举。

    继承 ``str`` 是为了能直接赋给 ``SearchResponse.failure_reason: str``
    与 Pydantic 的 ``Dict[str, Any]`` 字段而无需 ``.value``；但**显式写 ``.value`` 更清晰**，
    落盘 JSON 时也一定是字符串。
    """

    # ---- 工具层产生（5 值；Arm 4 原有）----
    NOT_CONFIGURED = "not_configured"  # provider 未配置 / 依赖不可用
    TIMEOUT = "timeout"  # 请求超时
    PROVIDER_ERROR = "provider_error"  # provider 返回错误（5xx、限流）
    EMPTY_RESULT = "empty_result"  # provider 正常响应但无结果
    PARSE_ERROR = "parse_error"  # 返回了数据但解析失败

    # ---- 非工具层产生（4 值；Q4 新增，由 Arm 1 直接构造）----
    LLM_ERROR = "llm_error"  # LLM 调用失败（非超时、非限流）
    TOKEN_LIMIT = "token_limit"  # 触发 token 硬闸 / 上下文超长
    RECURSION_LIMIT = "recursion_limit"  # GraphRecursionError
    INTERNAL = "internal"  # 其他未分类异常


#: 工具层 5 值 —— **只**由工具层（Arm 4）产生，写入 ``SearchResponse.failure_reason``。
TOOL_REASONS: FrozenSet[str] = frozenset(
    r.value
    for r in (
        FailureReason.NOT_CONFIGURED,
        FailureReason.TIMEOUT,
        FailureReason.PROVIDER_ERROR,
        FailureReason.EMPTY_RESULT,
        FailureReason.PARSE_ERROR,
    )
)

#: 非工具类 4 值 —— 没有工具层来源，由 Arm 1 各产生点直接构造 ``DegradationEntry``。
NON_TOOL_REASONS: FrozenSet[str] = frozenset(
    r.value
    for r in (
        FailureReason.LLM_ERROR,
        FailureReason.TOKEN_LIMIT,
        FailureReason.RECURSION_LIMIT,
        FailureReason.INTERNAL,
    )
)

ALL_REASONS: FrozenSet[str] = TOOL_REASONS | NON_TOOL_REASONS


def is_tool_reason(reason: str) -> bool:
    """该失败原因是否属于「工具层 5 值」。

    用于单测锁定单向派生契约：**工具层产生的原因不得由 Arm 1 凭空构造**。
    """
    return reason in TOOL_REASONS


def is_valid_reason(reason: str) -> bool:
    """是否为枚举表内的合法值（非法值意味着某处在手写字面量）。"""
    return reason in ALL_REASONS


def classify_exception(exc: BaseException) -> str:
    """把异常归类为**非工具类**失败原因（Arm 1 ``run()`` 异常路径用）。

    只返回 :data:`NON_TOOL_REASONS` 中的值 —— 工具层异常应当已经在工具内部被捕获并
    转成 ``SearchResponse.failure_reason``，能冒泡到 ``run()`` 的一律是框架级/内部错误。

    归类依据（按优先级）：

    * ``GraphRecursionError`` ⇒ ``recursion_limit``（LangGraph 图循环超限）
    * 异常类名/消息含 token / context length 线索 ⇒ ``token_limit``
    * 其余 ⇒ ``internal``

    不做 ``llm_error`` 的自动判定：LLM 调用失败在节点内已走各节点自己的 fallback，
    能冒泡到 ``run()`` 说明不是普通 LLM 错误，留由调用方显式指定。
    """
    name = type(exc).__name__
    if name == "GraphRecursionError":
        return FailureReason.RECURSION_LIMIT.value
    msg = f"{name}: {exc}".lower()
    if "token" in msg or "context length" in msg or "context_length" in msg:
        return FailureReason.TOKEN_LIMIT.value
    return FailureReason.INTERNAL.value
