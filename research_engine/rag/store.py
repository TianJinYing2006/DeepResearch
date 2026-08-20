# -*- coding: utf-8 -*-
"""Qdrant 向量库封装。

负责文档向量的写入与检索。Embedding 使用阿里云百炼 text-embedding-v3。
Qdrant 连接采用懒加载，连接失败时优雅降级（RAG 检索返回空，不影响网络搜索）。
"""
from __future__ import annotations

from typing import List, Optional

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from config import config


class VectorStore:
    """Qdrant 向量存储封装。"""

    def __init__(self, url: Optional[str] = None, collection: Optional[str] = None):
        self.url = url or config.rag.qdrant_url
        self.collection = collection or config.rag.collection
        self._client: Optional[QdrantClient] = None
        self._available: Optional[bool] = None

    def _get_client(self) -> Optional[QdrantClient]:
        """懒加载并测试连接。连接失败返回 None（降级）。"""
        if self._available is False:
            return None
        if self._client is None:
            try:
                client = QdrantClient(url=self.url, check_compatibility=False)
                client.get_collections()  # 测试连接
                self._client = client
                self._available = True
                self._ensure_collection()
            except Exception:  # noqa: BLE001
                self._available = False
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
