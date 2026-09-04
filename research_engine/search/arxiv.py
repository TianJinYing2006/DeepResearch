# -*- coding: utf-8 -*-
"""arXiv 学术检索 Provider（W4 Q3 定案：官方 API 直连，零新增依赖）。

- API：http://export.arxiv.org/api/query（官方就是 http，非 https——别被强制跳转坑）
- 排序：sortBy=relevance（研究要相关证据，不是最新 arXiv）
- 限流：模块级 RateLimiter min_interval=3s（官方礼貌请求要求；每轮仅 1 次请求，
  实测 2~4 轮最坏 +12s，且在线程池内不阻塞 web/rag）
- 返回：SearchResult(source="arxiv", metadata={arxiv_id, primary_category, published})
- 失败语义：请求异常/解析失败 → 空 SearchResponse（上层并行 return_exceptions 天然兼容，
  重试由 Q4 的读型 retries=1 在调度层负责，provider 不做内嵌重试）
"""
from __future__ import annotations

import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import List

import requests

from research_engine.search.base import SearchProvider, SearchResponse, SearchResult

ARXIV_API = "http://export.arxiv.org/api/query"
ARXIV_NS = {
    "atom": "http://www.w3.org/2005/Atom",
    "arxiv": "http://arxiv.org/schemas/atom",
}
# Q3：abstract 信息密度高，max_results 比 web 少（体积闭环 Q5 中入池再统一截断 1000 字符）
DEFAULT_MAX_RESULTS = 5
REQUEST_TIMEOUT = 15  # 秒；与 bocha 一致


class RateLimiter:
    """进程内最小间隔限速器（arXiv 官方要求 ≥3s 间隔）。"""

    def __init__(self, min_interval: float = 3.0):
        self.min_interval = min_interval
        self._lock = threading.Lock()
        self._last_ts: float = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            gap = self._last_ts + self.min_interval - now
            if gap > 0:
                time.sleep(gap)
            self._last_ts = time.monotonic()


def _clean_text(text: str | None) -> str:
    """Atom 文本常带排版换行/多空格，压平为单行。"""
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


class ArxivSearchProvider(SearchProvider):
    """arXiv 官方 API 直连实现。"""

    def __init__(self, max_results: int = DEFAULT_MAX_RESULTS):
        self.max_results = max_results
        self._limiter = RateLimiter(min_interval=3.0)

    def search(self, query: str, max_results: int = DEFAULT_MAX_RESULTS) -> SearchResponse:
        self._limiter.wait()  # 3s 间隔约束（Q3）
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        try:
            resp = requests.get(ARXIV_API, params=params, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            results = self._parse(resp.text)
            return SearchResponse(query=query, results=results)
        except Exception:  # noqa: BLE001 — 失败语义：空结果，交给调度层重试/降级
            return SearchResponse(query=query, results=[])

    def _parse(self, xml_text: str) -> List[SearchResult]:
        root = ET.fromstring(xml_text)
        results: List[SearchResult] = []
        for entry in root.findall("atom:entry", ARXIV_NS):
            id_url = _clean_text(entry.findtext("atom:id", "", ARXIV_NS))
            title = _clean_text(entry.findtext("atom:title", "", ARXIV_NS))
            summary = _clean_text(entry.findtext("atom:summary", "", ARXIV_NS))
            published = _clean_text(entry.findtext("atom:published", "", ARXIV_NS))
            if not id_url or not title:
                continue
            arxiv_id = id_url.rsplit("/abs/", 1)[-1]
            # primary_category 为自闭合空元素，信息在 term 属性（非文本）→ find().get("term")
            cat_el = entry.find("arxiv:primary_category", ARXIV_NS)
            primary_category = _clean_text(cat_el.get("term", "") if cat_el is not None else "")
            authors = [
                _clean_text(a.findtext("atom:name", "", ARXIV_NS))
                for a in entry.findall("atom:author", ARXIV_NS)
                if _clean_text(a.findtext("atom:name", "", ARXIV_NS))
            ]
            results.append(
                SearchResult(
                    title=title,
                    url=id_url.replace("http://", "https://"),  # 官方 API 返回 http → 规范化为 https（abs 页 https 可用；Q6 溯源前缀一统）
                    snippet=summary,
                    source="arxiv",
                    metadata={
                        "arxiv_id": arxiv_id,
                        "primary_category": primary_category,
                        "published": published,
                        "authors": authors[:3],  # 前 3 作者，防体积膨胀（Q5）
                    },
                )
            )
        return results