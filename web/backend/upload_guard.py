"""上传防护（P0-8a）：流式落盘 + 三重校验 + 文件名清洗。

OWASP File Upload 基线：
- 扩展名 allowlist（先解码/清洗文件名）+ **不信任 Content-Type** + 文件签名（magic bytes）；
- 服务端生成存储名；原文件名仅清洗后作为展示元数据（`source`）；
- 大小上限在**流式读取**时执行 —— 不把整文件读进内存；
- DOCX 额外做 ZIP 结构校验与解压比检查（压缩炸弹防护）。
"""
from __future__ import annotations

import hashlib
import os
import re
import unicodedata
import zipfile
from typing import Optional

CHUNK_SIZE = 64 * 1024
HEAD_BYTES = 16
MAX_FILENAME_CHARS = 120
DOCX_MAX_ENTRIES = 2000
DOCX_MAX_EXPANSION_RATIO = 50  # 解压后总大小 / 压缩包大小 上限（zip bomb 防护）

ALLOWED_EXT = {".pdf", ".docx", ".pptx", ".xlsx", ".md", ".markdown",
               ".txt", ".text", ".html", ".htm"}


class UploadRejected(Exception):
    """上传被拒（`code` / `message` 由 API 层转结构化错误）。"""

    def __init__(self, code: str, message: str, detail: Optional[str] = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.detail = detail


def sanitize_filename(name: Optional[str]) -> str:
    """清洗原始文件名：去路径 / 控制字符 / 危险符号，保留可读字符，限长。

    仅作展示元数据；存储名由服务端生成（UUID），不参与路径拼接。
    """
    base = os.path.basename((name or "").replace("\\", "/"))
    base = unicodedata.normalize("NFKC", base)
    base = "".join(ch for ch in base if ch.isprintable())
    base = re.sub(r"[^\w.\- ()（）\u4e00-\u9fff]", "_", base, flags=re.UNICODE)
    base = base.strip(" .") or "upload"
    return base[:MAX_FILENAME_CHARS]


def extension_of(name: str) -> str:
    return os.path.splitext(name)[1].lower()


async def stream_to_temp(file, path: str, max_bytes: int) -> tuple[int, str, bytes]:
    """流式写入临时文件并计算 sha256；超限抛 `UploadRejected(payload_too_large)`。

    Returns:
        `(size, sha256_hex, head_bytes)`；`head_bytes` 供 magic bytes 校验。
    """
    hasher = hashlib.sha256()
    total = 0
    head = b""
    with open(path, "wb") as handle:
        while True:
            chunk = await file.read(CHUNK_SIZE)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise UploadRejected(
                    "payload_too_large",
                    f"文件超过 {max_bytes / 1024 / 1024:g}MB 上限",
                    detail=f"size>{max_bytes}",
                )
            if len(head) < HEAD_BYTES:
                head += chunk[: HEAD_BYTES - len(head)]
            hasher.update(chunk)
            handle.write(chunk)
    return total, hasher.hexdigest(), head


def detect_and_validate(path: str, filename: str, head: bytes) -> str:
    """扩展名 + magic bytes 双校验，返回规范类型（`pdf` / `docx` / `text`）。"""
    ext = extension_of(filename)
    if ext not in ALLOWED_EXT:
        raise UploadRejected("unsupported_file_type", "不支持的文件类型",
                             detail=f"allowed={sorted(ALLOWED_EXT)}")
    if ext == ".pdf":
        if not head.startswith(b"%PDF-"):
            raise UploadRejected("unsupported_file_type",
                                 "文件内容与扩展名不符（不是 PDF）",
                                 detail="magic mismatch: pdf")
        return "pdf"
    if ext in (".docx", ".pptx", ".xlsx"):
        if not head.startswith(b"PK\x03\x04"):
            raise UploadRejected("unsupported_file_type",
                                 f"文件内容与扩展名不符（不是 {ext.lstrip('.').upper()}/ZIP）",
                                 detail=f"magic mismatch: {ext.lstrip('.')}")
        _validate_ooxml_zip(path, ext)
        return ext.lstrip(".")
    if b"\x00" in head:
        raise UploadRejected("unsupported_file_type",
                             "文本文件包含二进制内容（疑似伪装）",
                             detail="nul byte in head")
    return "html" if ext in (".html", ".htm") else "text"


#: OOXML 家族（docx/pptx/xlsx）各自的内部结构标记
_OOXML_MARKERS = {".docx": "word/", ".pptx": "ppt/", ".xlsx": "xl/"}


def _validate_ooxml_zip(path: str, ext: str) -> None:
    """OOXML 结构校验（需求 23 扩展 pptx/xlsx）：内部标记 + 解压比（zip bomb 防护）。"""
    marker = _OOXML_MARKERS.get(ext, "word/")
    label = ext.lstrip(".").upper()
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > DOCX_MAX_ENTRIES:
                raise UploadRejected("unsupported_file_type",
                                     f"{label} 内部条目过多（疑似压缩炸弹）")
            names = {info.filename for info in infos}
            if not any(name.startswith(marker) or name == "[Content_Types].xml"
                       for name in names):
                raise UploadRejected("unsupported_file_type",
                                     f"{label} 结构校验失败（缺少 {marker} 内容）")
            packed = os.path.getsize(path)
            expanded = sum(info.file_size for info in infos)
            if packed > 0 and expanded > packed * DOCX_MAX_EXPANSION_RATIO:
                raise UploadRejected("unsupported_file_type",
                                     f"{label} 解压比例异常（疑似压缩炸弹）")
    except zipfile.BadZipFile as exc:
        raise UploadRejected("unsupported_file_type", f"{label} 不是有效的 ZIP 包") from exc
