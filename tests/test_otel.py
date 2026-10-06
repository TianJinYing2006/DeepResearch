"""P1-8 OTel 接入单测（零外部 Collector）。"""
from __future__ import annotations

from fakes import FakeStore

import web.backend.otel as otel
import web.backend.usage as usage_module
from research_engine.usage import UsageRecord
from web.backend.usage import make_store_sink


def test_setup_disabled_without_env(monkeypatch):
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)
    monkeypatch.delenv("DR_METRICS_PROMETHEUS", raising=False)
    monkeypatch.setattr(otel, "ENABLED", False)
    assert otel.setup_otel("test-service") is False


def test_record_otel_usage_noop_when_disabled(monkeypatch):
    monkeypatch.setattr(otel, "ENABLED", False)
    otel.record_otel_usage(UsageRecord(kind="llm", provider="dashscope"))  # 不抛即通过


def test_record_otel_usage_records_split_tokens(monkeypatch):
    class _Hist:
        def __init__(self):
            self.points: list[tuple] = []

        def record(self, value, attributes):
            self.points.append((value, attributes))

    class _Counter:
        def __init__(self):
            self.calls: list[tuple] = []

        def add(self, value, attributes):
            self.calls.append((value, attributes))

    hist, counter = _Hist(), _Counter()
    monkeypatch.setattr(otel, "ENABLED", True)
    monkeypatch.setattr(otel, "_token_hist", hist)
    monkeypatch.setattr(otel, "_op_counter", counter)

    otel.record_otel_usage(UsageRecord(
        kind="llm", provider="dashscope", model="qwen-plus", role="critic",
        input_tokens=100, output_tokens=20, total_tokens=120))

    assert [point[0] for point in hist.points] == [100, 20]
    assert {point[1]["gen_ai.token.type"] for point in hist.points} == {"input", "output"}
    assert hist.points[0][1]["gen_ai.request.model"] == "qwen-plus"
    assert counter.calls[0][1]["dr.kind"] == "llm"
    assert counter.calls[0][1]["dr.role"] == "critic"


def test_run_span_emits_span_with_attributes(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(otel, "_tracer", lambda: provider.get_tracer("test"))

    with otel.run_span("dr.run", **{"dr.run_id": "r1", "dr.attempt": 2}):
        pass

    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    assert spans[0].name == "dr.run"
    assert spans[0].attributes["dr.run_id"] == "r1"
    assert spans[0].attributes["dr.attempt"] == 2


def test_usage_sink_calls_otel(monkeypatch):
    recorded: list[UsageRecord] = []
    monkeypatch.setattr(usage_module, "record_otel_usage", recorded.append)

    store = FakeStore()
    sink = make_store_sink(store, run_id="r1", attempt=1)
    sink(UsageRecord(kind="search", provider="bocha", role="web"))

    assert recorded and recorded[0].kind == "search"
    assert store.list_usage(run_id="r1")[0]["kind"] == "search"
