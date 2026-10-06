# -*- coding: utf-8 -*-
"""审计 F09/F10 回归：跨工具融合多样性 + 证据身份去重与新证据率。

- F09：``pool_and_trim`` 轮转保留来源多样性（web/rag 满额时 arXiv 不再整源饿死）；
  ``stats`` 既有键=原始产出、``adopted``=实际采用（快照消息用 adopted/raw 双计数）；
- F10：``dedupe_new_findings`` 按 ``(evidence_id, sq_id)`` 去重（跳内+跨跳）、
  跨子问题保留归属、无身份保守保留；graph._research 同步 visited_sources（跳内不重复）
  并落 ``evidence_novelty``；Critic 裁决输入消费新证据率；revise 回填队列内去重。

全部离线：fake researcher / monkeypatch LLMClient，零 API key。
"""
from __future__ import annotations

from research_engine.agents.researcher import POOL_TOTAL_CAP, pool_and_trim
from research_engine.evidence import dedupe_new_findings, ensure_evidence_identity
from research_engine.state import ResearchFinding, ResearchState, SubQuestion


def _finding(content: str, source: str = "https://s/a", sq_id: str = "q1",
             source_type: str = "web") -> ResearchFinding:
    return ResearchFinding(content=content, source=source, source_type=source_type,
                           sq_id=sq_id, confidence=0.6)


def _subs() -> list[SubQuestion]:
    return [SubQuestion(id="q1", question="Q1", rationale="r")]


# ---------------------------------------------------------------- F09：多样性优先融合

def test_pool_round_robin_no_source_starved():
    """web/rag/arxiv 各 5 条、无 code：10 个名额三源分享，不再按固定顺序饿死 arxiv。"""
    all_f = {
        "web": [_finding(f"w{i}", source=f"https://w/{i}") for i in range(5)],
        "rag": [_finding(f"r{i}", source=f"rag:d{i}", source_type="rag") for i in range(5)],
        "arxiv": [_finding(f"a{i}", source=f"https://arxiv.org/abs/{i}", source_type="arxiv") for i in range(5)],
    }
    pooled, stats = pool_and_trim(all_f)
    counts = {k: sum(1 for f in pooled if f.source_type == ("code_exec" if k == "code" else k))
              for k in ("web", "rag", "arxiv")}
    assert len(pooled) == POOL_TOTAL_CAP
    assert all(counts[k] >= 3 for k in counts), f"任一来源不应被饿死：{counts}"
    assert stats["adopted"] == {"web": 4, "rag": 3, "arxiv": 3}
    assert stats["web"] == 5 and stats["arxiv"] == 5  # 原始产出数保留


def test_pool_rag_only_fills_available_slots():
    """单一来源时轮转退化为顺序取（不空转、不超 Top-5）。"""
    web = [_finding(f"w{i}", source=f"https://w/{i}") for i in range(8)]
    pooled, stats = pool_and_trim({"web": web})
    assert len(pooled) == 5  # 每工具 Top-5
    assert stats["adopted"] == {"web": 5}


# ---------------------------------------------------------------- F10：证据身份去重

def test_dedupe_same_evidence_same_sq_across_hops():
    old = _finding("同一片段")
    ensure_evidence_identity([old])
    dup = _finding("同一片段")          # 同 source+content ⇒ 同 evidence_id
    fresh = _finding("新片段", source="https://s/b")
    ensure_evidence_identity([dup, fresh])

    kept, stats = dedupe_new_findings([dup, fresh], [old])
    assert [f.content for f in kept] == ["新片段"]
    assert stats == {"considered": 2, "kept": 1, "dropped_duplicates": 1}


def test_dedupe_same_evidence_other_sq_kept():
    """跨子问题保留归属：同一片段支撑 q2 时不能被 q1 的历史去重吞掉。"""
    old = _finding("同一片段", sq_id="q1")
    new = _finding("同一片段", sq_id="q2")
    ensure_evidence_identity([old, new])
    kept, stats = dedupe_new_findings([new], [old])
    assert len(kept) == 1 and stats["dropped_duplicates"] == 0


