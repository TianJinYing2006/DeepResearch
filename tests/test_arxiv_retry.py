"""arXiv 间歇性连接失败重试单测（2026-09-22）。

背景：代理对 export.arxiv.org 的 CONNECT 隧道实测约 **40% 失败**（连测 5 次
3 成 2 败），表现为 ProxyError / Max retries exceeded。端点本身是好的，属间歇性
网络故障 ⇒ 重试即可自愈；但**真持续故障仍必须如实降级**，不能靠重试把故障掩盖掉。

锁定三条边界：
1. 间歇性连接失败 ⇒ 重试后成功，**不得记降级**；
2. 重试耗尽仍失败 ⇒ 照常 provider_error，且 detail 写明重试次数；
3. HTTP 错误（服务端明确答复）⇒ **不重试**，一次即归类。
"""
from __future__ import annotations

import pytest
import requests

from research_engine.failure_reasons import FailureReason
from research_engine.search.arxiv import ArxivSearchProvider, RateLimiter

_MIN_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom">
  <entry>
    <id>http://arxiv.org/abs/2401.00001v1</id>
    <title>Test Paper Title</title>
    <summary>Test abstract content.</summary>
    <published>2024-01-01T00:00:00Z</published>
    <author><name>Alice</name></author>
  </entry>
</feed>"""


def _provider() -> ArxivSearchProvider:
    p = ArxivSearchProvider.__new__(ArxivSearchProvider)
    p.max_results = 3
    p._limiter = RateLimiter(min_interval=0)  # 关掉 3s 礼貌间隔，测试才跑得快
    return p


def _ok_response() -> requests.Response:
    resp = requests.Response()
    resp.status_code = 200
    resp._content = _MIN_FEED
    return resp


# ---- 1) 间歇性失败：重试后成功，不算降级 ----

def test_retries_then_succeeds_without_degradation(monkeypatch):
    """前两次 ProxyError、第三次成功 ⇒ 重试自愈，不得写 failure_reason。"""
    import research_engine.search.arxiv as A

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        if calls["n"] < 3:
            raise requests.exceptions.ProxyError("Tunnel connection failed: 502")
        return _ok_response()

    monkeypatch.setattr(A.requests, "get", fake_get)
    monkeypatch.setattr(A.time, "sleep", lambda _s: None)  # 跳过退避等待

    resp = _provider().search("q")
    assert calls["n"] == 3, "应当重试到第 3 次"
    assert resp.failure_reason is None, "重试成功后不得记为降级"
    assert len(resp.results) == 1


# ---- 2) 持续故障：重试耗尽仍要如实降级 ----

def test_exhausted_retries_still_reports_provider_error(monkeypatch):
    """一直连不上 ⇒ 重试耗尽后照常降级（重试不得成为掩盖故障的手段）。"""
    import research_engine.search.arxiv as A

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        raise requests.exceptions.ProxyError("Tunnel connection failed: 502")

    monkeypatch.setattr(A.requests, "get", fake_get)
    monkeypatch.setattr(A.time, "sleep", lambda _s: None)

    resp = _provider().search("q")
    assert calls["n"] == A.MAX_ATTEMPTS
    assert resp.failure_reason == FailureReason.PROVIDER_ERROR.value
    assert "重试" in resp.failure_detail, "detail 需写明重试次数，便于排障"


def test_timeout_reports_timeout_reason(monkeypatch):
    import research_engine.search.arxiv as A
    monkeypatch.setattr(A.requests, "get",
                        lambda *a, **k: (_ for _ in ()).throw(requests.exceptions.Timeout("t/o")))
    monkeypatch.setattr(A.time, "sleep", lambda _s: None)
    resp = _provider().search("q")
    assert resp.failure_reason == FailureReason.TIMEOUT.value


# ---- 3) HTTP 错误是服务端答复，不重试 ----

@pytest.mark.parametrize("status", [400, 403, 500])
def test_http_error_is_not_retried(monkeypatch, status):
    """4xx/5xx 是服务端明确答复，重试无意义 ⇒ 只请求一次。"""
    import research_engine.search.arxiv as A

    calls = {"n": 0}

    def fake_get(url, params=None, timeout=None):
        calls["n"] += 1
        resp = requests.Response()
        resp.status_code = status
        resp.url = url

        def _raise_for_status():
            raise requests.exceptions.HTTPError(f"HTTP {status}", response=resp)
        resp.raise_for_status = _raise_for_status
        return resp

    monkeypatch.setattr(A.requests, "get", fake_get)
    monkeypatch.setattr(A.time, "sleep", lambda _s: None)

    resp = _provider().search("q")
    assert calls["n"] == 1, "HTTP 错误不应重试"
    assert resp.failure_reason == FailureReason.PROVIDER_ERROR.value


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
