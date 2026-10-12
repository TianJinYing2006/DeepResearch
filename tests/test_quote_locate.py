"""需求 34 E2：引文定位 L0~L2 复核单测（零网络）。"""
from __future__ import annotations

from research_engine.quote_locate import (
    MAX_QUOTE_CHARS,
    extract_quote,
    fold_punct,
    fold_whitespace,
    locate_quote,
)


def test_l0_exact_substring():
    snap = "前言。量子计算取得突破。结尾。"
    quote = "量子计算取得突破。"
    located = locate_quote(snap, quote)
    assert located is not None
    assert located.level == "L0"
    assert snap[located.start:located.end] == quote
    assert located.quote == quote


def test_l1_whitespace_fold_returns_raw_slice():
    snap = "第一段\n\n量子计算 取得\n突破。\n结尾"
    folded_quote = "量子计算 取得 突破。"  # 折叠视图的句子（原文含换行）
    located = locate_quote(snap, folded_quote)
    assert located is not None and located.level == "L1"
    assert located.quote == snap[located.start:located.end]  # 原文切片（逐字）
    assert "\n" in located.quote


def test_l2_punct_fold():
    snap = "标题：“量子计算”取得突破—2024年。"
    half_width = "\"量子计算\"取得突破-2024年。"  # 半角引号/破折号
    located = locate_quote(snap, half_width)
    assert located is not None and located.level == "L2"
    assert located.quote == snap[located.start:located.end]


def test_not_found_and_empty_inputs():
    assert locate_quote("完全无关文本。", "量子计算取得突破。") is None
    assert locate_quote("", "x") is None
    assert locate_quote("x", "") is None


def test_hint_disambiguation_picks_nearest():
    snap = "重复句。中间内容。重复句。"
    first = locate_quote(snap, "重复句。")
    assert first.start == 0
    near_end = locate_quote(snap, "重复句。", hint_start=10)
    assert near_end.start == snap.rfind("重复句。")


def test_fold_whitespace_spans_map_back():
    folded, spans = fold_whitespace("a \n\t b")
    assert folded == "a b"
    assert spans[0] == (0, 1)
    assert spans[1] == (1, 5)   # 空白运行整体
    assert spans[2] == (5, 6)


def test_fold_punct_is_length_preserving():
    text = "“全角”—ＡＢＣ"
    assert len(fold_punct(text)) == len(text)


def test_extract_quote_returns_verbatim_slice_with_newlines():
    snap = "背景资料。\n\n量子计算在2024年取得突破性进展。\n\n后续内容。"
    located = extract_quote("量子计算于2024年取得突破", snap)
    assert located is not None
    assert "量子计算在2024年取得突破性进展。" in located.quote
    assert snap[located.start:located.end] == located.quote
    assert located.level in ("L0", "L1", "L2")


def test_extract_quote_none_on_no_overlap():
    assert extract_quote("量子计算", "完全无关的页面内容。") is None
    assert extract_quote("", "任意文本") is None


def test_extract_quote_caps_length():
    snap = "开头。" + "量子计算" * 300 + "。"
    located = extract_quote("量子计算", snap, max_quote_chars=100)
    assert located is not None
    assert len(located.quote) <= 100 + 4  # 上限内（切片含原文空白映射余量）
    assert located.quote == snap[located.start:located.end]


def test_max_quote_chars_constant():
    assert MAX_QUOTE_CHARS == 500
