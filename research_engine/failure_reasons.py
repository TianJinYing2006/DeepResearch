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

    # ---- 非工具层产生（4 值；由 Arm 1 直接构造）----
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


#: provider 异常类名（OpenAI SDK / httpx 直抛）——冒泡到图外的 LLM 调用失败
_PROVIDER_EXCEPTION_NAMES: FrozenSet[str] = frozenset(
    {
        "RateLimitError",
        "APITimeoutError",
        "APIConnectionError",
        "InternalServerError",
        "ServiceUnavailableError",
        "ConnectError",
        "ReadTimeout",
    }
)

#: provider 错误消息里的限流线索（百炼 `limit_requests` / 标准 429 文案）
_PROVIDER_RATE_LIMIT_HINTS = (
    "limit_requests",
    "rate limit",
    "ratelimit",
    "too many requests",
    "429",
)


def _is_provider_exception(exc: BaseException) -> bool:
    """是否是「LLM provider 侧」异常（429/5xx/网络超时/限流文案）。"""
    name = type(exc).__name__
    msg = f"{name}: {exc}".lower()
    status = getattr(exc, "status_code", None)
    if isinstance(status, int) and (status == 429 or 500 <= status <= 599):
        return True
    if any(hint in msg for hint in _PROVIDER_RATE_LIMIT_HINTS):
        return True
    return name in _PROVIDER_EXCEPTION_NAMES


def classify_exception(exc: BaseException) -> str:
    """把异常归类为**非工具类**失败原因（Arm 1 ``run()`` 异常路径用）。

    只返回 :data:`NON_TOOL_REASONS` 中的值 —— 工具层异常应当已经在工具内部被捕获并
    转成 ``SearchResponse.failure_reason``，能冒泡到 ``run()`` 的按以下优先级归类：

    * ``GraphRecursionError`` ⇒ ``recursion_limit``（LangGraph 图循环超限）
    * 异常类名/消息含 token / context length 线索 ⇒ ``token_limit``
    * provider 429 / 5xx、限流类异常名、OpenAI SDK 网络异常 ⇒ ``llm_error``
    * 其余 ⇒ ``internal``

    ⚠️ 2026-10-31 修正：此前只要不是递归/token 就一律 ``internal``，把百炼 429
    （``limit_requests``，真实线上首跳失败）误判成内部错误，前端只能显示
    「未知错误」。**不能**把图内异常归成 ``provider_error``：那是工具层 5 值之一，
    由 ``is_tool_reason`` 锁死「只能由工具层产生」的单向派生契约；图内 LLM 失败
    使用非工具层的 ``llm_error``。原始响应体仍留在 ``degradation_log.detail``
    供复核（见 :func:`describe_exception`）。
    """
    name = type(exc).__name__
    if name == "GraphRecursionError":
        return FailureReason.RECURSION_LIMIT.value
    msg = f"{name}: {exc}".lower()
    if "token" in msg or "context length" in msg or "context_length" in msg:
        return FailureReason.TOKEN_LIMIT.value
    if _is_provider_exception(exc):
        return FailureReason.LLM_ERROR.value
    return FailureReason.INTERNAL.value


def describe_exception(exc: BaseException) -> tuple[str, str]:
    """把异常映射成 ``(failure_reason, 面向用户的 message)``。

    provider 侧 ``llm_error`` 不回显原始响应体（可能是整段 provider JSON），只给
    可行动的中文说明；原始 ``str(exc)`` 仍由调用方写入 ``degradation_log.detail``。
    其他错误保持原样，避免掩盖真实堆栈信息（截断 500 字符）。
    """
    code = classify_exception(exc)
    if code != FailureReason.LLM_ERROR.value or not _is_provider_exception(exc):
        return code, str(exc)[:500]
    status = getattr(exc, "status_code", None)
    if status == 429:
        return (
            code,
            "模型服务请求频率超限（HTTP 429）；请稍后点击「用同样参数重试」。",
        )
    if isinstance(status, int) and status >= 500:
        return (
            code,
            f"模型服务暂时不可用（HTTP {status}）；请稍后点击「用同样参数重试」。",
        )
    return (
        code,
        "模型服务调用失败（网络异常或超时）；请稍后点击「用同样参数重试」。",
    )
