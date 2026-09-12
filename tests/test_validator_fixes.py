# -*- coding: utf-8 -*-
"""W7 Arm 3：validator 五项修复 + 双口径单测（零 API）。

覆盖：
- F1 论断 claim 不再截断
- F2 claim_echo 回显错位 → 标 unreliable 降级通过留痕
- F3 非论断句/元话语被过滤
- F4 _claim_text 无主语时前补主语
- TBD-5 verified_relaxed = existence AND (faithful OR supported)
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from research_engine.agents.validator import Validator
from research_engine.state import Citation, ResearchFinding


def _mock_llm_client(verdicts: list):
    """构造一个会返回给定 verdicts 的 mock LLMClient。"""
    patcher = patch("research_engine.agents.validator.LLMClient")

    def _enter():
        MockClient = patcher.start()
        inst = MagicMock()
        inst.chat_json.return_value = {"citations": verdicts}
        MockClient.return_value = inst
        return MockClient

    def _exit():
        patcher.stop()

    return _enter, _exit


# ---------- F1：claim 不截断 ----------

def test_f1_long_claim_not_truncated():
    v = Validator()
    long_claim = "Transformer 模型" * 20  # 长 claim
    report = f"{long_claim}[来源: 1]"
    citations = v._extract_citations(report)
    assert len(citations) == 1
    # W7 F1：送 LLM 的 claim 不再截断到 100 字符（窗口 200，原行为只取 100）
    assert len(citations[0]["claim"]) > 100
    assert "Transformer 模型" in citations[0]["claim"]


# ---------- F2：claim_echo 回显错位降级 ----------

def test_f2_claim_echo_mismatch_degraded():
    v = Validator()
    report = "Transformer 使用自注意力机制。[来源: 1]"
    findings = [
        ResearchFinding(
            content="Transformer 使用自注意力。",
            source="https://example.com/t",
            source_type="web",
        )
    ]
    enter, exit = _mock_llm_client([{
        "finding_id": "1",
        "claim": "Transformer 使用自注意力机制。",
        "claim_echo": "这是完全不同的 claim",  # 错位
        "faithful": False,
        "supported": False,
        "confidence": 0.9,
        "is_meta": False,
        "note": "内容不匹配",
    }])
    enter()
    try:
        citations = v.validate(report, findings)
    finally:
        exit()

    assert len(citations) == 1
    c = citations[0]
    assert isinstance(c, Citation)
    assert c.verified is True  # 降级保守通过
    assert "claim_echo 错位" in c.note
    assert c.verified_relaxed is True  # 保守通过应传导到宽松口径（Bug-1 修复）


def test_f2_claim_echo_match_normal():
    v = Validator()
    report = "Transformer 使用自注意力机制。[来源: 1]"
    findings = [
        ResearchFinding(
            content="Transformer 使用自注意力。",
            source="https://example.com/t",
            source_type="web",
        )
    ]
    enter, exit = _mock_llm_client([{
        "finding_id": "1",
        "claim": "Transformer 使用自注意力机制。",
        "claim_echo": "Transformer 使用自注意力机制。",  # 一致
        "faithful": True,
        "supported": False,
        "confidence": 0.95,
        "is_meta": False,
        "note": "",
    }])
    enter()
    try:
        citations = v.validate(report, findings)
    finally:
        exit()

    assert citations[0].verified is True
    assert "claim_echo 错位" not in citations[0].note


# ---------- F3：非论断过滤 ----------

def test_f3_meta_claim_filtered():
    v = Validator()
    report = "综上所述 [来源: 1]，Transformer 使用自注意力 [来源: 2]。"
    citations = v._extract_citations(report)
    assert len(citations) == 1
    assert "综上所述" not in citations[0]["claim"]
    assert "Transformer" in citations[0]["claim"]


def test_f3_table_fragment_filtered():
    v = Validator()
    report = "| 方法 | 准确率 | [来源: 1] |  Transformer 优于 CNN [来源: 2]。"
    citations = v._extract_citations(report)
    assert len(citations) == 1
    assert "Transformer" in citations[0]["claim"]


# ---------- F4：主语兜底 ----------

def test_f4_subject_backed():
    v = Validator()
    report = "Transformer 模型结构复杂。其自注意力机制是核心 [来源: 1]。"
    citations = v._extract_citations(report)
    assert len(citations) == 1
    claim = citations[0]["claim"]
    assert "其自注意力" in claim
    # 前补主语后 claim 应包含 Transformer
    assert "Transformer" in claim


# ---------- TBD-5：双口径 ----------

def test_verified_relaxed_when_supported_but_not_faithful():
    v = Validator()
    report = "论断 A 是系统生成的结论 [来源: 1]"
    findings = [
        ResearchFinding(
            content="论断 A 在 elsewhere 被支持。",
            source="https://example.com/a",
            source_type="web",
        )
    ]
    enter, exit = _mock_llm_client([{
        "finding_id": "1",
        "claim": "论断 A 是系统生成的结论",
        "claim_echo": "论断 A 是系统生成的结论",
        "faithful": False,
        "supported": True,
        "confidence": 0.7,
        "is_meta": False,
        "note": "引错编号但 supported=true",
    }])
    enter()
    try:
        citations = v.validate(report, findings)
    finally:
        exit()

    c = citations[0]
    assert c.verified is False        # strict：faithful false
    assert c.verified_relaxed is True # relaxed：supported true


def test_verified_relaxed_false_when_existence_false():
    v = Validator()
    report = "论断 A 是系统生成的结论 [来源: 999]"  # 越界编号
    findings = [
        ResearchFinding(
            content="无关",
            source="https://example.com/a",
            source_type="web",
        )
    ]
    # 存在性 False 短路，不进 LLM
    citations = v.validate(report, findings)
    assert len(citations) == 1
    assert citations[0].existence is False
    assert citations[0].verified is False
    assert citations[0].verified_relaxed is False


# ---------- Arm 5：喂料裁剪 A′（三条硬约束） ----------

def test_trim_preserves_original_numbering():
    """A′：只喂被引用的 findings，且保留原编号。"""
    findings = [
        ResearchFinding(content="A", source="s1", source_type="web"),
        ResearchFinding(content="B", source="s2", source_type="web"),
        ResearchFinding(content="C", source="s3", source_type="web"),
    ]
    to_check = [{"finding_id": "2"}]
    text, trimmed = Validator._build_findings_text(findings, to_check)
    assert trimmed is True
    assert "[1]" not in text
    assert "[2] 来源: s2" in text
    assert "[3]" not in text


def test_trim_full_index_still_used_for_existence():
    """A′ 硬约束①：存在性校验仍基于全量 index，不因裁剪而恒真。"""
    v = Validator()
    report = "论断 [来源: 999]"  # 越界编号
    findings = [
        ResearchFinding(content="A", source="s1", source_type="web"),
        ResearchFinding(content="B", source="s2", source_type="web"),
    ]
    # 即使 _build_findings_text 只保留有效编号，阶段 1 仍应判 999 不存在
    citations = v.validate(report, findings)
    assert len(citations) == 1
    assert citations[0].existence is False
    assert citations[0].finding_id == "999"


def test_trim_fallback_when_no_usable_finding_id():
    """A′ 安全阀：存在性通过但 finding_id 为空（URL 协议引用）→ 降级全量。"""
    findings = [
        ResearchFinding(content="A", source="http://example.com/a", source_type="web"),
        ResearchFinding(content="B", source="http://example.com/b", source_type="web"),
    ]
    # URL 引用命中 source，existence=True，但 finding_id 为空
    to_check = [{"finding_id": ""}]
    text, trimmed = Validator._build_findings_text(findings, to_check)
    assert trimmed is False
    assert "[1] 来源: http://example.com/a" in text
    assert "[2] 来源: http://example.com/b" in text


def test_trim_url_source_to_id_reverse_lookup():
    """Bug-5 修复验证：URL 格式引用通过 source 反查 finding 编号，裁剪只喂对应 finding。"""
    findings = [
        ResearchFinding(content="A content here", source="http://example.com/a", source_type="web"),
        ResearchFinding(content="B content here", source="http://example.com/b", source_type="web"),
        ResearchFinding(content="C content here", source="http://example.com/c", source_type="web"),
    ]
    # to_check 带 source 字段，finding_id 为空
    to_check = [
        {"finding_id": "", "source": "http://example.com/a"},
        {"finding_id": "", "source": "http://example.com/c"},
    ]
    text, trimmed = Validator._build_findings_text(findings, to_check)
    assert trimmed is True
    assert "[1] 来源: http://example.com/a" in text
    assert "[3] 来源: http://example.com/c" in text
    assert "http://example.com/b" not in text
