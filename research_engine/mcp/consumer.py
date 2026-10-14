"""需求 37 M1：外部 MCP 消费适配层（TBD-1~4 / TBD-8~13，2026-10-14 拍板）。

本模块是 MCP 与主链路之间的**唯一适配点**，职责：

1. **配置解析 + URL 安全校验**（:func:`parse_servers`）：启用时构建 fail-fast；
   校验语义与 `research_engine.net.safe_fetch` 同源 —— 仅 https、禁 URL 凭据、
   DNS 全解析逐地址校验（私网段需 `allow_private` 显式放行且带理由，TBD-11）；
2. **调用抽象**（:data:`McpCaller`）：默认实现走官方 `mcp` SDK（v1 仅 Streamable HTTP），
   测试注入 fake caller（零网络）；在工具线程内同步执行（SDK 异步接口经 asyncio.run 桥接）；
3. **失败映射**（:func:`classify_mcp_exception`）：MCP 异常 → 既有工具层五值
   （`provider_error` / `timeout` / `parse_error` / `empty_result` / `not_configured`，
   **零新增枚举**，单产者契约不破；TBD-4）；
4. **结果归一化**（:meth:`McpConsumer.search`）：不可见字符剥离 + 字节/条数上限，
   产出 :class:`McpCallOutcome`（含 server/tool/call_id/retrieval_time 身份）。

纪律：默认关闭（:func:`build_mcp_consumer` 返回 None = 完全回现状，关闭态零漂移）；
响应正文不额外落盘（TBD-12：只把清洗后的文本交给 finding 层）；不做 LLM
function-calling / 工具自选（TBD-3：每个 server 显式配置查询型工具名，固定 query 注入）。
"""
from __future__ import annotations

import ipaddress
import json
import logging
import socket
import time
import uuid
from dataclasses import dataclass
from typing import Callable, List, Optional, Sequence, Tuple
from urllib.parse import urlsplit

from config import config
from research_engine.failure_reasons import FailureReason
from research_engine.sanitize import strip_invisible

_log = logging.getLogger("deepresearch.mcp")

#: TBD-2：单次调用超时默认 15s，v1 上限 15s（越界 fail-fast；放宽需独立评审）
DEFAULT_TIMEOUT_SECONDS = 15.0
MAX_TIMEOUT_SECONDS = 15.0
DEFAULT_MAX_RESULT_BYTES = 64 * 1024
DEFAULT_MAX_FINDINGS = 5
_TRUNC = "…[截断]"
#: TBD-3：v1 固定 query 注入参数名（工具 schema 版本化后按 server 扩展）
_QUERY_ARG = "query"


class McpConfigError(ValueError):
    """MCP 配置非法（启用时构建 fail-fast；不静默降级）。"""


@dataclass(frozen=True)
class McpServerConfig:
    """单个 MCP server 的校验后配置（TBD-2 schema）。"""

    name: str
    url: str
    tools: Tuple[str, ...]
    timeout: float = DEFAULT_TIMEOUT_SECONDS
    max_result_bytes: int = DEFAULT_MAX_RESULT_BYTES
    max_findings: int = DEFAULT_MAX_FINDINGS
    allow_private: bool = False
    private_reason: str = ""


@dataclass(frozen=True)
class McpToolCallResult:
    """一次工具调用的原始结果（caller 返回；失败时 reason 取工具层五值之一）。"""

    texts: Tuple[str, ...] = ()
    failure_reason: Optional[str] = None
    detail: str = ""


@dataclass(frozen=True)
class McpCallOutcome:
    """归一化后的调用结果（消费方转 ResearchFinding；纯数据，零模型）。"""

    server: str
    tool: str
    call_id: str
    retrieval_time: float
    items: Tuple[str, ...] = ()
    failure_reason: Optional[str] = None
    detail: str = ""


McpCaller = Callable[[McpServerConfig, str, str], McpToolCallResult]


# ---- URL 安全校验（与 safe_fetch 同源语义：resolve → validate；仅 https）----


