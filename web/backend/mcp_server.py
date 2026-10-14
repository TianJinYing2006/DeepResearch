"""需求 37 M2：对外 MCP Server（TBD-5 / TBD-6 / TBD-10，2026-10-14 拍板）。

边界（硬约束）：
- **默认关闭**（`DR_MCP_SERVER_ENABLED=false`；main.py 仅在开启时挂载 `/mcp`）；
- **机器令牌鉴权**（`DR_MCP_TOKEN`，常量时间比较；fail-closed：未配置 ⇒ 一律 401；
  仅读 `Authorization: Bearer`，拒绝 query-string token —— 只认头）；
- **准入单一口径**：`deep_research` 复用 `main.start_research(...)` 服务函数
  （配额/审核/注入/幂等一处不落，MCP 不自建第二条创建链路，TBD-6）；
- **service principal**：单一主体 `mcp:<server_name>`（TBD-10），所有 run 归属该主体、
  仅该主体可查（status/report 每次做主体所有权校验；不存在与无权同口径 404）；
- **报告输出 gate 同口径**：`research_report` 复用 `_enforce_output_policy`
  （flagged / under-review 阻断）；响应有长度上限（TBD-12：正文不额外落盘）；
- **审计**（best-effort）：只记主体 / 工具 / run_id / 请求哈希 / 决策，不落原始 prompt/query；
- 工具 schema 固定且版本化（`TOOL_SCHEMA_VERSION`；入参上限见常量区）。

传输：Streamable HTTP（v1 唯一传输；stdio 不做）。TLS/请求体上限由反代与部署层保障
（见 release-runbook §MCP）；`json_response=True + stateless_http=True`（无会话状态）。

注意：本模块**不用** `from __future__ import annotations` —— FastMCP 1.9.4 在注册工具时
直接 `issubclass(param.annotation, Context)`，字符串化注解会 TypeError（实测）；
工具签名注解必须是具体对象（这也是工具 JSON schema 的来源）。
"""
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, Optional, Tuple

from config import config
from research_engine.failure_reasons import FailureReason, is_fault_reason
from research_engine.search.base import create_search_provider
from research_engine.usage import UsageRecord, UsageSinkError, emit_usage, pop_usage_sink, push_usage_sink

_log = logging.getLogger("deepresearch.mcp.server")

#: 机器令牌（只存于环境/密钥管理；不写日志、不进 egress/快照）
MCP_TOKEN = (os.getenv("DR_MCP_TOKEN") or "").strip()

#: 工具面 schema 版本（固定且版本化；变更需同步 release-runbook）
TOOL_SCHEMA_VERSION = "mcp-tools/v1"

#: 入参/出参上限（固定 schema 的一部分）
MAX_QUERY_CHARS = 500
MAX_TOPIC_CHARS = 500
MAX_INSTRUCTIONS_CHARS = 2000
MAX_RESULTS = 10
MAX_SNIPPET_CHARS = 500
MAX_REPORT_CHARS = 200_000          # md：超限截断 + 标记
MAX_JSON_REPORT_CHARS = 500_000     # json：超限结构化报错（截断 JSON 无意义）

_TOOLS = ("quick_search", "deep_research", "research_status", "research_report")


def principal() -> str:
    """单一 service principal（TBD-10）：`mcp:<server_name>`。"""
    return f"mcp:{config.mcp.server_name}"


def _request_digest(text: str) -> str:
    """请求哈希（审计用；不落原文）。"""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _audit_tool(tool: str, *, run_id: Optional[str] = None, request_hash: str = "",
                decision: str = "ok") -> None:
    """审计（best-effort；不落原始 prompt/query —— 只记主体/工具/run_id/请求哈希/决策）。"""
    try:
        from web.backend import main as backend  # 延迟导入防循环

        backend._audit(
            "mcp_tool_called", actor_user_id=principal(),
            target_type="run" if run_id else None, target_id=run_id,
            detail={"tool": tool, "run_id": run_id, "request_hash": request_hash,
                    "decision": decision})
    except Exception:  # noqa: BLE001 —— 审计是旁路，不得影响工具调用
        pass


def _tool_error(code: str, message: str) -> Exception:
    from mcp.server.fastmcp.exceptions import ToolError

    return ToolError(f"{code}: {message}")


