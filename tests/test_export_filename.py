# -*- coding: utf-8 -*-
"""需求 17 / #77：导出文件名（话题名 + RFC 6266 双格式）单元测试。"""
from __future__ import annotations

from urllib.parse import quote

from web.backend.download_names import (
    MAX_TOPIC_CHARS,
    build_export_filename,
    content_disposition,
)

RID = "abc123def456"


def test_chinese_topic_display_and_ascii_fallback():
    display, fallback = build_export_filename("天蝎座与狮子座的适配", RID)
    assert display == "天蝎座与狮子座的适配.md"
    assert fallback == f"deepresearch-{RID}.md"


def test_ascii_topic_uses_same_name_for_both():
    display, fallback = build_export_filename("RAG study 2026", RID)
    assert display == "RAG study 2026.md"
    assert fallback == display


def test_path_traversal_is_stripped():
    display, fallback = build_export_filename("../../etc/passwd", RID)
    assert "/" not in display and "\\" not in display
    assert ".." not in display
    assert fallback == display  # 纯 ASCII，回退名 = 展示名


def test_illegal_chars_are_replaced():
    display, _ = build_export_filename('a<b>c:d"e|f?g*h', RID)
    for ch in '<>:"|?*':
        assert ch not in display


def test_empty_or_blank_topic_falls_back_to_run_id():
    for topic in (None, "", "   ", "...", "/"):
        display, fallback = build_export_filename(topic, RID)
        assert display == f"deepresearch-{RID}.md"
        assert fallback == display


def test_long_topic_is_truncated():
    display, _ = build_export_filename("长" * 100, RID)
    assert len(display) <= MAX_TOPIC_CHARS + len(".md")


def test_content_disposition_rfc6266_shape():
    header = content_disposition("天蝎座.md", f"deepresearch-{RID}.md")
    assert header.startswith('attachment; filename="deepresearch-')
    assert f"filename*=UTF-8''{quote('天蝎座.md', safe='')}" in header
    assert header.isascii()  # filename* 已百分号编码，整体无裸非 ASCII
