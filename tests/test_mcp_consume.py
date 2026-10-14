"""需求 37 M1：外部 MCP 消费单测（零网络；caller 全注入，无真实 MCP server）。

覆盖：
- 配置解析与 URL 安全矩阵（https/凭据/私网/放行/上限，fail-fast）；
- 失败映射五值（零新增枚举）+ 调用隔离；
- 结果归一化（不可见字符剥离 / 字节与条数上限 / empty_result）；
- 关闭态零漂移（targets 硬断言）+ 启用态并入；
- 记账（kind='tool'，成功/失败均记）+ 降级留痕（empty_result 不算故障）；
- 三处注册（池 adopted / 展示 format_tool_detail）+ 渲染来源标签；
- egress 终点登记（仅启用时出现）与 provenance 开关+指纹（无 DNS）。
"""
from __future__ import annotations

import json

import pytest

from config import config
from research_engine.agents.researcher import Researcher, format_tool_detail, pool_and_trim
from research_engine.failure_reasons import FailureReason
from research_engine.mcp.consumer import (
    McpConfigError,
    McpConsumer,
    McpServerConfig,
    McpToolCallResult,
    build_mcp_consumer,
    classify_mcp_exception,
    parse_servers,
    server_fingerprint,
    server_hosts,
)
from research_engine.render import ReportRenderer
from research_engine.search.base import SearchResponse
from research_engine.state import ResearchFinding
from research_engine.usage import UsageRecord, use_usage_sink


def _server(**kw) -> McpServerConfig:
    base = dict(name="wechatbot", url="https://93.184.216.34/mcp", tools=("search",))
    base.update(kw)
    return McpServerConfig(**base)


class FakeCaller:
    """注入式 caller：返回预设结果或抛预设异常；记录调用序（server, tool, query）。"""

    def __init__(self, result):
        self.result = result
        self.calls: list = []

    def __call__(self, server, tool, query):
        self.calls.append((server.name, tool, query))
        if isinstance(self.result, BaseException):
            raise self.result
        return self.result


# ---- 配置解析与 URL 安全矩阵 ----


def test_parse_servers_valid_defaults():
    raw = json.dumps([{"name": "wb", "url": "https://93.184.216.34/mcp",
                       "tools": ["search"]}])
    servers = parse_servers(raw)
    assert servers[0].name == "wb"
    assert servers[0].timeout == 15.0
    assert servers[0].max_result_bytes == 64 * 1024
    assert servers[0].max_findings == 5
    assert servers[0].allow_private is False


def test_parse_servers_empty_means_unconfigured():
    assert parse_servers("") == []
    assert parse_servers("   ") == []


@pytest.mark.parametrize("payload", [
    "not-json",                                                              # 非 JSON
    json.dumps({"name": "x"}),                                               # 非数组
    json.dumps([{"url": "https://93.184.216.34/mcp", "tools": ["s"]}]),      # 缺 name
    json.dumps([{"name": "x", "tools": ["s"]}]),                             # 缺 url
    json.dumps([{"name": "x", "url": "http://93.184.216.34/mcp", "tools": ["s"]}]),   # 非 https
    json.dumps([{"name": "x", "url": "https://u:p@93.184.216.34/mcp", "tools": ["s"]}]),  # 凭据
    json.dumps([{"name": "x", "url": "https://93.184.216.34/mcp", "tools": []}]),    # 空工具
    json.dumps([{"name": "x", "url": "https://10.0.0.1/mcp", "tools": ["s"]}]),      # 私网无放行
    json.dumps([{"name": "x", "url": "https://10.0.0.1/mcp", "tools": ["s"],
                 "allow_private": True}]),                               # 放行缺理由
    json.dumps([{"name": "x", "url": "https://127.0.0.1/mcp", "tools": ["s"],
                 "allow_private": True, "private_reason": "r"}]),        # 环回一律拒绝
    json.dumps([{"name": "x", "url": "https://93.184.216.34/mcp", "tools": ["s"],
                 "timeout": 99}]),                                       # 超上限 15s
    json.dumps([{"name": "x", "url": "https://93.184.216.34/mcp", "tools": ["s"]},
                {"name": "x", "url": "https://93.184.216.34/mcp", "tools": ["s"]}]),  # 重名
])
def test_parse_servers_rejects_bad_configs(payload):
    with pytest.raises(McpConfigError):
        parse_servers(payload)


def test_parse_servers_allows_private_with_reason():
    raw = json.dumps([{"name": "x", "url": "https://10.0.0.1/mcp", "tools": ["s"],
                       "allow_private": True, "private_reason": "内部灰度（审计留痕）"}])
    servers = parse_servers(raw)
    assert servers[0].allow_private is True
    assert servers[0].private_reason


