"""混合检索 v2（需求 23 §5）：双路召回 + RRF 身份融合 + 证据组装 + 可选 rerank。

与 v1 的差异（对应设计文档 §2.2 F1/F4/F5 三条现状事实）：

- **融合修复**：v1 是「向量优先、BM25 垫底截断」（BM25 实际不生效）；
  v2 双路各召回 ``recall_per_route``（默认 20）后做 **RRF**（k=60 初值，
  由评测调整），参数不写成固定最优；
- **身份契约**：融合与去重以 ``chunk_id`` 为准（新点携带；历史点回退
  ``legacy:<doc_id>:<text hash>``），**不按文本去重** —— 同文多出处保留来源；
- **缓存一致性**：BM25 语料缓存键含 ``(scope, revision)``；任何写路径递增
  revision 即整体失效（「删除后不可从缓存命中」）；
- **证据组装**（检索 ≠ 直接入 prompt）：同文档上限 + 同文档包含关系去重 +
  token 预算，最终取 ``top_k``；
- **rerank 可选**（默认关，评测决定）：fail-open 回落 RRF 顺序，失败不进降级日志。

多租户隔离语义不变（scope 服务端下推 + Python 后置过滤，唯一实现在 scope.py）。
"""
from __future__ import annotations

import hashlib
from typing import Dict, List

from openai import OpenAI
from rank_bm25 import BM25Okapi

from config import config
from research_engine.failure_reasons import FailureReason
from research_engine.rag.chunker_v2 import estimate_tokens
from research_engine.rag.rerank import rerank as _rerank_call
from research_engine.rag.response import BackendFailure, RetrieveResponse
from research_engine.rag.scope import RagScope, current_scope
from research_engine.rag.store import VectorStore
from research_engine.rag.tokenizer import tokenize
from research_engine.sanitize import strip_invisible
from research_engine.usage import UsageRecord, emit_usage


def _legacy_id(doc_id: str, text: str) -> str:
    digest = hashlib.sha1(text.encode("utf-8")).hexdigest()[:10]
    return f"legacy:{doc_id}:{digest}"


def _identity(payload: dict, text: str) -> str:
    chunk_id = payload.get("chunk_id")
    if chunk_id:
        return str(chunk_id)
    doc_id = str(payload.get("doc_id") or payload.get("source", ""))
    return _legacy_id(doc_id, text)


def _entry(payload: dict, text: str, score: float, source: str) -> dict:
    return {
        "text": text,
        "score": float(score),
        "source": source,
        "doc": payload.get("source", ""),
        "doc_id": payload.get("doc_id") or payload.get("source", ""),
        "chunk_id": _identity(payload, text),
        "generation": payload.get("generation"),
        "locator": payload.get("locator") or {},
        "title_path": list(payload.get("title_path") or []),
    }


def _rrf_merge(vector_entries: List[dict], bm25_entries: List[dict], *, k: int) -> List[dict]:
    """RRF 融合：按身份累计 ``1/(k+rank)``；两路都命中者自然排前。"""
    slots: Dict[str, dict] = {}
    for rank, entry in enumerate(vector_entries, start=1):
        slot = slots.setdefault(entry["chunk_id"], {"entry": entry, "score": 0.0, "ranks": {}})
        slot["score"] += 1.0 / (k + rank)
        slot["ranks"]["vector"] = rank
    for rank, entry in enumerate(bm25_entries, start=1):
        slot = slots.setdefault(entry["chunk_id"], {"entry": entry, "score": 0.0, "ranks": {}})
        slot["score"] += 1.0 / (k + rank)
        slot["ranks"]["bm25"] = rank
        if "vector" not in slot["ranks"]:
            slot["entry"] = entry
    merged: List[dict] = []
    for chunk_id, slot in slots.items():
        entry = dict(slot["entry"])
        entry["score"] = slot["score"]
        entry["ranks"] = dict(slot["ranks"])
        entry["source"] = ("rrf" if len(slot["ranks"]) > 1
                           else ("vector" if "vector" in slot["ranks"] else "bm25"))
        entry["chunk_id"] = chunk_id
        merged.append(entry)
    # ⚠️ score 为 RRF 排序贡献，不是可信度/相关性概率 —— 不得用作拒答阈值（§5.2）
    merged.sort(key=lambda item: (-item["score"], item["chunk_id"]))
    return merged


