"""Writer Agent：基于研究发现生成带引用的研究报告。

使用 smart 层 LLM 写作，输出 Markdown 报告，每个论断标注引用来源编号。
W7 Arm4：按子问题分节喂料 + 信息不足标注纪律 + 系统级兜底。
"""
from __future__ import annotations

import re
from typing import Any, List

from config import config
from research_engine.context.manager import ContextManager
from research_engine.llm.router import get_router
from research_engine.state import ResearchFinding, SubQuestion

# W7 Arm4：分节喂料关闭时（TBD-8 基线对照）回退到 v1.1 提示
WRITER_SYSTEM_LEGACY = """你是一位专业的研究报告撰写者。基于给定的研究发现，撰写一份结构清晰、内容详实的研究报告。

要求：
1. 使用 Markdown 格式，包含标题、小节
2. 每个关键论断后必须标注引用来源，格式为 [来源: N]，其中 N 是研究发现编号（如 [来源: 3]）；多编号用 [来源: 5, 72, 77] 形式
3. 只能引用研究发现中真实存在的编号，禁止编造编号
4. 忠实于研究发现，不编造事实
5. 对信息不足的部分明确标注"信息不足"
6. 报告应覆盖所有子问题
7. 标注为"自指/方法论"的研究发现（描述 DeepResearch/本系统/本 Agent 自身架构或机制）只能用于独立的"方法论说明"小节，不得作为事实证据在正文中引用
8. 不要在报告末尾写"全部结论严格基于...""未引入外部信息"这类绝对可信声明——引用统计信息由系统在报告末尾自动标注
9. "方法论说明"小节使用陈述句客观描述系统机制（如"本系统包含 Planner→Researcher→Writer→Validator 四个节点"），不使用"严格遵循""唯一正确""确保最先进"等绝对化或自我标榜式表述，并与正文事实部分明确分隔"""

WRITER_SYSTEM = """你是一位专业的研究报告撰写者。基于给定的研究发现，撰写一份结构清晰、内容详实的研究报告。

核心约束（必须遵守）：
1. 只能使用"研究发现"中列出的材料，禁止使用外部知识或预训练知识补充内容。
2. 必须按"子问题清单"分节撰写；每个子问题一节。若该节无材料或材料不足，请在该节正文明确写"信息不足：本小节目前缺乏足够研究发现支撑，无法得出可靠结论。"，不要为求完整而编造。
3. 每个关键论断后必须标注引用来源，格式为 [来源: N]，其中 N 是研究发现编号；多编号用 [来源: 5, 72, 77] 形式。
4. 只能引用发现中真实存在的编号，禁止编造编号；引用编号必须在可用范围内。
5. 忠实于研究发现，不夸大、不推断、不编造事实。
6. 报告应覆盖所有子问题；无材料小节仍保留二级标题并写"信息不足"。
7. 标注为"自指/方法论"的研究发现（描述 DeepResearch/本系统/本 Agent 自身架构或机制）只能用于独立的"方法论说明"小节，不得作为事实证据在正文中引用。
8. 不要在报告末尾写"全部结论严格基于...""未引入外部信息"这类绝对可信声明——引用统计信息由系统在报告末尾自动标注。
9. "方法论说明"小节使用陈述句客观描述系统机制（如"本系统包含 Planner→Researcher→Writer→Validator 四个节点"），不使用"严格遵循""唯一正确""确保最先进"等绝对化或自我标榜式表述，并与正文事实部分明确分隔。

输出格式：
- 使用 Markdown，一级标题为研究主题。
- 每个子问题使用二级标题。
- 无材料小节仍保留二级标题并写"信息不足"。"""


