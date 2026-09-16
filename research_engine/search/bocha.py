"""博查搜索 Provider 实现。

调用博查 Web Search API，返回结构化搜索结果。

W8 Arm 4：**失败不再靠抛异常表达**，一律返回带 ``failure_reason`` 的
:class:`SearchResponse` —— 工具层是工具类 5 值的**唯一产生点**，消费方单向派生
（``DegradationEntry(reason=resp.failure_reason, ...)``）。

改造前这里有三种「说不清为什么」的失败路径：

* 未配置 API key ⇒ ``raise RuntimeError``（消费方只能凭异常文本猜原因）；
* HTTP 非 2xx ⇒ ``raise_for_status()`` 抛出（同上）；
* 正常响应但零结果 ⇒ 静默返回空 ``SearchResponse``（与「搜索失败」不可分）。
"""
from __future__ import annotations

import requests

from config import config
from research_engine.failure_reasons import FailureReason
from research_engine.search.base import SearchProvider, SearchResponse, SearchResult


class BochaSearchProvider(SearchProvider):
    """博查搜索实现。"""

    API_URL = "https://api.bochaai.com/v1/web-search"

    def __init__(self):
        self.api_key = config.search.bocha_api_key

    def search(self, query: str, max_results: int = 8) -> SearchResponse:
        # ① 未配置：不再 raise（raise 会让消费方只能凭异常文本猜原因）
        if not self.api_key:
            return SearchResponse(
                query=query,
                failure_reason=FailureReason.NOT_CONFIGURED.value,
                failure_detail="未配置 BOCHA_API_KEY",
            )

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "query": query,
            "summary": True,
            "count": max_results,
        }

        # ② 传输/协议层失败：按异常类型精确归类，不猜
        try:
            resp = requests.post(self.API_URL, headers=headers, json=payload, timeout=15)
            resp.raise_for_status()
        except requests.exceptions.Timeout as e:
            return SearchResponse(query=query, failure_reason=FailureReason.TIMEOUT.value,
                                  failure_detail=str(e)[:300])
        except requests.exceptions.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            # 401/403 多半是 key 失效/无权限 ⇒ 处置动作是「换配置」，不是重试
            reason = (FailureReason.NOT_CONFIGURED.value if code in (401, 403)
                      else FailureReason.PROVIDER_ERROR.value)
            return SearchResponse(query=query, failure_reason=reason,
                                  failure_detail=f"HTTP {code}: {str(e)[:200]}")
        except requests.exceptions.RequestException as e:
            return SearchResponse(query=query, failure_reason=FailureReason.PROVIDER_ERROR.value,
                                  failure_detail=str(e)[:300])

        # ③ 解析失败：拿到响应但解不出 JSON（网关拦截页 / 限流页常见）
        try:
            data = resp.json()
        except ValueError as e:  # JSONDecodeError 是 ValueError 子类
            return SearchResponse(query=query, failure_reason=FailureReason.PARSE_ERROR.value,
                                  failure_detail=str(e)[:300])

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

        # ④ 正常响应但零结果：是「结果」不是「故障」（D-03 不上抛为 run 级降级）
        if not results:
            return SearchResponse(query=query, results=[],
                                  failure_reason=FailureReason.EMPTY_RESULT.value)
        return SearchResponse(query=query, results=results)
