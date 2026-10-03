"""需求 23 知识库管理 API 测试（FakeStore，零 PG / Qdrant）。

覆盖：PG 台账列表字段、分块预览（活动版本 / 指定版本）、重命名与标签、
rechunk / reembed 入队（含无快照 409、非 ready 409）、容量用量、越权 404。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, token_hash
from web.backend.ratelimit import FixedWindowLimiter


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    fake.create_ingestion("ing000000001", "local:abc", user_id=None, source="doc.md",
                          sha256="abc", size_bytes=1024, stored_name="")
    row = fake.ingestions["ing000000001"]
    row.update(status="ready", chunks=2, active_generation=1, index_revision=1)
    fake.replace_parse_snapshot("local:abc", [
        {"block_index": 0, "kind": "heading", "title_path": ["标题"], "locator": {}, "text": "标题"},
        {"block_index": 1, "kind": "paragraph", "title_path": ["标题"], "locator": {"page": 1},
         "text": "正文段落。"},
    ])
    fake.create_index_generation("local:abc", chunker_version="v2",
                                 embedding_model="text-embedding-v3", embedding_dim=1024)
    fake.insert_rag_chunks("local:abc", 1, [
        {"chunk_index": 0, "chunk_id": "local:abc:g1:0", "text": "正文段落。",
         "embed_text": "标题\n\n正文段落。", "title_path": ["标题"], "locator": {"page": 1}},
    ])
    fake.activate_index_generation("local:abc", 1)
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "RAG_UPLOAD_LIMITER", FixedWindowLimiter(1000))
    return TestClient(api.app)


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE) or "c1"}


def test_docs_list_from_pg_ledger(client: TestClient):
    body = client.get("/api/rag/docs").json()
    assert body["docs"] == [{
        "doc_id": "local:abc", "source": "doc.md", "display_name": None, "tags": [],
        "status": "ready", "chunks": 2, "size_bytes": 1024,
        "created_at": body["docs"][0]["created_at"], "error": None,
        "active_generation": 1,
    }]


def test_chunks_preview_active_and_explicit_generation(client: TestClient):
    body = client.get("/api/rag/docs/local:abc/chunks").json()
    assert body["generation"] == 1 and body["total"] == 1
    assert body["chunks"][0]["chunk_id"] == "local:abc:g1:0"
    assert body["chunks"][0]["locator"] == {"page": 1}

    explicit = client.get("/api/rag/docs/local:abc/chunks",
                          params={"generation": 1, "limit": 1}).json()
    assert explicit["total"] == 1


def test_rename_and_tags(client: TestClient, store: FakeStore):
    ok = client.patch("/api/rag/docs", json={
        "doc_id": "local:abc", "display_name": "产品手册", "tags": ["手册", " v2 "],
    })
    assert ok.status_code == 200
    row = store.get_ingestion("ing000000001")
    assert row["display_name"] == "产品手册" and row["tags"] == ["手册", "v2"]

    empty = client.patch("/api/rag/docs", json={"doc_id": "local:abc", "display_name": "  "})
    assert empty.status_code == 422 and empty.json()["detail"]["code"] == "invalid_request"

    missing = client.patch("/api/rag/docs", json={"doc_id": "local:nope", "tags": []})
    assert missing.status_code == 404


def test_rechunk_requeues_with_snapshot(client: TestClient, store: FakeStore):
    ok = client.post("/api/rag/docs/rechunk", json={"doc_id": "local:abc"})
    assert ok.status_code == 200
    assert ok.json()["task"] == "rechunk"
    row = store.get_ingestion("ing000000001")
    assert row["status"] == "pending" and row["task"] == "rechunk" and row["attempts"] == 0


def test_rechunk_without_snapshot_returns_409(client: TestClient, store: FakeStore):
    store.rag_snapshots.pop("local:abc", None)
    blocked = client.post("/api/rag/docs/rechunk", json={"doc_id": "local:abc"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "rag_no_snapshot"


def test_rebuild_requires_ready_status(client: TestClient, store: FakeStore):
    store.ingestions["ing000000001"]["status"] = "processing"
    blocked = client.post("/api/rag/docs/reembed", json={"doc_id": "local:abc"})
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "rag_not_ready"


def test_reembed_requeues(client: TestClient, store: FakeStore):
    ok = client.post("/api/rag/docs/reembed", json={"doc_id": "local:abc"})
    assert ok.status_code == 200 and ok.json()["task"] == "reembed"


def test_usage_bytes(client: TestClient):
    body = client.get("/api/rag/usage").json()
    assert body["used_bytes"] == 1024 and body["quota_bytes"] is None


def test_management_cross_user_is_404(monkeypatch, store: FakeStore):
    store.create_user("u1", "u1@example.com", "h")
    store.create_user("u2", "u2@example.com", "h")
    store.create_session(token_hash("tok2"), "u2", datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "RAG_UPLOAD_LIMITER", FixedWindowLimiter(1000))
    client = TestClient(api.app)
    client.cookies.set("dr_session", "tok2")
    client.cookies.set(CSRF_COOKIE, "c2")
    headers = {CSRF_HEADER: "c2"}

    assert client.get("/api/rag/docs/local:abc/chunks").status_code == 404
    assert client.patch("/api/rag/docs", json={"doc_id": "local:abc", "tags": []},
                        headers=headers).status_code == 404
    assert client.post("/api/rag/docs/rechunk", json={"doc_id": "local:abc"},
                       headers=headers).status_code == 404
