# -*- coding: utf-8 -*-
"""需求 33 OTel observability 离线单测（全 mock / 零外发，替代 W3 Langfuse 版）。

覆盖：
- 三态开关判定（`DR_OTEL_ENABLED=false` 强制禁用 / 缺端点自动降级 / 端点齐备自动启用）；
- fail-silent：未启用时 span / trace / generation 全部 no-op；
- `create_trace_id` 32-hex 唯一性；`start_trace` 强制 trace_id + 节点 span 父子关系（InMemory 断言）；
- `span_node` 属性（dr.node / dr.round / dr.input 截断）；`llm_call_span` + `record_llm_result`
  （gen_ai.usage / 成本估算 / 预览截断）；
- `mask_text` 截断与敏感替换（默认关）；URL 模板回显；format_cost_report 保守上界；
- 守卫：research_engine 全包无 langfuse import（防回退）。
"""
import ast
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research_engine.observability as obs  # noqa: E402
from config import config  # noqa: E402
from research_engine.llm.client import LLMClient  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent


def _reset_obs():
    """重置模块级惰性单例缓存（测试间隔离）。"""
    obs._ready = False
    obs._provider = None


@pytest.fixture(autouse=True)
def _clean_obs():
    _reset_obs()
    yield
    _reset_obs()


def _enable_with_memory(monkeypatch):
    """注入 InMemory TracerProvider（绕过全局 set，测试间零污染）。"""
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(obs, "_ready", True)
    monkeypatch.setattr(obs, "_provider", provider)
    return exporter


# ---------- 三态开关 / 隔离 ----------

def test_conftest_forces_disabled():
    """三重隔离①：conftest 必须在测试集合中强制禁用（覆盖 .env 里的任何配置）。"""
    assert os.environ.get("DR_OTEL_ENABLED") == "false"
    assert config.otel.enabled == "false"


def test_enabled_three_states(monkeypatch):
    monkeypatch.setattr(config.otel, "enabled", "false")
    monkeypatch.setattr(config.otel, "endpoint", "http://127.0.0.1:4318")
    assert obs._enabled() is False  # 强制禁用优先
    monkeypatch.setattr(config.otel, "enabled", "auto")
    monkeypatch.setattr(config.otel, "endpoint", "")
    assert obs._enabled() is False  # 缺端点自动降级
    monkeypatch.setattr(config.otel, "endpoint", "http://127.0.0.1:4318")
    assert obs._enabled() is True  # 端点齐备自动启用


def test_disabled_noop_everywhere(monkeypatch):
    monkeypatch.setattr(config.otel, "enabled", "false")
    assert "已禁用" in obs.status_line()
    assert obs.create_trace_id("dr-1") is None
    with obs.start_trace("a" * 32, "topic", thread_id="dr-1") as root:
        assert root is None
    with obs.span_node("检索 R1", node="research", depth=1) as sp:
        assert sp is None
    with obs.llm_call_span("qwen-plus", role="writer") as gen:
        assert gen is None
    assert obs.flush_and_get_url("") is None


def test_ensure_reuses_existing_sdk_provider(monkeypatch):
    """TBD-7：进程内已有 SDK provider（Worker 先初始化）→ 复用不覆盖、不重复 set。"""
    from opentelemetry import trace as trace_api
    from opentelemetry.sdk.trace import TracerProvider

    existing = TracerProvider()
    monkeypatch.setattr(config.otel, "enabled", "true")
    monkeypatch.setattr(config.otel, "endpoint", "http://127.0.0.1:4318")
    monkeypatch.setattr(trace_api, "get_tracer_provider", lambda: existing)
    assert obs._ensure() is existing


# ---------- trace 结构 ----------

def test_create_trace_id_unique_32hex(monkeypatch):
    _enable_with_memory(monkeypatch)
    a = obs.create_trace_id("dr-abc")
    b = obs.create_trace_id("dr-abc")  # 同 thread：每次运行唯一（不共享 id）
    assert a and b and a != b
    assert len(a) == 32 and len(b) == 32
    assert all(c in "0123456789abcdef" for c in a + b)


def test_start_trace_forces_trace_id_and_node_parenting(monkeypatch):
    exporter = _enable_with_memory(monkeypatch)
    tid = "ab" * 16
    with obs.start_trace(tid, "研究主题" * 40, thread_id="dr-x", user_instructions="要求"):
        with obs.span_node("R1-检索", node="research", input={"query": "q"}, depth=1) as sp:
            assert sp is not None
    spans = exporter.get_finished_spans()
    names = [s.name for s in spans]
    assert "R1-检索" in names
    root = next(s for s in spans if s.name.startswith("deepresearch: "))
    child = next(s for s in spans if s.name == "R1-检索")
    assert format(root.context.trace_id, "032x") == tid  # 强制 trace_id 生效
    assert format(child.context.trace_id, "032x") == tid  # 子 span 同 trace
    assert child.parent is not None and child.parent.span_id == root.context.span_id  # 父子关系
    assert root.attributes["dr.thread_id"] == "dr-x"
    assert len(root.name) <= len("deepresearch: ") + 80  # 根 span 名截断


