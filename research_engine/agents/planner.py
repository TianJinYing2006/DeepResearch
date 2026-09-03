# -*- coding: utf-8 -*-
"""Planner Agent：将研究主题分解为子问题。

使用 strategic 层 LLM 做高层规划，输出结构化子问题列表。
"""
from __future__ import annotations

from typing import List

from config import config
from research_engine.llm.router import get_router
from research_engine.state import SubQuestion

PLANNER_SYSTEM = """你是一位资深研究规划专家。你的任务是将用户的研究主题分解为若干相互独立、可执行的子问题。

要求：
1. 每个子问题应聚焦一个可独立检索的方面
2. 子问题之间尽量不重叠
3. 数量控制在 {max_subquestions} 个以内
4. 为每个子问题说明研究它的理由

请以 JSON 格式输出，结构如下：
{{
  "subquestions": [
    {{"id": "q1", "question": "子问题内容", "rationale": "研究理由"}}
  ]
}}
"""


REPLAN_SYSTEM = """你是一位资深研究规划专家。之前的子问题分解在研究中被 critic 判定为方向跑偏。
请基于【研究主题】【现有子问题】【已收集发现】【重规划原因】重新分解出更优的子问题集合。

要求：
1. 数量控制在 {max_subquestions} 个以内
2. 修正之前方向跑偏的问题，保留仍有价值的角度
3. 每个子问题聚焦一个可独立检索的方面

请以 JSON 格式输出：
{{
  "subquestions": [
    {{"id": "q1", "question": "子问题内容", "rationale": "研究理由"}}
  ]
}}
"""


class Planner:
    """研究规划器。"""

    def plan(self, topic: str, user_instructions: str = "", state: Any = None) -> List[SubQuestion]:
        router = get_router()
        system = PLANNER_SYSTEM.format(max_subquestions=config.research.max_subquestions)
        user = f"研究主题：{topic}\n"
        if user_instructions:
            user += f"用户附加要求：{user_instructions}\n"
        user += "请分解为子问题。"

        try:
            data = router.strategic_json(system, user, state=state)
            subs = []
            for item in data.get("subquestions", []):
                subs.append(
                    SubQuestion(
                        id=item.get("id", f"q{len(subs)+1}"),
                        question=item.get("question", ""),
                        rationale=item.get("rationale", ""),
                    )
                )
            return subs
        except Exception:  # noqa: BLE001
            # 降级：把主题本身作为唯一子问题
            return [SubQuestion(id="q1", question=topic, rationale="主题本身作为研究问题")]

    def replan(
        self,
        topic: str,
        subs: List[SubQuestion],
        findings: List[Any],
        reason: str,
        state: Any = None,
    ) -> List[SubQuestion]:
        """方向跑偏时的全量重分解（Q2-B 兜底，受 max_replan 限次）。"""
        router = get_router()
        system = REPLAN_SYSTEM.format(max_subquestions=config.research.max_subquestions)
        subs_text = "\n".join(f"- {s.id}: {s.question}" for s in subs) or "（无）"
        find_text = "\n".join(f"- {getattr(f, 'content', '')[:150]}" for f in findings[:12]) or "（无）"
        user = (
            f"研究主题：{topic}\n\n"
            f"现有子问题：\n{subs_text}\n\n"
            f"已收集发现：\n{find_text}\n\n"
            f"重规划原因：{reason}\n\n请重新分解。"
        )
        try:
            data = router.strategic_json(system, user, state=state)
            new_subs = []
            for item in data.get("subquestions", []):
                new_subs.append(
                    SubQuestion(
                        id=item.get("id", f"q{len(new_subs)+1}"),
                        question=item.get("question", ""),
                        rationale=item.get("rationale", ""),
                    )
                )
            return new_subs or subs
        except Exception:  # noqa: BLE001
            # 降级：沿用旧子问题，不空转
            return subs
