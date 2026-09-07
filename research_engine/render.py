"""报告渲染器（W2 R2.1/R2.2/R2.5 + R2.4 兜底）。

grill 落点：
- Q1=A：正文保留 + ⚠️ 标记 + 末尾附录；本模块是 render 节点的 post-process，不回流 state.report
- Q4=A：类型标注由渲染层完成，Writer 维持纯编号 [来源: N] —— 本模块复用 Validator._split_ref
         （ADR-0005 同源）做拆分，绝不把类型标进校验输入
- Q6=A：可信声明按双口径（存在性 / 忠实度）分别统计
- Q5=A：is_meta 复核结果（citation.is_meta）在此兜底标注"🔄自指"
- R2.5：末尾"运行溯源"块，数值全部取自 state

降级契约：render() 任何异常返回原 report，不阻断主流程。
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any, List, Optional

from research_engine.agents.validator import Validator
from research_engine.state import Citation, ResearchFinding

_PATTERN = re.compile(r"\[来源:\s*([^\]]+)\]")


class ReportRenderer:
    """把 Writer 原始报告渲染成可审计展示版 report_display。"""

    # ---- R2.1 + Q4：类型标注（复用 ADR-0005 拆分，Writer 协议不变）----

    def annotate_types(self, report: str, findings: List[ResearchFinding],
                       citations: Optional[List[Citation]] = None) -> str:
        """把 [来源: N] 替换为 [来源: N · 🔵web] / [来源: N · 🟢rag：<源>]。

        - 多编号 [来源: 5, 72, 77] 每条分别标注（ADR-0005 同源拆分）
        - URL 协议 [来源: <URL>] 按 source 匹配 finding 标注类型
        - 失败引用（verified=False）在编号后加 ⚠️（R2.2 正文警示）
        - is_meta 引用（finding 层或 citation 复核层命中）加 🔄自指（R2.4 兜底标注）
        """
        index = {str(i): f for i, f in enumerate(findings, 1)}
        source_to_f = {f.source: f for f in findings}

        failed_ids: set = set()
        meta_ids: set = set()
        if citations:
            failed_ids = {c.finding_id for c in citations if not c.verified and c.finding_id}
            meta_ids = {c.finding_id for c in citations if c.is_meta and c.finding_id}

        def repl(m: re.Match) -> str:
            ref = m.group(1).strip()
            refs = Validator._split_ref(ref)  # ADR-0005 同源拆分（Q4 防线同源）
            parts: List[str] = []
            for r in refs:
                f = index.get(r) or source_to_f.get(r)
                if f is None:
                    parts.append(r)  # 越界编号/未知来源：原样保留
                    continue
                if f.source_type == "web":
                    label = "🔵web"
                elif f.source_type == "rag":
                    label = f"🟢rag：{f.source}"
                elif f.source_type == "arxiv":
                    label = "🔬arxiv"  # W4 Q6：新来源类型图标
                elif f.source_type == "code_exec":
                    label = "💻code"   # W4 Q6
                else:
                    label = f.source_type
                marks = []
                if r in failed_ids:
                    marks.append("⚠️")
                if f.is_meta or r in meta_ids:
                    marks.append("🔄自指")
                suffix = (" · " + " · ".join(marks)) if marks else ""
                parts.append(f"{r} · {label}{suffix}")
            return f"[来源: {', '.join(parts)}]"

        return _PATTERN.sub(repl, report)

    # ---- R2.2 + Q6：动态可信声明（双口径）----

    def build_trust_statement(self, citations: List[Citation]) -> str:
        """报告末尾可信声明：存在性 / 忠实度双口径（Q6=A），随失败数动态生成。

        - 存在性口径：existence=True 数 / 总数
        - 忠实度口径：verified=True 数 / 存在性通过数（忠实度只在存在性子集上判定）
        - 发生 LLM 降级时显式注明，避免口径失真
        """
        total = len(citations)
        if total == 0:
            return "\n\n> **可信声明**：报告中未检测到引用标注，无法校验。"
        existence_ok = sum(1 for c in citations if c.existence)
        faithful_ok = sum(1 for c in citations if c.verified)
        degraded = any("降级为存在性判定" in c.note for c in citations)
        lines = [
            "\n\n> **可信声明**（动态生成，非 LLM 自述）",
            f"> - 来源存在性：{existence_ok}/{total} 条通过",
            f"> - 论断忠实度：{faithful_ok}/{existence_ok} 条通过（仅在存在性通过的子集上判定）",
            "> - 未通过者见下方附录",
        ]
        if degraded:
            lines.append("> - ⚠️ 本次校验发生 LLM 降级，忠实度口径按存在性通过计，参考性有限")
        return "\n".join(lines)

    # ---- R2.2：失败论断附录 ----

    def build_failed_appendix(self, citations: List[Citation]) -> str:
        """把 verified=False 的论断抽进报告末尾附录，附 claim + 原因 + finding_id（Q1=A）。"""
        failed = [c for c in citations if not c.verified]
        if not failed:
            return ""
        lines = ["\n\n## ⚠️ 未通过引用校验的论断", ""]
        for c in failed:
            lines.append(f"- **{c.claim[:150]}**")
            meta = [
                f"来源: {c.source}",
                f"编号: {c.finding_id}" if c.finding_id else "",
                c.source_type or "",
            ]
            meta = [x for x in meta if x]
            lines.append(f"  - {' · '.join(meta)}")
            lines.append(f"  - 原因: {c.note or '未说明'}")
        return "\n".join(lines)

    # ---- R2.5：运行溯源 block ----

    def build_run_provenance(self, state: Any) -> str:
        """运行溯源块：总跳数 / critic 决策计数 / 工具命中四桶 / token / replan（取自 state）。

        W4 Q6：两桶（rag/其余=web）会把 arxiv(abs URL)/code(code:hash) 误算进 web——
        改四桶分前缀统计。
        """
        signals = Counter(entry.get("signal", "") for entry in getattr(state, "reflection_log", []) or [])
        vs = list(getattr(state, "visited_sources", []) or [])
        rag_count = sum(1 for s in vs if str(s).startswith("rag:"))
        arxiv_count = sum(1 for s in vs if str(s).startswith("https://arxiv.org/"))
        code_count = sum(1 for s in vs if str(s).startswith("code:"))
        web_count = len(vs) - rag_count - arxiv_count - code_count  # 无前缀即 web
        return (
            "\n\n---\n## 📊 运行溯源\n"
            f"- **检索总跳数**：{getattr(state, 'depth', 0)}\n"
            f"- **Critic 决策**：continue {signals.get('continue', 0)} / revise {signals.get('revise', 0)} / stop {signals.get('stop', 0)}\n"
            f"- **命中来源**（visited_sources 去重后）：web {web_count} / rag {rag_count} / arxiv {arxiv_count} / code {code_count}\n"
            f"- **Token 消耗**：{getattr(state, 'token_used', 0)}\n"
            f"- **Replan 次数**：{getattr(state, 'replan_count', 0)}"
        )

    # ---- 主入口 ----

    def render(self, report: str, citations: List[Citation],
               findings: List[ResearchFinding], state: Any) -> str:
        """渲染 report_display：正文标注 → 可信声明 → 失败附录 → 运行溯源。

        任何异常直接返回原 report（降级，不阻断主流程）。
        """
        try:
            display = self.annotate_types(report, findings, citations)
            display += self.build_trust_statement(citations)
            display += self.build_failed_appendix(citations)
            display += self.build_run_provenance(state)
            return display
        except Exception:  # noqa: BLE001
            return report