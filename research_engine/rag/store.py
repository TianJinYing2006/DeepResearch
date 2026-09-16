"""Qdrant 向量库封装。

负责文档向量的写入与检索。Embedding 使用阿里云百炼 text-embedding-v3。
Qdrant 连接采用懒加载，连接失败时优雅降级（RAG 检索返回空，不影响网络搜索）。
"""
from __future__ import annotations

import time
from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from config import config
from research_engine.failure_reasons import FailureReason


class VectorStore:
    """Qdrant 向量存储封装。"""

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
        """确保集合存在，不存在则创建。"""
        if self._client is None:
            return
        collections = self._client.get_collections().collections
        names = {c.name for c in collections}
        if self.collection not in names:
            self._client.create_collection(
                collection_name=self.collection,
                vectors_config=VectorParams(size=1024, distance=Distance.COSINE),
            )

    def upsert(self, points: List[PointStruct]):
        """批量写入向量点。"""
        client = self._get_client()
        if client and points:
            client.upsert(collection_name=self.collection, points=points)

    def search(self, vector: List[float], top_k: int = 5) -> List[dict]:
        """向量检索，返回 [{id, score, payload}]。Qdrant 不可用时返回空。"""
        client = self._get_client()
        if client is None:
            return []
        resp = client.query_points(
            collection_name=self.collection,
            query=vector,
            limit=top_k,
            with_payload=True,
        )
        return [
            {
                "id": p.id,
                "score": p.score,
                "payload": p.payload or {},
            }
            for p in resp.points
        ]

    def scroll_all(self, limit: int = 10000) -> List[dict]:
        """滚动获取全部点（用于 BM25）。Qdrant 不可用时返回空。"""
        client = self._get_client()
        if client is None:
            return []
        points, _ = client.scroll(
            collection_name=self.collection,
            limit=limit,
            with_payload=True,
        )
        return [p.payload or {} for p in points]

    def delete_collection(self):
        """删除集合（用于重建）。"""
        client = self._get_client()
        if client:
            client.delete_collection(collection_name=self.collection)
