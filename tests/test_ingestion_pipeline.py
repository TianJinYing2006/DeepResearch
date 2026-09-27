"""P0-8b 异步摄取管线单测（FakeStore + 假摄取器/向量库，零 Qdrant / LLM）。"""
from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

import research_engine.rag.store as store_module
import web.backend.ingestion as ingestion_module
from research_engine.rag.ingest import IngestLimitExceeded
from web.backend import main as api
from web.backend.auth import token_hash
from web.backend.ingestion import process_ingestions_once, purge_expired_documents


class _FakeIngester:
    def __init__(self, *, chunks: int = 3, error: Exception | None = None):
        self.chunks = chunks
        self.error = error

    def ingest_file(self, path, doc_id, *, user_id=None, tenant_id=None, visibility="private"):
        if self.error:
            raise self.error
        assert os.path.isfile(path)  # 隔离区文件必须存在
        return self.chunks


class _FakeVectorStore:
    def __init__(self, *, reason: str | None = None, remaining: int = 0):
        self.reason = reason
        self.remaining = remaining
        self.deleted_docs: list[tuple[str, bool]] = []

    @property
    def unavailable_reason(self):
        return self.reason

    def delete_by_doc(self, doc_id, *, wait=True):
        self.deleted_docs.append((doc_id, wait))

    def count_by_doc(self, doc_id):
        return self.remaining


@pytest.fixture()
def quarantine(tmp_path, monkeypatch):
    monkeypatch.setenv("DR_RAG_QUARANTINE_DIR", str(tmp_path))
    return tmp_path


def _create(store: FakeStore, ingestion_id: str = "ing000000001", doc_id: str = "u1:abc",
            stored_name: str = "f.md", *, user_id: str | None = "u1",
            write_file: bool = True) -> str:
    if write_file:
        path = ingestion_module.quarantine_path(stored_name)
        with open(path, "wb") as handle:
            handle.write(b"# doc")
    store.create_ingestion(ingestion_id, doc_id, user_id=user_id, source="doc.md",
                           sha256="abc", size_bytes=6, stored_name=stored_name)
    return ingestion_id


def test_ingestion_success_marks_ready_and_removes_file(quarantine):
    store = FakeStore()
    ingestion_id = _create(store)
    summary = process_ingestions_once(store, ingester_factory=lambda: _FakeIngester(chunks=3))
    assert summary == {"claimed": 1, "ready": 1, "rejected": 0, "retried": 0}
    row = store.get_ingestion(ingestion_id)
    assert row["status"] == "ready" and row["chunks"] == 3
    assert not os.path.exists(ingestion_module.quarantine_path("f.md"))


def test_limit_exceeded_rejects_without_retry(quarantine):
    store = FakeStore()
    ingestion_id = _create(store)
    ingester = _FakeIngester(error=IngestLimitExceeded("页数超限"))
    summary = process_ingestions_once(store, ingester_factory=lambda: ingester)
    assert summary["rejected"] == 1 and summary["retried"] == 0
    row = store.get_ingestion(ingestion_id)
    assert row["status"] == "rejected" and "页数超限" in row["last_error"]
    assert not os.path.exists(ingestion_module.quarantine_path("f.md"))


def test_transient_failure_retries_with_backoff_then_rejected(quarantine):
    store = FakeStore()
    ingestion_id = _create(store)
    ingester = _FakeIngester(error=RuntimeError("embedding 500"))

    summary = process_ingestions_once(store, ingester_factory=lambda: ingester, max_attempts=2)
    assert summary["retried"] == 1
    row = store.get_ingestion(ingestion_id)
    assert row["status"] == "pending" and row["attempts"] == 1
    assert os.path.exists(ingestion_module.quarantine_path("f.md"))  # 保留待重试

    # 时间推进后重试耗尽 → rejected
    store.ingestions[ingestion_id]["next_attempt_at"] = datetime.now(UTC) - timedelta(seconds=1)
    summary = process_ingestions_once(store, ingester_factory=lambda: ingester, max_attempts=2)
    assert summary["rejected"] == 1
    assert store.get_ingestion(ingestion_id)["status"] == "rejected"


