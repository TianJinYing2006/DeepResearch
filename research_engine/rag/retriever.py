# -*- coding: utf-8 -*-
"""混合检索：向量检索 + BM25 关键词检索，结果融合。

借鉴 wechatbot 的混合检索经验。rerank 默认关闭，先评测再决定去留。
Qdrant 不可用时优雅降级（返回空，不影响网络搜索主链路）。
"""
from __future__ import annotations

from typing import List

from openai import OpenAI
from rank_bm25 import BM25Okapi

from config import config
from research_engine.rag.store import VectorStore
from research_engine.rag.tokenizer import tokenize


class HybridRetriever:
    """混合检索器（向量 + BM25）。"""

    def __init__(self):
        self.store = VectorStore()
        self._client: OpenAI | None = None
        # 缓存所有文档块用于 BM25（简单实现，数据量小时够用）
        self._all_texts: List[str] = []
        self._bm25: BM25Okapi | None = None

    def _get_client(self) -> OpenAI:
        """懒加载 embedding 客户端。"""
        if self._client is None:
            if not config.llm.api_key:
                raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用 Embedding")
            self._client = OpenAI(
                base_url=config.llm.base_url,
                api_key=config.llm.api_key,
            )
        return self._client

    def _load_all_texts(self) -> List[str]:
        """从 Qdrant 加载全部文档块文本（用于 BM25）。"""
        if self._all_texts:
            return self._all_texts
        payloads = self.store.scroll_all()
        self._all_texts = [p.get("text", "") for p in payloads if p.get("text")]
        if self._all_texts:
            self._bm25 = BM25Okapi([tokenize(t) for t in self._all_texts])
        return self._all_texts

    def embed_query(self, query: str) -> List[float]:
        resp = self._get_client().embeddings.create(
            model=config.rag.embedding_model,
            input=[query],
        )
        return resp.data[0].embedding

    def retrieve(self, query: str, top_k: int | None = None) -> List[dict]:
        """混合检索，返回 [{text, score, source}]。Qdrant 不可用时返回空。"""
        top_k = top_k or config.rag.top_k
        # 向量检索
        vec = self.embed_query(query)
        vec_hits = self.store.search(vec, top_k=top_k * 2)

        # BM25 检索
        texts = self._load_all_texts()
        bm25_hits = []
        if self._bm25 and texts:
            scores = self._bm25.get_scores(tokenize(query))
            ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
            for idx in ranked[:top_k]:
                if scores[idx] > 0:
                    bm25_hits.append({"text": texts[idx], "score": float(scores[idx]), "source": "bm25"})

        # 融合（简单加权，向量为主）
        merged: List[dict] = []
        seen = set()
        for h in vec_hits:
            text = h["payload"].get("text", "")
            if text and text not in seen:
                seen.add(text)
                merged.append({"text": text, "score": h["score"], "source": "vector"})
        for h in bm25_hits:
            if h["text"] not in seen:
                seen.add(h["text"])
                merged.append(h)

        return merged[:top_k]
