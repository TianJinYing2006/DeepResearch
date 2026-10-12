"""需求 34 E3：find_in_source 定位内核单测（零网络、零依赖）。"""
from __future__ import annotations

from research_engine.find_in_source import (
    TOP_K,
    SourceWindow,
    claim_terms,
    find_in_source,
    select_windows,
)


def test_claim_terms_cjk_bigrams_and_stopwords():
    terms = claim_terms("量子计算的未来")
    assert "量子" in terms and "子计" in terms and "计算" in terms
    assert "的" not in terms  # 停用词（且 2-gram 口径不产生单字虚词）


def test_find_support_window_in_long_text():
    text = "导语。" * 200 + "量子计算在2024年取得突破。" + "结尾。" * 200
    windows = find_in_source("量子计算2024年取得突破", text)
    assert windows
    assert any("量子计算在2024年取得突破" in w.text for w in windows[:TOP_K])
    assert windows[0].score > 0


def test_no_overlap_returns_empty():
    assert find_in_source("量子计算", "完全没有相关内容的段落。" * 50) == []
    assert find_in_source("", "任意文本") == []
    assert find_in_source("量子计算", "") == []


def test_short_text_single_window():
    text = "短文本里提到了量子计算。"
    windows = find_in_source("量子计算", text)
    assert len(windows) == 1
    assert windows[0].start == 0 and windows[0].end == len(text)
    assert windows[0].score == 1.0


def test_topk_cap_and_score_order():
    text = "量子计算。" * 300  # 1500 字符 > 窗长
    windows = find_in_source("量子计算", text)
    assert 0 < len(windows) <= TOP_K
    scores = [w.score for w in windows]
    assert scores == sorted(scores, reverse=True)
    starts = [w.start for w in windows]
    assert starts == sorted(starts)  # 并列分按位置靠前


def test_english_and_digits():
    text = ("Deep Research 2024 achieves 95% accuracy in benchmarks. " * 40)
    windows = find_in_source("Deep Research accuracy 95%", text)
    assert windows
    assert "accuracy" in windows[0].text


def test_select_windows_skips_overlap_and_caps_budget():
    text = "abcdefghij" * 200  # 2000 字符，保证切片足长
    overlapping = [
        SourceWindow(0, 500, text[0:500], 0.9),
        SourceWindow(100, 600, text[100:600], 0.8),   # 与第一个重叠 ⇒ 跳过
        SourceWindow(600, 1100, text[600:1100], 0.7),
    ]
    picked = select_windows(overlapping, max_total_chars=2000)
    assert [w.start for w in picked] == [0, 600]

    capped = select_windows(overlapping, max_total_chars=900)
    assert [w.start for w in capped] == [0]  # 600 起的窗口超出剩余预算被跳过


def test_determinism():
    text = "量子计算。" * 300
    assert find_in_source("量子计算", text) == find_in_source("量子计算", text)
