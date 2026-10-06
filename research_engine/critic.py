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
from research_engine.budget import research_token_ceiling
from research_engine.runtime_profile import effective_llm_model, effective_research_config
from research_engine.state import ResearchFinding, ResearchState, SubQuestion

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
    "\"next_queries\": [{\"sq_id\": str, \"query\": str}], \"cited_evidence_ids\": [str]}。"
    "cited_evidence_ids 必须引用证据清单里真实存在的证据 ID（如 E3），不得编造清单外的 ID。"
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


#: Critic 治理事件（与 Planner 的 PLANNER_EVENT_* 同构）：ID 归一化改写，非 FailureReason。
CRITIC_EVENT_SQID_REWRITTEN = "next_query_sq_id_rewritten"


def normalize_next_queries(
    queries: List[Dict[str, Any]], valid_ids: List[str],
) -> tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
    """把 critic 的 ``next_queries.sq_id`` 归一化到现有子问题（需求 16 / bug #75）。

    背景：critic LLM 会编造不存在或派生的 sq_id（如 ``q2.1``、``q2a``）；原实现未校验
    直接回填 frontier，导致 ① 报告出现「子问题：q2.1」空壳章节（渲染兜底把原始 ID
    当标题）；② ``per_subq_hop`` 给每个幽灵 ID 开新桶，稀释每子问题跳数帽。

    规则（最长前缀匹配，参照社区 structured-output-repair 的 fuzzy-key 修复思路）：
    - ``q2.1`` / ``q2a`` → ``q2``；
    - 无法匹配 → 置空字符串归「未分类材料」（**不丢弃 query**，保检索覆盖）；
    - 每条改写记录治理事件（审计用；不进 ``degradation_log``，不推导 degraded）。
    """
    ordered = sorted({str(i) for i in valid_ids if i}, key=len, reverse=True)
    normalized: List[Dict[str, Any]] = []
    events: List[Dict[str, Any]] = []
    for item in queries:
        if not isinstance(item, dict):
            continue
        query = dict(item)
        raw = str(query.get("sq_id", "") or "").strip()
        match = raw if raw in ordered else next(
            (vid for vid in ordered if raw.startswith(vid)), "")
        query["sq_id"] = match
        if raw != match:
            events.append({
                "event": CRITIC_EVENT_SQID_REWRITTEN,
                "phase": "critic",
                "original_id": raw,
                "rewritten_id": match,
            })
        normalized.append(query)
    return normalized, events


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
    cited_evidence_ids: List[str] = Field(
        default_factory=list,
        description="F01：支撑裁决的关键证据 ID（证据清单中的 E 编号），代码复核存在性",
    )


# ---- F01：证据清单（让 Critic 消费证据正文，而非只有发现条数）----

#: 送入 Critic 的证据条数上限（控 prompt 体积；ID 按 findings 原位置分配，跨轮稳定）
MAX_DIGEST_ITEMS = 20
#: 单条证据进入 Critic 的正文上限（超出截断；完整正文仍保留在 state.findings）
MAX_DIGEST_ITEM_CHARS = 200


def evidence_id(rank: int) -> str:
    """稳定证据 ID：按**非自指发现的先后顺序**连续编号（E1、E2…）。

    findings 只追加、不重排 ⇒ 同一 finding 跨反思轮次的 ID 不变，裁决引用可回溯；
    自指材料不占编号，清单里不会出现「E1 缺席」的困惑。
    """
    return f"E{rank + 1}"


