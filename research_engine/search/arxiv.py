"""arXiv 学术检索 Provider（W4 Q3 定案：官方 API 直连，零新增依赖）。

- API：**https**://export.arxiv.org/api/query
  ⚠️ 2026-09 修正：原实现用 ``http://``，并注明「官方就是 http，非 https」。
  **该结论已过时** —— arXiv 现在对 http 返回 **301 强制跳转 https**，而跳转链在
  本项目部署环境下会失败（代理对重定向后的 CONNECT 隧道返回 502），表现为
  整个 arXiv 源**持续 provider_error**、每跳都记一条降级。改为 https 直连后
  实测恢复（HTTP 200 + 可解析条目），且省掉一次往返。
  教训：外部 API 的 scheme 约定会变，且「能跳转」不等于「跳转链在代理后可用」——
  能被 301 救回来的请求，也可能死在重定向之后的那一跳上。
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

from research_engine.failure_reasons import FailureReason  # W8 Arm 4
from research_engine.search.base import SearchProvider, SearchResponse, SearchResult

ARXIV_API = "https://export.arxiv.org/api/query"
# 间歇性连接失败的重试次数与退避（秒）。
# 实测（2026-09-22）：代理对 export.arxiv.org 的 CONNECT 隧道约 **40% 失败**
# （连测 5 次 3 成 2 败），表现为 ProxyError / Max retries exceeded —— 端点本身是好的，
# 属于**间歇性**网络故障，重试一次基本就能成。
# ⚠️ 只重试**连接类**异常：HTTP 4xx/5xx 是服务端的明确答复，重试不会改变结果。
MAX_ATTEMPTS = 3
RETRY_BACKOFF = (1.0, 2.0)  # 第 2、3 次尝试前的额外等待（叠加在 3s 礼貌间隔之上）
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
        params = {
            "search_query": f"all:{query}",
            "start": 0,
            "max_results": max_results,
            "sortBy": "relevance",
            "sortOrder": "descending",
        }
        for attempt in range(1, MAX_ATTEMPTS + 1):
            self._limiter.wait()  # 3s 间隔约束（Q3）
            # ① 传输/协议层：按异常类型精确归类（W8 Arm 4 —— 不再靠 classify_tool_exception 猜）
            try:
                resp = requests.get(ARXIV_API, params=params, timeout=REQUEST_TIMEOUT)
                resp.raise_for_status()
            except requests.exceptions.HTTPError as e:
                # HTTP 是服务端明确答复（含 5xx）—— 重试不会改变结果，直接归类
                return self._fail(query, FailureReason.PROVIDER_ERROR.value,
                                  f"HTTP {getattr(e.response, 'status_code', None)}: {e}")
            except requests.exceptions.Timeout as e:
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF[attempt - 1])
                    continue
                return self._fail(query, FailureReason.TIMEOUT.value,
                                  f"重试 {MAX_ATTEMPTS} 次仍超时: {e}")
            except requests.exceptions.RequestException as e:
                # ConnectionError / ProxyError 等**间歇性**连接故障 ⇒ 值得重试
                if attempt < MAX_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF[attempt - 1])
                    continue
                return self._fail(query, FailureReason.PROVIDER_ERROR.value,
                                  f"重试 {MAX_ATTEMPTS} 次仍失败: {e}")

            # ② 解析层：拿到 200 但不合法 Atom ⇒ parse_error（与「确实没结果」分开）
            try:
                results = self._parse(resp.text)
            except Exception as e:  # noqa: BLE001 — 本段只做 XML 解析，归为 parse_error 是准确的
                return self._fail(query, FailureReason.PARSE_ERROR.value, str(e))

            # ③ 正常响应但零命中：是「结果」不是「故障」（D-03 不上抛为 run 级降级）
            if not results:
                return SearchResponse(query=query, results=[],
                                      failure_reason=FailureReason.EMPTY_RESULT.value)
            return SearchResponse(query=query, results=results)

        return self._fail(query, FailureReason.PROVIDER_ERROR.value, "重试循环意外退出")

    @staticmethod
    def _fail(query: str, reason: str, detail: str) -> SearchResponse:
        """构造失败响应（工具层 5 值的唯一产生点，消费方单向派生即可）。"""
        return SearchResponse(query=query, results=[], failure_reason=reason,
                              failure_detail=detail[:300])

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