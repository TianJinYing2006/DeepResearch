# -*- coding: utf-8 -*-
"""评测 C：报告质量评测（LLM-as-judge）。

借鉴 LangChain open_deep_research 的 RACE 评分思路，
从全面性、准确性、相关性、清晰度四个维度打分。
"""
from __future__ import annotations

from research_engine.llm.router import get_router

REPORT_EVAL_SYSTEM = """你是研究报告质量评审专家。请从以下四个维度对报告打分（0-100）：
1. comprehensiveness（全面性）：是否覆盖了研究主题的各个方面
2. accuracy（准确性）：事实是否准确，是否有明显错误
3. relevance（相关性）：内容是否紧扣研究主题
4. clarity（清晰度）：结构是否清晰，表达是否易懂

请以 JSON 格式输出：
{{
  "comprehensiveness": 0-100,
  "accuracy": 0-100,
  "relevance": 0-100,
  "clarity": 0-100,
  "overall": 0-100,
  "comment": "总体评价"
}}
"""


class ReportEvaluator:
    """报告质量评测器（LLM-as-judge）。"""

    def __init__(self):
        self.router = get_router()

    def evaluate(self, topic: str, report: str) -> dict:
        user = f"研究主题：{topic}\n\n研究报告：\n{report}\n\n请评分。"
        try:
            data = self.router.strategic_json(REPORT_EVAL_SYSTEM, user)
            return {
                "comprehensiveness": data.get("comprehensiveness", 0),
                "accuracy": data.get("accuracy", 0),
                "relevance": data.get("relevance", 0),
                "clarity": data.get("clarity", 0),
                "overall": data.get("overall", 0),
                "comment": data.get("comment", ""),
            }
        except Exception:  # noqa: BLE001
            return {"overall": 0, "comment": "评测失败"}
