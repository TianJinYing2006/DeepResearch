# -*- coding: utf-8 -*-
"""评测 A：检索命中率评测。

复用 wechatbot 的评测方法论：真实查询 + 命中率评测。
给定一组查询和对应的 ground truth 文档，评测 top-k 命中率。
"""
from __future__ import annotations

from typing import List, Tuple

from research_engine.rag.retriever import HybridRetriever


class RetrievalEvaluator:
    """检索命中率评测器。"""

    def __init__(self):
        self.retriever = HybridRetriever()

    def evaluate(
        self,
        queries: List[Tuple[str, List[str]]],
        top_k: int = 5,
    ) -> dict:
        """评测检索命中率。

        queries: [(查询, [期望命中的文档关键词列表])]
        返回命中率统计。
        """
        total = len(queries)
        hit_count = 0
        per_query = []

        for query, expected_keywords in queries:
            hits = self.retriever.retrieve(query, top_k=top_k)
            hit_texts = " ".join(h["text"] for h in hits)
            hit = any(kw in hit_texts for kw in expected_keywords)
            if hit:
                hit_count += 1
            per_query.append({"query": query, "hit": hit, "top_k": top_k})

        return {
            "total": total,
            "hit": hit_count,
            "hit_rate": round(hit_count / total, 4) if total else 0,
            "per_query": per_query,
        }
