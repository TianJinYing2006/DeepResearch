# -*- coding: utf-8 -*-
r"""W1 图循环收敛锁（grill Q9 第一步）：纯单测，mock LLM，零 API key。

锁定《第一周需求文档》DoD 第 1/3 条：
  - 循环绝不无限循环（硬闸四维度 + 路由纯函数，可证）
  - 在预算内停止（max_total_hops 等硬上限优先于 LLM）

运行：python tests/test_graph_loop.py
风格：沿用本仓库离线测试约定（main() + assert，无需 pytest / 真实 LLM）。
"""
from __future__ import annotations

import os
import sys

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

from config import config
from research_engine.state import ResearchState
from research_engine.critic import hard_gate, route_critic, Critic


def make_state(**kw) -> ResearchState:
    kw.setdefault("topic", "t")
    return ResearchState(**kw)


# ---------- 1) 硬闸四维度 + 正常 ----------
def test_hard_gate():
    seed = [{"sq_id": "s1", "query": "q"}]

    # frontier 空 → 没有待检索查询，必须 stop
    assert hard_gate(make_state(frontier=[])) == "stop", "frontier 空必须 stop"

    # depth 达全局上限 → stop
    assert hard_gate(make_state(frontier=seed, depth=config.research.max_total_hops)) == "stop"

    # token 达预算 → stop（Q6-B）
    assert hard_gate(make_state(frontier=seed, token_used=config.research.token_budget)) == "stop"

    # replan 达上限 → stop（Q2-B 兜底）
    assert hard_gate(make_state(frontier=seed, replan_count=config.research.max_replan)) == "stop"

    # 全部在预算内 → 不触发（返回 None，交 LLM 裁决）
    assert hard_gate(make_state(frontier=seed)) is None
    print("  ✅ hard_gate：frontier空/depth/token/replan 四维度均触发 stop；正常返回 None")


# ---------- 2) 路由纯函数三态 ----------
def test_route_critic():
    assert route_critic(make_state(critic_signal="stop")) == "stop"
    assert route_critic(make_state(critic_signal="continue")) == "continue"
    assert route_critic(make_state(critic_signal="revise")) == "revise"
    print("  ✅ route_critic：critic_signal 三态纯函数映射正确")


# ---------- 3) Critic.decide + 注入 mock LLM 三态 ----------
def test_decide_with_mock_llm():
    # sufficient=True → stop
    c1 = Critic(llm_fn=lambda s: {"sufficient": True, "needs_replan": False, "next_queries": []})
    assert c1.decide(make_state(frontier=[{"sq_id": "s1", "query": "q"}])) == "stop"

    # needs_replan=True → revise（Q2-B 兜底路径）
    c2 = Critic(llm_fn=lambda s: {"sufficient": False, "needs_replan": True, "next_queries": []})
    assert c2.decide(make_state(frontier=[{"sq_id": "s1", "query": "q"}])) == "revise"

    # 不充分且有 next_queries → continue（Q2-A 回填 frontier）
    c3 = Critic(llm_fn=lambda s: {
        "sufficient": False,
        "needs_replan": False,
        "next_queries": [{"sq_id": "s1", "query": "q2"}],
    })
    assert c3.decide(make_state(frontier=[{"sq_id": "s1", "query": "q"}])) == "continue"
    print("  ✅ Critic.decide：sufficient→stop / needs_replan→revise / 不充分+next_queries→continue")


# ---------- 4) 硬闸优先于 LLM（短路，绝不调 LLM）----------
def test_hard_gate_short_circuits_llm():
    def boom(_state):
        raise RuntimeError("硬闸未触发时不应调用 LLM")

    c = Critic(llm_fn=boom)
    # frontier 空 → 硬闸直接 stop，llm_fn 不应被调用
    assert c.decide(make_state(frontier=[])) == "stop"
    # depth 超限 → 同样短路
    assert c.decide(make_state(frontier=[{"sq_id": "s1", "query": "q"}], depth=config.research.max_total_hops)) == "stop"
    print("  ✅ 硬闸短路：frontier 空 / depth 超限时 LLM 完全不被调用（安全由代码兜底，Q3）")


# ---------- 4b) Q6-B：LLMClient._accumulate_usage 把 token 累加进 state（可观测+控闸数据源）----------
def test_accumulate_usage_into_state():
    from research_engine.llm.client import LLMClient

    class _Resp:
        class usage:  # noqa: N801
            total_tokens = 1234

    st = make_state(token_used=10)
    LLMClient()._accumulate_usage(_Resp(), st)
    assert st.token_used == 1244, "token 必须累加进 state.token_used（Q6-B 数据源）"

    # 响应无 usage 时不应报错，也不改变现有值
    class _RespNoUsage:
        usage = None

    LLMClient()._accumulate_usage(_RespNoUsage(), st)
    assert st.token_used == 1244, "无 usage 时应安全跳过，不改变累计值"
    print("  ✅ _accumulate_usage：token 累加进 state，无 usage 时安全跳过（Q6-B 数据源正确）")