def _env_int(name: str, default: int) -> int:
    try:
        return int((os.getenv(name) or "").strip() or default)
    except ValueError:
        return default


#: 调用限流（每分钟/主体；0=关闭）。多实例部署时经 `DR_REDIS_URL` 走共享滑动窗口，
#: 否则回落进程内固定窗口（与 Web 限流同一实现，fail-open 语义一致）。
MCP_RATE_PER_MINUTE = _env_int("DR_MCP_RATE_PER_MINUTE", 60)
_mcp_limiter: Any = None


def _limiter() -> Any:
    global _mcp_limiter
    if _mcp_limiter is None:
        from web.backend.ratelimit import make_limiter

        redis_url = (os.getenv("DR_REDIS_URL") or "").strip()
        _mcp_limiter = make_limiter("mcp", MCP_RATE_PER_MINUTE, redis_url=redis_url)
    return _mcp_limiter


def _enforce_rate_limit(tool: str) -> None:
    if not _limiter().allow(f"mcp:{principal()}"):
        _audit_tool(tool, decision="rate_limited")
        raise _tool_error("rate_limited", "MCP 调用过于频繁，稍后再试")


def _api_error_to_tool(exc: Exception) -> Exception:
    code = getattr(exc, "code", "internal")
    message = getattr(exc, "message", str(exc))
    return _tool_error(code, message)


# ---------------------------------------------------------------- 四工具（v1）


def quick_search(query: str, max_results: int = 5) -> Dict[str, Any]:
    """快速搜索（同步；provider 直调；不创建 run）。

    返回 `{provider, query_hash, results[], failure_reason}`；provider 结构化失败
    以 `failure_reason` 如实返回（不抛工具错误）；记账 `kind='search'`（per_call 计次）。
    """
    from web.backend import main as backend

    _enforce_rate_limit("quick_search")
    q = (query or "").strip()
    if not q:
        raise _tool_error("empty_query", "query 不能为空")
    if len(q) > MAX_QUERY_CHARS:
        raise _tool_error("query_too_long", f"query 超过 {MAX_QUERY_CHARS} 字符")
    try:
        n = max(1, min(int(max_results or 5), MAX_RESULTS))
    except (TypeError, ValueError):
        raise _tool_error("invalid_request", "max_results 必须是整数") from None
    digest = _request_digest(q)

    provider_name = config.search.provider
    provider = create_search_provider(provider_name)
    sink_token = None
    if backend.store is not None:
        from web.backend.usage import make_store_sink

        sink_token = push_usage_sink(make_store_sink(
            backend.store, run_id=None, attempt=1, detail={"role": "mcp_quick_search"}))
    try:
        try:
            resp = provider.search(q, max_results=n)
        except UsageSinkError as exc:  # F06：严格记账失败必须上抛（不得静默吞账）
            raise _tool_error("accounting_failed", str(exc)[:200]) from exc
        except Exception as exc:  # noqa: BLE001 —— 未预期异常按 provider_error 如实返回
            # 失败同样计次（与主链路搜索口径一致：调用成本已发生）
            emit_usage(UsageRecord(kind="search", provider=provider_name,
                                   role="mcp_quick_search"))
            _audit_tool("quick_search", request_hash=digest, decision="provider_error")
            return {"provider": provider_name, "query_hash": digest, "results": [],
                    "failure_reason": FailureReason.PROVIDER_ERROR.value,
                    "failure_detail": f"{type(exc).__name__}: {exc}"[:200]}
        emit_usage(UsageRecord(kind="search", provider=provider_name,
                               role="mcp_quick_search"))
    finally:
        if sink_token is not None:
            pop_usage_sink(sink_token)

    results = [{"title": (r.title or "")[:MAX_SNIPPET_CHARS],
                "url": r.url,
                "snippet": (r.snippet or "")[:MAX_SNIPPET_CHARS]}
               for r in (resp.results or [])]
    failure_reason = getattr(resp, "failure_reason", None)
    decision = failure_reason if is_fault_reason(failure_reason) else "ok"
    _audit_tool("quick_search", request_hash=digest, decision=str(decision))
    return {"provider": provider_name, "query_hash": digest, "results": results,
            "failure_reason": failure_reason,
            "failure_detail": getattr(resp, "failure_detail", "") or ""}


