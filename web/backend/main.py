"""FastAPI 应用装配（需求 9 §7.4 最小骨架）。

**范围封顶（ADR-0001 §1.1 + D-20 三条硬边界）**：
只做呈现层与传输层。不做登录、用户系统、任务队列、多租户、权限、云端部署。
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import shutil
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
from .metrics import METRICS
from .moderation import (
    MAX_APPEAL_LENGTH,
    MAX_INSTRUCTIONS_LENGTH,
    MAX_TOPIC_LENGTH,
    scan,
)
from .profiles import DEFAULT_PROFILE, PROFILES, profile_options, resolve_profile
from .queue import RunQueue
from .ratelimit import FixedWindowLimiter
from .runner import RunManager
from .store import ACTIVE_STATUSES, QuotaExceeded, RunStore
from .upload_guard import (
    ALLOWED_EXT,
    UploadRejected,
    detect_and_validate,
    extension_of,
    sanitize_filename,
    stream_to_temp,
)

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


@app.middleware("http")
async def _metrics_middleware(request: Request, call_next):
    """P8-A：HTTP 状态分类计数 + 全局延迟累计（进程内，重启清零）。"""
    started = time.perf_counter()
    response = await call_next(request)
    METRICS.inc(f"http_{response.status_code // 100}xx")
    METRICS.observe_latency_ms((time.perf_counter() - started) * 1000)
    return response


@app.middleware("http")
async def _https_enforcement(request: Request, call_next):
    """P0-9：production 环境拒绝非 HTTPS 请求（信任反向代理透传的 X-Forwarded-Proto）。

    本地 / staging 不拦（staging 由启动硬校验保证 Cookie Secure 等配置）。
    """
    if ENV == "production":
        proto = request.headers.get("x-forwarded-proto") or request.url.scheme
        if proto.split(",")[0].strip().lower() != "https":
            return JSONResponse(
                status_code=400,
                content={"detail": error_payload(
                    "https_required", "生产环境仅接受 HTTPS 请求",
                    detail=f"scheme={proto}")},
            )
    return await call_next(request)

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


def _parse_env() -> str:
    """运行环境（P0-9）：local（默认，宽松）/ staging / production（启动硬校验）。"""
    env = (os.getenv("DR_ENV") or "local").strip().lower()
    if env not in {"local", "staging", "production"}:
        raise RuntimeError(f"DR_ENV 仅支持 local|staging|production，收到：{env!r}")
    return env


ENV = _parse_env()


def _validate_runtime_config() -> None:
    """P0-9 启动硬校验：staging / production 不允许带不安全默认启动（fail fast）。

    校验项：鉴权开关、Secure Cookie、CORS 显式来源、LLM 密钥。
    本地（`DR_ENV=local`，含 CI / E2E / 单测）不校验，行为与历史一致。
    """
    if ENV == "local":
        return
    problems: list[str] = []
    if not AUTH_REQUIRED:
        problems.append("DR_AUTH_REQUIRED 必须为 true（邀请制/账号体系是准入前提）")
    if not COOKIE_SECURE:
        problems.append("DR_COOKIE_SECURE 必须为 true（HTTPS 下会话 Cookie 才安全）")
    if not (os.getenv("DR_CORS_ORIGINS") or "").strip():
        problems.append("DR_CORS_ORIGINS 必须显式配置（禁止沿用 localhost 默认）")
    if not DEMO_MODE and not config.llm.api_key:
        problems.append("DASHSCOPE_API_KEY 缺失（研究链路无法运行）")
    if problems:
        raise RuntimeError(
            f"启动自检失败（DR_ENV={ENV}）：" + "；".join(problems)
            + "。确认配置后重启；本地开发可设 DR_ENV=local。"
        )


_validate_runtime_config()

# P4-B：配额与限流（推荐基线 v2 默认值；0 = 关闭对应闸）。
MAX_USER_CONCURRENT = max(1, int(_env_number("DR_MAX_USER_CONCURRENT", 1)))
DAILY_RUNS_PER_USER = int(_env_number("DR_DAILY_RUNS_PER_USER", 1))
RUN_BUDGET_CNY = _env_number("DR_RUN_BUDGET_CNY", 1.50)
MONTHLY_BUDGET_CNY = _env_number("DR_MONTHLY_BUDGET_CNY", 1500.0)
LOGIN_LIMITER = FixedWindowLimiter(int(_env_number("DR_LOGIN_RATE_PER_MINUTE", 10)))
SUBMIT_LIMITER = FixedWindowLimiter(int(_env_number("DR_SUBMIT_RATE_PER_MINUTE", 10)))

# P6-A / P0-8a：RAG 上传限制（流式落盘 + magic bytes 三重校验 + 解析限额）
RAG_MAX_UPLOAD_MB = _env_number("DR_RAG_MAX_FILE_MB", 10.0)
RAG_UPLOAD_LIMITER = FixedWindowLimiter(int(_env_number("DR_RAG_UPLOADS_PER_MINUTE", 10)))

# P8-A：告警判定阈值（触达渠道由部署方接 IM/邮件；此处只做“可判定”）
ALERT_5XX_RATE_PCT = _env_number("DR_ALERT_5XX_RATE_PCT", 2.0)
ALERT_QUEUE_DEPTH = int(_env_number("DR_ALERT_QUEUE_DEPTH", 20))
ALERT_STALE_RUNS = int(_env_number("DR_ALERT_STALE_RUNS", 1))
ALERT_MONTHLY_PCT = _env_number("DR_ALERT_MONTHLY_PCT", 80.0)

# P7-A：合规文本（隐私政策 / 用户协议）以仓库文档为唯一来源
LEGAL_DIR = Path(__file__).resolve().parents[2] / "docs" / "legal"


# P3：执行模式。`inprocess` = 请求进程内线程执行（P1/P2 行为，默认，本地开发）；
# `queue` = 创建 QUEUED 任务入 Redis 队列，由独立 Worker 执行（staging/生产）。
# 队列模式要求同时配置任务库与 Redis —— 配错直接启动失败，不做静默回退。
# ⚠️ 必须先于任务库启动维护解析：维护语义取决于「谁在执行」（见 _startup_store_maintenance）。
_EXECUTION_MODE = (os.getenv("DR_EXECUTION_MODE") or "inprocess").strip().lower()
if _EXECUTION_MODE not in {"inprocess", "queue"}:
    raise RuntimeError(f"DR_EXECUTION_MODE 仅支持 inprocess|queue，收到：{_EXECUTION_MODE!r}")
EXECUTION_MODE = "inprocess" if DEMO_MODE else _EXECUTION_MODE


def _startup_store_maintenance(run_store: RunStore, execution_mode: str) -> None:
    """启动时的任务库维护（P2-C / P3-B）。

    - `inprocess`：执行器就是本进程的线程，进程重启后库里非终局任务已无人执行
      ⇒ 标记 `LOST`（否则会永远停在 RUNNING/QUEUED 无人接管）；
    - `queue`：执行权在独立 Worker（租约 + 心跳 + 清扫），API 重启**不得**触碰
      活跃任务 —— 否则会把 Worker 正在执行的 RUNNING 与仍在 Redis 里的 QUEUED
      误判为 `LOST`；接管只属于 Worker 的租约清扫（`sweep_stale_runs`）。
    - 过期会话清理与执行模式无关，两种模式都做（读取侧已按 `expires_at` 校验，
      这里只是存储卫生）。
    """
    if execution_mode == "inprocess":
        run_store.mark_stale_as_lost()
    run_store.purge_expired_sessions()


# P2-C：任务库（PostgreSQL）。演示模式不接库，保证 E2E / 本地 UI 演示零依赖。
store = None if DEMO_MODE else _make_store()
if store is not None:
    try:
        _startup_store_maintenance(store, EXECUTION_MODE)
    except Exception:  # noqa: BLE001 —— 库不可用时由 readiness 与请求侧结构化错误表达
        pass

queue: Optional[RunQueue] = None
if EXECUTION_MODE == "queue":
    if store is None:
        raise RuntimeError("DR_EXECUTION_MODE=queue 需要配置 DR_DATABASE_URL（P0-2：派发权威在任务库）")
    _redis_url = (os.getenv("DR_REDIS_URL") or "").strip()
    if _redis_url:
        queue = RunQueue(_redis_url)  # 可选：仅作唤醒信号（丢失不影响正确性）

if DEMO_MODE:
    from .demo_graph import DemoGraph

    # 演示节奏可调：E2E 用 DR_DEMO_STEP_SECONDS 把单节点压到零点几秒，
    # 否则 11 个节点 × 1.8s 的演示会让每条用例都等 20s（且更容易互相撞并发闸）。
    _DEMO_STEP_SECONDS = float(os.getenv("DR_DEMO_STEP_SECONDS", "1.8"))

    manager = RunManager(graph_factory=lambda: DemoGraph(step_seconds=_DEMO_STEP_SECONDS))
else:
    manager = RunManager(store=store)


class StartRequest(BaseModel):
    topic: str = Field(..., max_length=MAX_TOPIC_LENGTH, description="研究主题")
    instructions: str = Field("", max_length=MAX_INSTRUCTIONS_LENGTH, description="附加要求")
    profile: str = Field(
        DEFAULT_PROFILE, max_length=32,
        description="运行档位：quick | standard（底层参数由服务端档位固定，客户端不可覆盖）")
    # 以下 4 个字段仅作旧客户端兼容：值一律**忽略**（以服务端档位/配置为准），
    # 非空时记入 run 快照 request.ignored_overrides 作为审计留痕（需求 10 §5.6）。
    max_total_hops: Optional[int] = Field(
        None, ge=1, le=50, description="已废弃：由服务端档位固定，传入将被忽略")
    max_subquestions: Optional[int] = Field(
        None, ge=1, le=8, description="已废弃：由服务端档位固定，传入将被忽略")
    search_provider: Optional[str] = Field(
        None, max_length=32, description="已废弃：由服务端配置固定，传入将被忽略")
    enable_arxiv: Optional[bool] = Field(
        None, description="已废弃：由服务端配置固定，传入将被忽略")
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
        # P0 profile 固化：前端只展示档位（底层参数不可提交，请求体同名字段被忽略）
        "profiles": profile_options(),
        "default_profile": DEFAULT_PROFILE,
        # P1-2 / P1-3：把后端**实际生效**的闸值下发给前端，前端才能显示剩余时间
        # 与「已有研究在运行」提示，而不是靠猜。
        "run_timeout_seconds": manager.run_timeout_seconds,
        "max_concurrent_runs": manager.max_concurrent_runs,
        # P4-A / P6-A：前端据此决定是否展示登录页（未开启鉴权时保持匿名可用）
        "auth_required": AUTH_REQUIRED,
        "invite_only": INVITE_ONLY,
    }


def _queue_depth() -> Optional[int]:
    """队列深度：以任务库 QUEUED 计数为准（P0-2）；无库时回落 Redis（仅唤醒信号）。"""
    if store is not None:
        try:
            return int(store.count_queued())
        except Exception:  # noqa: BLE001 —— 健康接口不因库抖动而失败
            return None
    if queue is None:
        return None
    try:
        return queue.depth()
    except Exception:  # noqa: BLE001
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
        "moderation_status": row.get("moderation_status"),
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
        "moderation_status": row.get("moderation_status"),
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


def _enforce_output_policy(run_id: str) -> None:
    """P0-4 统一输出闸：`flagged` / `blocked` 的报告不向普通用户开放查看与导出。

    原文仍保留在 `run_artifacts`，管理员经 CLI（`admin run-report`）复核。
    """
    if store is None:
        return
    row = _store_call(store.get_run, run_id)
    status = row.get("moderation_status") if row else None
    if status in ("flagged", "blocked"):
        raise http_error(
            "output_under_review",
            "报告命中内容安全预检，正在等待人工复核",
            detail=f"moderation_status={status}",
        )


def _redact_report_payload(payload: dict) -> dict:
    """P0-4：修复前落库的终局事件可能带完整正文 —— 回放前强制脱敏。"""
    result = payload.get("result")
    if not isinstance(result, dict) or not result.get("report"):
        return payload
    return {**payload, "result": {**result, "report": ""}, "output_under_review": True}


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


def _ignored_overrides(req: StartRequest) -> dict:
    """旧客户端传入的底层参数：一律忽略；非空值记入 run 快照（审计留痕）。"""
    values = {
        "max_total_hops": req.max_total_hops,
        "max_subquestions": req.max_subquestions,
        "search_provider": req.search_provider,
        "enable_arxiv": req.enable_arxiv,
    }
    return {key: value for key, value in values.items() if value is not None}


def _effective_run_budget(profile) -> float:
    """单次预算 = 档位值，且不超过全局环境闸 `DR_RUN_BUDGET_CNY`（<=0 视为不设闸）。"""
    if RUN_BUDGET_CNY > 0:
        return min(profile.run_budget_cny, RUN_BUDGET_CNY)
    return profile.run_budget_cny


def _quota_api_error(exc: QuotaExceeded) -> ApiError:
    """P0-3：准入失败 → 既有结构化错误码（全局并发 = concurrency_limit，其余 = quota_exceeded）。"""
    if exc.kind == "global_concurrency":
        return ApiError("concurrency_limit", "已有研究在运行，请稍后再试", detail=exc.detail)
    if exc.kind == "monthly_budget":
        return ApiError(
            "quota_exceeded",
            "本月全局预算已用尽，已暂停新建任务（查询 / 导出不受影响）",
            detail=exc.detail,
        )
    if exc.kind == "user_concurrency":
        return ApiError("quota_exceeded", "你有正在运行的任务（单用户并发上限）", detail=exc.detail)
    return ApiError("quota_exceeded", "今日运行次数已达上限", detail=exc.detail)


def _admission_limits() -> dict:
    """当前生效的准入闸（P0-3：由 store 在同一事务内原子执行）。"""
    return {
        "global_active_limit": manager.max_concurrent_runs,
        "user_active_limit": MAX_USER_CONCURRENT,
        "daily_limit": DAILY_RUNS_PER_USER or None,
        "monthly_budget_cny": MONTHLY_BUDGET_CNY or None,
        "daily_since": _day_start_utc(),
    }


def _start_queued(req: StartRequest, user_id: Optional[str], profile, ignored: dict) -> str:
    """队列模式（P3）：创建 `QUEUED` 任务并投递 Redis 队列；重复幂等键返回既有 run_id。

    幂等命中先于并发检查 —— 重复提交是同一个逻辑请求，不应被并发闸拒绝。
    P0：run.request 写**档位快照**（而非客户端参数）；超时 / 预算取自档位。
    """
    if store is None:
        raise ApiError("persistence_unavailable", "队列模式需要任务库（DR_DATABASE_URL）")
    if req.idempotency_key is not None:
        existing = _store_call(store.get_run_by_idempotency, user_id, req.idempotency_key)
        if existing is not None:
            return existing["run_id"]
    run_id = uuid.uuid4().hex[:12]
    try:
        row, created = store.create_run_admitted(
            run_id, req.topic,
            {
                "instructions": req.instructions,
                "profile": profile.snapshot(),
                "ignored_overrides": ignored or None,
            },
            user_id=user_id,
            idempotency_key=req.idempotency_key,
            status="QUEUED",
            timeout_at=datetime.now(UTC) + timedelta(seconds=profile.timeout_seconds),
            budget_limit_cny=_effective_run_budget(profile),
            **_admission_limits(),
        )
    except QuotaExceeded as exc:  # P0-3：准入闸在同一事务内判定
        raise _quota_api_error(exc) from exc
    except Exception as exc:  # noqa: BLE001
        raise ApiError(
            "persistence_unavailable",
            f"任务创建失败：{type(exc).__name__}: {exc}"[:300],
        ) from exc
    if not created:
        return row["run_id"]
    if queue is not None:
        try:
            queue.enqueue(row["run_id"])  # 唤醒信号：丢失由 PG 轮询兜底（P0-2）
        except Exception:  # noqa: BLE001 —— 信号失败不影响任务正确性
            pass
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


class DeleteAccountRequest(BaseModel):
    password: str = Field(..., max_length=200)


@app.delete("/api/auth/account")
def auth_delete_account(req: DeleteAccountRequest, request: Request, response: Response) -> dict:
    """注销账号（P7-A / P0-7）：验密 + CSRF；登记**持久注销请求**并立即删账号与会话。

    任务与审核记录按外键 SET NULL **保留但匿名**（审计需要）；RAG 向量清理改为
    durable outbox：与注销登记同事务写入，由 Worker 带指数退避重试直至验证归零
    （`deletion_request_id` 可经 CLI `deletion-list` 查询；失败会告警，绝不静默）。
    """
    if store is None:
        raise http_error("persistence_unavailable", "账号功能需要任务库（DR_DATABASE_URL）")
    user = _session_user(request)
    if user is None:
        raise http_error("unauthenticated", "请先登录")
    _check_csrf(request)
    if not verify_password(user["password_hash"], req.password):
        raise http_error("invalid_credentials", "密码不正确")
    user_id = user["user_id"]
    request_id = uuid.uuid4().hex[:12]

    try:
        store.request_account_deletion(request_id, user_id)
    except Exception as exc:  # noqa: BLE001 —— 注销登记是硬前提，失败即明确报错
        raise http_error(
            "persistence_unavailable",
            f"注销登记失败：{type(exc).__name__}: {exc}"[:200],
        ) from exc

    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True, "deletion_request_id": request_id, "rag_cleanup": "pending"}


@app.post("/api/research", response_model=StartResponse)
def start(req: StartRequest, request: Request) -> StartResponse:
    user_id = _require_user(request)
    _check_csrf(request)
    if not SUBMIT_LIMITER.allow(f"submit:{user_id or _client_key(request)}"):
        raise http_error("rate_limited", "提交过于频繁，稍后再试")
    # P0 profile 固化：档位由服务端解析；请求体底层参数（旧字段）一律忽略并留痕。
    profile = resolve_profile(req.profile)
    if profile is None:
        raise http_error(
            "invalid_request",
            f"未知运行档位：{req.profile}",
            detail=f"known={sorted(PROFILES)}",
        )
    ignored = _ignored_overrides(req)
    # 幂等命中先于配额闸：重复提交是同一个逻辑请求，不应被日限额/预算拒绝。
    if store is not None and req.idempotency_key is not None:
        existing = _store_call(store.get_run_by_idempotency, user_id, req.idempotency_key)
        if existing is not None:
            return StartResponse(run_id=existing["run_id"])
    _enforce_quotas(user_id)
    if not req.topic.strip():
        raise http_error("empty_topic", "topic 不能为空")
    # P7-A：输入侧预检（规则词表；命中即拒绝并留审核记录，不进入队列/执行）
    blocked_terms = scan(f"{req.topic}\n{req.instructions}")
    if blocked_terms:
        if store is not None:
            _store_call(store.record_moderation, "input_blocked", user_id=user_id,
                        detail={"matches": blocked_terms[:10]})
        raise http_error(
            "content_blocked", "输入包含不允许的内容",
            detail=f"matches={blocked_terms[:5]}",
        )
    # 搜索引擎由服务端配置固定（需求 10 §3.1 第 13 项）；客户端字段已在上面忽略。
    try:
        if EXECUTION_MODE == "queue":
            run_id = _start_queued(req, user_id, profile, ignored)
        else:
            run_id = manager.start(
                req.topic, req.instructions, profile,
                req.idempotency_key, user_id=user_id,
                budget_limit_cny=_effective_run_budget(profile),
                ignored_overrides=ignored,
                admission=_admission_limits())
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


# ------------------------------------------------------------------ 可观测与告警（P8-A）


def _runs_metrics_24h() -> dict:
    if store is None:
        return {}
    counts = _store_call(store.status_counts_since, datetime.now(UTC) - timedelta(hours=24))
    total = sum(counts.values())
    return {
        "window_hours": 24,
        "by_status": counts,
        "total": total,
        "success_rate": round(counts.get("SUCCEEDED", 0) / total, 4) if total else None,
        "failure_rate": round(counts.get("FAILED", 0) / total, 4) if total else None,
        "timeout_rate": round(counts.get("TIMED_OUT", 0) / total, 4) if total else None,
        "lost": counts.get("LOST", 0),
    }


@app.get("/api/metrics")
def metrics_endpoint() -> dict:
    """运行指标（P8-A）：进程内计数 + 任务库实时聚合；**不含任何用户内容 / PII**。

    ⚠️ 生产部署时应由反向代理限制来源（或经统一网关鉴权）；多实例下进程内计数
    只代表单实例（见上线清单 §3.6）。
    """
    snapshot = METRICS.snapshot()
    snapshot.update({
        "persistence": store is not None,
        "execution_mode": EXECUTION_MODE,
        "active_runs": manager.active_runs,
        "queue_depth": _queue_depth(),
        "runs_24h": _runs_metrics_24h(),
        "stale_leases": _store_call(store.count_stale_leases) if store is not None else None,
        "month_cost_cny": round(_store_call(store.month_cost_cny), 4) if store is not None else None,
        "month_budget_cny": MONTHLY_BUDGET_CNY or None,
        # P0-7：注销清理进度（pending/in_progress/completed/abandoned）
        "deletions": (_store_call(store.count_deletions_by_status) if store is not None else None),
    })
    return snapshot


@app.get("/api/ops/alerts")
def ops_alerts() -> dict:
    """告警判定（P8-A）：按阈值评估当前指标；**触达**由部署方接 IM/邮件（P8-B）。"""
    http = METRICS.snapshot()["http"]
    total_http = http["2xx"] + http["4xx"] + http["5xx"]
    rate_5xx = (http["5xx"] / total_http * 100) if total_http else 0.0
    queue_depth = _queue_depth()
    stale = _store_call(store.count_stale_leases) if store is not None else 0
    monthly = _store_call(store.month_cost_cny) if store is not None else 0.0
    monthly_pct = (monthly / MONTHLY_BUDGET_CNY * 100) if MONTHLY_BUDGET_CNY else 0.0
    deletions = _store_call(store.count_deletions_by_status) if store is not None else {}

    alerts: list[dict] = []

    def _check(code: str, severity: str, value: float, threshold: float, message: str) -> None:
        if value >= threshold:
            alerts.append({
                "code": code, "severity": severity,
                "value": round(value, 4), "threshold": threshold, "message": message,
            })

    _check("http_5xx_rate", "high", rate_5xx, ALERT_5XX_RATE_PCT, "5xx 比例超过阈值")
    if queue_depth is not None:
        _check("queue_depth", "medium", queue_depth, ALERT_QUEUE_DEPTH, "队列积压超过阈值")
    _check("stale_leases", "high", stale, ALERT_STALE_RUNS, "存在租约过期未被接管的任务")
    _check("deletion_abandoned", "high", int(deletions.get("abandoned", 0)), 1,
           "存在被放弃的注销清理（外部数据可能残留，需人工介入）")
    if MONTHLY_BUDGET_CNY:
        _check("monthly_budget", "high", monthly_pct, ALERT_MONTHLY_PCT, "月度预算消耗达到阈值")
    return {"ok": True, "alert_count": len(alerts), "alerts": alerts}


# ------------------------------------------------------------------ 内容安全与合规文本（P7-A）


class AppealRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=MAX_APPEAL_LENGTH)
    run_id: Optional[str] = Field(None, max_length=32)


@app.post("/api/moderation/appeal")
def moderation_appeal(req: AppealRequest, request: Request) -> dict:
    """申诉入口（P7-A / P0-5）：只落审核记录供人工复核；不做自动处置。

    带 `run_id` 时（P0-5）：
    - 鉴权开启时校验归属（非本人 404，不泄露存在性）；
    - 只接受被标记（`flagged` / `blocked`）的任务，未标记 ⇒ 409 `appeal_not_applicable`；
    - 同一用户同一任务只允许一次申诉 ⇒ 重复 ⇒ 409 `appeal_duplicate`。
    不带 `run_id` 时为通用申诉（如输入预检异议）。
    """
    if store is None:
        raise http_error("persistence_unavailable", "申诉需要任务库（DR_DATABASE_URL）")
    user_id = _require_user(request)
    _check_csrf(request)
    if req.run_id:
        _authorize_run(request, req.run_id)
        row = _store_call(store.get_run, req.run_id)
        if (row or {}).get("moderation_status") not in ("flagged", "blocked"):
            raise http_error(
                "appeal_not_applicable",
                "该任务未被标记，无需申诉",
                detail=f"run_id={req.run_id}; moderation_status={((row or {}).get('moderation_status'))}",
            )
        if _store_call(store.has_appeal, user_id, req.run_id):
            raise http_error(
                "appeal_duplicate",
                "该任务的申诉已在处理中",
                detail=f"run_id={req.run_id}",
            )
    _store_call(
        store.record_moderation, "appeal", user_id=user_id, run_id=req.run_id,
        detail={"message": req.message[:MAX_APPEAL_LENGTH]},
    )
    return {"ok": True}


@app.get("/api/legal/{doc}")
def legal_document(doc: str) -> dict:
    """隐私政策 / 用户协议（P7-A）：以仓库 `docs/legal/` 为唯一来源。"""
    files = {"privacy": "privacy-policy.md", "terms": "terms-of-service.md"}
    if doc not in files:
        raise http_error("invalid_request", "未知文档", detail=f"known={sorted(files)}")
    path = LEGAL_DIR / files[doc]
    if not path.is_file():
        raise http_error("invalid_request", "文档尚未准备（请联系管理员）")
    return {"doc": doc, "markdown": path.read_text(encoding="utf-8")}


# ------------------------------------------------------------------ RAG 知识库（P6-A）

@app.post("/api/rag/ingest")
async def rag_ingest(request: Request, file: UploadFile = File(...)) -> Response:
    """上传并摄取文档（P6-A / P0-8a / P0-8b）：流式落盘 + 三重校验 + 异步管线。

    - 有任务库（staging/生产）：写入隔离区 + 登记 `rag_ingestions` ⇒ **202**，
      由 Worker 异步解析 / embedding / 写 Qdrant；`GET /api/rag/ingestions/{id}` 查状态；
      相同内容（内容寻址 doc_id）重复上传命中既有记录，不重复处理。
    - 无任务库（本地零依赖）：退化为同步摄取 ⇒ **200**（行为与 P6-A 一致）。
    - 三重校验与解析限额同 P0-8a；隔离区目录由 `DR_RAG_QUARANTINE_DIR` 配置。
    """
    user_id = _require_user(request)
    _check_csrf(request)
    if not RAG_UPLOAD_LIMITER.allow(f"rag:{user_id or _client_key(request)}"):
        raise http_error("rate_limited", "上传过于频繁，稍后再试")

    filename = sanitize_filename(file.filename)
    extension = extension_of(filename)
    if extension not in ALLOWED_EXT:
        raise http_error(
            "unsupported_file_type", "不支持的文件类型",
            detail=f"allowed={sorted(ALLOWED_EXT)}",
        )

    max_bytes = int(RAG_MAX_UPLOAD_MB * 1024 * 1024)
    if store is not None:
        from .ingestion import quarantine_dir

        stored_name = f"{uuid.uuid4().hex}{extension}"
        path = os.path.join(quarantine_dir(), stored_name)
    else:
        stored_name = ""
        tmp_dir = tempfile.mkdtemp(prefix="dr-rag-")
        path = os.path.join(tmp_dir, f"{uuid.uuid4().hex}{extension}")

    try:
        try:
            size, digest, head = await stream_to_temp(file, path, max_bytes)
        except UploadRejected as exc:
            raise http_error(exc.code, exc.message, detail=exc.detail) from exc
        if size == 0:
            raise http_error("invalid_request", "文件为空")
        try:
            detect_and_validate(path, filename, head)
        except UploadRejected as exc:
            raise http_error(exc.code, exc.message, detail=exc.detail) from exc

        doc_id = f"{user_id or 'local'}:{digest[:16]}"
        if store is not None:
            # P0-8b：登记后由 Worker 异步处理；重复内容直接返回既有记录
            existing = _store_call(store.find_ingestion_by_doc, user_id, doc_id)
            if existing is not None:
                os.remove(path)
                return JSONResponse(status_code=202, content={
                    "ingestion_id": existing["ingestion_id"], "doc_id": doc_id,
                    "source": existing["source"], "status": existing["status"],
                })
            ingestion_id = uuid.uuid4().hex[:12]
            try:
                store.create_ingestion(
                    ingestion_id, doc_id, user_id=user_id, source=filename,
                    sha256=digest, size_bytes=size, stored_name=stored_name)
            except Exception as exc:  # noqa: BLE001
                os.remove(path)
                raise http_error(
                    "persistence_unavailable",
                    f"摄取登记失败：{type(exc).__name__}: {exc}"[:200],
                ) from exc
            return JSONResponse(status_code=202, content={
                "ingestion_id": ingestion_id, "doc_id": doc_id,
                "source": filename, "status": "pending",
            })

        # 本地零依赖：同步摄取（P6-A 行为）
        from research_engine.rag.ingest import DocumentIngester, IngestLimitExceeded

        def _run_ingest() -> int:
            return DocumentIngester().ingest_file(path, doc_id=doc_id, user_id=user_id)

        try:
            chunks = await asyncio.get_running_loop().run_in_executor(None, _run_ingest)
        except IngestLimitExceeded as exc:
            raise http_error("document_limit_exceeded", str(exc)[:200]) from exc
        except Exception as exc:  # noqa: BLE001
            raise http_error(
                "rag_ingest_failed",
                f"文档摄取失败：{type(exc).__name__}: {exc}"[:200],
            ) from exc
        if not chunks:
            raise http_error("rag_ingest_failed", "文档未解析出任何内容")
        return JSONResponse(status_code=200, content={
            "doc_id": doc_id, "source": filename, "chunks": chunks,
        })
    finally:
        if store is None:
            shutil.rmtree(tmp_dir, ignore_errors=True)


@app.get("/api/rag/ingestions/{ingestion_id}")
def rag_ingestion_status(ingestion_id: str, request: Request) -> dict:
    """摄取状态（P0-8b）：前端轮询 pending → processing → ready | rejected。"""
    if store is None:
        raise http_error("persistence_unavailable", "摄取状态需要任务库（DR_DATABASE_URL）")
    user_id = _require_user(request)
    row = _store_call(store.get_ingestion, ingestion_id)
    if row is None or (AUTH_REQUIRED and row.get("user_id") != user_id):
        raise http_error("ingestion_not_found", f"摄取记录不存在：{ingestion_id}")
    return {
        "ingestion_id": row["ingestion_id"],
        "doc_id": row["doc_id"],
        "source": row["source"],
        "status": row["status"],
        "chunks": row["chunks"],
        "attempts": row["attempts"],
        "scan_status": row["scan_status"],
        "error": row.get("last_error"),
        "created_at": _iso(row.get("created_at")),
        "processed_at": _iso(row.get("processed_at")),
    }


@app.delete("/api/rag/docs")
def rag_delete_doc(request: Request, doc_id: str = Query(..., max_length=128)) -> dict:
    """删除知识库文档（P0-8b）：Qdrant 按 doc_id 删除并**验证归零**，再清隔离区文件。

    同步删除 + 失败 503（用户在场可重试）；账号注销走 durable outbox（P0-7）。
    """
    if store is None:
        raise http_error("persistence_unavailable", "文档删除需要任务库（DR_DATABASE_URL）")
    user_id = _require_user(request)
    _check_csrf(request)
    if doc_id.split(":", 1)[0] != (user_id or "local"):
        raise http_error("invalid_request", "无权删除该文档", detail="ownership mismatch")

    from research_engine.rag.store import VectorStore

    from .ingestion import quarantine_path

    vector_store = VectorStore()
    reason = vector_store.unavailable_reason
    if reason:
        raise http_error("rag_unavailable", "知识库当前不可用，稍后再试",
                         detail=vector_store.last_error or reason)
    try:
        vector_store.delete_by_doc(doc_id, wait=True)
        remaining = vector_store.count_by_doc(doc_id)
        if remaining:
            raise RuntimeError(f"still has {remaining} points")
    except Exception as exc:  # noqa: BLE001 —— 删除失败如实报错，用户可重试
        raise http_error(
            "rag_unavailable",
            f"文档删除失败：{type(exc).__name__}: {exc}"[:200],
        ) from exc

    names = _store_call(store.delete_ingestion_by_doc, user_id, doc_id)
    for name in names:
        if not name:
            continue
        try:
            os.remove(quarantine_path(name))
        except FileNotFoundError:
            pass
    return {"ok": True, "doc_id": doc_id}


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
    counts: dict[str, dict] = {}
    for payload in vector_store.scroll_all(scope=RagScope(user_id=user_id)):
        doc_id = str(payload.get("doc_id") or "")
        source = payload.get("source") or doc_id or "(未命名)"
        key = doc_id or source
        entry = counts.setdefault(key, {"doc_id": doc_id, "source": source, "chunks": 0})
        entry["chunks"] += 1
    return {"docs": sorted(counts.values(), key=lambda item: item["source"])}


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
    P4-A：鉴权开启时只允许导出自己的 run。P0-4：flagged / blocked 一律 403。
    """
    _authorize_run(request, run_id)
    _enforce_output_policy(run_id)
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
    METRICS.sse_open()
    try:
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
            flagged = row.get("moderation_status") in ("flagged", "blocked")
            for event in events:
                cursor = event["sequence"]
                payload = event["payload"] or {}
                # P0-4：flagged / blocked 的历史回放不携带正文（原文只在产物里）
                if flagged and event["event_type"] == "RUN_FINISHED":
                    payload = _redact_report_payload(payload)
                yield sse_frame(
                    event_id=cursor,
                    event_type=event["event_type"],
                    payload=payload,
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
    finally:
        METRICS.sse_close()


async def _event_gen(run_id: str, last_event_id: Optional[int]) -> AsyncIterator[str]:
    METRICS.sse_open()
    try:
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
    finally:
        METRICS.sse_close()


FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"
if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
