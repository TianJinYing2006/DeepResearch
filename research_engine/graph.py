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

import uuid
from typing import Any, Dict, List

from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, StateGraph

from config import config
from research_engine.agents.planner import Planner
from research_engine.agents.researcher import Researcher
from research_engine.agents.validator import Validator
from research_engine.agents.writer import Writer
from research_engine.context.manager import ContextManager
from research_engine.critic import Critic, route_critic
from research_engine.llm.client import LLMClient  # W3（Q3=D'）：类级计数做 run 级对账基线
from research_engine.observability import (  # W3：可观测层（Q1~Q7）
    create_trace_id,
    get_langfuse,
    span_node,
    start_trace,
)
from research_engine.render import ReportRenderer
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
        self.renderer = ReportRenderer()  # W2：validate 后渲染可审计展示版（Q1=A 挂 validate→END）
        self.trace_id: str | None = None  # W3（Q2/Q5）：本次运行的 Langfuse trace id（CLI 收尾回显用）
        self.tokens_diff: int | None = None  # W3（Q3=D'）：本次 run 的对账差值（漏传 state 累计）
        self.graph = self._build()

    def _build(self):
        g = StateGraph(ResearchState)

        g.add_node("plan", self._plan)
        g.add_node("research", self._research)
        g.add_node("critic", self._critic)
        g.add_node("revise", self._revise)
        g.add_node("write", self._write)
        g.add_node("validate", self._validate)
        g.add_node("render", self._render)  # W2（Q1=A）：validate 后渲染，可审计展示不回流 report

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
        g.add_edge("validate", "render")  # W2：先校验后渲染（修正文档旧稿"write 之前"时序倒置）
        g.add_edge("render", END)

        # 有循环 → 必须挂 checkpointer + 设 recursion_limit（run 时传）
        return g.compile(checkpointer=MemorySaver())

    # ---- 节点实现（Q7=A：纯函数，只 return delta，绝不就地改 state）----

    def _plan(self, state: ResearchState) -> Dict[str, Any]:
        with span_node("规划", node="plan", input={"topic": state.topic[:200]}):
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

        # 单跳检索（W4：并行全工具 + 结果池择优；visited_sources 去重在本节点）
        # W3（Q2=C1'）：每跳一个扁平 span，轮次前缀 R{k}，metadata 带 depth/sq_id/query
        with span_node(f"R{state.depth + 1}-检索", node="research",
                       input={"query": (query or "")[:200]},
                       depth=state.depth + 1, sq_id=sq_id or "", query=(query or "")[:200]):
            new_findings, tool_stats = self.researcher.search_once(query, state)

        merged_findings = list(state.findings) + new_findings
        seen = set(state.visited_sources)
        new_sources = [f.source for f in new_findings if f.source not in seen]
        merged_visited = list(state.visited_sources) + new_sources

        new_depth = state.depth + 1
        per_subq_hop[sq_id] = per_subq_hop.get(sq_id, 0) + 1

        # W4 Q8：状态快照消息（新增条数 / 工具明细 / 累计条数 / hop 进度）——零新增 state 字段
        tool_detail = " / ".join(
            f"{k} {tool_stats.get(k, 0)}" + ("(失败)" if k == "code" and tool_stats.get("code_failed", 0) else "")
            for k in ("web", "rag", "arxiv", "code") if k in tool_stats
        )
        snapshot = (f"第 {new_depth}/{rc.max_total_hops} 跳 [{sq_id}]："
                    f"+{len(new_findings)} 条新发现（{tool_detail}），累计 {len(merged_findings)} 条")

        return {
            "frontier": frontier,  # 已弹出 head
            "findings": merged_findings,
            "visited_sources": merged_visited,
            "depth": new_depth,
            "per_subq_hop": per_subq_hop,
            "status": "researching",
            "progress": [
                {"stage": "research", "msg": snapshot},
            ],
        }

    def _critic(self, state: ResearchState) -> Dict[str, Any]:
        # 先硬闸（确定性），未触发才调 LLM；结果写回 state 各 verdict 字段并返回 signal
        # W3（Q2=C1'）：扁平 span 与对向检索成对 R{k}-裁决
        with span_node(f"R{state.depth}-裁决", node="critic",
                       input={"depth": state.depth}, depth=state.depth):
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
                 + ("（需重分解）" if state.needs_replan else "")}
            ],
        }

    def _revise(self, state: ResearchState) -> Dict[str, Any]:
        rc = config.research
        with span_node(f"R{state.depth}-修订", node="revise",
                       input={"needs_replan": state.needs_replan, "replan_count": state.replan_count}):
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
        with span_node("写作", node="write"):
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
        with span_node("校验", node="validate"):
            citations = self.validator.validate(state.report, state.findings, state)
        verified = sum(1 for c in citations if c.verified)
        return {
            "citations": citations,
            "status": "done",
            "token_used": state.token_used,  # Q6-B：validator 的 LLM token 累计写回
            "progress": [
                {"stage": "validate",
                 "msg": f"校验完成：{verified}/{len(citations)} 条引用通过（存在性 AND 忠实度）"}
            ],
        }

    def _render(self, state: ResearchState) -> Dict[str, Any]:
        # W2（Q1=A/Q4=A/Q6=A/R2.5）：render 节点统一 post-process：
        # 类型标注 + 失败 ⚠️ + 可信声明（双口径）+ 失败附录 + 运行溯源。
        # 展示层增强写 report_display，不回流 report（Writer 纯编号协议保持字节级不变）。
        with span_node("渲染", node="render"):
            display = self.renderer.render(state.report, state.citations, state.findings, state)
        return {
            "report_display": display,
            "status": "done",
            "progress": [{"stage": "render", "msg": "溯源渲染完成：类型标注+失败隔离+运行溯源"}],
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

        W3（Q2/Q5）：启用观测时，以自持 trace_id 开启根 trace 包裹 invoke——
        self.trace_id 在进入前赋值，即使 invoke 抛异常，CLI 的 finally 也能取到并 flush 回显 URL。
        """
        if thread_id is None:
            thread_id = f"dr-{uuid.uuid4().hex[:12]}"
        initial = ResearchState(topic=topic, user_instructions=user_instructions)
        cfg = {"configurable": {"thread_id": thread_id},
               "recursion_limit": config.research.max_total_hops * 2 + 20}
        self.trace_id = create_trace_id(thread_id)  # 未启用 → None（走无观测路径）
        token_base = LLMClient.tokens_total  # Q3=D'：run 级对账基线（类级累计跨 run 增长）
        lf = get_langfuse()
        if lf is None or not self.trace_id:
            result = self.graph.invoke(initial, cfg)
        else:
            with start_trace(self.trace_id, topic, thread_id=thread_id,
                             user_instructions=user_instructions):
                result = self.graph.invoke(initial, cfg)
        if isinstance(result, dict):
            result = ResearchState(**result)
        # Q3=D'：基线对齐后差值 = 本次 run 中"调用 LLM 但未传 state"的 token 累计
        self.tokens_diff = (LLMClient.tokens_total - token_base) - result.token_used
        return result


def create_graph() -> DeepResearchGraph:
    return DeepResearchGraph()
