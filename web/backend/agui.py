"""AG-UI 事件封装（需求 9 §7.2）。

**为什么对齐 AG-UI**：它是「Agent ↔ 前端」的**事件语义标准**，
采纳者含 Google ADK、LangChain、AWS、Microsoft Agent Framework、Mastra、PydanticAI、CrewAI、
LlamaIndex，且 **LangGraph 有官方适配**。后端按它的事件名吐流，
将来换 CopilotKit 前端时**后端一行不用改**。

⚠️ 对齐的是**语义**，不是引入 SDK —— 本项目用原生 SSE 手写帧，不依赖任何 AG-UI 包。
"""
from __future__ import annotations

import json
from typing import Any, Dict

# --- AG-UI 标准事件 -----------------------------------------------------------
RUN_STARTED = "RUN_STARTED"
RUN_FINISHED = "RUN_FINISHED"
RUN_ERROR = "RUN_ERROR"
STEP_STARTED = "STEP_STARTED"
STEP_FINISHED = "STEP_FINISHED"
STATE_DELTA = "STATE_DELTA"

# --- 自定义扩展 ---------------------------------------------------------------
# AG-UI **没有**「降级」事件（它标准化的是事件生命周期，不是事件全集），
# 因此这里扩展一个。协议允许 custom events，故不破坏对齐。
# 这条正是硬伤 3（降级事后才可见）的解药。
DEGRADATION = "DEGRADATION"

# 心跳间隔（秒）。依据：nginx 默认 proxy_read_timeout=60 ⇒ 15s 留 4 倍余量。
# （GPT Researcher 用 30s 心跳作参照，本项目取更保守的 15s。）
HEARTBEAT_SECONDS = 15

# 心跳帧：SSE **注释行**，不是事件 ⇒ 不会触发前端 onmessage。
HEARTBEAT_FRAME = ": ping\n\n"


def sse_frame(*, event_id: int, event_type: str, payload: Dict[str, Any]) -> str:
    """把一个 AG-UI 事件序列化为 SSE 帧。

    同时写 `event:` 与 `data:`：前者便于浏览器 DevTools 调试，
    后者承载完整 JSON（含 `type` 字段），前端 `onmessage` 直接解析即可。
    """
    body = json.dumps({"type": event_type, **payload}, ensure_ascii=False)
    return f"id: {event_id}\nevent: {event_type}\ndata: {body}\n\n"
