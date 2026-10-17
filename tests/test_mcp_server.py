"""需求 37 M2：对外 MCP Server 单测（零网络；provider / service 全注入）。

覆盖：
- Bearer 鉴权（缺/错/query-string 拒绝；未配置 fail-closed；纯头口径）；
- 协议路径（ASGI 级 initialize / tools/list / tools/call，手动驱动 session lifespan）；
- 四工具语义：参数上限、失败五值透传、准入单一口径（deep_research → start_research）、
  service principal 归属（不存在与无权同口径）、输出 gate 与截断；
- 记账（kind='search'，per_call）与审计（只记请求哈希，不落原始 prompt）。
"""
from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from research_engine.search.base import SearchResponse, SearchResult
from web.backend import main as api
from web.backend import mcp_server
from web.backend.errors import ApiError

TOKEN = "test-mcp-token"

_INIT_PARAMS = {"protocolVersion": "2025-03-26", "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"}}


class FakeProvider:
    def __init__(self, resp=None, exc=None):
        self.resp = resp
        self.exc = exc
        self.calls = []

    def search(self, query, max_results=8):
        self.calls.append((query, max_results))
        if self.exc is not None:
            raise self.exc
        return self.resp


def _resp(results=(), failure_reason=None, failure_detail=""):
    return SearchResponse(query="q", results=list(results),
                          failure_reason=failure_reason, failure_detail=failure_detail)


@pytest.fixture
def mcp_token(monkeypatch):
    """固定测试令牌（build_mcp_asgi 在调用时读取模块全局）。"""
    monkeypatch.setattr(mcp_server, "MCP_TOKEN", TOKEN)


def _rpc(method, params=None, *, token=TOKEN, path="/mcp/"):
    """ASGI 级 JSON-RPC 调用（每次构建独立实例并驱动 session manager；零网络）。"""
    app, fastmcp = mcp_server.build_mcp_asgi()
    payload = {"jsonrpc": "2.0", "id": 1, "method": method}
    if params is not None:
        payload["params"] = params

    async def _call():
        transport = httpx.ASGITransport(app=app)
        headers = {"Accept": "application/json, text/event-stream"}
        if token is not None:
            headers["Authorization"] = f"Bearer {token}"
        async with fastmcp.session_manager.run():
            async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
                return await client.post(path, json=payload, headers=headers)

    return asyncio.run(_call())


def _tool_call(name, arguments):
    r = _rpc("tools/call", {"name": name, "arguments": arguments})
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["result"]["isError"] is False, data
    return json.loads(data["result"]["content"][0]["text"])


# ---- 鉴权 ----


def test_auth_rejects_missing_wrong_and_query_string_token(mcp_token):
    assert _rpc("initialize", _INIT_PARAMS, token=None).status_code == 401
    assert _rpc("initialize", _INIT_PARAMS, token="wrong").status_code == 401
    # 只认 Authorization 头：query-string 里的 token 不算数
    r = _rpc("initialize", _INIT_PARAMS, token=None, path=f"/mcp/?token={TOKEN}")
    assert r.status_code == 401


def test_auth_fail_closed_when_token_unset(monkeypatch):
    monkeypatch.setattr(mcp_server, "MCP_TOKEN", "")
    assert _rpc("initialize", _INIT_PARAMS, token="anything").status_code == 401


# ---- 协议路径 ----


def test_initialize_and_tools_list(mcp_token):
    r = _rpc("initialize", _INIT_PARAMS)
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "ikaoju"
    r2 = _rpc("tools/list")
    assert r2.status_code == 200
    names = {t["name"] for t in r2.json()["result"]["tools"]}
    assert names == {"quick_search", "deep_research", "research_status", "research_report"}


def test_tools_call_quick_search_end_to_end(mcp_token, monkeypatch):
    provider = FakeProvider(_resp([SearchResult(title="标题", url="https://e.com/a",
                                                snippet="摘要")]))
    monkeypatch.setattr(mcp_server, "create_search_provider", lambda name: provider)
    out = _tool_call("quick_search", {"query": "hello", "max_results": 3})
    assert out["results"] == [{"title": "标题", "url": "https://e.com/a", "snippet": "摘要"}]
    assert provider.calls == [("hello", 3)]
    assert out["query_hash"] and out["failure_reason"] is None


# ---- quick_search 语义 ----