def build_evidence_digest(
    findings: List[ResearchFinding],
    subquestions: List[SubQuestion],
    max_items: int = MAX_DIGEST_ITEMS,
    max_chars: int = MAX_DIGEST_ITEM_CHARS,
) -> tuple[str, List[str]]:
    """把 findings 组织为 Critic 可引用的证据清单；返回 ``(digest, valid_ids)``。

    F01 修复：旧 prompt 只给「发现条数」，10 条相关材料与 10 条无关材料产生**相同**
    的裁决输入。本函数让裁决真正消费证据内容：

    - **稳定 ID**：``E{i+1}`` 按**非自指发现的先后顺序**连续分配（append-only ⇒ 跨轮
      稳定，自指材料不占编号）；``is_meta`` 材料绝不作为正文证据，不进入清单（只计数提醒）；
    - **按子问题组织**：先按 ``subquestions`` 顺序分组，未归类材料殿后；无任何证据的
      子问题显式标注 ``[缺口]``，让「信息充分」可以按子问题解释；
    - **有界**：最多 ``max_items`` 条（置信度降序，平票取更早），单条正文截断到
      ``max_chars``，防止 prompt 随 findings 线性膨胀。

    ``valid_ids`` 只包含实际出现在 digest 中的 ID——裁决引用清单外的 ID 会被
    :func:`normalize_cited_evidence_ids` 过滤。
    """
    indexed = [(i, f) for i, f in enumerate(findings) if not getattr(f, "is_meta", False)]
    meta_count = len(findings) - len(indexed)
    if not indexed:
        return "（本轮暂无可用证据）", []

    # ID 按非自指发现的固定顺序分配（与是否进入本轮截断无关 ⇒ 跨轮稳定）
    ranks = {i: rank for rank, (i, _) in enumerate(indexed)}
    chosen = sorted(
        indexed, key=lambda pair: (-float(pair[1].confidence), pair[0])
    )[: max(0, max_items)]
    chosen.sort(key=lambda pair: pair[0])
    valid_ids = [evidence_id(ranks[i]) for i, _ in chosen]

    by_sq: Dict[str, List[tuple[int, ResearchFinding]]] = {}
    for i, f in chosen:
        by_sq.setdefault(getattr(f, "sq_id", "") or "", []).append((i, f))

    def _render_item(i: int, f: ResearchFinding) -> str:
        content = " ".join(str(getattr(f, "content", "")).split())
        if len(content) > max_chars:
            content = content[:max_chars] + "…"
        return (
            f"[{evidence_id(ranks[i])}] 类型={getattr(f, 'source_type', '')} "
            f"置信度={float(getattr(f, 'confidence', 0.0)):.2f} 来源={getattr(f, 'source', '')}\n"
            f"    {content}"
        )

    lines: List[str] = [
        f"证据清单（共 {len(findings)} 条发现，其中自指/元描述 {meta_count} 条不作为证据；"
        f"本轮列出 {len(chosen)} 条，ID 为稳定引用号）："
    ]
    emitted: List[str] = []
    for sq in subquestions:
        group = by_sq.get(sq.id) or []
        lines.append(f"## 子问题 {sq.id}：{sq.question}")
        if not group:
            lines.append("[缺口] 该子问题当前没有任何可引用证据")
        for i, f in group:
            lines.append(_render_item(i, f))
        emitted.append(sq.id)
    leftover = [
        (i, f)
        for key, group in by_sq.items() if key not in emitted
        for i, f in group
    ]
    if leftover:
        leftover.sort(key=lambda pair: pair[0])
        lines.append("## 未归类材料")
        for i, f in leftover:
            lines.append(_render_item(i, f))
    return "\n".join(lines), valid_ids


def normalize_cited_evidence_ids(ids: Any, valid_ids: List[str]) -> List[str]:
    """过滤 Critic 引用的证据 ID：保序、去重、只保留 digest 中真实存在的 ID。"""
    valid = set(valid_ids)
    out: List[str] = []
    for raw in ids if isinstance(ids, (list, tuple)) else []:
        token = str(raw or "").strip().upper()
        if token in valid and token not in out:
            out.append(token)
    return out


