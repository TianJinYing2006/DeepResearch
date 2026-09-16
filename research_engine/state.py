"""LangGraph 状态定义。

使用 Pydantic 类型化状态，借鉴 LangChain open_deep_research 的 AgentState 设计。
"""
from __future__ import annotations

import operator
import threading
import time
from dataclasses import dataclass, field
from typing import Annotated, Any, Dict, List, Optional

from pydantic import BaseModel, Field

# W8 Arm 1（Q4 拍板）：三态 —— 原四态里的 `partial` 与 `degraded` 判定条件完全相同
# （"tracker 非空" ⇔ "部分节点失败"），无任何观测量能分开 ⇒ `partial` 移出 state，
# 降为报告层派生指标（按子问题覆盖率标注）。
# 行业取证：OpenAI Responses API 的 ResponseStatus 终态恰为 3 个，无 `partial`（见 §6.6）。
RUN_STATUS_SUCCESS = "success"  # 全链路无降级
RUN_STATUS_DEGRADED = "degraded"  # 走过 fallback 但最终产出了报告
RUN_STATUS_FAILED = "failed"  # 无报告产出（含 run() 层捕获到异常）
RUN_STATUSES = (RUN_STATUS_SUCCESS, RUN_STATUS_DEGRADED, RUN_STATUS_FAILED)


@dataclass
class DegradationEntry:
    """一次降级的记录（W8 Arm 1 / §5.1.2）。

    `reason` **必须是** :mod:`research_engine.failure_reasons` 枚举表内的值；
    工具层产生的降级须由 ``SearchResponse.failure_reason`` **单向派生**，
    **禁止在本处手写第二个字面量**（Q8 B4 契约 —— 违反则两处描述会静默分叉）。
    """

    node: str  # "planner" / "researcher" / "writer" / "validator"
    component: str  # "llm" / "web_search" / "rag_search" / "arxiv_search" / "code_exec"
    reason: str  # 失败原因枚举，见 failure_reasons.FailureReason
    detail: str = ""  # 自由文本补充（原始异常摘要等），可为空
    fallback_action: str = ""  # "topic_only" / "empty_list" / "fallback_report" / "existence_only"
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        """序列化为普通 dict（写入 state / 落盘 JSON 用）。"""
        return {
            "node": self.node,
            "component": self.component,
            "reason": self.reason,
            "detail": self.detail,
            "fallback_action": self.fallback_action,
            "timestamp": self.timestamp,
        }


class DegradationSink:
    """降级记录缓冲区（线程安全），供各 Agent 在 fallback 处留痕。

    为什么需要它：Agent 的 ``except`` 分支通常只返回降级结果（空列表 / 兜底报告），
    **拿不到也不该直接改** LangGraph 的 state。故先缓存在 Agent 上，
    由 graph 节点调用 :meth:`drain_degradations` 取出，经 ``degradation_log`` 的
    ``operator.add`` reducer 入 state（节点只 return 增量 ⇒ 并发安全）。

    用锁是因为 ``_search_*`` 在 ``ThreadPoolExecutor`` 里并发执行。
    """

    def __init__(self) -> None:
        self._degradations: List[DegradationEntry] = []
        self._deg_lock = threading.Lock()

    def _record_degradation(
        self,
        component: str,
        reason: str,
        detail: str = "",
        fallback_action: str = "empty_list",
        node: str = "",
    ) -> None:
        """记一条降级。`reason` 必须来自 `failure_reasons` 枚举。"""
        with self._deg_lock:
            self._degradations.append(
                DegradationEntry(
                    node=node or type(self).__name__.lower(),
                    component=component,
                    reason=reason,
                    detail=(detail or "")[:300],
                    fallback_action=fallback_action,
                )
            )

    def drain_degradations(self) -> List[DegradationEntry]:
        """取走并清空（graph 节点调用）。"""
        with self._deg_lock:
            out = list(self._degradations)
            self._degradations = []
        return out


class SubQuestion(BaseModel):
    """Planner 分解出的子问题。"""
    id: str = Field(description="子问题 ID")
    question: str = Field(description="子问题内容")
    rationale: str = Field(description="为什么需要研究这个子问题")


