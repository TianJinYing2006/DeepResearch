"""Langfuse 可观测层（W3，grill Q1~Q7 全部定稿，见 docs/requirements/3-langfuse-observability.md）。

设计要点（速查）：
- Q1=B'：接入 = langfuse.openai 包装器（模块级全局 wrap openai 方法，LLM generation 自动记录
  usage/cost，LLMClient 代码零改动）+ start_as_current_observation 显式 span（弃 @observe：
  装饰器会把函数入参/返回值整包塞进 span，含 report 全文，体积爆炸难脱敏）。
- Q1 约束①：enabled 才 import langfuse.openai —— 全局 monkey-patch，未启用/单测环境禁止导包。
- Q2=A1'：trace id 必须 32-hex（4.x 硬性要求），由 client.create_trace_id(seed=唯一串) 确定性生成；
  seed 含 thread_id+时间戳+uuid → 每次运行唯一，同 thread 多次运行不覆盖（Langfuse 按 id upsert）；
  metadata.thread_id 关联可过滤同 thread 全部历史。
- Q2=C1'：循环扁平兄弟 span，命名 R{k}-检索 / R{k}-裁决（轮次前缀便于控制台圈出单轮全貌）；
  span 中文展示名 + metadata.node 英文键。
- Q3=D'：本地对账在 client.py 的 LLMClient.tokens_total（无条件计数），本模块只提供报告入口。
- Q4：三态开关（enabled=auto 三件套齐即启用 / false 强制禁用 / 缺 key 自动降级）+ 知情打印；
  脱敏 mask 回调：str 字段 >truncate_len 截断；敏感替换默认关（LANGFUSE_MASK_SENSITIVE=true 才做
  11 位数字形态替换，避免误伤论文/代码数字）。
- Q5：get_langfuse() 惰性单例 fail-silent（未启用/构造失败 → None）；CLI 退出前 flush + URL 回显
  （自持 trace_id，finally 兜底成功/异常）；Web 不 flush（常驻进程后台线程自然上传）。
- Q6：成本展示 —— token 用 state.token_used 精确值，单价取 config.llm.pricing 最贵 output 档做
  保守上界，人民币为主（W5 eval 复用同一张定价表）。
- Q7：截断长度作为常量放本模块（TRUNCATE_LEN / SPAN_INPUT_TRUNCATE），不进 config 开关。
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from config import config

logger = logging.getLogger(__name__)

# Q7：策略常量进代码，不见开关
TRUNCATE_LEN = 4000           # generation 字段截断（Q4 mask）
SPAN_INPUT_TRUNCATE = 200     # 节点 span input 截断（Q6 体积闭环）


# ---------------- 三态开关（Q4） ----------------

def _enabled() -> bool:
    lc = config.langfuse
    if lc.enabled == "false":
        return False
    if lc.enabled == "true":  # 显式开启仍需三件套
        return bool(lc.public_key and lc.secret_key and lc.host)
    # auto（默认）：三件套齐备即启用
    return bool(lc.public_key and lc.secret_key and lc.host)


# ---------------- 惰性单例（Q5 fail-silent 第二层） ----------------

_langfuse: Any = None
_langfuse_ready = False


def mask_callback(*, data: Any) -> Any:
    """Q4 脱敏回调（langfuse MaskFunction：按字段值调用，input/output/metadata 各自过一遍）。

    仅做长度裁剪；敏感替换默认关（避免误伤论文/代码中的数字信息）。
    """
    lc = config.langfuse
    if isinstance(data, str):
        if len(data) > TRUNCATE_LEN:
            data = data[:TRUNCATE_LEN] + "…[truncated]"
        if lc.mask_sensitive:
            data = re.sub(r"\b\d{11}\b", "***", data)  # 保守：11 位数字（手机号形态）
    return data


def get_langfuse() -> Any:
    """惰性单例。未启用/构造失败返回 None（所有调用点 if lf is not None: 防御）。

    关键约束（Q1）：仅在启用时 import langfuse.openai（触发全局 wrap），
    未启用/单测环境绝不导包 —— 防 monkey-patch 泄漏到其他调用方。
    """
    global _langfuse, _langfuse_ready
    if _langfuse_ready:
        return _langfuse
    _langfuse_ready = True
    if not _enabled():
        _langfuse = None
        return None
    try:
        from langfuse import Langfuse  # 延迟导入：未启用时零依赖零开销

        lc = config.langfuse
        # Q1 约束①：启用才导包，全局 wrap openai 方法（observability 一处 import 覆盖全部 agent）
        import langfuse.openai  # noqa: F401

        _langfuse = Langfuse(
            public_key=lc.public_key,
            secret_key=lc.secret_key,
            host=lc.host,
            sample_rate=lc.sample_rate,
            mask=mask_callback,
        )
        logger.info("Langfuse 可观测性：已启用 (host=%s) | 脱敏：长度裁剪 %d 字符 | 敏感替换: %s",
                    lc.host, TRUNCATE_LEN, "开" if lc.mask_sensitive else "关")
    except Exception as exc:  # Q5 fail-silent 第一层兜底：观测坏了绝不拖累主流程
        logger.warning("Langfuse 初始化失败，观测降级为禁用：%s", exc)
        _langfuse = None
    return _langfuse


def status_line() -> str:
    """Q4 知情打印（CLI/Web 启动时展示：数据去向 + 处理方式）。"""
    lf = get_langfuse()
    if lf is None:
        return ("🔍 Langfuse 可观测性：已禁用"
                "（未配置 LANGFUSE_PUBLIC_KEY/SECRET_KEY/HOST 或 LANGFUSE_ENABLED=false）")
    lc = config.langfuse
    return (f"🔍 Langfuse 可观测性：已启用 (host={lc.host})"
            f" | 脱敏：长度裁剪 {TRUNCATE_LEN} 字符"
            f" | 敏感替换: {'开' if lc.mask_sensitive else '关'}"
            f" | LANGFUSE_ENABLED={lc.enabled}")


# ---------------- trace 结构（Q2） ----------------

def create_trace_id(thread_id: str) -> Optional[str]:
    """Q2（4.x 修正落地）：trace id 硬性要求 32-hex，由官方 create_trace_id(seed=唯一串) 生成。

    seed = {thread_id}|{时间戳}|{uuid8} → 每次运行唯一（同 thread 重跑不覆盖历史 trace）；
    未启用返回 None。
    """
    lf = get_langfuse()
    if lf is None:
        return None
    seed = f"{thread_id}|{time.strftime('%Y%m%d%H%M%S')}|{uuid.uuid4().hex[:8]}"
    return lf.create_trace_id(seed=seed)


@contextmanager
def start_trace(
    trace_id: str,
    topic: str,
    *,
    thread_id: str,
    user_instructions: str = "",
) -> Iterator[Any]:
    """Q2 根 trace：以指定 trace_id 开启（trace_context 传入），一次研究 = 一条 trace。

    未启用时 no-op（yield None）。root span 名 = deepresearch: <topic>（截断 80）。
    """
    lf = get_langfuse()
    if lf is None:
        yield None
        return
    meta: Dict[str, Any] = {"thread_id": thread_id, "sample_rate": config.langfuse.sample_rate}
    inp: Any = {"topic": topic[:SPAN_INPUT_TRUNCATE]}
    if user_instructions:
        inp["user_instructions"] = user_instructions[:SPAN_INPUT_TRUNCATE]
    with lf.start_as_current_observation(
        trace_context={"trace_id": trace_id},
        name=f"deepresearch: {topic[:80]}",
        as_type="span",
        input=inp,
        metadata=meta,
    ) as root:
        yield root


@contextmanager
def span_node(name: str, *, node: str, input: Any = None, **meta: Any) -> Iterator[Any]:
    """Q2 节点 span：中文展示名 + metadata.node 英文键。

    未启用时 no-op（yield None），节点代码零成本降级。
    """
    lf = get_langfuse()
    if lf is None:
        yield None
        return
    metadata: Dict[str, Any] = {"node": node, **meta}
    with lf.start_as_current_observation(name=name, as_type="span", input=input, metadata=metadata) as sp:
        yield sp


# ---------------- 收尾（Q5/Q6） ----------------

def flush_and_get_url(trace_id: str) -> Optional[str]:
    """Q5：CLI 退出前 flush（尽力而为，超时/异常不阻塞退出）+ 回显 trace URL。

    - flush 失败返回 None（上层打印警告）；
    - URL 在 flush 完成后才有效。
    """
    lf = get_langfuse()
    if lf is None or not trace_id:
        return None
    try:
        lf.flush()
    except Exception as exc:  # noqa: BLE001
        logger.warning("Langfuse flush 失败（trace 可能未完整上报）：%s", exc)
        return None
    try:
        return lf.get_trace_url(trace_id=trace_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("获取 trace URL 失败：%s", exc)
        return None


def format_cost_report(token_used: int) -> str:
    """Q6：成本展示 —— 精确 token + 最贵 output 单价保守上界，人民币为主。

    单价取 config.llm.pricing 各模型 output 档的最大值（最保守、永不低估，
    呼应 W1 硬闸的保守精神）；W5 eval 复用同一张定价表算平均 token 成本。
    """
    prices = [p.get("output", 0.0) for p in config.llm.pricing.values()]
    worst = max(prices) if prices else 0.0
    max_cost = token_used / 1000.0 * worst
    return f"💰 本次研究消耗 {token_used:,} tokens（按最贵输出单价保守预估 ≤ ¥{max_cost:.2f}）"