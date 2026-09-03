# -*- coding: utf-8 -*-
"""Writer Agent：基于研究发现生成带引用的研究报告。

使用 smart 层 LLM 写作，输出 Markdown 报告，每个论断标注引用来源编号。
"""
from __future__ import annotations

from typing import Any, List

from research_engine.context.manager import ContextManager
from research_engine.llm.router import get_router
from research_engine.state import ResearchFinding

WRITER_SYSTEM = """你是一位专业的研究报告撰写者。基于给定的研究发现，撰写一份结构清晰、内容详实的研究报告。

要求：
1. 使用 Markdown 格式，包含标题、小节
2. 每个关键论断后必须标注引用来源，格式为 [来源: N]，其中 N 是研究发现列表中的编号（如 [来源: 3]）；多编号用 [来源: 5, 72, 77] 形式
3. 只能引用研究发现列表中真实存在的编号，禁止编造编号
4. 忠实于研究发现，不编造事实
5. 对信息不足的部分明确标注"信息不足"
6. 报告应覆盖所有子问题
7. 标注为"自指/方法论"的研究发现（描述 DeepResearch/本系统/本 Agent 自身架构或机制）只能用于独立的"方法论说明"小节，不得作为事实证据在正文中引用
8. 不要在报告末尾写"全部结论严格基于...""未引入外部信息"这类绝对可信声明——引用统计信息由系统在报告末尾自动标注
9. "方法论说明"小节使用陈述句客观描述系统机制（如"本系统包含 Planner→Researcher→Writer→Validator 四个节点"），不使用"严格遵循""唯一正确""确保最先进"等绝对化或自我标榜式表述，并与正文事实部分明确分隔"""


class Writer:
    """报告撰写器。

    注意：Writer 不再自行压缩研究发现。压缩由 graph 的 write 节点完成并写回
    state，确保 Writer（生成引用编号）与 Validator（还原编号）消费同一份列表。
    见 ADR-0004。
    """

    def __init__(self):
        self.router = get_router()
        self.context = ContextManager()

    def write(self, topic: str, subquestions: List, findings: List[ResearchFinding], state: Any = None) -> str:
        context_text = self.context.format_for_writer(findings)

        subq_text = "\n".join(f"- {s.question}" for s in subquestions)
        user = f"研究主题：{topic}\n\n子问题：\n{subq_text}\n\n研究发现：\n{context_text}\n\n请撰写研究报告。"

        try:
            return self.router.smart_chat(WRITER_SYSTEM, user, state=state)
        except Exception:  # noqa: BLE001
            return f"# {topic}\n\n（报告生成失败，请检查 LLM 配置）"