def test_build_consumer_switch_and_config(monkeypatch):
    monkeypatch.setattr(config.mcp, "consume_enabled", False)
    assert build_mcp_consumer() is None
    monkeypatch.setattr(config.mcp, "consume_enabled", True)
    monkeypatch.setattr(config.mcp, "servers_json", "")
    assert build_mcp_consumer() is None  # 开关开但未配置 ⇒ 不并入（零噪声）
    monkeypatch.setattr(config.mcp, "servers_json",
                        json.dumps([{"name": "wb", "url": "https://93.184.216.34/mcp",
                                     "tools": ["s"]}]))
    consumer = build_mcp_consumer()
    assert consumer is not None and consumer.servers[0].name == "wb"
    monkeypatch.setattr(config.mcp, "servers_json", "{bad json")
    with pytest.raises(McpConfigError):
        build_mcp_consumer()  # 启用 + 非法配置 ⇒ fail-fast


# ---- 失败映射（五值零新增）与调用隔离 ----


def test_classify_mcp_exception_maps_existing_five_values():
    assert classify_mcp_exception(TimeoutError("t")) == FailureReason.TIMEOUT.value
    assert classify_mcp_exception(RuntimeError("connection refused")) == \
        FailureReason.PROVIDER_ERROR.value
    assert classify_mcp_exception(ValueError("invalid json payload")) == \
        FailureReason.PARSE_ERROR.value


def test_consumer_maps_caller_exception_to_provider_error():
    consumer = McpConsumer([_server()], caller=FakeCaller(RuntimeError("boom")))
    out = consumer.search("q")[0]
    assert out.failure_reason == FailureReason.PROVIDER_ERROR.value
    assert out.items == ()


def test_consumer_passes_through_caller_failure_reason():
    consumer = McpConsumer([_server()], caller=FakeCaller(
        McpToolCallResult(failure_reason=FailureReason.TIMEOUT.value, detail="t")))
    out = consumer.search("q")[0]
    assert out.failure_reason == FailureReason.TIMEOUT.value


def test_consumer_iterates_servers_and_tools():
    caller = FakeCaller(McpToolCallResult(texts=("x",)))
    consumer = McpConsumer([
        _server(name="a", tools=("s1", "s2")),
        _server(name="b", tools=("s3",)),
    ], caller=caller)
    outcomes = consumer.search("q")
    assert [(o.server, o.tool) for o in outcomes] == [("a", "s1"), ("a", "s2"), ("b", "s3")]
    assert [c[2] for c in caller.calls] == ["q", "q", "q"]


# ---- 归一化（清洗 / 截断 / 条数上限 / empty_result）----


def test_consumer_strips_invisible_and_empty_is_empty_result():
    consumer = McpConsumer([_server()], caller=FakeCaller(
        McpToolCallResult(texts=("a\u200bb",))))
    out = consumer.search("q")[0]
    assert out.items == ("ab",)
    assert out.failure_reason is None

    consumer2 = McpConsumer([_server()], caller=FakeCaller(McpToolCallResult(texts=())))
    out2 = consumer2.search("q")[0]
    assert out2.failure_reason == FailureReason.EMPTY_RESULT.value


def test_consumer_truncates_to_byte_budget_and_caps_findings():
    texts = ("a" * 10, "b" * 10, "c" * 10)
    srv = _server(max_findings=2, max_result_bytes=15)
    consumer = McpConsumer([srv], caller=FakeCaller(McpToolCallResult(texts=texts)))
    out = consumer.search("q")[0]
    assert len(out.items) == 2  # 条数上限
    assert out.items[0] == "a" * 10
    assert out.items[1].startswith("bbbbb") and out.items[1].endswith("…[截断]")  # 字节预算


# ---- 关闭态零漂移 / 启用态并入（targets 硬断言）----


class _FakeSearch:
    def search(self, query, max_results=8):  # noqa: ARG002
        return SearchResponse(query=query, results=[])


def _stub_all_tools(monkeypatch):
    monkeypatch.setattr(Researcher, "_search_web", lambda self, q: [])
    monkeypatch.setattr(Researcher, "_search_rag", lambda self, q: [])
    monkeypatch.setattr(Researcher, "_search_arxiv", lambda self, q: [])
    monkeypatch.setattr(Researcher, "_search_code", lambda self, q: [])


def test_disabled_targets_unchanged(monkeypatch):
    _stub_all_tools(monkeypatch)
    researcher = Researcher()
    assert researcher._mcp is None  # 默认关闭
    _, stats = researcher.search_once("q")
    expected = {"web", "rag"}
    if config.search.enable_arxiv:
        expected.add("arxiv")
    tool_keys = {k for k in stats if k not in ("adopted", "code_failed")}
    assert tool_keys == expected  # mcp 不在 ⇒ 与现状逐项一致（硬断言）
    assert "mcp" not in stats
    assert "mcp" not in format_tool_detail(stats)


def test_enabled_consumer_joins_targets(monkeypatch):
    _stub_all_tools(monkeypatch)
    researcher = Researcher()
    researcher._mcp = McpConsumer([_server()], caller=FakeCaller(
        McpToolCallResult(texts=("外部结果",))))
    pooled, stats = researcher.search_once("q")
    assert "mcp" in stats and stats["mcp"] == 1
    assert any(f.source_type == "mcp" for f in pooled)
    assert "mcp 1/1" in format_tool_detail(stats)