def deep_research(topic: str, instructions: str = "", profile: str = "standard",
                  idempotency_key: str = "") -> Dict[str, Any]:
    """发起深度研究（异步）：创建 run 并立即返回 `{run_id, status_url}`。

    走 `main.start_research(...)` 服务函数（与 Web 同一准入口径：配额/审核/注入/幂等）；
    幂等键缺省时按「主体 + 请求内容」派生（同请求重复调用返回同一 run）。
    轮询用 `research_status`，取报告用 `research_report`。
    """
    from web.backend import main as backend

    _enforce_rate_limit("deep_research")
    t = (topic or "").strip()
    if not t:
        raise _tool_error("empty_topic", "topic 不能为空")
    if len(t) > MAX_TOPIC_CHARS:
        raise _tool_error("topic_too_long", f"topic 超过 {MAX_TOPIC_CHARS} 字符")
    instr = instructions or ""
    if len(instr) > MAX_INSTRUCTIONS_CHARS:
        raise _tool_error("instructions_too_long",
                          f"instructions 超过 {MAX_INSTRUCTIONS_CHARS} 字符")
    digest = _request_digest(f"{t}\n{instr}\n{profile}")
    key = (idempotency_key or "").strip() or f"mcp-{principal()}-{digest}"
    try:
        run_id = backend.start_research(
            topic=t, instructions=instr, profile_name=profile,
            user_id=principal(), idempotency_key=key, ignored=None, request=None)
    except backend.ApiError as exc:
        _audit_tool("deep_research", request_hash=digest, decision=exc.code)
        raise _api_error_to_tool(exc) from exc
    _audit_tool("deep_research", run_id=run_id, request_hash=digest)
    return {"run_id": run_id, "status_url": f"/api/research/{run_id}",
            "schema_version": TOOL_SCHEMA_VERSION}


def _owned_run(run_id: str) -> str:
    """主体所有权校验：不存在与无权同口径（不泄露存在性）；返回规范化 run_id。"""
    from web.backend import main as backend

    rid = (run_id or "").strip()
    if not rid:
        raise _tool_error("invalid_request", "run_id 不能为空")
    exists, owner = backend._run_owner(rid)
    if not exists or owner != principal():
        _audit_tool("research_status", run_id=rid, decision="not_found_or_denied")
        raise _tool_error("run_id_not_found", rid)
    return rid


def research_status(run_id: str) -> Dict[str, Any]:
    """查询研究状态（只读；仅本主体创建的 run 可见）。

    返回快照投影（限量字段）：status / run_status / topic / has_report / stop_reason。
    """
    from web.backend import main as backend

    _enforce_rate_limit("research_status")
    rid = _owned_run(run_id)
    snap = backend.manager.snapshot(rid)
    if snap is None and backend.store is not None:
        snap = backend._snapshot_from_store(rid)
    if snap is None:
        raise _tool_error("run_id_not_found", rid)
    _audit_tool("research_status", run_id=rid)
    return {
        "run_id": rid,
        "status": snap.get("status"),
        "run_status": snap.get("run_status"),
        "topic": snap.get("topic"),
        "started_at": snap.get("started_at"),
        "has_report": bool(snap.get("has_report")),
        "stop_reason": snap.get("stop_reason"),
    }


