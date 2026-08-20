# -*- coding: utf-8 -*-
"""博查搜索 Provider 实现。

调用博查 Web Search API，返回结构化搜索结果。
"""
from __future__ import annotations

import requests

from config import config
from research_engine.search.base import SearchProvider, SearchResponse, SearchResult


class BochaSearchProvider(SearchProvider):
    """博查搜索实现。"""

    API_URL = "https://api.bochaai.com/v1/web-search"

    def __init__(self):
        self.api_key = config.search.bocha_api_key

    def search(self, query: str, max_results: int = 8) -> SearchResponse:
        if not self.api_key:
            raise RuntimeError("未配置 BOCHA_API_KEY，无法使用博查搜索")

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "summary": True,
            "count": max_results,
        }
        resp = requests.post(self.API_URL, headers=headers, json=payload, timeout=15)
        resp.raise_for_status()
        data = resp.json()

        results = []
        for item in data.get("data", {}).get("webPages", {}).get("value", []):
            results.append(
                SearchResult(
                    title=item.get("name", ""),
                    url=item.get("url", ""),
                    snippet=item.get("summary", item.get("snippet", "")),
                    source="bocha",
                )
            )
        return SearchResponse(query=query, results=results)