# ---- 记账（kind='tool'）与降级留痕 ----


def test_search_mcp_findings_identity_and_accounting(monkeypatch):
    records: list[UsageRecord] = []
    researcher = Researcher()
    researcher._mcp = McpConsumer([_server()], caller=FakeCaller(
        McpToolCallResult(texts=("结果一", "结果二"))))
    with use_usage_sink(records.append):
        findings = researcher._search_mcp("q")

    assert [f.source_type for f in findings] == ["mcp", "mcp"]
    meta = findings[0].metadata
    assert meta["server_id"] == "wechatbot" and meta["tool_name"] == "search"
    assert meta["call_id"] and meta["retrieval_time"] > 0
    assert findings[0].source.startswith("mcp:wechatbot:search:")
    assert findings[0].source != findings[1].source  # 内容指纹区分（去重契约）
    assert records == [UsageRecord(kind="tool", provider="wechatbot", role="search")]


def test_search_mcp_accounts_failures_and_records_degradation():
    records: list[UsageRecord] = []
    researcher = Researcher()
    researcher._mcp = McpConsumer([_server()], caller=FakeCaller(
        McpToolCallResult(failure_reason=FailureReason.TIMEOUT.value, detail="t")))
    with use_usage_sink(records.append):
        findings = researcher._search_mcp("q")
    assert findings == []
    assert records == [UsageRecord(kind="tool", provider="wechatbot", role="search")]
    entries = researcher.drain_degradations()
    assert [(e.component, e.reason) for e in entries] == [("mcp:wechatbot", "timeout")]


def test_search_mcp_empty_result_is_not_fault():
    researcher = Researcher()
    researcher._mcp = McpConsumer([_server()], caller=FakeCaller(McpToolCallResult()))
    assert researcher._search_mcp("q") == []
    assert researcher.drain_degradations() == []  # D-03：empty_result 不算故障


def test_search_mcp_noop_when_disabled():
    researcher = Researcher()
    assert researcher._mcp is None
    assert researcher._search_mcp("q") == []


# ---- 三处注册（池 / 展示）与渲染标签 ----


def test_pool_adopted_and_detail_include_mcp():
    finding = ResearchFinding(content="x", source="mcp:wb:search:abcd",
                              source_type="mcp", confidence=0.6)
    pooled, stats = pool_and_trim({"web": [], "rag": [], "arxiv": [], "mcp": [finding]})
    assert pooled == [finding]
    assert stats["adopted"]["mcp"] == 1
    assert "mcp 1/1" in format_tool_detail(stats)
    assert format_tool_detail({"web": 1, "adopted": {"web": 1}}) == "web 1/1"  # 无 mcp 键不显示


def test_render_mcp_source_label():
    finding = ResearchFinding(content="x", source="mcp:wechatbot:search:abcd1234",
                              source_type="mcp", confidence=0.6,
                              metadata={"server_id": "wechatbot"})
    out = ReportRenderer().annotate_types("结论 [来源: 1]", [finding])
    assert "🧩mcp:wechatbot" in out


# ---- egress 终点登记 / provenance 开关+指纹（无 DNS 路径）----


def test_egress_snapshot_registers_mcp_only_when_enabled(monkeypatch):
    from web.backend.egress import build_egress_snapshot

    monkeypatch.setattr(config.mcp, "consume_enabled", False)
    monkeypatch.setattr(config.mcp, "servers_json", "")
    assert "mcp" not in build_egress_snapshot(None)  # 关闭态逐字段零漂移

    monkeypatch.setattr(config.mcp, "consume_enabled", True)
    monkeypatch.setattr(config.mcp, "servers_json", json.dumps(
        [{"name": "wb", "url": "https://mcp.internal.example/mcp", "tools": ["s"]}]))
    snap = build_egress_snapshot(None)
    assert snap["mcp"] == {"consume_enabled": True,
                           "endpoint_hosts": ["mcp.internal.example"]}


def test_provenance_snapshot_has_mcp_switches_and_fingerprint(monkeypatch):
    from research_engine.eval.provenance import config_snapshot

    monkeypatch.setattr(config.mcp, "consume_enabled", False)
    monkeypatch.setattr(config.mcp, "server_enabled", False)
    monkeypatch.setattr(config.mcp, "servers_json", json.dumps(
        [{"name": "wb", "url": "https://mcp.internal.example/mcp", "tools": ["s"]}]))
    snap = config_snapshot()
    assert snap["mcp_switches"] == {"consume": False, "server": False}
    assert snap["mcp_servers"] == ["wb@mcp.internal.example"]  # 指纹无 DNS、不含密钥

    monkeypatch.setattr(config.mcp, "servers_json", "{bad")
    assert server_fingerprint() == [] and server_hosts() == []  # 旁路容错
