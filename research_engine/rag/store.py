"""Qdrant 向量库封装。

负责文档向量的写入与检索。Embedding 使用阿里云百炼 text-embedding-v3。
Qdrant 连接采用懒加载，连接失败时优雅降级（RAG 检索返回空，不影响网络搜索）。
"""
from __future__ import annotations

import time
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    KeywordIndexParams,
    MatchValue,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)

from config import config
from research_engine.failure_reasons import FailureReason
from research_engine.rag.scope import RagScope, payload_matches


class VectorStore:
    """Qdrant 向量存储封装。"""

    #: P1-7：检索过滤 / 删除依赖的 payload 字段（幂等建索引）
    INDEXED_PAYLOAD_FIELDS = ("user_id", "tenant_id", "visibility", "doc_id")

    def __init__(self, url: Optional[str] = None, collection: Optional[str] = None):
        self.url = url or config.rag.qdrant_url
        self.collection = collection or config.rag.collection
        self._client: Optional[QdrantClient] = None
        self._available: Optional[bool] = None
        self._last_fail_at: float = 0.0
        # W8 Arm 4：不可用的**原因**（原先只返回 []，「连不上」与「确实没命中」不可分）
        self._last_error: Optional[str] = None

    # ---- W8 Arm 4：把「不可用」从「空结果」里分离出来 ----

    @property
    def unavailable_reason(self) -> Optional[str]:
        """向量库不可用时返回 :class:`FailureReason` 值；可用（或已自愈）时返回 ``None``。

        ⚠️ 会触发一次懒加载尝试 —— 与 :meth:`search` 内部调用同一份逻辑，不额外增加连接
        （2s 冷却期内不会重复对不可达服务做超时重试）。
        """
        if self._client is not None:
            return None
        self._get_client()
        if self._client is not None:
            return None
        if not self.url:
            return FailureReason.NOT_CONFIGURED.value
        return FailureReason.PROVIDER_ERROR.value

    @property
    def last_error(self) -> Optional[str]:
        """最近一次连接失败的原始异常摘要（排障用，不参与枚举统计）。"""
        return self._last_error

    def _get_client(self) -> Optional[QdrantClient]:
        """懒加载并测试连接。连接失败时进入 2s 冷却降级，冷却后自动重试。

        关键：不把一次瞬时失败永久缓存为不可用（否则整场研究进程的 RAG
        全部降级为空），也不在冷却期内反复对不可达服务做超时重试。
        """
        if self._client is not None:
            return self._client
        if self._available is False and time.time() - self._last_fail_at < 2.0:
            return None
        try:
            try:
                client = QdrantClient(url=self.url, check_compatibility=False)
            except TypeError:
                # qdrant_client 旧版（<1.12）无 check_compatibility kwarg，回退裸构造
                client = QdrantClient(url=self.url)
            client.get_collections()  # 测试连接
            self._client = client
            self._available = True
            self._ensure_collection()
        except Exception as e:  # noqa: BLE001
            # W8 Arm 4：留原始异常摘要 —— 否则「连不上」在下游只表现为一个空列表。
            self._available = False
            self._last_fail_at = time.time()
            self._last_error = str(e)[:300]
            self._client = None
        return self._client

    def _ensure_collection(self):
        """确保集合存在，不存在则创建；随后幂等补齐 payload index（P1-7）。"""
        if self._client is None:
            return
        collections = self._client.get_collections().collections
        names = {c.name for c in collections}
        if self.collection not in names:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
            )
        self._ensure_payload_indexes()

    def _ensure_payload_indexes(self) -> None:
        """P1-7：为过滤字段建 payload index（`user_id` 启用 `is_tenant`）。

        幂等：已存在的字段跳过；单字段失败不阻断（检索侧仍有 Python 后置过滤兜底），
        但会把原因留在 `last_error` 供排障。过滤字段建索引可显著加速多租户检索。
        """
        if self._client is None:
            return
        try:
            info = self._client.get_collection(self.collection)
            existing = set((getattr(info, "payload_schema", None) or {}).keys())
        except Exception:  # noqa: BLE001 —— 查询失败则尝试全量建（重复建是幂等的）
            existing = set()
        for field in self.INDEXED_PAYLOAD_FIELDS:
            if field in existing:
                continue
            schema = (KeywordIndexParams(type="keyword", is_tenant=True)
                      if field == "user_id" else PayloadSchemaType.KEYWORD)
            try:
                self._client.create_payload_index(
                    collection_name=self.collection, field_name=field,
                    field_schema=schema, wait=True)
            except Exception as exc:  # noqa: BLE001
                self._last_error = str(exc)[:300]

    def upsert(self, points: List[PointStruct]):
        """批量写入向量点。"""
        client = self._get_client()
        if client and points:
            client.upsert(collection_name=self.collection, points=points)

    def search(self, vector: List[float], top_k: int = 5,
               scope: Optional[RagScope] = None) -> List[dict]:
        """向量检索，返回 [{id, score, payload}]。Qdrant 不可用时返回空。

        P5 多租户隔离：`scope` 非空即强制按作用域过滤 —— owner 作用域同时下推
        Qdrant 服务端 `must` 过滤（减少跨租户数据触碰），再做 Python 后置过滤
        （`scope.payload_matches` 是语义唯一来源，不依赖 Qdrant 的空值语义）。
        """
        client = self._get_client()
        if client is None:
            return []
        query_filter = None
        if scope is not None and scope.is_owner_scope:
            query_filter = Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=scope.user_id)),
                FieldCondition(key="visibility", match=MatchValue(value="private")),
            ])
        resp = client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=top_k,
            with_payload=True,
            query_filter=query_filter,
        )
        hits = [
            {
                "id": p.id,
                "score": p.score,
                "payload": p.payload or {},
            }
            for p in resp.points
        ]
        if scope is not None:
            hits = [h for h in hits if payload_matches(h["payload"], scope)]
        return hits

    def scroll_all(self, limit: int = 10000, *,
                   scope: Optional[RagScope] = None) -> List[dict]:
        """滚动获取全部点（用于 BM25）。Qdrant 不可用时返回空。

        P5：`scope` 非空时在返回前按作用域过滤（BM25 需要全量语料，故不下推服务端过滤）。
        """
        client = self._get_client()
        if client is None:
            return []
        points, _ = client.scroll(
            collection_name=self.collection,
            limit=limit,
            with_payload=True,
        )
        payloads = [p.payload or {} for p in points]
        if scope is not None:
            payloads = [p for p in payloads if payload_matches(p, scope)]
        return payloads

    def delete_collection(self):
        """删除集合（用于重建）。"""
        client = self._get_client()
        if client:
            client.delete_collection(collection_name=self.collection)

    def delete_by_user(self, user_id: str, *, wait: bool = True) -> None:
        """删除某用户的全部文档块（P7-A 注销清理；P0-7 由 outbox 执行）。

        `wait=True` 等待删除实际完成（Qdrant `wait` 参数），供执行器做完成验证；
        Qdrant 不可用时抛 `RuntimeError` —— 由 outbox 重试语义处理。
        """
        client = self._get_client()
        if client is None:
            raise RuntimeError(self._last_error or "qdrant unavailable")
        client.delete(
            collection_name=self.collection,
            points_selector=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            ]),
            wait=wait,
        )

    def count_by_user(self, user_id: str) -> int:
        """该用户在集合中的点数（P0-7 删除完成验证；`exact=True` 不用近似值）。"""
        client = self._get_client()
        if client is None:
            raise RuntimeError(self._last_error or "qdrant unavailable")
        result = client.count(
            collection_name=self.collection,
            count_filter=Filter(must=[
                FieldCondition(key="user_id", match=MatchValue(value=user_id)),
            ]),
            exact=True,
        )
        return int(result.count)

    def delete_by_doc(self, doc_id: str, *, wait: bool = True) -> None:
        """删除某文档（内容寻址 doc_id）的全部块（P0-8b 文档删除 / 保留期清理）。"""
        client = self._get_client()
        if client is None:
            raise RuntimeError(self._last_error or "qdrant unavailable")
        client.delete(
            collection_name=self.collection,
            points_selector=Filter(must=[
                FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
            ]),
            wait=wait,
        )

    def count_by_doc(self, doc_id: str) -> int:
        """该文档在集合中的点数（P0-8b 删除完成验证）。"""
        client = self._get_client()
        if client is None:
            raise RuntimeError(self._last_error or "qdrant unavailable")
        result = client.count(
            collection_name=self.collection,
            count_filter=Filter(must=[
                FieldCondition(key="doc_id", match=MatchValue(value=doc_id)),
            ]),
            exact=True,
        )
        return int(result.count)
