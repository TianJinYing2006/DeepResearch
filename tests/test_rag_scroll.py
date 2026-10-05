"""审计 P2#7：BM25 全量语料 scroll —— 跟随游标分页 + active 服务端下推。"""
from __future__ import annotations

from types import SimpleNamespace

from research_engine.rag.scope import RagScope
from research_engine.rag.store import VectorStore


class _FakeClient:
    def __init__(self, pages):
        self.pages = pages
        self.offsets: list = []
        self.filters: list = []

    def scroll(self, collection_name, limit, with_payload=True, offset=None, scroll_filter=None):
        self.offsets.append(offset)
        self.filters.append(scroll_filter)
        index = 0 if offset is None else offset
        points = self.pages[index]
        next_offset = index + 1 if index + 1 < len(self.pages) else None
        return points, next_offset


def _store_with(client: _FakeClient) -> VectorStore:
    store = VectorStore(url="http://127.0.0.1:6333", collection="test_scroll")
    store._client = client
    store._available = True
    return store


def test_scroll_all_follows_pagination_cursor():
    """回归：只读第一页的 bug —— 两页数据必须都被收集，且 active 过滤下推到服务端。"""
    client = _FakeClient([
        [SimpleNamespace(payload={"text": "a", "user_id": "u1", "active": True})],
        [SimpleNamespace(payload={"text": "b", "user_id": "u1", "active": True})],
    ])
    store = _store_with(client)

    payloads = store.scroll_all(limit=10000, scope=RagScope(user_id="u1"))

    assert [p["text"] for p in payloads] == ["a", "b"]
    assert client.offsets == [None, 1]
    assert all(scroll_filter is not None for scroll_filter in client.filters)


def test_scroll_all_scope_filters_after_pagination():
    """作用域后置过滤语义不变：owner 只命中本人；匿名只命中无主块。"""
    client = _FakeClient([
        [SimpleNamespace(payload={"text": "mine", "user_id": "u1"}),
         SimpleNamespace(payload={"text": "other", "user_id": "u2"})],
        [SimpleNamespace(payload={"text": "legacy"})],
    ])
    store = _store_with(client)

    assert [p["text"] for p in store.scroll_all(scope=RagScope(user_id="u1"))] == ["mine"]
    assert [p["text"] for p in store.scroll_all(scope=RagScope())] == ["legacy"]


def test_scroll_all_respects_total_limit():
    """limit 是总量上限：翻页过程中取满即停。"""
    client = _FakeClient([
        [SimpleNamespace(payload={"text": "a"})],
        [SimpleNamespace(payload={"text": "b"})],
    ])
    store = _store_with(client)

    assert [p["text"] for p in store.scroll_all(limit=1)] == ["a"]
