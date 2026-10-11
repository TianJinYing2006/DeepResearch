"""需求 34 E1：抓取策略层单测（TBD-2 拍板口径；零网络——fetch/robots/clock/sleep 注入）。"""
from __future__ import annotations

from research_engine.failure_reasons import FailureReason
from research_engine.net.robots import RobotsCache, RobotsDecision
from research_engine.net.safe_fetch import FetchResult, UnsafeUrlError
from research_engine.net.web_fetch import (
    STATUS_MISSING,
    STATUS_OK,
    STATUS_SKIPPED_BUDGET,
    STATUS_SKIPPED_PDF,
    STATUS_SKIPPED_ROBOTS,
    STATUS_UNSUPPORTED,
    FetchBudget,
    WebFetcher,
)

HTML = b"<html><body><p>hello world</p></body></html>"


class AllowAllRobots:
    def check(self, url: str) -> RobotsDecision:  # noqa: ARG002
        return RobotsDecision(True, "allow")


class DenyRobots:
    def check(self, url: str) -> RobotsDecision:  # noqa: ARG002
        return RobotsDecision(False, "disallow")


def _budget(fetches: int = 15, nbytes: int = 5 * 1024 * 1024, seconds: float = 90.0) -> FetchBudget:
    return FetchBudget(max_fetches=fetches, max_bytes=nbytes, max_seconds=seconds)


def _fetcher(fetch, *, robots=None, budget=None, **kw) -> WebFetcher:
    return WebFetcher(budget=budget or _budget(), robots=robots or AllowAllRobots(),
                      fetch=fetch, clock=lambda: 0.0, sleep=lambda _s: None,
                      per_domain_interval=0.0, **kw)


def _ok(url: str = "https://e.com/a", body: bytes = HTML,
        ctype: str = "text/html; charset=utf-8") -> FetchResult:
    return FetchResult(url=url, status=200, content_type=ctype, body=body)


# ---- 成功路径 ----

def test_success_extracts_text_and_counts():
    fetcher = _fetcher(lambda url, **kw: _ok())
    out = fetcher.fetch("https://e.com/a")
    assert out.status == STATUS_OK and "hello world" in out.extracted.text
    assert out.attempts == 1 and out.source_bytes == len(HTML)


# ---- 预算三闸 ----

def test_budget_fetches_gate():
    calls = []
    fetcher = _fetcher(lambda url, **kw: (calls.append(url), _ok(url))[1],
                       budget=_budget(fetches=1))
    assert fetcher.fetch("https://e.com/1").status == STATUS_OK
    out = fetcher.fetch("https://e.com/2")
    assert out.status == STATUS_SKIPPED_BUDGET and len(calls) == 1


def test_budget_bytes_and_seconds_gate():
    fetcher = _fetcher(lambda url, **kw: _ok(), budget=_budget(nbytes=1))
    assert fetcher.fetch("https://e.com/1").status == STATUS_OK  # 首次尝试仍放行
    assert fetcher.fetch("https://e.com/2").status == STATUS_SKIPPED_BUDGET
    fetcher2 = _fetcher(lambda url, **kw: _ok(), budget=_budget(seconds=0.0))
    assert fetcher2.fetch("https://e.com/1").status == STATUS_SKIPPED_BUDGET


# ---- robots（TBD-2② 拍板）----

def test_robots_disallow_skips_without_fetch():
    calls = []
    fetcher = _fetcher(lambda url, **kw: (calls.append(url), _ok())[1], robots=DenyRobots())
    out = fetcher.fetch("https://e.com/a")
    assert out.status == STATUS_SKIPPED_ROBOTS and out.detail == "disallow" and calls == []


def test_robots_cache_unavailable_default_deny_and_approved():
    def fail(url, **kw):
        raise OSError("no route to host")

    deny = RobotsCache(user_agent="T", approved_domains=set(), fetch=fail)
    decision = deny.check("https://e.com/a")
    assert decision.allowed is False and decision.detail == "unavailable_deny"
    approve = RobotsCache(user_agent="T", approved_domains={"e.com"}, fetch=fail)
    decision2 = approve.check("https://e.com/a")
    assert decision2.allowed is True and decision2.detail == "approved"


