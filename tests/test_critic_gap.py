# -*- coding: utf-8 -*-
"""W7 Arm1：critic knowledge_gap 硬规则 + N=6 封顶单测。

离线测试，零 API key（注入 llm_fn 或 monkeypatch LLMClient）。
"""
from __future__ import annotations

from research_engine.critic import MAX_GAP_REFLECTIONS, Critic
from research_engine.state import ResearchState, SubQuestion


def _state(reflection_count: int = 0, frontier: list | None = None) -> ResearchState:
    sq = SubQuestion(id="sq1", question="测试子问题", rationale="r1")
    return ResearchState(
        topic="测试主题",
        subquestions=[sq],
        frontier=frontier if frontier is not None else [{"sq_id": "sq1", "query": "q"}],
        reflection_log=[{"depth": i} for i in range(reflection_count)],
    )


def _verdict(
    sufficient: bool = True,
    needs_replan: bool = False,
    knowledge_gap: str = "",
    next_queries: list | None = None,
) -> dict:
    return {
        "sufficient": sufficient,
        "needs_replan": needs_replan,
        "knowledge_gap": knowledge_gap,
        "next_queries": next_queries or [],
    }


def test_gap_nonempty_with_queries_forces_augment():
    """gap 非空 + next_queries 非空 → augment（回填 frontier，P1 frontier 闭环修复）。"""
    state = _state()
    critic = Critic(llm_fn=lambda s: _verdict(
        sufficient=True, knowledge_gap="缺少实验数据", next_queries=[{"sq_id": "sq1", "query": "找实验"}]
    ))
    signal = critic.decide(state)
    assert signal == "augment"
    assert state.critic_signal == "augment"
    assert state.critic_stop_reason == "gap_continue"
    assert state.critic_gap == "缺少实验数据"
    assert state.next_queries == [{"sq_id": "sq1", "query": "找实验"}]


def test_gap_nonempty_without_queries_stops_gap_unresolved():
    """gap 非空但无 next_queries → stop + gap_unresolved 留痕。"""
    state = _state()
    critic = Critic(llm_fn=lambda s: _verdict(
        sufficient=True, knowledge_gap="缺少实验数据", next_queries=[]
    ))
    signal = critic.decide(state)
    assert signal == "stop"
    assert state.critic_stop_reason == "gap_unresolved"


def test_no_gap_no_queries_stops_no_next_queries():
    """充分但无 gap 也无 queries → stop + no_next_queries。"""
    state = _state()
    critic = Critic(llm_fn=lambda s: _verdict(sufficient=True))
    signal = critic.decide(state)
    assert signal == "stop"
    assert state.critic_stop_reason == "no_next_queries"


def test_sufficient_false_continues():
    """不充分 → continue（由后续 hard_gate/ frontier 兜底）。
    Bug-4 修复后：sufficient=False 且无 gap/queries → lazy_continue（gap 硬规则可达）。"""
    state = _state()
    critic = Critic(llm_fn=lambda s: _verdict(sufficient=False))
    signal = critic.decide(state)
    assert signal == "continue"
    assert state.critic_stop_reason == "lazy_continue"


def test_needs_replan_revise():
    """方向跑偏优先于 gap 规则，走 revise。"""
    state = _state()
    critic = Critic(llm_fn=lambda s: _verdict(
        sufficient=True, needs_replan=True, knowledge_gap="方向错了", next_queries=[]
    ))
    signal = critic.decide(state)
    assert signal == "revise"
    assert state.critic_stop_reason == "revise"


def test_reflection_cap_stops_forcing_continue():
    """已达 N=6 封顶后不再强制 continue，交还 LLM 决策。"""
    state = _state(reflection_count=MAX_GAP_REFLECTIONS)
    critic = Critic(llm_fn=lambda s: _verdict(
        sufficient=True, knowledge_gap="仍缺数据", next_queries=[{"sq_id": "sq1", "query": "继续找"}]
    ))
    signal = critic.decide(state)
    assert signal == "stop"
    assert state.critic_stop_reason == "critic_stop"


def test_hard_gate_stop_reason():
    """硬闸触发时 stop_reason = hard_stop。"""
    state = _state(frontier=[])
    critic = Critic(llm_fn=lambda s: _verdict(sufficient=False))
    signal = critic.decide(state)
    assert signal == "stop"
    assert state.critic_stop_reason == "hard_stop"


def test_prompt_includes_reflection_budget(monkeypatch):
    """user prompt 注入"第 k/6 轮反思"预算感知信息。"""
    captured = {}

    def _chat_json(self, messages, state=None, schema=None):
        captured["messages"] = messages
        return _verdict(sufficient=True)

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)

    state = _state(reflection_count=2)
    Critic(llm_fn=None)._verdict(state)

    user_msg = next(m["content"] for m in captured["messages"] if m["role"] == "user")
    assert "第 3/6 轮反思" in user_msg