def test_span_node_input_truncated_and_meta_attrs(monkeypatch):
    exporter = _enable_with_memory(monkeypatch)
    with obs.span_node("规划", node="plan", input={"topic": "长" * 500}, round=2, sq_id="s1", flag=True):
        pass
    sp = exporter.get_finished_spans()[0]
    assert sp.attributes["dr.node"] == "plan"
    assert len(sp.attributes["dr.input"]) <= obs.SPAN_INPUT_TRUNCATE
    assert sp.attributes["dr.round"] == 2
    assert sp.attributes["dr.sq_id"] == "s1"
    assert sp.attributes["dr.flag"] is True


# ---------- LLM generation（单点埋点） ----------

def test_llm_span_records_usage_cost_and_preview(monkeypatch):
    exporter = _enable_with_memory(monkeypatch)
    from types import SimpleNamespace

    resp = SimpleNamespace(
        usage=SimpleNamespace(prompt_tokens=1000, completion_tokens=100, total_tokens=1100),
        model="qwen-plus", id="req-1",
    )
    with obs.llm_call_span("qwen-plus", role="writer", messages=[{"role": "user", "content": "hi"}]) as gen:
        obs.record_llm_result(gen, resp, "OK")
    sp = exporter.get_finished_spans()[0]
    assert sp.name == "chat qwen-plus"
    assert sp.attributes["gen_ai.provider.name"] == "dashscope"
    assert sp.attributes["gen_ai.request.model"] == "qwen-plus"
    assert sp.attributes["dr.role"] == "writer"
    assert sp.attributes["gen_ai.usage.input_tokens"] == 1000
    assert sp.attributes["gen_ai.usage.output_tokens"] == 100
    assert sp.attributes["gen_ai.usage.total_tokens"] == 1100
    assert sp.attributes["gen_ai.response.id"] == "req-1"
    # qwen-plus 定价：1000/1000*0.0008 + 100/1000*0.002 = 0.001
    assert abs(sp.attributes["dr.cost_estimate_cny"] - 0.001) < 1e-9
    assert sp.attributes["dr.completion_preview"] == "OK"
    assert len(sp.attributes["dr.messages_preview"]) <= obs.TRUNCATE_LEN + 20


def test_chat_emits_generation_span_via_single_point(monkeypatch):
    """需求 33 TBD-2 回归：走 chat() 真实路径 → generation span 自动记录（单点埋点）。"""
    exporter = _enable_with_memory(monkeypatch)
    from types import SimpleNamespace

    LLMClient.tokens_total = 0

    def fake_create(**kwargs):
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content="OK"))],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=23, total_tokens=123),
            model="qwen-plus", id="req-chat",
        )

    client = LLMClient()
    monkeypatch.setattr(client, "_get_client", lambda: SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))))

    out = client.chat([{"role": "user", "content": "hi"}])
    assert out == "OK"
    gens = [s for s in exporter.get_finished_spans() if s.name.startswith("chat ")]
    assert len(gens) == 1
    assert gens[0].attributes["gen_ai.usage.total_tokens"] == 123
    LLMClient.tokens_total = 0  # 还原


# ---------- 脱敏 / 成本 / URL ----------

def test_mask_text_truncates_and_sensitive_toggle(monkeypatch):
    monkeypatch.setattr(config.otel, "mask_sensitive", False)
    long_text = "长" * (obs.TRUNCATE_LEN + 100)
    out = obs.mask_text(long_text)
    assert len(out) <= obs.TRUNCATE_LEN + 20
    assert out.endswith("…[truncated]")
    assert "12345678901" in obs.mask_text("编号 12345678901")  # 默认不替换
    monkeypatch.setattr(config.otel, "mask_sensitive", True)
    masked = obs.mask_text("联系 12345678901 手机号")
    assert "12345678901" not in masked and "***" in masked


def test_flush_and_get_url_template(monkeypatch):
    _enable_with_memory(monkeypatch)
    monkeypatch.setattr(config.otel, "trace_url_template",
                        "http://localhost:16686/trace/{trace_id}")
    assert obs.flush_and_get_url("ab" * 16) == "http://localhost:16686/trace/" + "ab" * 16
    monkeypatch.setattr(config.otel, "trace_url_template", "")
    assert obs.flush_and_get_url("ab" * 16) is None  # 无模板 = 不回显


def test_format_cost_report_conservative(monkeypatch):
    monkeypatch.setattr(config.llm, "pricing", {
        "qwen-turbo": {"input": 0.0001, "output": 0.0002},
        "qwen-plus": {"input": 0.0002, "output": 0.001},
    })
    report = obs.format_cost_report(100_000)
    assert "100,000 tokens" in report
    assert "¥" in report
    assert "¥0.10" in report  # 100k/1000 * 0.001（最贵 output）保守上界


# ---------- 守卫：引擎侧零 langfuse import ----------

def test_engine_has_no_langfuse_import():
    offenders = []
    for path in (REPO_ROOT / "research_engine").rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                if any(alias.name.split(".")[0] == "langfuse" for alias in node.names):
                    offenders.append(str(path))
            elif isinstance(node, ast.ImportFrom):
                if (node.module or "").split(".")[0] == "langfuse":
                    offenders.append(str(path))
    assert offenders == [], f"引擎侧残留 langfuse import：{offenders}"
