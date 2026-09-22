"""子问题预算：动态每子问题跳数上限 + Planner 输出收口（截断 / 规范化）。

背景（为什么这两件事放在一个文件里测）
--------------------------------------
``per_subq_hop_cap=5`` 这个常量隐含假设「一定 4 个子问题」（5 = 20 ÷ 4），而子问题数
是 LLM 定的软约束、会偏离 ⇒ 预算分配错配：

* 8 个子问题 → 前 4 个各吃满 5 跳、后 4 个 **0 跳**（静默饿死）；
* 1 个子问题 → 只用 5 跳、**浪费 15 跳**，停止原因却显示「无待检索查询」，
  看起来像搜不到东西。

修法是让 cap 随实际子问题数浮动。同时 Planner 的输出要收口（截断 + 规范化），
否则「子问题数」这个 cap 的输入本身就是脏的。

所有测试零 LLM、零网络。
"""
from __future__ import annotations

import pytest

from config import config
from research_engine.agents.planner import Planner, build_planner_system, build_replan_system
from research_engine.failure_reasons import FailureReason
from research_engine.graph import effective_per_subq_hop_cap
from research_engine.render import ReportRenderer
from research_engine.state import ResearchState, SubQuestion


def _state_with_subquestions(n: int) -> ResearchState:
    subs = [SubQuestion(id=f"q{i + 1}", question=f"子问题 {i + 1}", rationale="")
            for i in range(n)]
    return ResearchState(
        topic="t",
        subquestions=subs,
        frontier=[{"sq_id": s.id, "query": s.question} for s in subs],
        per_subq_hop={s.id: 0 for s in subs},
    )


# ---------------------------------------------------------------- 1) cap 数值表


@pytest.mark.parametrize(
    ("subq_count", "expected"),
    [
        (1, 20),   # ceil(20/1)
        (3, 7),    # ceil(20/3) —— floor 会给 6，导致 18 跳就停
        (4, 5),    # 默认配置，必须与 W1 常量逐跳等价
        (6, 4),    # ceil(20/6)
        (8, 3),    # ceil(20/8) —— floor 会给 2，导致 16 跳就停
    ],
)
def test_effective_cap_table(subq_count, expected):
    """用 ceil 而非 floor：floor 会让「cap × 子问题数」小于总预算 ⇒ 提前停。"""
    state = _state_with_subquestions(subq_count)
    assert effective_per_subq_hop_cap(state, config.research) == expected


def test_default_four_subquestions_keep_cap_five():
    """默认 4 子问题 → cap 必须仍是 5（W1 Q5=A 防饿死语义，基线不得漂移）。"""
    state = _state_with_subquestions(4)
    assert effective_per_subq_hop_cap(state, config.research) == config.research.per_subq_hop_cap


def test_cap_falls_back_to_static_when_no_subquestions():
    """子问题数不可得（未规划 / 异常）→ 退回静态兜底，不改旧行为。"""
    state = ResearchState(topic="t")
    assert effective_per_subq_hop_cap(state, config.research) == config.research.per_subq_hop_cap


# ---------------------------------------------------------------- 2) 实际行为


