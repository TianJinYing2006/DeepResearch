"""P0-8a 上传硬化单测（Fake 摄取器，零 Qdrant / LLM）。

- 文件名清洗；扩展名 + magic bytes 三重校验（PDF / DOCX / 文本）；
- DOCX ZIP 结构校验；流式超限 413；内容寻址 doc_id（幂等去重）；
- 解析限额结构化错误；按用户上传限流。
"""
from __future__ import annotations

import hashlib
import io
import os
import time
import zipfile

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

import research_engine.rag.ingest as ingest_module
from config import config
from research_engine.rag.ingest import IngestLimitExceeded
from research_engine.rag.snapshot import parse_docx_blocks, parse_pdf_blocks
from web.backend import main as api
from web.backend import upload_guard
from web.backend.rag_pipeline import build_from_file
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


# ---------------------------------------------------------------- #325：上传链路加固


def _ooxml_with_padding(*, zero_entries: int, zeros_mb: int, noise_bytes: int) -> bytes:
    """构造「高展开 + 低比值」伪 DOCX：零填充条目压缩率极高，噪声条目抬高 packed 体积。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", "<w:document/>")
        for index in range(zero_entries):
            archive.writestr(f"word/pad{index}.bin", b"\0" * (zeros_mb * 1024 * 1024))
        archive.writestr("word/noise.bin", os.urandom(noise_bytes),
                         compress_type=zipfile.ZIP_STORED)
    return buffer.getvalue()


def test_ooxml_absolute_expanded_cap_rejects_low_ratio_bomb(client: TestClient, monkeypatch):
    """#325：绝对展开上限独立于比值闸（padding 可把解压比稀释到 50 以下）。"""
    monkeypatch.setattr(upload_guard, "MAX_EXPANDED_BYTES", 2 * 1024 * 1024)
    payload = _ooxml_with_padding(zero_entries=2, zeros_mb=1, noise_bytes=100 * 1024)
    # 展开 ≈ 2.1MB、packed ≈ 100KB ⇒ 比值 ≈ 20（< 50，旧逻辑放行）；绝对上限 2MB 触发。
    response = _upload(client, "bomb.docx", payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_file_type"
    assert "expanded" in response.json()["detail"]["detail"]


def test_ooxml_single_member_cap_rejects_bomb(client: TestClient, monkeypatch):
    """#325：单成员解压上限（不能靠拆成员绕过总量闸）。"""
    monkeypatch.setattr(upload_guard, "MAX_EXPANDED_BYTES", 2 * 1024 * 1024)
    payload = _ooxml_with_padding(zero_entries=1, zeros_mb=3, noise_bytes=8 * 1024)
    response = _upload(client, "bomb2.docx", payload)
    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "unsupported_file_type"
    assert "member" in response.json()["detail"]["detail"]


def test_rejected_upload_cleans_quarantine_orphans(monkeypatch, tmp_path):
    """#325：400/413 拒绝后隔离区不得残留无 DB 记录的孤儿；成功登记的文件必须保留。"""
    monkeypatch.setattr(api, "store", FakeStore())
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "RAG_UPLOAD_LIMITER", FixedWindowLimiter(1000))
    monkeypatch.setenv("DR_RAG_QUARANTINE_DIR", str(tmp_path))
    client = TestClient(api.app)

    ok = _upload(client, "good.md", b"# ok")
    assert ok.status_code == 202
    kept = list(tmp_path.iterdir())
    assert len(kept) == 1  # 成功登记后文件留给 worker 读

    bad = _upload(client, "fake.pdf", b"not a pdf")
    assert bad.status_code == 400
    disguised = _upload(client, "x.txt", b"hello\x00world")  # 二进制伪装文本 ⇒ 400
    assert disguised.status_code == 400
    monkeypatch.setattr(api, "RAG_MAX_UPLOAD_MB", 0.001)
    over = _upload(client, "big.txt", b"x" * 4096)
    assert over.status_code == 413
    assert list(tmp_path.iterdir()) == kept  # 拒绝路径没有新增孤儿


def test_docx_parse_deadline_raises_ingest_limit(tmp_path):
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("正文段落")
    path = tmp_path / "deadline.docx"
    document.save(str(path))
    with pytest.raises(IngestLimitExceeded):
        parse_docx_blocks(str(path), deadline=time.monotonic() - 1.0)


def test_pdf_parse_deadline_raises_ingest_limit(tmp_path):
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    writer.add_blank_page(width=200, height=200)
    path = tmp_path / "deadline.pdf"
    with open(path, "wb") as handle:
        writer.write(handle)
    with pytest.raises(IngestLimitExceeded):
        parse_pdf_blocks(str(path), deadline=time.monotonic() - 1.0)


def test_v2_pipeline_enforces_parse_wall_clock(monkeypatch, tmp_path):
    """#325：生产 v2 管线（worker 经 build_from_file）必须把解析墙钟传进解析器。"""
    docx = pytest.importorskip("docx")
    document = docx.Document()
    document.add_paragraph("正文段落")
    path = tmp_path / "pipeline.docx"
    document.save(str(path))
    monkeypatch.setattr(config.rag, "parse_timeout_seconds", -1.0)
    with pytest.raises(IngestLimitExceeded):
        build_from_file(FakeStore(), _FakeIngester(), doc_id="local:deadline",
                        path=str(path), source_name="pipeline.docx")
