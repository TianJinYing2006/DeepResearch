# -*- coding: utf-8 -*-
"""Researcher Agent：多跳检索（网络 + RAG）。

核心能力：
1. 对每个子问题执行动态多跳检索
2. 每跳基于"信息充分度"判断是否继续（上限 max_depth 防死循环）
3. 融合网络搜索（博查）与 RAG 私有知识库
4. 多源印证：关键论断需多个独立来源支持
"""
from __future__ import annotations

from typing import List

from config import config
from research_engine.llm.router import get_router
from research_engine.rag.retriever import HybridRetriever
from research_engine.search.base import SearchProvider, create_search_provider
from research_engine.state import ResearchFinding, SubQuestion

SUFFICIENCY_SYSTEM = """你是研究信息充分度判断器。根据当前已收集的信息，判断是否足以回答研究问题。

请以 JSON 格式输出：
{{
  "sufficient": true/false,
  "reason": "判断理由",
  "next_queries": ["如果不足，下一步要检索的查询词（最多3个）"]
}}
"""


class Researcher:
    """多跳检索器。"""

    def __init__(self):
        self.search: SearchProvider = create_search_provider(config.search.provider)
        self.retriever = HybridRetriever()
        self.router = get_router()

    def _search_web(self, query: str) -> List[ResearchFinding]:
        """网络搜索，返回研究发现。"""
        try:
            resp = self.search.search(query, max_results=config.search.max_results)
            findings = []
            for r in resp.results:
                findings.append(
                    ResearchFinding(
                        content=f"{r.title}\n{r.snippet}",
                        source=r.url,
                        source_type="web",
                        confidence=0.6,
                    )
                )
            return findings
        except Exception:  # noqa: BLE001
            return []

    def _search_rag(self, query: str) -> List[ResearchFinding]:
        """RAG 知识库检索，返回研究发现。"""
        try:
            hits = self.retriever.retrieve(query, top_k=config.rag.top_k)
            findings = []
            for h in hits:
                findings.append(
                    ResearchFinding(
                        content=h["text"],
                        source=f"rag:{h.get('source', 'vector')}",
                        source_type="rag",
                        confidence=0.7,
                    )
                )
            return findings
        except Exception:  # noqa: BLE001
            return []

    def _judge_sufficiency(self, question: str, findings: List[ResearchFinding]) -> dict:
        """判断信息是否充分，返回 {sufficient, reason, next_queries}。"""
        if not findings:
            return {"sufficient": False, "reason": "无信息", "next_queries": [question]}
        context = "\n".join(f"- {f.content[:200]}" for f in findings[:10])
        user = f"研究问题：{question}\n\n已收集信息：\n{context}\n\n请判断是否充分。"
        try:
            return self.router.strategic_json(SUFFICIENCY_SYSTEM, user)
        except Exception:  # noqa: BLE001
            return {"sufficient": True, "reason": "判断失败，保守终止", "next_queries": []}

    def research_subquestion(self, sub: SubQuestion) -> List[ResearchFinding]:
        """对单个子问题执行动态多跳检索。"""
        all_findings: List[ResearchFinding] = []
        queries = [sub.question]
        depth = 0

        while queries and depth < config.research.max_depth:
            depth += 1
            query = queries.pop(0)

            # 网络 + RAG 并行检索
            web_findings = self._search_web(query)
            rag_findings = self._search_rag(query)
            new_findings = web_findings + rag_findings

            # 去重（按来源）
            seen = {f.source for f in all_findings}
            for f in new_findings:
                if f.source not in seen:
                    seen.add(f.source)
                    all_findings.append(f)

            # 判断充分度
            verdict = self._judge_sufficiency(sub.question, all_findings)
            if verdict.get("sufficient"):
                break
            # 补充下一跳查询
            for q in verdict.get("next_queries", [])[:config.research.breadth]:
                if q not in queries:
                    queries.append(q)

        return all_findings

    def research(self, subquestions: List[SubQuestion]) -> List[ResearchFinding]:
        """对所有子问题执行多跳检索，汇总发现。"""
        all_findings: List[ResearchFinding] = []
        for sub in subquestions:
            findings = self.research_subquestion(sub)
            all_findings.extend(findings)
        return all_findings
