"""需求 23 管道 v2 单测（FakeStore + 假向量库，零 PG / Qdrant / LLM）。

覆盖：三层落库与激活、rechunk 退役旧代、reembed 前置与同分块、无快照 409 语义、
构建失败保留旧 active、retired/failed 清理（含失败重试路径）。
"""
from __future__ import annotations

import pytest
from fakes import FakeStore

from web.backend.rag_pipeline import (
    NoActiveChunksError,
    NoSnapshotError,
    build_from_chunks,
    build_from_file,
    build_from_snapshot,
    cleanup_stale_generations,
)


class _FakeVectorStore:
    def __init__(self, *, reason: str | None = None):
        self.points: dict[tuple, list] = {}
        self.active: dict[tuple, bool] = {}
        self.deleted: list[tuple] = []
        self.reason = reason

    def upsert(self, points):
        for point in points:
            payload = point.payload
            key = (payload["doc_id"], payload["generation"])
            self.points.setdefault(key, []).append(point)
            self.active[key] = bool(payload.get("active"))

    def count_by_generation(self, doc_id, generation):
        return len(self.points.get((doc_id, generation), []))

    def set_generation_active(self, doc_id, generation, active, *, wait=True):
        self.active[(doc_id, generation)] = active

    def delete_by_generation(self, doc_id, generation, *, wait=True):
        self.deleted.append((doc_id, generation))
        self.points.pop((doc_id, generation), None)

    @property
    def unavailable_reason(self):
        return self.reason

    @property
    def last_error(self):
        return "stub"


class _Ingester:
    def __init__(self, *, error: Exception | None = None, reason: str | None = None):
        self.error = error
        self.store = _FakeVectorStore(reason=reason)

    def embed(self, texts):
        if self.error:
            raise self.error
        return [[0.0] * 4 for _ in texts]


def _write(tmp_path, text: str = "# 标题\n\n正文段落。\n") -> str:
    path = tmp_path / "doc.md"
    path.write_text(text, encoding="utf-8")
    return str(path)


def test_build_from_file_layers_and_activation(tmp_path):
    store = FakeStore()
    ingester = _Ingester()
    result = build_from_file(store, ingester, doc_id="u1:doc", path=_write(tmp_path),
                             source_name="doc.md", user_id="u1")
    assert result == {"generation": 1, "chunks": 1, "retired": []}
    assert store.count_parse_snapshot("u1:doc") == 2  # heading + paragraph
    assert store.get_active_generation("u1:doc") == 1
    assert ingester.store.active[("u1:doc", 1)] is True
    assert store.get_rag_revision() == 1  # 激活递增修订号（检索缓存失效）
    rows = store.get_rag_chunk_rows("u1:doc", 1)
    assert rows[0]["text"] == "正文段落。"
    assert rows[0]["embed_text"].startswith("标题\n\n")  # 标题路径进 embedding 输入
    assert rows[0]["chunk_id"] == "u1:doc:g1:0"


def test_rechunk_retires_old_generation(tmp_path):
    store = FakeStore()
    ingester = _Ingester()
    build_from_file(store, ingester, doc_id="u1:doc", path=_write(tmp_path),
                    source_name="doc.md", user_id="u1")
    result = build_from_snapshot(store, ingester, doc_id="u1:doc",
                                 source_name="doc.md", user_id="u1")
    assert result["generation"] == 2 and result["retired"] == [1]
    assert store.get_active_generation("u1:doc") == 2
    assert ingester.store.active[("u1:doc", 1)] is False  # 旧代退役（检索不可见）
    assert ingester.store.active[("u1:doc", 2)] is True
    assert {row["generation"] for row in store.list_retired_generations()} == {1}
    assert store.get_rag_revision() == 2


def test_reembed_requires_active_chunks_and_keeps_same_text(tmp_path):
    store = FakeStore()
    ingester = _Ingester()
    with pytest.raises(NoActiveChunksError):
        build_from_chunks(store, ingester, doc_id="u1:doc",
                          source_name="doc.md", user_id="u1")
    build_from_file(store, ingester, doc_id="u1:doc", path=_write(tmp_path),
                    source_name="doc.md", user_id="u1")
    result = build_from_chunks(store, ingester, doc_id="u1:doc",
                               source_name="doc.md", user_id="u1")
    assert result["generation"] == 2 and result["retired"] == [1]
    old_rows = store.get_rag_chunk_rows("u1:doc", 1)
    new_rows = store.get_rag_chunk_rows("u1:doc", 2)
    assert new_rows[0]["text"] == old_rows[0]["text"]  # 同分块，只换向量


def test_rechunk_without_snapshot_raises():
    store = FakeStore()
    with pytest.raises(NoSnapshotError):
        build_from_snapshot(store, _Ingester(), doc_id="u1:gone",
                            source_name="x.md", user_id="u1")


def test_failure_keeps_old_active_and_marks_failed(tmp_path):
    store = FakeStore()
    good = _Ingester()
    build_from_file(store, good, doc_id="u1:doc", path=_write(tmp_path),
                    source_name="doc.md", user_id="u1")
    with pytest.raises(RuntimeError):
        build_from_snapshot(store, _Ingester(error=RuntimeError("embedding 500")),
                            doc_id="u1:doc", source_name="doc.md", user_id="u1")
    # 无不可检索窗口：旧版本仍是活动版本
    assert store.get_active_generation("u1:doc") == 1
    generations = store.rag_generations["u1:doc"]
    assert generations[-1]["generation"] == 2 and generations[-1]["status"] == "failed"
    # 失败代进入清理清单（孤儿向量清理）
    assert {row["generation"] for row in store.list_retired_generations()} == {2}


def test_cleanup_stale_generations_success_and_failure(tmp_path):
    store = FakeStore()
    ingester = _Ingester()
    build_from_file(store, ingester, doc_id="u1:doc", path=_write(tmp_path),
                    source_name="doc.md", user_id="u1")
    build_from_snapshot(store, ingester, doc_id="u1:doc", source_name="doc.md", user_id="u1")

    summary = cleanup_stale_generations(store, vector_store=ingester.store)
    assert summary == {"scanned": 1, "cleaned": 1, "failed": 0}
    assert ingester.store.deleted == [("u1:doc", 1)]
    assert store.list_retired_generations() == []
    assert store.count_rag_chunks("u1:doc", 1) == 0
    assert store.get_active_generation("u1:doc") == 2  # 活动代不受清理影响

    # 失败路径：向量库不可用 → 行保留、下轮重试
    store2 = FakeStore()
    ingester2 = _Ingester()
    build_from_file(store2, ingester2, doc_id="u1:doc", path=_write(tmp_path),
                    source_name="doc.md", user_id="u1")
    build_from_snapshot(store2, ingester2, doc_id="u1:doc", source_name="doc.md", user_id="u1")
    ingester2.store.reason = "down"
    summary = cleanup_stale_generations(store2, vector_store=ingester2.store)
    assert summary == {"scanned": 1, "cleaned": 0, "failed": 1}
    assert {row["generation"] for row in store2.list_retired_generations()} == {1}
