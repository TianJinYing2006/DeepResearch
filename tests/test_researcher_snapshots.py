"""需求 34 E1：researcher 快照接线单测（零网络；fetcher/store 全注入）。"""
from __future__ import annotations

from research_engine.agents.researcher import Researcher
from research_engine.net.web_fetch import (
    STATUS_DISABLED,
    STATUS_MISSING,
    STATUS_NOT_ATTEMPTED,
    STATUS_OK,
    FetchBudget,
    FetchOutcome,
)
from research_engine.net.webtext import EXTRACTOR_VERSION, ExtractedText
from research_engine.search.base import SearchResponse, SearchResult
from research_engine.snapshots import FetchContext, snapshot_id_for, use_fetch_context


class FakeSearch:
    def __init__(self, results):
        self._results = results

    def search(self, query, max_results=8):  # noqa: ARG002
        return SearchResponse(query=query, results=self._results)


class StubFetcher:
    def __init__(self, outcomes):
        self._outcomes = outcomes
        self.calls = []

    def fetch_many(self, urls):
        self.calls.append(list(urls))
        return [self._outcomes[u] for u in urls]


class StubStore:
    def __init__(self, fail: bool = False):
        self.saved = []
        self.fail = fail

    def save_snapshot(self, **kw):
        if self.fail:
            raise RuntimeError("pg down")
        self.saved.append(kw)
        return snapshot_id_for(kw["text"], kw["extractor_version"])


def _result(i: int) -> SearchResult:
    return SearchResult(title=f"标题{i}", url=f"https://e{i}.com/a", snippet=f"摘要{i}")


def _researcher(results):
    researcher = Researcher()
    researcher.search = FakeSearch(results)
    return researcher


def _ctx(fetcher, store=None, run_id="run-1"):
    return FetchContext(store=store, run_id=run_id,
                        budget=FetchBudget(max_fetches=15, max_bytes=5 * 1024 * 1024,
                                           max_seconds=90.0),
                        fetcher=fetcher)


def _ok_outcome(url: str, text: str = "正文全文内容") -> FetchOutcome:
    return FetchOutcome(url, STATUS_OK, content_type="text/html", source_bytes=100,
                        extracted=ExtractedText(kind="html", text=text))


def test_no_context_zero_change():
    """未绑定抓取上下文（功能关）⇒ 不抓、不加标记（零行为变化）。"""
    researcher = _researcher([_result(0)])
    findings = researcher._search_web("q")
    assert findings[0].content.startswith("标题0")
    assert "snapshot_status" not in findings[0].metadata


def test_success_replaces_content_and_persists():
    results = [_result(i) for i in range(4)]
    fetcher = StubFetcher({f"https://e{i}.com/a": _ok_outcome(f"https://e{i}.com/a")
                           for i in range(3)})
    store = StubStore()
    researcher = _researcher(results)
    with use_fetch_context(_ctx(fetcher, store)):
        findings = researcher._search_web("q")

    # 只抓 top-3；第 4 条标 not_attempted
    assert fetcher.calls == [["https://e0.com/a", "https://e1.com/a", "https://e2.com/a"]]
    assert findings[0].content.startswith("标题0")
    assert "正文全文内容" in findings[0].content
    assert findings[0].metadata["snapshot_status"] == STATUS_OK
    assert findings[0].metadata["snapshot_id"] == snapshot_id_for(
        "正文全文内容", EXTRACTOR_VERSION)
    assert findings[3].metadata["snapshot_status"] == STATUS_NOT_ATTEMPTED
    assert len(store.saved) == 3
    assert store.saved[0]["extractor_version"] == EXTRACTOR_VERSION
    assert store.saved[0]["run_id"] == "run-1"
    # N5：抓取不写降级日志
    assert researcher.drain_degradations() == []


def test_no_store_fetches_but_marks_disabled():
    """CLI/eval 无任务库：抓取继续、正文进证据，标记 disabled（TBD-2 not_configured 行）。"""
    fetcher = StubFetcher({"https://e0.com/a": _ok_outcome("https://e0.com/a")})
    researcher = _researcher([_result(0)])
    with use_fetch_context(_ctx(fetcher, store=None)):
        findings = researcher._search_web("q")
    assert "正文全文内容" in findings[0].content
    assert findings[0].metadata["snapshot_status"] == STATUS_DISABLED
    assert "snapshot_id" not in findings[0].metadata


def test_failure_degrades_to_snippet_only():
    fetcher = StubFetcher({"https://e0.com/a": FetchOutcome(
        "https://e0.com/a", STATUS_MISSING, reason="timeout", detail="timeout")})
    researcher = _researcher([_result(0)])
    with use_fetch_context(_ctx(fetcher, store=StubStore())):
        findings = researcher._search_web("q")
    assert findings[0].content.startswith("标题0")          # snippet-only 降级
    assert findings[0].metadata["snapshot_status"] == STATUS_MISSING
    assert findings[0].metadata["snapshot_reason"] == "timeout"
    assert researcher.drain_degradations() == []            # 不写降级日志（N5）


def test_store_failure_falls_back_to_disabled():
    fetcher = StubFetcher({"https://e0.com/a": _ok_outcome("https://e0.com/a")})
    researcher = _researcher([_result(0)])
    with use_fetch_context(_ctx(fetcher, store=StubStore(fail=True))):
        findings = researcher._search_web("q")
    assert findings[0].metadata["snapshot_status"] == STATUS_DISABLED
    assert findings[0].metadata["snapshot_detail"].startswith("store_error:")
    assert "正文全文内容" in findings[0].content                 # 正文仍进证据