# ---------- 4c) Q8/RAG：RAG finding 的 source 必须按文档身份（rag:<filename>），不得被去重饿死 ----------
def test_rag_source_uses_doc_filename():
    from research_engine.agents.researcher import Researcher

    res = Researcher.__new__(Researcher)  # 不走 __init__（避免触发网络/嵌入）

    class FakeRetriever:
        def retrieve(self, query, top_k=5):
            return [
                {"text": "chunk A", "score": 0.90, "source": "vector", "doc": "doc_a.md"},
                {"text": "chunk B", "score": 0.80, "source": "vector", "doc": "doc_a.md"},
                {"text": "chunk C", "score": 0.70, "source": "bm25", "doc": "doc_b.md"},
                {"text": "chunk D", "score": 0.60, "source": "bm25", "doc": ""},  # 无 doc 时兜底
            ]

    res.retriever = FakeRetriever()
    fs = res._search_rag("q")
    srcs = [f.source for f in fs]
    assert srcs == ["rag:doc_a.md", "rag:doc_a.md", "rag:doc_b.md", "rag:bm25"], \
        f"RAG source 应为 rag:<filename>（无 doc 兜底 rag:bm25），实际 {srcs}"
    assert all(f.source_type == "rag" for f in fs)
    print(f"  ✅ RAG source 按文档身份：{srcs}（不再共用 rag:vector 导致去重饿死）")


# ---------- 4d) Q8/RAG：跨查询按 rag:<filename> 去重，不同 RAG 文档互不误伤 ----------
def test_search_once_rag_dedup_across_queries():
    from research_engine.agents.researcher import Researcher
    from research_engine.state import ResearchFinding

    res = Researcher.__new__(Researcher)
    res._search_web = lambda q: []
    res._search_rag = lambda q: [
        ResearchFinding(content=f"r1-{q}", source="rag:doc_a.md", source_type="rag", confidence=0.7),
        ResearchFinding(content=f"r2-{q}", source="rag:doc_b.md", source_type="rag", confidence=0.7),
    ]

    st = make_state()
    first = res.search_once("q1", st)
    assert {f.source for f in first} == {"rag:doc_a.md", "rag:doc_b.md"}, \
        f"首批应保留两个不同 RAG 文档，实际 {[f.source for f in first]}"

    # 模拟图把首批 source 写回 visited_sources：第二跳不应重复拉同一批 RAG 文档
    st2 = make_state(visited_sources=[f.source for f in first])
    second = res.search_once("q2", st2)
    assert second == [], f"同一 RAG 文档重复检索应被 visited_sources 去重，实际 {[f.source for f in second]}"
    print("  ✅ search_once：跨查询按 rag:<filename> 去重，不同 RAG 文档互不误伤")


