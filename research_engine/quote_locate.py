"""逐字引文定位与 L0~L2 复核（需求 34 E2 / TBD-4 第二段、TBD-5 拍板口径，2026-10-11）。

两段式的第二段（精定位）：在 E3 候选窗口内选出「最相关句子」为引文候选，
再按 L0→L1→L2 逐级规范化匹配把它定位回**原始快照文本**：

- **L0**：快照文本精确子串包含；
- **L1**：连续空白折叠为单空格后匹配；
- **L2**：全半角统一 + 引号/破折号归一 + L1；
- 三级皆未命中 ⇒ None（调用方记 ``quote_status=not_found``，进 E5 分级；
  **不得**据此判「引用错误」——TBD-5 红线）。

引文候选取自**折叠视图**（折叠后的窗口做句子切分与打分）：网络正文含换行/多空白，
折叠视图的句子映射回原文通常命中 L1；全角标点/弯引号差异命中 L2。定位成功后
**存储原文切片**（逐字引文）与字符偏移（char_start/char_end），不用定位符（TBD-5）。

🚨 红线（TBD-5）：offset 只对「同一 ``extractor_version`` + 同一 ``snapshot_sha256``」成立。
E1 的内容寻址已把版本编进 id（``sha256(版本 + 正文)``）⇒ 同 id 即同版本同正文，偏移恒可复算；
跨版本快照是**另一个 id**，不得互认（E5 的 stale 判定基于 id 比对）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from research_engine.find_in_source import claim_terms, find_in_source

#: 单条引文上限（字符；超出按句内前缀截断——仍是原文切片）
MAX_QUOTE_CHARS = 500

#: L2 归一：全角 ASCII → 半角（U+FF01..U+FF5E 平移 0xFEE0）+ 常见标点 1→1 归一
_PUNCT_MAP = {chr(0xFF01 + i): chr(0x21 + i) for i in range(0x5E)}
_PUNCT_MAP.update({
    "\u3000": " ",                       # 全角空格
    "\u00a0": " ",                       # NBSP
    "\u201c": "\"", "\u201d": "\"",      # “ ”
    "\u2018": "'", "\u2019": "'",        # ‘ ’
    "\u2013": "-", "\u2014": "-", "\u2015": "-",  # – — ―
    "\u2026": ".",                       # …（1→1 归一，仅匹配用）
})

_SENTENCE_SPLIT = re.compile(r"(?<=[。！？；!?;])\s*|(?<=\.\s)")


@dataclass(frozen=True)
class LocatedQuote:
    """一条已定位的引文（原文切片 + 偏移 + 命中级别）。"""

    start: int
    end: int
    level: str  # "L0" | "L1" | "L2"
    quote: str  # 原文切片（逐字）


def fold_whitespace(text: str) -> Tuple[str, List[Tuple[int, int]]]:
    """L1：连续空白折叠为单空格。

    返回 ``(折叠文本, spans)``；``spans[i]`` = 折叠文本第 i 个字符对应的**原文区间**
    （空白运行映射为整个运行的 ``[start, end)``；普通字符 ``[i, i+1)``）。
    """
    folded: List[str] = []
    spans: List[Tuple[int, int]] = []
    i, n = 0, len(text)
    while i < n:
        if text[i].isspace():
            j = i
            while j < n and text[j].isspace():
                j += 1
            folded.append(" ")
            spans.append((i, j))
            i = j
        else:
            folded.append(text[i])
            spans.append((i, i + 1))
            i += 1
    return "".join(folded), spans


def fold_punct(text: str) -> str:
    """L2：标点归一（1→1 映射，不改变长度）。"""
    return "".join(_PUNCT_MAP.get(ch, ch) for ch in text)


def _find_all(haystack: str, needle: str) -> List[int]:
    out: List[int] = []
    if not needle:
        return out
    i = haystack.find(needle)
    while i >= 0:
        out.append(i)
        i = haystack.find(needle, i + 1)
    return out


def _pick(positions: List[int], hint_start: Optional[int]) -> Optional[int]:
    """多个命中时取距 hint 最近者（无 hint 取第一个）。"""
    if not positions:
        return None
    if hint_start is None:
        return positions[0]
    return min(positions, key=lambda p: abs(p - hint_start))


def locate_quote(snapshot_text: str, quote_text: str, *,
                 hint_start: Optional[int] = None) -> Optional[LocatedQuote]:
    """把引文候选定位回原始快照（L0→L1→L2）；三级皆未命中返回 None。"""
    quote = quote_text or ""
    if not snapshot_text or not quote:
        return None
    # L0：精确子串
    hit = _pick(_find_all(snapshot_text, quote), hint_start)
    if hit is not None:
        return LocatedQuote(hit, hit + len(quote), "L0", snapshot_text[hit:hit + len(quote)])
    # L1：空白折叠（引文来自折叠视图时的常规路径）
    folded_snapshot, spans = fold_whitespace(snapshot_text)
    folded_quote, _ = fold_whitespace(quote)
    if not folded_quote:
        return None
    picked = _pick(_find_all(folded_snapshot, folded_quote), hint_start)
    if picked is not None:
        start = spans[picked][0]
        end = spans[picked + len(folded_quote) - 1][1]
        return LocatedQuote(start, end, "L1", snapshot_text[start:end])
    # L2：标点归一 + L1（折叠位置与归一位置一一对应，spans 可复用）
    normalized_snapshot = fold_punct(folded_snapshot)
    normalized_quote = fold_punct(folded_quote)
    picked2 = _pick(_find_all(normalized_snapshot, normalized_quote), hint_start)
    if picked2 is not None:
        start = spans[picked2][0]
        end = spans[picked2 + len(normalized_quote) - 1][1]
        return LocatedQuote(start, end, "L2", snapshot_text[start:end])
    return None


def _split_sentences(text: str) -> List[str]:
    """折叠视图的轻量句子切分（中文句读 + 英文 ``. ``；不做缩写消歧）。"""
    return [part.strip() for part in _SENTENCE_SPLIT.split(text) if part and part.strip()]


def extract_quote(claim: str, snapshot_text: str, *,
                  max_quote_chars: int = MAX_QUOTE_CHARS) -> Optional[LocatedQuote]:
    """E3 窗口 → 最相关句子（折叠视图打分）→ L0~L2 定位回原文；失败返回 None。"""
    windows = find_in_source(claim, snapshot_text)
    if not windows:
        return None
    terms = claim_terms(claim)
    best_sentence = ""
    best_score = 0
    hint: Optional[int] = None
    for window in windows:
        folded, _ = fold_whitespace(window.text)
        for sentence in _split_sentences(folded):
            score = len(terms & claim_terms(sentence))
            if score > best_score:
                best_sentence, best_score, hint = sentence, score, window.start
    if not best_sentence or best_score <= 0:
        return None
    return locate_quote(snapshot_text, best_sentence[:max_quote_chars], hint_start=hint)