class ResearchFinding(BaseModel):
    """单条研究发现（带来源）。"""
    content: str = Field(description="研究发现内容")
    source: str = Field(description="来源（URL 或文档 ID 或 code:hash）")
    source_type: str = Field(description="来源类型：web / rag / arxiv / code_exec")
    confidence: float = Field(default=0.5, description="置信度 0-1")
    is_meta: bool = Field(default=False, description="是否自指/元描述（R2.4：描述本系统自身），绝不作为正文证据")
    sq_id: str = Field(default="", description="W7 Arm4 G1：所属子问题 ID，用于 writer 分节喂料")
    # W4 Q3（统一证据抽象）：富元数据桶——arxiv: {arxiv_id, primary_category, citation_count, structured_match}；
    #                    code_exec: {retry_history?} / 通用 {retry_history, structured_match}
    metadata: dict = Field(default_factory=dict, description="W4 扩展元数据（结构化保留，供呈现层展示，LLM 校验不消费）")


class Citation(BaseModel):
    """报告中的一条引用。

    W2 扩展（grill Q2/Q3/Q6）：
    - finding_id：citation↔finding 锚点（URL 协议引用可为空）
    - source_type/confidence/note：来源类型、校验置信度、失败原因（不再丢弃）
    - existence：本地"来源存在性"判定（双口径之一）；verified = existence AND 忠实度（Q3=A）
    """
    claim: str = Field(description="论断")
    source: str = Field(description="引用来源")
    verified: bool = Field(default=False, description="是否通过校验：来源存在 且 论断忠实（R2.3）")
    supported: bool = Field(default=False, description="是否通过多源印证")
    finding_id: str = Field(default="", description="关联 finding 编号（grill Q2=A），URL 协议引用可为空")
    source_type: str = Field(default="", description="来源类型：web / rag（grill Q2=A）")
    confidence: float = Field(default=0.0, description="LLM 校验置信度 0-1（grill Q3=A，不再丢弃）")
    note: str = Field(default="", description="校验说明/失败原因（grill Q3=A，不再丢弃）")
    existence: bool = Field(default=False, description="本地来源存在性判定（grill Q6=A 双口径之一）")
    verified_relaxed: bool = Field(default=False, description="W7 宽松口径：existence AND (faithful OR supported)，仅呈现不改动 verified 语义")
    is_meta: bool = Field(default=False, description="自指/元描述复核结果（R2.4 Q5=A：validator verdict 兜底）")


