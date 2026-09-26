"""FastAPI 应用装配（需求 9 §7.4 最小骨架）。

**范围封顶（ADR-0001 §1.1 + D-20 三条硬边界）**：
只做呈现层与传输层。不做登录、用户系统、任务队列、多租户、权限、云端部署。
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import socket
import tempfile
import time
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import config
from research_engine.search.base import KNOWN_PROVIDERS

from .agui import HEARTBEAT_FRAME, HEARTBEAT_SECONDS, sse_frame
from .auth import (
    CSRF_COOKIE,
    CSRF_HEADER,
    MIN_PASSWORD_LENGTH,
    SESSION_COOKIE,
    hash_password,
    is_valid_email,
    new_token,
    normalize_email,
    token_hash,
    verify_password,
)
from .errors import ApiError, error_payload, http_error
from .queue import RunQueue
from .ratelimit import FixedWindowLimiter
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


def _env_flag(name: str, default: str = "false") -> bool:
    return (os.getenv(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def _env_number(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


# P4-A：鉴权与邀请制。默认全关（本地开发 / E2E 零变化）；staging/生产由环境变量打开。
AUTH_REQUIRED = _env_flag("DR_AUTH_REQUIRED", "false")
INVITE_ONLY = _env_flag("DR_INVITE_ONLY", "true")
COOKIE_SECURE = _env_flag("DR_COOKIE_SECURE", "false")
SESSION_TTL_SECONDS = int(os.getenv("DR_SESSION_TTL_SECONDS", "604800"))

# P4-B：配额与限流（推荐基线 v2 默认值；0 = 关闭对应闸）。
MAX_USER_CONCURRENT = max(1, int(_env_number("DR_MAX_USER_CONCURRENT", 1)))
DAILY_RUNS_PER_USER = int(_env_number("DR_DAILY_RUNS_PER_USER", 1))
RUN_BUDGET_CNY = _env_number("DR_RUN_BUDGET_CNY", 1.50)
MONTHLY_BUDGET_CNY = _env_number("DR_MONTHLY_BUDGET_CNY", 1500.0)
LOGIN_LIMITER = FixedWindowLimiter(int(_env_number("DR_LOGIN_RATE_PER_MINUTE", 10)))
SUBMIT_LIMITER = FixedWindowLimiter(int(_env_number("DR_SUBMIT_RATE_PER_MINUTE", 10)))

# P6-A：RAG 上传限制（文件类型白名单 + 单文件大小上限）
RAG_MAX_UPLOAD_MB = _env_number("DR_RAG_MAX_FILE_MB", 10.0)
RAG_ALLOWED_EXT = {".pdf", ".docx", ".md", ".markdown", ".txt", ".text"}


# P2-C：任务库（PostgreSQL）。演示模式不接库，保证 E2E / 本地 UI 演示零依赖。
store = None if DEMO_MODE else _make_store()
if store is not None:
    try:
        # 单实例内存态执行的既有事实：进程重启后，库里非终局的任务已无人执行 ⇒ 标记 LOST。
        store.mark_stale_as_lost()
        # 顺手清理过期会话（读取侧已按 expires_at 校验，此处只是存储卫生）。
        store.purge_expired_sessions()
    except Exception:  # noqa: BLE001 —— 库不可用时由 readiness 与请求侧结构化错误表达
        pass

# P3：执行模式。`inprocess` = 请求进程内线程执行（P1/P2 行为，默认，本地开发）；
# `queue` = 创建 QUEUED 任务入 Redis 队列，由独立 Worker 执行（staging/生产）。
# 队列模式要求同时配置任务库与 Redis —— 配错直接启动失败，不做静默回退。
_EXECUTION_MODE = (os.getenv("DR_EXECUTION_MODE") or "inprocess").strip().lower()
if _EXECUTION_MODE not in {"inprocess", "queue"}:
    raise RuntimeError(f"DR_EXECUTION_MODE 仅支持 inprocess|queue，收到：{_EXECUTION_MODE!r}")
EXECUTION_MODE = "inprocess" if DEMO_MODE else _EXECUTION_MODE

queue: Optional[RunQueue] = None
if EXECUTION_MODE == "queue":
    _redis_url = (os.getenv("DR_REDIS_URL") or "").strip()
    if store is None or not _redis_url:
        raise RuntimeError("DR_EXECUTION_MODE=queue 需要同时配置 DR_DATABASE_URL 与 DR_REDIS_URL")
    queue = RunQueue(_redis_url)

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
        # P4-A / P6-A：前端据此决定是否展示登录页（未开启鉴权时保持匿名可用）
        "auth_required": AUTH_REQUIRED,
        "invite_only": INVITE_ONLY,
    }


def _queue_depth() -> Optional[int]:
    if queue is None:
        return None
    try:
        return queue.depth()
    except Exception:  # noqa: BLE001 —— 健康接口不因队列抖动而失败
        return None


@app.get("/api/health")
def health() -> dict:
    return {
        "ok": True,
        "version": "w9",
        "demo_mode": DEMO_MODE,
        "persistence": store is not None,
        "execution_mode": EXECUTION_MODE,
        "queue_depth": _queue_depth(),
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
        "degradation_count": _store_call(store.count_events, run_id, "DEGRADATION"),
        "depth": 0,
        "findings_count": 0,
        "event_count": _store_call(store.count_events, run_id),
        "last_event_type": _store_call(store.last_event_type, run_id),
        "has_report": _store_call(store.has_artifact, run_id, "report_md"),
        "elapsed_seconds": round(elapsed, 1),
        "remaining_seconds": round(max(0.0, timeout_seconds - elapsed), 1),
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


# ------------------------------------------------------------------ 鉴权（P4-A）


def _session_user(request: Request) -> Optional[dict]:
    if store is None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    return _store_call(store.get_session_user, token_hash(token))


def _require_user(request: Request) -> Optional[str]:
    """返回当前 user_id；未启用鉴权时返回 None（匿名模式，行为与 P3 一致）。"""
    if not AUTH_REQUIRED:
        return None
    user = _session_user(request)
    if user is None:
        raise http_error("unauthenticated", "请先登录")
    return user["user_id"]


def _check_csrf(request: Request) -> None:
    """双提交 Cookie 校验（仅在启用鉴权后生效；登录/注册除外）。"""
    if not AUTH_REQUIRED:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise http_error("csrf_failed", "CSRF 校验失败：缺少或错误的 X-CSRF-Token")


def _run_owner(run_id: str) -> tuple[bool, Optional[str]]:
    """(是否存在, 所有者 user_id)：内存优先，回落任务库。"""
    if manager.exists(run_id):
        return True, manager.owner(run_id)
    if store is not None:
        row = _store_call(store.get_run, run_id)
        if row is not None:
            return True, row.get("user_id")
    return False, None


def _authorize_run(request: Request, run_id: str) -> Optional[str]:
    """存在性 + 归属校验。鉴权开启时，非本人一律 404（不泄露存在性）。"""
    exists, owner = _run_owner(run_id)
    if not exists:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    if AUTH_REQUIRED:
        user_id = _require_user(request)
        if owner != user_id:
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    return owner


def _public_user(user: dict) -> dict:
    return {"user_id": user["user_id"], "email": user["email"]}


def _set_session_cookies(response: Response, user_id: str) -> None:
    """建会话 + 写 Cookie：session 为 httpOnly，CSRF 为可读双提交 Cookie。"""
    token = new_token()
    _store_call(store.create_session, token_hash(token), user_id,
                datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS))
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True,
                        samesite="lax", secure=COOKIE_SECURE, path="/")
    response.set_cookie(CSRF_COOKIE, new_token(), max_age=SESSION_TTL_SECONDS, httponly=False,
                        samesite="lax", secure=COOKIE_SECURE, path="/")


# ------------------------------------------------------------------ 配额与限流（P4-B）


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _day_start_utc() -> datetime:
    return datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)


def _enforce_quotas(user_id: Optional[str]) -> None:
    """配额闸：全局月度预算 → 单用户并发 → 单用户每日次数（任一超限即 429）。"""
    if store is None:
        return
    if MONTHLY_BUDGET_CNY > 0:
        spent = _store_call(store.month_cost_cny)
        if spent >= MONTHLY_BUDGET_CNY:
            raise http_error(
                "quota_exceeded",
                "本月全局预算已用尽，已暂停新建任务（查询 / 导出不受影响）",
                detail=f"spent={spent:.4f}; limit={MONTHLY_BUDGET_CNY}",
            )
    if user_id is None:
        return
    active = _store_call(store.count_active, user_id)
    if active >= MAX_USER_CONCURRENT:
        raise http_error(
            "quota_exceeded", "你有正在运行的任务（单用户并发上限）",
            detail=f"active={active}; limit={MAX_USER_CONCURRENT}",
        )
    if DAILY_RUNS_PER_USER > 0:
        used = _store_call(store.count_user_runs_since, user_id, _day_start_utc())
        if used >= DAILY_RUNS_PER_USER:
            raise http_error(
                "quota_exceeded", "今日运行次数已达上限",
                detail=f"used={used}; limit={DAILY_RUNS_PER_USER}",
            )


def _start_queued(req: StartRequest, user_id: Optional[str]) -> str:
    """队列模式（P3）：创建 `QUEUED` 任务并投递 Redis 队列；重复幂等键返回既有 run_id。

    幂等命中先于并发检查 —— 重复提交是同一个逻辑请求，不应被并发闸拒绝。
    """
    if store is None or queue is None:
        raise ApiError("persistence_unavailable", "队列模式需要任务库与 Redis 均已配置")
    if req.idempotency_key is not None:
        existing = _store_call(store.get_run_by_idempotency, user_id, req.idempotency_key)
        if existing is not None:
            return existing["run_id"]
    active = _store_call(store.count_active)
    limit = manager.max_concurrent_runs
    if active >= limit:
        raise ApiError(
            "concurrency_limit",
            f"已有 {active} 个研究在运行，上限 {limit}",
            detail=f"active={active}; limit={limit}",
        )
    run_id = uuid.uuid4().hex[:12]
    try:
        row, created = store.create_run(
            run_id, req.topic,
            {
                "instructions": req.instructions,
                "max_total_hops": req.max_total_hops,
                "search_provider": req.search_provider,
                "enable_arxiv": req.enable_arxiv,
                "max_subquestions": req.max_subquestions,
            },
            user_id=user_id,
            idempotency_key=req.idempotency_key,
            status="QUEUED",
            timeout_at=datetime.now(UTC) + timedelta(seconds=manager.run_timeout_seconds),
            budget_limit_cny=RUN_BUDGET_CNY or None,
        )
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            "persistence_unavailable",
            f"任务创建失败：{type(exc).__name__}: {exc}"[:300],
        ) from exc
    if not created:
        return row["run_id"]
    try:
        queue.enqueue(row["run_id"])
    except Exception as exc:  # noqa: BLE001 —— 入队失败必须落终局，不留永久 QUEUED
        try:
            store.update_status(row["run_id"], "FAILED", allowed_from=("QUEUED",),
                                stop_reason="error", finished_at=datetime.now(UTC))
        except Exception:  # noqa: BLE001
            pass
        raise ApiError(
            "persistence_unavailable",
            f"任务入队失败：{type(exc).__name__}",
        ) from exc
    return row["run_id"]


class RegisterRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)
    invite_code: Optional[str] = Field(None, max_length=128, description="邀请制下必填")


class LoginRequest(BaseModel):
    email: str = Field(..., max_length=254)
    password: str = Field(..., max_length=200)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(..., max_length=200)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)


@app.post("/api/auth/register")
def auth_register(req: RegisterRequest, request: Request, response: Response) -> dict:
    """邀请制注册（`DR_INVITE_ONLY=true` 时邀请码必填）；成功后自动登录。"""
    if not LOGIN_LIMITER.allow(f"register:{_client_key(request)}"):
        raise http_error("rate_limited", "注册请求过于频繁，稍后再试")
    if store is None:
        raise http_error("persistence_unavailable", "账号功能需要任务库（DR_DATABASE_URL）")
    email = normalize_email(req.email)
    if not is_valid_email(email):
        raise http_error("invalid_request", "邮箱格式不合法")
    user_id = uuid.uuid4().hex[:12]
    try:
        if INVITE_ONLY:
            if not req.invite_code:
                raise ValueError("invite_invalid")
            user = store.register_with_invite(
                user_id, email, hash_password(req.password), token_hash(req.invite_code.strip()))
        else:
            user = store.create_user(user_id, email, hash_password(req.password))
    except ValueError as exc:
        raise http_error("invite_invalid", "邀请码无效、已使用或已过期") from exc
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "sqlstate", None) == "23505":
            raise http_error("email_taken", "该邮箱已注册") from exc
        raise http_error(
            "persistence_unavailable",
            f"注册失败：{type(exc).__name__}: {exc}"[:200],
        ) from exc
    _store_call(store.touch_last_login, user["user_id"])
    _set_session_cookies(response, user["user_id"])
    return {"user": _public_user(user)}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, response: Response) -> dict:
    """邮箱 + 密码登录；失败统一 401（不区分「用户不存在 / 密码错 / 已封禁」）。"""
    if not LOGIN_LIMITER.allow(f"login:{_client_key(request)}"):
        raise http_error("rate_limited", "登录请求过于频繁，稍后再试")
    if store is None:
        raise http_error("persistence_unavailable", "账号功能需要任务库（DR_DATABASE_URL）")
    user = _store_call(store.get_user_by_email, normalize_email(req.email))
    if (user is None or user["status"] != "active"
            or not verify_password(user["password_hash"], req.password)):
        raise http_error("invalid_credentials", "邮箱或密码不正确")
    _store_call(store.touch_last_login, user["user_id"])
    _set_session_cookies(response, user["user_id"])
    return {"user": _public_user(user)}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    token = request.cookies.get(SESSION_COOKIE)
    if token and store is not None:
        _store_call(store.revoke_session, token_hash(token))
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/session")
def auth_session(request: Request) -> dict:
    user = _session_user(request)
    if user is None:
        raise http_error("unauthenticated", "未登录")
    return {"user": _public_user(user)}


@app.post("/api/auth/password")
def auth_change_password(req: ChangePasswordRequest, request: Request, response: Response) -> dict:
    """登录态改密：校验当前密码 → 更新 Argon2id → **吊销全部会话** → 当前设备重新签发。

    其它设备的会话一并失效（改密的预期安全行为）。
    """
    if store is None:
        raise http_error("persistence_unavailable", "账号功能需要任务库（DR_DATABASE_URL）")
    user = _session_user(request)
    if user is None:
        raise http_error("unauthenticated", "请先登录")
    _check_csrf(request)
    if not verify_password(user["password_hash"], req.current_password):
        raise http_error("invalid_credentials", "当前密码不正确")
    _store_call(store.update_password, user["user_id"], hash_password(req.new_password))
    _store_call(store.revoke_user_sessions, user["user_id"])
    _set_session_cookies(response, user["user_id"])
    return {"ok": True}


@app.post("/api/research", response_model=StartResponse)
def start(req: StartRequest, request: Request) -> StartResponse:
    user_id = _require_user(request)
    _check_csrf(request)
    if not SUBMIT_LIMITER.allow(f"submit:{user_id or _client_key(request)}"):
        raise http_error("rate_limited", "提交过于频繁，稍后再试")
    # 幂等命中先于配额闸：重复提交是同一个逻辑请求，不应被日限额/预算拒绝。
    if store is not None and req.idempotency_key is not None:
        existing = _store_call(store.get_run_by_idempotency, user_id, req.idempotency_key)
        if existing is not None:
            return StartResponse(run_id=existing["run_id"])
    _enforce_quotas(user_id)
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
        if EXECUTION_MODE == "queue":
            run_id = _start_queued(req, user_id)
        else:
            run_id = manager.start(
                req.topic, req.instructions, req.max_total_hops,
                req.search_provider, req.enable_arxiv, req.max_subquestions,
                req.idempotency_key, user_id=user_id,
                budget_limit_cny=RUN_BUDGET_CNY or None)
    except ApiError as exc:  # 并发上限 / 持久化不可用等运行器侧拒绝
        raise exc.to_http() from exc
    return StartResponse(run_id=run_id)


@app.get("/api/research/{run_id}")
def run_status(run_id: str, request: Request) -> dict:
    """运行画像（P1-4）：内存优先；不在内存时回落任务库（P2-C，进程重启后仍可查）。

    与 SSE 互补：SSE 是**增量**流，断了就靠 `Last-Event-ID` 续；本接口是**快照**，
    供页面刷新 / 新标签页直接问一句「还活着吗、跑到哪了」，不必重开一条流。
    P4-A：鉴权开启时只允许查看自己的 run（非本人 404，不泄露存在性）。
    """
    _authorize_run(request, run_id)
    snap = manager.snapshot(run_id)
    if snap is None and store is not None:
        snap = _snapshot_from_store(run_id)
    if snap is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    return snap


@app.get("/api/runs")
def list_runs(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    status: Optional[str] = Query(None, description="按状态过滤（逗号分隔，如 RUNNING,FAILED）"),
) -> dict:
    """历史任务列表（P2-C）：读任务库；未配置库时结构化 503。

    P4-A：鉴权开启时只返回当前用户的 run。
    """
    if store is None:
        raise http_error(
            "persistence_unavailable",
            "未配置任务库（DR_DATABASE_URL），无法列出历史任务",
        )
    statuses = [item.strip() for item in status.split(",") if item.strip()] if status else None
    rows = _store_call(store.list_runs, user_id=_require_user(request),
                       limit=limit, offset=offset, statuses=statuses)
    return {"runs": [_run_brief(row) for row in rows], "limit": limit, "offset": offset}


@app.get("/api/quota")
def quota(request: Request) -> dict:
    """配额与预算快照（P4-B / §5.10）：当日次数、并发占用、单次与月度预算。"""
    if store is None:
        raise http_error("persistence_unavailable", "未配置任务库（DR_DATABASE_URL）")
    user_id = _require_user(request)
    monthly_used = _store_call(store.month_cost_cny)
    daily_used = (
        _store_call(store.count_user_runs_since, user_id, _day_start_utc())
        if user_id else None
    )
    return {
        "user_id": user_id,
        "daily_runs_used": daily_used,
        "daily_runs_limit": DAILY_RUNS_PER_USER or None,
        "user_active_runs": _store_call(store.count_active, user_id) if user_id else None,
        "user_concurrent_limit": MAX_USER_CONCURRENT,
        "run_budget_cny": RUN_BUDGET_CNY or None,
        "monthly_cost_cny": round(monthly_used, 4),
        "monthly_budget_cny": MONTHLY_BUDGET_CNY or None,
    }


# ------------------------------------------------------------------ RAG 知识库（P6-A）

@app.post("/api/rag/ingest")
async def rag_ingest(request: Request, file: UploadFile = File(...)) -> dict:
    """上传并摄取文档（P6-A）：白名单类型 + 大小上限；**按当前用户打标**（P5 作用域）。

    摄取是 CPU/IO 混合任务（解析 + embedding），放执行器线程跑，避免阻塞事件循环。
    """
    user_id = _require_user(request)
    _check_csrf(request)
    filename = os.path.basename(file.filename or "")
    extension = os.path.splitext(filename)[1].lower()
    if not filename or extension not in RAG_ALLOWED_EXT:
        raise http_error(
            "invalid_request", "不支持的文件类型",
            detail=f"allowed={sorted(RAG_ALLOWED_EXT)}",
        )
    content = await file.read()
    if len(content) > RAG_MAX_UPLOAD_MB * 1024 * 1024:
        raise http_error(
            "invalid_request",
            f"文件超过 {RAG_MAX_UPLOAD_MB:g}MB 上限",
            detail=f"size={len(content)}; max_mb={RAG_MAX_UPLOAD_MB:g}",
        )
    if not content:
        raise http_error("invalid_request", "文件为空")

    from research_engine.rag.ingest import DocumentIngester

    def _run_ingest() -> int:
        ingester = DocumentIngester()
        with tempfile.TemporaryDirectory(prefix="dr-rag-") as tmp_dir:
            path = os.path.join(tmp_dir, filename)
            with open(path, "wb") as handle:
                handle.write(content)
            return ingester.ingest_file(path, doc_id=f"{user_id or 'local'}:{filename}",
                                        user_id=user_id)

    try:
        chunks = await asyncio.get_running_loop().run_in_executor(None, _run_ingest)
    except Exception as exc:  # noqa: BLE001 —— embedding / 解析 / Qdrant 故障统一结构化
        raise http_error(
            "rag_ingest_failed",
            f"文档摄取失败：{type(exc).__name__}: {exc}"[:200],
        ) from exc
    if not chunks:
        raise http_error("rag_ingest_failed", "文档未解析出任何内容")
    return {"doc_id": f"{user_id or 'local'}:{filename}", "source": filename, "chunks": chunks}


@app.get("/api/rag/docs")
def rag_docs(request: Request) -> dict:
    """当前作用域可见的知识库文档清单（P6-A；按 P5 作用域过滤，不泄露他人文档）。"""
    user_id = _require_user(request)
    from research_engine.rag.scope import RagScope
    from research_engine.rag.store import VectorStore

    vector_store = VectorStore()
    reason = vector_store.unavailable_reason
    if reason:
        raise http_error(
            "rag_unavailable",
            "知识库当前不可用（Qdrant 未配置或连不上）",
            detail=vector_store.last_error or reason,
        )
    counts: dict[str, int] = {}
    for payload in vector_store.scroll_all(scope=RagScope(user_id=user_id)):
        source = payload.get("source") or payload.get("doc_id") or "(未命名)"
        counts[source] = counts.get(source, 0) + 1
    return {"docs": [{"source": name, "chunks": count} for name, count in sorted(counts.items())]}


@app.post("/api/research/{run_id}/cancel")
def cancel(run_id: str, request: Request) -> dict:
    """立即返回（契约 C1：前端点取消后不必等后端确认就显示「正在停止」）。

    内存中没有该 run 时回落任务库（P2-C）：只落取消请求，执行者已不在 ⇒ 无实际执行可停。
    P4-A：鉴权开启时只允许取消自己的 run。
    """
    _authorize_run(request, run_id)
    _check_csrf(request)
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
def export_report(run_id: str, request: Request,
                  fmt: str = Query("md", alias="format", pattern="^(md|json)$")) -> Response:
    """报告导出（P1-6）：`format=md` 下载 Markdown，`format=json` 取结构化载荷。

    为什么走后端而不沿用前端 Blob：前端那份只有 `result.report` 正文，
    导出的文件脱离页面后无从自证来源；后端版本带 run_id / run_status /
    降级条数等审计元数据与引用清单。P2-C：内存未命中时回落任务库产物。
    P4-A：鉴权开启时只允许导出自己的 run。
    """
    _authorize_run(request, run_id)
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
    request: Request,
    last_event_id: Optional[int] = Query(None),
    last_event_id_header: Optional[int] = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """SSE 事件流（AG-UI 语义）。

    内存未命中但任务库有该 run（P2-C，进程重启后）⇒ 从任务库回放并实时尾随。
    P4-A：鉴权开启时只允许订阅自己的 run。
    """
    _authorize_run(request, run_id)
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
    """任务库实时尾随（P2-C 一次回放 → P3 轮询尾随）。

    先按 `sequence` 补发历史，再每秒轮询新增事件，直到 run 终局；期间每 15s 发心跳注释帧。
    L3-A 规模下轮询足够简单可靠，暂不引入 Redis 订阅（queue 只做任务分发）。
    """
    cursor = last_event_id if last_event_id is not None else -1
    last_beat = time.monotonic()
    while True:
        try:
            events = store.get_events(run_id, after=cursor)
            row = store.get_run(run_id)
        except Exception:  # noqa: BLE001 —— 响应已开始，无法再转 503；直接收口
            return
        if row is None:
            return
        for event in events:
            cursor = event["sequence"]
            yield sse_frame(
                event_id=cursor,
                event_type=event["event_type"],
                payload=event["payload"] or {},
            )
        if row["status"] not in ACTIVE_STATUSES:
            return
        if events:
            continue  # 有新增就立即追平，不额外等一秒
        now = time.monotonic()
        if now - last_beat >= HEARTBEAT_SECONDS:
            last_beat = now
            yield HEARTBEAT_FRAME
        await asyncio.sleep(1.0)


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