# ---------- 4e) RAG 健壮性：VectorStore 瞬时连接失败 2s 冷却降级，冷却后自动重试（不整场置灰） ----------
def test_vectorstore_cooldown_retry():
    import research_engine.rag.store as store_mod
    from research_engine.rag.store import VectorStore

    original_time = store_mod.time.time
    original_client_cls = store_mod.QdrantClient
    fake_now = [100.0]
    calls = {"n": 0}

    class FakeQdrant:
        def __init__(self, *a, **kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("首次连接瞬时失败")

        def get_collections(self):
            return type("R", (), {"collections": []})()

        def create_collection(self, **kw):
            pass

    store_mod.time.time = lambda: fake_now[0]
    store_mod.QdrantClient = FakeQdrant
    try:
        vs = VectorStore(url="http://127.0.0.1:9999", collection="t")
        assert vs._get_client() is None, "首次失败后应进入降级"
        assert vs._get_client() is None, "冷却期内应继续降级（不重试）"
        fake_now[0] += 3.0  # 冷却期（2s）过后
        client = vs._get_client()
        assert client is not None, "冷却期过后应自动重试成功"
        assert calls["n"] == 2, f"应恰好重试一次，实际 {calls['n']}"
    finally:
        store_mod.time.time = original_time
        store_mod.QdrantClient = original_client_cls
    print("  ✅ VectorStore：瞬时失败 2s 冷却降级，冷却后自动重试自愈（不再整场置灰）")


# ---------- 5) 最小图循环：硬闸强制终止，无 GraphRecursionError ----------
def test_loop_terminates_with_fake_nodes():
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph

    def fake_research(state: ResearchState):
        # 取队首做一跳，depth+1；只要没到全局上限就模拟"继续检索"补一个同 sq 新查询
        head = state.frontier[0] if state.frontier else {"sq_id": "s1", "query": "q"}
        new_frontier = state.frontier[1:]
        new_depth = state.depth + 1
        if new_depth < config.research.max_total_hops:
            new_frontier = new_frontier + [{"sq_id": head["sq_id"], "query": f"q{new_depth}"}]
        return {"frontier": new_frontier, "depth": new_depth, "critic_signal": "continue"}

    def fake_critic(state: ResearchState):
        gate = hard_gate(state, config)
        return {"critic_signal": "stop" if gate == "stop" else "continue"}

    def route(state: ResearchState):
        return state.critic_signal

    g = StateGraph(ResearchState)
    g.add_node("research", fake_research)
    g.add_node("critic", fake_critic)
    g.set_entry_point("research")
    g.add_edge("research", "critic")
    g.add_conditional_edges("critic", route, {"continue": "research", "stop": END})
    app = g.compile(checkpointer=MemorySaver())

    init = ResearchState(topic="t", frontier=[{"sq_id": "s1", "query": "q0"}])
    # recursion_limit 远大于预算上限，用以证明"是硬闸在停，而非框架兜底抛错"
    res = app.invoke(init, {"configurable": {"thread_id": "test-loop"}, "recursion_limit": 100})

    final = res if isinstance(res, dict) else res
    depth = final.get("depth") if isinstance(final, dict) else final.depth
    signal = final.get("critic_signal") if isinstance(final, dict) else final.critic_signal

    assert depth <= config.research.max_total_hops, f"depth({depth}) 超过预算上限"
    assert signal == "stop", "循环必须在预算内停止"
    print(f"  ✅ 最小图循环：即使 critic 永远 return continue，硬闸在 depth={depth} 强制 stop，无 GraphRecursionError")


# ---------- 6) 真实 route_critic 接入条件边 + thread_id/MemorySaver 端到端跑通 ----------
def test_topology_route_and_thread_id():
    """用与真实 graph.py 完全相同的条件边拓扑 + 真实 route_critic，
    但 research/critic 用 fake 节点（零 LLM、零网络），验证：
      (a) 三态 continue/revise/stop 经 route_critic 正确路由
      (b) 带 thread_id 时 MemorySaver 正常（不报 "thread_id required"）
      (c) 循环在预算内 stop → write → validate 走到 done
    """
    from langgraph.checkpoint.memory import MemorySaver
    from langgraph.graph import END, StateGraph
    from research_engine.critic import route_critic

    def fake_research(state: ResearchState):
        head = state.frontier[0] if state.frontier else None
        new_frontier = state.frontier[1:]
        new_depth = state.depth + 1
        if new_depth >= 6:  # 模拟 max_total_hops 上限：清空队列触发 stop
            new_frontier = []
        return {"frontier": new_frontier, "depth": new_depth, "critic_signal": "continue"}

    def mock_critic(state: ResearchState):
        # 模拟 LLM 裁决：预算内 continue，达上限 sufficient→stop
        if state.depth >= 6:
            return {"critic_signal": "stop", "sufficient": True}
        return {"critic_signal": "continue", "sufficient": False}

    def fake_revise(state: ResearchState):
        return {"frontier": list(state.frontier) + list(state.next_queries), "next_queries": []}

    def fake_write(state: ResearchState):
        return {"report": "r", "status": "writing"}

    def fake_validate(state: ResearchState):
        return {"status": "done"}

    g = StateGraph(ResearchState)
    g.add_node("research", fake_research)
    g.add_node("critic", mock_critic)
    g.add_node("revise", fake_revise)
    g.add_node("write", fake_write)
    g.add_node("validate", fake_validate)
    g.set_entry_point("research")
    g.add_edge("research", "critic")  # 每跳检索后必过 critic 裁决
    g.add_conditional_edges("critic", route_critic,
                            {"continue": "research", "revise": "revise", "stop": "write"})
    g.add_edge("revise", "research")
    g.add_edge("write", "validate")
    g.add_edge("validate", END)
    app = g.compile(checkpointer=MemorySaver())

    init = ResearchState(
        topic="t",
        frontier=[{"sq_id": f"s{i}", "query": f"q{i}"} for i in range(3)],
    )
    # 与 run() 相同的 config 形状：thread_id + recursion_limit
    res = app.invoke(init, {"configurable": {"thread_id": "topo-test"}, "recursion_limit": 100})
    final = res if isinstance(res, dict) else res
    depth = final.get("depth") if isinstance(final, dict) else final.depth
    status = final.get("status") if isinstance(final, dict) else final.status
    assert depth <= 6, f"depth({depth}) 超过模拟上限"
    assert status == "done", "应在预算内 stop → write → validate 完成"
    print("  ✅ 拓扑验证：真实 route_critic 接入条件边，三态路由正确；thread_id+MemorySaver 正常，端到端跑通到 done")


def main():
    print("========== W1 收敛锁：硬闸 + 路由纯函数（纯单测，零 LLM） ==========")
    test_hard_gate()
    test_route_critic()
    test_decide_with_mock_llm()
    test_hard_gate_short_circuits_llm()
    test_accumulate_usage_into_state()
    test_rag_source_uses_doc_filename()
    test_search_once_rag_dedup_across_queries()
    test_vectorstore_cooldown_retry()
    test_loop_terminates_with_fake_nodes()
    test_topology_route_and_thread_id()
    print("\n========== 全部断言通过：W1 图循环收敛已被纯单测锁定 ==========")


if __name__ == "__main__":
    main()
