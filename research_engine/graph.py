# -*- coding: utf-8 -*-
"""LangGraph 状态机编排（W1：frontier 循环 + Critic 条件边 + 硬闸）。

W1 重构（grill 设计，见 .workbuddy/design-grill.md）：
- 图拓扑（Q1=A）：全局 frontier 队列驱动 `research → critic → (continue|revise|stop)` 条件边，
  多子问题 × 多跳用同一套边表达，硬闸天然作用于全局、收敛可证。
- 决策分层（Q3=A）：critic 节点先跑确定性硬闸（depth/frontier/replan/token），再调 LLM 裁决；
  路由函数 route_critic 为纯函数，把 critic_signal 映射到三态。
- revise 去向（Q2=A/B）：常态把 critic 的 next_queries 追回 frontier 继续研究（A）；
  仅当 critic 判"方向跑偏"(needs_replan) 且 replan_count < max_replan 时触发 Planner.replan 全量重分解（B 兜底）。
- 预算等价（Q4=A）：max_total_hops=20 与旧 max_depth×max_subquestions 精确等价。
- 防饿死（Q5=A）：每子问题种子 query 1 条 + 每子问题跳数上限 per_subq_hop_cap=5。
- token 进硬闸（Q6=B）：client/router 累加 usage，token_used ≥ token_budget → stop。
- state 契约（Q7=A）：findings/visited_sources/frontier/subquestions 不加 reducer（保持覆写）；
  reflection_log/progress 加 add reducer（纯追加）；所有节点纯函数化，只 return delta。
- W1/W2 边界（Q8=A）：Send 并行推 W2；启用 visited_sources 去重；max_concurrent 不启用。
- 验收（Q9=A）：挂 MemorySaver + run() 传 thread_id + recursion_limit；收敛由 tests/test_graph_loop.py 纯单测锁定。
"""
from __future__ import annotations

import operator
import uuid
from typing import Annotated, Any, Dict, List

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from config import config
from research_engine.agents.planner import Planner
from research_engine.agents.researcher import Researcher
from research_engine.agents.validator import Validator
from research_engine.agents.writer import Writer
from research_engine.context.manager import ContextManager
from research_engine.critic import Critic, route_critic
from research_engine.state import ResearchState, SubQuestion


