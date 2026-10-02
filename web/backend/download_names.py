"""导出下载名（需求 17 / #77）：话题名 → 安全的 Content-Disposition 文件名。

背景：导出 .md 原文件名是 `deepresearch-<run_id>.md`，识别度差；改为话题名。

行业口径（RFC 6266 + RFC 5987）：
- `filename`（ASCII 回退）在前、`filename*`（UTF-8 percent-encoded）在后，
  接收方优先 `filename*`；不支持的旧 UA 退回 ASCII 名；
- 清洗：复用 `upload_guard.sanitize_filename`（去路径 / 控制字符 / 危险符号）；
- 截断：展示名 ≤ `MAX_TOPIC_CHARS`；空话题 / 清洗后为空 → 回退 `deepresearch-<run_id>`。

安全边界：本模块只产出**头部字符串**，不参与任何文件系统路径拼接；
服务端自建的 ASCII 回退名不含用户输入。
"""
from __future__ import annotations

from typing import Optional, Tuple
from urllib.parse import quote

from .upload_guard import sanitize_filename

MAX_TOPIC_CHARS = 50


def build_export_filename(
    topic: Optional[str], run_id: str, ext: str = ".md",
) -> Tuple[str, str]:
    """返回 ``(display_filename, ascii_fallback)``。

    - ``display_filename``：清洗 + 截断后的话题名（可含中文）；
    - ``ascii_fallback``：纯 ASCII 的回退名；话题可 ASCII 表达时与 display 相同，
      否则固定 ``deepresearch-<run_id><ext>``。
    """
    cleaned = sanitize_filename(topic)[:MAX_TOPIC_CHARS].strip(" .")
    fallback = f"deepresearch-{run_id}{ext}"
    # sanitize_filename 对空输入返回 "upload"（上传语义）；导出场景应回退 run_id
    if not cleaned or cleaned == "upload":
        return fallback, fallback
    display = f"{cleaned}{ext}"
    ascii_fallback = display if display.isascii() else fallback
    return display, ascii_fallback


def content_disposition(display: str, ascii_fallback: str) -> str:
    """RFC 6266 头部值：``filename``（ASCII 回退）在前，``filename*``（UTF-8）在后。"""
    return (
        f'attachment; filename="{ascii_fallback}"; '
        f"filename*=UTF-8''{quote(display, safe='')}"
    )