def test_robots_parse_disallow_and_404_allow():
    robots_txt = b"User-agent: *\nDisallow: /private\n"

    def fetch(url, **kw):
        assert url == "https://e.com/robots.txt"
        return FetchResult(url=url, status=200, content_type="text/plain", body=robots_txt)

    cache = RobotsCache(user_agent="T", fetch=fetch)
    assert cache.check("https://e.com/private/x").allowed is False
    assert cache.check("https://e.com/public").allowed is True

    def fetch_404(url, **kw):
        return FetchResult(url=url, status=404, content_type="text/plain", body=b"")

    cache404 = RobotsCache(user_agent="T", fetch=fetch_404)
    assert cache404.check("https://e.com/anything").allowed is True


def test_robots_redirect_tries_other_scheme_same_host():
    def fetch(url, **kw):
        if url.startswith("https://"):
            return FetchResult(url=url, status=301, content_type="text/plain", body=b"")
        return FetchResult(url=url, status=200, content_type="text/plain",
                           body=b"User-agent: *\nAllow: /\n")

    cache = RobotsCache(user_agent="T", fetch=fetch)
    assert cache.check("https://e.com/a").allowed is True


# ---- 重试 / 熔断 / 分类 ----

def test_timeout_retries_capped_and_counted_in_quota():
    calls = []

    def boom(url, **kw):
        calls.append(url)
        raise TimeoutError("timed out")

    budget = _budget()
    fetcher = _fetcher(boom, budget=budget)
    out = fetcher.fetch("https://e.com/a")
    assert out.status == STATUS_MISSING and out.reason == FailureReason.TIMEOUT.value
    assert out.attempts == 3 and len(calls) == 3   # 1 次 + 重试 ≤2
    assert budget.snapshot()["fetches"] == 3       # 重试计入抓取额度


def test_http_error_not_retried():
    calls = []
    fetcher = _fetcher(lambda url, **kw: (calls.append(url),
                                          FetchResult(url, 404, "text/html", b""))[1])
    out = fetcher.fetch("https://e.com/a")
    assert out.status == STATUS_MISSING and out.detail == "http_404" and len(calls) == 1


def test_circuit_breaker_after_two_failures():
    calls = []

    def boom(url, **kw):
        calls.append(url)
        raise TimeoutError("timed out")

    fetcher = _fetcher(boom)
    assert fetcher.fetch("https://e.com/a").status == STATUS_MISSING
    assert fetcher.fetch("https://e.com/b").status == STATUS_MISSING
    fired = len(calls)
    out = fetcher.fetch("https://e.com/c")
    assert out.status == STATUS_MISSING and out.detail == "circuit_open"
    assert len(calls) == fired  # 熔断后不再发起网络请求


def test_redirect_and_too_large_classification():
    def redirect(url, **kw):
        raise UnsafeUrlError("redirects are not followed")

    def too_large(url, **kw):
        raise UnsafeUrlError("response exceeds 2097152 bytes")

    out1 = _fetcher(redirect).fetch("https://e.com/a")
    assert out1.status == STATUS_MISSING and out1.detail == "redirect"
    assert out1.reason == FailureReason.PROVIDER_ERROR.value
    out2 = _fetcher(too_large).fetch("https://e.com/a")
    assert out2.reason == FailureReason.PARSE_ERROR.value


def test_pdf_and_js_page():
    pdf = _fetcher(lambda url, **kw: _ok(ctype="application/pdf", body=b"%PDF-1.7"))
    assert pdf.fetch("https://e.com/a.pdf").status == STATUS_SKIPPED_PDF
    js_page = _fetcher(lambda url, **kw: _ok(body=b"<html><body>   </body></html>"))
    assert js_page.fetch("https://e.com/a").status == STATUS_UNSUPPORTED


def test_non_text_content_type_parse_error():
    fetcher = _fetcher(lambda url, **kw: _ok(ctype="image/png", body=b"\x89PNG"))
    out = fetcher.fetch("https://e.com/a.png")
    assert out.status == STATUS_MISSING and out.reason == FailureReason.PARSE_ERROR.value


# ---- 限速（同域名间隔）----

def test_same_domain_interval_uses_injected_sleep():
    clock = {"t": 0.0}
    slept = []

    def fetch(url, **kw):
        clock["t"] += 0.01
        return _ok(url)

    def fake_sleep(seconds):
        slept.append(seconds)
        clock["t"] += seconds

    fetcher = WebFetcher(budget=_budget(), robots=AllowAllRobots(), fetch=fetch,
                         clock=lambda: clock["t"], sleep=fake_sleep,
                         per_domain_interval=0.2)
    fetcher.fetch("https://e.com/1")
    fetcher.fetch("https://e.com/2")
    assert len(slept) == 1 and abs(slept[0] - 0.2) < 1e-6
