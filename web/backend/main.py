"""FastAPI 应用装配（需求 9 §7.4 最小骨架）。

**范围封顶（ADR-0001 §1.1 + D-20 三条硬边界）**：
只做呈现层与传输层。不做登录、用户系统、任务队列、多租户、权限、云端部署。
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, Header, Query
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import config
from research_engine.search.base import KNOWN_PROVIDERS

from .agui import HEARTBEAT_FRAME, HEARTBEAT_SECONDS, sse_frame
from .errors import ApiError, error_payload, http_error
from .runner import RunManager
from .store import ACTIVE_STATUSES, RunStore

app = FastAPI(title="DeepResearch", version="w9")


@app.exception_handler(RequestValidationError)
async def request_validation_error(_request, exc: RequestValidationError) -> JSONResponse:
    """#11：FastAPI 自带的 422 也要走**结构化错误**契约（前端只认键，不解析文本）。

    否则「所有 HTTP 错误共用 {code,message,...}」这句话在参数校验一类错误上是假的。
    """
    detail = "; ".join(
        f"{'.'.join(str(part) for part in err.get('loc', []))}: {err.get('msg', '')}"
        for err in exc.errors()
    )[:500]
    return JSONResponse(
        status_code=422,
        content={"detail": error_payload("invalid_request", "请求参数校验失败", detail=detail)},
    )


# 开发期默认允许 Vite dev server（5173）；staging/生产必须用 DR_CORS_ORIGINS 显式覆盖
# （P1：CORS 环境变量化，不允许把 localhost 默认带进生产）。
DEFAULT_CORS_ORIGINS = ("http://localhost:5173", "http://127.0.0.1:5173")


def _cors_origins() -> list[str]:
    raw = os.getenv("DR_CORS_ORIGINS", "")
    if raw.strip():
        return [origin.strip() for origin in raw.split(",") if origin.strip()]
    return list(DEFAULT_CORS_ORIGINS)


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["*"],
    allow_headers=["*"],
)

# DR_DEMO=1 ⇒ 用假 graph 跑演示（不调 LLM、不烧钱），用于查看 UI 效果。
# 演示与真实运行**共用**后端全部 SSE 管线，故事件序列 / 降级推送 / 取消行为都是真的。
DEMO_MODE = os.getenv("DR_DEMO") == "1"


def _make_store() -> Optional[RunStore]:
    """按 `DR_DATABASE_URL` 装配任务库；未配置时返回 None（本地开发零依赖模式）。"""
    dsn = os.getenv("DR_DATABASE_URL", "").strip()
    return RunStore(dsn) if dsn else None


# P2-C：任务库（PostgreSQL）。演示模式不接库，保证 E2E / 本地 UI 演示零依赖。
store = None if DEMO_MODE else _make_store()
if store is not None:
    try:
        # 单实例内存态执行的既有事实：进程重启后，库里非终局的任务已无人执行 ⇒ 标记 LOST。
        store.mark_stale_as_lost()
    except Exception:  # noqa: BLE001 —— 库不可用时由 readiness 与请求侧结构化错误表达
        pass

if DEMO_MODE:
    from .demo_graph import DemoGraph

    # 演示节奏可调：E2E 用 DR_DEMO_STEP_SECONDS 把单节点压到零点几秒，
    # 否则 11 个节点 × 1.8s 的演示会让每条用例都等 20s（且更容易互相撞并发闸）。
    _DEMO_STEP_SECONDS = float(os.getenv("DR_DEMO_STEP_SECONDS", "1.8"))

    manager = RunManager(graph_factory=lambda: DemoGraph(step_seconds=_DEMO_STEP_SECONDS))
else:
    manager = RunManager(store=store)


class StartRequest(BaseModel):
    topic: str = Field(..., description="研究主题")
    instructions: str = Field("", description="附加要求")
    max_total_hops: Optional[int] = Field(None, ge=1, le=50, description="全局检索跳数上限")
    max_subquestions: Optional[int] = Field(None, ge=1, le=8, description="Planner 子问题数上限")
    search_provider: Optional[str] = Field(None, description="搜索引擎：bocha | tavily（不传则用配置默认值）")
    enable_arxiv: Optional[bool] = Field(None, description="是否开启学术检索（arXiv）")
    idempotency_key: Optional[str] = Field(
        None, max_length=128, description="创建幂等键：重复提交返回既有 run_id，不重复执行")


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
        "persistence": store is not None,
        "active_runs": manager.active_runs,
        "max_concurrent_runs": manager.max_concurrent_runs,
        "run_timeout_seconds": manager.run_timeout_seconds,
    }


def _probe_target(url: str, default_port: int) -> Optional[tuple[str, int]]:
    parsed = urlsplit(url)
    if not parsed.hostname:
        return None
    return parsed.hostname, parsed.port or default_port


def _tcp_reachable(host: str, port: int, timeout: float = 0.5) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


@app.get("/api/health/live")
def health_live() -> dict:
    """liveness：进程活着即 200，不查依赖（依赖抖动不应触发编排层重启）。"""
    return {"ok": True, "check": "live"}


@app.get("/api/health/ready")
def health_ready(response: Response) -> dict:
    """readiness：只对**已配置**的依赖做探针。

    - PostgreSQL：配置了任务库（P2-C）时用真实 `SELECT 1` 探针，否则退化为 TCP 连接；
    - Redis：TCP 探针（P3 接队列后升级为 `PING`）；
    - `not_configured` 不判失败 —— 过渡期（还没接库）不会把本地与 CI 全判红；
      一旦设置但探不通即 503，由编排层摘流量。
    """
    checks = {}
    for name, env_key, default_port in (
        ("postgres", "DR_DATABASE_URL", 5432),
        ("redis", "DR_REDIS_URL", 6379),
    ):
        if name == "postgres" and store is not None:
            try:
                store.ping()
                checks[name] = {"status": "ok", "target": "dr_database_url"}
            except Exception as exc:  # noqa: BLE001
                checks[name] = {
                    "status": "unreachable",
                    "target": f"ping failed: {type(exc).__name__}",
                }
            continue
        raw = os.getenv(env_key, "").strip()
        if not raw:
            checks[name] = {"status": "not_configured"}
            continue
        target = _probe_target(raw, default_port)
        if target is None:
            checks[name] = {"status": "invalid_url"}
            continue
        host, port = target
        checks[name] = {
            "status": "ok" if _tcp_reachable(host, port) else "unreachable",
            "target": f"{host}:{port}",
        }
    failed = [item for item in checks.values() if item["status"] in {"unreachable", "invalid_url"}]
    if failed:
        response.status_code = 503
    return {"ok": not failed, "status": "ready" if not failed else "not_ready", "checks": checks}


def _store_call(fn, *args, **kwargs):
    """任务库调用包装：失败转结构化 503（不让裸异常变成 500）。"""
    try:
        return fn(*args, **kwargs)
    except Exception as exc:  # noqa: BLE001
        raise http_error(
            "persistence_unavailable",
            f"任务库不可用：{type(exc).__name__}: {exc}"[:200],
        ) from exc


def _iso(value: Optional[datetime]) -> Optional[str]:
    return value.isoformat(timespec="seconds") if value else None


def _snapshot_from_store(run_id: str) -> Optional[dict]:
    """任务库行 → 与内存画像同形的快照（P2-C：进程重启后仍可查询）。"""
    row = _store_call(store.get_run, run_id)
    if row is None:
        return None
    active = row["status"] in ACTIVE_STATUSES
    started = row.get("started_at") or row["created_at"]
    ended = row.get("finished_at") or datetime.now(UTC)
    elapsed = max(0.0, (ended - started).total_seconds()) if started else 0.0
    timeout_seconds = 0
    if row.get("timeout_at") and row.get("created_at"):
        timeout_seconds = int((row["timeout_at"] - row["created_at"]).total_seconds())
    return {
        "run_id": row["run_id"],
        "topic": row["topic"],
        "status": "running" if active else "finished",
        "persisted": True,
        "started_at": _iso(started),
        "timeout_seconds": timeout_seconds,
        "stop_reason": row.get("stop_reason"),
        "cancelled": row.get("stop_reason") in ("cancelled", "user_cancelled"),
        "run_status": row.get("research_status"),
        "token_used": row.get("token_used") or 0,
        "cost_estimate_cny": float(row.get("cost_estimate_cny") or 0.0),
        "degradation_count": 0,
        "depth": 0,
        "findings_count": 0,
        "event_count": _store_call(store.count_events, run_id),
        "last_event_type": None,
        "has_report": _store_call(store.has_artifact, run_id, "report_md"),
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(max(0.0, timeout_seconds - elapsed), 1) if active else 0.0,
        "worker_status": row.get("worker_status"),
    }


def _run_brief(row: dict) -> dict:
    return {
        "run_id": row["run_id"],
        "topic": row["topic"],
        "status": row["status"],
        "stop_reason": row.get("stop_reason"),
        "created_at": _iso(row["created_at"]),
        "started_at": _iso(row.get("started_at")),
        "finished_at": _iso(row.get("finished_at")),
        "token_used": row.get("token_used") or 0,
        "cost_estimate_cny": float(row.get("cost_estimate_cny") or 0.0),
        "has_report": bool(row.get("has_report")),
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
            req.search_provider, req.enable_arxiv, req.max_subquestions,
            req.idempotency_key)
    except ApiError as exc:  # 并发上限（P1-3）/ 持久化不可用（P2-C）等运行器侧拒绝
        raise exc.to_http() from exc
    return StartResponse(run_id=run_id)


@app.get("/api/research/{run_id}")
def run_status(run_id: str) -> dict:
    """运行画像（P1-4）：内存优先；不在内存时回落任务库（P2-C，进程重启后仍可查）。

    与 SSE 互补：SSE 是**增量**流，断了就靠 `Last-Event-ID` 续；本接口是**快照**，
    供页面刷新 / 新标签页直接问一句「还活着吗、跑到哪了」，不必重开一条流。
    """
    snap = manager.snapshot(run_id)
    if snap is None and store is not None:
        snap = _snapshot_from_store(run_id)
    if snap is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    return snap


@app.get("/api/runs")
def list_runs(
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="按状态过滤（逗号分隔，如 RUNNING,FAILED）"),
) -> dict:
    """历史任务列表（P2-C）：读任务库；未配置库时结构化 503。"""
    if store is None:
        raise http_error(
            "persistence_unavailable",
            "未配置任务库（DR_DATABASE_URL），无法列出历史任务",
        )
    statuses = [item.strip() for item in status.split(",") if item.strip()] if status else None
    rows = _store_call(store.list_runs, limit=limit, offset=offset, statuses=statuses)
    return {"runs": [_run_brief(row) for row in rows], "limit": limit, "offset": offset}


@app.post("/api/research/{run_id}/cancel")
def cancel(run_id: str) -> dict:
    """立即返回（契约 C1：前端点取消后不必等后端确认就显示「正在停止」）。

    内存中没有该 run 时回落任务库（P2-C）：只落取消请求，执行者已不在 ⇒ 无实际执行可停。
    """
    if not manager.cancel(run_id):
        if store is None or _store_call(store.get_run, run_id) is None:
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
        _store_call(store.request_cancel, run_id)
    return {"ok": True}


def _export_from_store(run_id: str, fmt: str, row: dict) -> Response:
    """任务库导出回落（P2-C）：进程重启后仍可下载历史报告。"""
    if not row.get("finished_at"):
        raise http_error("report_not_ready", "研究尚未结束，暂无报告可导出")
    if fmt == "json":
        body = _store_call(store.get_artifact, run_id, "export_json")
        if not body:
            raise http_error("report_unavailable", "本次运行没有产出报告")
        return JSONResponse(json.loads(body))
    body = _store_call(store.get_artifact, run_id, "report_md")
    if not body:
        raise http_error("report_unavailable", "本次运行没有产出报告")
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="deepresearch-{run_id}.md"'},
    )


@app.get("/api/research/{run_id}/report")
def export_report(run_id: str, fmt: str = Query("md", alias="format", pattern="^(md|json)$")) -> Response:
    """报告导出（P1-6）：`format=md` 下载 Markdown，`format=json` 取结构化载荷。

    为什么走后端而不沿用前端 Blob：前端那份只有 `result.report` 正文，
    导出的文件脱离页面后无从自证来源；后端版本带 run_id / run_status /
    降级条数等审计元数据与引用清单。P2-C：内存未命中时回落任务库产物。
    """
    if not manager.exists(run_id):
        if store is None:
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
        row = _store_call(store.get_run, run_id)
        if row is None:
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
        return _export_from_store(run_id, fmt, row)
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
    """SSE 事件流（AG-UI 语义）。

    内存未命中但任务库有该 run（P2-C，进程重启后）⇒ 按存储事件回放一遍后收口，
    不做实时尾随（旧进程已不存在，任务在启动时已被标记 LOST/终局）。
    """
    resume_after = last_event_id_header if last_event_id_header is not None else last_event_id
    if not manager.exists(run_id):
        if store is None:
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
        row = _store_call(store.get_run, run_id)
        if row is None:
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
        return StreamingResponse(
            _stored_event_gen(run_id, resume_after),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )
    return StreamingResponse(
        _event_gen(run_id, resume_after),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # 关掉 nginx 缓冲，否则长连接会被攒着不推
        },
    )


async def _stored_event_gen(run_id: str, last_event_id: Optional[int]) -> AsyncIterator[str]:
    """任务库回放：按 `sequence` 原样重发历史帧（帧号 = 存储序号，与内存态一致）。"""
    cursor = last_event_id if last_event_id is not None else -1
    try:
        events = store.get_events(run_id, after=cursor)
    except Exception:  # noqa: BLE001 —— 响应已开始，无法再转 503；直接收口
        return
    for event in events:
        yield sse_frame(
            event_id=event["sequence"],
            event_type=event["event_type"],
            payload=event["payload"] or {},
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
