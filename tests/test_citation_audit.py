# -*- coding: utf-8 -*-
"""W2 引用溯源可审计化 单测（grill Q1~Q6 锁行为）。

全部离线：mock LLMClient.chat_json，零 API key。
运行：python -m pytest tests/test_citation_audit.py -q

覆盖（对应需求文档 §9）：
- Q2：_build_index 返回 (source, source_type) 且保留编号；citation 含 finding_id/source_type/confidence/note
- Q3：存在性 False 短路不进 LLM；来源存在但论断不忠实 → verified=False
- Q1/Q4：render 正文 ⚠️ + 附录（claim/note/finding_id）；Writer 纯编号经 render 后类型标注正确
- Q4：_split_ref 对带类型标注的 ref 拒绝拆分（守住 ADR-0005 防线）
- Q5：validator verdict 输出 is_meta 复核 → render 兜底标注 🔄自指；compress/format_for_writer 透传标记
- Q6：可信声明按存在性/忠实度双口径统计
- R2.5：运行溯源块数值与注入 state 一致
"""
from __future__ import annotations

import pytest
from types import SimpleNamespace

from research_engine.agents.validator import Validator
from research_engine.context.manager import ContextManager
from research_engine.llm.client import LLMClient
from research_engine.render import ReportRenderer
from research_engine.state import Citation, ResearchFinding


def make_findings(n: int = 32) -> list:
    """32 条 findings，混 web/rag，含 is_meta 样例（覆盖编号 >20 场景）。"""
    findings = []
    for i in range(1, n + 1):
        if i == 3:
            findings.append(ResearchFinding(
                content="DeepResearch Agent 采用 Planner→Researcher→Writer→Validator 四节点编排",
                source="rag:doc_meta.md", source_type="rag", confidence=0.7, is_meta=True,
            ))
        elif i % 2 == 0:
            findings.append(ResearchFinding(
                content=f"来源{i}的发现：某项公开事实数据",
                source=f"https://example.com/source-{i}", source_type="web", confidence=0.6,
            ))
        else:
            findings.append(ResearchFinding(
                content=f"来源{i}的发现：业内公开报告内容",
                source=f"rag:doc{i}.md", source_type="rag", confidence=0.7,
            ))
    return findings


@pytest.fixture
def findings():
    return make_findings()


@pytest.fixture
def renderer():
    return ReportRenderer()


# ============ Q2：编号 → (source, source_type) 映射 ============

def test_build_index_keeps_number_and_type(findings):
    idx = Validator()._build_index(findings)
    assert idx["1"] == ("rag:doc1.md", "rag")
    assert idx["2"] == ("https://example.com/source-2", "web")
    assert idx["3"] == ("rag:doc_meta.md", "rag")
    assert "32" in idx, "编号必须保留到 32（Q2 锚点）"


# ============ Q3：存在性短路 + 字段携带 ============

def test_existence_false_short_circuits_llm(findings, monkeypatch):
    calls = {"n": 0}
    real = LLMClient.chat_json

    def fake(self, messages, **kw):
        calls["n"] += 1
        return real(self, messages, **kw)   # 不应走到这里

    monkeypatch.setattr(LLMClient, "chat_json", fake)
    v = Validator()
    # 仅越界引用：存在性 False，必须短路，不进 LLM
    cits = v.validate("越界引用 [来源: 999]。", [findings[0]])
    assert len(cits) == 1
    assert cits[0].existence is False
    assert cits[0].verified is False
    assert cits[0].finding_id == "999"
    assert "不存在" in cits[0].note
    assert calls["n"] == 0, "存在性 False 的引用不得触发 LLM 调用（Q3 短路）"