def _resolve_addresses(host: str) -> List[str]:
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise McpConfigError(f"mcp server dns resolution failed: {host}") from exc
    return sorted({info[4][0] for info in infos})


def _is_public(ip: str) -> bool:
    """与 `safe_fetch._is_public_ip` 同源判定（SSRF 校验一致性）。"""
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (address.is_private or address.is_loopback or address.is_link_local
                or address.is_reserved or address.is_multicast or address.is_unspecified)


def validate_mcp_url(url: str, *, allow_private: bool = False, reason: str = "",
                     resolve: bool = True) -> Tuple[str, str, List[str]]:
    """校验 MCP 终点 URL，返回 `(scheme, host, 已校验 IP 列表)`；不通过抛 `McpConfigError`。

    - 仅 https（TBD-2：v1 不做明文 / stdio）；
    - 禁 URL 内嵌凭据（凭据走环境变量引用，不进 URL / 日志）；
    - `resolve=True` 时 DNS 全解析并逐地址校验；私网段（`is_private`）需
      `allow_private=True` **且必须带 reason**（TBD-11：显式配置 + 理由 + 审计）；
    - 环回 / link-local / 保留 / 组播 / 未指定地址**一律拒绝**（放行不覆盖本机与元数据端点）。
    """
    parts = urlsplit(url)
    if parts.scheme != "https":
        raise McpConfigError(f"mcp server scheme must be https: {url}")
    if parts.username or parts.password:
        raise McpConfigError("mcp server url credentials not allowed")
    host = parts.hostname
    if not host:
        raise McpConfigError(f"mcp server missing host: {url}")
    if allow_private and not reason.strip():
        raise McpConfigError(f"mcp server '{host}': allow_private requires private_reason")
    if not resolve:
        return parts.scheme, host, []
    addresses = _resolve_addresses(host)
    if not addresses:
        raise McpConfigError(f"mcp server no addresses for host: {host}")
    for address in addresses:
        if _is_public(address):
            continue
        parsed = ipaddress.ip_address(address) if _is_ip(address) else None
        if parsed is None:
            raise McpConfigError(f"mcp server bad address: {address}")
        if allow_private and parsed.is_private and not (
                parsed.is_loopback or parsed.is_link_local or parsed.is_reserved
                or parsed.is_multicast or parsed.is_unspecified):
            continue
        raise McpConfigError(f"mcp server host resolves to non-public address: {host}")
    return parts.scheme, host, addresses


