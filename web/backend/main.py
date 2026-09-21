"""FastAPI 应用装配（需求 9 §7.4 最小骨架）。

**范围封顶（ADR-0001 §1.1 + D-20 三条硬边界）**：
只做呈现层与传输层。不做登录、用户系统、任务队列、多租户、权限、云端部署。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .agui import HEARTBEAT_FRAME, HEARTBEAT_SECONDS
from .runner import RunManager

app = FastAPI(title="DeepResearch", version="w9")

# 开发期：Vite dev server（5173）→ 后端（8000）。生产由 FastAPI 托管 dist/ 后即可去掉。
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# DR_DEMO=1 ⇒ 用假 graph 跑演示（不调 LLM、不烧钱），用于查看 UI 效果。
# 演示与真实运行**共用**后端全部 SSE 管线，故事件序列 / 降级推送 / 取消行为都是真的。
DEMO_MODE = os.getenv("DR_DEMO") == "1"

if DEMO_MODE:
    from .demo_graph import DemoGraph

    manager = RunManager(graph_factory=DemoGraph)
else:
    manager = RunManager()


class StartRequest(BaseModel):
    topic: str = Field(..., description="研究主题")
    instructions: str = Field("", description="附加要求")
    max_total_hops: Optional[int] = Field(None, ge=1, le=50, description="全局检索跳数上限")


class StartResponse(BaseModel):
    run_id: str


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "version": "w9"}


@app.post("/api/research", response_model=StartResponse)
def start(req: StartRequest) -> StartResponse:
    if not req.topic.strip():
        raise HTTPException(status_code=400, detail="topic 不能为空")
    return StartResponse(run_id=manager.start(req.topic, req.instructions, req.max_total_hops))


@app.post("/api/research/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    """立即返回（契约 C1：前端点取消后不必等后端确认就显示「正在停止」）。"""
    if not manager.cancel(run_id):
        raise HTTPException(status_code=404, detail="run_id 不存在")
    return {"ok": True}


@app.get("/api/research/{run_id}/stream")
async def stream(
    run_id: str,
    last_event_id: Optional[int] = Query(None),
    last_event_id_header: Optional[int] = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE 事件流（AG-UI 语义）。"""
    if not manager.exists(run_id):
        raise HTTPException(status_code=404, detail="run_id 不存在")
    resume_after = last_event_id_header if last_event_id_header is not None else last_event_id
    return StreamingResponse(
        _event_gen(run_id, resume_after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲，否则长连接会被攒着不推
        },
    )


async def _event_gen(run_id: str, last_event_id: Optional[int]) -> AsyncIterator[str]:
    cursor = last_event_id if last_event_id is not None else -1
    loop = asyncio.get_running_loop()
    while True:
        frame, finished = await loop.run_in_executor(
            None,
            manager.wait_for_frame,
            run_id,
            cursor,
            HEARTBEAT_SECONDS,
        )
        if frame is not None:
            cursor += 1
            yield frame
            continue
        if finished:
            break
        yield HEARTBEAT_FRAME


FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
