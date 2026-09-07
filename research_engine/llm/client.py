"""LLM 客户端封装。

基于 OpenAI 兼容接口调用阿里云百炼 Qwen 系列模型。
所有 Agent 通过本模块访问 LLM，统一处理结构化输出与错误降级。
客户端懒加载，图构建时不强制需要 API key。
"""
from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from config import config


class LLMClient:
    """OpenAI 兼容的 LLM 客户端，封装百炼 Qwen 调用。"""

    # W3（grill Q3=D'）：类级无条件计数（跨所有 agent 实例共享累计，不管 state 是否传入）。
    # 按 run 对齐后与 state.token_used 差值为"漏传 state 的调用路径累计"——单点记账自动暴露漏计。
    tokens_total = 0
    # W5（grill Q2）：双轨分桶——模型名桶（成本 = Σ token×该模型单价）+ 职责桶（面试口径"critic 占 85%"）。
    # 类级跨实例共享；eval run.py 在 Phase 1/2 各自开头调用 reset_stats() 隔离出研究/评估成本。
    # model_io_stats 记 input/output 拆分，成本按 W3 pricing 分层精确加权（无需近似比例）。
    model_stats: Dict[str, int] = {}
    role_stats: Dict[str, int] = {}
    model_io_stats: Dict[str, Dict[str, int]] = {}

    def __init__(self, model: Optional[str] = None, role: Optional[str] = None):
        self.model = model or config.llm.smart_model
        self.role = role or "unknown"
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
        timeout: Optional[float] = None,
    ) -> str:
        """普通对话，返回文本内容。state 传入时把 token 用量累加进 state.token_used（Q6-B）。

        W3（Q3=D' 修复）：_accumulate_usage **无条件**调用——tokens_total（类级）任何调用都累计；
        state.token_used 仅当 state 非 None 时累计（内部判断）。跑完差值 = 漏传 state 的调用路径。
        W5（Q4）：timeout 透传给 openai SDK（judge 调用设 60s 治"LLM 层无超时"僵尸源）。
        """
        kwargs: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature if temperature is not None else config.llm.temperature,
        }
        if max_tokens:
            kwargs["max_tokens"] = max_tokens
        if response_format:
            kwargs["response_format"] = response_format
        if timeout is not None:
            kwargs["timeout"] = timeout

        resp = self._get_client().chat.completions.create(**kwargs)
        content = resp.choices[0].message.content or ""
        self._accumulate_usage(resp, state)  # D'：无条件调用（内部处理 state=None）
        return content

    def _accumulate_usage(self, resp, state) -> None:
        """把响应里的 token 用量累加进 state.token_used（Q6-B 可观测+控闸）。

        W3（Q3=D'）：tokens_total（类级）**无条件**累加（不管 state 是否传入）；
        state.token_used 只累加传 state 的调用。按 run 对齐后差值 = 漏传 state 的调用路径累计。
        W5（Q2）：模型名桶 + 职责桶同步累加（双轨成本报告）。
        """
        try:
            u = resp.usage
            if u is not None:
                total = int(getattr(u, "total_tokens", 0) or 0)
                inp = int(getattr(u, "prompt_tokens", 0) or 0)
                out = int(getattr(u, "completion_tokens", 0) or 0)
                LLMClient.tokens_total += total  # D'：无条件（类级，跨实例共享）
                LLMClient.model_stats[self.model] = LLMClient.model_stats.get(self.model, 0) + total
                LLMClient.role_stats[self.role] = LLMClient.role_stats.get(self.role, 0) + total
                io = LLMClient.model_io_stats.setdefault(self.model, {"input": 0, "output": 0})
                io["input"] += inp
                io["output"] += out
                if state is not None:
                    state.token_used = getattr(state, "token_used", 0) + total
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def reset_stats() -> None:
        """清零三个类级计数器（eval run.py Phase 1/2 各自开头调用，隔离研究/评估成本）。"""
        LLMClient.tokens_total = 0
        LLMClient.model_stats = {}
        LLMClient.role_stats = {}
        LLMClient.model_io_stats = {}

    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: Optional[float] = None,
        state: Optional[Any] = None,
        schema: Optional[type[BaseModel]] = None,
        retries: int = 2,
        timeout: Optional[float] = None,
    ) -> Dict[str, Any]:
        """结构化输出，返回 JSON 对象（可选 Pydantic 校验 + 自动纠错重试）。

        - 解析或校验失败时，把上次坏输出 + 错误信息追加进 messages 重试，最多 retries 次。
        - schema 传入时用 Pydantic 校验并 model_dump() 成 dict；不传则只保证是合法 JSON。
        - state 照常累加 token 用量（Q6-B）；新增参数带默认值，现有调用零改动。
        - W5（Q4）：timeout 透传（judge 类调用设 60s）。
        """
        attempt = 0
        while True:
            content = self.chat(
                messages,
                temperature=temperature,
                response_format={"type": "json_object"},
                state=state,
                timeout=timeout,
            )
            try:
                obj = self._parse_json(content)
                if schema is not None:
                    obj = schema.model_validate(obj).model_dump()
                return obj
            except (json.JSONDecodeError, ValidationError) as exc:
                attempt += 1
                if attempt > retries:
                    raise
                messages = self._with_correction(messages, content, exc)

    @staticmethod
    def _parse_json(content: str) -> Any:
        """剥掉 ```json 代码块包裹后解析 JSON。"""
        text = content.strip()
        if text.startswith("```"):
            text = text[3:].strip()
            if text.lower().startswith("json"):
                text = text[4:].strip()
        if text.endswith("```"):
            text = text[:-3].strip()
        return json.loads(text)

    @staticmethod
    def _with_correction(
        messages: List[Dict[str, str]], bad_content: str, exc: Exception
    ) -> List[Dict[str, str]]:
        """把坏输出 + 错误反馈追加进 messages，用于纠错重试。"""
        msgs = list(messages)
        msgs.append({"role": "assistant", "content": bad_content[:800]})
        msgs.append(
            {
                "role": "user",
                "content": f"你上一次的输出不是合法 JSON（错误：{exc}）。"
                "请只输出一个合法的 JSON 对象，不要包含解释或 markdown 代码块。",
            }
        )
        return msgs


def build_messages(system: str, user: str) -> List[Dict[str, str]]:
    """构造标准 messages。"""
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
