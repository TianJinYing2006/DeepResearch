"""需求 34 E1：网页正文提取单测（TBD-3 拍板口径；零网络）。"""
from __future__ import annotations

from research_engine.net.webtext import (
    EXTRACTOR_VERSION,
    decode_body,
    extract_text,
    looks_like_pdf,
)

HTML = (
    "<html><head><meta charset=\"utf-8\"><title>t</title>"
    "<style>body{color:red}</style><script>alert(1)</script></head>"
    "<body><nav>导航区</nav><header>页头</header>"
    "<main><h1>大标题</h1><p>第一段  内容</p><p>第二段</p></main>"
    "<footer>页脚</footer><aside>侧栏</aside></body></html>"
)


def test_extract_main_and_strip_boilerplate():
    out = extract_text(HTML.encode("utf-8"), "text/html; charset=utf-8",
                       "https://e.com/a", max_bytes=65536)
    assert out.kind == "html" and out.truncated is False
    assert "第一段 内容" in out.text and "第二段" in out.text
    for gone in ("alert(1)", "color:red", "导航区", "页头", "页脚", "侧栏"):
        assert gone not in out.text


def test_fallback_chain_body():
    html = "<html><body><div>唯一正文</div></body></html>"
    out = extract_text(html.encode("utf-8"), "text/html", "https://e.com", max_bytes=65536)
    assert out.kind == "html" and "唯一正文" in out.text


def test_truncate_by_utf8_bytes_not_silent():
    html = "<html><body>" + "汉" * 100 + "</body></html>"
    out = extract_text(html.encode("utf-8"), "text/html", "https://e.com", max_bytes=30)
    assert out.truncated is True
    assert len(out.text.encode("utf-8")) <= 30
    assert out.text  # 不静默丢光


def test_pdf_double_detection():
    assert looks_like_pdf("application/pdf", "https://e.com/a") is True
    assert looks_like_pdf("", "https://e.com/a.PDF") is True
    assert looks_like_pdf("text/html", "https://e.com/a") is False
    out = extract_text(b"%PDF-1.7", "application/pdf", "https://e.com/a.pdf", max_bytes=100)
    assert out.kind == "pdf"


def test_unsupported_empty_and_plain_text():
    assert extract_text(b"\x89PNG", "image/png", "https://e.com/a.png", max_bytes=100).kind == "unsupported"
    assert extract_text(b"<html><body>   </body></html>", "text/html",
                        "https://e.com", max_bytes=100).kind == "empty"
    out = extract_text("纯文本内容".encode("utf-8"), "text/plain", "https://e.com/t.txt", max_bytes=100)
    assert out.kind == "text" and out.text == "纯文本内容"


def test_charset_meta_gbk():
    body = "<html><head><meta charset=\"gbk\"></head><body>中文内容</body></html>".encode("gbk")
    out = extract_text(body, "text/html", "https://e.com", max_bytes=1000)
    assert "中文内容" in out.text


def test_decode_body_unknown_charset_falls_back():
    assert decode_body("abc".encode("utf-8"), "text/html; charset=no-such-charset") == "abc"


def test_extractor_version_constant():
    assert EXTRACTOR_VERSION == "webtext.v1"
