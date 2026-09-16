"""混合检索：向量检索 + BM25 关键词检索，结果融合。

借鉴 wechatbot 的混合检索经验。rerank 默认关闭，先评测再决定去留。
Qdrant 不可用时优雅降级（返回空，不影响网络搜索主链路）。
"""
from __future__ import annotations

from typing import List

from openai import OpenAI
from rank_bm25 import BM25Okapi

from config import config
from research_engine.failure_reasons import FailureReason
from research_engine.rag.response import BackendFailure, RetrieveResponse
from research_engine.rag.store import VectorStore
from research_engine.rag.tokenizer import tokenize


class HybridRetriever:
    """混合检索器（向量 + BM25）。"""

    def __init__(self):
        self.store = VectorStore()
        self._client: OpenAI | None = None
        # 缓存所有文档块用于 BM25（简单实现，数据量小时够用）
        self._all_texts: List[str] = []
        self._all_sources: List[str] = []  # 与 _all_texts 对齐的文档身份（文件名），供 RAG source 去重
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
        """从 Qdrant 加载全部文档块文本（用于 BM25），并同步记录每块的文档身份。"""
        if self._all_texts:
            return self._all_texts
        payloads = self.store.scroll_all()
        kept = [p for p in payloads if p.get("text")]
        self._all_texts = [p["text"] for p in kept]
        self._all_sources = [p.get("source", "") for p in kept]
        if self._all_texts:
            self._bm25 = BM25Okapi([tokenize(t) for t in self._all_texts])
        return self._all_texts

    def embed_query(self, query: str) -> List[float]:
        resp = self._get_client().embeddings.create(
            model=config.rag.embedding_model,
            input=[query],
        )
        return resp.data[0].embedding

    def retrieve(self, query: str, top_k: int | None = None) -> RetrieveResponse:
        """混合检索，返回 :class:`RetrieveResponse`（W8 Arm 4，决策 D-02）。

        ``resp.items`` 元素同构于改造前的 ``[{text, score, source, doc}]``；
        ``doc`` = 文档身份（payload.source 文件名），供上层 ``rag:<filename>`` 去重。

        ⚠️ 改造前本方法返回裸 ``List[dict]``，于是 ``[]`` 同时表达了「确实没命中 /
        未配置 / 向量库不可用 / embedding 失败 / 部分 backend 失败」六种事实 ——
        **信息在到达 researcher 前就已丢失**，故障不可归因。现在按 backend 分别留痕。
        """
        top_k = top_k or config.rag.top_k
        resp = RetrieveResponse(query=query)

        # ---- backend 1：向量检索（依赖 embedding + Qdrant）----
        vec_hits: List[dict] = []
        if not config.llm.api_key:
            resp.note_backend_failure(
                "vector", FailureReason.NOT_CONFIGURED.value, "未配置 DASHSCOPE_API_KEY，无法调用 Embedding"
            )
        else:
            try:
                vec = self.embed_query(query)
                vec_hits = self.store.search(vec, top_k=top_k * 2)
                reason = self.store.unavailable_reason
                if reason:
                    # Qdrant 不可用 ⇒ search() 静默返 []，这里把事实补回来
                    resp.note_backend_failure("vector", reason, self.store.last_error or "")
                    vec_hits = []
            except Exception as e:  # noqa: BLE001 — embedding / 查询异常，保住 BM25 那一路
                resp.note_backend_failure("vector", FailureReason.PROVIDER_ERROR.value, str(e)[:300])
                vec_hits = []

        # ---- backend 2：BM25 检索（依赖 Qdrant scroll，不依赖 embedding）----
        bm25_hits: List[dict] = []
        try:
            texts = self._load_all_texts()
            reason = self.store.unavailable_reason
            if reason:
                resp.note_backend_failure("bm25", reason, self.store.last_error or "")
                texts = []
            if self._bm25 and texts:
                scores = self._bm25.get_scores(tokenize(query))
                ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
                for idx in ranked[:top_k]:
                    if scores[idx] > 0:
                        bm25_hits.append({
                            "text": texts[idx],
                            "score": float(scores[idx]),
                            "source": "bm25",
                            "doc": self._all_sources[idx],
                        })
        except Exception as e:  # noqa: BLE001
            resp.note_backend_failure("bm25", FailureReason.PROVIDER_ERROR.value, str(e)[:300])
            bm25_hits = []

        # ---- 融合（简单加权，向量为主）----
        merged: List[dict] = []
        seen = set()
        for h in vec_hits:
            text = h["payload"].get("text", "")
            if text and text not in seen:
                seen.add(text)
                merged.append({
                    "text": text,
                    "score": h["score"],
                    "source": "vector",
                    "doc": h["payload"].get("source", ""),
                })
        for h in bm25_hits:
            if h["text"] not in seen:
                seen.add(h["text"])
                merged.append(h)
        resp.items = merged[:top_k]

        # ---- 整体判定 ----
        if not resp.items:
            if resp.backend_failures:
                # 所有可用 backend 都挂了（或唯一那一路挂了）⇒ 整体故障，取最具处置价值的原因
                resp.failure_reason = _worst_reason(resp.backend_failures)
                resp.failure_detail = "; ".join(
                    f"{bf.backend}: {bf.detail}" for bf in resp.backend_failures if bf.detail
                )[:300]
            else:
                # 两路都正常执行了，只是确实没命中 —— 是结果，不是故障（D-03 不上抛降级）
                resp.failure_reason = FailureReason.EMPTY_RESULT.value
        # 有 items 但部分 backend 失败 ⇒ failure_reason 保持 None，失败事实留在 backend_failures
        return resp


#: 聚合多个 backend 失败原因时的优先级（越靠前越具处置价值）
_REASON_PRIORITY = (
    FailureReason.NOT_CONFIGURED.value,
    FailureReason.PARSE_ERROR.value,
    FailureReason.TIMEOUT.value,
    FailureReason.PROVIDER_ERROR.value,
    FailureReason.EMPTY_RESULT.value,
)


def _worst_reason(failures: List[BackendFailure]) -> str:
    """按处置价值选一个代表原因（确定性，不依赖 backend 顺序）。"""
    for r in _REASON_PRIORITY:
        if any(bf.reason == r for bf in failures):
            return r
    return failures[0].reason
