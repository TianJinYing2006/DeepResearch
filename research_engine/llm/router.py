"""三层 LLM 分级路由（借鉴 gpt-researcher 的 FAST/SMART/STRATEGIC）。

- fast:      快速摘要、信息提取（qwen-turbo，最便宜）
- smart:     分析、写作、检索词生成（qwen-plus）
- strategic: 高层规划、裁决、充分度判断（qwen-plus，可升级 qwen-max）

初始阶段全部用便宜模型，后续可单独升级 strategic 提升规划质量。
"""
from __future__ import annotations

import threading
from typing import Any, Dict, Optional

from config import config
from research_engine.llm.client import LLMClient, build_messages
from research_engine.runtime_profile import effective_llm_model


class LLMRouter:
    """按任务复杂度路由到不同层级的 LLM。"""

    def __init__(self, models: Optional[tuple[str, str, str]] = None):
        # W5（Q2）：role 标签 = 职责桶 key——compress / smart（writer+researcher 合桶）/ planner
        # P0 profile 固化：模型组合由调用方（get_router）按本场 run 生效值给出
        fast, smart, strategic = models or (
            config.llm.fast_model, config.llm.smart_model, config.llm.strategic_model)
        self._fast = LLMClient(model=fast, role="compress")
        self._smart = LLMClient(model=smart, role="smart")
        self._strategic = LLMClient(model=strategic, role="planner")

    # ---- fast 层：摘要、提取 ----
    def fast_chat(self, system: str, user: str, state: Optional[Any] = None) -> str:
        return self._fast.chat(build_messages(system, user), state=state)

    # ---- smart 层：分析、写作 ----
    def smart_chat(self, system: str, user: str, state: Optional[Any] = None) -> str:
        return self._smart.chat(build_messages(system, user), state=state)

    def smart_json(self, system: str, user: str, state: Optional[Any] = None) -> Dict:
        return self._smart.chat_json(build_messages(system, user), state=state)

    # ---- strategic 层：规划、裁决 ----
    def strategic_chat(self, system: str, user: str, state: Optional[Any] = None) -> str:
        return self._strategic.chat(build_messages(system, user), state=state)

    def strategic_json(self, system: str, user: str, state: Optional[Any] = None) -> Dict:
        return self._strategic.chat_json(build_messages(system, user), state=state)


_routers: Dict[tuple, LLMRouter] = {}
_router_lock = threading.Lock()


def get_router() -> LLMRouter:
    """按**本场 run 生效的模型组合**取路由（P0 profile 固化）。

    无档位（CLI / eval）时键等于全局配置，行为与历史一致；
    有档位时不同档位各自缓存 —— 避免共享单例把 A 的模型漏给 B。
    """
    key = (effective_llm_model("fast"), effective_llm_model("smart"),
           effective_llm_model("strategic"))
    with _router_lock:
        router = _routers.get(key)
        if router is None:
            router = LLMRouter(models=key)
            _routers[key] = router
        return router
