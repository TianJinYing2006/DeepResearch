# -*- coding: utf-8 -*-
"""审计 F01/F02 回归：Critic 消费证据正文 + frontier 空后的多跳闭环。

F01（Critic 只看到发现条数）：
- 证据清单必须包含正文、来源与稳定 ID（E1…），且按子问题组织；
- 相同发现条数、不同内容 → 裁决输入必须不同（旧实现两者完全一致）；
- 裁决引用的证据 ID 经代码复核，清单外 ID 一律过滤。

F02（单子问题无法多跳）：
- frontier 空不再是确定性硬闸，深度/ token 才是；
- 最后一跳后 Critic 仍有机会补充查询（augment → 回填 frontier）；
- 确实无补充查询时以 no_new_queries 语义收敛，不会空转。

全部离线：注入 llm_fn 或 monkeypatch LLMClient，零 API key。
"""
from __future__ import annotations

from research_engine.critic import (
    Critic,
    build_evidence_digest,
    hard_gate,
    normalize_cited_evidence_ids,
    route_critic,
)
from research_engine.state import ResearchFinding, ResearchState, SubQuestion


def _sq(sq_id: str = "sq1", question: str = "问题？") -> SubQuestion:
    return SubQuestion(id=sq_id, question=question, rationale="r")


def _finding(
    content: str,
    source: str = "https://a",
    sq_id: str = "sq1",
    confidence: float = 0.6,
    is_meta: bool = False,
    source_type: str = "web",
) -> ResearchFinding:
    return ResearchFinding(
        content=content, source=source, source_type=source_type,
        confidence=confidence, sq_id=sq_id, is_meta=is_meta,
    )


def _state(
    findings: list | None = None,
    frontier: list | None = None,
    subquestions: list | None = None,
    **kw,
) -> ResearchState:
    subs = subquestions if subquestions is not None else [_sq()]
    return ResearchState(
        topic="主题",
        subquestions=subs,
        frontier=[{"sq_id": "sq1", "query": "q"}] if frontier is None else frontier,
        findings=findings or [],
        **kw,
    )


def _get(final, key):
    """兼容 LangGraph invoke 返回 dict / pydantic state 的取值。"""
    return final.get(key) if isinstance(final, dict) else getattr(final, key)


# ---------------------------------------------------------------- F01：证据清单

def test_digest_contains_content_source_and_stable_ids():
    findings = [
        _finding("事实A", source="https://a"),
        _finding("事实B", source="https://b", confidence=0.8),
    ]
    digest, ids = build_evidence_digest(findings, [_sq()])
    assert ids == ["E1", "E2"], "ID 必须按 findings 位置稳定分配"
    for token in ("事实A", "事实B", "https://a", "https://b", "[E1]", "[E2]"):
        assert token in digest, f"证据清单缺少 {token}"


def test_digest_marks_missing_subquestion_evidence():
    findings = [_finding("只覆盖 sq1", sq_id="sq1")]
    digest, ids = build_evidence_digest(findings, [_sq("sq1"), _sq("sq2", "第二个问题")])
    assert ids == ["E1"]
    assert "[缺口]" in digest, "无证据的子问题必须显式标注缺口"
    assert "sq2" in digest


def test_digest_excludes_meta_findings():
    findings = [_finding("系统自述", is_meta=True), _finding("真实证据")]
    digest, ids = build_evidence_digest(findings, [_sq()])
    assert ids == ["E1"], "is_meta 材料绝不作为证据"
    assert "系统自述" not in digest
    assert "自指/元描述 1 条" in digest


def test_digest_is_bounded_and_prefers_confidence():
    findings = [_finding(f"低置信 {i}", confidence=0.3, source=f"u{i}") for i in range(5)]
    findings.append(_finding("高置信", confidence=0.9, source="high"))
    digest, ids = build_evidence_digest(findings, [_sq()], max_items=1)
    assert len(ids) == 1
    assert "高置信" in digest
    assert "低置信 0" not in digest, "超出上限的低置信材料不应出现"


def test_prompt_consumes_content_same_count(monkeypatch):
    """同样 1 条发现、不同内容 ⇒ 裁决输入必须不同（旧实现只有条数，完全相同）。"""
    captured: dict = {}

    def _chat_json(self, messages, state=None, schema=None):
        captured["user"] = next(m["content"] for m in messages if m["role"] == "user")
        return {"sufficient": True}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)
    critic = Critic(llm_fn=None)

    critic._verdict(_state(findings=[_finding("RAG 的召回率提升 12%")]))
    relevant = captured["user"]
    critic._verdict(_state(findings=[_finding("今天天气晴朗")]))
    irrelevant = captured["user"]

    assert "RAG 的召回率提升 12%" in relevant
    assert "今天天气晴朗" in irrelevant
    assert relevant != irrelevant, "相同条数、不同内容必须产生不同裁决输入"


def test_cited_evidence_ids_filtered_to_digest():
    state = _state(findings=[_finding("证据一")])
    critic = Critic(llm_fn=lambda s: {
        "sufficient": True,
        "cited_evidence_ids": ["e1", "E99", "E1"],
    })
    critic.decide(state)
    assert state.critic_cited_evidence_ids == ["E1"], "清单外/重复 ID 必须被过滤"


def test_cited_ids_empty_without_findings():
    state = _state(findings=[])
    critic = Critic(llm_fn=lambda s: {"sufficient": True, "cited_evidence_ids": ["E1"]})
    critic.decide(state)
    assert state.critic_cited_evidence_ids == []


