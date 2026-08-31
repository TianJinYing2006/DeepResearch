# -*- coding: utf-8 -*-
"""LLM 客户端封装。

基于 OpenAI 兼容接口调用阿里云百炼 Qwen 系列模型。
所有 Agent 通过本模块访问 LLM，统一处理结构化输出与错误降级。
客户端懒加载，图构建时不强制需要 API key。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openai import OpenAI

from config import config


class LLMClient:
    """OpenAI 兼容的 LLM 客户端，封装百炼 Qwen 调用。"""

    def __init__(self, model: Optional[str] = None):
        self.model = model or config.llm.smart_model
        self._client: Optional[OpenAI] = None

    def _get_client(self) -> OpenAI:
        """懒加载客户端。"""
        if self._client is None:
            if not config.llm.api_key:
                raise RuntimeError("未配置 DASHSCOPE_API_KEY，无法调用 LLM")
            self._client = OpenAI(
                base_url=config.llm.base_url,
                api_key=config.llm.api_key,
            )
        return self._client

    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        max_tokens: Optional[int] = None,
        response_format: Optional[Dict[str, str]] = None,
        state: Optional[Any] = None,
    ) -> str:
        """普通对话，返回文本内容。state 传入时把 token 用量累加进 state.token_used（Q6-B）。"""
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else config.llm.temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["response_format"] = response_format

        resp = self._get_client().chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        if state is not None:
            self._accumulate_usage(resp, state)
        return content

    def _accumulate_usage(self, resp, state) -> None:
        """把响应里的 token 用量累加进 state.token_used（Q6-B 可观测+控闸）。"""
        try:
            u = resp.usage
            if u is not None:
                total = int(getattr(u, "total_tokens", 0) or 0)
                state.token_used = getattr(state, "token_used", 0) + total
        except Exception:  # noqa: BLE001
            pass

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        state: Optional[Any] = None,
    ) -> Dict[str, Any]:
        """结构化输出，返回 JSON 对象。失败时抛出异常由调用方降级。state 传入时累加 token 用量。"""
        content = self.chat(
            messages,
            temperature=temperature,
            response_format={"type": "json_object"},
            state=state,
        )
        # 兼容模型可能返回 ```json 包裹
        content = content.strip()
        if content.startswith("```"):
            content = content.strip("`")
            if content.startswith("json"):
                content = content[4:]
        return json.loads(content)


def build_messages(system: str, user: str) -> List[Dict[str, str]]:
    """构造标准 messages。"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
