"""网页正文提取（需求 34 E1 / TBD-3 拍板口径，2026-10-11）。

口径（需求 34 §5 TBD-3）：
- bs4 结构化优先：移除 script / style / noscript / nav / header / footer / aside；
  正文容器优先 ``<main>`` → ``<article>`` → ``<body>`` 兜底；
- 空白折叠（连续空白 → 单空格；段落换行最多保留 1 个空行）；
- 入库上限按 **UTF-8 字节数**（TBD-1 拍板）：超限**截断并标记**（``truncated=True``），不得静默丢弃；
- 编码：HTTP ``Content-Type`` charset → ``<meta charset>`` → utf-8 ``errors="replace"``；
- PDF：按 content-type 或 URL 扩展名**双重判定** ⇒ 不抓正文（kind=``pdf``）；
- **必须落 `extractor_version`**（D-06 机理）：清洗规则变化会改变字符偏移，
  没有版本号就无法判断旧 offset 是否可信（E2/E5 的 offset 红线依赖它）。
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

#: 提取器版本（TBD-3 强制落盘；清洗规则变更时必须递增）
EXTRACTOR_VERSION = "webtext.v1"

_STRIP_TAGS = ("script", "style", "noscript", "nav", "header", "footer", "aside")
_HTML_TYPES = ("text/html", "application/xhtml+xml")
_TEXT_TYPES = ("text/plain",)
_META_CHARSET_RE = re.compile(
    rb"""<meta[^>]+charset\s*=\s*["']?\s*([a-zA-Z0-9_\-]+)""", re.IGNORECASE)


@dataclass(frozen=True)
class ExtractedText:
    """一次正文提取的结果。``kind`` 取值与抓取层状态映射见 ``web_fetch``。"""

    kind: str  # "html" | "text" | "pdf" | "unsupported" | "empty"
    text: str = ""
    truncated: bool = False


def base_content_type(content_type: str) -> str:
    """取 MIME 主类型（去参数、小写）。"""
    return (content_type or "").split(";")[0].strip().lower()


def looks_like_pdf(content_type: str, url: str) -> bool:
    """PDF 双重判定（content-type 或 URL 扩展名；TBD-3）。"""
    if base_content_type(content_type) == "application/pdf":
        return True
    return urlsplit(url).path.lower().endswith(".pdf")


def _charset_from_content_type(content_type: str) -> str:
    for part in (content_type or "").split(";"):
        part = part.strip()
        if part.lower().startswith("charset="):
            return part.split("=", 1)[1].strip().strip('"').strip("'")
    return ""


def decode_body(body: bytes, content_type: str) -> str:
    """解码响应体：Content-Type charset → meta charset → utf-8(errors=replace)。"""
    charset = _charset_from_content_type(content_type)
    if not charset:
        match = _META_CHARSET_RE.search(body[:4096])
        if match:
            charset = match.group(1).decode("ascii", errors="ignore")
    for candidate in (charset, "utf-8"):
        if not candidate:
            continue
        try:
            return body.decode(candidate, errors="replace")
        except LookupError:  # 未知 charset 名 → 尝试下一个
            continue
    return body.decode("utf-8", errors="replace")


def _collapse_whitespace(text: str) -> str:
    """空白折叠：行内连续空白 → 单空格；保留段落（最多 1 个空行）。"""
    lines = []
    blank_run = 0
    for line in text.splitlines():
        cleaned = re.sub(r"[ \t\u3000\u00a0]+", " ", line).strip()
        if cleaned:
            lines.append(cleaned)
            blank_run = 0
        elif lines:
            blank_run += 1
            if blank_run == 1:
                lines.append("")
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines)


def _truncate_utf8(text: str, max_bytes: int) -> tuple[str, bool]:
    """按 UTF-8 字节数截断（不切断多字节字符）；返回 (text, truncated)。"""
    encoded = text.encode("utf-8")
    if len(encoded) <= max_bytes:
        return text, False
    return encoded[:max_bytes].decode("utf-8", errors="ignore"), True


def extract_text(body: bytes, content_type: str, url: str, *, max_bytes: int) -> ExtractedText:
    """提取可读正文（纯函数，零网络）。

    - ``kind="pdf"``：PDF（不抓正文；抓取层记 ``skipped_pdf``）；
    - ``kind="unsupported"``：非 HTML/纯文本类型（抓取层映射 ``parse_error``）；
    - ``kind="empty"``：HTML 但提取不到正文（抓取层标「暂不支持」）；
    - ``kind="html"/"text"``：成功（text 已按 max_bytes 截断并标记）。
    """
    if looks_like_pdf(content_type, url):
        return ExtractedText(kind="pdf")

    ctype = base_content_type(content_type)
    if ctype in _TEXT_TYPES:
        text, truncated = _truncate_utf8(
            _collapse_whitespace(decode_body(body, content_type)), max_bytes)
        if not text:
            return ExtractedText(kind="empty")
        return ExtractedText(kind="text", text=text, truncated=truncated)

    # 无 content-type 时按 HTML 尝试（浏览器嗅探语义）；其余类型判不支持
    if ctype and ctype not in _HTML_TYPES:
        return ExtractedText(kind="unsupported")

    soup = BeautifulSoup(decode_body(body, content_type), "html.parser")
    for tag in soup(_STRIP_TAGS):
        tag.decompose()
    node = soup.find("main") or soup.find("article") or soup.body or soup
    text = _collapse_whitespace(node.get_text(separator="\n"))
    if not text:
        return ExtractedText(kind="empty")
    text, truncated = _truncate_utf8(text, max_bytes)
    return ExtractedText(kind="html", text=text, truncated=truncated)


def charset_of(content_type: str) -> Optional[str]:
    """（调试用）从 content-type 提取 charset，无则 None。"""
    return _charset_from_content_type(content_type) or None
