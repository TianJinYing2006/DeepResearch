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

# 需求 35 R4（TBD-6 拍板）：报告正文流式协议（自定义扩展，同属 custom events）。
# - REPORT_DELTA：正文增量（payload: {text, offset}；offset = 本轮（上次 reset 后）累计字符数）；
# - REPORT_RESET：清空预览缓冲（流式开始 / 半截失败时发出；终局正文整段替换，绝不拼接）。
REPORT_DELTA = "REPORT_DELTA"
REPORT_RESET = "REPORT_RESET"

# 需求 36（TBD-6 拍板）：HITL 交互事件（自定义扩展）。
# 为什么单独成组：挂起/回答/过期必须可从 `run_events` 重建卡片（SSE 断线重连后
# 按 sequence 回放即得最新交互状态），因此需要**稳定且可枚举**的事件名；
# 事件载荷与状态/轮次/到期的映射在 `web/backend/hitl.py` 单点定义。
# - CLARIFY_REQUESTED / PLAN_PROPOSED：交互卡片（挂起时写入；含 interaction_id/round/expires_at）；
# - CLARIFY_ANSWERED / PLAN_DECISION：用户回答（含 action 与文本；flagged 回放脱敏）；
# - CLARIFY_EXPIRED / PLAN_EXPIRED：等待到期的显式留痕（不伪装成 worker 执行超时）。
CLARIFY_REQUESTED = "CLARIFY_REQUESTED"
CLARIFY_ANSWERED = "CLARIFY_ANSWERED"
CLARIFY_EXPIRED = "CLARIFY_EXPIRED"
PLAN_PROPOSED = "PLAN_PROPOSED"
PLAN_DECISION = "PLAN_DECISION"
PLAN_EXPIRED = "PLAN_EXPIRED"

#: 需求 36：交互类事件全集（回放脱敏 / 契约测试共用；新增交互事件必须登记于此）。
INTERACTION_EVENT_TYPES = (
    CLARIFY_REQUESTED, CLARIFY_ANSWERED, CLARIFY_EXPIRED,
    PLAN_PROPOSED, PLAN_DECISION, PLAN_EXPIRED,
)

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
