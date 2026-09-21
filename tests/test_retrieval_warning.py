"""检索链路健康告警（ReportRenderer.build_retrieval_warning）单测。

背景：搜索源全线故障（API 额度耗尽 / 向量库未启动）时，各节点各自优雅降级为零
结果，流程仍会跑完并产出一份**看起来正常**的报告。本告警把这件事摆到报告顶部，
避免无依据生成的正文被当成有检索支撑的结论。

锁定三条判定边界（保守，只在确凿时告警）：
1. 有检索故障 + 零 finding ⇒ 告警；
2. 有 finding ⇒ 不告警（确有检索产出）；
3. 零 finding 但故障不是检索类（如纯 LLM 降级）⇒ 不告警（不是检索的问题）。
"""
from __future__ import annotations

import pytest

from research_engine.render import ReportRenderer, _deg_get
from research_engine.state import ResearchFinding, ResearchState


def _state_with_faults(*, components_reasons, findings=None) -> ResearchState:
    st = ResearchState(topic="测试话题", findings=findings or [])
    for comp, reason in components_reasons:
        st.add_degradation(node="researcher", component=comp, reason=reason,
                           detail="detail", fallback_action="empty_list")
    return st


# ---- 1) 该告警：有检索故障 + 零 finding ----

def test_warns_when_search_faults_and_no_findings():
    st = _state_with_faults(components_reasons=[
        ("web_search", "provider_error"),
        ("rag_search:vector", "not_configured"),
        ("rag_search:bm25", "not_configured"),
    ])
    out = ReportRenderer().build_retrieval_warning(st)
    assert "未取得任何真实检索结果" in out
    assert "请勿作为事实引用" in out
    # 组件与原因都要出现在告警里，否则用户无法自查
    assert "web_search" in out
    assert "rag_search:vector" in out
    assert "provider_error" in out
    assert "not_configured" in out


def test_warns_for_rag_only_faults():
    """只有本地检索挂了（网络搜索正常但因故零结果）同样要告警。"""
    st = _state_with_faults(components_reasons=[("rag_search", "not_configured")])
    assert "未取得任何真实检索结果" in ReportRenderer().build_retrieval_warning(st)


# ---- 2) 不该告警：确有检索产出 ----

def test_no_warning_when_findings_exist():
    st = _state_with_faults(
        components_reasons=[("web_search", "provider_error")],
        findings=[ResearchFinding(content="有内容", source="https://x",
                                  source_type="web", confidence=0.8)],
    )
    assert ReportRenderer().build_retrieval_warning(st) == ""


# ---- 3) 不该告警：故障不是检索类 ----

def test_no_warning_when_only_llm_fault():
    """纯 LLM 降级（如 writer 走兜底报告）不该触发「检索」告警。"""
    st = _state_with_faults(components_reasons=[("llm", "llm_error")])
    assert ReportRenderer().build_retrieval_warning(st) == ""


def test_no_warning_when_no_degradation_at_all():
    assert ReportRenderer().build_retrieval_warning(ResearchState(topic="t")) == ""


# ---- 4) dual-read：DegradationEntry 与 dict 两种形态都要读得动 ----

def test_deg_get_reads_both_object_and_dict():
    st = ResearchState(topic="t")
    entry = st.add_degradation(node="researcher", component="web_search",
                               reason="provider_error")
    assert _deg_get(entry, "reason") == "provider_error"
    assert _deg_get({"reason": "timeout"}, "reason") == "timeout"
    assert _deg_get({}, "reason", default="none") == "none"


def test_warning_works_after_dict_serialization():
    """LangGraph 序列化后 degradation_log 元素变 dict，告警仍须生效。"""
    st = ResearchState(topic="t")
    st.add_degradation(node="researcher", component="web_search", reason="provider_error")
    st.degradation_log = [e.to_dict() if hasattr(e, "to_dict") else e
                          for e in st.degradation_log]
    assert "未取得任何真实检索结果" in ReportRenderer().build_retrieval_warning(st)


# ---- 5) 置顶：告警必须在正文之前，否则会被淹没 ----

def test_warning_is_prepended_to_report():
    st = _state_with_faults(components_reasons=[("web_search", "provider_error")])
    rendered = ReportRenderer().render("正文内容", [], [], st)
    assert rendered.index("未取得任何真实检索结果") < rendered.index("正文内容")


# ---- 6) 契约：不污染 run_status / degradation_log ----

def test_warning_does_not_touch_run_status_or_log():
    """告警只改展示文本，不得新增 run_status 第四态、不得追加降级条目。"""
    st = _state_with_faults(components_reasons=[("web_search", "provider_error")])
    before = len(st.degradation_log)
    status_before = st.run_status
    ReportRenderer().build_retrieval_warning(st)
    assert len(st.degradation_log) == before
    assert st.run_status == status_before


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
