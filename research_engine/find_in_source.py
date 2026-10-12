"""claim → 快照内支持窗口定位（需求 34 E3 / TBD-4 拍板口径，2026-10-11）。

两段式的第一段（候选窗口，E3 内核；第二段「精定位 L0~L2 + offset」属 E2）：

    claim → 轻量关键词抽取 → 快照滑窗（窗长 800 / 步长 100 字符）
          → 字面重叠打分 → Top-3 窗口

口径（需求 34 §5 TBD-4/TBD-5）：
- **轻量关键词抽取**（拍板：新增，避免把实体识别结果当关键词）：纯规则、无模型、
  无外部依赖；中文按**字**取 2-gram（段长 1 时单字兜底），英文/数字按小写词；
  命中停用词即丢弃。字符级口径与 E2 的逐字 offset 红线（char_start/char_end）同族
  （BM25 的 jieba 词级分词服务召回，本模块服务逐字定位，两者互补）。
- **打分**：窗口对 claim 词条的**覆盖率**（命中词条数 / claim 词条总数）；
  并列按窗口起点靠前。
- 输出 :class:`SourceWindow`（start/end/text/score）；无任何重叠 ⇒ 返回空列表
  （调用方走既有降级路径，不阻塞）。
"""
from __future__ import annotations

import re
from bisect import bisect_left
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

#: 滑窗参数（TBD-4 拍板：窗长 800 / 步长 100 / Top-3）
WINDOW_CHARS = 800
WINDOW_STRIDE = 100
TOP_K = 3

_CJK_RE = re.compile(r"[\u3040-\u9fff\uf900-\ufaff]")

#: 轻量停用词（中文虚词 / 英文功能词；命中即丢弃，不参与打分）
_STOPWORDS = frozenset("""
的 了 和 与 及 或 是 在 有 为 对 从 到 等 被 把 让 向 于 中 上 下 内 外
而 也 都 就 还 又 并 但 如 若 则 其 之 者 该 此 这 那
我们 你们 他们 以及 一个 一种 一些 通过 进行 可以 可能 需要 应当 应该 具有 包括
表示 认为 显示 表明 目前 已经 正在 同时 此外 因此 所以 从而 由于 因为 如果
虽然 但是 然而 而且 并且 例如 比如 其中 以上 以下 之一
the a an and or of to in on for with by from as at is are was were be been being
this that these those it its their our your his her they we you he she
not no non can could may might will would shall should must
have has had do does did than then also such via per into over under
about between within without among across during before after above below
up down out off again further once here there when where why how
all any both each few more most other some only own same so too very
""".split())


@dataclass(frozen=True)
class SourceWindow:
    """快照内一个候选窗口（字符偏移 + 原文 + 覆盖率分数）。"""

    start: int
    end: int
    text: str
    score: float


def _terms_with_positions(text: str) -> List[Tuple[str, int]]:
    """抽取 ``(词条, 起始字符位置)``。

    - 中文段：2-gram（相邻两字；段长 1 时单字兜底）；
    - 英文/数字段：小写词（长度 ≥2，或含数字的短词如 ``5g``）；
    - 其余字符（标点/空白）为分隔符。
    """
    out: List[Tuple[str, int]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if _CJK_RE.match(ch):
            j = i
            while j < n and _CJK_RE.match(text[j]):
                j += 1
            seg = text[i:j]
            if len(seg) == 1:
                out.append((seg, i))
            else:
                for k in range(len(seg) - 1):
                    out.append((seg[k:k + 2], i + k))
            i = j
        elif ch.isascii() and ch.isalnum():
            j = i
            while j < n and text[j].isascii() and text[j].isalnum():
                j += 1
            word = text[i:j].lower()
            if len(word) >= 2 or any(c.isdigit() for c in word):
                out.append((word, i))
            i = j
        else:
            i += 1
    return out


def claim_terms(claim: str) -> set:
    """轻量关键词抽取（去停用词后的词条集合）。"""
    return {term for term, _ in _terms_with_positions(claim or "")} - _STOPWORDS


def find_in_source(claim: str, text: str, *, window_chars: int = WINDOW_CHARS,
                   stride: int = WINDOW_STRIDE, top_k: int = TOP_K) -> List[SourceWindow]:
    """在 ``text`` 中定位与 ``claim`` 字面重叠最高的 Top-K 窗口（分数降序）。

    - ``text`` 不超窗长 ⇒ 整段单窗口（有重叠时）；
    - 无重叠 ⇒ 空列表（调用方降级，不阻塞）。
    """
    terms = claim_terms(claim)
    if not terms or not text:
        return []
    positions: Dict[str, List[int]] = {}
    for term, pos in _terms_with_positions(text):
        if term in terms:
            positions.setdefault(term, []).append(pos)
    if not positions:
        return []
    n = len(text)
    if n <= window_chars:
        return [SourceWindow(0, n, text, round(len(positions) / len(terms), 4))]
    starts = list(range(0, max(1, n - window_chars + 1), max(1, stride)))
    if starts[-1] + window_chars < n:  # 末尾锚定，保证尾部可被覆盖
        starts.append(n - window_chars)
    candidates: List[SourceWindow] = []
    for start in starts:
        end = start + window_chars
        hits = 0
        for plist in positions.values():
            idx = bisect_left(plist, start)
            if idx < len(plist) and plist[idx] < end:
                hits += 1
        if hits:
            candidates.append(
                SourceWindow(start, end, text[start:end], round(hits / len(terms), 4)))
    if not candidates:
        return []
    candidates.sort(key=lambda w: (-w.score, w.start))
    return candidates[:max(1, top_k)]


def select_windows(windows: Sequence[SourceWindow], *,
                   max_total_chars: int) -> List[SourceWindow]:
    """贪心选**互不重叠**的窗口（分数降序），总字符不超预算；输出按位置排序。"""
    picked: List[SourceWindow] = []
    total = 0
    for window in sorted(windows, key=lambda w: (-w.score, w.start)):
        if any(not (window.end <= p.start or window.start >= p.end) for p in picked):
            continue
        if total + len(window.text) > max_total_chars:
            continue
        picked.append(window)
        total += len(window.text)
    picked.sort(key=lambda w: w.start)
    return picked
