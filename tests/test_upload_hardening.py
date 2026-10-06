"""P0-8a 上传硬化单测（Fake 摄取器，零 Qdrant / LLM）。

- 文件名清洗；扩展名 + magic bytes 三重校验（PDF / DOCX / 文本）；
- DOCX ZIP 结构校验；流式超限 413；内容寻址 doc_id（幂等去重）；
- 解析限额结构化错误；按用户上传限流。
"""
from __future__ import annotations

import hashlib
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

import research_engine.rag.ingest as ingest_module
from config import config
from web.backend import main as api
from web.backend.ratelimit import FixedWindowLimiter
from web.backend.upload_guard import sanitize_filename


class _FakeIngester:
    calls: list[dict] = []

    def ingest_file(self, path, doc_id, *, user_id=None, tenant_id=None, visibility="private"):
        type(self).calls.append({"doc_id": doc_id, "user_id": user_id, "path": path})
        return 1


@pytest.fixture()
def client(monkeypatch) -> TestClient:
    _FakeIngester.calls = []
    monkeypatch.setattr(ingest_module, "DocumentIngester", _FakeIngester)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    # 上传限流器是模块级单例：用例内放开，避免跨用例累计误伤（限流本身单独用例覆盖）
    monkeypatch.setattr(api, "RAG_UPLOAD_LIMITER", FixedWindowLimiter(1000))
    return TestClient(api.app)


def _upload(client: TestClient, name: str, content: bytes,
            mime: str = "application/octet-stream"):
    return client.post("/api/rag/ingest", files={"file": (name, content, mime)})


def test_sanitize_filename_strips_paths_and_controls():
    assert sanitize_filename("../../a\x00b.pdf") == "ab.pdf"
    assert sanitize_filename("  ..\\..\\evil\r\n.md ") == "evil.md"
    assert sanitize_filename("") == "upload"


def test_pdf_magic_mismatch_rejected(client: TestClient):
    response = _upload(client, "fake.pdf", b"not a pdf at all")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_file_type"
    assert "不支持的文件类型" in response.text


def test_docx_must_be_zip_with_word_structure(client: TestClient):
    bad_zip = _upload(client, "x.docx", b"PK\x03\x04garbage")
    assert bad_zip.status_code == 400
    assert bad_zip.json()["detail"]["code"] == "unsupported_file_type"

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("foo.txt", "hello")
    no_word = _upload(client, "y.docx", buffer.getvalue())
    assert no_word.status_code == 400
    assert "DOCX" in no_word.json()["detail"]["message"]


def test_text_with_nul_rejected(client: TestClient):
    response = _upload(client, "x.txt", b"hello\x00world")
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_file_type"


def test_oversize_is_rejected_413(client: TestClient, monkeypatch):
    monkeypatch.setattr(api, "RAG_MAX_UPLOAD_MB", 0.001)
    response = _upload(client, "big.txt", b"x" * 4096)
    assert response.status_code == 413
    assert response.json()["detail"]["code"] == "payload_too_large"
    assert "上限" in response.json()["detail"]["message"]


def test_doc_id_is_content_addressed_and_deduplicated(client: TestClient):
    first = _upload(client, "notes.md", b"# same content")
    second = _upload(client, "renamed.md", b"# same content")
    assert first.status_code == 200 and second.status_code == 200
    expected = f"local:{hashlib.sha256(b'# same content').hexdigest()[:16]}"
    assert first.json()["doc_id"] == expected
    assert second.json()["doc_id"] == expected  # 内容寻址 ⇒ 幂等去重
    assert first.json()["source"] == "notes.md"


def test_upload_rate_limited_per_user(client: TestClient, monkeypatch):
    monkeypatch.setattr(api, "RAG_UPLOAD_LIMITER", FixedWindowLimiter(1))
    assert _upload(client, "a.md", b"# a").status_code == 200
    limited = _upload(client, "b.md", b"# b")
    assert limited.status_code == 429
    assert limited.json()["detail"]["code"] == "rate_limited"


def test_parse_limit_exceeded_is_structured(monkeypatch):
    """真实摄取器（不 mock）：字符数超限 ⇒ 422 document_limit_exceeded。"""
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "RAG_UPLOAD_LIMITER", FixedWindowLimiter(1000))
    monkeypatch.setattr(config.rag, "max_chars", 10)
    client = TestClient(api.app)

    response = _upload(client, "long.txt", b"x" * 100)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "document_limit_exceeded"
