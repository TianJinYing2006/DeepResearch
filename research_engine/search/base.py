"""搜索 Provider 抽象基类。

所有网络搜索实现继承 SearchProvider，通过工厂函数按配置选择。
借鉴 wechatbot 的 MCP Provider 切换思路，支持可插拔。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class SearchResult:
    """单条搜索结果。W4：metadata 桶承载工具特有元数据（如 arxiv 的 arxiv_id/primary_category），
    由 researcher 转 ResearchFinding.metadata（统一证据抽象，Q3）。"""
    title: str
    url: str
    snippet: str
    source: str = "web"
    metadata: dict = field(default_factory=dict)


@dataclass
class SearchResponse:
    """搜索响应。

    W8 Arm 1 前置（Q8 拍板）：新增 `failure_reason` —— **工具层 5 值的唯一产生点**。

    与 Arm 1 的 `degradation_log` 是**两个消费方，不是两份拷贝**（Q8 B4）：
    本字段面向**下游节点**（planner 当轮决策），**同步、一次调用即覆盖**；
    `degradation_log` 面向**事后审计**，**异步、run 级追加**。同构于 OTel 的
    `Span.Status`（单值后写覆盖）与 `add_event()`（追加流）。

    **单向派生契约**：`DegradationEntry.reason` 必须取本字段的值，
    **禁止在消费处手写第二个字面量** —— 否则两处对同一事件的描述会静默分叉。
    """
    query: str
    results: List[SearchResult] = field(default_factory=list)
    #: 失败原因枚举（仅 :data:`research_engine.failure_reasons.TOOL_REASONS` 5 值）；
    #: 成功时为 `None`。由本模块（工具层）产生，其他层不得凭空构造。
    failure_reason: Optional[str] = None
    #: 自由文本补充（原始异常摘要等），可为空；仅用于排障，不参与枚举统计。
    failure_detail: str = ""

    @property
    def ok(self) -> bool:
        """是否成功（无失败原因）。"""
        return self.failure_reason is None


class SearchProvider(ABC):
    """搜索 Provider 抽象。"""

    @abstractmethod
    def search(self, query: str, max_results: int = 8) -> SearchResponse:
        """执行搜索，返回结果列表。"""
        raise NotImplementedError


def create_search_provider(name: str = "bocha") -> SearchProvider:
    """按名称创建搜索 Provider（``config.search.provider`` 的唯一装配入口）。

    支持 ``bocha``（博查，默认）与 ``tavily``（1000 次/月免费，专为 Agent 设计）。
    未知名称**直接抛错而非静默回退到默认源** —— 配错了搜索源却不吭声，比启动失败
    危险得多（会用错的源跑完整场研究，产物看起来正常）。
    """
    if name == "bocha":
        from research_engine.search.bocha import BochaSearchProvider
        return BochaSearchProvider()
    if name == "tavily":
        from research_engine.search.tavily import TavilySearchProvider
        return TavilySearchProvider()
    raise ValueError(f"未知搜索 Provider: {name}（可用：bocha / tavily）")
