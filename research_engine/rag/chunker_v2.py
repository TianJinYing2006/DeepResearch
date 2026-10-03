"""分块 v2（需求 23 §4）：结构保真、token 预算、长段重叠、表格规则化拆分。

输入是 :mod:`research_engine.rag.snapshot` 的结构块（解析快照），输出 `ChunkDraft`：

- **尺寸用 token 预算**（跨中英混排更稳）；字符数仅用于资源限额；
- 标题栈来自快照的 `title_path`（不跨标题合并）；
- **overlap 只用于长文本二次切分**（不机械跨标题、跨表格复制）；
- 表格：小表整表成块；大表按行组拆分并**重复表头**，locator 记录行范围；
- `embed_text = 标题路径 + 正文`（低成本上下文补充）；展示正文 `text` 独立。

本模块纯函数、无外部依赖，可在不装 qdrant/openai 的环境直接单测。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable, List, Sequence, Tuple

from research_engine.rag.snapshot import SnapshotBlock

_CJK = re.compile(r"[\u3040-\u9fff\uf900-\ufaff]")
_SENTENCE = re.compile(r"(?<=[。！？!?；;.\n])")


@dataclass(frozen=True)
class ChunkDraft:
    """分块草稿（generation/chunk_id 由摄取管线在落库时赋予）。"""

    text: str
    embed_text: str
    title_path: Tuple[str, ...]
    locator: dict


def estimate_tokens(text: str) -> int:
    """轻量 token 估算：CJK 字符 ≈ 1 token；其余字符 ≈ 1/4 token（向上取整）。

    只用于分块预算，不用于计费（计费以 provider 返回的 usage 为准）。
    """
    if not text:
        return 0
    cjk = len(_CJK.findall(text))
    other = len(text) - cjk
    return cjk + (other + 3) // 4


def _embed_text(title_path: Sequence[str], text: str) -> str:
    if not title_path:
        return text
    return " > ".join(title_path) + "\n\n" + text


def _tail_tokens(text: str, tokens: int) -> str:
    """从尾部截取 ≈tokens 个 token 的文本（overlap 用；按 CJK/非 CJK 混合估算）。"""
    budget = 0
    index = len(text)
    while index > 0 and budget < tokens:
        char = text[index - 1]
        budget += 1 if _CJK.match(char) else 0.25
        index -= 1
    return text[index:]


def _split_long(text: str, max_tokens: int, overlap_tokens: int) -> List[str]:
    """长文本二次切分：优先按句子打包；超长单句按字符硬切；片间加 overlap。"""
    pieces: List[str] = []
    current = ""
    for sentence in filter(None, _SENTENCE.split(text)):
        if estimate_tokens(sentence) > max_tokens:
            # 超长单句：先冲刷当前，再按字符硬切该句
            if current:
                pieces.append(current)
                current = ""
            piece = ""
            for char in sentence:
                piece += char
                if estimate_tokens(piece) >= max_tokens:
                    pieces.append(piece)
                    piece = ""
            if piece:
                current = piece
            continue
        candidate = current + sentence
        if current and estimate_tokens(candidate) > max_tokens:
            pieces.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        pieces.append(current)
    if len(pieces) <= 1 or overlap_tokens <= 0:
        return pieces
    with_overlap = [pieces[0]]
    for piece in pieces[1:]:
        prefix = _tail_tokens(with_overlap[-1], overlap_tokens)
        with_overlap.append((prefix + piece) if prefix else piece)
    return with_overlap


def _chunk_table(block: SnapshotBlock, max_tokens: int) -> List[ChunkDraft]:
    """表格：小表整表；大表按行组拆分 + 重复表头 + 行范围 locator。"""
    rows = [list(row) for row in block.rows]
    base_locator = dict(block.locator)
    full_text = ("\n".join("\t".join(row) for row in rows) if rows else block.text)
    if not rows:
        return [ChunkDraft(text=full_text, embed_text=_embed_text(block.title_path, full_text),
                           title_path=block.title_path, locator=base_locator)]
    if estimate_tokens(full_text) <= max_tokens:
        return [ChunkDraft(text=full_text,
                           embed_text=_embed_text(block.title_path, full_text),
                           title_path=block.title_path, locator=base_locator)]
    header = "\t".join(rows[0])
    header_tokens = estimate_tokens(header)
    drafts: List[ChunkDraft] = []
    group: List[str] = []
    group_tokens = header_tokens
    group_start = 2  # 1-based 全表行号：第 1 行为表头
    row_number = 2
    for row in rows[1:]:
        row_text = "\t".join(row)
        row_tokens = estimate_tokens(row_text)
        if group and group_tokens + row_tokens > max_tokens:
            text = header + "\n" + "\n".join(group)
            locator = {**base_locator, "row_range": [group_start, row_number - 1]}
            drafts.append(ChunkDraft(text=text, embed_text=_embed_text(block.title_path, text),
                                     title_path=block.title_path, locator=locator))
            group, group_tokens = [], header_tokens
            group_start = row_number
        group.append(row_text)
        group_tokens += row_tokens
        row_number += 1
    if group:
        text = header + "\n" + "\n".join(group)
        locator = {**base_locator, "row_range": [group_start, row_number - 1]}
        drafts.append(ChunkDraft(text=text, embed_text=_embed_text(block.title_path, text),
                                 title_path=block.title_path, locator=locator))
    return drafts


def chunk_blocks(
    blocks: Iterable[SnapshotBlock],
    *,
    max_tokens: int = 400,
    overlap_tokens: int = 60,
) -> List[ChunkDraft]:
    """结构块 → 分块草稿（规则见模块 docstring）。"""
    drafts: List[ChunkDraft] = []
    buffer: List[str] = []
    buffer_title: Tuple[str, ...] = ()
    buffer_locator: dict = {}

    def flush() -> None:
        if not buffer:
            return
        text = "\n\n".join(buffer)
        drafts.append(ChunkDraft(text=text, embed_text=_embed_text(buffer_title, text),
                                 title_path=buffer_title, locator=dict(buffer_locator)))
        buffer.clear()

    for block in blocks:
        if block.kind == "heading":
            continue
        if block.kind in ("table", "sheet"):
            if not block.rows and not block.text.strip():
                continue
            flush()
            drafts.extend(_chunk_table(block, max_tokens))
            continue
        if not block.text.strip():
            continue
        if block.kind == "code" or "slide" in block.locator:
            # 代码块与幻灯片：独立成块（不与其他段落合并）
            flush()
            pieces = ([block.text] if estimate_tokens(block.text) <= max_tokens
                      else _split_long(block.text, max_tokens, overlap_tokens))
            for piece in pieces:
                drafts.append(ChunkDraft(text=piece, embed_text=_embed_text(block.title_path, piece),
                                         title_path=block.title_path, locator=dict(block.locator)))
            continue
        # 普通段落：同标题路径下累积到预算；超预算整段二次切分（long-text overlap）
        if buffer and buffer_title != block.title_path:
            flush()
        if estimate_tokens(block.text) > max_tokens:
            flush()
            pieces = _split_long(block.text, max_tokens, overlap_tokens)
            for piece in pieces:
                drafts.append(ChunkDraft(text=piece, embed_text=_embed_text(block.title_path, piece),
                                         title_path=block.title_path, locator=dict(block.locator)))
            continue
        candidate = estimate_tokens("\n\n".join(buffer + [block.text])) if buffer \
            else estimate_tokens(block.text)
        if buffer and candidate > max_tokens:
            flush()
        if not buffer:
            buffer_title = block.title_path
            buffer_locator = dict(block.locator)
        buffer.append(block.text)
    flush()
    return drafts
