# -*- coding: utf-8 -*-
"""需求 14 / #73：术语偏离检测（确定性）+ 渲染告警单测。零 API。"""
from __future__ import annotations

from types import SimpleNamespace

from research_engine.render import ReportRenderer
from research_engine.state import ResearchFinding
from research_engine.term_ambiguity import analyze_term_drift, extract_terms


def _state(topic: str, contents: list[str]) -> SimpleNamespace:
    findings = [
        ResearchFinding(content=content, source=f"https://example.com/{i}", source_type="web")
        for i, content in enumerate(contents, 1)
    ]
    return SimpleNamespace(
        topic=topic,
        findings=findings,
        visited_sources=[f.source for f in findings],
        degradation_log=[],
        planner_events=[],
        reflection_log=[],
        depth=1,
        token_used=0,
        replan_count=0,
    )


def test_jev_topic_flags_missing_acronym():
    """JEV↔EVA 实测形态：缩写 0 命中 → 强信号。"""
    report = analyze_term_drift(
        "JEV对企业项目落地的影响",
        ["EVA（经济增加值）是国资委对央企的考核指标。",
         "EVA 在企业的价值管理中广泛应用。"],
    )
    assert report.suspicious is True
    assert report.reason == "acronym_missing"
    assert "JEV" in report.missing_acronyms


def test_control_topic_without_drift():
    report = analyze_term_drift(
        "2026 年 RAG 系统评估方法综述",
        ["RAG 系统评估常用 RAGAS、CRAG 等基准。",
         "RAG 评估指标涵盖 faithfulness 与 answer relevance。"],
    )
    assert report.suspicious is False
    assert report.missing_acronyms == []


def test_low_hit_ratio_flagged():
    report = analyze_term_drift(
        "量子退火在物流调度中的应用",
        ["现有材料讨论的是模拟退火与遗传算法的一般流程。"],
    )
    assert report.suspicious is True
    assert report.reason == "low_hit_ratio"


def test_no_corpus_not_suspicious():
    report = analyze_term_drift("JEV对企业项目落地的影响", [])
    assert report.suspicious is False
    assert report.reason == "no_corpus"


def test_extract_terms_filters_stopwords_and_builds_cjk_bigrams():
    terms = extract_terms("The impact of RAG on 企业知识管理")
    lowered = [t.lower() for t in terms]
    assert "rag" in lowered
    assert "the" not in lowered
    assert "企业" in terms and "知识" in terms


def test_render_prepends_drift_warning():
    state = _state("JEV对企业项目落地的影响",
                   ["EVA（经济增加值）是国资委对央企的考核指标。"])
    display = ReportRenderer().render("# 报告\n正文[来源: 1]", [], state.findings, state)
    assert "术语偏离警示" in display
    assert display.startswith("\n\n> ⚠️ 术语偏离警示")


def test_render_no_warning_for_matching_corpus():
    state = _state("2026 年 RAG 评估方法",
                   ["RAG 评估常用 RAGAS 与 faithfulness 指标。"])
    display = ReportRenderer().render("# 报告\n正文[来源: 1]", [], state.findings, state)
    assert "术语偏离警示" not in display