def test_quick_search_validation_limits_and_failure(monkeypatch):
    provider = FakeProvider(_resp(failure_reason="timeout", failure_detail="slow"))
    monkeypatch.setattr(mcp_server, "create_search_provider", lambda name: provider)

    with pytest.raises(ToolError, match="empty_query"):
        mcp_server.quick_search("   ")
    with pytest.raises(ToolError, match="query_too_long"):
        mcp_server.quick_search("x" * (mcp_server.MAX_QUERY_CHARS + 1))

    out = mcp_server.quick_search("q", max_results=999)
    assert provider.calls[-1] == ("q", mcp_server.MAX_RESULTS)  # 条数上限
    assert out["failure_reason"] == "timeout"  # 五值透传（结构化，不抛）

    provider.exc = RuntimeError("boom")
    out2 = mcp_server.quick_search("q")
    assert out2["results"] == [] and out2["failure_reason"] == "provider_error"


def test_quick_search_accounts_with_store(monkeypatch):
    records = []
    captured = {}

    def fake_make_sink(store, **kw):
        captured.update(kw)
        return records.append

    monkeypatch.setattr(api, "store", object())  # 非 None 即挂 store sink
    monkeypatch.setattr("web.backend.usage.make_store_sink", fake_make_sink)
    monkeypatch.setattr(mcp_server, "create_search_provider",
                        lambda name: FakeProvider(_resp([SearchResult(title="t", url="u",
                                                                      snippet="s")])))
    mcp_server.quick_search("q")
    assert captured["detail"] == {"role": "mcp_quick_search"}
    assert records and records[0].kind == "search" and records[0].role == "mcp_quick_search"


def test_rate_limit_denies(monkeypatch):
    class _Deny:
        def allow(self, key):  # noqa: ARG002
            return False

    monkeypatch.setattr(mcp_server, "_limiter", lambda: _Deny())
    with pytest.raises(ToolError, match="rate_limited"):
        mcp_server.quick_search("q")


# ---- deep_research：准入单一口径 + principal ----


def test_deep_research_delegates_to_service_with_principal(monkeypatch):
    calls = []

    def fake_start_research(**kw):
        calls.append(kw)
        return "run-abc123"

    monkeypatch.setattr(api, "start_research", fake_start_research)
    out = mcp_server.deep_research("主题A", "说明B", "standard")
    assert out["run_id"] == "run-abc123"
    assert out["status_url"] == "/api/research/run-abc123"

    kw = calls[0]
    assert kw["user_id"] == "mcp:ikaoju"  # 单一 service principal（TBD-10）
    assert kw["topic"] == "主题A" and kw["profile_name"] == "standard"
    assert kw["idempotency_key"].startswith("mcp-mcp:ikaoju-")
    # 同请求派生同一幂等键（重复调用返回同一 run 的语义）
    mcp_server.deep_research("主题A", "说明B", "standard")
    assert calls[0]["idempotency_key"] == calls[1]["idempotency_key"]


def test_deep_research_maps_api_error_and_validates(monkeypatch):
    def blocked(**kw):
        raise ApiError("content_blocked", "输入包含不允许的内容")

    monkeypatch.setattr(api, "start_research", blocked)
    with pytest.raises(ToolError, match="content_blocked"):
        mcp_server.deep_research("x")

    with pytest.raises(ToolError, match="empty_topic"):
        mcp_server.deep_research("  ")
    with pytest.raises(ToolError, match="topic_too_long"):
        mcp_server.deep_research("t" * (mcp_server.MAX_TOPIC_CHARS + 1))
    with pytest.raises(ToolError, match="instructions_too_long"):
        mcp_server.deep_research("t", "i" * (mcp_server.MAX_INSTRUCTIONS_CHARS + 1))


def test_deep_research_provisions_principal_once(monkeypatch):
    """需求 37 M2：主体惰性开户（runs.user_id 外键完整性）；进程内只开户一次。"""
    calls = []

    class _FakeStore:
        def ensure_service_principal(self, user_id, *, email, password_hash):
            calls.append((user_id, email, password_hash))
            return True

    monkeypatch.setattr(api, "store", _FakeStore())
    monkeypatch.setattr(mcp_server, "_principal_ready", False)
    monkeypatch.setattr(api, "start_research", lambda **kw: "run-p")
    monkeypatch.setattr(api, "_audit", lambda action, **kw: None)

    mcp_server.deep_research("主题甲")
    mcp_server.deep_research("主题乙")
    assert len(calls) == 1  # 进程内只开户一次
    assert calls[0][0] == "mcp:ikaoju"
    assert calls[0][1] == "ikaoju@mcp.invalid"  # 保留域（不可路由）
    assert calls[0][2]  # 一次性随机口令的 Argon2id 哈希（不可登录）


