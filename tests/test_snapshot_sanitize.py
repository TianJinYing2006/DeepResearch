"""需求 42：web 快照正文净化（写侧入库前 + validator 读侧纵深防御）。

审计：`researcher._apply_web_snapshot` 把**未净化**的 `extracted.text` 交给
`save_snapshot`，而 `validator` 的窗口 / quote 又从快照原文取文本 ⇒ 恶意网页的
零宽 / bidi / tag-block 走私字符可进入 fact-check 窗口或被写进引文（可影响
faithful 判定，间接假绿；也会随引文外泄字节）。

本测试零网络、不调 LLM（store / fetcher / LLM 全部注入或 monkeypatch）。
"""
from __future__ import annotations

from research_engine.agents.researcher import Researcher
from research_engine.agents.validator import Validator
from research_engine.net.web_fetch import STATUS_OK, FetchBudget, FetchOutcome
from research_engine.net.webtext import EXTRACTOR_VERSION, ExtractedText
from research_engine.sanitize import count_invisible, strip_invisible
from research_engine.search.base import SearchResponse, SearchResult
from research_engine.snapshots import FetchContext, snapshot_id_for, use_fetch_context
from research_engine.state import ResearchFinding

SUPPORT = "量子计算在2024年取得突破性进展。"
CLAIM = "量子计算在2024年取得突破"
#: 零宽空格 + bidi override + tag-block 走私字符（正常渲染不可见）
INVISIBLE = "\u200b\u202e\U000E0041"
#: 把不可见字符插进**论断与引文之间的正文**：不净化就会随窗口 / 引文一起带出
DIRTY_TEXT = CLAIM + INVISIBLE + "性进展。"
CLEAN_TEXT = strip_invisible(DIRTY_TEXT)


class _ReadStore:
    """只读快照 store（validator 读侧用例）。"""

    def __init__(self, snapshots: dict):
        self._snapshots = snapshots

    def save_snapshot(self, **_kw):  # pragma: no cover - 读侧用例不写
        raise AssertionError("读侧用例不应写快照")

    def load_snapshot(self, sha256: str):
        return self._snapshots.get(sha256)


class _WriteStore(_ReadStore):
    """记录落盘参数并返回内容寻址 id（researcher 写侧用例）。"""

    def __init__(self):
        super().__init__({})
        self.saved: list[dict] = []

    def save_snapshot(self, **kw):
        self.saved.append(kw)
        return snapshot_id_for(kw["text"], kw["extractor_version"])


class _Search:
    def __init__(self, results):
        self._results = results

    def search(self, query, max_results=8):  # noqa: ARG002
        return SearchResponse(query=query, results=self._results)


class _Fetcher:
    def __init__(self, outcomes):
        self._outcomes = outcomes

    def fetch_many(self, urls):
        return [self._outcomes[url] for url in urls]


def _ctx(store, fetcher=None):
    return FetchContext(store=store, run_id="run-1",
                        budget=FetchBudget(max_fetches=15, max_bytes=5 * 1024 * 1024,
                                           max_seconds=90.0),
                        fetcher=fetcher)


def _long_content() -> str:
    # >4000 字符且不含支持片段 ⇒ 走快照窗口路径（与 test_validator_windows 同口径）
    return "无关内容段。" * 700 + "[截断]"


def _web_finding(snapshot_id: str = "sha-1") -> ResearchFinding:
    return ResearchFinding(
        content=_long_content(), source="https://e.com/a", source_type="web",
        confidence=0.6, metadata={"snapshot_id": snapshot_id, "snapshot_status": "ok"})


# ------------------------------------------------------------------ 写侧

def test_web_snapshot_is_sanitized_before_persist():
    """写侧：落库快照正文与 finding 正文都必须已净化（单一来源）。"""
    url = "https://e0.com/a"
    dirty_body = "导语。" * 3 + DIRTY_TEXT + "结尾。" * 3
    fetcher = _Fetcher({url: FetchOutcome(
        url, STATUS_OK, content_type="text/html", source_bytes=100,
        extracted=ExtractedText(kind="html", text=dirty_body))})
    store = _WriteStore()
    researcher = Researcher()
    researcher.search = _Search(
        [SearchResult(title="标题\ufeff", url=url, snippet="摘要")])

    with use_fetch_context(_ctx(store, fetcher)):
        findings = researcher._search_web("q")

    assert findings and count_invisible(findings[0].content) == 0
    assert CLEAN_TEXT in findings[0].content            # 正文仍含可见内容（未误删）
    assert len(store.saved) == 1
    saved = store.saved[0]["text"]
    assert saved == strip_invisible(dirty_body)          # 落库文本已净化
    assert CLEAN_TEXT in saved
    assert count_invisible(saved) == 0
    # snapshot_id 基于净化后文本（内容寻址 / 同文去重口径一致）
    assert findings[0].metadata["snapshot_id"] == snapshot_id_for(saved, EXTRACTOR_VERSION)


# ------------------------------------------------------------------ 读侧（纵深）

def test_validator_window_evidence_has_no_invisible_chars():
    """读侧：历史脏快照取窗口后，裁判 prompt 里的证据不含不可见字符。"""
    snapshot = "导语。" * 100 + DIRTY_TEXT + "结尾。" * 100
    assert count_invisible(snapshot) > 0                  # 前置：快照确实脏
    store = _ReadStore({"sha-1": snapshot})
    stats: dict = {}
    statuses: dict = {}
    to_check = [{"claim": CLAIM, "finding_id": "1", "source": "https://e.com/a"}]

    with use_fetch_context(_ctx(store)):
        lines, _trimmed = Validator._build_findings_lines(
            [_web_finding()], to_check, {}, stats, statuses)

    rendered = "\n".join(lines.values())
    assert CLAIM in rendered
    assert count_invisible(rendered) == 0
    assert statuses["1"] == "windowed"


def test_validator_quote_has_no_invisible_chars(monkeypatch):
    """读侧：quote 定位从脏快照取到的引文不得携带走私字符。"""
    snapshot = f"背景资料。\n\n{CLAIM}{INVISIBLE}性进展。\n\n后续内容。"
    assert count_invisible(snapshot) > 0                  # 前置：快照确实脏
    store = _ReadStore({"sha-1": snapshot})
    verdict = {"citations": [{"finding_id": "1", "claim": CLAIM, "claim_echo": CLAIM,
                              "faithful": True, "supported": False,
                              "confidence": 0.9, "note": ""}]}
    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json",
                        lambda self, messages, state=None, schema=None: verdict)

    with use_fetch_context(_ctx(store)):
        validator = Validator()
        citations = validator.validate(f"{CLAIM} [来源: 1]", [_web_finding()])

    citation = citations[0]
    assert citation.quote_status == "located"
    assert citation.quote and count_invisible(citation.quote) == 0
    assert CLAIM in citation.quote
