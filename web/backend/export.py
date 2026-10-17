"""报告导出（P1-6；需求 38 F1：统一装配模型）。

**为什么放在后端**：前端原本就有「下载 .md」按钮，但那份 md 是**纯报告正文**
（`result.report`），不含 run_id / run_status / 降级条数等审计元数据，也不含引用清单
⇒ 导出的文件脱离页面后无法自证来源。后端导出把元数据 + 正文 + 引用清单一次拼齐。

需求 38 F1（§12.2）起：本模块提供**版本化 `ExportDocument` 中间模型**——私有导出的
内存/store 双路径、以及后续 PDF/DOCX 全部从同一装配入口渲染；**JSON 导出保持既有
载荷结构契约**（`build_export_payload` 的输出即契约，测试锁定 result 字段集），
不经过本模型投影。

🚨 边界：导出的是**已产生的结果**（内存或任务库产物），按需渲染、不落盘、不写库
（D-19 延续）。历史 run 缺失字段时显式标注不可用（`None` ⇒ 渲染为「不可用」），
不伪造数据。
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime
from typing import Any, Dict, List, Optional

#: 导出装配模型 schema 版本（需求 38 §12.2：版本化；字段变更须递增）
EXPORT_DOCUMENT_SCHEMA_VERSION = 1


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def build_export_payload(
    *,
    run_id: str,
    topic: str,
    meta: Dict[str, Any],
    result: Dict[str, Any],
    egress: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """组装导出载荷：元数据 + 结果，**结构即 JSON 导出契约**（不经 ExportDocument 投影）。

    `egress`（P1-9）：run 创建时固化的数据流向快照（谁收到了什么；不含密钥）。
    """
    payload = {
        "run_id": run_id,
        "topic": topic,
        "generated_at": _now_iso(),
        "run_status": meta.get("run_status"),
        "stop_reason": meta.get("stop_reason"),
        "cancelled": bool(meta.get("cancelled")),
        "token_used": meta.get("token_used", 0),
        "cost_estimate_cny": meta.get("cost_estimate_cny", 0.0),
        "elapsed_seconds": meta.get("elapsed_seconds", 0),
        "degradation_count": meta.get("degradation_count", 0),
        "depth": meta.get("depth", result.get("depth", 0)),
        "result": result,
    }
    if egress is not None:
        payload["egress"] = egress
    return payload


@dataclass(frozen=True)
class ExportDocument:
    """版本化导出装配模型（需求 38 F1 / §12.2）：md/pdf/docx 的唯一数据来源。

    字段口径：
    - `report`：报告正文**原文照录**（终局实际保存的文本；不承诺是 writer 原始稿——
      终局优先 `report_display`，见需求 38 §12.5 P1 记录）；
    - `citations` / `visited_sources`：`None` = **不可用**（历史产物未保留，渲染显式标注）；
      空列表 = 已知为空（渲染时省略该节，与既有内存路径行为一致）；
    - 其余元数据与 `build_export_payload` 同口径。
    """

    run_id: str
    topic: str
    generated_at: str
    run_status: Optional[str] = None
    stop_reason: Optional[str] = None
    cancelled: bool = False
    token_used: int = 0
    cost_estimate_cny: float = 0.0
    elapsed_seconds: Any = 0
    degradation_count: Optional[int] = 0
    depth: Optional[int] = 0
    report: str = ""
    citations: Optional[List[Dict[str, Any]]] = None
    visited_sources: Optional[List[str]] = None
    egress: Optional[Dict[str, Any]] = None
    schema_version: int = field(default=EXPORT_DOCUMENT_SCHEMA_VERSION)


def document_from_payload(payload: Dict[str, Any]) -> ExportDocument:
    """内存路径装配：由导出载荷构建 `ExportDocument`（内存路径字段完整，无不可用态）。"""
    result = payload.get("result")
    result = result if isinstance(result, dict) else None
    return ExportDocument(
        run_id=str(payload.get("run_id") or ""),
        topic=str(payload.get("topic") or ""),
        generated_at=str(payload.get("generated_at") or _now_iso()),
        run_status=payload.get("run_status"),
        stop_reason=payload.get("stop_reason"),
        cancelled=bool(payload.get("cancelled")),
        token_used=int(payload.get("token_used") or 0),
        cost_estimate_cny=float(payload.get("cost_estimate_cny") or 0.0),
        elapsed_seconds=payload.get("elapsed_seconds", 0),
        degradation_count=int(payload.get("degradation_count") or 0),
        depth=int(payload.get("depth") or 0),
        report=str((result or {}).get("report") or ""),
        citations=list(result.get("citations") or []) if result is not None else None,
        visited_sources=list(result.get("visited_sources") or []) if result is not None else None,
        egress=payload.get("egress"),
    )


def _elapsed_seconds_from_row(row: Dict[str, Any]) -> Any:
    created = row.get("created_at")
    finished = row.get("finished_at")
    if created is None or finished is None:
        return 0
    try:
        return round(max(0.0, (finished - created).total_seconds()), 1)
    except TypeError:  # 非常规时间值：如实按不可知处理
        return 0


def document_from_store(row: Dict[str, Any], *, export_json: Optional[str] = None,
                        report_md: Optional[str] = None) -> ExportDocument:
    """任务库回落装配（需求 38 F1）：与内存路径**同一模型与渲染口径**。

    优先 `export_json` 全量载荷（P1-6 起随终局同事务写入）；缺失（历史 run）⇒ 降级为
    「正文 + row 可提供元数据」，引用/来源/降级统计**显式置 `None`**（渲染为不可用，
    不伪造空清单）。
    """
    payload: Optional[Dict[str, Any]] = None
    if export_json:
        try:
            parsed = json.loads(export_json)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, dict):
            payload = parsed
    if payload is not None:
        doc = document_from_payload(payload)
        if not doc.topic:
            doc = replace(doc, topic=str(row.get("topic") or ""))
        if not doc.report.strip() and report_md:
            doc = replace(doc, report=report_md)
        return doc
    stop_reason = row.get("stop_reason")
    return ExportDocument(
        run_id=str(row.get("run_id") or ""),
        topic=str(row.get("topic") or ""),
        generated_at=_now_iso(),
        run_status=row.get("research_status"),
        stop_reason=stop_reason,
        cancelled=stop_reason in ("cancelled", "user_cancelled"),
        token_used=int(row.get("token_used") or 0),
        cost_estimate_cny=float(row.get("cost_estimate_cny") or 0.0),
        elapsed_seconds=_elapsed_seconds_from_row(row),
        degradation_count=None,
        depth=None,
        report=str(report_md or ""),
        citations=None,
        visited_sources=None,
        egress=(row.get("request") or {}).get("egress"),
    )


def _citation_lines(citations: List[Dict[str, Any]]) -> List[str]:
    lines: List[str] = []
    for i, c in enumerate(citations, start=1):
        verified = "存在性校验通过" if c.get("existence") else "存在性校验未通过"
        supported = "论断受支持" if c.get("supported") else "论断未获支持"
        confidence = c.get("confidence")
        conf = f"{confidence:.2f}" if isinstance(confidence, (int, float)) else "n/a"
        claim = str(c.get("claim", "")).strip() or "（无论断）"
        note = str(c.get("note", "")).strip()
        note = f"　备注：{note}" if note else ""
        lines.append(
            f"{i}. {claim} — <{c.get('source', '')}>"
            f"（{c.get('source_type', 'unknown')} · 置信度 {conf} · {verified} · {supported}）{note}"
        )
    return lines


def _meta_value(value: Any) -> Any:
    """不可知字段（None）显式渲染为 n/a（不伪造 0）。"""
    return "n/a" if value is None else value


def render_markdown(doc: ExportDocument) -> str:
    """把导出装配模型渲染成 Markdown（报告正文**原文照录**，不做任何改写）。

    与既有导出口径一致：元数据头 + 正文 + 引用清单 + 访问来源；
    历史 run 缺失字段（`None`）显式标注「不可用」。
    """
    report = doc.report.strip()
    head = [
        f"# {doc.topic}：爱考据 iKaoju 报告导出",
        "",
        f"> run_id `{doc.run_id}` · 导出时间 {doc.generated_at}",
        f"> run_status **{doc.run_status}** · 停止原因 **{doc.stop_reason}**"
        f"{' · 已取消' if doc.cancelled else ''}",
        f"> token {doc.token_used} · 成本估算 ¥{doc.cost_estimate_cny}"
        f" · 耗时 {doc.elapsed_seconds}s"
        f" · 降级条目 {_meta_value(doc.degradation_count)}"
        f" · 检索深度 {_meta_value(doc.depth)}",
        "",
        "---",
        "",
    ]

    body = [report, ""] if report else ["（本次运行未产出报告正文）", ""]

    parts = head + body
    if doc.citations is None:
        parts += ["## 引用清单", "", "（不可用：历史产物未保留引用数据）", ""]
    elif doc.citations:
        parts += ["## 引用清单", ""] + _citation_lines(doc.citations) + [""]
    if doc.visited_sources is None:
        parts += ["## 访问过的来源", "", "（不可用：历史产物未保留来源数据）", ""]
    elif doc.visited_sources:
        parts += ["## 访问过的来源", ""] + [f"- <{s}>" for s in doc.visited_sources] + [""]

    return "\n".join(parts).rstrip() + "\n"
