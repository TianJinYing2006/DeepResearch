# -*- coding: utf-8 -*-
"""Critic 节点：确定性硬闸 + LLM 语义裁决，分层决策（grill Q3）。

设计要点（见 .workbuddy/design-grill.md）：
- hard_gate：纯确定性。任一预算/收敛上限被触发即返回 "stop"，且**优先于** LLM。
  安全属性由代码兜底，不押在随机性 LLM 上（Q3=A）。
- route_critic：纯函数。把 critic 节点写入 state.critic_signal 的信号映射成条件边。
  输入确定 → 输出确定 → 可断言、可单测（呼应 Q3 可证性）。
- Critic.decide：先 hard_gate；未触发才调 LLM 拿结构化 verdict
  {sufficient, needs_replan, next_queries}，写回 state 并落 critic_signal。
  LLM 调用可注入（llm_fn），纯单测零 API key。
"""
from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional

from config import config
from research_engine.state import ResearchState

Signal = str  # "continue" | "revise" | "stop"


def hard_gate(state: ResearchState, cfg=config) -> Optional[Signal]:
    """确定性硬闸。触发任一上限即返回 "stop"，否则 None（继续走 LLM 裁决）。

    四维度（grill Q3/Q4/Q5/Q6）：
      - frontier 空            → 没有待检索查询，循环终止
      - depth ≥ max_total_hops → 全局跳数预算耗尽（与旧 max_depth×max_subquestions 等价）
      - token_used ≥ token_budget → LLM token 预算耗尽（Q6-B）
      - replan_count ≥ max_replan  → replan 兜底次数耗尽，防空转（Q2-B）
    """
    rc = cfg.research
    if not state.frontier:
        return "stop"
    if state.depth >= rc.max_total_hops:
        return "stop"
    if state.token_used >= rc.token_budget:
        return "stop"
    if state.replan_count >= rc.max_replan:
        return "stop"
    return None


def route_critic(state: ResearchState) -> Signal:
    """纯函数路由：读取 critic 节点写入的 critic_signal，映射到条件边。"""
    signal = state.critic_signal
    if signal in ("continue", "revise", "stop"):
        return signal
    # 兜底：基于 verdict 字段推导（decide 已写 signal，理论上不会到这）
    if state.sufficient:
        return "stop"
    if state.needs_replan:
        return "revise"
    return "continue"


class Critic:
    """Critic 节点逻辑。decide() 先硬闸后 LLM，最后落 critic_signal。"""

    def __init__(self, llm_fn: Optional[Callable[[ResearchState], Dict[str, Any]]] = None):
        # llm_fn 可注入，便于纯单测零 API key。生产环境传 None 走真实 LLM。
        self.llm_fn = llm_fn

    def decide(self, state: ResearchState, cfg=config) -> Signal:
        gate = hard_gate(state, cfg)
        if gate == "stop":
            state.critic_signal = "stop"
            return "stop"
        verdict = self._verdict(state)
        # verdict: {sufficient, needs_replan, next_queries}
        state.sufficient = bool(verdict.get("sufficient", False))
        state.needs_replan = bool(verdict.get("needs_replan", False))
        state.next_queries = list(verdict.get("next_queries", []))
        state.critic_signal = route_critic(state)
        return state.critic_signal

    def _verdict(self, state: ResearchState) -> Dict[str, Any]:
        """拿 critic 的结构化裁决。llm_fn 注入时直接用（测试）；否则走真实 LLM。"""
        if self.llm_fn is not None:
            return self.llm_fn(state)
        # 真实 LLM 裁决（生产路径；token 累加由 router 在 Q6 完成）。
        from research_engine.llm.client import LLMClient

        system = (
            "你是深度研究 Agent 的质量 critic。基于已有发现判断某子问题的研究是否充分，"
            "或是否需要换角度重新检索，或方向是否彻底跑偏需要重分解。"
            "只输出 JSON：{\"sufficient\": bool, \"needs_replan\": bool, \"next_queries\": [{\"sq_id\": str, \"query\": str}]}。"
        )
        subs = "; ".join(f"{sq.id}: {sq.question}" for sq in state.subquestions)
        user = (
            f"研究主题：{state.topic}\n"
            f"子问题集合：{subs}\n"
            f"当前已检索跳数：{state.depth}，发现条数：{len(state.findings)}\n"
            f"请判断：发现是否已充分支撑报告？若需换角度，给出 next_queries（带 sq_id）；"
            f"若方向跑偏，needs_replan=true。"
        )
        client = LLMClient(model=config.llm.smart_model)
        return client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            state=state,  # Q6-B：让 critic 的 LLM token 用量也累加进硬闸
        )
