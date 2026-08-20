# -*- coding: utf-8 -*-
"""三层 LLM 分级路由（借鉴 gpt-researcher 的 FAST/SMART/STRATEGIC）。

- fast:      快速摘要、信息提取（qwen-turbo，最便宜）
- smart:     分析、写作、检索词生成（qwen-plus）
- strategic: 高层规划、裁决、充分度判断（qwen-plus，可升级 qwen-max）

初始阶段全部用便宜模型，后续可单独升级 strategic 提升规划质量。
"""
from __future__ import annotations

from typing import Dict, List, Optional

from config import config
from research_engine.llm.client import LLMClient, build_messages


class LLMRouter:
    """按任务复杂度路由到不同层级的 LLM。"""

    def __init__(self):
        self._fast = LLMClient(model=config.llm.fast_model)
        self._smart = LLMClient(model=config.llm.smart_model)
        self._strategic = LLMClient(model=config.llm.strategic_model)

    # ---- fast 层：摘要、提取 ----
    def fast_chat(self, system: str, user: str) -> str:
        return self._fast.chat(build_messages(system, user))

    # ---- smart 层：分析、写作 ----
    def smart_chat(self, system: str, user: str) -> str:
        return self._smart.chat(build_messages(system, user))

    def smart_json(self, system: str, user: str) -> Dict:
        return self._smart.chat_json(build_messages(system, user))

    # ---- strategic 层：规划、裁决 ----
    def strategic_chat(self, system: str, user: str) -> str:
        return self._strategic.chat(build_messages(system, user))

    def strategic_json(self, system: str, user: str) -> Dict:
        return self._strategic.chat_json(build_messages(system, user))


_router: Optional[LLMRouter] = None


def get_router() -> LLMRouter:
    """单例获取路由。"""
    global _router
    if _router is None:
        _router = LLMRouter()
    return _router