def _fake_graph_with_cap(n_subq: int) -> int:
    """用最小 fake 图跑真实 cap 逻辑，返回最终 depth（零 LLM）。

    research 节点模拟「critic 源源不断回填新查询」—— 这是最能暴露 cap 缺陷的压力
    场景：只要 cap 没掐断，frontier 永远不空，于是**最终 depth 完全由 cap 决定**。
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    from research_engine.critic import hard_gate

    def fake_research(state: ResearchState):
        cap = effective_per_subq_hop_cap(state, config.research)
        frontier = list(state.frontier)
        per = dict(state.per_subq_hop)
        head = None
        while frontier:
            cand = frontier.pop(0)
            sid = cand.get("sq_id", "")
            if per.get(sid, 0) >= cap:
                continue
            head = cand
            break
        if head is None:
            # 所有子问题都达 cap ⇒ 队列实质性空（真实实现里会带 cap 数值的 progress）
            return {"frontier": [], "critic_signal": "continue"}
        sid = head.get("sq_id", "")
        per[sid] = per.get(sid, 0) + 1
        if per[sid] < cap:
            frontier = frontier + [{"sq_id": sid, "query": f"q{state.depth}"}]
        return {"frontier": frontier, "depth": state.depth + 1,
                "per_subq_hop": per, "critic_signal": "continue"}

    def fake_critic(state: ResearchState):
        return {"critic_signal": "stop" if hard_gate(state, config) == "stop" else "continue"}

    g = StateGraph(ResearchState)
    g.add_node("research", fake_research)
    g.add_node("critic", fake_critic)
    g.set_entry_point("research")
    g.add_edge("research", "critic")
    g.add_conditional_edges("critic", lambda s: s.critic_signal,
                            {"continue": "research", "stop": END})
    app = g.compile(checkpointer=MemorySaver())

    init = _state_with_subquestions(n_subq)
    res = app.invoke(init, {"configurable": {"thread_id": "cap-behaviour"},
                            "recursion_limit": 200})
    final = res if isinstance(res, dict) else res
    return final.get("depth") if isinstance(final, dict) else final.depth


def test_eight_subquestions_do_not_stop_at_sixteen_hops():
    """8 个子问题必须用满 20 跳，而不是 floor 方案的 16 跳。

    cap=3 的含义是「任一子问题最多 3 跳」，不是「一定会跑 24 跳」—— 真正的边界
    是全局 max_total_hops 硬闸。
    """
    depth = _fake_graph_with_cap(8)
    assert depth == 20, f"8 子问题应在全局硬闸处停（20 跳），实际 {depth}（floor 会停在 16）"


def test_single_subquestion_uses_full_budget():
    """1 个子问题不得只用 5 跳 —— 旧常量会浪费 15 跳。"""
    depth = _fake_graph_with_cap(1)
    assert depth == 20, f"单子问题应占满预算，实际 {depth}"


def test_eight_subquestions_are_all_retrieved_no_starvation():
    """8 个子问题场景下每个子问题都至少被检索一次（旧的静态 cap=5 会让后 4 个 0 跳）。"""
    from research_engine.agents.researcher import Researcher
    from research_engine.graph import DeepResearchGraph
    from research_engine.state import DegradationSink

    graph = DeepResearchGraph.__new__(DeepResearchGraph)  # 绕过 __init__（避免建 provider / 连库）
    researcher = Researcher.__new__(Researcher)
    researcher.degradations = DegradationSink()
    researcher.search_once = lambda query, state: ([], {})
    graph.researcher = researcher

    state = _state_with_subquestions(8)
    # 每个子问题给足 5 条查询，让 cap 成为唯一约束
    state = state.model_copy(update={
        "frontier": [{"sq_id": s.id, "query": f"{s.question} #{i}"}
                     for s in state.subquestions for i in range(5)],
    })

    for _ in range(60):
        delta = graph._research(state)
        if not delta.get("frontier"):
            break
        state = state.model_copy(update=delta)

    assert set(state.per_subq_hop.values()) == {3}, (
        f"每个子问题应恰好 3 跳（cap=ceil(20/8)），实际 {state.per_subq_hop}")
    assert len(state.per_subq_hop) == 8, "不允许有子问题从未被检索（饿死）"


# ---------------------------------------------------------------- 3) Planner 收口


class _FakeRouter:
    def __init__(self, payload):
        self.payload = payload

    def strategic_json(self, system, user, state=None):
        return self.payload


def _patch_router(monkeypatch, payload):
    import research_engine.agents.planner as planner_mod

    monkeypatch.setattr(planner_mod, "get_router", lambda: _FakeRouter(payload))


def _subquestions_payload(items):
    return {"subquestions": items}


def test_plan_truncates_and_records_planner_event(monkeypatch):
    monkeypatch.setattr(config.research, "max_subquestions", 4)
    _patch_router(monkeypatch, _subquestions_payload(
        [{"id": f"q{i + 1}", "question": f"问题 {i + 1}", "rationale": "r"} for i in range(6)]))

    planner = Planner()
    result = planner.plan("主题")

    assert len(result) == 4
    assert planner.drain_degradations() == []
    events = planner.drain_planner_events()
    assert len(events) == 1
    assert events[0]["event"] == "subquestions_truncated"
    assert events[0]["phase"] == "plan"
    assert events[0]["returned"] == 6
    assert events[0]["accepted"] == 4
    assert events[0]["dropped"] == 2
    assert events[0]["limit"] == 4


def test_replan_uses_same_subquestion_bound_and_event(monkeypatch):
    """replan 必须走同一收口 —— 否则只修 plan() 会漏掉这条路。"""
    monkeypatch.setattr(config.research, "max_subquestions", 2)
    _patch_router(monkeypatch, _subquestions_payload(
        [{"id": f"q{i + 1}", "question": f"问题 {i + 1}"} for i in range(5)]))

    planner = Planner()
    old = [SubQuestion(id="q1", question="旧问题", rationale="")]
    result = planner.replan("主题", old, [], "跑偏")

    assert len(result) == 2
    assert planner.drain_degradations() == []
    events = planner.drain_planner_events()
    assert len(events) == 1
    assert events[0]["event"] == "subquestions_truncated"
    assert events[0]["phase"] == "replan"
    assert events[0]["returned"] == 5
    assert events[0]["accepted"] == 2
    assert events[0]["dropped"] == 3


def test_no_degradation_when_within_limit(monkeypatch):
    monkeypatch.setattr(config.research, "max_subquestions", 4)
    _patch_router(monkeypatch, _subquestions_payload(
        [{"id": "q1", "question": "问题 1"}, {"id": "q2", "question": "问题 2"}]))

    planner = Planner()
    assert len(planner.plan("主题")) == 2
    assert planner.drain_degradations() == []


def test_duplicate_subquestion_ids_are_rewritten(monkeypatch):
    """重复 ID 会让两个子问题共享 per_subq_hop 配额 ⇒ 错误限流。"""
    _patch_router(monkeypatch, _subquestions_payload([
        {"id": "q1", "question": "问题 1"},
        {"id": "q1", "question": "问题 2"},
        {"id": "q1", "question": "问题 3"},
    ]))

    planner = Planner()
    result = planner.plan("主题")
    ids = [s.id for s in result]
    assert len(set(ids)) == len(ids), f"ID 仍重复：{ids}"
    events = planner.drain_planner_events()
    assert events == [{
        "event": "duplicate_id_rewritten",
        "phase": "plan",
        "original_id": "q1",
        "rewritten_id": "q2",
    }, {
        "event": "duplicate_id_rewritten",
        "phase": "plan",
        "original_id": "q1",
        "rewritten_id": "q3",
    }]


def test_empty_questions_fall_back_to_topic_only(monkeypatch):
    """过滤空 question 后一个不剩 ⇒ 必须退化成「主题即子问题」并留痕。

    否则 frontier 空 → critic 立刻 stop → 报告空跑，而且看不出原因。
    """
    _patch_router(monkeypatch, _subquestions_payload([
        {"id": "q1", "question": "   "},
        {"id": "q2", "question": ""},
    ]))

    planner = Planner()
    result = planner.plan("主题")

    assert len(result) == 1
    assert result[0].question == "主题"
    logs = planner.drain_degradations()
    assert logs and logs[0].reason == FailureReason.LLM_ERROR.value
    assert logs[0].fallback_action == "topic_only"
    assert planner.drain_planner_events() == [{
        "event": "empty_question_dropped",
        "phase": "plan",
        "count": 2,
    }]


def test_replan_llm_failure_records_degradation(monkeypatch):
    """replan 的 LLM 真故障必须留痕，不能静默沿用旧子问题。"""
    import research_engine.agents.planner as planner_mod

    class _FailingRouter:
        def strategic_json(self, system, user, state=None):
            raise RuntimeError("replan boom")

    monkeypatch.setattr(planner_mod, "get_router", lambda: _FailingRouter())

    planner = Planner()
    old = [SubQuestion(id="q1", question="旧问题", rationale="")]
    result = planner.replan("主题", old, [], "跑偏")

    assert result == old
    logs = planner.drain_degradations()
    assert len(logs) == 1
    assert logs[0].reason == FailureReason.LLM_ERROR.value
    assert logs[0].fallback_action == "keep_previous_subquestions"
    assert "phase=replan" in logs[0].detail


def test_replan_empty_parse_records_degradation(monkeypatch):
    """replan 解析后为空也必须留痕，不能静默沿用旧子问题。"""
    _patch_router(monkeypatch, _subquestions_payload([
        {"id": "q1", "question": "   "},
    ]))

    planner = Planner()
    old = [SubQuestion(id="q1", question="旧问题", rationale="")]
    result = planner.replan("主题", old, [], "跑偏")

    assert result == old
    logs = planner.drain_degradations()
    assert len(logs) == 1
    assert logs[0].reason == FailureReason.LLM_ERROR.value
    assert logs[0].fallback_action == "keep_previous_subquestions"
    assert "phase=replan" in logs[0].detail


def test_graph_plan_drains_planner_events_into_state_delta(monkeypatch):
    """graph 节点必须把事件送进 add reducer，而不是只留在 Planner 实例上。"""
    from research_engine.graph import DeepResearchGraph

    monkeypatch.setattr(config.research, "max_subquestions", 1)
    _patch_router(monkeypatch, _subquestions_payload([
        {"id": "q1", "question": "问题 1"},
        {"id": "q2", "question": "问题 2"},
    ]))

    graph = DeepResearchGraph.__new__(DeepResearchGraph)
    graph.planner = Planner()
    delta = graph._plan(ResearchState(topic="主题"))

    assert delta["degradation_log"] == []
    assert delta["planner_events"] == [{
        "event": "subquestions_truncated",
        "phase": "plan",
        "returned": 2,
        "accepted": 1,
        "dropped": 1,
        "limit": 1,
    }]
    assert graph.planner.drain_planner_events() == []


def test_planner_events_are_audit_only_and_rendered(monkeypatch):
    """策略事件进入独立通道：不写 degradation，也不把 run_status 变 degraded。"""
    monkeypatch.setattr(config.research, "max_subquestions", 1)
    _patch_router(monkeypatch, _subquestions_payload([
        {"id": "q1", "question": "问题 1"},
        {"id": "q2", "question": "问题 2"},
    ]))

    planner = Planner()
    assert len(planner.plan("主题")) == 1
    assert planner.drain_degradations() == []
    events = planner.drain_planner_events()
    state = ResearchState(topic="主题", planner_events=events)

    assert state.resolve_run_status(has_report=True) == "success"
    rendered = ReportRenderer().render("正文", [], [], state)
    assert "规划治理" in rendered
    assert "subquestions_truncated" in rendered
    assert "不计入运行降级" in rendered


def test_prompt_requires_priority_order():
    """prompt 必须说明「按重要性降序 + 尾部会被丢弃」，否则截断砍谁全凭运气。"""
    assert "重要性降序" in build_planner_system()
    assert "尾部" in build_planner_system()
    assert "尾部" in build_replan_system()


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
