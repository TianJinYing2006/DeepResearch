# -*- coding: utf-8 -*-
"""LangGraph 状态机编排。

将 Planner / Researcher / Writer / Validator 串成完整研究流程。
借鉴 LangChain open_deep_research 的图编排思路。
"""
from __future__ import annotations

from typing import Any, Dict

from langgraph.graph import END, StateGraph

from research_engine.agents.planner import Planner
from research_engine.agents.researcher import Researcher
from research_engine.agents.validator import Validator
from research_engine.agents.writer import Writer
from research_engine.state import ResearchState


class DeepResearchGraph:
    """深度研究图。"""

    def __init__(self):
        self.planner = Planner()
        self.researcher = Researcher()
        self.writer = Writer()
        self.validator = Validator()
        self.graph = self._build()

    def _build(self):
        g = StateGraph(ResearchState)

        g.add_node("plan", self._plan)
        g.add_node("research", self._research)
        g.add_node("write", self._write)
        g.add_node("validate", self._validate)

        g.set_entry_point("plan")
        g.add_edge("plan", "research")
        g.add_edge("research", "write")
        g.add_edge("write", "validate")
        g.add_edge("validate", END)

        return g.compile()

    # ---- 节点实现 ----
    def _plan(self, state: ResearchState) -> Dict[str, Any]:
        state.status = "planning"
        state.progress.append({"stage": "plan", "msg": "正在分解研究问题..."})
        subs = self.planner.plan(state.topic, state.user_instructions)
        state.subquestions = subs
        state.progress.append({"stage": "plan", "msg": f"已分解为 {len(subs)} 个子问题"})
        return {"subquestions": subs, "status": "planning", "progress": state.progress}

    def _research(self, state: ResearchState) -> Dict[str, Any]:
        state.status = "researching"
        state.progress.append({"stage": "research", "msg": "开始多跳检索（网络 + RAG）..."})
        findings = self.researcher.research(state.subquestions)
        state.findings = findings
        state.progress.append({"stage": "research", "msg": f"检索完成，共 {len(findings)} 条发现"})
        return {"findings": findings, "status": "researching", "progress": state.progress}

    def _write(self, state: ResearchState) -> Dict[str, Any]:
        state.status = "writing"
        state.progress.append({"stage": "write", "msg": "正在生成研究报告..."})
        report = self.writer.write(state.topic, state.subquestions, state.findings)
        state.report = report
        state.progress.append({"stage": "write", "msg": "报告生成完成"})
        return {"report": report, "status": "writing", "progress": state.progress}

    def _validate(self, state: ResearchState) -> Dict[str, Any]:
        state.status = "validating"
        state.progress.append({"stage": "validate", "msg": "正在校验引用与多源印证..."})
        citations = self.validator.validate(state.report, state.findings)
        state.citations = citations
        verified = sum(1 for c in citations if c.verified)
        state.progress.append(
            {"stage": "validate", "msg": f"校验完成：{verified}/{len(citations)} 条引用通过存在性校验"}
        )
        state.status = "done"
        return {"citations": citations, "status": "done", "progress": state.progress}

    def run(self, topic: str, user_instructions: str = "") -> ResearchState:
        """运行完整研究流程。"""
        initial = ResearchState(topic=topic, user_instructions=user_instructions)
        result = self.graph.invoke(initial)
        # LangGraph invoke 返回 dict，转回 ResearchState
        if isinstance(result, dict):
            return ResearchState(**result)
        return result


def create_graph() -> DeepResearchGraph:
    return DeepResearchGraph()
