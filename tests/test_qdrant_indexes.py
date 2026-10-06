"""P1-7 Qdrant payload index 单测（假 Qdrant 客户端，零外部服务）。"""
from __future__ import annotations

from types import SimpleNamespace

from qdrant_client.models import KeywordIndexParams, PayloadSchemaType

from research_engine.rag.store import VectorStore


class _FakeQdrant:
    def __init__(self, collection: str = "deepresearch_docs", exists: bool = True):
        self.collection = collection
        self.exists = exists
        self.payload_schema: dict = {}
        self.created_indexes: list[tuple[str, object]] = []
        self.created_collections: list[str] = []

    def get_collections(self):
        names = [SimpleNamespace(name=self.collection)] if self.exists else []
        return SimpleNamespace(collections=names)

    def get_collection(self, name):
        return SimpleNamespace(payload_schema=dict(self.payload_schema))

    def create_collection(self, collection_name, vectors_config=None):
        self.exists = True
        self.created_collections.append(collection_name)

    def create_payload_index(self, collection_name, field_name, field_schema=None, wait=True):
        self.created_indexes.append((field_name, field_schema))
        self.payload_schema[field_name] = field_schema


def _store_with(fake: _FakeQdrant) -> VectorStore:
    store = VectorStore(url="http://127.0.0.1:6333", collection="deepresearch_docs")
    store._client = fake
    store._available = True
    return store


def test_payload_indexes_created_with_tenant_user_id():
    fake = _FakeQdrant()
    store = _store_with(fake)

    store._ensure_collection()

    fields = [name for name, _ in fake.created_indexes]
    assert fields == ["user_id", "tenant_id", "visibility", "doc_id", "generation"]
    schemas = dict(fake.created_indexes)
    assert isinstance(schemas["user_id"], KeywordIndexParams)
    assert schemas["user_id"].is_tenant is True
    assert schemas["doc_id"] == PayloadSchemaType.KEYWORD
    assert schemas["generation"] == PayloadSchemaType.INTEGER  # 需求 23：代过滤


def test_payload_indexes_idempotent_on_second_call():
    fake = _FakeQdrant()
    store = _store_with(fake)
    store._ensure_collection()
    first_count = len(fake.created_indexes)

    store._ensure_collection()

    assert len(fake.created_indexes) == first_count  # 已存在的字段不重复建


def test_missing_collection_is_created_then_indexed():
    fake = _FakeQdrant(exists=False)
    store = _store_with(fake)

    store._ensure_collection()

    assert fake.created_collections == ["deepresearch_docs"]
    assert [name for name, _ in fake.created_indexes] == [
        "user_id", "tenant_id", "visibility", "doc_id", "generation"]
