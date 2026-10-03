"""结构化解析快照（需求 23 §3.1 / §4）：解析一次，多次利用。

三层数据中的第一层——**重新分块的唯一输入**。设计要点：

- 快照存「结构块」而非原文件：原文件仍可按保留期删除（合规口径不变），
  但标题路径 / 页码 / 表格行列等结构信息被保留；
- 每个块携带 `title_path`（该块所属章节路径）与 `locator`（页码 / 幻灯片 /
  工作表 / 行范围），供分块 v2 生成稳定定位（引用回溯的基础）；
- 表格以 `rows` 保存（首行视为表头），分块 v2 大表拆分组时重复表头；
- PDF 能力边界如实处理：仅文字版可抽取，标题识别采用保守启发式（不强承诺）。

解析器统一返回 `list[SnapshotBlock]`；pptx / xlsx 依赖懒加载（模块缺失时抛 ImportError，
由摄取管线转结构化错误）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import List, Tuple

_HEADING_MD = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_FENCE = re.compile(r"^\s*```")
_TABLE_ROW = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP = re.compile(r"^\s*\|?[\s:|-]+\|?\s*$")
_PDF_NUM_HEADING = re.compile(
    r"^\s*(?:第[一二三四五六七八九十百\d]+[章节篇]|[一二三四五六七八九十]+、|\d+(?:\.\d+)*[、.．]?\s+\S)")


@dataclass(frozen=True)
class SnapshotBlock:
    """解析快照中的一个结构块。

    kind ∈ heading | paragraph | table | code | slide | sheet
    - heading：仅作结构标记（分块 v2 不产 chunk，其文本进 title_path）；
    - table/sheet：`rows` 为行列结构（首行表头）；`text` 为 TSV 展示；
    - title_path：该块所属章节路径（heading 块为「含自身」的路径）。
    """

    kind: str
    text: str
    title_path: Tuple[str, ...] = ()
    locator: dict = field(default_factory=dict)
    rows: Tuple[Tuple[str, ...], ...] = ()


def _table_text(rows: List[List[str]]) -> str:
    return "\n".join("\t".join(cell for cell in row) for row in rows)


# ------------------------------------------------------------------ Markdown


def parse_markdown_blocks(text: str) -> List[SnapshotBlock]:
    """Markdown / 纯文本：标题栈 + 段落 + 表格 + 代码块。"""
    blocks: List[SnapshotBlock] = []
    path: List[str] = []
    para: List[str] = []
    fence_lines: List[str] = []
    in_fence = False

    def flush_para() -> None:
        if para:
            blocks.append(SnapshotBlock(kind="paragraph", text="\n".join(para),
                                        title_path=tuple(path)))
            para.clear()

    lines = text.splitlines()
    i = 0
    while i < len(lines):
        line = lines[i]
        if in_fence:
            if _FENCE.match(line):
                blocks.append(SnapshotBlock(kind="code", text="\n".join(fence_lines),
                                            title_path=tuple(path)))
                fence_lines.clear()
                in_fence = False
            else:
                fence_lines.append(line)
            i += 1
            continue
        if _FENCE.match(line):
            flush_para()
            in_fence = True
            i += 1
            continue
        heading = _HEADING_MD.match(line)
        if heading:
            flush_para()
            level = len(heading.group(1))
            path[:] = path[: level - 1] + [heading.group(2).strip()]
            blocks.append(SnapshotBlock(kind="heading", text=heading.group(2).strip(),
                                        title_path=tuple(path)))
            i += 1
            continue
        if _TABLE_ROW.match(line) and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1]):
            flush_para()
            rows: List[List[str]] = []
            header = [c.strip() for c in _TABLE_ROW.match(line).group(1).split("|")]
            rows.append(header)
            i += 2
            while i < len(lines):
                m = _TABLE_ROW.match(lines[i])
                if not m:
                    break
                rows.append([c.strip() for c in m.group(1).split("|")])
                i += 1
            blocks.append(SnapshotBlock(kind="table", text=_table_text(rows),
                                        title_path=tuple(path),
                                        rows=tuple(tuple(r) for r in rows)))
            continue
        if not line.strip():
            flush_para()
        else:
            para.append(line)
        i += 1
    if in_fence and fence_lines:
        blocks.append(SnapshotBlock(kind="code", text="\n".join(fence_lines),
                                    title_path=tuple(path)))
    flush_para()
    return blocks


# ------------------------------------------------------------------ DOCX


def parse_docx_blocks(path: str) -> List[SnapshotBlock]:
    import docx  # 懒加载

    doc = docx.Document(path)
    blocks: List[SnapshotBlock] = []
    path_stack: List[str] = []
    for paragraph in doc.paragraphs:
        text = (paragraph.text or "").strip()
        if not text:
            continue
        style = (paragraph.style.name if paragraph.style is not None else "") or ""
        m = re.match(r"Heading\s*(\d+)", style, re.IGNORECASE)
        if m:
            level = max(1, int(m.group(1)))
            path_stack[:] = path_stack[: level - 1] + [text]
            blocks.append(SnapshotBlock(kind="heading", text=text,
                                        title_path=tuple(path_stack)))
        else:
            blocks.append(SnapshotBlock(kind="paragraph", text=text,
                                        title_path=tuple(path_stack)))
    for table in getattr(doc, "tables", []):
        rows = [[(cell.text or "").strip() for cell in row.cells] for row in table.rows]
        if rows:
            blocks.append(SnapshotBlock(kind="table", text=_table_text(rows),
                                        title_path=tuple(path_stack),
                                        rows=tuple(tuple(r) for r in rows)))
    return blocks


# ------------------------------------------------------------------ PDF


def parse_pdf_blocks(path: str) -> List[SnapshotBlock]:
    """PDF：按页抽取，保守识别编号标题；locator 记录页码（1-based）。"""
    from pypdf import PdfReader  # 懒加载

    reader = PdfReader(path)
    from config import config as _config  # 懒加载（保持模块可独立测试）

    if len(reader.pages) > _config.rag.max_pages:
        raise ValueError(f"PDF 页数 {len(reader.pages)} 超过上限 {_config.rag.max_pages}")
    blocks: List[SnapshotBlock] = []
    path_stack: List[str] = []
    for page_number, page in enumerate(reader.pages, start=1):
        text = page.extract_text() or ""
        para: List[str] = []

        def flush(page_number: int = page_number) -> None:
            if para:
                blocks.append(SnapshotBlock(kind="paragraph", text="\n".join(para),
                                            title_path=tuple(path_stack),
                                            locator={"page": page_number}))
                para.clear()

        for raw_line in text.splitlines():
            line = raw_line.strip()
            if not line:
                flush()
                continue
            if _PDF_NUM_HEADING.match(line) and len(line) <= 60:
                flush()
                path_stack[:] = [line]
                blocks.append(SnapshotBlock(kind="heading", text=line,
                                            title_path=tuple(path_stack),
                                            locator={"page": page_number}))
            else:
                para.append(line)
        flush()
    return blocks


# ------------------------------------------------------------------ HTML


def parse_html_blocks(text: str) -> List[SnapshotBlock]:
    """HTML：去导航噪声，保留标题路径 + 段落 + 表格。"""
    from bs4 import BeautifulSoup  # 懒加载（依赖已有）

    soup = BeautifulSoup(text, "html.parser")
    for tag in soup(["script", "style", "nav", "header", "footer", "aside", "noscript"]):
        tag.decompose()
    blocks: List[SnapshotBlock] = []
    path_stack: List[str] = []
    for el in soup.find_all(["h1", "h2", "h3", "h4", "h5", "h6", "p", "pre", "table", "li"]):
        name = el.name
        if name.startswith("h") and len(name) == 2 and name[1].isdigit():
            title = el.get_text(" ", strip=True)
            if not title:
                continue
            level = int(name[1])
            path_stack[:] = path_stack[: level - 1] + [title]
            blocks.append(SnapshotBlock(kind="heading", text=title,
                                        title_path=tuple(path_stack)))
            continue
        if name == "table":
            rows: List[List[str]] = []
            for tr in el.find_all("tr"):
                cells = [td.get_text(" ", strip=True) for td in tr.find_all(["td", "th"])]
                if cells:
                    rows.append(cells)
            if rows:
                blocks.append(SnapshotBlock(kind="table", text=_table_text(rows),
                                            title_path=tuple(path_stack),
                                            rows=tuple(tuple(r) for r in rows)))
            continue
        content = el.get_text("\n" if name == "pre" else " ", strip=True)
        if content:
            blocks.append(SnapshotBlock(kind="code" if name == "pre" else "paragraph",
                                        text=content, title_path=tuple(path_stack)))
    return blocks


# ------------------------------------------------------------------ PPTX


def parse_pptx_blocks(path: str) -> List[SnapshotBlock]:
    """PPT：每页一个结构块（标题入 title_path，文本框合并为正文）。"""
    from pptx import Presentation  # 懒加载（python-pptx）

    deck = Presentation(path)
    blocks: List[SnapshotBlock] = []
    for slide_number, slide in enumerate(deck.slides, start=1):
        title = ""
        try:
            if slide.shapes.title is not None:
                title = (slide.shapes.title.text or "").strip()
        except Exception:  # noqa: BLE001 —— 无标题版式
            title = ""
        parts: List[str] = []
        for shape in slide.shapes:
            if not getattr(shape, "has_text_frame", False):
                continue
            content = (shape.text_frame.text or "").strip()
            if not content or content == title:
                continue
            parts.append(content)
        if not title and not parts:
            continue
        title_path = (title,) if title else ()
        if title:
            blocks.append(SnapshotBlock(kind="heading", text=title, title_path=title_path,
                                        locator={"slide": slide_number}))
        for part in parts:
            blocks.append(SnapshotBlock(kind="paragraph", text=part, title_path=title_path,
                                        locator={"slide": slide_number}))
    return blocks


# ------------------------------------------------------------------ XLSX


def parse_xlsx_blocks(path: str, *, max_rows: int = 5000) -> List[SnapshotBlock]:
    """XLSX：每张工作表一个 table 结构块（首行表头；locator 记录 sheet 与行范围）。"""
    from openpyxl import load_workbook  # 懒加载（openpyxl）

    workbook = load_workbook(path, read_only=True, data_only=True)
    blocks: List[SnapshotBlock] = []
    for sheet in workbook.worksheets:
        rows: List[List[str]] = []
        for row_index, row in enumerate(sheet.iter_rows(values_only=True), start=1):
            if row_index > max_rows:
                break
            cells = ["" if value is None else str(value) for value in row]
            if any(cell.strip() for cell in cells):
                rows.append(cells)
        if not rows:
            continue
        blocks.append(SnapshotBlock(
            kind="sheet", text=_table_text(rows),
            title_path=(sheet.title,),
            locator={"sheet": sheet.title, "row_range": [1, len(rows)]},
            rows=tuple(tuple(r) for r in rows)))
    workbook.close()
    return blocks


# ------------------------------------------------------------------ Dispatcher


def snapshot_file(path: str) -> List[SnapshotBlock]:
    """按扩展名解析为结构快照（与 upload_guard 白名单一致）。"""
    ext = os.path.splitext(path)[1].lower()
    if ext == ".pdf":
        return parse_pdf_blocks(path)
    if ext == ".docx":
        return parse_docx_blocks(path)
    if ext in (".md", ".markdown", ".txt", ".text"):
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return parse_markdown_blocks(handle.read())
    if ext in (".html", ".htm"):
        with open(path, encoding="utf-8", errors="ignore") as handle:
            return parse_html_blocks(handle.read())
    if ext == ".pptx":
        return parse_pptx_blocks(path)
    if ext == ".xlsx":
        return parse_xlsx_blocks(path)
    raise ValueError(f"不支持的文档类型: {ext}")