def _is_ip(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


# ---- 配置解析（fail-fast；结构检查与 DNS 校验可分离，指纹/egress 用无 DNS 路径）----


def _parse_servers(raw_json: str, *, resolve: bool) -> List[McpServerConfig]:
    raw = (raw_json or "").strip()
    if not raw:
        return []
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise McpConfigError(f"DR_MCP_SERVERS is not valid JSON: {exc}") from exc
    if not isinstance(data, list):
        raise McpConfigError("DR_MCP_SERVERS must be a JSON array")
    servers: List[McpServerConfig] = []
    seen: set[str] = set()
    for i, item in enumerate(data):
        if not isinstance(item, dict):
            raise McpConfigError(f"DR_MCP_SERVERS[{i}] must be an object")
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url:
            raise McpConfigError(f"DR_MCP_SERVERS[{i}] requires name and url")
        if name in seen:
            raise McpConfigError(f"duplicate mcp server name: {name}")
        seen.add(name)
        tools_raw = item.get("tools")
        if not isinstance(tools_raw, list):
            raise McpConfigError(f"mcp server '{name}': tools must be a non-empty array")
        tools = tuple(str(t).strip() for t in tools_raw if str(t).strip())
        if not tools:
            raise McpConfigError(f"mcp server '{name}': tools must be a non-empty array")
        try:
            timeout = float(item.get("timeout", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            raise McpConfigError(f"mcp server '{name}': invalid timeout") from None
        if not (0 < timeout <= MAX_TIMEOUT_SECONDS):
            raise McpConfigError(
                f"mcp server '{name}': timeout must be in (0, {MAX_TIMEOUT_SECONDS}]")
        try:
            max_bytes = int(item.get("max_result_bytes", DEFAULT_MAX_RESULT_BYTES))
            max_findings = int(item.get("max_findings", DEFAULT_MAX_FINDINGS))
        except (TypeError, ValueError):
            raise McpConfigError(f"mcp server '{name}': invalid result caps") from None
        if max_bytes <= 0 or max_findings <= 0:
            raise McpConfigError(f"mcp server '{name}': result caps must be positive")
        allow_private = bool(item.get("allow_private", False))
        reason = str(item.get("private_reason") or "").strip()
        validate_mcp_url(url, allow_private=allow_private, reason=reason, resolve=resolve)
        servers.append(McpServerConfig(
            name=name, url=url, tools=tools, timeout=timeout,
            max_result_bytes=max_bytes, max_findings=max_findings,
            allow_private=allow_private, private_reason=reason))
    return servers


def parse_servers(raw_json: str) -> List[McpServerConfig]:
    """解析 `DR_MCP_SERVERS` 并做**含 DNS**的完整校验；非法即抛（启用时 fail-fast）。"""
    return _parse_servers(raw_json, resolve=True)


# ---- 失败映射（五值零新增；TBD-4）----


def classify_mcp_exception(exc: BaseException) -> str:
    """MCP 调用异常 → 既有工具层五值（零新增枚举）。

    映射口径（TBD-4）：超时 ⇒ `timeout`；解析/校验类 ⇒ `parse_error`；
    连接/鉴权/其余 ⇒ `provider_error`（兜底不猜新值）。
    """
    if isinstance(exc, TimeoutError):  # asyncio.TimeoutError 在 3.11+ 即内置 TimeoutError
        return FailureReason.TIMEOUT.value
    name = type(exc).__name__.lower()
    msg = f"{name}: {exc}".lower()
    if "timeout" in msg or "timed out" in msg:
        return FailureReason.TIMEOUT.value
    if any(k in name or k in msg for k in ("json", "parse", "validation", "decode")):
        return FailureReason.PARSE_ERROR.value
    return FailureReason.PROVIDER_ERROR.value


# ---- 默认 caller：官方 mcp SDK（Streamable HTTP）----


def _sdk_call_tool(server: McpServerConfig, tool: str, query: str) -> McpToolCallResult:
    """官方 mcp SDK 调用；SDK 不可用按 `not_configured` 归类（fail-safe，不 crash）。

    在工具线程内同步执行：SDK 为异步接口，`asyncio.run` 桥接（每次调用独立事件循环，
    调用间无共享状态）；除 SDK 超时参数外再套 `wait_for` 硬期限（防对端半开挂死线程）。
    """
    try:
        import asyncio

        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client
    except Exception as exc:  # noqa: BLE001 —— 依赖不可用 ⇒ not_configured（五值语义）
        return McpToolCallResult(
            failure_reason=FailureReason.NOT_CONFIGURED.value,
            detail=f"mcp sdk unavailable: {type(exc).__name__}")

    async def _call() -> McpToolCallResult:
        async with streamablehttp_client(
                server.url, timeout=server.timeout,
                sse_read_timeout=server.timeout) as (read, write, _):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(tool, {_QUERY_ARG: query})
                if getattr(result, "isError", False):
                    return McpToolCallResult(
                        failure_reason=FailureReason.PROVIDER_ERROR.value,
                        detail="tool returned error result")
                texts = tuple(
                    str(item.text) for item in (result.content or [])
                    if getattr(item, "type", "") == "text" and getattr(item, "text", None))
                if not texts:
                    return McpToolCallResult(failure_reason=FailureReason.EMPTY_RESULT.value)
                return McpToolCallResult(texts=texts)

    async def _call_with_deadline() -> McpToolCallResult:
        return await asyncio.wait_for(_call(), timeout=server.timeout + 5.0)

    try:
        return asyncio.run(_call_with_deadline())
    except Exception as exc:  # noqa: BLE001 —— 全部异常映射五值，不外抛（调用隔离）
        return McpToolCallResult(failure_reason=classify_mcp_exception(exc),
                                 detail=f"{type(exc).__name__}: {exc}"[:200])


# ---- 消费器 ----


class McpConsumer:
    """消费端适配器：对配置中的每个 server × 查询型工具各发一次调用。

    调用隔离：单 server/工具异常映射为失败 outcome（`provider_error` 兜底），
    不阻断其余调用（与 `search_once` 的单工具隔离语义一致）。
    """

    def __init__(self, servers: Sequence[McpServerConfig],
                 caller: Optional[McpCaller] = None):
        self.servers: Tuple[McpServerConfig, ...] = tuple(servers)
        self._caller: McpCaller = caller or _sdk_call_tool

    def search(self, query: str) -> List[McpCallOutcome]:
        """执行一轮 MCP 检索；成功/失败都产出 outcome（失败由消费方记降级 + 记账）。"""
        outcomes: List[McpCallOutcome] = []
        for server in self.servers:
            for tool in server.tools:
                call_id = uuid.uuid4().hex[:16]
                try:
                    raw = self._caller(server, tool, query)
                except Exception as exc:  # noqa: BLE001 —— 调用隔离（单 server 不绊倒其余）
                    raw = McpToolCallResult(
                        failure_reason=classify_mcp_exception(exc),
                        detail=f"{type(exc).__name__}: {exc}"[:200])
                outcomes.append(_normalize(server, tool, call_id, raw))
        return outcomes


def _normalize(server: McpServerConfig, tool: str, call_id: str,
               raw: McpToolCallResult) -> McpCallOutcome:
    """归一化：剥离不可见字符 + 字节/条数上限；空结果归 `empty_result`（TBD-4）。"""
    reason = raw.failure_reason
    items: List[str] = []
    if reason is None:
        budget = server.max_result_bytes
        for text in raw.texts[: server.max_findings]:
            cleaned = strip_invisible(str(text))
            encoded = cleaned.encode("utf-8")
            if len(encoded) > budget:
                cleaned = encoded[:budget].decode("utf-8", errors="ignore") + _TRUNC
                encoded = cleaned.encode("utf-8")
            if cleaned:
                items.append(cleaned)
                budget = max(0, budget - len(encoded))
            if budget <= 0:
                break
        if not items:
            reason = FailureReason.EMPTY_RESULT.value
    return McpCallOutcome(
        server=server.name, tool=tool, call_id=call_id,
        retrieval_time=round(time.time(), 3), items=tuple(items),
        failure_reason=reason, detail=raw.detail)


# ---- 构建 / 指纹（关闭态零漂移；指纹与 egress 走无 DNS 路径）----


def build_mcp_consumer() -> Optional[McpConsumer]:
    """启用且配置了 server 时构建消费器；否则 None（targets 不含 mcp = 零漂移）。

    开关开启但 `DR_MCP_SERVERS` 为空 ⇒ None（视为未配置）；非空但非法 ⇒
    `McpConfigError` 上抛（TBD-2：配置错误启动时 fail-fast，不静默降级）。
    """
    if not config.mcp.consume_enabled:
        return None
    servers = parse_servers(config.mcp.servers_json)
    if not servers:
        return None
    return McpConsumer(servers)


def _lenient_servers() -> List[McpServerConfig]:
    """结构解析（无 DNS），非法返回空列表 —— 仅用于指纹 / egress 登记等旁路。"""
    try:
        return _parse_servers(config.mcp.servers_json, resolve=False)
    except McpConfigError:
        return []


def server_fingerprint() -> List[str]:
    """server 列表指纹（`name@host` 排序列表；不含密钥/URL 凭据；TBD-7）。"""
    return sorted(f"{s.name}@{urlsplit(s.url).hostname or ''}" for s in _lenient_servers())


def server_hosts() -> List[str]:
    """配置中 server 的 host 列表（egress 终点登记用；TBD-2）。"""
    hosts = {urlsplit(s.url).hostname or "" for s in _lenient_servers()}
    return sorted(h for h in hosts if h)
