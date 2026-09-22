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

1. **工具类 5 值**的**唯一产生点是工具层**，写入 ``SearchResponse.failure_reason``
   （外部搜索：``search/bocha.py``、``search/arxiv.py``）或 ``RetrieveResponse.failure_reason``
   （本地检索：``rag/retriever.py`` + ``rag/store.py`` 的 ``unavailable_reason``）；
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

from enum import StrEnum
from typing import FrozenSet, Optional


class FailureReason(StrEnum):
    """失败原因枚举。

    继承 ``StrEnum``（**Python 3.11+**）而非 ``(str, Enum)``，两个理由：

    ① 与仓库的 Python 下界一致 —— W8 Arm 3 把支持区间钉为 3.11~3.13 并加了运行时闸
       （``research_engine/__init__.py``）+ CI matrix，``StrEnum`` 正在该区间内；
       ruff 的 ``target-version = py311`` 也会要求它（``UP042``）。
    ② 行为更可预期：``(str, Enum)`` 下 ``f"{member}"`` 会渲染成 ``"FailureReason.TIMEOUT"``
       而非 ``"timeout"``（经典坑），``StrEnum`` 下两者一致。

    继承 ``str`` 的收益（能直接赋给 ``SearchResponse.failure_reason: str`` 而无需 ``.value``）
    ``StrEnum`` 同样具备；但**本仓库仍统一显式写 ``.value``**，落盘 JSON 时一定是字符串。
    """

    # ---- 工具层产生（5 值；Arm 4 原有）----
    NOT_CONFIGURED = "not_configured"  # provider 未配置 / 依赖不可用
    TIMEOUT = "timeout"  # 请求超时
    PROVIDER_ERROR = "provider_error"  # provider 返回错误（5xx、限流）
    EMPTY_RESULT = "empty_result"  # provider 正常响应但无结果
    PARSE_ERROR = "parse_error"  # 返回了数据但解析失败

    # ---- 非工具层产生（5 值；Q4 新增 4 值 + W9 后新增 1 值，由 Arm 1 直接构造）----
    LLM_ERROR = "llm_error"  # LLM 调用失败（非超时、非限流）
    TOKEN_LIMIT = "token_limit"  # 触发 token 硬闸 / 上下文超长
    RECURSION_LIMIT = "recursion_limit"  # GraphRecursionError
    INTERNAL = "internal"  # 其他未分类异常
    #: Planner 返回的子问题数超过上限（max_subquestions），尾部被系统截断。
    #: **不是故障**，是「模型没遵守数量约束、系统执行了配置约束」——但也不允许静默，
    #: 故记入 degradation_log 供审计（⇒ run_status=degraded）。
    #: ⚠️ 若实测发现它导致 degraded 噪声过大（例如用户把上限调成 1 时几乎必现），
    #: 翻转点只有 Planner._bound_subquestions 一处 —— 改成不进 degradation_log、
    #: 走独立审计通道即可，不要在调用点散落判断（同 D-03 的「一处定义全局生效」）。
    PLANNER_OUTPUT_TRUNCATED = "planner_output_truncated"


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

#: 非工具类 5 值 —— 没有工具层来源，由 Arm 1 各产生点直接构造 ``DegradationEntry``。
NON_TOOL_REASONS: FrozenSet[str] = frozenset(
    r.value
    for r in (
        FailureReason.LLM_ERROR,
        FailureReason.TOKEN_LIMIT,
        FailureReason.RECURSION_LIMIT,
        FailureReason.INTERNAL,
        FailureReason.PLANNER_OUTPUT_TRUNCATED,
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


#: **结果类**原因：不是故障，只是「工具正常执行了但没产出」。
#: 记在 ``failure_reason`` 里供 planner 当轮决策（换源 / 改查询），但**不进
#: ``degradation_log``、不推导 ``run_status``** —— 见 :func:`is_fault_reason` 的理由。
NON_FAULT_REASONS: FrozenSet[str] = frozenset({FailureReason.EMPTY_RESULT.value})


def is_fault_reason(reason: Optional[str]) -> bool:
    """该原因是否应上抛为 **run 级降级**（写 ``degradation_log``）。

    ⚠️ 决策 **D-03**：``empty_result`` **不算故障**。

    理由（不是偏好，是算术）：``ResearchState.resolve_run_status()`` 的规则是
    「``degradation_log`` 非空 + 有报告 ⇒ ``degraded``」。一个多跳研究流程里
    「某次检索零命中」几乎必然发生，若把它写成降级条目，**几乎每轮 run 都会是
    ``degraded``**，``run_status`` 就不再是健康度信号，Arm 1 的「故障可归因」也就
    退化成噪声。零命中是**结果**，未配置 / 超时 / provider 错 / 解析错才是**故障**。

    想翻转这条口径，只需把 ``empty_result`` 从 :data:`NON_FAULT_REASONS` 里移除 ——
    **一处定义、全局生效**，不要在调用点散落 `!= "empty_result"` 的判断。
    """
    if reason is None:
        return False
    return reason not in NON_FAULT_REASONS


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
