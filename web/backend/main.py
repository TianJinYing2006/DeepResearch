"""FastAPI 应用装配（需求 9 §7.4 最小骨架）。

**范围封顶（ADR-0001 §1.1 + D-20 三条硬边界）**：
只做呈现层与传输层。不做登录、用户系统、任务队列、多租户、权限、云端部署。
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import AsyncIterator, Optional

from fastapi import FastAPI, Header, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import config
from research_engine.search.base import KNOWN_PROVIDERS

from .agui import HEARTBEAT_FRAME, HEARTBEAT_SECONDS
from .errors import ApiError, http_error
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

    # 演示节奏可调：E2E 用 DR_DEMO_STEP_SECONDS 把单节点压到零点几秒，
    # 否则 11 个节点 × 1.8s 的演示会让每条用例都等 20s（且更容易互相撞并发闸）。
    _DEMO_STEP_SECONDS = float(os.getenv("DR_DEMO_STEP_SECONDS", "1.8"))

    manager = RunManager(graph_factory=lambda: DemoGraph(step_seconds=_DEMO_STEP_SECONDS))
else:
    manager = RunManager()


class StartRequest(BaseModel):
    topic: str = Field(..., description="研究主题")
    instructions: str = Field("", description="附加要求")
    max_total_hops: Optional[int] = Field(None, ge=1, le=50, description="全局检索跳数上限")
    max_subquestions: Optional[int] = Field(None, ge=1, le=8, description="Planner 子问题数上限")
    search_provider: Optional[str] = Field(None, description="搜索引擎：bocha | tavily（不传则用配置默认值）")
    enable_arxiv: Optional[bool] = Field(None, description="是否开启学术检索（arXiv）")


class StartResponse(BaseModel):
    run_id: str


#: 搜索源展示名 —— 新增 provider 时在此登记即可被前端 /api/options 列出
_PROVIDER_LABELS = {"bocha": "博查", "tavily": "Tavily"}


def _provider_has_key(name: str) -> bool:
    """该搜索源是否配了 key（决定前端是否把它列为可选）。"""
    if name == "bocha":
        return bool(config.search.bocha_api_key)
    if name == "tavily":
        return bool(config.search.tavily_api_key)
    return False


@app.get("/api/options")
def options() -> dict:
    """前端运行选项：可用的搜索源 + 学术检索默认值。

    只把**已配 key** 的源列为可用 —— 否则用户选了没 key 的源，整场研究每跳都
    降级为零结果，跑完了才发现白跑（博查额度耗尽正是这个情形，只能靠 403 事后发现）。
    ⚠️ 已配 key ≠ 额度充足：额度耗尽只能在调用时由 provider 报出。
    """
    from research_engine.search.base import KNOWN_PROVIDERS
    providers = [
        {"value": name, "label": _PROVIDER_LABELS.get(name, name),
         "available": _provider_has_key(name)}
        for name in KNOWN_PROVIDERS
    ]
    return {
        "search_providers": providers,
        "default_provider": config.search.provider,
        "enable_arxiv_default": config.search.enable_arxiv,
        "max_total_hops_default": config.research.max_total_hops,
        "max_subquestions_default": config.research.max_subquestions,
        # P1-2 / P1-3：把后端**实际生效**的闸值下发给前端，前端才能显示剩余时间
        # 与「已有研究在运行」提示，而不是靠猜。
        "run_timeout_seconds": manager.run_timeout_seconds,
        "max_concurrent_runs": manager.max_concurrent_runs,
    }


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": "w9",
        "demo_mode": DEMO_MODE,
        "active_runs": manager.active_runs,
        "max_concurrent_runs": manager.max_concurrent_runs,
        "run_timeout_seconds": manager.run_timeout_seconds,
    }


@app.post("/api/research", response_model=StartResponse)
def start(req: StartRequest) -> StartResponse:
    if not req.topic.strip():
        raise http_error("empty_topic", "topic 不能为空")
    # 搜索引擎必须**启动前**校验：未知源 / 未配 key 若放行，整场研究每跳都降级为零
    # 结果，跑完才在报告里发现白跑（博查额度耗尽就是这个情形的极端版）。
    if req.search_provider is not None:
        if req.search_provider not in KNOWN_PROVIDERS:
            raise http_error(
                "unknown_search_provider",
                f"未知搜索引擎：{req.search_provider}",
                detail=f"known={' / '.join(KNOWN_PROVIDERS)}",
                node=None,
            )
        if not _provider_has_key(req.search_provider):
            raise http_error(
                "missing_search_key",
                f"搜索引擎「{req.search_provider}」未配置 API key",
                detail=f"provider={req.search_provider}",
                component="search",
            )
    try:
        run_id = manager.start(
            req.topic, req.instructions, req.max_total_hops,
            req.search_provider, req.enable_arxiv, req.max_subquestions)
    except ApiError as exc:  # 并发上限（P1-3）等运行器侧拒绝
        raise exc.to_http() from exc
    return StartResponse(run_id=run_id)


@app.get("/api/research/{run_id}")
def run_status(run_id: str) -> dict:
    """运行画像（P1-4）：内存态，重启即失（D-19 不做持久化）。

    与 SSE 互补：SSE 是**增量**流，断了就靠 `Last-Event-ID` 续；本接口是**快照**，
    供页面刷新 / 新标签页直接问一句「还活着吗、跑到哪了」，不必重开一条流。
    """
    snap = manager.snapshot(run_id)
    if snap is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    return snap


@app.post("/api/research/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    """立即返回（契约 C1：前端点取消后不必等后端确认就显示「正在停止」）。"""
    if not manager.cancel(run_id):
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    return {"ok": True}


@app.get("/api/research/{run_id}/report")
def export_report(run_id: str, fmt: str = Query("md", alias="format", pattern="^(md|json)$")) -> Response:
    """报告导出（P1-6）：`format=md` 下载 Markdown，`format=json` 取结构化载荷。

    为什么走后端而不沿用前端 Blob：前端那份只有 `result.report` 正文，
    导出的文件脱离页面后无从自证来源；后端版本带 run_id / run_status /
    降级条数等审计元数据与引用清单。
    """
    if not manager.exists(run_id):
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    if not manager.has_result(run_id):
        if not manager.is_finished(run_id):
            raise http_error("report_not_ready", "研究尚未结束，暂无报告可导出")
        raise http_error("report_unavailable", "本次运行没有产出报告")
    payload = manager.export_payload(run_id)
    if payload is None or not str(payload.get("result", {}).get("report") or "").strip():
        raise http_error("report_unavailable", "本次运行没有产出报告")

    if fmt == "json":
        return JSONResponse(payload)
    return Response(
        content=manager.export_markdown(run_id) or "",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="deepresearch-{run_id}.md"'},
    )


@app.get("/api/research/{run_id}/stream")
async def stream(
    run_id: str,
    last_event_id: Optional[int] = Query(None),
    last_event_id_header: Optional[int] = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE 事件流（AG-UI 语义）。"""
    if not manager.exists(run_id):
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
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
        # P1-2：协作式停止（取消 / 超时）依赖节点边界检查点。若节点内部挂死，
        # 边界永远不到 ⇒ 这里按**硬截止**补一帧 RUN_ERROR(stop_forced) 收口，
        # 让客户端停止干等。后台线程可能仍在跑（Python 无法 kill 线程），
        # 这条限制写在 runner.force_stop_if_overdue 的 docstring 里。
        forced = manager.force_stop_if_overdue(run_id)
        if forced is not None:
            yield forced
            break
        yield HEARTBEAT_FRAME


FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
