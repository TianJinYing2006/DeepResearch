"""Tavily Provider 单测：锁定 W8 Arm 4「工具层 5 值」契约。

与 ``tests/test_arm4_failure_reasons.py`` 中 bocha 的用例同构 —— 换搜索源不该
换掉失败语义，否则「故障可归因」会随 provider 漂移。

覆盖：未配置 / 超时 / 401·403 / 5xx·429 / 解析失败 / 零结果 / 正常解析，
以及工厂函数装配（``SEARCH_PROVIDER`` 开关真正生效）。
"""
from __future__ import annotations

import pytest
import requests

from research_engine.failure_reasons import TOOL_REASONS, FailureReason


def _fake_post(*, status=200, payload=None, raise_exc=None):
    """给 tavily 用的 requests.post 替身（tavily 用 json= 传参、不传 headers）。"""

    def _post(url, json=None, headers=None, timeout=None):
        if raise_exc is not None:
            raise raise_exc
        resp = requests.Response()
        resp.status_code = status
        if status >= 400:
            resp._content = b"err"
            resp.url = url

            def _raise_for_status():
                raise requests.exceptions.HTTPError(f"HTTP {status}", response=resp)
        else:
            def _raise_for_status():
                return None
        resp.raise_for_status = _raise_for_status
        if payload is None:
            def _bad_json():
                raise ValueError("Expecting value: line 1 column 1")
            resp.json = _bad_json
        else:
            resp.json = lambda: payload
        return resp

    return _post


def _provider(api_key="k"):
    from research_engine.search.tavily import TavilySearchProvider
    p = TavilySearchProvider.__new__(TavilySearchProvider)  # 绕过 __init__ 读 config
    p.api_key = api_key
    return p


# ---- ① 未配置：不再 raise，结构化返回 ----

def test_not_configured_does_not_raise():
    resp = _provider(api_key="").search("q")
    assert resp.failure_reason == FailureReason.NOT_CONFIGURED.value
    assert resp.failure_reason in TOOL_REASONS
    assert resp.results == []


# ---- ② 传输层按异常类型精确归类 ----

@pytest.mark.parametrize("exc,expected", [
    (requests.exceptions.Timeout("t/o"), FailureReason.TIMEOUT.value),
    (requests.exceptions.ConnectionError("refused"), FailureReason.PROVIDER_ERROR.value),
])
def test_transport_failures(monkeypatch, exc, expected):
    import research_engine.search.tavily as T
    monkeypatch.setattr(T.requests, "post", _fake_post(raise_exc=exc))
    assert _provider().search("q").failure_reason == expected


# ---- ③ HTTP 状态：401/403 是「换配置」，5xx/429 是 provider 错 ----

@pytest.mark.parametrize("status,expected", [
    (401, FailureReason.NOT_CONFIGURED.value),
    (403, FailureReason.NOT_CONFIGURED.value),
    (500, FailureReason.PROVIDER_ERROR.value),
    (429, FailureReason.PROVIDER_ERROR.value),
])
def test_http_status(monkeypatch, status, expected):
    import research_engine.search.tavily as T
    monkeypatch.setattr(T.requests, "post", _fake_post(status=status))
    assert _provider().search("q").failure_reason == expected


# ---- ④ 解析失败 / 零结果 ----

def test_parse_error_when_response_not_json(monkeypatch):
    """拿到响应但解不出 JSON（网关拦截页 / 限流页）⇒ parse_error，不抛异常。"""
    import research_engine.search.tavily as T
    monkeypatch.setattr(T.requests, "post", _fake_post(status=200, payload=None))
    assert _provider().search("q").failure_reason == FailureReason.PARSE_ERROR.value


def test_empty_result_is_not_a_fault(monkeypatch):
    """零命中是「结果」不是「故障」（D-03）：记 empty_result 但不得上抛为 run 级降级。"""
    import research_engine.search.tavily as T
    monkeypatch.setattr(T.requests, "post", _fake_post(status=200, payload={"results": []}))
    resp = _provider().search("q")
    assert resp.failure_reason == FailureReason.EMPTY_RESULT.value
    assert resp.results == []


# ---- ⑤ 正常解析 ----

def test_parses_results(monkeypatch):
    import research_engine.search.tavily as T
    monkeypatch.setattr(T.requests, "post", _fake_post(status=200, payload={
        "results": [
            {"title": "T1", "url": "https://a", "content": "正文A", "score": 0.9},
            {"title": "", "url": "", "content": "无 URL 应被丢弃"},
        ],
    }))
    resp = _provider().search("q", max_results=5)
    assert resp.failure_reason is None
    assert resp.ok
    # 无 url 的条目不可引用，必须丢弃（否则 citation 校验会对不上来源）
    assert len(resp.results) == 1
    r = resp.results[0]
    assert (r.title, r.url, r.snippet) == ("T1", "https://a", "正文A")
    assert r.source == "web"
    assert r.metadata["score"] == 0.9


# ---- ⑥ 工厂装配：SEARCH_PROVIDER 开关真正生效 ----

def test_factory_builds_tavily():
    from research_engine.search.base import create_search_provider
    from research_engine.search.tavily import TavilySearchProvider
    assert isinstance(create_search_provider("tavily"), TavilySearchProvider)


def test_factory_builds_bocha():
    from research_engine.search.base import create_search_provider
    from research_engine.search.bocha import BochaSearchProvider
    assert isinstance(create_search_provider("bocha"), BochaSearchProvider)


def test_factory_rejects_unknown_provider():
    """配错搜索源要立刻炸，而不是静默回退到默认源（用错源跑完整场是最危险的）。"""
    from research_engine.search.base import create_search_provider
    with pytest.raises(ValueError, match="未知搜索 Provider"):
        create_search_provider("nonexistent")


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