def test_normalize_cited_ids_pure():
    assert normalize_cited_evidence_ids(["E2", " E1 ", "E2", "E9"], ["E1", "E2"]) == ["E2", "E1"]
    assert normalize_cited_evidence_ids(None, ["E1"]) == []
    assert normalize_cited_evidence_ids("E1", ["E1"]) == []


# ---------------------------------------------------------------- F02：frontier 空不早停

def test_hard_gate_no_longer_stops_on_empty_frontier():
    assert hard_gate(_state(frontier=[])) is None


def test_empty_frontier_insufficient_with_queries_augments():
    state = _state(frontier=[])
    critic = Critic(llm_fn=lambda s: {
        "sufficient": False,
        "next_queries": [{"sq_id": "sq1", "query": "下一跳"}],
    })
    assert critic.decide(state) == "augment"
    assert state.next_queries == [{"sq_id": "sq1", "query": "下一跳"}]


def test_empty_frontier_no_queries_stops_no_new_queries():
    state = _state(frontier=[])
    critic = Critic(llm_fn=lambda s: {"sufficient": False})
    assert critic.decide(state) == "stop"
    assert state.critic_stop_reason == "no_new_queries"


def test_empty_frontier_sufficient_with_gap_queries_augments():
    state = _state(frontier=[])
    critic = Critic(llm_fn=lambda s: {
        "sufficient": True,
        "knowledge_gap": "缺少实验数据",
        "next_queries": [{"sq_id": "sq1", "query": "补实验"}],
    })
    assert critic.decide(state) == "augment"


def test_single_subquestion_multi_hop_end_to_end():
    """单子问题：第一跳消耗唯一种子后 frontier 空，Critic 仍能补充第二跳（旧行为只跳一次）。"""
    from langgraph.graph import END, StateGraph

    calls = {"n": 0}

    def llm(state):
        calls["n"] += 1
        if calls["n"] == 1:
            return {"sufficient": False, "next_queries": [{"sq_id": "sq1", "query": "补充查询"}]}
        return {"sufficient": True}

    critic = Critic(llm_fn=llm)
    seen_depths: list = []

    def fake_research(state: ResearchState):
        frontier = list(state.frontier)
        if frontier:
            frontier.pop(0)
        return {"frontier": frontier, "depth": state.depth + 1}

    def critic_node(state: ResearchState):
        seen_depths.append(state.depth)
        critic.decide(state)
        return {
            "critic_signal": state.critic_signal,
            "sufficient": state.sufficient,
            "next_queries": state.next_queries,
            "reflection_log": [{"depth": state.depth, "signal": state.critic_signal}],
        }

    def fake_revise(state: ResearchState):
        return {"frontier": list(state.frontier) + list(state.next_queries), "next_queries": []}

    g = StateGraph(ResearchState)
    g.add_node("research", fake_research)
    g.add_node("critic", critic_node)
    g.add_node("revise", fake_revise)
    g.set_entry_point("research")
    g.add_edge("research", "critic")
    g.add_conditional_edges(
        "critic", route_critic,
        {"continue": "research", "augment": "revise", "revise": "revise", "stop": END},
    )
    g.add_edge("revise", "research")
    app = g.compile()

    final = app.invoke(_state(frontier=[{"sq_id": "sq1", "query": "种子"}]))
    assert _get(final, "depth") == 2, f"单子问题应完成两跳，实际 {_get(final, 'depth')}"
    assert calls["n"] == 2, "Critic 必须在 frontier 空后仍被调用一次"
    assert seen_depths == [1, 2], f"Critic 应在两次检索后都被调用，实际 {seen_depths}"


def test_empty_frontier_llm_always_augments_terminates_within_caps():
    """frontier 每次被消耗后都为空：augment 循环必须被有界收敛条件终止，不能空转。

    两个兜底：gap 反思轮次封顶（MAX_GAP_REFLECTIONS）与 depth 硬闸，取先到者。
    """
    from langgraph.graph import END, StateGraph

    critic = Critic(llm_fn=lambda s: {
        "sufficient": False,
        "next_queries": [{"sq_id": "sq1", "query": "继续"}],
    })

    def fake_research(state: ResearchState):
        frontier = list(state.frontier)
        if frontier:
            frontier.pop(0)
        return {"frontier": frontier, "depth": state.depth + 1}

    def critic_node(state: ResearchState):
        critic.decide(state)
        return {
            "critic_signal": state.critic_signal,
            "sufficient": state.sufficient,
            "next_queries": state.next_queries,
            "critic_stop_reason": state.critic_stop_reason,
            "reflection_log": [{"depth": state.depth, "signal": state.critic_signal}],
        }

    def fake_revise(state: ResearchState):
        return {"frontier": list(state.frontier) + list(state.next_queries), "next_queries": []}

    g = StateGraph(ResearchState)
    g.add_node("research", fake_research)
    g.add_node("critic", critic_node)
    g.add_node("revise", fake_revise)
    g.set_entry_point("research")
    g.add_edge("research", "critic")
    g.add_conditional_edges(
        "critic", route_critic,
        {"continue": "research", "augment": "revise", "revise": "revise", "stop": END},
    )
    g.add_edge("revise", "research")
    app = g.compile()
    final = app.invoke(
        _state(frontier=[{"sq_id": "sq1", "query": "种子"}]),
        {"recursion_limit": 200},
    )
    assert _get(final, "depth") > 1, "确认真实发生了多跳"
    assert _get(final, "critic_stop_reason") in ("gap_reflection_cap", "hard_stop")
