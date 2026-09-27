"""外部内容净化（P2-1a）：剥离不可见 Unicode。

OWASP LLM01:2026 明确：tag-block（U+E0000–E007F）、变体选择符（U+FE00–FE0F）、
零宽字符（U+200B/C/D、U+2060）在正常渲染中不可见，可被用于夹带指令或外泄字节；
**所有 ingest / render 边界都应剥离**。

本模块只做确定性净化（不改写可见文本语义），在外部内容进入系统的位置调用：
搜索片段 / arXiv 摘要 / RAG 分块（摄入与检索两侧）。
"""
from __future__ import annotations

#: 需剥离的不可见/走私字符区间（含零宽、变体选择符、tag-block、BOM、软连字符等）
_INVISIBLE_RANGES = (
    (0x00AD, 0x00AD),   # soft hyphen
    (0x200B, 0x200F),   # zero-width space/joiner/non-joiner + LRM/RLM
    (0x202A, 0x202E),   # bidi embedding/override
    (0x2060, 0x2064),   # word joiner / invisible operators
    (0x2066, 0x2069),   # bidi isolates
    (0xFE00, 0xFE0F),   # variation selectors
    (0xFEFF, 0xFEFF),   # BOM / zero-width no-break space
    (0xFFF9, 0xFFFB),   # interlinear annotation
    (0xE0000, 0xE007F), # tag block（走私任意字节）
    (0xE0100, 0xE01EF), # variation selectors supplement
)


def _is_invisible(char: str) -> bool:
    code = ord(char)
    return any(start <= code <= end for start, end in _INVISIBLE_RANGES)


def strip_invisible(text: str) -> str:
    """剥离不可见/走私字符；无命中时原样返回（对正常文本零影响）。"""
    if not text:
        return text
    return "".join(char for char in text if not _is_invisible(char))


def count_invisible(text: str) -> int:
    """统计不可见字符数量（观测/审计用，不返回原文）。"""
    if not text:
        return 0
    return sum(1 for char in text if _is_invisible(char))
