"""需求 34 收口：证据链端到端集成（fake fetch + 小快照；全链路零网络）。

覆盖：fake 搜索 → E1 抓取（stub）→ 快照（stub store）→ E2 逐字引文定位 →
E4 自检 → E5 分级 → 渲染附录（report_display）。
"""
from __future__ import annotations

from research_engine.agents.researcher import Researcher
from research_engine.agents.validator import Validator
from research_engine.evidence_grading import grade_evidence_chain
from research_engine.net.web_fetch import STATUS_OK, FetchBudget, FetchOutcome
from research_engine.net.webtext import ExtractedText
from research_engine.render import ReportRenderer
from research_engine.search.base import SearchResponse, SearchResult
from research_engine.self_check import run_delivery_self_check
from research_engine.snapshots import FetchContext, snapshot_id_for, use_fetch_context
from research_engine.state import Citation, ResearchFinding, ResearchState

SUPPORT = "量子计算在2024年取得突破性进展。"
CLAIM = "量子计算于2024年取得突破"
SNAPSHOT = f"导语。\n\n{SUPPORT}\n\n后续内容。"


class FakeSearch:
    def search(self, query, max_results=8):  # noqa: ARG002
        return SearchResponse(query=query, results=[
            SearchResult(title="量子计算年度综述", url="https://e.com/a", snippet="摘要片段"),
        ])


class StubStore:
    def __init__(self):
        self.saved: dict = {}

    def save_snapshot(self, **kw):
        snapshot_id = snapshot_id_for(kw["text"], kw["extractor_version"])
        self.saved[snapshot_id] = kw["text"]
        return snapshot_id

    def load_snapshot(self, sha256):
        return self.saved.get(sha256)


class StubFetcher:
    def fetch_many(self, urls):
        return [FetchOutcome(u, STATUS_OK, content_type="text/html",
                             source_bytes=len(SNAPSHOT),
                             extracted=ExtractedText(kind="html", text=SNAPSHOT))
                for u in urls]


def test_evidence_chain_end_to_end(monkeypatch):
    store = StubStore()
    ctx = FetchContext(store=store, run_id="run-e2e",
                       budget=FetchBudget(max_fetches=15, max_bytes=5 * 1024 * 1024,
                                          max_seconds=90.0),
                       fetcher=StubFetcher())
    with use_fetch_context(ctx):
        # E1：researcher 抓取 → 快照落盘（内容寻址）
        researcher = Researcher()
        researcher.search = FakeSearch()
        findings = researcher._search_web("量子计算进展")
        assert findings and SUPPORT in findings[0].content
        snapshot_id = findings[0].metadata["snapshot_id"]
        assert findings[0].metadata["snapshot_status"] == "ok"
        assert store.saved[snapshot_id] == SNAPSHOT
        assert not researcher.drain_degradations()

        # E2：validator 逐字引文（LLM 全注入；代码定位 L0~L2）
        monkeypatch.setattr(
            "research_engine.llm.client.LLMClient.chat_json",
            lambda self, messages, state=None, schema=None: {"citations": [{
                "finding_id": "1", "claim": CLAIM, "claim_echo": CLAIM,
                "faithful": True, "supported": False, "confidence": 0.9, "note": ""}]})
        validator = Validator()
        citations = validator.validate(f"{CLAIM} [来源: 1]", findings)
        citation = citations[0]
        assert citation.quote_status == "located"
        assert citation.snapshot_id == snapshot_id
        assert SNAPSHOT[citation.char_start:citation.char_end] == citation.quote
        assert SUPPORT in citation.quote
        assert citation.match_level in ("L0", "L1", "L2")

        # E4：零模型自检（离线复算：0 违例）
        report = f"{CLAIM} [来源: 1]"
        check = run_delivery_self_check(report, citations)
        assert all(item["violations"] == 0 for item in check["checks"])

        # E5：分级处置（本链无致命项；全净链路无分级项 ⇒ 附录按设计省略）
        grading = grade_evidence_chain(citations, findings, report)
        assert grading["counts"] == {"total": 0, "fatal": 0, "repairable": 0, "by_reason": {}}

        # 渲染附录（report_display）
        state = ResearchState(topic="量子计算进展", report=report)
        display = ReportRenderer().render(report, citations, findings, state,
                                          delivery_self_check=check,
                                          evidence_grading=grading)
        assert "交付自检" in display
        assert "证据链分级处置" not in display

        # 有分级项（抓取失败类）⇒ 附录出现
        missing = ResearchFinding(content="x", source="https://e2.com/b", source_type="web",
                                  confidence=0.6, metadata={"snapshot_status": "missing"})
        grading2 = grade_evidence_chain(
            [Citation(claim=CLAIM, source="https://e2.com/b", finding_id="1",
                      existence=True, verified=True)], [missing], report)
        assert grading2["counts"]["repairable"] == 1
        display2 = ReportRenderer().render(report, citations, findings, state,
                                           delivery_self_check=check,
                                           evidence_grading=grading2)
        assert "证据链分级处置" in display2
