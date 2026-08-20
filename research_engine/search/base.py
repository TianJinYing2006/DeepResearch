# -*- coding: utf-8 -*-
"""搜索 Provider 抽象基类。

所有网络搜索实现继承 SearchProvider，通过工厂函数按配置选择。
借鉴 wechatbot 的 MCP Provider 切换思路，支持可插拔。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import List


@dataclass
class SearchResult:
    """单条搜索结果。"""
    title: str
    url: str
    snippet: str
    source: str = "web"


@dataclass
class SearchResponse:
    """搜索响应。"""
    query: str
    results: List[SearchResult] = field(default_factory=list)


class SearchProvider(ABC):
    """搜索 Provider 抽象。"""

    @abstractmethod
    def search(self, query: str, max_results: int = 8) -> SearchResponse:
        """执行搜索，返回结果列表。"""
        raise NotImplementedError


def create_search_provider(name: str = "bocha") -> SearchProvider:
    """按名称创建搜索 Provider。"""
    if name == "bocha":
        from research_engine.search.bocha import BochaSearchProvider
        return BochaSearchProvider()
    raise ValueError(f"未知搜索 Provider: {name}")
