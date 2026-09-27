"""SSRF 安全的出站抓取（P2-1a）。

当前主链路没有「抓取用户 URL」的功能（搜索走 provider API、arXiv 固定域名），
本模块作为**未来 URL 抓取能力的唯一入口**预置，并固定安全契约：

1. scheme 仅 http/https；可选 host allowlist；
2. DNS 解析后校验**每一个**地址（拒绝私网 / 环回 / link-local / 保留 / 组播 / 未指定，
   覆盖 169.254.169.254 等云元数据端点）；解析失败即拒绝；
3. **不跟随重定向**（3xx 一律拒绝，避免「合法 URL 跳内网」）；
4. 连接 **pin 到已校验 IP**（TLS 仍用原 host 做 SNI 与证书校验），关闭 DNS rebinding 窗口；
5. 连接/读取超时 + 响应体大小上限。

行业依据：OWASP SSRF Cheat Sheet（resolve → validate → pin、禁重定向）。
"""
from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

import urllib3

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_ALLOWED_SCHEMES = ("http", "https")


class UnsafeUrlError(ValueError):
    """URL 未通过 SSRF 校验（scheme / allowlist / 私网 / 重定向 / 超限等）。"""


@dataclass(frozen=True)
class FetchResult:
    url: str
    status: int
    content_type: str
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


def _is_public_ip(ip: str) -> bool:
    try:
        address = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return not (
        address.is_private or address.is_loopback or address.is_link_local
        or address.is_reserved or address.is_multicast or address.is_unspecified
    )


def resolve_public_ips(host: str) -> list[str]:
    """解析 host 的全部地址并校验；任一地址非公网即拒绝（防域名指向内网）。"""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise UnsafeUrlError(f"dns resolution failed: {host}") from exc
    addresses = sorted({info[4][0] for info in infos})
    if not addresses:
        raise UnsafeUrlError(f"no addresses for host: {host}")
    for address in addresses:
        if not _is_public_ip(address):
            raise UnsafeUrlError(f"host resolves to non-public address: {host}")
    return addresses


def validate_url(url: str, *,
                 allowed_hosts: Optional[set[str]] = None) -> tuple[str, str, list[str]]:
    """校验 URL 并返回 `(scheme, host, 已校验 IP 列表)`；不通过抛 `UnsafeUrlError`。"""
    parts = urlsplit(url)
    if parts.scheme not in _ALLOWED_SCHEMES:
        raise UnsafeUrlError(f"scheme not allowed: {parts.scheme or '(empty)'}")
    if parts.username or parts.password:
        raise UnsafeUrlError("url credentials not allowed")
    host = parts.hostname
    if not host:
        raise UnsafeUrlError("missing host")
    if allowed_hosts is not None and host not in allowed_hosts:
        raise UnsafeUrlError(f"host not in allowlist: {host}")
    return parts.scheme, host, resolve_public_ips(host)


def safe_fetch(url: str, *, allowed_hosts: Optional[set[str]] = None,
               timeout: float = DEFAULT_TIMEOUT_SECONDS,
               max_bytes: int = DEFAULT_MAX_BYTES) -> FetchResult:
    """SSRF 安全的 GET：校验 → pin IP → 禁重定向 → 限时限量。"""
    scheme, host, addresses = validate_url(url, allowed_hosts=allowed_hosts)
    parts = urlsplit(url)
    path = parts.path or "/"
    if parts.query:
        path = f"{path}?{parts.query}"
    port = parts.port or (443 if scheme == "https" else 80)

    if scheme == "https":
        pool = urllib3.HTTPSConnectionPool(
            addresses[0], port=port, server_hostname=host,  # pin IP，SNI/证书仍按原 host
            cert_reqs="CERT_REQUIRED",
            timeout=urllib3.Timeout(connect=timeout, read=timeout), retries=False,
        )
    else:
        pool = urllib3.HTTPConnectionPool(
            addresses[0], port=port,
            timeout=urllib3.Timeout(connect=timeout, read=timeout), retries=False,
        )
    status = 0
    content_type = ""
    try:
        response = pool.request("GET", path, headers={"Host": host},
                                redirect=False, preload_content=False)
        try:
            status = int(response.status)
            content_type = response.headers.get("Content-Type", "")
            if 300 <= status < 400:
                raise UnsafeUrlError("redirects are not followed")
            body = response.read(max_bytes + 1)
        finally:
            response.release_conn()
    finally:
        pool.close()
    if len(body) > max_bytes:
        raise UnsafeUrlError(f"response exceeds {max_bytes} bytes")
    return FetchResult(url=url, status=status, content_type=content_type, body=body)