def research_report(run_id: str, format: str = "md") -> Dict[str, Any]:
    """取回研究报告（只读；输出 gate 与 Web 导出同口径：flagged/under-review 阻断）。

    `format="md"` 返回 Markdown 文本（超 `MAX_REPORT_CHARS` 截断 + 标记）；
    `format="json"` 返回结构化载荷（超 `MAX_JSON_REPORT_CHARS` 报 `report_too_large`）。
    """
    from web.backend import main as backend

    _enforce_rate_limit("research_report")
    rid = _owned_run(run_id)
    fmt = (format or "md").strip().lower()
    if fmt not in ("md", "json"):
        raise _tool_error("invalid_request", "format 仅支持 md / json")
    try:
        backend._enforce_output_policy(rid)  # flagged / under-review 阻断（与导出同口径）
    except backend.ApiError as exc:
        _audit_tool("research_report", run_id=rid, decision=exc.code)
        raise _api_error_to_tool(exc) from exc

    if backend.manager.exists(rid):
        if not backend.manager.has_result(rid):
            if not backend.manager.is_finished(rid):
                raise _tool_error("report_not_ready", "研究尚未结束，暂无报告")
            raise _tool_error("report_unavailable", "本次运行没有产出报告")
        if fmt == "json":
            payload = backend.manager.export_payload(rid)
            body = json.dumps(payload, ensure_ascii=False) if payload is not None else ""
        else:
            body = backend.manager.export_markdown(rid) or ""
    else:
        if backend.store is None:
            raise _tool_error("run_id_not_found", rid)
        row = backend._store_call(backend.store.get_run, rid)
        if row is None:
            raise _tool_error("run_id_not_found", rid)
        if not row.get("finished_at"):
            raise _tool_error("report_not_ready", "研究尚未结束，暂无报告")
        body = backend._artifact_text(rid, "export_json" if fmt == "json" else "report_md") or ""

    if not body.strip():
        raise _tool_error("report_unavailable", "本次运行没有产出报告")
    truncated = False
    if fmt == "json":
        if len(body) > MAX_JSON_REPORT_CHARS:
            raise _tool_error("report_too_large",
                              f"JSON 载荷超过 {MAX_JSON_REPORT_CHARS} 字符，请用 format=md")
    elif len(body) > MAX_REPORT_CHARS:
        body = body[:MAX_REPORT_CHARS] + "\n\n…[报告过长已截断]"
        truncated = True
    _audit_tool("research_report", run_id=rid)
    return {"run_id": rid, "format": fmt, "content": body, "truncated": truncated}


# ---------------------------------------------------------------- 鉴权 + ASGI 构建


class _BearerAuth:
    """纯 ASGI Bearer 鉴权（只认 `Authorization` 头；query-string token 一律忽略=拒绝）。

    fail-closed：令牌未配置（空）⇒ 任何请求 401（不静默放行）。
    """

    def __init__(self, app: Any, token: str) -> None:
        self.app = app
        self._token = token or ""

    async def __call__(self, scope: Dict[str, Any], receive: Any, send: Any) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        headers = {key.decode("latin-1").lower(): value.decode("latin-1")
                   for key, value in scope.get("headers", [])}
        auth = headers.get("authorization", "")
        presented = auth[7:].strip() if auth[:7].lower() == "bearer " else ""
        if not self._token or not presented or not hmac.compare_digest(presented, self._token):
            body = json.dumps({"error": "unauthenticated",
                               "detail": "需要 Authorization: Bearer <token>"}).encode()
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json; charset=utf-8"),
                                    (b"www-authenticate", b"Bearer")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_mcp_asgi() -> Tuple[Any, Any]:
    """构建 Streamable HTTP 子应用（含 Bearer 鉴权）；返回 `(asgi_app, fastmcp)`。

    `fastmcp` 供 main.py 把会话管理器接入 lifespan（`session_manager.run()`）。
    """
    from mcp.server.fastmcp import FastMCP

    if not MCP_TOKEN:
        _log.error("DR_MCP_SERVER_ENABLED=true 但 DR_MCP_TOKEN 未配置 —— "
                   "MCP 入口 fail-closed（所有请求 401）")

    mcp = FastMCP(
        config.mcp.server_name,
        instructions=("DeepResearch 研究能力（只读 + 创建研究）。"
                      "工具面版本 " + TOOL_SCHEMA_VERSION + "。"),
        streamable_http_path="/",
        json_response=True,
        stateless_http=True,
    )
    mcp.add_tool(quick_search, name="quick_search",
                 description="快速搜索：同步返回检索结果（不创建研究任务）")
    mcp.add_tool(deep_research, name="deep_research",
                 description="发起深度研究（异步）：返回 run_id，用 research_status 轮询")
    mcp.add_tool(research_status, name="research_status",
                 description="查询研究状态（仅本主体创建的任务）")
    mcp.add_tool(research_report, name="research_report",
                 description="取回研究报告 md/json（输出 gate 与 Web 导出一致）")
    return _BearerAuth(mcp.streamable_http_app(), MCP_TOKEN), mcp