def _contained(a: str, b: str) -> bool:
    """同一文档内的包含式重复判定（防「五个结果实际重复同一段」）。"""
    x = " ".join((a or "").split())
    y = " ".join((b or "").split())
    if not x or not y:
        return False
    short, long = (x, y) if len(x) <= len(y) else (y, x)
    if len(short) < 40:
        return False
    return len(short) >= 0.6 * len(long) and short in long


def _assemble(entries: List[dict], *, top_k: int, max_per_doc: int,
              max_tokens: int) -> List[dict]:
    """证据组装：同文档上限 + 同文档包含去重 + token 预算（贪心填充）。"""
    picked: List[dict] = []
    per_doc: Dict[str, int] = {}
    tokens_used = 0
    for entry in entries:
        if len(picked) >= top_k:
            break
        doc_key = str(entry.get("doc") or entry.get("doc_id") or "")
        if per_doc.get(doc_key, 0) >= max_per_doc:
            continue
        text = entry.get("text", "")
        if any(
            str(other.get("doc") or other.get("doc_id") or "") == doc_key
            and _contained(text, other.get("text", ""))
            for other in picked
        ):
            continue
        cost = estimate_tokens(text)
        if picked and tokens_used + cost > max_tokens:
            continue
        picked.append(entry)
        per_doc[doc_key] = per_doc.get(doc_key, 0) + 1
        tokens_used += cost
    return picked


