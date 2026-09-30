# -*- coding: utf-8 -*-
"""需求 16 / bug #75：critic next_queries sq_id 归一化 + 未知 ID 渲染防御。

离线测试，零 API key（注入 llm_fn）。
"""
from __future__ import annotations

from research_engine.context import manager as manager_mod
from research_engine.context.manager import ContextManager
from research_engine.critic import CRITIC_EVENT_SQID_REWRITTEN, Critic
from research_engine.state import ResearchFinding, ResearchState, SubQuestion


def _state(sub_ids: list[str]) -> ResearchState:
    subs = [SubQuestion(id=i, question=f"问题{i}", rationale="r") for i in sub_ids]
    return ResearchState(
        topic="测试主题",
        subquestions=subs,
        frontier=[{"sq_id": sub_ids[0], "query": "种子查询"}],
    )


def _verdict(next_queries: list) -> dict:
    return {
        "sufficient": False,
        "needs_replan": False,
        "knowledge_gap": "存在知识缺口",
        "next_queries": next_queries,
    }


def test_prefix_normalization_and_audit_events():
    """q2.1/q2a → q2；完全未知 → 置空；query 不丢弃；逐条留痕。"""
    state = _state(["q1", "q2"])
    critic = Critic(llm_fn=lambda s: _verdict([
        {"sq_id": "q2.1", "query": "a"},
        {"sq_id": "q2a", "query": "b"},
        {"sq_id": "q1", "query": "c"},
        {"sq_id": "q9", "query": "d"},
    ]))
    assert critic.decide(state) == "augment"
    assert [q["sq_id"] for q in state.next_queries] == ["q2", "q2", "q1", ""]
    assert [q["query"] for q in state.next_queries] == ["a", "b", "c", "d"]

    events = critic.drain_events()
    assert [e["original_id"] for e in events] == ["q2.1", "q2a", "q9"]
    assert [e["rewritten_id"] for e in events] == ["q2", "q2", ""]
    assert all(e["event"] == CRITIC_EVENT_SQID_REWRITTEN for e in events)
    assert critic.drain_events() == []  # drain 后清空


def test_longest_prefix_wins():
    """前缀冲突时取最长匹配：q12a → q12（而不是 q1）。"""
    state = _state(["q1", "q12"])
    critic = Critic(llm_fn=lambda s: _verdict([
        {"sq_id": "q12a", "query": "a"},
        {"sq_id": "q1b", "query": "b"},
    ]))
    critic.decide(state)
    assert [q["sq_id"] for q in state.next_queries] == ["q12", "q1"]


def test_clean_queries_leave_no_events():
    """合法 sq_id 原样保留、不产生治理事件。"""
    state = _state(["q1", "q2"])
    critic = Critic(llm_fn=lambda s: _verdict([{"sq_id": "q1", "query": "a"}]))
    critic.decide(state)
    assert state.next_queries == [{"sq_id": "q1", "query": "a"}]
    assert critic.drain_events() == []


def test_unknown_sqid_not_rendered_as_subquestion(monkeypatch):
    """L3 渲染防御：未知 sq_id 不得冒充「子问题」标题，连续未知合并为一组。"""
    monkeypatch.setattr(
        manager_mod.config.experiment, "writer_sectioned_feed_enabled", True)
    mgr = ContextManager()
    findings = [
        ResearchFinding(content="c1", source="s1", source_type="web", sq_id="q1"),
        ResearchFinding(content="c2", source="s2", source_type="web", sq_id="q2.1"),
        ResearchFinding(content="c3", source="s3", source_type="web", sq_id="q2a"),
    ]
    subs = [
        SubQuestion(id="q1", question="问题一", rationale="r"),
        SubQuestion(id="q2", question="问题二", rationale="r"),
    ]
    text = mgr.format_for_writer(findings, subs)
    assert "q2.1" not in text and "q2a" not in text
    assert text.count("未分类材料") == 1
    assert "--- 子问题：问题一 ---" in text
