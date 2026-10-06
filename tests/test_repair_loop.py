# -*- coding: utf-8 -*-
"""审计 F11（3b）回归：有界返工闭环（校验失败 → 确定性移除 → 二次校验 → 渲染）。

全部离线：纯函数 + 最小 LangGraph 拓扑（无 LLM / 无网络）。
"""
from __future__ import annotations

from research_engine.agents.validator import Validator
from research_engine.graph import MAX_REPAIR_PASSES, DeepResearchGraph, route_repair
from research_engine.repair import REPAIR_REMOVED_NOTE, plan_repairs, repair_report
from research_engine.state import Citation, ResearchState


def _citation(claim: str, start: int, end: int, verified: bool,
              source: str = "https://a") -> Citation:
    return Citation(claim=claim, source=source, verified=verified, existence=True,
                    claim_start=start, claim_end=end)


# ---------------------------------------------------------------- 纯函数

def test_plan_repairs_requires_all_refs_failed_on_span():
    """多编号引用共享区间：只要一条通过，整句保留；独立句全失败才删除。"""
    shared = [_citation("共享句", 10, 20, True),
              _citation("共享句", 10, 20, False, source="b")]
    failed = [_citation("坏句", 30, 40, False)]
    assert plan_repairs(shared + failed) == [(30, 40)]


def test_repair_report_replaces_failed_sentences():
    report = "好论断足够长可以保留。坏论断未能通过校验需要移除 [来源: 2]。"
    bad_start = report.index("坏论断")
    bad_end = report.index("[来源: 2]") + len("[来源: 2]")
    citations = [
        _citation("好论断足够长可以保留", 0, report.index("。"), True),
        _citation("坏论断未能通过校验需要移除", bad_start, bad_end, False),
    ]

    repaired, stats = repair_report(report, citations)

    assert "坏论断" not in repaired
    assert "好论断足够长可以保留" in repaired
    assert REPAIR_REMOVED_NOTE in repaired
    assert stats["removed_claims"] == 1
    assert stats["failed_before"] == 1


def test_repair_report_skips_missing_spans():
    """旧快照/手工 Citation 无定位（claim_start=-1）→ 不改动文本。"""
    report = "旧引用没有定位信息 [来源: 1]。"
    repaired, stats = repair_report(report, [_citation("旧引用", -1, -1, False)])
    assert repaired == report and stats["removed_claims"] == 0


def test_route_repair_caps_and_conditions():
    failing = ResearchState(topic="t", report="r", citations=[_citation("坏", 0, 2, False)])
    assert route_repair(failing) == "repair"

    failing.repair_count = MAX_REPAIR_PASSES
    assert route_repair(failing) == "render", "返工封顶后必须收敛"

    ok = ResearchState(topic="t", report="r", citations=[_citation("好", 0, 2, True)])
    assert route_repair(ok) == "render"
    assert route_repair(ResearchState(topic="t", report="", citations=[])) == "render"


def test_extract_citations_records_spans():
    report = "结论甲足够长可以进入校验 [来源: 1]。"
    citations = Validator()._extract_citations(report)
    assert len(citations) == 1
    record = citations[0]
    assert 0 <= record["claim_start"] < record["claim_end"] <= len(report)
    assert "[来源: 1]" in report[record["claim_start"]:record["claim_end"]]


# ---------------------------------------------------------------- 端到端（最小图）

def test_repair_loop_end_to_end():
    """失败 → repair → 二次校验 → render；修复后报告不含失败论断、随后收敛。"""
    from langgraph.graph import END, StateGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)  # 只为取 _repair 绑定方法
    calls = {"validate": 0}

    report_v1 = "保留的好论断足够长可以校验 [来源: 1]。坏论断能够定位但未通过 [来源: 2]。"
    bad_start = report_v1.index("坏论断")
    bad_end = report_v1.index("[来源: 2]") + len("[来源: 2]")

    def validate_node(state: ResearchState):
        calls["validate"] += 1
        if calls["validate"] == 1:
            return {
                "citations": [
                    _citation("保留的好论断足够长可以校验", 0, report_v1.index("。"),
                              True, source="s1"),
                    _citation("坏论断能够定位但未通过", bad_start, bad_end,
                              False, source="s2"),
                ],
                "validator_stats": {"pass": 1}, "status": "done", "run_status": "success",
            }
        assert "坏论断" not in state.report, "返工后二次校验时失败论断应已移除"
        return {
            "citations": [_citation("保留的好论断足够长可以校验", 0, 18, True, source="s1")],
            "validator_stats": {"pass": 1}, "status": "done", "run_status": "success",
        }

    def render_node(state: ResearchState):
        return {"report_display": state.report}

    g = StateGraph(ResearchState)
    g.add_node("validate", validate_node)
    g.add_node("repair", graph._repair)
    g.add_node("render", render_node)
    g.set_entry_point("validate")
    g.add_conditional_edges("validate", route_repair,
                            {"repair": "repair", "render": "render"})
    g.add_edge("repair", "validate")
    g.add_edge("render", END)
    app = g.compile()

    final = app.invoke(ResearchState(topic="t", report=report_v1))

    assert calls["validate"] == 2, "返工后必须重走一次校验"
    assert "坏论断" not in final["report"]
    assert final["repair_count"] == 1
    assert final["repair_log"][0]["removed_claims"] == 1
    assert final["repair_log"][0]["failed_before"] == 1