class ResearchState(BaseModel):
    """研究流程的全局状态。"""
    # 输入
    topic: str = Field(description="研究主题")
    user_instructions: str = Field(default="", description="用户附加要求")

    # 规划
    subquestions: List[SubQuestion] = Field(default_factory=list)

    # 检索
    findings: List[ResearchFinding] = Field(default_factory=list)
    visited_sources: List[str] = Field(default_factory=list, description="已访问来源，去重（Q8 启用）")

    # ---- W1 新增：循环 / 硬闸状态（呼应 grill Q1/Q3/Q5/Q6）----
    frontier: List[Dict[str, Any]] = Field(default_factory=list, description="全局待检索队列，元素 {sq_id, query}")
    depth: int = Field(default=0, description="已消耗总跳数")
    per_subq_hop: Dict[str, int] = Field(default_factory=dict, description="每子问题已消耗跳数，防 starvation（Q5）")
    token_used: int = Field(default=0, description="LLM token 累计消耗（Q6 观测+控闸）")
    replan_count: int = Field(default=0, description="已触发 replan 次数（Q2-B 兜底）")
    # critic 节点的结构化输出，供路由函数纯函数读取（Q3 分层）
    critic_signal: str = Field(default="", description="条件边路由信号：continue/revise/stop")
    sufficient: bool = Field(default=False, description="critic 判研究是否充分")
    needs_replan: bool = Field(default=False, description="critic 判是否需要重分解")
    next_queries: List[Dict[str, Any]] = Field(default_factory=list, description="critic 产出的新查询，回填 frontier（Q2=A）")
    critic_gap: str = Field(default="", description="W7 Arm1：critic 识别的知识缺口文本")
    critic_stop_reason: str = Field(default="", description="W7 Arm1：本轮 critic 停止原因（hard_stop/gap_unresolved/no_next_queries/critic_stop/continue/gap_continue/revise）")
    # Q7=A：纯追加日志用 add reducer，节点只 return delta，避免 checkpointer 重放错位
    reflection_log: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list, description="反思日志，纯追加（Q7 add reducer）")

    # 报告
    report: str = Field(default="", description="最终报告（Writer 原始输出，编号协议不变）")
    report_display: str = Field(default="", description="渲染后报告（R2.1/2.2/2.5：类型标注+⚠️+附录+溯源块，由 render 节点产出，不回流 report）")
    citations: List[Citation] = Field(default_factory=list)
    validator_stats: Dict[str, Any] = Field(default_factory=dict, description="Validator extraction/denominator accounting")

    # 过程追踪（用于 Web UI 实时展示）—— Q7=A：add reducer，节点只 return 本步新增条目
    progress: Annotated[List[Dict[str, Any]], operator.add] = Field(default_factory=list)
    status: str = Field(default="pending", description="pending/planning/researching/writing/validating/done/failed")

    # ---- W8 Arm 1（Q4 拍板）：流程健康度 + 降级审计 ----
    # 命名三分（Q3 甲 + Q4）：run_status=流程健康度（本字段）/ invoke_status=_run_one 执行结果
    # / metrics_status=phase2 指标齐备性 —— 三者互不相犯，勿混用。
    run_status: str = Field(
        default=RUN_STATUS_SUCCESS,
        description="流程健康度：success/degraded/failed（三态，无 partial；见 §5.1.1）",
    )
    # 必须带 operator.add reducer（照抄上方 progress 写法）—— 不加会在 asyncio.gather
    # 并发下**丢记录**（LangGraph 对无 reducer 字段取覆盖语义）。
    degradation_log: Annotated[List[DegradationEntry], operator.add] = Field(
        default_factory=list,
        description="降级审计流，run 级追加（对应 OTel add_event；单值对照是 SearchResponse.failure_reason）",
    )
    # Q4 优化点 ③：由 Optional[str] 改结构化，对齐 OpenAI error{code,message}。
    # ⚠️ 语义红线：成功时 None、失败时非 None —— eval/metrics.py:45 的
    # `ok_error = state.get("error") is None` 是**完成率四条件之一**，依赖这个语义。
    error: Optional[Dict[str, Any]] = Field(
        default=None,
        description="结构化错误 {code: 失败原因枚举, message: str, node: Optional[str]}",
    )

    def add_degradation(
        self,
        node: str,
        component: str,
        reason: str,
        detail: str = "",
        fallback_action: str = "",
    ) -> DegradationEntry:
        """追加一条降级记录（Arm 1 各 fallback 落点统一走这里）。

        `reason` 必须是 `failure_reasons.FailureReason` 内的值；工具层降级请传
        `resp.failure_reason`（单向派生），不要在调用处手写第二个字面量。
        """
        entry = DegradationEntry(
            node=node,
            component=component,
            reason=reason,
            detail=detail,
            fallback_action=fallback_action,
        )
        # 注意：本方法**直接改 self**，适用于 run() 异常路径等拿到 state 对象后
        # 无法再走 reducer 的场景；图内节点仍应 `return {"degradation_log": [entry]}`
        # 让 reducer 生效（并发安全）。
        self.degradation_log = list(self.degradation_log) + [entry]
        return entry

    def set_error(self, code: str, message: str, node: Optional[str] = None) -> Dict[str, Any]:
        """写结构化 error，并同步把 run_status 置 failed。"""
        self.error = {"code": code, "message": message, "node": node}
        self.run_status = RUN_STATUS_FAILED
        return self.error

    def resolve_run_status(self, has_report: bool) -> str:
        """按 §5.1.2 判定规则由 degradation_log 推导 run_status。

        * tracker 为空              → success
        * tracker 非空 + 有报告      → degraded
        * 无报告                    → failed
        """
        if not self.degradation_log:
            return RUN_STATUS_SUCCESS
        return RUN_STATUS_DEGRADED if has_report else RUN_STATUS_FAILED