class DeepResearchGraph:
    """深度研究图（W1：显式 frontier 循环 + Critic 条件边）。"""

    def __init__(self):
        self.planner = Planner()
        self.researcher = Researcher()
        self.writer = Writer()
        self.validator = Validator()
        self.context = ContextManager()
        self.critic = Critic()  # llm_fn=None → 生产路径走真实 LLM
        self.graph = self._build()

    def _build(self):
        g = StateGraph(ResearchState)

        g.add_node("plan", self._plan)
        g.add_node("research", self._research)
        g.add_node("critic", self._critic)
        g.add_node("revise", self._revise)
        g.add_node("write", self._write)
        g.add_node("validate", self._validate)

        g.set_entry_point("plan")
        g.add_edge("plan", "research")
        g.add_edge("research", "critic")  # 每跳检索后必过 critic 裁决
        # critic 经纯函数路由三态
        g.add_conditional_edges(
            "critic",
            route_critic,
            {"continue": "research", "revise": "revise", "stop": "write"},
        )
        g.add_edge("revise", "research")  # 回填 next_queries 或 replan 后，继续研究
        g.add_edge("write", "validate")
        g.add_edge("validate", END)

        # 有循环 → 必须挂 checkpointer + 设 recursion_limit（run 时传）
        return g.compile(checkpointer=MemorySaver())

    # ---- 节点实现（Q7=A：纯函数，只 return delta，绝不就地改 state）----

    def _plan(self, state: ResearchState) -> Dict[str, Any]:
        subs: List[SubQuestion] = self.planner.plan(
            state.topic, state.user_instructions, state
        )
        # Q5=A 种子：每子问题 1 条初始 query（带 sq_id），塞入全局 frontier
        frontier = [{"sq_id": s.id, "query": s.question} for s in subs]
        per_subq_hop = {s.id: 0 for s in subs}
        return {
            "subquestions": subs,
            "frontier": frontier,
            "per_subq_hop": per_subq_hop,
            "status": "planning",
            "token_used": state.token_used,  # Q6-B：planner 的 LLM token 累计写回
            "progress": [
                {"stage": "plan", "msg": f"已分解为 {len(subs)} 个子问题，种子查询入队"}
            ],
        }

    def _research(self, state: ResearchState) -> Dict[str, Any]:
        rc = config.research
        frontier = list(state.frontier)
        per_subq_hop = dict(state.per_subq_hop)

        # 跳过已达"每子问题跳数上限"的查询（Q5=A 防饿死软约束）；不放进 depth
        head = None
        sq_id = None
        query = None
        while frontier:
            cand = frontier.pop(0)
            sid = cand.get("sq_id", "")
            if per_subq_hop.get(sid, 0) >= rc.per_subq_hop_cap:
                continue
            head = cand
            sq_id = sid
            query = cand.get("query", "")
            break

        if head is None:
            # 剩余查询全被 per_cap 过滤 → 队列实质性空，交给 critic 判 stop
            return {
                "frontier": [],
                "status": "researching",
                "progress": [
                    {"stage": "research", "msg": "剩余查询均达每子问题跳数上限，停止检索"}
                ],
            }

        # 单跳检索（基于 state.visited_sources 去重，Q8 启用）
        new_findings = self.researcher.search_once(query, state)

        merged_findings = list(state.findings) + new_findings
        seen = set(state.visited_sources)
        new_sources = [f.source for f in new_findings if f.source not in seen]
        merged_visited = list(state.visited_sources) + new_sources

        new_depth = state.depth + 1
        per_subq_hop[sq_id] = per_subq_hop.get(sq_id, 0) + 1

        return {
            "frontier": frontier,  # 已弹出 head
            "findings": merged_findings,
            "visited_sources": merged_visited,
            "depth": new_depth,
            "per_subq_hop": per_subq_hop,
            "status": "researching",
            "progress": [
                {"stage": "research", "msg": f"第 {new_depth} 跳 [{sq_id}]：{query} → {len(new_findings)} 条新发现"}
            ],
        }

    def _critic(self, state: ResearchState) -> Dict[str, Any]:
        # 先硬闸（确定性），未触发才调 LLM；结果写回 state 各 verdict 字段并返回 signal
        signal = self.critic.decide(state)
        entry = {
            "depth": state.depth,
            "signal": signal,
            "sufficient": state.sufficient,
            "needs_replan": state.needs_replan,
            "next_queries": state.next_queries,
        }
        return {
            "critic_signal": state.critic_signal,
            "sufficient": state.sufficient,
            "needs_replan": state.needs_replan,
            "next_queries": state.next_queries,
            "token_used": state.token_used,  # Q6-B：critic 的 LLM token 累计写回
            "reflection_log": [entry],  # add reducer 追加（Q7）
            "progress": [
                {"stage": "critic", "msg": f"depth={state.depth} 裁决={signal}"
                 + (f"（需重分解）" if state.needs_replan else "")}
            ],
        }

    def _revise(self, state: ResearchState) -> Dict[str, Any]:
        rc = config.research
        # Q2-B 兜底：方向跑偏且未达 replan 上限 → 全量重分解、重新 seed frontier
        if state.needs_replan and state.replan_count < rc.max_replan:
            new_subs = self.planner.replan(
                state.topic, state.subquestions, state.findings,
                "critic 判方向跑偏", state,
            )
            new_frontier = [{"sq_id": s.id, "query": s.question} for s in new_subs]
            new_per = {s.id: 0 for s in new_subs}
            return {
                "subquestions": new_subs,
                "frontier": new_frontier,
                "per_subq_hop": new_per,
                "replan_count": state.replan_count + 1,
                "needs_replan": False,
                "next_queries": [],
                "token_used": state.token_used,  # Q6-B：replan 的 LLM token 累计写回
                "progress": [
                    {"stage": "revise", "msg": f"重分解：{len(new_subs)} 个子问题（replan_count={state.replan_count + 1}）"}
                ],
            }
        # Q2-A 常态：next_queries 追回 frontier 队尾，继续研究同一子问题
        appended = list(state.next_queries)
        return {
            "frontier": list(state.frontier) + appended,
            "needs_replan": False,
            "next_queries": [],
            "token_used": state.token_used,  # Q6-B：本步无新 LLM 调用，原样写回保持最新累计
            "progress": [
                {"stage": "revise", "msg": f"换角度再搜：回填 {len(appended)} 条 next_queries"}
            ],
        }

    def _write(self, state: ResearchState) -> Dict[str, Any]:
        # 先压缩再写作；压缩结果写回 state.findings（ADR-0004 引用编号契约，保持覆写语义）
        compressed = self.context.compress(state.findings, state.topic, state)
        report = self.writer.write(state.topic, state.subquestions, compressed, state)
        return {
            "report": report,
            "findings": compressed,  # 不加 reducer → 覆写（Q7=A）
            "status": "writing",
            "token_used": state.token_used,  # Q6-B：writer+compress 的 LLM token 累计写回
            "progress": [{"stage": "write", "msg": "报告生成完成"}],
        }

    def _validate(self, state: ResearchState) -> Dict[str, Any]:
        citations = self.validator.validate(state.report, state.findings, state)
        verified = sum(1 for c in citations if c.verified)
        return {
            "citations": citations,
            "status": "done",
            "token_used": state.token_used,  # Q6-B：validator 的 LLM token 累计写回
            "progress": [
                {"stage": "validate",
                 "msg": f"校验完成：{verified}/{len(citations)} 条引用通过存在性校验"}
            ],
        }

    def run(
        self,
        topic: str,
        user_instructions: str = "",
        thread_id: str | None = None,
    ) -> ResearchState:
        """运行完整研究流程。

        Q9=A：每次 invoke 必须带 thread_id（MemorySaver 依赖），并设 recursion_limit。
        recursion_limit 远大于预算上限（max_total_hops），用以证明"是硬闸在停，而非框架兜底抛错"。
        """
        if thread_id is None:
            thread_id = f"dr-{uuid.uuid4().hex[:12]}"
        initial = ResearchState(topic=topic, user_instructions=user_instructions)
        cfg = {"configurable": {"thread_id": thread_id},
               "recursion_limit": config.research.max_total_hops * 2 + 20}
        result = self.graph.invoke(initial, cfg)
        if isinstance(result, dict):
            return ResearchState(**result)
        return result


def create_graph() -> DeepResearchGraph:
    return DeepResearchGraph()