def test_dedupe_without_identity_conservative():
    """无 evidence_id（旧数据/异常路径）保守保留，不误删。"""
    no_id = _finding("无身份片段")
    kept, stats = dedupe_new_findings([no_id], [])
    assert len(kept) == 1 and stats["dropped_duplicates"] == 0


def test_dedupe_intra_batch():
    a = _finding("批内重复")
    b = _finding("批内重复")
    ensure_evidence_identity([a, b])
    kept, stats = dedupe_new_findings([a, b], [])
    assert len(kept) == 1 and stats["dropped_duplicates"] == 1


# ---------------------------------------------------------------- F10：图级接线

def _graph_with_fake_researcher(findings, tool_stats):
    from research_engine.graph import DeepResearchGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)

    class _R:
        def search_once(self, query, state=None):
            return list(findings), dict(tool_stats)

        def drain_degradations(self):
            return []

    graph.researcher = _R()
    return graph


def test_research_hop_dedup_novelty_and_visited_sync():
    old = _finding("旧片段")
    ensure_evidence_identity([old])
    dup = _finding("旧片段")                                  # 与历史同身份 ⇒ 去重
    s_a1 = _finding("A 片段 1", source="https://s/a")
    s_a2 = _finding("A 片段 2", source="https://s/a")          # 同 source 不同片段 ⇒ 保留
    s_b = _finding("B 片段", source="https://s/b")
    ensure_evidence_identity([dup, s_a1, s_a2, s_b])

    graph = _graph_with_fake_researcher(
        [dup, s_a1, s_a2, s_b],
        {"web": 4, "adopted": {"web": 3}, "code_failed": 0},
    )
    state = ResearchState(
        topic="t", subquestions=_subs(),
        frontier=[{"sq_id": "q1", "query": "q"}],
        findings=[old], visited_sources=[],
    )
    delta = graph._research(state)

    assert len(delta["findings"]) == 4  # 1 旧 + 3 新（去重 1）
    novelty = delta["evidence_novelty"][0]
    assert novelty["considered"] == 4 and novelty["kept"] == 3
    assert novelty["dropped_duplicates"] == 1 and novelty["ratio"] == 0.75
    # visited_sources：跳内同 source 只记一次
    assert delta["visited_sources"] == ["https://s/a", "https://s/b"]
    msg = delta["progress"][0]["msg"]
    assert "去重 1 条" in msg and "web 3/4" in msg and "+3 条新发现" in msg


def test_revise_dedupes_queries_already_pending():
    from research_engine.graph import DeepResearchGraph

    graph = DeepResearchGraph.__new__(DeepResearchGraph)
    state = ResearchState(
        topic="t", subquestions=_subs(),
        frontier=[{"sq_id": "q1", "query": "已有查询"}],
        next_queries=[{"sq_id": "q1", "query": "已有查询"},
                      {"sq_id": "q1", "query": "新查询"}],
    )
    delta = graph._revise(state)
    assert [q["query"] for q in delta["frontier"]] == ["已有查询", "新查询"]
    assert "队列内去重 1 条" in delta["progress"][0]["msg"]


def test_critic_prompt_consumes_novelty(monkeypatch):
    captured: dict = {}

    def _chat_json(self, messages, state=None, schema=None):
        captured["user"] = next(m["content"] for m in messages if m["role"] == "user")
        return {"sufficient": True, "needs_replan": False, "knowledge_gap": "",
                "next_queries": [], "cited_evidence_ids": []}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)

    from research_engine.critic import Critic

    state = ResearchState(
        topic="t", subquestions=_subs(),
        evidence_novelty=[{"depth": 1, "sq_id": "q1", "considered": 4,
                           "kept": 1, "dropped_duplicates": 3, "ratio": 0.25}],
    )
    Critic().decide(state)
    assert "新增证据 1/4" in captured["user"]
    assert "换查询或停止" in captured["user"]
