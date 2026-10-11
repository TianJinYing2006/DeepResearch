"""OTel 可观测层（需求 33：Langfuse → OTel 原生迁移，反转 W3 接入决策）。

设计要点（对应需求 33 §5 TBD，速查）：
- TBD-2：generation 单点显式埋点 —— `LLMClient.chat` 是全项目唯一 LLM 出口，
  在其内部开 `gen_ai.*` span；不做 monkey-patch（吸取 `langfuse.openai` 全局 wrap 的版本耦合教训）。
- TBD-3：根 trace 的 trace_id 由本模块自持（32-hex），经自定义 IdGenerator 播种给根 span
  （**无假父指针**——spike 实测 NonRecordingSpan 方案会让 Langfuse 把根观测标成非根）；
  CLI `finally` 在异常路径也能取到 id 回显 URL（W3 Q5 语义保留）。
- TBD-4：成本本地自算（`config.llm.pricing` 精确单价），逐调用估算写 span 属性
  `dr.cost_estimate_cny`（后端可展示，替代 Langfuse 控制台 cost 列）；
  CLI 汇总行沿用保守上界口径（人民币为主）。
- TBD-5：URL 回显走可配模板 `DR_OTEL_TRACE_URL_TEMPLATE`（如 Jaeger `.../trace/{trace_id}`）。
- TBD-6：三态开关（`DR_OTEL_ENABLED=false` 强制禁用 / 端点齐备自动启用 / 缺端点自动降级）
  + 双层 fail-silent；截断常量进代码不进配置（W3 Q7 纪律保留）。
- TBD-7：OTel 全局 provider 单次 `set` —— 进程内已有 SDK provider（Worker 先初始化 web 侧）
  时直接复用，不重复覆盖；CLI 进程由本模块初始化（`service.name` 可配）。
- 与 `web/backend/otel.py` 同 env（`OTEL_EXPORTER_OTLP_ENDPOINT`）；未配置导出时零行为变化。
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import uuid
from contextlib import contextmanager
from typing import Any, Dict, Iterator, Optional

from config import config

logger = logging.getLogger(__name__)

# 策略常量进代码，不见开关（W3 Q7 / 需求 33 TBD-6）
TRUNCATE_LEN = 4000           # generation 字段截断（messages / completion 预览）
SPAN_INPUT_TRUNCATE = 200     # 节点 span input 截断（体积闭环）


# ---------------- 三态开关（TBD-6） ----------------

def _enabled() -> bool:
    oc = config.otel
    if oc.enabled == "false":
        return False
    return bool((oc.endpoint or "").strip())


# ---------------- 惰性单例 / provider（TBD-7，fail-silent） ----------------

_ready = False
_provider: Any = None


# ---------------- trace id 播种（TBD-3：根 span 无假父指针） ----------------

_seeded_trace_id: Optional[int] = None
_seed_lock = threading.Lock()


def _seed_trace_id(trace_id: str) -> None:
    """为下一条新建根 span 预置 trace_id（由 IdGenerator 一次性消费）。"""
    global _seeded_trace_id
    try:
        value = int(trace_id, 16)
    except (TypeError, ValueError):
        return
    if value <= 0:
        return
    with _seed_lock:
        _seeded_trace_id = value


def _take_seeded_trace_id() -> Optional[int]:
    global _seeded_trace_id
    with _seed_lock:
        value = _seeded_trace_id
        _seeded_trace_id = None
    return value


def _build_seeded_id_generator() -> Any:
    """IdGenerator：优先返回被播种的 trace_id（仅根 span 创建时消费），否则随机。"""
    from opentelemetry.sdk.trace.id_generator import RandomIdGenerator

    class _SeededIdGenerator(RandomIdGenerator):
        def generate_trace_id(self) -> int:
            forced = _take_seeded_trace_id()
            if forced:
                return forced
            return super().generate_trace_id()

    return _SeededIdGenerator()


def _ensure() -> Any:
    """惰性初始化 OTel（幂等）。未启用/构造失败 → None（所有调用点防御）。"""
    global _ready, _provider
    if _ready:
        return _provider
    _ready = True
    if not _enabled():
        return None
    try:
        from opentelemetry import trace as trace_api
        from opentelemetry.sdk.trace import TracerProvider

        existing = trace_api.get_tracer_provider()
        if isinstance(existing, TracerProvider):
            # TBD-7：进程内已有 SDK provider（如 Worker 进程先初始化 web 侧）→ 复用不覆盖
            _provider = existing
            return _provider
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace.export import BatchSpanProcessor

        oc = config.otel
        resource = Resource.create({
            "service.name": oc.service_name,
            "service.version": os.environ.get("DR_WORKER_VERSION", "dev"),
            "deployment.environment.name": os.environ.get("DR_ENV", "local"),
        })
        provider = TracerProvider(resource=resource, id_generator=_build_seeded_id_generator())
        provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        trace_api.set_tracer_provider(provider)
        _provider = provider
        logger.info("OTel 可观测性：已启用 (endpoint=%s, service=%s)", oc.endpoint, oc.service_name)
    except Exception as exc:  # noqa: BLE001 —— TBD-6 第一层兜底：观测坏了绝不拖累主流程
        logger.warning("OTel 初始化失败，观测降级为禁用：%s", exc)
        _provider = None
    return _provider


def _tracer() -> Any:
    provider = _ensure()
    if provider is None:
        return None
    return provider.get_tracer("deepresearch.engine")


# ---------------- 脱敏（TBD-6：常量 + 默认关的敏感替换） ----------------

def mask_text(value: str) -> str:
    """单字段 >TRUNCATE_LEN 截断；敏感替换默认关（`DR_OTEL_MASK_SENSITIVE=true` 才做）。"""
    if not isinstance(value, str):
        return value
    if len(value) > TRUNCATE_LEN:
        value = value[:TRUNCATE_LEN] + "…[truncated]"
    if config.otel.mask_sensitive:
        value = re.sub(r"\b\d{11}\b", "***", value)  # 保守：11 位数字（手机号形态）
    return value


def _preview(value: Any, limit: int = SPAN_INPUT_TRUNCATE) -> Optional[str]:
    """把任意 input 规整为截断字符串（None 原样返回）。"""
    if value is None:
        return None
    try:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    except Exception:  # noqa: BLE001
        text = str(value)
    return text[:limit]


def _attr_value(value: Any) -> Any:
    """把 metadata 值规整为 OTel 可接受的标量（复杂结构降级为截断 JSON）。"""
    if isinstance(value, str):
        return value[:SPAN_INPUT_TRUNCATE]
    if isinstance(value, (bool, int, float)):
        return value
    return _preview(value) or ""


# ---------------- 知情打印（TBD-6） ----------------

def status_line() -> str:
    """CLI/Web 启动时展示：数据去向 + 处理方式。"""
    provider = _ensure()
    if provider is None:
        return ("🔍 OTel 可观测性：已禁用"
                "（未配置 OTEL_EXPORTER_OTLP_ENDPOINT 或 DR_OTEL_ENABLED=false）")
    return (f"🔍 OTel 可观测性：已启用 (endpoint={config.otel.endpoint}, "
            f"service={config.otel.service_name})"
            f" | 脱敏：长度裁剪 {TRUNCATE_LEN} 字符"
            f" | 敏感替换: {'开' if config.otel.mask_sensitive else '关'}")


# ---------------- trace 结构（TBD-3） ----------------

def create_trace_id(thread_id: str) -> Optional[str]:
    """自持 trace id（32-hex，OTel TraceId 格式）。未启用返回 None（走无观测路径）。

    每次运行唯一（同 thread 重跑不共享 id）；thread 维度用 `dr.thread_id` 属性过滤。
    """
    if _ensure() is None:
        return None
    return uuid.uuid4().hex


@contextmanager
def start_trace(
    trace_id: str,
    topic: str,
    *,
    thread_id: str,
    user_instructions: str = "",
) -> Iterator[Any]:
    """根 span（一次研究 = 一条 trace）。未启用时 no-op（yield None）。

    trace_id 由 `create_trace_id` 自持，经 IdGenerator 播种给根 span（**无假父指针**）——
    异常路径 CLI 也能回显 URL（W3 Q5）。注意：若进程内复用了外部 provider（如 Worker
    先初始化 web 侧），播种不生效，根 span 用随机 id（该路径无 URL 回显需求）。
    """
    tracer = _tracer()
    if tracer is None or not trace_id:
        yield None
        return
    attrs: Dict[str, Any] = {
        "dr.thread_id": thread_id,
        "dr.topic": topic[:SPAN_INPUT_TRUNCATE],
        "dr.user_instructions": user_instructions[:SPAN_INPUT_TRUNCATE],
    }
    _seed_trace_id(trace_id)
    with tracer.start_as_current_span(f"deepresearch: {topic[:80]}", attributes=attrs) as root:
        yield root


@contextmanager
def span_node(name: str, *, node: str, input: Any = None, **meta: Any) -> Iterator[Any]:
    """节点 span：中文展示名 + `dr.node` 英文键（W3 B1）；meta 平铺为 `dr.*` 属性。

    未启用时 no-op（yield None），节点代码零成本降级。
    """
    tracer = _tracer()
    if tracer is None:
        yield None
        return
    attrs: Dict[str, Any] = {"dr.node": node}
    preview = _preview(input)
    if preview is not None:
        attrs["dr.input"] = preview
    for key, value in meta.items():
        if value is not None:
            attrs[f"dr.{key}"] = _attr_value(value)
    with tracer.start_as_current_span(name, attributes=attrs) as sp:
        yield sp


# ---------------- LLM generation 单点（TBD-2 / TBD-4） ----------------

@contextmanager
def llm_call_span(model: str, *, role: str, messages: Any = None) -> Iterator[Any]:
    """LLM 调用 span（`gen_ai.*` 语义）。未启用时 no-op（yield None）。"""
    tracer = _tracer()
    if tracer is None:
        yield None
        return
    from opentelemetry.trace import SpanKind

    attrs: Dict[str, Any] = {
        "gen_ai.operation.name": "chat",
        "gen_ai.provider.name": "dashscope",
        "gen_ai.request.model": model,
        "dr.role": role,
    }
    if messages is not None:
        try:
            attrs["dr.messages_preview"] = mask_text(
                json.dumps(messages, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            pass
    with tracer.start_as_current_span(f"chat {model}", kind=SpanKind.CLIENT, attributes=attrs) as sp:
        yield sp


def _estimate_cost_cny(model: str, input_tokens: int, output_tokens: int) -> Optional[float]:
    """按定价表精确估算单次调用成本（元）；模型不在表内返回 None。"""
    price = config.llm.pricing.get(model)
    if not price:
        return None
    try:
        return round(input_tokens / 1000.0 * float(price.get("input", 0.0))
                     + output_tokens / 1000.0 * float(price.get("output", 0.0)), 6)
    except Exception:  # noqa: BLE001
        return None


def record_llm_result(span: Any, resp: Any, content: str = "") -> None:
    """成功路径：usage / cost / 正文预览写入 span（fail-silent，绝不抛）。"""
    if span is None:
        return
    try:
        usage = getattr(resp, "usage", None)
        if usage is not None:
            inp = int(getattr(usage, "prompt_tokens", 0) or 0)
            out = int(getattr(usage, "completion_tokens", 0) or 0)
            total = int(getattr(usage, "total_tokens", 0) or 0)
            span.set_attribute("gen_ai.usage.input_tokens", inp)
            span.set_attribute("gen_ai.usage.output_tokens", out)
            if total:
                span.set_attribute("gen_ai.usage.total_tokens", total)
            cost = _estimate_cost_cny(getattr(resp, "model", "") or "", inp, out)
            if cost is not None:
                span.set_attribute("dr.cost_estimate_cny", cost)
        resp_id = getattr(resp, "id", None)
        if resp_id:
            span.set_attribute("gen_ai.response.id", str(resp_id))
        if content:
            span.set_attribute("dr.completion_preview", mask_text(content))
    except Exception as exc:  # noqa: BLE001 —— 观测旁路绝不拖累主链路
        logger.warning("OTel generation 记录失败（忽略）：%s", exc)


# ---------------- 收尾（TBD-5 / 成本） ----------------

def flush_and_get_url(trace_id: str) -> Optional[str]:
    """CLI 退出前 flush（尽力而为，超时不阻塞退出）+ 按模板回显 trace URL。

    - 未启用 / 未配置模板 → None；
    - flush 失败 → None（上层打印警告）；URL 在 flush 完成后才有意义。
    """
    provider = _ensure()
    if provider is None or not trace_id:
        return None
    try:
        provider.force_flush(timeout_millis=5000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("OTel flush 失败（trace 可能未完整上报）：%s", exc)
        return None
    template = (config.otel.trace_url_template or "").strip()
    if not template:
        return None
    try:
        return template.format(trace_id=trace_id)
    except Exception as exc:  # noqa: BLE001
        logger.warning("trace URL 模板渲染失败：%s", exc)
        return None


def format_cost_report(token_used: int) -> str:
    """成本展示 —— 精确 token + 最贵 output 单价保守上界，人民币为主（W3 Q6 口径保留）。

    单价取 config.llm.pricing 各模型 output 档的最大值（最保守、永不低估，
    呼应 W1 硬闸的保守精神）；W5 eval 复用同一张定价表算平均 token 成本。
    """
    prices = [p.get("output", 0.0) for p in config.llm.pricing.values()]
    worst = max(prices) if prices else 0.0
    max_cost = token_used / 1000.0 * worst
    return f"💰 本次研究消耗 {token_used:,} tokens（按最贵输出单价保守预估 ≤ ¥{max_cost:.2f}）"