class Writer:
    """报告撰写器。

    注意：Writer 不再自行压缩研究发现。压缩由 graph 的 write 节点完成并写回
    state，确保 Writer（生成引用编号）与 Validator（还原编号）消费同一份列表。
    见 ADR-0004。
    """

    def __init__(self):
        self.router = get_router()
        self.context = ContextManager()

    def write(
        self,
        topic: str,
        subquestions: List[SubQuestion],
        findings: List[ResearchFinding],
        state: Any = None,
    ) -> str:
        # W7 Arm4：分节喂料开关
        sectioned = config.experiment.writer_sectioned_feed_enabled
        context_text = self.context.format_for_writer(findings, subquestions)

        subq_text = "\n".join(f"- {s.question}" for s in subquestions)
        if sectioned:
            system = WRITER_SYSTEM
            user = (
                f"研究主题：{topic}\n\n"
                f"子问题清单：\n{subq_text}\n\n"
                f"研究发现（按子问题分节，每条以 Finding N: 开头，N 为编号）：\n{context_text}\n\n"
                "请严格按子问题分节撰写研究报告。"
            )
        else:
            system = WRITER_SYSTEM_LEGACY
            user = (
                f"研究主题：{topic}\n\n"
                f"子问题：\n{subq_text}\n\n"
                f"研究发现：\n{context_text}\n\n"
                "请撰写研究报告。"
            )

        try:
            report = self.router.smart_chat(system, user, state=state)
        except Exception:  # noqa: BLE001
            report = self._fallback_report(topic, subquestions)

        if sectioned:
            return self._ensure_sections(report, subquestions, findings)
        return report

    def _fallback_report(self, topic: str, subquestions: List[SubQuestion]) -> str:
        """LLM 调用失败时的系统级兜底：每个子问题都写信息不足。"""
        lines = [f"# {topic}", "", "（报告生成遇到异常，以下为系统兜底输出）"]
        for s in subquestions:
            lines.extend([f"\n## {s.question}", "信息不足：本小节目前缺乏足够研究发现支撑，无法得出可靠结论。"])
        return "\n".join(lines)

    def _ensure_sections(
        self,
        report: str,
        subquestions: List[SubQuestion],
        findings: List[ResearchFinding],
    ) -> str:
        """G4 系统级兜底：1）替换越界引用；2）为缺失的子问题补信息不足小节。"""
        max_n = len(findings)

        def _extract_nums(inner: str) -> List[str]:
            """从引用内部提取数字编号，兼容 # 前缀和多种分隔符。"""
            raw_tokens = [t for t in re.split(r"[,，、;；/和\s]+", inner.strip()) if t]
            tokens = [t.lstrip("#").strip() for t in raw_tokens]
            return [t for t in tokens if t.isdigit()]

        def _replace_citation(match: re.Match) -> str:
            inner = match.group(1)
            nums = _extract_nums(inner)
            if not nums:
                return match.group(0)
            # Bug-3 修复：只裁掉越界编号，保留有效编号，不整条替换
            valid_nums = [n for n in nums if 1 <= int(n) <= max_n]
            if not valid_nums:
                return "[来源: 信息不足]"
            if len(valid_nums) < len(nums):
                return f"[来源: {', '.join(valid_nums)}]"
            return match.group(0)

        # P0 引用协议统一：匹配 [来源: N] 和 [来源: #N] 格式
        report = re.sub(r"\[来源:\s*([#\d\s,，、;；/和]+)\]", _replace_citation, report)

        # P0 引用协议统一：裸 [#N] / [N] 归一化为 [来源: N]
        # 排除 markdown 链接 [...](...) 和已处理的 [来源: ...]
        def _normalize_bare(match: re.Match) -> str:
            inner = match.group(1)
            nums = _extract_nums(inner)
            if not nums:
                return match.group(0)
            valid_nums = [n for n in nums if 1 <= int(n) <= max_n]
            if not valid_nums:
                # 设计-4 修复：与 _replace_citation 一致，越界返回信息不足
                return "[来源: 信息不足]"
            return f"[来源: {', '.join(valid_nums)}]"

        # P0/Bug-8 引用协议统一：裸 [#N] / [N] 归一化为 [来源: N]
        # Bug-8 修复：要求前面是句末标点或空白，避免匹配"实验[1]"等行文括号
        report = re.sub(
            r"(?:(?<=[。！？；\n\s])|(?<=^))\[(#?\d[#\d\s,，、;；/和]*)\](?!\()",
            _normalize_bare,
            report,
        )

        missing: List[str] = []
        for s in subquestions:
            # 设计-6 修复：用标题行精确匹配替代朴素子串匹配，避免短问题文本误判
            heading_pattern = re.compile(r"^##+\s*" + re.escape(s.question), re.M)
            if not heading_pattern.search(report):
                missing.append(
                    f"## {s.question}\n\n"
                    "信息不足：本小节目前缺乏足够研究发现支撑，无法得出可靠结论。"
                )
        if missing:
            report += "\n\n---\n\n" + "\n\n".join(missing)
        return report