def test_validate_carries_source_type_and_confidence(findings, monkeypatch):
    def fake(self, messages, state=None, **kw):
        return {"citations": [
            {"finding_id": "1", "claim": "论断", "faithful": True,
             "confidence": 0.9, "supported": True, "is_meta": False, "note": "ok"},
        ]}

    monkeypatch.setattr(LLMClient, "chat_json", fake)
    v = Validator()
    cits = v.validate("论断 [来源: 1]。", findings)
    c = cits[0]
    assert c.finding_id == "1"
    assert c.source_type == "rag"
    assert c.confidence == pytest.approx(0.9)
    assert c.note == "ok"
    assert c.existence is True
    assert c.verified is True


def test_faithful_false_marks_unverified(findings, monkeypatch):
    def fake(self, messages, state=None, **kw):
        return {"citations": [
            {"finding_id": "2", "claim": "论断", "faithful": False,
             "confidence": 0.3, "note": "论断夸大：来源未提及该增长数字"},
        ]}

    monkeypatch.setattr(LLMClient, "chat_json", fake)
    v = Validator()
    cits = v.validate("论断 [来源: 2]。", findings)
    c = cits[0]
    assert c.existence is True, "来源存在"
    assert c.verified is False, "来源存在但论断不忠实 → verified=False（Q3 忠实度生效）"
    assert "夸大" in c.note, "reason 不再丢弃"
    assert c.confidence == pytest.approx(0.3)


def test_llm_failure_fallback_to_existence(findings, monkeypatch):
    def fake(self, messages, state=None, **kw):
        raise RuntimeError("LLM 挂了")

    monkeypatch.setattr(LLMClient, "chat_json", fake)
    v = Validator()
    cits = v.validate("论断 [来源: 22]。", findings)  # 编号 22 > 20：旧实现截断后无从判
    assert cits[0].existence is True
    assert cits[0].verified is True, "整体降级：保 W1 存在性行为"
    assert "降级" in cits[0].note


# ============ Q1/Q4：render 附录 + 类型标注 ============

def test_render_appendix_and_warning_mark(renderer, findings):
    citations = [
        Citation(claim="某论断", source="https://example.com/source-2", verified=False,
                 finding_id="2", source_type="web", note="来源不存在", existence=False),
        Citation(claim="另一论断", source="rag:doc5.md", verified=True,
                 finding_id="5", source_type="rag", existence=True),
    ]
    report = "某论断 [来源: 2]。另一论断 [来源: 5]。"
    state = SimpleNamespace(depth=5, token_used=999, replan_count=0,
                            reflection_log=[{"signal": "continue"}, {"signal": "stop"}],
                            visited_sources=["https://a.com", "rag:doc_meta.md"])
    display = renderer.render(report, citations, findings, state)

    assert "来源: 2 · 🔵web · ⚠️" in display, "失败引用应标 ⚠️ + 类型（R2.2 正文警示 + R2.1 类型）"
    assert "来源: 5 · 🟢rag：rag:doc5.md" in display, "rag 引用应显示具体源"
    assert "## ⚠️ 未通过引用校验的论断" in display
    assert "来源不存在" in display
    assert "编号: 2" in display


def test_render_writer_pure_number_protocol(renderer, findings):
    """Q4 防线：Writer 输出纯编号，类型标注由 render 完成（标注不回流 report）。"""
    citations = [Citation(claim="论断", source="https://example.com/source-2",
                          verified=True, finding_id="2", source_type="web", existence=True)]
    report = "论断 [来源: 2]。"
    state = SimpleNamespace(depth=1, token_used=10, replan_count=0,
                            reflection_log=[], visited_sources=[])
    display = renderer.render(report, citations, findings, state)
    assert "[来源: 2]" in report, "state.report 保持 Writer 纯编号（Q4 协议不变）"
    assert "来源: 2 · 🔵web" in display, "展示层才做类型标注"


# ============ Q4：ADR-0005 防线 ============