# ---- research_status：所有权 + 投影 ----


def test_research_status_ownership_and_projection(monkeypatch):
    monkeypatch.setattr(api, "_run_owner", lambda rid: (True, "mcp:ikaoju"))
    monkeypatch.setattr(api.manager, "snapshot", lambda rid: {
        "status": "finished", "run_status": "success", "topic": "t",
        "started_at": "2026-10-14T00:00:00+00:00", "has_report": True,
        "stop_reason": "completed"})
    out = mcp_server.research_status("run-1")
    assert out["status"] == "finished" and out["run_status"] == "success"
    assert out["has_report"] is True and out["topic"] == "t"

    # 非本主体：与不存在同口径（不泄露存在性）
    monkeypatch.setattr(api, "_run_owner", lambda rid: (True, "someone-else"))
    with pytest.raises(ToolError, match="run_id_not_found"):
        mcp_server.research_status("run-1")
    monkeypatch.setattr(api, "_run_owner", lambda rid: (False, None))
    with pytest.raises(ToolError, match="run_id_not_found"):
        mcp_server.research_status("run-1")


# ---- research_report：输出 gate + 截断 ----


def test_research_report_gate_and_paths(monkeypatch):
    monkeypatch.setattr(api, "_run_owner", lambda rid: (True, "mcp:ikaoju"))

    def flagged(rid):
        raise ApiError("output_under_review", "报告命中内容安全预检，正在等待人工复核")

    monkeypatch.setattr(api, "_enforce_output_policy", flagged)
    with pytest.raises(ToolError, match="output_under_review"):
        mcp_server.research_report("run-1")

    monkeypatch.setattr(api, "_enforce_output_policy", lambda rid: None)
    monkeypatch.setattr(api.manager, "exists", lambda rid: True)
    monkeypatch.setattr(api.manager, "has_result", lambda rid: True)
    monkeypatch.setattr(api.manager, "export_markdown", lambda rid: "# 报告正文")
    out = mcp_server.research_report("run-1")
    assert out["content"] == "# 报告正文" and out["truncated"] is False

    monkeypatch.setattr(api.manager, "export_markdown",
                        lambda rid: "x" * (mcp_server.MAX_REPORT_CHARS + 10))
    out2 = mcp_server.research_report("run-1")
    assert out2["truncated"] is True and out2["content"].endswith("…[报告过长已截断]")

    monkeypatch.setattr(api.manager, "export_payload", lambda rid: {
        "result": {"report": "y" * (mcp_server.MAX_JSON_REPORT_CHARS + 10)}})
    with pytest.raises(ToolError, match="report_too_large"):
        mcp_server.research_report("run-1", format="json")

    with pytest.raises(ToolError, match="invalid_request"):
        mcp_server.research_report("run-1", format="pdf")

    monkeypatch.setattr(api.manager, "has_result", lambda rid: False)
    monkeypatch.setattr(api.manager, "is_finished", lambda rid: False)
    with pytest.raises(ToolError, match="report_not_ready"):
        mcp_server.research_report("run-1")


# ---- 审计：只记哈希，不落原始文本 ----


def test_audit_records_hash_not_raw_text(monkeypatch):
    seen = []
    monkeypatch.setattr(api, "_audit", lambda action, **kw: seen.append((action, kw)))
    monkeypatch.setattr(api, "start_research", lambda **kw: "run-xyz")
    monkeypatch.setattr(mcp_server, "create_search_provider",
                        lambda name: FakeProvider(_resp([])))
    mcp_server.quick_search("敏感查询文本")
    mcp_server.deep_research("敏感主题文本")

    assert seen and all(action == "mcp_tool_called" for action, _ in seen)
    joined = json.dumps(seen, ensure_ascii=False)
    assert "敏感查询文本" not in joined and "敏感主题文本" not in joined
    assert seen[0][1]["detail"]["request_hash"]
    assert seen[0][1]["actor_user_id"] == "mcp:ikaoju"
    assert seen[1][1]["detail"]["run_id"] == "run-xyz"
