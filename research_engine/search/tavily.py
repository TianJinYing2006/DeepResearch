"""Tavily 搜索 Provider 实现。

接入背景
--------
博查（``bocha``）额度耗尽后返回 ``403 "You do not have enough money or package
quota"``，网络搜索整条链路降级为零结果（W8 Arm 1 记为 ``not_configured``），
研究报告退化为模型无依据生成。Tavily 提供 **1000 次/月免费额度**，且专为
LLM/Agent 设计（返回已去重的正文片段，省掉一层解析），作为可切换的搜索源接入。

W8 Arm 4 契约（与 :mod:`research_engine.search.bocha` 完全一致）
----------------------------------------------------------------
**失败不再靠抛异常表达**，一律返回带 ``failure_reason`` 的 :class:`SearchResponse`
—— 工具层是**工具类 5 值的唯一产生点**，消费方单向派生
（``DegradationEntry(reason=resp.failure_reason, ...)``），
**禁止在消费处手写第二个字面量**。

四类失败路径与博查同构：

* 未配置 API key ⇒ ``not_configured``；
* 超时 ⇒ ``timeout``；
* HTTP 401/403 ⇒ ``not_configured``（key 失效 / 额度耗尽，处置动作是「换配置」而非重试）；
  其余非 2xx（含 429 限流）⇒ ``provider_error``；
* 拿到响应但解不出 JSON ⇒ ``parse_error``；
* 正常响应但零结果 ⇒ ``empty_result``（**是结果不是故障**，D-03 不上抛为 run 级降级）。
"""
from __future__ import annotations

import requests

from config import config
from research_engine.failure_reasons import FailureReason
from research_engine.search.base import SearchProvider, SearchResponse, SearchResult


class TavilySearchProvider(SearchProvider):
    """Tavily 搜索实现（专为 AI Agent 设计，返回已去重正文片段）。"""

    API_URL = "https://api.tavily.com/search"
    #: Tavily 的 advanced 深度会额外抓取正文，明显更慢；研究流程每跳都要搜，
    #: 默认走 basic（关键词召回已足够，后续由 validator 判忠实度）。
    SEARCH_DEPTH = "basic"
    TIMEOUT = 20

    def __init__(self):
        self.api_key = config.search.tavily_api_key

    def search(self, query: str, max_results: int = 8) -> SearchResponse:
        """执行搜索。**任何失败都返回带 failure_reason 的响应，不抛异常。**"""
        # ① 未配置：不再 raise（raise 会让消费方只能凭异常文本猜原因）
        if not self.api_key:
            return SearchResponse(
                query=query,
                failure_reason=FailureReason.NOT_CONFIGURED.value,
                failure_detail="未配置 TAVILY_API_KEY",
            )

        payload = {
            "api_key": self.api_key,
            "query": query,
            "max_results": max_results,
            "search_depth": self.SEARCH_DEPTH,
            # 研究流程要的是可引用的证据片段，不是 Tavily 自己生成的答案
            # （后者会绕过 citation 校验，等于把未经核实的结论喂给 writer）。
            "include_answer": False,
        }

        # ② 传输/协议层失败：按异常类型精确归类，不猜
        try:
            resp = requests.post(self.API_URL, json=payload, timeout=self.TIMEOUT)
            resp.raise_for_status()
        except requests.exceptions.Timeout as e:
            return SearchResponse(query=query,
                                  failure_reason=FailureReason.TIMEOUT.value,
                                  failure_detail=str(e)[:300])
        except requests.exceptions.HTTPError as e:
            code = getattr(e.response, "status_code", None)
            # 401/403 多半是 key 失效或额度耗尽 ⇒ 处置动作是「换配置」，不是重试
            reason = (FailureReason.NOT_CONFIGURED.value if code in (401, 403)
                      else FailureReason.PROVIDER_ERROR.value)
            return SearchResponse(query=query, failure_reason=reason,
                                  failure_detail=f"HTTP {code}: {str(e)[:200]}")
        except requests.exceptions.RequestException as e:
            return SearchResponse(query=query,
                                  failure_reason=FailureReason.PROVIDER_ERROR.value,
                                  failure_detail=str(e)[:300])

        # ③ 解析失败：拿到响应但解不出 JSON（网关拦截页 / 限流页常见）
        try:
            data = resp.json()
        except ValueError as e:  # JSONDecodeError 是 ValueError 子类
            return SearchResponse(query=query,
                                  failure_reason=FailureReason.PARSE_ERROR.value,
                                  failure_detail=str(e)[:300])

        results = []
        for item in data.get("results", []) or []:
            url = item.get("url", "")
            if not url:
                continue
            results.append(
                SearchResult(
                    title=item.get("title", ""),
                    url=url,
                    # Tavily 返回的是 content（正文片段），比 SERP snippet 更长更可引用
                    snippet=item.get("content", ""),
                    source="web",
                    metadata={"score": item.get("score")},
                )
            )

        # ④ 正常响应但零结果：是「结果」不是「故障」（D-03 不上抛为 run 级降级）
        if not results:
            return SearchResponse(query=query, results=[],
                                  failure_reason=FailureReason.EMPTY_RESULT.value)
        return SearchResponse(query=query, results=results)
