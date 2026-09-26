"""P6-A RAG 上传 / 清单 API 测试（Fake 摄取器 + Fake 向量库，零 Qdrant / 零 LLM）。

覆盖：类型白名单与大小上限、匿名/登录作用域打标、摄取失败结构化、
鉴权开启时的 401/403、文档清单按作用域分组与不可用时的 503。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

import research_engine.rag.ingest as ingest_module
import research_engine.rag.store as store_module
from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, token_hash


class _FakeIngester:
    calls: list[dict] = []
    chunks = 2
    error: str | None = None

    def ingest_file(self, path, doc_id, *, user_id=None, tenant_id=None, visibility="private"):
        type(self).calls.append({"path": path, "doc_id": doc_id, "user_id": user_id})
        if type(self).error:
            raise RuntimeError(type(self).error)
        return type(self).chunks


class _FakeVectorStore:
    payloads: list[dict] = []
    seen_scopes: list[str | None] = []
    reason: str | None = None

    @property
    def unavailable_reason(self):
        return type(self).reason

    @property
    def last_error(self):
        return "connection refused"

    def scroll_all(self, limit=10000, *, scope=None):
        type(self).seen_scopes.append(None if scope is None else scope.user_id)
        return type(self).payloads


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    _FakeIngester.calls = []
    _FakeIngester.chunks = 2
    _FakeIngester.error = None
    monkeypatch.setattr(ingest_module, "DocumentIngester", _FakeIngester)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    return TestClient(api.app)


def _upload(client: TestClient, name: str = "doc.md", content: bytes = b"# hello"):
    return client.post("/api/rag/ingest", files={"file": (name, content, "text/markdown")})


def test_ingest_success_with_anonymous_scope(client: TestClient):
    response = _upload(client)
    assert response.status_code == 200
    body = response.json()
    assert body["chunks"] == 2
    assert body["source"] == "doc.md"
    assert body["doc_id"].startswith("local:")
    assert _FakeIngester.calls[0]["user_id"] is None


def test_ingest_rejects_bad_type_and_oversize(client: TestClient, monkeypatch):
    bad = _upload(client, name="evil.exe")
    assert bad.status_code == 422  # invalid_request 规格为 422（P1 定稿）
    assert bad.json()["detail"]["code"] == "invalid_request"

    monkeypatch.setattr(api, "RAG_MAX_UPLOAD_MB", 0.001)
    big = _upload(client, content=b"x" * 4096)
    assert big.status_code == 422
    assert "上限" in big.json()["detail"]["message"]


def test_ingest_failure_is_structured(client: TestClient):
    _FakeIngester.error = "embedding 500"
    response = _upload(client)
    assert response.status_code == 503
    detail = response.json()["detail"]
    assert detail["code"] == "rag_ingest_failed"
    assert "embedding 500" in detail["message"]


def test_ingest_requires_auth_and_csrf_when_enabled(monkeypatch):
    _FakeIngester.calls = []
    _FakeIngester.error = None
    monkeypatch.setattr(ingest_module, "DocumentIngester", _FakeIngester)
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    fake = FakeStore()
    fake.create_user("u-rag", "rag@example.com", "hash")
    fake.create_session(token_hash("tok-rag"), "u-rag",
                        datetime.now(UTC) + timedelta(hours=1))
    monkeypatch.setattr(api, "store", fake)
    client = TestClient(api.app)

    assert _upload(client).status_code == 401  # 未登录
    client.cookies.set("dr_session", "tok-rag")
    assert _upload(client).status_code == 403  # 缺 CSRF
    client.cookies.set(CSRF_COOKIE, "c1")
    ok = client.post("/api/rag/ingest", files={"file": ("a.md", b"x", "text/markdown")},
                     headers={CSRF_HEADER: "c1"})
    assert ok.status_code == 200
    assert _FakeIngester.calls[-1]["user_id"] == "u-rag"
    assert _FakeIngester.calls[-1]["doc_id"].startswith("u-rag:")


def test_docs_lists_sources_by_scope(monkeypatch):
    monkeypatch.setattr(store_module, "VectorStore", _FakeVectorStore)
    _FakeVectorStore.payloads = [
        {"source": "a.md", "text": "x"},
        {"source": "a.md", "text": "y"},
        {"source": "b.md", "text": "z"},
    ]
    _FakeVectorStore.reason = None
    _FakeVectorStore.seen_scopes = []
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)

    body = TestClient(api.app).get("/api/rag/docs").json()
    assert body["docs"] == [
        {"source": "a.md", "chunks": 2},
        {"source": "b.md", "chunks": 1},
    ]
    assert _FakeVectorStore.seen_scopes == [None]


def test_docs_returns_503_when_vector_store_unavailable(monkeypatch):
    monkeypatch.setattr(store_module, "VectorStore", _FakeVectorStore)
    _FakeVectorStore.reason = "provider_error"
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)

    response = TestClient(api.app).get("/api/rag/docs")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "rag_unavailable"
