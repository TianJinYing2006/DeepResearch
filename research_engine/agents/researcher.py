# -*- coding: utf-8 -*-
"""Researcher Agent：单跳检索（网络 + RAG）。

W1 重构（grill Q1/Q8）：把"多跳循环"从 researcher 内部的 while 提到图层级。
本类只负责**一跳**检索（search_once），由图的 research↔critic 条件边驱动多跳。
- 去重：基于 state.visited_sources（Q8 启用 dead code，避免重复拉同一来源，省 token/跳数）。
- 充分度判断已移至 critic 节点（grill Q3），这里不再做 LLM 充分度裁决。
"""
from __future__ import annotations

from typing import List, Any

from config import config
from research_engine.rag.retriever import HybridRetriever
from research_engine.search.base import SearchProvider, create_search_provider
from research_engine.state import ResearchFinding

# R2.4 Q5=A 第一层：自指/元描述关键词启发式初标（漏标由 validator verdict 兜底复核）
META_KEYWORDS = (
    "deepresearch", "本系统", "本 agent", "本agent", "本工具",
    "planner→researcher→writer→validator", "四节点编排",
    "该agent", "该 agent", "本 架构",
)


def _is_meta_content(content: str) -> bool:
    """判定 finding 内容是否为自指/元描述（描述 DeepResearch 系统自身）。"""
    c = content.lower()
    return any(k in c for k in META_KEYWORDS)


class Researcher:
    """单跳检索器。"""

    def __init__(self):
        self.search: SearchProvider = create_search_provider(config.search.provider)
        self.retriever = HybridRetriever()

    def _search_web(self, query: str) -> List[ResearchFinding]:
        """网络搜索，返回研究发现。"""
        try:
            resp = self.search.search(query, max_results=config.search.max_results)
            findings = []
            for r in resp.results:
                content = f"{r.title}\n{r.snippet}"
                findings.append(
                    ResearchFinding(
                        content=content,
                        source=r.url,
                        source_type="web",
                        confidence=0.6,
                        is_meta=_is_meta_content(content),  # R2.4 Q5=A 第一层初标
                    )
                )
            return findings
        except Exception:  # noqa: BLE001
            return []

    def _search_rag(self, query: str) -> List[ResearchFinding]:
        """RAG 知识库检索，返回研究发现。

        去重契约（Q8）：source 必须按文档身份 `rag:<filename>` 取值，
        否则全部命中共用 rag:vector/rag:bm25 会被 visited_sources 去重饿死到 ≤2 条。
        """
        try:
            hits = self.retriever.retrieve(query, top_k=config.rag.top_k)
            findings = []
            for h in hits:
                doc = h.get("doc") or h.get("source") or "unknown"
                text = h["text"]
                findings.append(
                    ResearchFinding(
                        content=text,
                        source=f"rag:{doc}",
                        source_type="rag",
                        confidence=0.7,
                        is_meta=_is_meta_content(text),  # R2.4 Q5=A 第一层初标
                    )
                )
            return findings
        except Exception:  # noqa: BLE001
            return []

    def search_once(self, query: str, state: Any) -> List[ResearchFinding]:
        """对单个查询做一跳检索（网络 + RAG），基于 state.visited_sources 去重后返回新发现。

        纯函数式：不修改 state，由调用方（图的 research 节点）把结果写回 state。
        """
        new_findings = self._search_web(query) + self._search_rag(query)
        seen = set(getattr(state, "visited_sources", []) or [])
        added: List[ResearchFinding] = []
        for f in new_findings:
            if f.source not in seen:
                seen.add(f.source)
                added.append(f)
        return added
