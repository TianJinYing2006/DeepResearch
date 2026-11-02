"""P2-1a safe_fetch SSRF 防护单测（零外部网络）。"""
from __future__ import annotations

import socket

import pytest

from research_engine.net.safe_fetch import (
    UnsafeUrlError,
    is_public_ip,
    resolve_public_ips,
    safe_fetch,
    validate_url,
)


def _addr_infos(*ips):
    return [(socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (ip, 0))
            for ip in ips]


# ---- 2026-11 审计 #331：is_global / CGNAT / IPv4-mapped 判据 ----


def test_is_public_ip_rejects_non_global_and_cgnat():
    """旧实现缺 is_global：CGNAT 100.64/10（Tailscale/AWS CNI 常用段）被判公网。"""
    for ip in (
        "100.64.0.1",
        "100.127.255.254",
        "::ffff:100.64.0.1",   # IPv4-mapped 变体也必须跟着拒
        "10.0.0.1",
        "127.0.0.1",
        "::1",
        "169.254.169.254",
        "192.0.2.1",
        "224.0.0.1",           # 组播：is_global 为 True，须由显式 deny 兜住
        "0.0.0.0",
        "::ffff:127.0.0.1",
    ):
        assert is_public_ip(ip) is False, ip


def test_is_public_ip_allows_global_unicast():
    """公网单播（含 IPv4-mapped 公网地址）仍放行——旧实现会误杀 ::ffff:8.8.8.8。"""
    for ip in ("93.184.216.34", "8.8.8.8", "2606:4700::1111", "::ffff:8.8.8.8"):
        assert is_public_ip(ip) is True, ip


def test_validate_url_rejects_cgnat_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_infos("100.64.0.1"))
    with pytest.raises(UnsafeUrlError):
        validate_url("http://cgnat.example/")


def test_validate_url_rejects_scheme_and_credentials():
    with pytest.raises(UnsafeUrlError):
        validate_url("file:///etc/passwd")
    with pytest.raises(UnsafeUrlError):
        validate_url("http://user:pass@example.com/")


def test_validate_url_rejects_private_resolution(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_infos("127.0.0.1"))
    with pytest.raises(UnsafeUrlError):
        validate_url("http://attacker.example/")

    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_infos("169.254.169.254"))
    with pytest.raises(UnsafeUrlError):
        validate_url("http://metadata.example/")


def test_validate_url_rejects_mixed_public_private(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo",
                        lambda *a, **k: _addr_infos("93.184.216.34", "10.0.0.5"))
    with pytest.raises(UnsafeUrlError):  # 任一解析地址非公网即拒绝（防 DNS 指向内网）
        validate_url("http://dual.example/")


def test_validate_url_allowlist(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_infos("93.184.216.34"))
    assert validate_url("https://ok.example/x", allowed_hosts={"ok.example"})[1] == "ok.example"
    with pytest.raises(UnsafeUrlError):
        validate_url("https://other.example/x", allowed_hosts={"ok.example"})


def test_resolve_public_ips_returns_validated(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_infos("93.184.216.34"))
    assert resolve_public_ips("ok.example") == ["93.184.216.34"]


class _FakeResponse:
    def __init__(self, status: int = 200, body: bytes = b"ok",
                 headers: dict | None = None):
        self.status = status
        self._body = body
        self.headers = headers or {"Content-Type": "text/plain"}

    def read(self, amt=None):
        return self._body

    def release_conn(self):
        pass


class _FakePool:
    def __init__(self, response: _FakeResponse):
        self._response = response
        self.closed = False

    def request(self, *args, **kwargs):
        return self._response

    def close(self):
        self.closed = True


def _patch_pool(monkeypatch, response: _FakeResponse) -> None:
    monkeypatch.setattr(socket, "getaddrinfo", lambda *a, **k: _addr_infos("93.184.216.34"))
    import research_engine.net.safe_fetch as module

    monkeypatch.setattr(module.urllib3, "HTTPSConnectionPool",
                        lambda *a, **k: _FakePool(response))


def test_safe_fetch_rejects_redirects(monkeypatch):
    _patch_pool(monkeypatch, _FakeResponse(status=302))
    with pytest.raises(UnsafeUrlError):
        safe_fetch("https://ok.example/x")


def test_safe_fetch_enforces_size_cap(monkeypatch):
    _patch_pool(monkeypatch, _FakeResponse(body=b"x" * 20))
    with pytest.raises(UnsafeUrlError):
        safe_fetch("https://ok.example/x", max_bytes=10)


def test_safe_fetch_success_returns_body(monkeypatch):
    _patch_pool(monkeypatch, _FakeResponse(body="# 页面".encode("utf-8")))
    result = safe_fetch("https://ok.example/doc")
    assert result.status == 200 and result.text == "# 页面"
