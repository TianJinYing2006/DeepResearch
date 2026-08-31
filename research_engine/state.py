# -*- coding: utf-8 -*-
"""LangGraph 状态定义。

使用 Pydantic 类型化状态，借鉴 LangChain open_deep_research 的 AgentState 设计。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class SubQuestion(BaseModel):
    """Planner 分解出的子问题。"""
    id: str = Field(description="子问题 ID")
    question: str = Field(description="子问题内容")
    rationale: str = Field(description="为什么需要研究这个子问题")


class ResearchFinding(BaseModel):
    """单条研究发现（带来源）。"""
    content: str = Field(description="研究发现内容")
    source: str = Field(description="来源（URL 或文档 ID）")
    source_type: str = Field(description="来源类型：web / rag")
    confidence: float = Field(default=0.5, description="置信度 0-1")


class Citation(BaseModel):
    """报告中的一条引用。"""
    claim: str = Field(description="论断")
    source: str = Field(description="引用来源")
    verified: bool = Field(default=False, description="是否通过存在性校验")
    supported: bool = Field(default=False, description="是否通过多源印证")


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
    reflection_log: List[Dict[str, Any]] = Field(default_factory=list, description="反思日志，纯追加（Q7）")

    # 报告
    report: str = Field(default="", description="最终报告")
    citations: List[Citation] = Field(default_factory=list)

    # 过程追踪（用于 Web UI 实时展示）
    progress: List[Dict[str, Any]] = Field(default_factory=list)
    status: str = Field(default="pending", description="pending/planning/researching/writing/validating/done/failed")
    error: Optional[str] = None
