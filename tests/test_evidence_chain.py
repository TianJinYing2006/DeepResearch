# -*- coding: utf-8 -*-
"""审计 F07/F08 回归：证据三层（原文 / 工作摘要 / 引用）与验证保真。

- 原文层：append-only + 内容寻址稳定 ID / hash / 检索时间；
- 工作摘要层：compress 产物携带 origin_evidence_ids 回指原文；
- Validator：按 origin 链回原文取正文（不再 500 字符静默截断），
  截断/缺失显式计入 stats；无引用事实句覆盖率随 stats 落库。

全部离线：注入 llm_fn / monkeypatch LLMClient，零 API key。
"""
from __future__ import annotations

from research_engine.agents.validator import Validator
from research_engine.context.manager import ContextManager
from research_engine.evidence import (
    build_evidence_index,
    ensure_evidence_identity,
    resolve_evidence_text,
)
from research_engine.state import ResearchFinding, ResearchState, SubQuestion


def _finding(content: str, source: str = "https://a", sq_id: str = "q1",
             source_type: str = "web") -> ResearchFinding:
    return ResearchFinding(content=content, source=source, source_type=source_type,
                           sq_id=sq_id, confidence=0.6)


# ---------------------------------------------------------------- 证据身份

def test_evidence_identity_is_content_addressed_and_idempotent():
    a = _finding("同一内容")
    b = _finding("同一内容")
    c = _finding("不同内容")
    assert ensure_evidence_identity([a, b, c]) == 3
    assert a.evidence_id == b.evidence_id != c.evidence_id
    assert a.content_hash and a.retrieved_at > 0
    first = a.evidence_id
    assert ensure_evidence_identity([a]) == 0, "身份补齐必须幂等"
    assert a.evidence_id == first


def test_resolve_evidence_text_head_tail_and_missing():
    raw = _finding("H" * 80 + "TAIL-FACT")
    ensure_evidence_identity([raw])
    working = _finding("[摘要]")
    working.metadata = {"origin_evidence_ids": [raw.evidence_id]}

    text, status = resolve_evidence_text(working, build_evidence_index([raw]), max_chars=40)
    assert status == "truncated"
    assert "TAIL-FACT" in text, "头尾保留必须覆盖长文后段事实"

    text2, status2 = resolve_evidence_text(working, {})
    assert status2 == "missing" and text2 == "[摘要]", "原文缺失显式回落摘要"


# ---------------------------------------------------------------- 压缩来源链

def test_compress_carries_origin_evidence_ids(monkeypatch):
    import research_engine.context.manager as manager_module

    class _Router:
        def fast_chat(self, system, user, state=None):
            return "[摘要]"

    monkeypatch.setattr(manager_module, "get_router", lambda: _Router())
    findings = [_finding(f"材料 {i}", source="rag:doc.md", sq_id="q1") for i in range(31)]
    ensure_evidence_identity(findings)

    compressed = ContextManager(max_findings=30).compress(findings, "主题")

    assert len(compressed) == 1
    origins = compressed[0].metadata["origin_evidence_ids"]
    assert origins == [f.evidence_id for f in findings], "摘要必须回指全部原文证据"


def test_write_node_keeps_raw_layer():
    """graph._write 只写工作摘要层；原文层 findings 不被覆写（F08）。"""
    from research_engine.graph import DeepResearchGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)

    class _Ctx:
        def compress(self, findings, topic, state=None):
            return [ResearchFinding(
                content="[摘要]", source="rag:doc.md", source_type="rag", sq_id="q1",
                metadata={"origin_evidence_ids": [f.evidence_id for f in findings]})]

    class _Writer:
        def __init__(self):
            self.seen = None

        def write(self, topic, subquestions, findings, state=None):
            self.seen = findings
            return "# 报告"

        def drain_degradations(self):
            return []

    writer = _Writer()
    graph.context = _Ctx()
    graph.writer = writer
    state = ResearchState(
        topic="t", subquestions=[SubQuestion(id="q1", question="Q", rationale="r")],
        findings=[_finding("原文 1"), _finding("原文 2")],
    )
    delta = graph._write(state)

    assert "working_findings" in delta and len(delta["working_findings"]) == 1
    assert "findings" not in delta, "原文层不得被 compress 覆写"
    assert writer.seen == delta["working_findings"], "Writer 必须消费工作摘要层"