def test_infected_scan_rejects(quarantine, monkeypatch):
    store = FakeStore()
    ingestion_id = _create(store)
    monkeypatch.setattr(ingestion_module, "_scan_file", lambda path: "infected")
    summary = process_ingestions_once(store, ingester_factory=lambda: _FakeIngester())
    assert summary["rejected"] == 1
    row = store.get_ingestion(ingestion_id)
    assert row["status"] == "rejected" and row["scan_status"] == "infected"


def test_missing_quarantine_file_retries(quarantine):
    store = FakeStore()
    ingestion_id = _create(store, stored_name="gone.md", write_file=False)
    summary = process_ingestions_once(store, ingester_factory=lambda: _FakeIngester())
    assert summary["retried"] == 1
    assert store.get_ingestion(ingestion_id)["status"] == "pending"


def test_retention_purge_deletes_vectors_and_marks_deleted(quarantine):
    store = FakeStore()
    ingestion_id = _create(store)
    store.ingestions[ingestion_id].update(status="ready", chunks=2,
                                          created_at=datetime.now(UTC) - timedelta(days=100))
    vector_store = _FakeVectorStore()

    summary = purge_expired_documents(store, vector_store=vector_store, days=90)

    assert summary["purged"] == 1
    assert vector_store.deleted_docs == [("u1:abc", True)]
    assert store.get_ingestion(ingestion_id)["status"] == "deleted"


def test_retention_purge_keeps_row_when_vectors_fail(quarantine):
    store = FakeStore()
    ingestion_id = _create(store)
    store.ingestions[ingestion_id].update(status="ready", chunks=2,
                                          created_at=datetime.now(UTC) - timedelta(days=100))
    vector_store = _FakeVectorStore(reason="down")

    summary = purge_expired_documents(store, vector_store=vector_store, days=90)

    assert summary["failed"] == 1
    assert store.get_ingestion(ingestion_id)["status"] == "ready"  # 下轮重试


# ---- API 层 ----

def test_async_upload_registers_and_deduplicates(monkeypatch, quarantine):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    client = TestClient(api.app)

    first = client.post("/api/rag/ingest", files={"file": ("a.md", b"# same", "text/markdown")})
    assert first.status_code == 202
    body = first.json()
    assert body["status"] == "pending"
    row = store.get_ingestion(body["ingestion_id"])
    assert row["stored_name"]
    assert os.path.exists(ingestion_module.quarantine_path(row["stored_name"]))

    second = client.post("/api/rag/ingest", files={"file": ("renamed.md", b"# same", "text/markdown")})
    assert second.status_code == 202
    assert second.json()["ingestion_id"] == body["ingestion_id"]  # 内容寻址去重


def test_status_endpoint_and_ownership(monkeypatch, quarantine):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    store.create_user("u1", "u1@example.com", "h")
    store.create_session(token_hash("tok1"), "u1", datetime.now(UTC) + timedelta(hours=1))
    _create(store, user_id="u1")
    client = TestClient(api.app)
    client.cookies.set("dr_session", "tok1")

    ok = client.get("/api/rag/ingestions/ing000000001")
    assert ok.status_code == 200 and ok.json()["status"] == "pending"

    store.create_user("u2", "u2@example.com", "h")
    store.create_session(token_hash("tok2"), "u2", datetime.now(UTC) + timedelta(hours=1))
    client.cookies.set("dr_session", "tok2")
    assert client.get("/api/rag/ingestions/ing000000001").status_code == 404


def test_delete_doc_endpoint_verifies_and_cleans(monkeypatch, quarantine):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(store_module, "VectorStore", _FakeVectorStore)
    _create(store, doc_id="local:abc", stored_name="d.md", user_id=None)
    client = TestClient(api.app)

    response = client.delete("/api/rag/docs", params={"doc_id": "local:abc"})
    assert response.status_code == 200
    assert store.get_ingestion("ing000000001")["status"] == "deleted"
    assert not os.path.exists(ingestion_module.quarantine_path("d.md"))

    denied = client.delete("/api/rag/docs", params={"doc_id": "someone:abc"})
    assert denied.status_code == 422
