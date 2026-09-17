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

from pydantic import BaseModel, Field

from config import config
from research_engine.state import ResearchState

Signal = str  # "continue" | "revise" | "stop"

# W7 Arm1 TBD-1b：gap 反思轮次专用封顶（不复用 max_total_hops=20）
MAX_GAP_REFLECTIONS = 6

_CRITIC_SYSTEM_BASE = (
    "你是深度研究 Agent 的质量 critic。基于已有发现判断研究是否充分，"
    "或是否需要换角度重新检索，或方向是否彻底跑偏需要重分解。"
    "W7 新增：若判为充分但仍有未补上的知识缺口，请在 knowledge_gap 中说明，"
    "并给出 next_queries 弥补；若缺口已被补全或无法给出查询，knowledge_gap 留空。"
)

# W7 Arm1（critic_gap_enabled）注入的动态片段。**必须在指纹覆盖范围内**——
# 它由一个实验开关控制，同 commit 下开/关会得到不同的提示词与不同的裁决倾向。
_CRITIC_GAP_EXTRA = (
    "Bug-4 修复补充：判断 sufficient 时使用'基本充分'标准——如果已有发现能支撑报告"
    "的主体结论，仅有少量细节缺口，应判 sufficient=true 并在 knowledge_gap 中说明缺口；"
    "只有当主体结论缺乏支撑时才判 sufficient=false。无论 sufficient 取何值，"
    "只要存在知识缺口就应填写 knowledge_gap 和 next_queries。"
)

_CRITIC_SYSTEM_TAIL = (
    "只输出 JSON：{\"sufficient\": bool, \"needs_replan\": bool, \"knowledge_gap\": str, "
    "\"next_queries\": [{\"sq_id\": str, \"query\": str}]}。"
)


def build_critic_system(cfg=None) -> str:
    """Critic system 提示词的**唯一产生点**（W8 Arm 6）。

    为什么 critic 必须单独抽出来：它的提示词是**运行时拼装**的 ——
    `critic_gap_enabled` 开关会把 `_CRITIC_GAP_EXTRA` 这段裁决标准插进去。
    若只哈希常量模板，则「开关翻转导致 critic 行为改变」这件事在 provenance 里
    **完全不可见** —— 而 W7 已实测 critic 行为直接决定 avg_steps 与覆盖度。
    """
    c = cfg if cfg is not None else config
    gap_extra = _CRITIC_GAP_EXTRA if c.experiment.critic_gap_enabled else ""
    return _CRITIC_SYSTEM_BASE + gap_extra + _CRITIC_SYSTEM_TAIL


class CriticVerdict(BaseModel):
    """critic LLM 裁决的结构化 schema（防模型漏 key 被静默默认值误判）。"""

    sufficient: bool = Field(description="研究是否已充分支撑报告")
    needs_replan: bool = Field(default=False, description="方向是否跑偏需重分解")
    knowledge_gap: str = Field(
        default="",
        description="W7 Arm1：若研究充分但仍有未补缺口，说明缺失知识；否则留空",
    )
    next_queries: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="换角度的新查询 [{sq_id, query}]，回填 frontier",
    )


def hard_gate(state: ResearchState, cfg=config) -> Optional[Signal]:
    """确定性硬闸。触发任一上限即返回 "stop"，否则 None（继续走 LLM 裁决）。

    P1 max_replan 语义修正：replan_count 不再是全局停止条件，
    只在 needs_replan=True 时限制重规划次数（在 _resolve_signal 中检查）。

    三维度：
      - frontier 空            → 没有待检索查询，循环终止
      - depth ≥ max_total_hops → 全局跳数预算耗尽
      - token_used ≥ token_budget → LLM token 预算耗尽
    """
    rc = cfg.research
    if not state.frontier:
        return "stop"
    if state.depth >= rc.max_total_hops:
        return "stop"
    if state.token_used >= rc.token_budget:
        return "stop"
    return None


