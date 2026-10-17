"""需求 38 F2：PDF 渲染器（在隔离子进程内运行；受限 HTML；WeasyPrint）。

安全边界（§12.2）：
- **受限 markdown→HTML**：全部内容转义，只识别结构（标题/列表/引用/代码块），
  **不解释任何原始 HTML** ⇒ 报告正文无法注入标记或资源引用；
- **url_fetcher 拒绝一切外部资源**（file/http/https/…）⇒ 正文无法触发本机文件读取
  或网络请求；v1 报告不含图片，fetcher 在正常路径不会被调用（仍保留为硬边界）；
- 输出约束：页数上限（交付约束）与输出字节上限在返回前校验，超限报结构化错误
  （不静默截断）。

子进程协议（`spawn`，由 export_worker 的 supervisor 管理）：常驻循环，通过 Pipe 接收
`{"doc": <ExportDocument 序列化>, "max_pages": int, "max_output_bytes": int}`，
返回 `{"ok": true, "pdf": bytes, "pages": int, "sha256": str}` 或
`{"ok": false, "error_code": str, "detail": str}`；`None` 表示退出。
子进程**不连接数据库**（输入经 Pipe 传递，保持依赖最小与连接隔离）。
"""
from __future__ import annotations

import hashlib
import re
from typing import Any, Dict, Optional, Tuple


#: 渲染错误（error_code 落作业表；detail 截断且不含正文）
class PdfRenderError(RuntimeError):
    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}"[:300])
        self.code = code


