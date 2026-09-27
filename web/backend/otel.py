"""OpenTelemetry 可观测接入（P1-8）。

两种导出路径（可同时启用）：
- **OTLP**（traces + metrics）：设置 `OTEL_EXPORTER_OTLP_ENDPOINT` 即启用（Collector / 后端）；
- **Prometheus 抓取**：`DR_METRICS_PROMETHEUS=true` 时在 API 暴露 `/metrics`。

默认两者都关 ⇒ 零行为变化（span 为 no-op、指标写入 no-op，测试与本地不受影响）。
Resource 属性：`service.name` / `service.version` / `deployment.environment.name`（`DR_ENV`）。
**不落 prompt / 报告正文 / 上传原文**：只记模型、provider、token、kind/role 等低基数属性。

指标遵循 GenAI 语义约定：`gen_ai.client.token.usage`（input/output 拆分）、
`gen_ai.client.operation.count`；业务 span 用于后台任务（Worker / 摄取 / 注销清理），
作为独立根 span，不挂到请求链路上。
"""
from __future__ import annotations

import os
from contextlib import contextmanager
from typing import Any, Iterator, Optional

from opentelemetry import metrics, trace
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

ENABLED = False
PROMETHEUS_ENABLED = False
_token_hist: Optional[Any] = None
_op_counter: Optional[Any] = None


def _env_flag(name: str, default: str = "false") -> bool:
    return (os.getenv(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def setup_otel(service_name: str) -> bool:
    """初始化 OTel（幂等）；返回是否启用。未配置导出端点时保持完全关闭。"""
    global ENABLED, PROMETHEUS_ENABLED
    otlp_endpoint = (os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT") or "").strip()
    prometheus = _env_flag("DR_METRICS_PROMETHEUS")
    if not otlp_endpoint and not prometheus:
        return False
    if ENABLED:
        return True

    resource = Resource.create({
        "service.name": service_name,
        "service.version": (os.getenv("DR_WORKER_VERSION") or "dev"),
        "deployment.environment.name": (os.getenv("DR_ENV") or "local"),
    })
    tracer_provider = TracerProvider(resource=resource)
    metric_readers: list[Any] = []
    if otlp_endpoint:
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        tracer_provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
        metric_readers.append(PeriodicExportingMetricReader(OTLPMetricExporter()))
    trace.set_tracer_provider(tracer_provider)

    if prometheus:
        from opentelemetry.exporter.prometheus import PrometheusMetricReader

        metric_readers.append(PrometheusMetricReader())
    metrics.set_meter_provider(MeterProvider(resource=resource, metric_readers=metric_readers))
    _ensure_meters()

    ENABLED = True
    PROMETHEUS_ENABLED = prometheus
    return True


def _ensure_meters() -> None:
    global _token_hist, _op_counter
    meter = metrics.get_meter("deepresearch")
    _token_hist = meter.create_histogram(
        "gen_ai.client.token.usage", unit="{token}",
        description="LLM/embedding token usage (GenAI semantic conventions)")
    _op_counter = meter.create_counter(
        "gen_ai.client.operation.count", unit="{call}",
        description="provider calls (llm/embedding/search)")


def record_otel_usage(record) -> None:
    """usage sink 旁路（P1-4 → P1-8）：把逐调用用量写进 OTel 指标；未启用 no-op。"""
    if not ENABLED or _token_hist is None or _op_counter is None:
        return
    base = {
        "gen_ai.provider.name": record.provider or "unknown",
        "gen_ai.request.model": record.model or "unknown",
        "dr.kind": record.kind,
        "dr.role": record.role or "unknown",
    }
    if record.input_tokens:
        _token_hist.record(record.input_tokens, {**base, "gen_ai.token.type": "input"})
    if record.output_tokens:
        _token_hist.record(record.output_tokens, {**base, "gen_ai.token.type": "output"})
    if not record.input_tokens and not record.output_tokens and record.total_tokens:
        _token_hist.record(record.total_tokens, {**base, "gen_ai.token.type": "total"})
    _op_counter.add(1, base)


def _tracer():
    """业务 tracer（测试可 monkeypatch 注入内存 exporter）。"""
    return trace.get_tracer("deepresearch")


@contextmanager
def run_span(name: str, **attributes: Any) -> Iterator[Any]:
    """业务 span（后台任务独立根 span）；未启用时是 no-op span。"""
    tracer = _tracer()
    with tracer.start_as_current_span(name, attributes=attributes) as span:
        yield span


def instrument_fastapi(app) -> None:
    if not ENABLED:
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app)


def instrument_httpx() -> None:
    if not ENABLED:
        return
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    HTTPXClientInstrumentor().instrument()


def prometheus_asgi_app():
    """Prometheus 抓取用 ASGI app（仅 `DR_METRICS_PROMETHEUS=true` 时挂载）。"""
    from prometheus_client import make_asgi_app

    return make_asgi_app()