def route_critic(state: ResearchState) -> Signal:
    """纯函数路由：读取 critic 节点写入的 critic_signal，映射到条件边。

    P1 frontier 闭环修复：新增 "augment" 信号（gap 查询回填 frontier）。
    """
    signal = state.critic_signal
    if signal in ("continue", "revise", "stop", "augment"):
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
            state.critic_stop_reason = "hard_stop"
            state.critic_gap = ""
            return "stop"
        verdict = self._verdict(state)
        # verdict: {sufficient, needs_replan, knowledge_gap, next_queries}
        state.sufficient = bool(verdict.get("sufficient", False))
        state.needs_replan = bool(verdict.get("needs_replan", False))
        state.critic_gap = str(verdict.get("knowledge_gap", "")).strip()
        state.next_queries = list(verdict.get("next_queries", []))
        state.critic_signal, state.critic_stop_reason = self._resolve_signal(state)
        return state.critic_signal

    def _resolve_signal(self, state: ResearchState) -> tuple[Signal, str]:
        """W7 Arm1：gap 硬规则 + N=6 封顶。返回 (signal, stop_reason)。

        P1 frontier 闭环修复：
        - "augment" = gap 查询回填 frontier（走 revise 节点的 Q2-A 路径）
        - "continue" = 使用现有 frontier 继续（不追加新查询）
        - "revise" = 方向跑偏，需要重规划
        - "stop" = 终止
        """
        # 方向跑偏优先走 revise（与 gap 规则互不覆盖）
        # P1 max_replan 语义修正：replan 达到上限时不再重规划，转为 stop
        if state.needs_replan:
            from config import config as _cfg_rc
            if state.replan_count >= _cfg_rc.research.max_replan:
                return "stop", "replan_exhausted"
            return "revise", "revise"

        # W7 Arm1：gap 硬规则可通过 CRITIC_GAP_ENABLED 关闭（TBD-8 基线对照）
        from config import config as _cfg

        if not _cfg.experiment.critic_gap_enabled:
            if state.sufficient:
                return "stop", "critic_stop"
            return "continue", "continue"

        # Bug-4 修复：gap 硬规则在 sufficient=True 和 sufficient=False 时都可达
        gap = state.critic_gap
        has_queries = bool(state.next_queries)
        reflection_count = len(state.reflection_log)

        if state.sufficient:
            # sufficient=True：gap 非空 + next_queries 非空 → 强制回填（防 LLM 早停）
            if not gap or not has_queries:
                if gap and not has_queries:
                    return "stop", "gap_unresolved"
                if not has_queries:
                    return "stop", "no_next_queries"
                return "stop", "critic_stop"
            # gap 非空且 next_queries 非空
            if reflection_count < MAX_GAP_REFLECTIONS:
                return "augment", "gap_continue"
            return "stop", "critic_stop"
        else:
            # sufficient=False：模型说"不充分"
            # Bug-4 修复：如果模型没填 gap 和 next_queries 就说"不充分"，
            # 说明模型没指出具体缺口——仍走 continue，但标记为 lazy_continue
            if not gap and not has_queries:
                return "continue", "lazy_continue"
            # Bug-10 修复：有 gap 但无 next_queries → 无法回填 frontier，走 continue
            if gap and not has_queries:
                return "continue", "gap_unresolved_continue"
            # 有 next_queries → 回填 frontier
            # 设计-1 修复：sufficient=False 时也受 MAX_GAP_REFLECTIONS 封顶
            if reflection_count < MAX_GAP_REFLECTIONS:
                return "augment", "gap_continue"
            return "continue", "gap_reflection_cap"

    def _verdict(self, state: ResearchState) -> Dict[str, Any]:
        """拿 critic 的结构化裁决。llm_fn 注入时直接用（测试）；否则走真实 LLM。"""
        if self.llm_fn is not None:
            return self.llm_fn(state)
        # 真实 LLM 裁决（生产路径；token 累加由 router 在 Q6 完成）。
        from research_engine.llm.client import LLMClient

        # W8 Arm 6：提示词由 build_critic_system() 产出（唯一产生点），
        # 保证 provenance 的 prompt_hash 与这里发出去的串逐字一致。
        system = build_critic_system()
        subs = "; ".join(f"{sq.id}: {sq.question}" for sq in state.subquestions)
        reflection_round = len(state.reflection_log) + 1
        user = (
            f"研究主题：{state.topic}\n"
            f"子问题集合：{subs}\n"
            f"当前已检索跳数：{state.depth}，发现条数：{len(state.findings)}\n"
            f"这是第 {reflection_round}/{MAX_GAP_REFLECTIONS} 轮反思。\n"
            f"请判断：发现是否已充分支撑报告？若仍有知识缺口，在 knowledge_gap 中说明并给出 next_queries（带 sq_id）；"
            f"若方向跑偏，needs_replan=true。"
        )
        # W4 Q7：裁决归位 critic_model（修复 W3 前硬编码 smart_model 的现状 bug——裁决属 strategic 层）
        # W5（Q2）：role="critic" 进职责桶
        client = LLMClient(model=config.llm.critic_model, role="critic")
        return client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            state=state,  # Q6-B：让 critic 的 LLM token 用量也累加进硬闸
            schema=CriticVerdict,  # 漏 key/类型错 → ValidationError → 自动纠错重试，不再静默默认
        )