# ---------------------------------------------------------------- Validator 回原文

def test_validator_uses_original_evidence_beyond_500_chars(monkeypatch):
    """F07：关键事实位于摘要之外（>500 字符处）时，仍必须进入校验输入。"""
    raw = _finding("前缀" * 300 + "关键事实：召回率提升 12%")
    ensure_evidence_identity([raw])
    working = ResearchFinding(
        content="[摘要] 泛指提到召回率", source=raw.source, source_type="web", sq_id="q1",
        metadata={"origin_evidence_ids": [raw.evidence_id]})

    captured: dict = {}

    def _chat_json(self, messages, state=None, schema=None):
        captured["user"] = next(m["content"] for m in messages if m["role"] == "user")
        return {"citations": [{"finding_id": "1", "claim": "召回率提升 12%",
                               "faithful": True, "supported": False,
                               "confidence": 0.9, "note": ""}]}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)

    validator = Validator()
    citations = validator.validate("系统召回率提升 12% [来源: 1]", [working],
                                   evidence=[raw])

    assert "关键事实：召回率提升 12%" in captured["user"], "校验输入必须回到原文层"
    assert citations and citations[0].existence is True


def test_validator_reports_truncation_and_missing(monkeypatch):
    """F07：预算截断 / 原文缺失必须显式计入 stats（不静默）。"""
    import research_engine.evidence as evidence_module

    long_raw = _finding("长" * 300)
    ensure_evidence_identity([long_raw])
    truncated = ResearchFinding(
        content="[摘要A]", source=long_raw.source, source_type="web",
        metadata={"origin_evidence_ids": [long_raw.evidence_id]})
    missing = _finding("[摘要B]", source="https://missing")
    missing.metadata = {"origin_evidence_ids": ["ev_not_found"]}

    monkeypatch.setattr(evidence_module, "EVIDENCE_TEXT_MAX_CHARS", 100)

    def _chat_json(self, messages, state=None, schema=None):
        return {"citations": []}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)

    validator = Validator()
    validator.validate("论断甲足够长可以进校验 [来源: 1]，论断乙同样足够长 [来源: 2]",
                       [truncated, missing], evidence=[long_raw])

    stats = validator.last_validation_stats
    assert stats["evidence_truncated_count"] == 1
    assert stats["evidence_missing_count"] == 1


# ---------------------------------------------------------------- F11 覆盖率统计

def test_uncited_fact_coverage_stats():
    report = (
        "# 标题\n\n"
        "本次研究覆盖了多个来源的公开信息，系统整体表现稳定 [来源: 1]。\n\n"
        "另一个重要结论是长文后段事实也应当可以被完整核验。\n\n"
        "最后一个带引用的论断同样需要被校验 [来源: 2]。\n"
    )
    stats = Validator._fact_coverage_stats(report)
    assert stats["fact_sentence_count"] == 3
    assert stats["uncited_fact_sentence_count"] == 1
    assert stats["citation_coverage"] == round(2 / 3, 4)


def test_validation_stats_include_coverage_and_evidence_health(monkeypatch):
    findings = [_finding("发现一")]

    def _chat_json(self, messages, state=None, schema=None):
        return {"citations": []}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)
    validator = Validator()
    validator.validate("这条论断足够长且带引用 [来源: 1]", findings)
    stats = validator.last_validation_stats
    for key in ("fact_sentence_count", "uncited_fact_sentence_count", "citation_coverage",
                "evidence_truncated_count", "evidence_missing_count"):
        assert key in stats
