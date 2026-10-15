"""需求 38 F2：PDF 渲染器单测（受限 HTML / 拒资源 / 页数与输出上限 / CJK 可提取）。

无 pango 环境（如 Windows 开发机）自动跳过——真实渲染在 CI/Linux 与 export 镜像内验证。
"""
from __future__ import annotations

import io

import pytest

try:
    import weasyprint  # noqa: F401
except (ImportError, OSError) as exc:  # 无 pango/gobject（如 Windows 开发机）⇒ 整模块跳过
    pytest.skip(f"weasyprint 不可用：{exc}", allow_module_level=True)

from pypdf import PdfReader  # noqa: E402

from web.backend import pdf_render  # noqa: E402


def _doc(**overrides):
    doc = {
        "run_id": "run-pdf",
        "topic": "PDF 渲染主题",
        "generated_at": "2026-10-16T00:00:00+00:00",
        "run_status": "success",
        "stop_reason": "completed",
        "cancelled": False,
        "token_used": 100,
        "cost_estimate_cny": 0.001,
        "elapsed_seconds": 12.5,
        "degradation_count": 1,
        "depth": 2,
        "report": "# 报告标题\n\n中文正文段落。\n\n## 小节\n\n- 要点一\n- 要点二",
        "citations": [{"claim": "论断", "source": "https://e.com/a", "source_type": "web",
                       "confidence": 0.9, "existence": True, "supported": True}],
        "visited_sources": ["https://e.com/a"],
        "egress": None,
    }
    doc.update(overrides)
    return doc


def test_restricted_html_escapes_raw_html():
    html = pdf_render.markdown_to_restricted_html(
        "# 标题\n\n<script>alert(1)</script>\n\n<img src=x>")
    assert "&lt;script&gt;" in html and "<script>" not in html
    assert "&lt;img" in html and "<img" not in html


def test_restricted_html_structure():
    html = pdf_render.markdown_to_restricted_html(
        "# 一级\n\n段落\n\n## 二级\n\n- 项一\n- 项二\n\n1. 甲\n2. 乙\n\n> 引用\n\n```\ncode\n```")
    assert "<h1>一级</h1>" in html and "<h2>二级</h2>" in html
    assert "<ul>" in html and "<li>项一</li>" in html and "<li>项二</li>" in html
    assert "<ol>" in html and "<li>甲</li>" in html and "<li>乙</li>" in html
    assert "<blockquote>引用</blockquote>" in html
    assert "<pre>" in html


def test_deny_all_fetcher_returns_none():
    assert pdf_render._deny_all_fetcher("http://evil.example/a") is None
    assert pdf_render._deny_all_fetcher("file:///etc/passwd") is None


def test_render_pdf_cjk_extractable():
    pdf, pages = pdf_render.render_pdf_from_doc(
        _doc(), max_pages=100, max_output_bytes=20 * 1024 * 1024)
    assert pdf.startswith(b"%PDF")
    assert pages >= 1
    text = "".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "中文正文段落" in text
    assert "引用清单" in text


def test_render_pdf_unavailable_explicit():
    pdf, _ = pdf_render.render_pdf_from_doc(
        _doc(citations=None, visited_sources=None), max_pages=100,
        max_output_bytes=20 * 1024 * 1024)
    text = "".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(pdf)).pages)
    assert "不可用" in text


def test_render_pdf_limits():
    doc = _doc(report="# T\n\n" + "\n\n".join(f"## 节 {i}\n\n内容" for i in range(400)))
    with pytest.raises(pdf_render.PdfRenderError) as e:
        pdf_render.render_pdf_from_doc(doc, max_pages=1, max_output_bytes=10**9)
    assert e.value.code == "export_too_many_pages"
    with pytest.raises(pdf_render.PdfRenderError) as e2:
        pdf_render.render_pdf_from_doc(_doc(), max_pages=100, max_output_bytes=100)
    assert e2.value.code == "export_too_large"


class _FakeConn:
    def __init__(self, msgs):
        self.msgs = list(msgs)
        self.sent: list = []
        self.closed = False

    def recv(self):
        return self.msgs.pop(0)

    def send(self, msg):
        self.sent.append(msg)

    def close(self):
        self.closed = True


def test_child_main_protocol():
    conn = _FakeConn([
        {"doc": _doc(), "max_pages": 100, "max_output_bytes": 20 * 1024 * 1024},
        {"doc": _doc(), "max_pages": 0, "max_output_bytes": 10**9},  # 页数超限 ⇒ 错误回复
        None,
    ])
    pdf_render.child_main(conn)
    assert conn.sent[0]["ok"] is True and conn.sent[0]["pdf"].startswith(b"%PDF")
    assert conn.sent[0]["sha256"]
    assert conn.sent[1]["ok"] is False and conn.sent[1]["error_code"] == "export_too_many_pages"
    assert conn.closed is True
