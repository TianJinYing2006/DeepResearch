# -*- coding: utf-8 -*-
"""W7 Arm4：Writer 分节喂料 G1~G5 单测。

离线测试，不调用真实 LLM（monkeypatch 掉 smart_chat）。
"""
from __future__ import annotations

from research_engine.agents.writer import Writer
from research_engine.context.manager import ContextManager
from research_engine.state import ResearchFinding, SubQuestion

TOPIC = "测试主题"


def _subquestions() -> list[SubQuestion]:
    return [
        SubQuestion(id="sq1", question="子问题一", rationale="r1"),
        SubQuestion(id="sq2", question="子问题二", rationale="r2"),
        SubQuestion(id="sq3", question="子问题三", rationale="r3"),
    ]


def test_format_for_writer_groups_by_sq_id_and_preserves_index():
    """G2：按子问题分节，且全局编号与 findings 列表顺序一致。"""
    sqs = _subquestions()
    findings = [
        ResearchFinding(content="A", source="s1", source_type="web", sq_id="sq1"),
        ResearchFinding(content="B", source="s2", source_type="web", sq_id="sq2"),
        ResearchFinding(content="C", source="s3", source_type="web", sq_id="sq1"),
    ]
    text = ContextManager().format_for_writer(findings, sqs)

    # 顺序：A(1) -> 分节 -> B(2) -> 分节 -> C(3)
    assert "--- 子问题：子问题一 ---" in text
    assert "--- 子问题：子问题二 ---" in text
    # P0 引用协议统一：findings 编号格式为 "Finding N:"
    assert "Finding 1: 来源: s1" in text
    assert "Finding 2: 来源: s2" in text
    assert "Finding 3: 来源: s3" in text
    # B 在 C 之前 → 编号 2 必须在 3 之前
    assert text.index("Finding 2:") < text.index("Finding 3:")


def test_format_for_writer_lists_empty_subquestions():
    """G4：无材料的子问题被显式列出，强制 writer 看到信息不足义务。"""
    sqs = _subquestions()
    findings = [ResearchFinding(content="A", source="s1", source_type="web", sq_id="sq1")]
    text = ContextManager().format_for_writer(findings, sqs)

    assert "--- 以下子问题暂无研究发现 ---" in text
    assert "--- 子问题：子问题二 ---" in text
    assert "--- 子问题：子问题三 ---" in text


def test_format_for_writer_no_findings_all_empty():
    sqs = _subquestions()
    text = ContextManager().format_for_writer([], sqs)
    for s in sqs:
        assert f"--- 子问题：{s.question} ---" in text
        assert "（暂无研究发现）" in text


def test_compress_preserves_sq_id(monkeypatch):
    """G1：压缩后 ResearchFinding 仍携带 sq_id。"""
    findings = [
        ResearchFinding(content="x", source="same", source_type="web", sq_id="sq1"),
        ResearchFinding(content="y", source="same", source_type="web", sq_id="sq1"),
    ]
    monkeypatch.setattr(
        "research_engine.llm.router.LLMRouter.fast_chat",
        lambda self, system, user, state=None: "compressed",
    )
    compressed = ContextManager(max_findings=1).compress(findings, TOPIC)
    assert len(compressed) == 1
    assert compressed[0].sq_id == "sq1"


def test_ensure_sections_replaces_out_of_range_citations():
    """G4：引用编号越界时强制替换为 [来源: 信息不足]。"""
    findings = [
        ResearchFinding(content="A", source="s1", source_type="web"),
        ResearchFinding(content="B", source="s2", source_type="web"),
    ]
    report = "论断甲 [来源: 1]；论断乙 [来源: 5]；论断丙 [来源: 2, 99]"
    fixed = Writer()._ensure_sections(report, [], findings)
    assert "[来源: 1]" in fixed
    assert "[来源: 5]" not in fixed
    assert "[来源: 信息不足]" in fixed
    assert "[来源: 2, 99]" not in fixed


def test_ensure_sections_appends_missing_subquestions():
    """G4：writer 漏写子问题时系统强制补信息不足小节。"""
    sqs = _subquestions()
    report = "# 报告\n\n## 子问题一\n内容。"
    fixed = Writer()._ensure_sections(report, sqs, [])
    assert "## 子问题二" in fixed
    assert "## 子问题三" in fixed
    assert "信息不足" in fixed


def test_fallback_report_on_llm_failure(monkeypatch):
    """G4：smart_chat 抛异常时系统兜底输出全信息不足。"""
    sqs = _subquestions()

    def _boom(*args, **kwargs):
        raise RuntimeError("LLM unavailable")

    monkeypatch.setattr("research_engine.llm.router.LLMRouter.smart_chat", _boom)
    report = Writer().write(TOPIC, sqs, [])
    for s in sqs:
        assert f"## {s.question}" in report
    assert report.count("信息不足") == len(sqs)


def test_write_receives_sectioned_context(monkeypatch):
    """端到端：write 调用时 context 按子问题分节喂入。"""
    sqs = _subquestions()
    findings = [
        ResearchFinding(content="A", source="s1", source_type="web", sq_id="sq1"),
        ResearchFinding(content="B", source="s2", source_type="web", sq_id="sq2"),
    ]
    captured = {}

    def _smart_chat(self, system, user, state=None):
        captured["user"] = user
        return "# 报告\n\n## 子问题一\n甲 [来源: 1]。\n\n## 子问题二\n乙 [来源: 2]。"

    monkeypatch.setattr("research_engine.llm.router.LLMRouter.smart_chat", _smart_chat)
    Writer().write(TOPIC, sqs, findings)

    assert "--- 子问题：子问题一 ---" in captured["user"]
    assert "--- 子问题：子问题二 ---" in captured["user"]
    # 子问题三无材料，也被列出
    assert "--- 子问题：子问题三 ---" in captured["user"]