def hard_gate(state: ResearchState, cfg=None) -> Optional[Signal]:
    """确定性硬闸。触发任一资源上限即返回 "stop"，否则 None（继续走 LLM 裁决）。

    F02（frontier 提前停止）：frontier 空**不再**是硬闸——最后一跳之后仍需让
    Critic 看到新证据、有机会生成补充查询。旧行为下「Planner 只给一个子问题」时，
    第一次 research 就消耗唯一种子、frontier 变空，Critic 在调 LLM 前被短路，
    单子问题最多研究一跳。真正「没有新查询可做」的终止由 Critic 语义裁决
    （``stop_reason=no_new_queries``），确定性兜底由下面的资源上限保证。

    两维度：
      - depth ≥ max_total_hops → 全局跳数预算耗尽
      - token_used ≥ token_budget - token_budget_reserve（F13：研究循环预留
        写作/校验额度；绝对预算 `token_budget` 仍是每次调用的准入线）

    P0 profile 固化：`cfg` 缺省时取**本场 run 生效**的研究配置
    （`runtime_profile.effective_research_config()`；无档位时等于全局 config）。
    """
    rc = cfg.research if cfg is not None else effective_research_config()
    if state.depth >= rc.max_total_hops:
        return "stop"
    if state.token_used >= research_token_ceiling(rc):
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
        # 治理事件缓冲（与 Planner 同构）：由 graph 节点 drain 后并入 planner_events 通道。
        self._events: List[Dict[str, Any]] = []

    def drain_events(self) -> List[Dict[str, Any]]:
        """取走并清空 critic 治理事件（非故障，不推导 degraded）。"""
        events = list(self._events)
        self._events = []
        return events

    def decide(self, state: ResearchState, cfg=None) -> Signal:
        gate = hard_gate(state, cfg)
        if gate == "stop":
            state.critic_signal = "stop"
            state.critic_stop_reason = "hard_stop"
            state.critic_gap = ""
            return "stop"
        verdict = self._verdict(state)
        # verdict: {sufficient, needs_replan, knowledge_gap, next_queries, cited_evidence_ids}
        state.sufficient = bool(verdict.get("sufficient", False))
        state.needs_replan = bool(verdict.get("needs_replan", False))
        state.critic_gap = str(verdict.get("knowledge_gap", "")).strip()
        # 需求 16 / bug #75：sq_id 引用完整性——收到即修复（最长前缀归一化 + 治理事件）
        normalized, events = normalize_next_queries(
            list(verdict.get("next_queries", [])),
            [sq.id for sq in state.subquestions],
        )
        self._events.extend(events)
        state.next_queries = normalized
        # F01：裁决引用的证据 ID 必须真实存在于证据清单（清单外的 ID 一律丢弃）
        _, valid_ids = build_evidence_digest(state.findings, state.subquestions)
        state.critic_cited_evidence_ids = normalize_cited_evidence_ids(
            verdict.get("cited_evidence_ids", []), valid_ids,
        )
        state.critic_signal, state.critic_stop_reason = self._resolve_signal(state)
        return state.critic_signal

    def _resolve_signal(self, state: ResearchState) -> tuple[Signal, str]:
        """W7 Arm1：gap 硬规则 + N=6 封顶。返回 (signal, stop_reason)。

        P1 frontier 闭环修复：
        - "augment" = gap 查询回填 frontier（走 revise 节点的 Q2-A 路径）
        - "continue" = 使用现有 frontier 继续（不追加新查询）
        - "revise" = 方向跑偏，需要重规划
        - "stop" = 终止

        F02 修复：frontier 空不再等于停止。预算内允许评估最后一跳并回填查询；
        只有「信息充分」或「无可执行的补充查询（no_new_queries）」才语义收敛。
        此处的所有 "continue" 分支都要求 frontier 非空，保证图不会空转。
        """
        # 方向跑偏优先走 revise（与 gap 规则互不覆盖）
        # P1 max_replan 语义修正：replan 达到上限时不再重规划，转为 stop
        if state.needs_replan:
            if state.replan_count >= effective_research_config().max_replan:
                return "stop", "replan_exhausted"
            return "revise", "revise"

        # W7 Arm1：gap 硬规则可通过 CRITIC_GAP_ENABLED 关闭（TBD-8 基线对照）
        from config import config as _cfg

        frontier_empty = not state.frontier
        gap = state.critic_gap
        has_queries = bool(state.next_queries)
        reflection_count = len(state.reflection_log)

        if not _cfg.experiment.critic_gap_enabled:
            if state.sufficient:
                return "stop", "critic_stop"
            if frontier_empty:
                # F02：仍允许模型给出的补充查询回填；否则明确以「无新查询」收敛
                if has_queries:
                    return "augment", "gap_continue"
                return "stop", "no_new_queries"
            return "continue", "continue"

        # Bug-4 修复：gap 硬规则在 sufficient=True 和 sufficient=False 时都可达
        if state.sufficient:
            # sufficient=True：gap 非空 + next_queries 非空 → 强制回填（防 LLM 早停）
            if not gap or not has_queries:
                if gap and not has_queries:
                    if frontier_empty:
                        return "stop", "no_new_queries"
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
            # 说明模型没指出具体缺口——frontier 还有查询时走 lazy_continue，
            # frontier 已空则不能再 continue（图会空转）⇒ 以 no_new_queries 收敛。
            if not has_queries:
                if frontier_empty:
                    return "stop", "no_new_queries"
                if gap:
                    return "continue", "gap_unresolved_continue"
                return "continue", "lazy_continue"
            # 有 next_queries → 回填 frontier
            # 设计-1 修复：sufficient=False 时也受 MAX_GAP_REFLECTIONS 封顶
            if reflection_count < MAX_GAP_REFLECTIONS:
                return "augment", "gap_continue"
            if frontier_empty:
                return "stop", "gap_reflection_cap"
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
        # F01：把按子问题组织的证据清单（正文+来源+稳定 ID）喂给 Critic；
        # 旧的「只有发现条数」输入使同样条数的相关/无关材料无法区分。
        digest, _valid_ids = build_evidence_digest(state.findings, state.subquestions)
        meta_count = sum(1 for f in state.findings if f.is_meta)
        subs = "; ".join(f"{sq.id}: {sq.question}" for sq in state.subquestions)
        reflection_round = len(state.reflection_log) + 1
        user = (
            f"研究主题：{state.topic}\n"
            f"子问题集合：{subs}\n"
            f"当前已检索跳数：{state.depth}，发现条数：{len(state.findings)}"
            f"（其中自指/元描述 {meta_count} 条不作为证据）\n"
            f"这是第 {reflection_round}/{MAX_GAP_REFLECTIONS} 轮反思。\n\n"
            f"{digest}\n\n"
            f"请基于以上证据正文（而非仅条数）判断：发现是否已充分支撑报告？"
            f"若仍有知识缺口，在 knowledge_gap 中说明并给出 next_queries（带 sq_id）；"
            f"若方向跑偏，needs_replan=true；"
            f"并在 cited_evidence_ids 中引用支撑你判断的关键证据 ID（清单之外不得编造）；"
            f"若证据互相矛盾，请在 knowledge_gap 中指出并引用冲突双方 ID。"
        )
        # W4 Q7：裁决归位 critic_model（修复 W3 前硬编码 smart_model 的现状 bug——裁决属 strategic 层）
        # W5（Q2）：role="critic" 进职责桶
        client = LLMClient(model=effective_llm_model("critic"), role="critic")
        return client.chat_json(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            state=state,  # Q6-B：让 critic 的 LLM token 用量也累加进硬闸
            schema=CriticVerdict,  # 漏 key/类型错 → ValidationError → 自动纠错重试，不再静默默认
        )