def _escape(text: str) -> str:
    return (text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


_ORDERED_RE = re.compile(r"^\d{1,4}\.\s+(.*)$")


def markdown_to_restricted_html(md_text: str) -> str:
    """受限转换：结构识别（# 标题 / - 列表 / 1. 列表 / > 引用 / ``` 代码块），全量转义。

    不引入 markdown 库：转换规则固定、确定性、无 HTML 解释面（安全优先于语法覆盖）。
    """
    parts: list[str] = []
    para: list[str] = []
    in_code = False
    list_tag: Optional[str] = None

    def flush_para() -> None:
        if para:
            parts.append("<p>" + "<br/>".join(_escape(x) for x in para) + "</p>")
            para.clear()

    def close_list() -> None:
        nonlocal list_tag
        if list_tag:
            parts.append(f"</{list_tag}>")
            list_tag = None

    for ln in (md_text or "").splitlines():
        stripped = ln.strip()
        if stripped.startswith("```"):
            flush_para()
            close_list()
            parts.append("</pre>" if in_code else "<pre>")
            in_code = not in_code
            continue
        if in_code:
            parts.append(_escape(ln))
            continue
        if not stripped:
            flush_para()
            close_list()
            continue
        if stripped.startswith("#"):
            flush_para()
            close_list()
            level = min(6, len(stripped) - len(stripped.lstrip("#")))
            parts.append(f"<h{level}>{_escape(stripped[level:].strip())}</h{level}>")
            continue
        if stripped.startswith(("- ", "* ")):
            flush_para()
            if list_tag != "ul":
                close_list()
                parts.append("<ul>")
                list_tag = "ul"
            parts.append(f"<li>{_escape(stripped[2:].strip())}</li>")
            continue
        m = _ORDERED_RE.match(stripped)
        if m:
            flush_para()
            if list_tag != "ol":
                close_list()
                parts.append("<ol>")
                list_tag = "ol"
            parts.append(f"<li>{_escape(m.group(1).strip())}</li>")
            continue
        if stripped.startswith(">"):
            flush_para()
            close_list()
            parts.append(f"<blockquote>{_escape(stripped.lstrip('>').strip())}</blockquote>")
            continue
        para.append(ln)
    flush_para()
    close_list()
    if in_code:
        parts.append("</pre>")
    return "\n".join(parts)


#: 版式（结构正确优先；CJK 字体族与镜像 fonts-noto-cjk 对齐）
_PAGE_CSS = """
@page { size: A4; margin: 18mm 16mm; }
body { font-family: "Noto Sans CJK SC", "Noto Sans CJK JP", sans-serif;
       font-size: 11pt; line-height: 1.5; color: #1a1a1a; }
h1 { font-size: 17pt; margin: 0 0 6pt; } h2 { font-size: 14pt; margin: 14pt 0 5pt; }
h3 { font-size: 12.5pt; margin: 10pt 0 4pt; }
p { margin: 4pt 0; } ul, ol { margin: 4pt 0 4pt 18pt; }
pre { font-size: 9.5pt; background: #f6f6f6; padding: 6px; white-space: pre-wrap;
      border-radius: 3px; }
blockquote { color: #555; border-left: 3px solid #cccccc; padding-left: 8px;
             margin: 6pt 0; }
.meta { color: #555555; font-size: 9.5pt; }
hr { border: none; border-top: 1px solid #dddddd; margin: 10pt 0; }
"""


def _deny_all_fetcher(url: str, *args: Any, **kwargs: Any) -> None:
    """拒绝一切外部资源（返回 None ⇒ 资源不可用；不发起任何读取/网络请求）。"""
    return None


def document_to_html(doc: Dict[str, Any]) -> str:
    """ExportDocument（dict 形态）→ 完整 HTML（与 render_markdown 同信息结构）。"""
    def _meta(value: Any) -> str:
        return "n/a" if value is None else str(value)

    head = [
        f"<h1>{_escape(str(doc.get('topic') or ''))}：爱考据 iKaoju 报告导出</h1>",
        f"<p class='meta'>run_id {_escape(str(doc.get('run_id') or ''))}"
        f" · 导出时间 {_escape(str(doc.get('generated_at') or ''))}</p>",
        f"<p class='meta'>run_status <b>{_escape(str(doc.get('run_status')))}</b>"
        f" · 停止原因 <b>{_escape(str(doc.get('stop_reason')))}</b>"
        f"{' · 已取消' if doc.get('cancelled') else ''}</p>",
        f"<p class='meta'>token {_escape(str(doc.get('token_used')))}"
        f" · 成本估算 ¥{_escape(str(doc.get('cost_estimate_cny')))}"
        f" · 耗时 {_escape(str(doc.get('elapsed_seconds')))}s"
        f" · 降级条目 {_meta(doc.get('degradation_count'))}"
        f" · 检索深度 {_meta(doc.get('depth'))}</p>",
        "<hr/>",
    ]
    report = str(doc.get("report") or "").strip()
    body = [markdown_to_restricted_html(report) if report
            else "<p>（本次运行未产出报告正文）</p>"]

    citations = doc.get("citations")
    if citations is None:
        body.append("<h2>引用清单</h2><p>（不可用：历史产物未保留引用数据）</p>")
    elif citations:
        items = []
        for i, c in enumerate(citations, start=1):
            verified = "存在性校验通过" if c.get("existence") else "存在性校验未通过"
            supported = "论断受支持" if c.get("supported") else "论断未获支持"
            confidence = c.get("confidence")
            conf = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "n/a"
            claim = str(c.get("claim", "")).strip() or "（无论断）"
            note = str(c.get("note", "")).strip()
            note_html = f"　备注：{_escape(note)}" if note else ""
            items.append(
                f"<li>{_escape(claim)} — &lt;{_escape(str(c.get('source', '')))}&gt;"
                f"（{_escape(str(c.get('source_type', 'unknown')))} · 置信度 {conf}"
                f" · {verified} · {supported}）{note_html}</li>")
        body.append("<h2>引用清单</h2><ol>" + "".join(items) + "</ol>")

    visited = doc.get("visited_sources")
    if visited is None:
        body.append("<h2>访问过的来源</h2><p>（不可用：历史产物未保留来源数据）</p>")
    elif visited:
        body.append("<h2>访问过的来源</h2><ul>"
                    + "".join(f"<li>&lt;{_escape(str(s))}&gt;</li>" for s in visited)
                    + "</ul>")

    return ("<!DOCTYPE html><html><head><meta charset='utf-8'><style>"
            + _PAGE_CSS + "</style></head><body>" + "\n".join(head + body)
            + "</body></html>")


def render_pdf_from_doc(doc: Dict[str, Any], *, max_pages: int,
                        max_output_bytes: int) -> Tuple[bytes, int]:
    """渲染 PDF；返回 `(pdf_bytes, pages)`；超限抛 `PdfRenderError`（结构化错误码）。"""
    from weasyprint import HTML  # 延迟导入：无 pango 环境（如 Windows 开发机）仅影响渲染路径

    html_doc = document_to_html(doc)
    rendered = HTML(string=html_doc, base_url=None,
                    url_fetcher=_deny_all_fetcher).render()
    pages = len(rendered.pages)
    if pages > max_pages:
        raise PdfRenderError("export_too_many_pages", f"pages={pages} limit={max_pages}")
    pdf = rendered.write_pdf()
    if len(pdf) > max_output_bytes:
        raise PdfRenderError("export_too_large", f"bytes={len(pdf)} limit={max_output_bytes}")
    return pdf, pages


def child_main(conn: Any) -> None:
    """渲染子进程入口（常驻；保持字体/排版引擎热态）。`None` 消息 ⇒ 退出。"""
    try:
        while True:
            msg = conn.recv()
            if msg is None:
                break
            try:
                pdf, pages = render_pdf_from_doc(
                    msg["doc"], max_pages=int(msg["max_pages"]),
                    max_output_bytes=int(msg["max_output_bytes"]))
                conn.send({"ok": True, "pdf": pdf, "pages": pages,
                           "sha256": hashlib.sha256(pdf).hexdigest()})
            except PdfRenderError as exc:
                conn.send({"ok": False, "error_code": exc.code, "detail": str(exc)[:200]})
            except Exception as exc:  # noqa: BLE001 —— 子进程绝不因单作业崩溃退出
                conn.send({"ok": False, "error_code": "export_render_failed",
                           "detail": f"{type(exc).__name__}: {exc}"[:200]})
    finally:
        conn.close()