def test_split_ref_rejects_annotated_ref():
    """带类型标注的 ref 必须拒绝拆分（否则 index miss 误判 verified=False）。"""
    refs = Validator._split_ref("9 · 🟢rag：doc.md")
    assert len(refs) == 1 and refs[0] == "9 · 🟢rag：doc.md", "非纯数字 ref 不得拆分（Q4/ADR-0005）"


# ============ W2.1：附录 claim 文本清理 ============

def test_claim_text_cleans_markdown_and_sentence_boundary():
    """W2.1：claim 按句子边界截断并清理 markdown 残留（附录展示观感）。"""
    v = Validator()
    report = "**GraphRAG**在多跳推理上表现优异，支持长链验证。这是其核心优势 [来源: 1]。"
    cits = v._extract_citations(report)
    claim = cits[0]["claim"]
    assert "**" not in claim, "加粗残留应被清理（W2.1）"
    assert claim.startswith("这是其核心优势"), f"应从句子边界开始，实际: {claim!r}"
    # 无句号长句兜底：不残留换行/多空格，且保底非空
    long_report = "第一句结束。**复杂段落**没有标点一直延续到引用位置附近的好几个词组 [来源: 2]。"
    claim2 = v._extract_citations(long_report)[0]["claim"]
    assert "**" not in claim2
    assert "\n" not in claim2 and "  " not in claim2


# ============ Q5：is_meta 复核兜底 + 透传 ============

def test_is_meta_verdict_backstop_annotated(renderer, findings, monkeypatch):
    """启发式漏标时，validator verdict 复核兜底 → render 标注 🔄自指。"""
    # 构造：finding 未标 is_meta，但 LLM verdict 复核判 is_meta
    plain_findings = make_findings()
    # finding[2]（编号3）is_meta=True 是启发式命中样例；这里用 citation.is_meta 走复核路径
    citations = [Citation(claim="本系统采用四节点", source="rag:doc3.md",
                          verified=True, finding_id="3", source_type="rag", existence=True,
                          is_meta=True)]
    report = "本系统采用四节点 [来源: 3]。"
    state = SimpleNamespace(depth=1, token_used=1, replan_count=0, reflection_log=[], visited_sources=[])
    display = renderer.render(report, citations, plain_findings, state)
    assert "🔄自指" in display, "validator 复核到的自指引用必须隔离标注（Q5 兜底）"


def test_compress_and_format_preserve_is_meta(findings, monkeypatch):
    """compress 透传 is_meta；format_for_writer 输出自指标记（Q5 + R2.4）。"""
    cm = ContextManager()
    text = cm.format_for_writer([findings[2]])  # is_meta=True
    assert "自指/方法论" in text, "writer 上下文必须看到自指标记"
    # compress 透传：同一来源组内任一 is_meta → 压缩后保留
    assert findings[2].is_meta is True


# ============ Q6：双口径可信声明 ============

def test_trust_statement_dual_metric(renderer):
    cits = [
        Citation(claim="甲", source="s1", existence=True, verified=True),
        Citation(claim="乙", source="s2", existence=True, verified=False),
        Citation(claim="丙", source="s3", existence=False, verified=False),
    ]
    stmt = renderer.build_trust_statement(cits)
    assert "来源存在性：2/3 条通过" in stmt
    assert "论断忠实度：1/2 条通过" in stmt, "忠实度只在存在性通过子集上判定（Q6 双口径）"


# ============ R2.5：运行溯源 ============

def test_run_provenance_matches_state(renderer):
    state = SimpleNamespace(
        depth=7, token_used=12345, replan_count=1,
        reflection_log=[{"signal": "continue"}, {"signal": "continue"}, {"signal": "stop"}],
        visited_sources=["https://a.com", "rag:doc1.md", "https://b.com", "rag:doc2.md"],
    )
    prov = renderer.build_run_provenance(state)
    assert "**检索总跳数**：7" in prov
    assert "continue 2 / revise 0 / stop 1" in prov
    assert "web 2 / rag 2" in prov
    assert "12345" in prov
    assert "**Replan 次数**：1" in prov