class HybridRetriever:
    """混合检索器（双路召回 + RRF + 证据组装 + 可选 rerank）。

    P5 多租户隔离：`retrieve(scope=...)` 未显式传入时读取 contextvar 当前作用域；
    检索两路都必须通过 `scope.payload_matches`。BM25 语料按 (作用域, 修订号) 缓存。
    """

    def __init__(self):
        self.store = VectorStore()
        self._client: OpenAI | None = None
        #: (user_id, tenant_id, revision) → (metas, bm25)；写路径递增 revision 即整体失效
        self._bm25_cache: dict[tuple, tuple[List[dict], BM25Okapi | None]] = {}

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

    def _effective_revision(self, scope: RagScope) -> int | None:
        """缓存修订号：作用域 revision 为 0（默认未指定）时读库当前值（写路径递增即失效）。

        修复：runner/worker 的 set_scope 只带 user_id/tenant_id（revision 取默认 0），
        长驻进程会永久缓存「摄取前的空语料」→ 运行期检索恒零命中。
        """
        if scope.revision:
            return scope.revision
        try:
            return self.store.get_rag_revision()
        except Exception:  # noqa: BLE001 —— 读不到时退化为不按修订失效（原行为）
            return scope.revision

    def _load_all(self, scope: RagScope) -> tuple[List[dict], BM25Okapi | None]:
        """按 (作用域, 修订号) 加载 BM25 语料（含身份/定位元数据），结果缓存。"""
        key = (scope.user_id, scope.tenant_id, self._effective_revision(scope))
        cached = self._bm25_cache.get(key)
        if cached is not None:
            return cached
        payloads = self.store.scroll_all(scope=scope)
        metas: List[dict] = []
        for payload in payloads:
            text = strip_invisible(payload.get("text", ""))  # P2-1a：检索侧净化
            if not text:
                continue
            meta = dict(payload)
            meta["text"] = text
            metas.append(meta)
        bm25 = BM25Okapi([tokenize(meta["text"]) for meta in metas]) if metas else None
        if len(self._bm25_cache) >= 8:  # 防内存无界：简单整体清空（命中率次要）
            self._bm25_cache.clear()
        self._bm25_cache[key] = (metas, bm25)
        return self._bm25_cache[key]

    def embed_query(self, query: str) -> List[float]:
        resp = self._get_client().embeddings.create(
            model=config.rag.embedding_model,
            input=[query],
        )
        usage = getattr(resp, "usage", None)
        total = int(getattr(usage, "total_tokens", 0) or 0) if usage else 0
        emit_usage(UsageRecord(
            kind="embedding", provider="dashscope", model=config.rag.embedding_model,
            role="query", input_tokens=total, total_tokens=total,
            request_id=getattr(resp, "id", None),
        ))
        return resp.data[0].embedding

    # ---- 单路召回 ----

    def _vector_hits(self, query: str, recall: int, scope: RagScope,
                     resp: RetrieveResponse) -> List[dict]:
        hits: List[dict] = []
        if not config.llm.api_key:
            resp.note_backend_failure(
                "vector", FailureReason.NOT_CONFIGURED.value, "未配置 DASHSCOPE_API_KEY，无法调用 Embedding")
            return hits
        try:
            vector = self.embed_query(query)
            raw = self.store.search(vector, top_k=recall, scope=scope)
            reason = self.store.unavailable_reason
            if reason:
                resp.note_backend_failure("vector", reason, self.store.last_error or "")
                return hits
            for hit in raw:
                payload = hit.get("payload") or {}
                text = strip_invisible(payload.get("text", ""))  # P2-1a：向量侧净化
                if text:
                    hits.append(_entry(payload, text, hit["score"], "vector"))
        except Exception as e:  # noqa: BLE001 — embedding / 查询异常，保住 BM25 那一路
            resp.note_backend_failure("vector", FailureReason.PROVIDER_ERROR.value, str(e)[:300])
        return hits

    def _bm25_hits(self, query: str, recall: int, scope: RagScope,
                   resp: RetrieveResponse) -> List[dict]:
        hits: List[dict] = []
        try:
            metas, bm25 = self._load_all(scope)
            reason = self.store.unavailable_reason
            if reason:
                resp.note_backend_failure("bm25", reason, self.store.last_error or "")
                return hits
            if bm25 and metas:
                scores = bm25.get_scores(tokenize(query))
                ranked = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
                for index in ranked[:recall]:
                    if scores[index] <= 0:
                        continue
                    meta = metas[index]
                    hits.append(_entry(meta, meta["text"], float(scores[index]), "bm25"))
        except Exception as e:  # noqa: BLE001
            resp.note_backend_failure("bm25", FailureReason.PROVIDER_ERROR.value, str(e)[:300])
        return hits

    # ---- 主入口 ----

    def retrieve(self, query: str, top_k: int | None = None,
                 scope: RagScope | None = None) -> RetrieveResponse:
        """双路召回 → RRF → 可选 rerank → 证据组装；返回 :class:`RetrieveResponse`。"""
        scope = scope if scope is not None else current_scope()
        top_k = top_k or config.rag.top_k
        recall = max(int(config.rag.recall_per_route), int(top_k))
        resp = RetrieveResponse(query=query)

        vector_entries = self._vector_hits(query, recall, scope, resp)
        bm25_entries = self._bm25_hits(query, recall, scope, resp)
        entries = _rrf_merge(vector_entries, bm25_entries, k=int(config.rag.rrf_k))

        if config.rag.use_rerank and entries:
            reranked = _rerank_call(query, [entry["text"] for entry in entries])
            if reranked:
                score_by_index = dict(reranked)
                order = [index for index, _ in reranked if 0 <= index < len(entries)]
                order += [index for index in range(len(entries)) if index not in set(order)]
                entries = [{**entries[index], "rerank_score": score_by_index.get(index)}
                           for index in order]

        resp.items = _assemble(
            entries,
            top_k=int(top_k),
            max_per_doc=max(1, int(config.rag.max_per_doc)),
            max_tokens=max(1, int(config.rag.evidence_max_tokens)),
        )

        # ---- 整体判定（语义与 v1 一致；见 response.py 判定矩阵）----
        if not resp.items:
            if resp.backend_failures:
                resp.failure_reason = _worst_reason(resp.backend_failures)
                resp.failure_detail = "; ".join(
                    f"{bf.backend}: {bf.detail}" for bf in resp.backend_failures if bf.detail
                )[:300]
            else:
                # 两路都正常执行了，只是确实没命中 —— 是结果，不是故障（D-03 不上抛降级）
                resp.failure_reason = FailureReason.EMPTY_RESULT.value
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
    for reason in _REASON_PRIORITY:
        if any(item.reason == reason for item in failures):
            return reason
    return failures[0].reason
