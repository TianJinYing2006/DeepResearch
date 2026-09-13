# -*- coding: utf-8 -*-
"""W7 技术债③ 次要指标：「信息不足」标注比例可统计（DoD §8 技术债③ 末条）。

该指标**只看不判**（无达标线），仅作为哨兵：比例塌到极低 = 可能从"承认缺口"转为"编造"；
比例过高 = 检索没喂饱。本文件锁定口径与聚合逻辑，防止后续改 writer 措辞时静默失效。
"""
from __future__ import annotations

from research_engine.eval.metrics import (
    INSUFFICIENT_CITATION,
    INSUFFICIENT_MARKER,
    compute_insufficient,
    split_report_sections,
)
from research_engine.eval.report_gen import _insufficient_summary


def _report(*sections: str) -> str:
    return "\n".join(sections)


def test_split_sections_returns_empty_for_blank_report():
    assert split_report_sections("") == []
    assert split_report_sections("   ") == []


def test_split_sections_falls_back_to_single_section_without_h2():
    parts = split_report_sections("# 标题\n正文内容")
    assert len(parts) == 1


def test_split_sections_splits_on_h2_only():
    parts = split_report_sections("# 总标题\n\n## 一\nA\n\n## 二\nB\n\n### 三\nC")
    assert len(parts) == 2
    assert parts[0].startswith("## 一")
    assert "### 三" in parts[1]


def test_no_marker_gives_zero_ratio():
    state = {"report": "## 一\n有材料\n\n## 二\n也有材料"}
    res = compute_insufficient(state)
    assert res["section_count"] == 2
    assert res["marked_sections"] == 0
    assert res["marker_ratio"] == 0.0


def test_fully_marked_report_has_ratio_one():
    state = {"report": _report(f"## 一\n{INSUFFICIENT_MARKER}：缺材料", f"## 二\n{INSUFFICIENT_MARKER}：缺材料")}
    res = compute_insufficient(state)
    assert res["marked_sections"] == 2
    assert res["marker_ratio"] == 1.0


def test_partial_marking_counts_marked_sections_only():
    state = {
        "report": _report(
            f"## 一\n{INSUFFICIENT_MARKER}：本小节缺乏材料",
            "## 二\n检索到了充分证据",
            "## 三\n同样充分",
            f"## 四\n{INSUFFICIENT_MARKER}：无支撑",
        )
    }
    res = compute_insufficient(state)
    assert res["section_count"] == 4
    assert res["marked_sections"] == 2
    assert res["marker_ratio"] == 0.5


def test_placeholder_is_counted_separately_and_does_not_affect_ratio():
    """引用位兜底替换不是「小节级标注」，必须单独计数。"""
    state = {"report": f"## 一\n结论见{INSUFFICIENT_CITATION}与{INSUFFICIENT_CITATION}\n\n## 二\n正常"}
    res = compute_insufficient(state)
    assert res["placeholder_count"] == 2
    assert res["marked_sections"] == 1  # 「[来源: 信息不足]」内含 marker，仍属该小节被标注
    assert res["marker_ratio"] == 0.5


def test_marker_matches_writer_prompt_wording():
    """锁定措辞：writer.py 的标注纪律与本模块常量必须一致，否则指标静默失效。"""
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "research_engine" / "agents" / "writer.py").read_text(
        encoding="utf-8"
    )
    assert INSUFFICIENT_MARKER in src
    assert "信息不足" in src  # 措辞契约


def test_summary_aggregates_counts_across_reports():
    results = [
        {"metrics": {"insufficient": {"section_count": 4, "marked_sections": 1, "placeholder_count": 0}}},
        {"metrics": {"insufficient": {"section_count": 6, "marked_sections": 3, "placeholder_count": 2}}},
    ]
    res = _insufficient_summary(results)
    assert res["reports"] == 2
    assert res["section_total"] == 10
    assert res["section_marked"] == 4
    assert res["placeholder_total"] == 2
    assert res["marker_ratio"] == 0.4


def test_summary_tolerates_missing_metric_and_zero_sections():
    assert _insufficient_summary([])["marker_ratio"] == 0.0
    assert _insufficient_summary([{"metrics": {}}])["reports"] == 0
    zero = _insufficient_summary(
        [{"metrics": {"insufficient": {"section_count": 0, "marked_sections": 0, "placeholder_count": 0}}}]
    )
    assert zero["marker_ratio"] == 0.0
