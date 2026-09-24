"""报告导出（P1-6）：把一次运行的结果拼成可下载的文件。

**为什么放在后端**：前端原本就有「下载 .md」按钮，但那份 md 是**纯报告正文**
（`result.report`），不含 run_id / run_status / 降级条数等审计元数据，也不含引用清单
⇒ 导出的文件脱离页面后无法自证来源。后端导出把元数据 + 正文 + 引用清单一次拼齐。

🚨 边界：导出的是**内存里的这次运行**（D-19 不做持久化），服务重启即不可导出。
本模块不落盘、不写数据库、不做用户目录管理。
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List


def build_export_payload(
    *,
    run_id: str,
    topic: str,
    meta: Dict[str, Any],
    result: Dict[str, Any],
) -> Dict[str, Any]:
    """组装导出载荷：元数据 + 结果，**结构即导出契约**。"""
    return {
        "run_id": run_id,
        "topic": topic,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "run_status": meta.get("run_status"),
        "stop_reason": meta.get("stop_reason"),
        "cancelled": bool(meta.get("cancelled")),
        "token_used": meta.get("token_used", 0),
        "cost_estimate_cny": meta.get("cost_estimate_cny", 0.0),
        "degradation_count": meta.get("degradation_count", 0),
        "depth": meta.get("depth", result.get("depth", 0)),
        "result": result,
    }


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


def render_markdown(payload: Dict[str, Any]) -> str:
    """把导出载荷渲染成 Markdown（报告正文**原文照录**，不做任何改写）。"""
    result = payload.get("result") or {}
    report = str(result.get("report") or "").strip()
    citations = list(result.get("citations") or [])
    visited = list(result.get("visited_sources") or [])

    head = [
        f"# {payload.get('topic', '')}：DeepResearch 报告导出",
        "",
        f"> run_id `{payload.get('run_id')}` · 导出时间 {payload.get('generated_at')}",
        f"> run_status **{payload.get('run_status')}** · 停止原因 **{payload.get('stop_reason')}**"
        f"{' · 已取消' if payload.get('cancelled') else ''}",
        f"> token {payload.get('token_used')} · 成本估算 ¥{payload.get('cost_estimate_cny')}"
        f" · 降级条目 {payload.get('degradation_count')} · 检索深度 {payload.get('depth')}",
        "",
        "---",
        "",
    ]

    body = [report, ""] if report else ["（本次运行未产出报告正文）", ""]

    parts = head + body
    if citations:
        parts += ["## 引用清单", ""] + _citation_lines(citations) + [""]
    if visited:
        parts += ["## 访问过的来源", ""] + [f"- <{s}>" for s in visited] + [""]

    return "\n".join(parts).rstrip() + "\n"
