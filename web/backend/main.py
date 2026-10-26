"""FastAPI 应用装配（需求 9 §7.4 最小骨架）。

**范围封顶（ADR-0001 §1.1 + D-20 三条硬边界）**：
只做呈现层与传输层。不做登录、用户系统、任务队列、多租户、权限、云端部署。
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import ipaddress
import json
import os
import secrets
import shutil
import socket
import sys
import tempfile
import time
import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, AsyncIterator, Optional
from urllib.parse import urlsplit

from fastapi import FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import (
    FileResponse,
    JSONResponse,
    RedirectResponse,
    Response,
    StreamingResponse,
)
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from config import config
from research_engine.search.base import KNOWN_PROVIDERS
from research_engine.usage import pop_usage_sink, push_usage_sink

from . import docx_render as docx_render_mod
from . import export as export_mod
from . import followup as followup_mod
from . import passkeys, secretbox
from .agui import (
    FOLLOWUP_ANSWERED,
    FOLLOWUP_ASKED,
    HEARTBEAT_FRAME,
    HEARTBEAT_SECONDS,
    INTERACTION_EVENT_TYPES,
    REPORT_DELTA,
    sse_frame,
)
from .alerts import alert_webhook_url, collect_alerts
from .appeals import submit_appeal
from .auth import (
    CSRF_COOKIE as CSRF_COOKIE_BASE,
)
from .auth import (
    CSRF_HEADER,
    MIN_PASSWORD_LENGTH,
    check_password_strength,
    device_key,
    hash_password,
    host_prefix_cookie_name,
    is_new_device,
    is_valid_email,
    new_token,
    normalize_email,
    token_hash,
    verify_password,
)
from .auth import (
    SESSION_COOKIE as SESSION_COOKIE_BASE,
)
from .download_names import build_export_filename, content_disposition
from .egress import build_egress_snapshot
from .errors import ApiError, error_payload, http_error
from .hitl import (
    ANSWER_EVENT_BY_KIND,
    KIND_BY_STATUS,
    build_answer_event_payload,
    build_resume_payload,
    interaction_view,
    validate_answer,
)
from .injection_guard import scan_injection
from .mailer import get_mailer, new_login_email, password_reset_email
from .metrics import METRICS
from .moderation import (
    MAX_APPEAL_LENGTH,
    MAX_INSTRUCTIONS_LENGTH,
    MAX_TOPIC_LENGTH,
    evaluate_input,
    redact_public_payload,
)
from .notify import RunEventNotifier
from .objectstore import get_object_store
from .observability import init_error_tracking, set_request_context
from .otel import (
    PROMETHEUS_ENABLED,
    instrument_fastapi,
    instrument_httpx,
    prometheus_asgi_app,
    setup_otel,
)
from .persistence import OPEN_STATUSES
from .profiles import DEFAULT_PROFILE, PROFILES, profile_options, resolve_profile
from .queue import RunQueue
from .ratelimit import make_limiter
from .runner import RunManager, _estimate_cost_cny
from .security_headers import SecurityHeadersMiddleware
from .store import ACTIVE_STATUSES, QuotaExceeded, RunStore
from .totp import (
    encode_secret as totp_encode_secret,
)
from .totp import (
    hash_recovery_code,
    new_recovery_codes,
    new_totp_secret,
    verify_totp,
)
from .totp import (
    provisioning_uri as totp_provisioning_uri,
)
from .upload_guard import (
    ALLOWED_EXT,
    UploadRejected,
    detect_and_validate,
    extension_of,
    sanitize_filename,
    stream_to_temp,
)
from .usage import make_store_sink

app = FastAPI(title="爱考据 iKaoju", version="0.2.0")

# P1-8：OpenTelemetry（默认关闭；OTLP 端点 / Prometheus 开关任一存在才启用）
OTEL_ENABLED = setup_otel("deepresearch-api")
instrument_httpx()
instrument_fastapi(app)


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
async def _request_id_middleware(request: Request, call_next):
    """P1-5：为每个请求分配 `X-Request-ID`（支持透传），供审计 / 日志关联。"""
    request_id = (request.headers.get("x-request-id") or "").strip()[:64]
    request.state.request_id = request_id or uuid.uuid4().hex[:12]
    set_request_context(request.state.request_id)
    response = await call_next(request)
    response.headers["X-Request-ID"] = request.state.request_id
    return response


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
        proto = _effective_proto(request)
        if proto != "https":
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
# RFC 6265bis `__Host-` 前缀（仅 HTTPS 启用）：要求 Secure + Path=/ + 无 Domain；
# HTTP 本地调试模式浏览器会拒收，故退回裸名（测试/本地行为不变）。
SESSION_COOKIE = host_prefix_cookie_name(SESSION_COOKIE_BASE, COOKIE_SECURE)
CSRF_COOKIE = host_prefix_cookie_name(CSRF_COOKIE_BASE, COOKIE_SECURE)
SESSION_TTL_SECONDS = int(os.getenv("DR_SESSION_TTL_SECONDS", "604800"))
# P1-10：空闲超时（秒）；0 = 仅绝对超时（L3-A 默认口径，文档登记）
SESSION_IDLE_SECONDS = int(_env_number("DR_SESSION_IDLE_SECONDS", 0))


def _parse_env() -> str:
    """运行环境（P0-9）：local（默认，宽松）/ staging / production（启动硬校验）。"""
    env = (os.getenv("DR_ENV") or "local").strip().lower()
    if env not in {"local", "staging", "production"}:
        raise RuntimeError(f"DR_ENV 仅支持 local|staging|production，收到：{env!r}")
    return env


ENV = _parse_env()

# P0-3（2026-10-26）：全局安全响应头（CSP / HSTS / nosniff / frame-ancestors / Referrer /
# Permissions）。纯 ASGI 中间件放在最外层，流式（SSE）语义不变，且不覆盖应用已设 header。
# HSTS 与 upgrade-insecure-requests 仅在 staging/production 注入。
app.add_middleware(
    SecurityHeadersMiddleware,
    env=ENV,
    connect_src_extra=(os.getenv("DR_CSP_CONNECT_SRC") or "").strip(),
)


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
# P1-2：配置 Redis 时使用滑动窗口（多实例共享）；否则回落进程内固定窗口。
MAX_USER_CONCURRENT = max(1, int(_env_number("DR_MAX_USER_CONCURRENT", 1)))
DAILY_RUNS_PER_USER = int(_env_number("DR_DAILY_RUNS_PER_USER", 1))
RUN_BUDGET_CNY = _env_number("DR_RUN_BUDGET_CNY", 1.50)
MONTHLY_BUDGET_CNY = _env_number("DR_MONTHLY_BUDGET_CNY", 1500.0)
_LIMITER_REDIS_URL = (os.getenv("DR_REDIS_URL") or "").strip()
LOGIN_LIMITER = make_limiter(
    "login_ip", int(_env_number("DR_LOGIN_RATE_PER_MINUTE", 10)),
    redis_url=_LIMITER_REDIS_URL)
LOGIN_ACCOUNT_LIMITER = make_limiter(
    "login_acct", int(_env_number("DR_LOGIN_ACCOUNT_RATE_PER_MINUTE", 5)),
    redis_url=_LIMITER_REDIS_URL)
SUBMIT_LIMITER = make_limiter(
    "submit", int(_env_number("DR_SUBMIT_RATE_PER_MINUTE", 10)),
    redis_url=_LIMITER_REDIS_URL)
# 需求 36 H3：追问按用户限流（每次追问都是真实 LLM 调用）
FOLLOWUP_LIMITER = make_limiter(
    "followup", int(_env_number("DR_FOLLOWUP_RATE_PER_MINUTE", 10)),
    redis_url=_LIMITER_REDIS_URL)
# P0-2（2026-10-26 评审）：MFA step-up / MFA 管理 / Passkey 管理的爆破防护（按用户）。
MFA_VERIFY_LIMITER = make_limiter(
    "mfa_verify", int(_env_number("DR_MFA_VERIFY_RATE_PER_MINUTE", 5)),
    redis_url=_LIMITER_REDIS_URL)
MFA_ENROLL_LIMITER = make_limiter(
    "mfa_enroll", int(_env_number("DR_MFA_ENROLL_RATE_PER_MINUTE", 10)),
    redis_url=_LIMITER_REDIS_URL)
PASSKEY_MANAGE_LIMITER = make_limiter(
    "passkey_manage", int(_env_number("DR_PASSKEY_MANAGE_RATE_PER_MINUTE", 10)),
    redis_url=_LIMITER_REDIS_URL)
# P1-2 / P0-10：可信反向代理。生产口径：显式配置代理 CIDR（反向代理必须清理
# 外部传入的 X-Forwarded-* 头），应用只信任来自可信 hop 的 XFF / X-Forwarded-Proto。
# `DR_TRUST_PROXY=true` 是历史布尔开关（信任任意来源的 XFF 首跳），仅在未配置
# CIDR 时保留兼容；staging/生产应改用 CIDR 白名单。
def _parse_trusted_proxy_cidrs(raw: str) -> tuple:
    networks = []
    for item in (raw or "").split(","):
        item = item.strip()
        if not item:
            continue
        try:
            networks.append(ipaddress.ip_network(item, strict=False))
        except ValueError:
            print(f"[trusted-proxy] 非法 CIDR 已忽略：{item!r}", flush=True)
    return tuple(networks)


TRUSTED_PROXY_CIDRS = _parse_trusted_proxy_cidrs(os.getenv("DR_TRUSTED_PROXY_CIDRS", ""))
PROXY_HOPS = max(1, int(_env_number("DR_PROXY_HOPS", 1)))
TRUST_PROXY = _env_flag("DR_TRUST_PROXY", "false")


def _is_trusted_proxy(host: Optional[str]) -> bool:
    """直连对端是否在可信代理 CIDR 内（未配置 CIDR 时恒 False）。"""
    if not TRUSTED_PROXY_CIDRS or not host:
        return False
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in TRUSTED_PROXY_CIDRS)


def _effective_proto(request: Request) -> str:
    """P0-10：仅当对端是可信代理时才采信 X-Forwarded-Proto（否则用直连 scheme）。"""
    if _is_trusted_proxy(request.client.host if request.client else None):
        forwarded = request.headers.get("x-forwarded-proto")
        if forwarded:
            return forwarded.split(",")[0].strip().lower()
    return request.url.scheme.lower()
# P0-9：运维接口（/api/metrics、/api/ops/*）应用层鉴权。配置 DR_OPS_TOKEN 后
# 必须携带 `X-Ops-Token`；staging/生产未配置 token 时运维接口一律 401（fail-closed）。
OPS_TOKEN = (os.getenv("DR_OPS_TOKEN") or "").strip()

# P6-A / P0-8a：RAG 上传限制（流式落盘 + magic bytes 三重校验 + 解析限额）
RAG_MAX_UPLOAD_MB = _env_number("DR_RAG_MAX_FILE_MB", 10.0)
#: 需求 23：知识库总配额（MB；0 = 不限，仅展示用量）
RAG_TOTAL_MB = _env_number("DR_RAG_TOTAL_MB", 0)
RAG_UPLOAD_LIMITER = make_limiter(
    "rag_upload", int(_env_number("DR_RAG_UPLOADS_PER_MINUTE", 10)),
    redis_url=_LIMITER_REDIS_URL)

# 需求 26：报告只读分享（默认关闭；过期默认 7 天，0=永不过期需显式选择）
SHARE_ENABLED = os.getenv("DR_SHARE_ENABLED", "false").lower() == "true"
SHARE_LIMITER = make_limiter(
    "share", int(_env_number("DR_SHARE_RATE_PER_MINUTE", 30)),
    redis_url=_LIMITER_REDIS_URL)

# P8-A：告警判定阈值（触达渠道由部署方接 IM/邮件；此处只做“可判定”）
ALERT_5XX_RATE_PCT = _env_number("DR_ALERT_5XX_RATE_PCT", 2.0)
ALERT_QUEUE_DEPTH = int(_env_number("DR_ALERT_QUEUE_DEPTH", 20))
ALERT_STALE_RUNS = int(_env_number("DR_ALERT_STALE_RUNS", 1))
ALERT_MONTHLY_PCT = _env_number("DR_ALERT_MONTHLY_PCT", 80.0)
# P1-3：readiness / 告警的 worker 心跳新鲜度窗口
WORKER_HEARTBEAT_MAX_AGE_SECONDS = int(_env_number("DR_WORKER_HEARTBEAT_MAX_AGE_SECONDS", 90))

# P7-A：合规文本（隐私政策 / 用户协议）以仓库文档为唯一来源
LEGAL_DIR = Path(__file__).resolve().parents[2] / "docs" / "legal"
# 需求 25：帮助中心 FAQ（仓库文档为唯一来源）
HELP_DIR = Path(__file__).resolve().parents[2] / "docs" / "help"


def _legal_version(path: Path) -> str:
    """需求 25：法律文档版本号 = 内容 sha256[:12]（注册同意留档用）。"""
    return hashlib.sha256(path.read_bytes()).hexdigest()[:12]


# P3：执行模式。`inprocess` = 请求进程内线程执行（P1/P2 行为，默认，本地开发）；
# `queue` = 创建 QUEUED 任务入 Redis 队列，由独立 Worker 执行（staging/生产）。
# 队列模式要求同时配置任务库与 Redis —— 配错直接启动失败，不做静默回退。
# ⚠️ 必须先于任务库启动维护解析：维护语义取决于「谁在执行」（见 _startup_store_maintenance）。
_EXECUTION_MODE = (os.getenv("DR_EXECUTION_MODE") or "inprocess").strip().lower()
if _EXECUTION_MODE not in {"inprocess", "queue"}:
    raise RuntimeError(f"DR_EXECUTION_MODE 仅支持 inprocess|queue，收到：{_EXECUTION_MODE!r}")
EXECUTION_MODE = "inprocess" if DEMO_MODE else _EXECUTION_MODE

# 需求 36：HITL（澄清/审批/追问）需要「挂起-回答-恢复」通道 ⇒ 仅队列模式（Worker）支持。
# 非队列模式下强制关闭（显式告警，不静默）：否则图会 interrupt，而在进程内执行器里
# 没有等待态持久化与 resume 通道，任务会卡死。worker 进程侧无此约束（它就绪即 queue）。
if EXECUTION_MODE != "queue":
    for _hitl_field in ("clarify_enabled", "plan_approval_enabled", "followup_enabled"):
        if getattr(config.hitl, _hitl_field):
            setattr(config.hitl, _hitl_field, False)
            print(f"[api] DR_HITL_* 需要队列模式（DR_EXECUTION_MODE=queue）："
                  f"已强制关闭 {_hitl_field}", file=sys.stderr, flush=True)


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
    # P1-10：清理已消费 / 过期超过 1 天的重置 token（存储卫生）
    run_store.purge_expired_password_resets()
    # 需求 20 P1-2：清理过期 / 已消费超过 1 天的 WebAuthn 挑战（存储卫生）
    run_store.purge_expired_webauthn_challenges()


# P2-C：任务库（PostgreSQL）。演示模式不接库，保证 E2E / 本地 UI 演示零依赖。
store = None if DEMO_MODE else _make_store()

# 需求 38 F2：导出作业的 store（真实模式 = 任务库；DR_DEMO = 内存替身，零 PG 依赖）。
# 端点只依赖其四个方法（get_run / find_reusable / create / get），演示替身见 demo_export.py。
_demo_export_store = None
if store is None and DEMO_MODE:
    from .demo_export import DemoExportStore

    _demo_export_store = DemoExportStore()


def _export_store():
    """导出端点使用的 store：真实=任务库；DR_DEMO=内存替身；两者皆无 ⇒ None（结构化不可用）。"""
    return store if store is not None else _demo_export_store

# 需求 25：错误追踪（Sentry 协议；DSN 空 = 关闭；观测旁路不阻断启动）
init_error_tracking()

# P2-4：SSE 尾随的 LISTEN/NOTIFY 唤醒（懒启动；通知只放 run_id，正确性靠轮询兜底）
SSE_POLL_SECONDS = max(0.5, _env_number("DR_SSE_POLL_SECONDS", 5.0))
_notifier: Optional[RunEventNotifier] = None


def _get_notifier() -> Optional[RunEventNotifier]:
    """按当前 store 懒装配通知器；无任务库（本地零依赖 / 测试替身）返回 None。"""
    global _notifier
    if store is None:
        return None
    dsn = getattr(store, "dsn", "")
    if not dsn:
        return None
    if _notifier is None or _notifier.dsn != dsn:
        _notifier = RunEventNotifier(dsn, poll_seconds=SSE_POLL_SECONDS)
    return _notifier
if store is not None:
    try:
        _startup_store_maintenance(store, EXECUTION_MODE)
        # P1-6：对象存储桶 + 生命周期（best-effort；未配置则为 None 直接跳过）
        _object_store = get_object_store()
        if _object_store is not None:
            # 不再静默：ensure_bucket 自己捕获分步骤失败并返回 warnings，
            # 这里逐条打到 stderr —— 尤其是「生命周期没设上」，否则 90 天保留期
            # 悄悄失效、与隐私政策承诺不符却无人知晓。
            for _warning in _object_store.ensure_bucket().get("warnings", []):
                print(f"[objectstore] {_warning}", file=sys.stderr, flush=True)
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
        # 需求 25：错误追踪（DSN 空 = 前端不初始化，零网络请求）
        "sentry_dsn": config.observability.sentry_dsn_frontend,
        "sentry_environment": config.observability.sentry_environment,
        "release": config.observability.sentry_release,
        # 需求 26：报告只读分享开关（前端据此隐藏分享入口）
        "share_enabled": SHARE_ENABLED,
        # 需求 36 H3：报告后追问开关（前端据此决定是否显示追问入口）
        "followup_enabled": bool(config.hitl.followup_enabled),
        # 需求 20 P1-2：Passkey 可用性 + RP ID（前端据此判断当前域名是否可注册/登录）
        "webauthn": {"available": passkeys.enabled(), "rp_id": passkeys.rp_id()},
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
    if EXECUTION_MODE == "queue" and store is not None:
        # P1-3：队列模式下「有活跃 worker」是 readiness 的硬条件
        try:
            live = int(store.count_live_workers(
                within_seconds=WORKER_HEARTBEAT_MAX_AGE_SECONDS))
        except Exception as exc:  # noqa: BLE001
            checks["worker"] = {"status": "unreachable", "target": f"{type(exc).__name__}"}
        else:
            checks["worker"] = {
                "status": "ok" if live else "unavailable",
                "live": live,
                "max_age_seconds": WORKER_HEARTBEAT_MAX_AGE_SECONDS,
            }
    object_store = get_object_store()
    if object_store is not None:
        # P1-6：配置了对象存储才探针（未配置 = 双轨回落 PG，不判失败）
        try:
            object_store.ping()
        except Exception as exc:  # noqa: BLE001
            checks["object_storage"] = {"status": "unreachable",
                                        "target": f"{type(exc).__name__}"}
        else:
            checks["object_storage"] = {"status": "ok", "target": object_store.endpoint}
    failed = [item for item in checks.values()
              if item["status"] in {"unreachable", "invalid_url", "unavailable"}]
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
    # 需求 36：等待态（AWAITING_*）尚未结束 ⇒ 快照仍为 running，并附当前交互卡片
    active = row["status"] in OPEN_STATUSES
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
        # 需求 36：当前待答交互（非等待态为 None；卡片字段见 hitl.interaction_view）
        "interaction": interaction_view(row),
        # P1-9：数据流向快照（创建时固化；历史任务可解释「当时发给了谁」）
        "egress": (row.get("request") or {}).get("egress"),
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
        # 需求 26：是否已有活跃分享（历史列表「已分享」徽标）
        "shared": bool(row.get("shared")),
        "moderation_status": row.get("moderation_status"),
        # 需求 22：置顶 / 软归档（NULL=未设置）
        "pinned_at": _iso(row.get("pinned_at")),
        "archived_at": _iso(row.get("archived_at")),
    }


# ------------------------------------------------------------------ 鉴权（P4-A）


def _session_user(request: Request) -> Optional[dict]:
    return _session_user_impl(request, require_mfa=True)


def _pending_session_user(request: Request) -> Optional[dict]:
    """登录 step-up 专用：允许 `mfa_verified=false` 的**半程会话**。

    仅 `/api/auth/mfa/verify` 使用；其余一律走 `_session_user`（要求已完成第二步）。
    """
    return _session_user_impl(request, require_mfa=False)


def _session_user_impl(request: Request, *, require_mfa: bool) -> Optional[dict]:
    if store is None:
        return None
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return None
    user = _store_call(store.get_session_user, token_hash(token),
                       idle_seconds=SESSION_IDLE_SECONDS)
    if user is None:
        return None
    if require_mfa and not user.get("session_mfa_verified", True):
        # 半程会话（等待第二步验证）：不得访问任何受保护接口
        return None
    try:
        store.touch_session(token_hash(token))  # P1-10：节流刷新 last_seen
    except Exception:  # noqa: BLE001 —— 会话卫生旁路
        pass
    return user


def _require_user(request: Request) -> Optional[str]:
    """返回当前 user_id；未启用鉴权时返回 None（匿名模式，行为与 P3 一致）。"""
    if not AUTH_REQUIRED:
        return None
    user = _session_user(request)
    if user is None:
        raise http_error("unauthenticated", "请先登录")
    return user["user_id"]


def _require_ops(request: Request) -> None:
    """运维接口应用层鉴权（P0-9）：反代限制来源是纵深，不是唯一边界。

    - 配置 `DR_OPS_TOKEN` ⇒ 必须携带匹配的 `X-Ops-Token`（常量时间比较）；
    - 未配置且 `DR_ENV` 为 staging/production ⇒ 401（fail-closed，禁止裸奔）；
    - 未配置且本地开发 ⇒ 放行（与历史行为一致）。
    """
    if OPS_TOKEN:
        provided = request.headers.get("x-ops-token", "")
        if provided and hmac.compare_digest(provided, OPS_TOKEN):
            return
        raise http_error("unauthenticated", "运维接口需要有效的 X-Ops-Token")
    if ENV in ("staging", "production"):
        raise http_error("unauthenticated", "运维接口未配置 DR_OPS_TOKEN（fail-closed）")


def _check_csrf(request: Request) -> None:
    """双提交 Cookie 校验（仅在启用鉴权后生效；登录/注册除外）。"""
    if not AUTH_REQUIRED:
        return
    cookie = request.cookies.get(CSRF_COOKIE)
    header = request.headers.get(CSRF_HEADER)
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        _audit("csrf_failed", request=request,
               detail={"path": request.url.path})
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
            _audit("authz_denied", request=request, actor_user_id=user_id,
                   target_type="run", target_id=run_id)
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    return owner


def _enforce_output_policy(run_id: str) -> None:
    """P0-4 统一输出闸：**除 NULL 与 cleared 外**的报告不向普通用户开放查看与导出。

    覆盖 `flagged` / `under_review`（provider 降级隔离）/ `blocked`（fail-closed）。
    原文仍保留在 `run_artifacts`，管理员经 CLI（`admin run-report`）复核。
    """
    if store is None:
        return
    row = _store_call(store.get_run, run_id)
    status = row.get("moderation_status") if row else None
    if status and status != "cleared":
        message = ("报告命中内容安全预检，正在等待人工复核"
                   if status in ("flagged", "blocked")
                   else "审核服务不可用，报告已隔离待审")
        raise http_error("output_under_review", message,
                         detail=f"moderation_status={status}")


def _redact_report_payload(payload: dict) -> dict:
    """P0-4 / P0-12：回放前递归脱敏 —— 任何正文键（含历史新增字段）都不得外泄。"""
    sanitized = redact_public_payload(payload)
    if isinstance(payload, dict) and sanitized == payload:
        return payload
    return {**sanitized, "output_under_review": True}


def _redact_interaction_payload(payload: dict) -> dict:
    """需求 36：flagged 回放对 HITL 事件脱敏（回答文本 / 卡片摘要都可能含敏感原文）。

    保留协议字段（interaction_id / kind / round / action）——UI 仍可展示
    「有一张待答卡片 / 已作答（内容待审）」，但正文一律置空并标记 under_review。
    """
    if not isinstance(payload, dict):
        return {"output_under_review": True}
    scrubbed = dict(payload)
    for key in ("text", "questions", "plan", "summary", "content", "question", "answer"):
        if key not in scrubbed:
            continue
        scrubbed[key] = {} if isinstance(scrubbed[key], dict) else ""
    scrubbed["output_under_review"] = True
    return scrubbed


def _public_user(user: dict) -> dict:
    return {"user_id": user["user_id"], "email": user["email"]}


def _reject_weak_password(password: str, *, email: str = "") -> None:
    """NIST 口令策略（长度由 Pydantic ``min_length`` 承担；此处为弱口令/上下文检查）。"""
    reason = check_password_strength(password, email=email)
    if reason:
        raise http_error("invalid_request", reason)


def _set_session_cookies(response: Response, user_id: str, request: Optional[Request] = None,
                         *, mfa_verified: bool = True) -> None:
    """建会话 + 写 Cookie：session 为 httpOnly，CSRF 为可读双提交 Cookie。

    P1-10：会话记录 IP / User-Agent（会话治理展示与排障）。
    P1-3：`mfa_verified=False` 表示「半程会话」——第二步验证通过前不得访问受保护接口。
    """
    token = new_token()
    _store_call(store.create_session, token_hash(token), user_id,
                datetime.now(UTC) + timedelta(seconds=SESSION_TTL_SECONDS),
                ip=(_client_key(request) if request is not None else None),
                user_agent=(request.headers.get("user-agent") if request is not None else None),
                mfa_verified=mfa_verified)
    response.set_cookie(SESSION_COOKIE, token, max_age=SESSION_TTL_SECONDS, httponly=True,
                        samesite="lax", secure=COOKIE_SECURE, path="/")
    response.set_cookie(CSRF_COOKIE, new_token(), max_age=SESSION_TTL_SECONDS, httponly=False,
                        samesite="lax", secure=COOKIE_SECURE, path="/")


def _notify_new_login(user: dict, request: Request) -> None:
    """需求 20 P1-1b：新设备登录提醒（best-effort，**绝不**影响登录本身）。

    - SMTP 未配置 ⇒ 审计 `login_notify_skipped`（显式降级，不静默吞）；
    - 投递成功 / 失败分别审计 `login_notified` / `login_notify_failed`；
    - 邮件不含 token / 链接 / 密码；设备（UA 头）与 IP 在 HTML 中已转义。
    """
    try:
        mailer = get_mailer()
        if mailer is None:
            _audit("login_notify_skipped", request=request, actor_user_id=user["user_id"],
                   detail={"reason": "mail_unavailable"})
            return
        ip = _client_key(request)
        device = (request.headers.get("user-agent") or "").strip()[:200]
        subject, text, html = new_login_email(
            when=datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC"),
            device=device or "未知设备", ip=ip)
        mailer.send(user["email"], subject, text, html)
        _audit("login_notified", request=request, actor_user_id=user["user_id"],
               detail={"ip": ip})
    except Exception as exc:  # noqa: BLE001 —— 通知是旁路，异常不得打断登录
        _audit("login_notify_failed", request=request, actor_user_id=user["user_id"],
               detail={"error": type(exc).__name__})


# ------------------------------------------------------------------ 配额与限流（P4-B）


def _client_key(request: Request) -> str:
    """客户端标识（P0-10）：仅当直连对端属于**可信代理 CIDR** 时采信 XFF。

    取 XFF 倒数第 `DR_PROXY_HOPS` 跳（默认 1 = 可信代理写入的最后一跳；
    代理必须清理外部传入的 X-Forwarded-* 头）。未配置 CIDR 时兼容旧
    `DR_TRUST_PROXY=true`（取首跳）；否则一律使用直连对端地址。
    """
    peer = request.client.host if request.client else None
    if _is_trusted_proxy(peer):
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            parts = [part.strip() for part in forwarded.split(",") if part.strip()]
            if parts:
                index = len(parts) - PROXY_HOPS
                return (parts[index] if index >= 0 else parts[0])[:64] or "unknown"
        return (peer or "unknown")[:64]
    if TRUST_PROXY:
        forwarded = request.headers.get("x-forwarded-for")
        if forwarded:
            return forwarded.split(",")[0].strip()[:64] or "unknown"
    return peer or "unknown"


def _audit(action: str, *, request: Optional[Request] = None,
           actor_user_id: Optional[str] = None, target_type: Optional[str] = None,
           target_id: Optional[str] = None, detail: Optional[dict] = None) -> None:
    """安全审计（P1-5，append-only）：best-effort —— 审计失败绝不打断业务。"""
    if store is None:
        return
    meta: dict = {}
    if request is not None:
        meta = {
            "ip": _client_key(request),
            "user_agent": request.headers.get("user-agent"),
            "request_id": getattr(request.state, "request_id", None),
        }
    try:
        store.record_audit(
            action, actor_user_id=actor_user_id, target_type=target_type,
            target_id=target_id, detail=detail, **meta)
    except Exception:  # noqa: BLE001 —— 审计是旁路，不得影响主流程
        pass


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


def _request_fingerprint(topic: str, instructions: str, profile_name: str) -> str:
    """P1-1：幂等请求指纹 —— 规范化（topic + instructions + profile）的 SHA-256。

    需求 37 M2：入参改原语（服务函数 `start_research` 与重试端点共用；MCP 同口径）。
    """
    canonical = json.dumps(
        {"topic": topic, "instructions": instructions, "profile": profile_name},
        ensure_ascii=False, sort_keys=True, separators=(",", ":"),
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _idempotent_existing(user_id: Optional[str], idempotency_key: Optional[str],
                         fingerprint: str) -> Optional[dict]:
    """P1-1：同键查询；指纹不一致 ⇒ 409（不得静默复用）。旧行 NULL 指纹按遗留口径放行。"""
    if store is None or idempotency_key is None:
        return None
    existing = _store_call(store.get_run_by_idempotency, user_id, idempotency_key)
    if existing is None:
        return None
    stored = existing.get("request_hash")
    if stored is not None and stored != fingerprint:
        raise http_error(
            "idempotency_conflict",
            "该幂等键已用于不同请求",
            detail=f"idempotency_key={idempotency_key}",
        )
    return existing


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


def _admission_limits(reserve_cny: Optional[float] = None) -> dict:
    """当前生效的准入闸（P0-3 / P0-2：由 store 在同一事务内原子执行）。

    `reserve_cny`：本次任务的预算预留（hold，通常 = 单 run 预算上界）；
    仅当配置了月度预算闸时生效。
    """
    return {
        "global_active_limit": manager.max_concurrent_runs,
        "user_active_limit": MAX_USER_CONCURRENT,
        "daily_limit": DAILY_RUNS_PER_USER or None,
        "monthly_budget_cny": MONTHLY_BUDGET_CNY or None,
        "reserve_cny": reserve_cny if (MONTHLY_BUDGET_CNY or 0) > 0 else None,
        "daily_since": _day_start_utc(),
    }


def _start_queued(*, topic: str, instructions: str, idempotency_key: Optional[str],
                  user_id: Optional[str], profile, ignored: dict,
                  fingerprint: str, retry_of: Optional[str] = None) -> str:
    """队列模式（P3）：创建 `QUEUED` 任务并投递唤醒信号；重复幂等键返回既有 run_id。

    幂等命中先于并发检查 —— 重复提交是同一个逻辑请求，不应被并发闸拒绝。
    P0：run.request 写**档位快照**（而非客户端参数）；超时 / 预算取自档位。
    P1-1：同键不同指纹 ⇒ 409（由 `_idempotent_existing` 判定）。
    需求 22：`retry_of` 记录重试血缘（原 run_id）。
    需求 37 M2：入参改原语（服务函数 `start_research` 与重试端点共用）。
    """
    if store is None:
        raise ApiError("persistence_unavailable", "队列模式需要任务库（DR_DATABASE_URL）")
    existing = _idempotent_existing(user_id, idempotency_key, fingerprint)
    if existing is not None:
        return existing["run_id"]
    run_id = uuid.uuid4().hex[:12]
    try:
        row, created = store.create_run_admitted(
            run_id, topic,
            {
                "instructions": instructions,
                "profile": profile.snapshot(),
                "ignored_overrides": ignored or None,
                # P1-9：数据流向快照（谁收到了什么；不含密钥）
                "egress": build_egress_snapshot(profile),
            },
            user_id=user_id,
            idempotency_key=idempotency_key,
            request_hash=fingerprint,
            retry_of=retry_of,
            status="QUEUED",
            timeout_at=datetime.now(UTC) + timedelta(seconds=profile.timeout_seconds),
            budget_limit_cny=_effective_run_budget(profile),
            **_admission_limits(_effective_run_budget(profile)),
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
    agree_terms: bool = Field(False, description="需求 25：必须勾选同意《用户协议》与《隐私政策》")


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
    if not req.agree_terms:
        raise http_error("invalid_request", "请先阅读并同意《用户协议》与《隐私政策》")
    _reject_weak_password(req.password, email=email)
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
    _set_session_cookies(response, user["user_id"], request)
    # 需求 25：注册同意留档（PIPL 可举证；版本 = 文档内容 hash）
    consent_versions = {
        "terms": _legal_version(LEGAL_DIR / "terms-of-service.md"),
        "privacy": _legal_version(LEGAL_DIR / "privacy-policy.md"),
    }
    _store_call(store.save_user_consents, user["user_id"],
                [{"doc_type": doc_type, "version": version}
                 for doc_type, version in consent_versions.items()],
                ip_hash=hashlib.sha256(_client_key(request).encode("utf-8")).hexdigest()[:16])
    _audit("register_success", request=request, actor_user_id=user["user_id"],
           detail={"invite": bool(req.invite_code), "consents": consent_versions})
    return {"user": _public_user(user)}


@app.post("/api/auth/login")
def auth_login(req: LoginRequest, request: Request, response: Response) -> dict:
    """邮箱 + 密码登录；失败统一 401（不区分「用户不存在 / 密码错 / 已封禁」）。

    P1-2：限流双维度 —— IP（`DR_LOGIN_RATE_PER_MINUTE`）+ 账号
    （`DR_LOGIN_ACCOUNT_RATE_PER_MINUTE`，邮箱哈希作键，防定向撞库）。
    """
    email = normalize_email(req.email)
    email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]
    if (not LOGIN_LIMITER.allow(f"login:{_client_key(request)}")
            or not LOGIN_ACCOUNT_LIMITER.allow(f"login-acct:{email_hash}")):
        _audit("login_rate_limited", request=request, detail={"email_hash": email_hash})
        raise http_error("rate_limited", "登录请求过于频繁，稍后再试")
    if store is None:
        raise http_error("persistence_unavailable", "账号功能需要任务库（DR_DATABASE_URL）")
    user = _store_call(store.get_user_by_email, email)
    if (user is None or user["status"] != "active"
            or not verify_password(user["password_hash"], req.password)):
        _audit("login_failed", request=request, detail={"email_hash": email_hash})
        raise http_error("invalid_credentials", "邮箱或密码不正确")
    _store_call(store.touch_last_login, user["user_id"])
    # P1-1b：先看「该用户既有活跃会话」判断是否新设备，再建本会话（否则自己会被算进去）
    prior = _store_call(store.list_sessions, user["user_id"])
    new_device = is_new_device(
        [device_key(row.get("user_agent"), row.get("ip")) for row in prior],
        request.headers.get("user-agent"), _client_key(request))
    # P1-3：MFA 用户先发「半程会话」，第二步验证通过才算登录完成。
    # fail-closed：有因子但服务端密钥不可用 ⇒ 拒绝（防「删密钥即绕过第二步」）。
    mfa_factor = _store_call(store.get_mfa_factor, user["user_id"])
    if mfa_factor is not None and not secretbox.configured():
        _audit("mfa_unavailable", request=request, actor_user_id=user["user_id"])
        raise http_error("mfa_unavailable", "MFA 暂不可用（服务端密钥未配置），请联系管理员")
    _set_session_cookies(response, user["user_id"], request,
                         mfa_verified=(mfa_factor is None))
    _audit("login_success", request=request, actor_user_id=user["user_id"])
    if mfa_factor is not None:
        _audit("mfa_challenge_issued", request=request, actor_user_id=user["user_id"])
        return {"mfa_required": True}
    if new_device:
        _notify_new_login(user, request)
    return {"user": _public_user(user)}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    user = _session_user(request)
    token = request.cookies.get(SESSION_COOKIE)
    if token and store is not None:
        _store_call(store.revoke_session, token_hash(token))
    if user is not None:
        _audit("logout", request=request, actor_user_id=user["user_id"])
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@app.get("/api/auth/session")
def auth_session(request: Request) -> dict:
    user = _session_user(request)
    if user is None:
        raise http_error("unauthenticated", "未登录")
    return {"user": _public_user(user)}


# ------------------------------------------------------------------ MFA：TOTP + 恢复码（需求 20 P1-3）

#: 认证器 App 中显示的签发方
MFA_ISSUER = "爱考据 iKaoju"


class MfaCodeRequest(BaseModel):
    code: str = Field(..., min_length=4, max_length=32)


class MfaDisableRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)
    code: str = Field("", max_length=32)


def _require_mfa_ready() -> None:
    """MFA 端点前置：服务端密钥未配置即 503（fail-closed，不静默降级）。"""
    if not secretbox.configured():
        raise http_error("mfa_unconfigured", "MFA 未启用（服务端缺少 DR_MFA_SECRET_KEY）")


def _iso(value) -> Optional[str]:
    return value.isoformat() if value is not None else None


def _verify_totp_factor(factor: dict, code: str) -> bool:
    """用因子密文校验 TOTP；密钥不可用 / 密文损坏一律 False（fail-closed）。"""
    try:
        secret = secretbox.decrypt(factor["secret_encrypted"])
    except Exception:  # noqa: BLE001
        return False
    if verify_totp(secret, code, time.time()):
        _store_call(store.touch_mfa_factor_used, factor["factor_id"])
        return True
    return False


def _verify_mfa_factor(user_id: str, factor: dict, code: str) -> bool:
    """TOTP 优先；否则尝试**恢复码**（单次消费成功才算通过）。

    恢复码不依赖服务端密钥 ⇒ **认证器丢失**时仍可关闭 MFA（防用户自锁）。
    （注意：若服务端密钥丢失，登录会在第一步即 fail-closed，恢复码无法用于登录——
    须由运维恢复 `DR_MFA_SECRET_KEY`。）
    """
    if _verify_totp_factor(factor, code):
        return True
    return bool(_store_call(store.consume_recovery_code, user_id, hash_recovery_code(code)))


@app.get("/api/auth/mfa")
def mfa_status(request: Request) -> dict:
    """当前用户的 MFA 状态（前端「会话与安全」页用）。"""
    user = _require_session_user(request)
    factor = _store_call(store.get_mfa_factor, user["user_id"])
    return {
        "available": secretbox.configured(),
        "enabled": factor is not None,
        "type": factor["type"] if factor else None,
        "confirmed_at": _iso(factor["confirmed_at"]) if factor else None,
        "recovery_codes_remaining": (
            _store_call(store.count_unused_recovery_codes, user["user_id"]) if factor else 0),
    }


@app.post("/api/auth/mfa/totp/begin")
def mfa_totp_begin(request: Request) -> dict:
    """开始绑定：生成新 secret（**仅本响应出现一次**）并暂存为未确认因子。"""
    _check_csrf(request)
    user = _require_session_user(request)
    _require_mfa_ready()
    if not MFA_ENROLL_LIMITER.allow(f"mfa-enroll:{user['user_id']}"):
        _audit("mfa_enroll_rate_limited", request=request, actor_user_id=user["user_id"])
        raise http_error("rate_limited", "MFA 操作过于频繁，稍后再试")
    if _store_call(store.get_mfa_factor, user["user_id"]) is not None:
        raise http_error("mfa_already_enabled", "已启用 MFA；如需换绑请先关闭")
    secret = new_totp_secret()
    factor_id = uuid.uuid4().hex[:12]
    _store_call(store.upsert_pending_mfa_factor, factor_id, user["user_id"],
                secretbox.encrypt(secret))
    _audit("mfa_enroll_started", request=request, actor_user_id=user["user_id"])
    encoded = totp_encode_secret(secret)
    return {"secret": encoded,
            "otpauth_uri": totp_provisioning_uri(encoded, account=user["email"],
                                                 issuer=MFA_ISSUER)}


@app.post("/api/auth/mfa/totp/confirm")
def mfa_totp_confirm(req: MfaCodeRequest, request: Request) -> dict:
    """确认绑定：校验一次 TOTP ⇒ 置 confirmed + 生成恢复码（明文**仅此一次**返回）。"""
    _check_csrf(request)
    user = _require_session_user(request)
    _require_mfa_ready()
    if not MFA_ENROLL_LIMITER.allow(f"mfa-enroll:{user['user_id']}"):
        _audit("mfa_enroll_rate_limited", request=request, actor_user_id=user["user_id"])
        raise http_error("rate_limited", "MFA 操作过于频繁，稍后再试")
    pending = _store_call(store.get_mfa_factor, user["user_id"], confirmed=False)
    if pending is None:
        raise http_error("mfa_not_started", "请先开始绑定（未找到待确认的因子）")
    if not _verify_totp_factor(pending, req.code):
        _audit("mfa_confirm_failed", request=request, actor_user_id=user["user_id"])
        raise http_error("mfa_code_invalid", "验证码不正确")
    if not _store_call(store.confirm_mfa_factor, user["user_id"], pending["factor_id"]):
        raise http_error("mfa_not_started", "待确认因子已失效，请重新绑定")
    codes = new_recovery_codes()
    _store_call(store.replace_recovery_codes, user["user_id"],
                [hash_recovery_code(code) for code in codes])
    _audit("mfa_enabled", request=request, actor_user_id=user["user_id"])
    return {"ok": True, "recovery_codes": codes}


@app.post("/api/auth/mfa/disable")
def mfa_disable(req: MfaDisableRequest, request: Request) -> dict:
    """关闭 MFA（需密码 + TOTP/恢复码；恢复码路径保证「密钥丢失」时仍可自救）。"""
    _check_csrf(request)
    user = _require_session_user(request)
    if not MFA_ENROLL_LIMITER.allow(f"mfa-enroll:{user['user_id']}"):
        _audit("mfa_enroll_rate_limited", request=request, actor_user_id=user["user_id"])
        raise http_error("rate_limited", "MFA 操作过于频繁，稍后再试")
    if not verify_password(user["password_hash"], req.password):
        _audit("mfa_disable_failed", request=request, actor_user_id=user["user_id"],
               detail={"reason": "bad_password"})
        raise http_error("invalid_credentials", "密码不正确")
    factor = _store_call(store.get_mfa_factor, user["user_id"])
    if factor is not None and not _verify_mfa_factor(user["user_id"], factor, req.code):
        _audit("mfa_disable_failed", request=request, actor_user_id=user["user_id"],
               detail={"reason": "bad_code"})
        raise http_error("mfa_code_invalid", "验证码或恢复码不正确")
    _store_call(store.delete_mfa_factors, user["user_id"])
    _store_call(store.replace_recovery_codes, user["user_id"], [])
    _audit("mfa_disabled", request=request, actor_user_id=user["user_id"])
    return {"ok": True}


@app.post("/api/auth/mfa/verify")
def mfa_verify(req: MfaCodeRequest, request: Request) -> dict:
    """登录第二步（step-up）：半程会话 + TOTP/恢复码 ⇒ 标记该会话已完成第二步。"""
    _check_csrf(request)
    user = _pending_session_user(request)
    if user is None:
        raise http_error("unauthenticated", "请先登录")
    if user.get("session_mfa_verified", True):
        return {"user": _public_user(user)}   # 幂等：已完成第二步
    factor = _store_call(store.get_mfa_factor, user["user_id"])
    if factor is None:
        # 因子已在别处移除（如已关闭 MFA）：直接完成，避免把自己锁死
        _store_call(store.set_session_mfa_verified, user["session_token_hash"], True)
        return {"user": _public_user(user)}
    if not MFA_VERIFY_LIMITER.allow(f"mfa-verify:{user['user_id']}"):
        _audit("mfa_verify_rate_limited", request=request, actor_user_id=user["user_id"])
        raise http_error("rate_limited", "验证码尝试过于频繁，稍后再试")
    if not _verify_mfa_factor(user["user_id"], factor, req.code):
        _audit("mfa_verify_failed", request=request, actor_user_id=user["user_id"])
        raise http_error("mfa_code_invalid", "验证码或恢复码不正确")
    _store_call(store.set_session_mfa_verified, user["session_token_hash"], True)
    _audit("mfa_verified", request=request, actor_user_id=user["user_id"])
    # 第二步完成后才判「新设备」：此时须排除本半程会话
    rows = [row for row in _store_call(store.list_sessions, user["user_id"])
            if row.get("token_hash") != user["session_token_hash"]]
    if is_new_device([device_key(row.get("user_agent"), row.get("ip")) for row in rows],
                     request.headers.get("user-agent"), _client_key(request)):
        _notify_new_login(user, request)
    return {"user": _public_user(user)}


# ------------------------------------------------------------------ Passkey：WebAuthn（需求 20 P1-2）


class WebAuthnRegisterCompleteRequest(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=128)
    credential: dict[str, Any] = Field(default_factory=dict)
    name: str = Field("", max_length=80)


class WebAuthnRegisterBeginRequest(BaseModel):
    """P0-2b：添加 Passkey 前必须重新输入当前密码（防会话劫持后持久化绑定）。"""

    password: str = Field(..., min_length=1, max_length=200)


class WebAuthnAuthenticateBeginRequest(BaseModel):
    email: str = Field("", max_length=254)


class WebAuthnAuthenticateCompleteRequest(BaseModel):
    challenge_id: str = Field(..., min_length=8, max_length=128)
    credential: dict[str, Any] = Field(default_factory=dict)


class WebAuthnDeleteRequest(BaseModel):
    password: str = Field(..., min_length=1, max_length=200)


def _require_webauthn_ready() -> None:
    if not passkeys.enabled():
        raise http_error("webauthn_unavailable", "Passkey 未启用（缺少 DR_WEBAUTHN_RP_ID / ORIGIN）")


def _passkey_view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "credential_id": passkeys.credential_id_b64(bytes(row["credential_id"])),
        "name": row.get("name") or "未命名通行密钥",
        "aaguid": row.get("aaguid"),
        "transports": list(row.get("transports") or []),
        "backup_state": bool(row.get("backup_state")),
        "device_type": row.get("device_type"),
        "created_at": _iso(row.get("created_at")),
        "last_used_at": _iso(row.get("last_used_at")),
    }


def _passkey_manage_rate_limit(user: dict[str, Any], request: Request) -> None:
    """P0-2：Passkey 管理（注册/删除）按用户限流，防挑战刷取与枚举。"""
    if not PASSKEY_MANAGE_LIMITER.allow(f"passkey-manage:{user['user_id']}"):
        _audit("passkey_manage_rate_limited", request=request, actor_user_id=user["user_id"])
        raise http_error("rate_limited", "Passkey 操作过于频繁，稍后再试")


def _passkey_rate_limit(request: Request, email_hash: Optional[str] = None) -> None:
    allowed = LOGIN_LIMITER.allow(f"passkey:{_client_key(request)}")
    if email_hash is not None:
        allowed = LOGIN_ACCOUNT_LIMITER.allow(f"passkey-acct:{email_hash}") and allowed
    if not allowed:
        _audit("passkey_rate_limited", request=request,
               detail={"email_hash": email_hash} if email_hash else None)
        raise http_error("rate_limited", "Passkey 请求过于频繁，稍后再试")


@app.get("/api/auth/webauthn")
def webauthn_status(request: Request) -> dict:
    """当前用户的 Passkey 列表（管理面板用；不返回公钥）。"""
    user = _require_session_user(request)
    rows = _store_call(store.list_webauthn_credentials, user["user_id"])
    return {
        "available": passkeys.enabled(),
        "rp_id": passkeys.rp_id(),
        "credentials": [_passkey_view(row) for row in rows],
    }


@app.post("/api/auth/webauthn/register/begin")
def webauthn_register_begin(req: WebAuthnRegisterBeginRequest, request: Request) -> dict:
    _check_csrf(request)
    user = _require_session_user(request)
    _passkey_manage_rate_limit(user, request)
    _require_webauthn_ready()
    if not verify_password(user["password_hash"], req.password):
        _audit("passkey_enroll_auth_failed", request=request, actor_user_id=user["user_id"])
        raise http_error("invalid_credentials", "密码不正确")
    existing = _store_call(store.list_webauthn_credentials, user["user_id"])
    challenge, options = passkeys.registration_options(
        user["user_id"], user["email"], existing)
    challenge_id = uuid.uuid4().hex
    _store_call(store.create_webauthn_challenge, challenge_id, user["user_id"],
                "register", challenge,
                datetime.now(UTC) + timedelta(seconds=passkeys.CHALLENGE_TTL_SECONDS))
    _audit("passkey_enroll_started", request=request, actor_user_id=user["user_id"])
    return {"challenge_id": challenge_id, "options": options}


@app.post("/api/auth/webauthn/register/complete")
def webauthn_register_complete(req: WebAuthnRegisterCompleteRequest,
                               request: Request) -> dict:
    _check_csrf(request)
    user = _require_session_user(request)
    _passkey_manage_rate_limit(user, request)
    _require_webauthn_ready()
    challenge_row = _store_call(store.consume_webauthn_challenge,
                                req.challenge_id, "register")
    if challenge_row is None or challenge_row.get("user_id") != user["user_id"]:
        raise http_error("webauthn_challenge_invalid", "注册挑战无效或已过期")
    try:
        verified = passkeys.verify_registration(
            req.credential, bytes(challenge_row["challenge"]))
    except Exception as exc:  # noqa: BLE001 — 客户端输入不可信，统一按校验失败处理
        _audit("passkey_register_failed", request=request,
               actor_user_id=user["user_id"],
               detail={"reason": type(exc).__name__})
        raise http_error("webauthn_registration_invalid", "Passkey 注册校验未通过") from exc
    name = (req.name or "").strip()[:64] or "通行密钥"
    try:
        store.create_webauthn_credential(
            bytes(verified.credential_id), user["user_id"],
            bytes(verified.credential_public_key), int(verified.sign_count),
            transports=passkeys.transports_from_payload(req.credential),
            aaguid=(verified.aaguid or None),
            backup_state=bool(verified.credential_backed_up),
            device_type=passkeys.device_type(verified.credential_device_type),
            name=name,
        )
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "sqlstate", None) == "23505":
            raise http_error("passkey_exists", "该 Passkey 已绑定") from exc
        raise http_error(
            "persistence_unavailable",
            f"保存 Passkey 失败：{type(exc).__name__}: {exc}"[:200],
        ) from exc
    _audit("passkey_registered", request=request, actor_user_id=user["user_id"],
           detail={"credential_id": passkeys.credential_id_b64(bytes(verified.credential_id))})
    rows = _store_call(store.list_webauthn_credentials, user["user_id"])
    row = next((item for item in rows
                if bytes(item["credential_id"]) == bytes(verified.credential_id)), None)
    return {"ok": True, "credential": _passkey_view(row) if row else None}


@app.post("/api/auth/webauthn/authenticate/begin")
def webauthn_authenticate_begin(req: WebAuthnAuthenticateBeginRequest,
                                request: Request) -> dict:
    """Passkey 登录第一步：邮箱可空（usernameless / 可发现凭据）。"""
    email = normalize_email(req.email)
    if email and not is_valid_email(email):
        raise http_error("invalid_request", "邮箱格式不合法")
    email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()[:16] if email else None
    _passkey_rate_limit(request, email_hash)
    user = _store_call(store.get_user_by_email, email) if email else None
    if user is not None and user.get("status") != "active":
        user = None
    credentials = (_store_call(store.list_webauthn_credentials, user["user_id"])
                   if user is not None else [])
    challenge, options = passkeys.authentication_options(credentials)
    challenge_id = uuid.uuid4().hex
    _store_call(store.create_webauthn_challenge, challenge_id,
                user["user_id"] if user is not None else None,
                "authenticate", challenge,
                datetime.now(UTC) + timedelta(seconds=passkeys.CHALLENGE_TTL_SECONDS))
    _audit("passkey_login_started", request=request,
           detail={"email_hash": email_hash} if email_hash else None)
    return {"challenge_id": challenge_id, "options": options}


@app.post("/api/auth/webauthn/authenticate/complete")
def webauthn_authenticate_complete(req: WebAuthnAuthenticateCompleteRequest,
                                   request: Request, response: Response) -> dict:
    _passkey_rate_limit(request)
    _require_webauthn_ready()
    try:
        credential_id = passkeys.credential_id_from_payload(req.credential)
    except ValueError as exc:
        raise http_error("webauthn_authentication_invalid", "Passkey 凭据无效") from exc
    credential = _store_call(store.get_webauthn_credential, credential_id)
    if credential is None:
        _audit("passkey_login_failed", request=request, detail={"reason": "not_found"})
        raise http_error("webauthn_authentication_invalid", "Passkey 登录校验未通过")
    user = _store_call(store.get_user, credential["user_id"])
    if user is None or user.get("status") != "active":
        _audit("passkey_login_failed", request=request, detail={"reason": "inactive"})
        raise http_error("webauthn_authentication_invalid", "Passkey 登录校验未通过")
    challenge_row = _store_call(store.consume_webauthn_challenge,
                                req.challenge_id, "authenticate")
    if challenge_row is None:
        raise http_error("webauthn_challenge_invalid", "登录挑战无效或已过期")
    bound_user_id = challenge_row.get("user_id")
    if bound_user_id is not None and bound_user_id != user["user_id"]:
        _audit("passkey_login_failed", request=request, actor_user_id=user["user_id"],
               detail={"reason": "user_mismatch"})
        raise http_error("webauthn_authentication_invalid", "Passkey 登录校验未通过")
    try:
        verified = passkeys.verify_authentication(
            req.credential, bytes(challenge_row["challenge"]),
            bytes(credential["public_key"]), int(credential["sign_count"]))
    except Exception as exc:  # noqa: BLE001
        _audit("passkey_login_failed", request=request, actor_user_id=user["user_id"],
               detail={"reason": type(exc).__name__})
        raise http_error("webauthn_authentication_invalid", "Passkey 登录校验未通过") from exc
    if bytes(verified.credential_id) != credential_id:
        _audit("passkey_login_failed", request=request, actor_user_id=user["user_id"],
               detail={"reason": "credential_mismatch"})
        raise http_error("webauthn_authentication_invalid", "Passkey 登录校验未通过")
    previous = int(credential["sign_count"])
    new_count = int(verified.new_sign_count)
    if (previous > 0 or new_count > 0) and new_count <= previous:
        # 克隆 / 重放信号：计数器必须严格递增（双方均为 0 = 认证器不提供计数器）
        _audit("passkey_clone_suspected", request=request, actor_user_id=user["user_id"],
               detail={"previous": previous, "new": new_count})
        raise http_error("webauthn_authentication_invalid", "Passkey 计数器异常，已拒绝登录")
    if not _store_call(store.bump_webauthn_sign_count, credential_id, previous, new_count):
        _audit("passkey_login_failed", request=request, actor_user_id=user["user_id"],
               detail={"reason": "sign_count_race"})
        raise http_error("webauthn_authentication_invalid", "Passkey 登录校验未通过")
    prior = _store_call(store.list_sessions, user["user_id"])
    new_device = is_new_device(
        [device_key(row.get("user_agent"), row.get("ip")) for row in prior],
        request.headers.get("user-agent"), _client_key(request))
    _store_call(store.touch_last_login, user["user_id"])
    # Passkey 本身是「持有 + 用户验证」的 MFA：登录成功即视为完成第二步。
    # 不额外要求 TOTP（否则 Passkey 会退化成第二因子而非替代登录方式）。
    _set_session_cookies(response, user["user_id"], request, mfa_verified=True)
    _audit("login_success", request=request, actor_user_id=user["user_id"],
           detail={"method": "passkey"})
    _audit("passkey_login_success", request=request, actor_user_id=user["user_id"],
           detail={"sign_count": new_count, "backup_state": bool(verified.credential_backed_up)})
    if new_device:
        _notify_new_login(user, request)
    return {"user": _public_user(user)}


@app.delete("/api/auth/webauthn/{credential_id}")
def webauthn_delete(credential_id: str, req: WebAuthnDeleteRequest,
                    request: Request) -> dict:
    _check_csrf(request)
    user = _require_session_user(request)
    _passkey_manage_rate_limit(user, request)
    _require_webauthn_ready()
    try:
        raw_id = passkeys.credential_id_from_payload({"rawId": credential_id})
    except ValueError as exc:
        raise http_error("passkey_not_found", "Passkey 不存在") from exc
    row = _store_call(store.get_webauthn_credential, raw_id)
    if row is None or row.get("user_id") != user["user_id"]:
        raise http_error("passkey_not_found", "Passkey 不存在")
    if not verify_password(user["password_hash"], req.password):
        _audit("passkey_delete_failed", request=request, actor_user_id=user["user_id"],
               detail={"reason": "bad_password"})
        raise http_error("invalid_credentials", "密码不正确")
    remaining = _store_call(store.count_webauthn_credentials, user["user_id"]) - 1
    _store_call(store.delete_webauthn_credential, user["user_id"], raw_id)
    _audit("passkey_deleted", request=request, actor_user_id=user["user_id"],
           detail={"credential_id": passkeys.credential_id_b64(raw_id),
                   "last_credential": remaining <= 0})
    return {"ok": True, "remaining": max(remaining, 0)}


# ------------------------------------------------------------------ 会话治理（P1-10）


class RevokeSessionRequest(BaseModel):
    password: Optional[str] = Field(None, max_length=200)


class ResetPasswordRequest(BaseModel):
    token: str = Field(..., min_length=10, max_length=256)
    new_password: str = Field(..., min_length=MIN_PASSWORD_LENGTH, max_length=200)


class ForgotPasswordRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)


#: 自助找回 token 有效期（分钟；与管理员 CLI create-reset-token 默认一致）
RESET_TOKEN_MINUTES = 30


def _require_session_user(request: Request) -> dict:
    user = _session_user(request)
    if user is None:
        raise http_error("unauthenticated", "请先登录")
    return user


@app.get("/api/auth/sessions")
def auth_sessions(request: Request) -> dict:
    """本用户活跃会话列表（P1-10）：对外只暴露 `session_id`，不泄露 token 摘要。"""
    user = _require_session_user(request)
    current = user.get("session_token_hash")
    rows = _store_call(store.list_sessions, user["user_id"])
    return {"sessions": [{
        "session_id": row["session_id"],
        "current": row["token_hash"] == current,
        "created_at": _iso(row["created_at"]),
        "last_seen_at": _iso(row["last_seen_at"]),
        "ip": row["ip"],
        "user_agent": row["user_agent"],
    } for row in rows]}


@app.delete("/api/auth/sessions/{session_id}")
def auth_revoke_session(session_id: str, req: RevokeSessionRequest, request: Request,
                        response: Response) -> dict:
    """终止一个会话（P1-10）；终止**其他**会话需重认证（ASVS 7.5.2）。"""
    user = _require_session_user(request)
    _check_csrf(request)
    is_current = user.get("session_id") == session_id
    if not is_current and (not req.password
                           or not verify_password(user["password_hash"], req.password)):
        _audit("session_revoke_denied", request=request, actor_user_id=user["user_id"],
               target_id=session_id)
        raise http_error("invalid_credentials", "终止其他会话需要输入当前密码")
    if not _store_call(store.revoke_session_by_id, user["user_id"], session_id):
        raise http_error("invalid_request", "会话不存在")
    _audit("session_revoked", request=request, actor_user_id=user["user_id"],
           target_id=session_id, detail={"current": is_current})
    if is_current:
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True}


@app.delete("/api/auth/sessions")
def auth_revoke_other_sessions(req: RevokeSessionRequest, request: Request) -> dict:
    """退出其他所有设备（P1-10）：需重认证；当前会话保留。"""
    user = _require_session_user(request)
    _check_csrf(request)
    if not req.password or not verify_password(user["password_hash"], req.password):
        _audit("session_revoke_denied", request=request, actor_user_id=user["user_id"],
               detail={"scope": "others"})
        raise http_error("invalid_credentials", "退出其他设备需要输入当前密码")
    revoked = _store_call(store.revoke_other_sessions, user["user_id"],
                          user["session_token_hash"])
    _audit("sessions_revoked_others", request=request, actor_user_id=user["user_id"],
           detail={"revoked": revoked})
    return {"ok": True, "revoked": revoked}


@app.post("/api/auth/reset")
def auth_reset_password(req: ResetPasswordRequest, request: Request) -> dict:
    """密码重置（P1-10）：管理员 CLI 发放一次性 token；成功后吊销全部会话。

    原子完成（消费 token + 改密 + 吊销会话同事务）；token 单次消费、30 分钟过期。
    """
    if not LOGIN_LIMITER.allow(f"reset:{_client_key(request)}"):
        raise http_error("rate_limited", "请求过于频繁，稍后再试")
    if store is None:
        raise http_error("persistence_unavailable", "账号功能需要任务库（DR_DATABASE_URL）")
    _reject_weak_password(req.new_password)
    user_id = _store_call(store.complete_password_reset, token_hash(req.token),
                          hash_password(req.new_password))
    if user_id is None:
        _audit("password_reset_failed", request=request)
        raise http_error("invalid_request", "重置链接无效或已过期，请重新申请")
    _audit("password_reset_completed", request=request, actor_user_id=user_id)
    return {"ok": True}


@app.post("/api/auth/forgot")
def auth_forgot_password(req: ForgotPasswordRequest, request: Request) -> dict:
    """自助找回（需求 24）：防枚举恒 200；投递失败留审计但不暴露。

    限流双维度（IP + 邮箱哈希）；冷却期内不重发；邮件通道未配置 → 503 结构化降级
    （在用户查询**之前**判断，避免成为存在性预言机）。
    """
    email = normalize_email(req.email)
    email_hash = hashlib.sha256(email.encode("utf-8")).hexdigest()[:16]
    if not is_valid_email(email):
        raise http_error("invalid_request", "邮箱格式不正确")
    if (not LOGIN_LIMITER.allow(f"forgot:{_client_key(request)}")
            or not LOGIN_ACCOUNT_LIMITER.allow(f"forgot-acct:{email_hash}")):
        _audit("forgot_rate_limited", request=request, detail={"email_hash": email_hash})
        raise http_error("rate_limited", "请求过于频繁，稍后再试")
    if store is None:
        raise http_error("persistence_unavailable", "账号功能需要任务库（DR_DATABASE_URL）")
    mailer = get_mailer()
    if mailer is None:
        raise http_error("mail_unavailable", "邮件通道未配置，请联系管理员重置密码")
    user = _store_call(store.get_user_by_email, email)
    if user is not None and user["status"] == "active":
        cooldown = int(config.mail.reset_cooldown_seconds)
        if not _store_call(store.has_recent_password_reset, user["user_id"], cooldown):
            token = new_token()
            expires_at = datetime.now(UTC) + timedelta(minutes=RESET_TOKEN_MINUTES)
            _store_call(store.create_password_reset, token_hash(token), user["user_id"],
                        expires_at, created_by="system")
            link = f"{config.mail.base_url.rstrip('/')}/#reset={token}"
            subject, text, html = password_reset_email(link, minutes=RESET_TOKEN_MINUTES)
            try:
                mailer.send(user["email"], subject, text, html)
                _audit("forgot_requested", request=request, detail={"email_hash": email_hash})
            except Exception as exc:  # noqa: BLE001 —— 投递失败不向匿名请求方暴露
                _audit("mail_send_failed", request=request,
                       detail={"email_hash": email_hash, "error": type(exc).__name__})
    return {"ok": True}


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
    _reject_weak_password(req.new_password, email=user.get("email", ""))
    _store_call(store.update_password, user["user_id"], hash_password(req.new_password))
    revoked = _store_call(store.revoke_user_sessions, user["user_id"])
    _set_session_cookies(response, user["user_id"], request)
    _audit("password_changed", request=request, actor_user_id=user["user_id"],
           detail={"revoked_sessions": revoked})
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
    _audit("account_deletion_requested", request=request, actor_user_id=user_id,
           target_type="deletion_request", target_id=request_id)

    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")
    return {"ok": True, "deletion_request_id": request_id, "rag_cleanup": "pending"}


def start_research(*, topic: str, instructions: str = "", profile_name: str,
                   user_id: Optional[str], idempotency_key: Optional[str] = None,
                   ignored: Optional[dict] = None,
                   request: Optional[Request] = None) -> str:
    """研究任务创建**服务函数**（需求 37 M2 / TBD-6）：准入 / 配额 / 审核 / 注入 / 幂等 单一口径。

    **唯一创建链路**：HTTP `POST /api/research` 与 MCP `deep_research` 工具都经本函数；
    MCP 不得自建第二条创建链路（配额/审核/幂等一处不落）。
    `request` 仅用于审计元数据（MCP 路径为 None）；失败一律抛 `ApiError`
    （HTTP 层 `to_http()` 转换；MCP 层转工具错误）。
    """
    profile = resolve_profile(profile_name)
    if profile is None:
        raise ApiError(
            "invalid_request",
            f"未知运行档位：{profile_name}",
            detail=f"known={sorted(PROFILES)}",
        )
    fingerprint = _request_fingerprint(topic, instructions, profile.name)
    # 幂等命中先于配额闸：重复提交是同一个逻辑请求，不应被日限额/预算拒绝。
    existing = _idempotent_existing(user_id, idempotency_key, fingerprint)
    if existing is not None:
        return existing["run_id"]
    _enforce_quotas(user_id)
    if not topic.strip():
        raise ApiError("empty_topic", "topic 不能为空")
    # P7-A / P2-5a / P0-3：输入侧预检（provider 化；命中即拒绝并留审核记录，
    # provider 不可用时按降级策略 fail-closed，不再静默放行）
    input_decision = evaluate_input(f"{topic}\n{instructions}")
    blocked_terms = list(input_decision.matches)
    if blocked_terms:
        if store is not None:
            _store_call(store.record_moderation, "input_blocked", user_id=user_id,
                        detail={"matches": blocked_terms[:10],
                                "provider": input_decision.provider,
                                "decision_id": input_decision.decision_id})
        _audit("input_blocked", request=request, actor_user_id=user_id,
               detail={"matches": blocked_terms[:5], "provider": input_decision.provider})
        raise ApiError(
            "content_blocked", "输入包含不允许的内容",
            detail=f"matches={blocked_terms[:5]}",
        )
    if input_decision.degraded and input_decision.decision != "allow":
        if store is not None:
            _store_call(store.record_moderation, "input_blocked", user_id=user_id,
                        detail={"moderation_degraded": True,
                                "policy": input_decision.policy,
                                "failure_reason": input_decision.failure_reason,
                                "decision_id": input_decision.decision_id})
        _audit("moderation_unavailable", request=request, actor_user_id=user_id,
               detail={"policy": input_decision.policy,
                       "failure_reason": input_decision.failure_reason})
        raise ApiError(
            "moderation_unavailable",
            "内容安全服务暂不可用，请稍后再试",
            detail=f"policy={input_decision.policy}",
        )
    # P2-1a：注入模式预检（窄口径：显式指令覆盖 / 系统提示索取；命中即拒绝并留痕）
    injection_hits = scan_injection(f"{topic}\n{instructions}")
    if injection_hits:
        if store is not None:
            _store_call(store.record_moderation, "input_blocked", user_id=user_id,
                        detail={"injection": injection_hits})
        _audit("input_injection_blocked", request=request, actor_user_id=user_id,
               detail={"patterns": injection_hits})
        raise ApiError(
            "content_blocked", "输入包含疑似指令注入内容",
            detail=f"patterns={injection_hits}",
        )
    # 搜索引擎由服务端配置固定（需求 10 §3.1 第 13 项）；客户端字段在 HTTP 层忽略。
    if EXECUTION_MODE == "queue":
        return _start_queued(
            topic=topic, instructions=instructions, idempotency_key=idempotency_key,
            user_id=user_id, profile=profile, ignored=ignored or {}, fingerprint=fingerprint)
    return manager.start(
        topic, instructions, profile,
        idempotency_key, user_id=user_id,
        budget_limit_cny=_effective_run_budget(profile),
        ignored_overrides=ignored or {},
        admission=_admission_limits(_effective_run_budget(profile)),
        request_hash=fingerprint)


@app.post("/api/research", response_model=StartResponse)
def start(req: StartRequest, request: Request) -> StartResponse:
    """提交研究（HTTP 薄层）：鉴权 / CSRF / 提交限流 → 服务函数（与 MCP 同口径）。"""
    user_id = _require_user(request)
    _check_csrf(request)
    if not SUBMIT_LIMITER.allow(f"submit:{user_id or _client_key(request)}"):
        raise http_error("rate_limited", "提交过于频繁，稍后再试")
    # P0 profile 固化：档位由服务端解析；请求体底层参数（旧字段）一律忽略并留痕。
    try:
        run_id = start_research(
            topic=req.topic, instructions=req.instructions, profile_name=req.profile,
            user_id=user_id, idempotency_key=req.idempotency_key,
            ignored=_ignored_overrides(req), request=request)
    except ApiError as exc:  # 并发上限 / 持久化不可用 / 配额 / 审核阻断等
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
    q: Optional[str] = Query(None, max_length=100, description="关键词（topic 模糊搜索）"),
    archived: bool = Query(False, description="true=只看归档；默认排除归档"),
) -> dict:
    """历史任务列表（P2-C / 需求 22）：读任务库；未配置库时结构化 503。

    P4-A：鉴权开启时只返回当前用户的 run。
    需求 22：`q` 关键词搜索 + `archived` 归档过滤 + 置顶优先排序。
    """
    if store is None:
        raise http_error(
            "persistence_unavailable",
            "未配置任务库（DR_DATABASE_URL），无法列出历史任务",
        )
    statuses = [item.strip() for item in status.split(",") if item.strip()] if status else None
    keyword = (q or "").strip() or None
    rows = _store_call(store.list_runs, user_id=_require_user(request),
                       limit=limit, offset=offset, statuses=statuses,
                       q=keyword, archived=archived)
    return {"runs": [_run_brief(row) for row in rows], "limit": limit, "offset": offset}


#: 需求 22：可重试的终态（失败 / 失联 / 超时）；成功与取消不重试
RETRYABLE_STATUSES = ("FAILED", "LOST", "TIMED_OUT")


class RenameRunRequest(BaseModel):
    topic: str = Field(..., max_length=MAX_TOPIC_LENGTH)


def _owned_run_row(request: Request, run_id: str) -> tuple[Optional[str], dict]:
    """需求 22 写操作共用前置：任务库可用 + 归属校验（非本人 404）+ CSRF + 取行。"""
    if store is None:
        raise http_error("persistence_unavailable", "未配置任务库（DR_DATABASE_URL）")
    owner = _authorize_run(request, run_id)
    _check_csrf(request)
    row = _store_call(store.get_run, run_id)
    if row is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    return owner, row


@app.patch("/api/runs/{run_id}")
def rename_run(run_id: str, req: RenameRunRequest, request: Request) -> dict:
    """需求 22：重命名（仅本人）。导出文件名随 topic 变化属预期行为，审计留痕。"""
    owner, row = _owned_run_row(request, run_id)
    topic = req.topic.strip()
    if not topic:
        raise http_error("empty_topic", "topic 不能为空")
    if not _store_call(store.update_topic, run_id, topic, user_id=owner):
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    _audit("run_renamed", request=request, actor_user_id=owner, target_type="run",
           target_id=run_id, detail={"old_topic": row["topic"][:200], "new_topic": topic[:200]})
    return {"ok": True, "run_id": run_id, "topic": topic}


@app.post("/api/runs/{run_id}/pin")
def pin_run(run_id: str, request: Request) -> dict:
    """需求 22：置顶（幂等）。"""
    owner, _ = _owned_run_row(request, run_id)
    _store_call(store.set_pinned, run_id, True, user_id=owner)
    _audit("run_pinned", request=request, actor_user_id=owner, target_type="run", target_id=run_id)
    return {"ok": True, "run_id": run_id, "pinned": True}


@app.post("/api/runs/{run_id}/unpin")
def unpin_run(run_id: str, request: Request) -> dict:
    """需求 22：取消置顶（幂等）。"""
    owner, _ = _owned_run_row(request, run_id)
    _store_call(store.set_pinned, run_id, False, user_id=owner)
    _audit("run_unpinned", request=request, actor_user_id=owner, target_type="run", target_id=run_id)
    return {"ok": True, "run_id": run_id, "pinned": False}


@app.post("/api/runs/{run_id}/archive")
def archive_run(run_id: str, request: Request) -> dict:
    """需求 22：软归档（仅影响默认列表可见性；进行中任务 409）。

    需求 36：等待人类输入（AWAITING_*）也算「进行中」，不得归档。
    """
    owner, row = _owned_run_row(request, run_id)
    if row["status"] in OPEN_STATUSES:
        raise http_error("run_active", "进行中的任务不能归档", detail=f"status={row['status']}")
    _store_call(store.set_archived, run_id, True, user_id=owner)
    _audit("run_archived", request=request, actor_user_id=owner, target_type="run", target_id=run_id)
    return {"ok": True, "run_id": run_id, "archived": True}


@app.post("/api/runs/{run_id}/unarchive")
def unarchive_run(run_id: str, request: Request) -> dict:
    """需求 22：取消归档（幂等）。"""
    owner, _ = _owned_run_row(request, run_id)
    _store_call(store.set_archived, run_id, False, user_id=owner)
    _audit("run_unarchived", request=request, actor_user_id=owner, target_type="run", target_id=run_id)
    return {"ok": True, "run_id": run_id, "archived": False}


@app.post("/api/runs/{run_id}/retry", response_model=StartResponse)
def retry_run(run_id: str, request: Request) -> StartResponse:
    """需求 22：失败任务一键重试 —— 复制原 request 快照创建**新 run**（`retry_of` 血缘）。

    幂等键固定 `retry-{原 run_id}`：重复点击返回同一个新 run；配额 / 预算照常走准入事务。
    仅终态失败（FAILED / LOST / TIMED_OUT）可重试；其余 409。
    """
    if store is None:
        raise http_error("persistence_unavailable", "未配置任务库（DR_DATABASE_URL）")
    owner = _authorize_run(request, run_id)
    _check_csrf(request)
    if not SUBMIT_LIMITER.allow(f"submit:{owner or _client_key(request)}"):
        raise http_error("rate_limited", "提交过于频繁，稍后再试")
    row = _store_call(store.get_run, run_id)
    if row is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    if row["status"] not in RETRYABLE_STATUSES:
        raise http_error(
            "run_not_retryable",
            "仅失败 / 失联 / 超时的任务可重试",
            detail=f"status={row['status']}",
        )
    payload = row.get("request") or {}
    profile_name = (payload.get("profile") or {}).get("name") or DEFAULT_PROFILE
    profile = resolve_profile(profile_name) or resolve_profile(DEFAULT_PROFILE)
    topic = row["topic"]
    instructions = payload.get("instructions") or ""
    idempotency_key = f"retry-{run_id}"
    ignored = payload.get("ignored_overrides") or {}
    fingerprint = _request_fingerprint(topic, instructions, profile.name)
    try:
        if EXECUTION_MODE == "queue":
            new_run_id = _start_queued(
                topic=topic, instructions=instructions, idempotency_key=idempotency_key,
                user_id=owner, profile=profile, ignored=ignored, fingerprint=fingerprint,
                retry_of=run_id)
        else:
            new_run_id = manager.start(
                topic, instructions, profile,
                idempotency_key, user_id=owner,
                budget_limit_cny=_effective_run_budget(profile),
                ignored_overrides=ignored,
                admission=_admission_limits(_effective_run_budget(profile)),
                request_hash=fingerprint, retry_of=run_id)
    except ApiError as exc:
        raise exc.to_http() from exc
    _audit("run_retried", request=request, actor_user_id=owner, target_type="run",
           target_id=run_id, detail={"new_run_id": new_run_id})
    return StartResponse(run_id=new_run_id)


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
def metrics_endpoint(request: Request) -> dict:
    """运行指标（P8-A）：进程内计数 + 任务库实时聚合；**不含任何用户内容 / PII**。

    P0-9：应用层鉴权 —— 配置 `DR_OPS_TOKEN` 时要求 `X-Ops-Token`；staging/生产
    未配置 token 一律 401（反代限制来源只作纵深）。多实例下进程内计数只代表单实例。
    """
    _require_ops(request)
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
        # P1-3：活跃 worker 数（心跳新鲜度窗口见 DR_WORKER_HEARTBEAT_MAX_AGE_SECONDS）
        "workers_live": (_store_call(store.count_live_workers,
                                     within_seconds=WORKER_HEARTBEAT_MAX_AGE_SECONDS)
                         if store is not None else None),
    })
    return snapshot


@app.get("/api/ops/alerts")
def ops_alerts(request: Request) -> dict:
    """告警判定（P8-A / P2-6）：按阈值评估当前指标；**外送**由 Worker 统一走 webhook（见 alerts.py）。

    P0-9：与 `/api/metrics` 同一应用层鉴权（DR_OPS_TOKEN / fail-closed）。
    """
    _require_ops(request)
    queue_depth = _queue_depth()
    stale = _store_call(store.count_stale_leases) if store is not None else 0
    monthly = _store_call(store.month_cost_cny) if store is not None else 0.0
    deletions = _store_call(store.count_deletions_by_status) if store is not None else {}
    workers_live = (
        _store_call(store.count_live_workers,
                    within_seconds=WORKER_HEARTBEAT_MAX_AGE_SECONDS)
        if store is not None else None
    )
    overdue_appeals = (
        _store_call(store.count_appeals_overdue, datetime.now(UTC))
        if store is not None else 0
    )
    alerts = collect_alerts(
        thresholds={
            "http_5xx_rate_pct": ALERT_5XX_RATE_PCT,
            "queue_depth": ALERT_QUEUE_DEPTH,
            "stale_runs": ALERT_STALE_RUNS,
            "monthly_pct": ALERT_MONTHLY_PCT,
        },
        http=METRICS.snapshot()["http"],
        queue_depth=queue_depth,
        stale_leases=stale,
        deletions=deletions,
        month_cost=monthly,
        month_budget=MONTHLY_BUDGET_CNY,
        workers_live=workers_live,
        execution_mode=EXECUTION_MODE,
        overdue_appeals=overdue_appeals,
    )
    return {"ok": True, "alert_count": len(alerts), "alerts": alerts,
            "webhook_configured": bool(alert_webhook_url())}


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
        if (row or {}).get("moderation_status") not in ("flagged", "blocked", "under_review"):
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
    row = _store_call(submit_appeal, store, user_id=user_id,
                      message=req.message[:MAX_APPEAL_LENGTH], run_id=req.run_id)
    return {"ok": True, "appeal_id": row["appeal_id"], "status": row["status"],
            "sla_due_at": row["sla_due_at"].isoformat() if row.get("sla_due_at") else None}


@app.get("/api/moderation/appeals")
def my_appeals(request: Request) -> dict:
    """本人申诉与复核状态（P2-5b）；鉴权关闭时（本地/演示）返回全部最近记录。"""
    if store is None:
        return {"ok": True, "appeals": []}
    user_id = _require_user(request) if AUTH_REQUIRED else None
    rows = _store_call(store.list_appeals, user_id=user_id, limit=50)

    def _iso(value) -> Optional[str]:
        return value.isoformat() if value is not None else None

    return {"ok": True, "appeals": [
        {"appeal_id": row["appeal_id"], "run_id": row.get("run_id"),
         "status": row["status"], "message": row["message"],
         "decision_note": row.get("decision_note"),
         "sla_due_at": _iso(row.get("sla_due_at")),
         "decided_at": _iso(row.get("decided_at")),
         "created_at": _iso(row.get("created_at"))}
        for row in rows
    ]}


class FeedbackRequest(BaseModel):
    category: str = Field(..., pattern="^(bug|idea|other)$")
    message: str = Field(..., min_length=10, max_length=2000)
    contact: Optional[str] = Field(None, max_length=200)
    page: Optional[str] = Field(None, max_length=64)


@app.post("/api/feedback")
def submit_feedback(req: FeedbackRequest, request: Request) -> dict:
    """站内产品反馈（需求 25）：登录 + 限流 + 审计（审计不含正文，只记长度）。"""
    user_id = _require_user(request)
    _check_csrf(request)
    if not SUBMIT_LIMITER.allow(f"feedback:{user_id or _client_key(request)}"):
        raise http_error("rate_limited", "反馈提交过于频繁，稍后再试")
    if store is None:
        raise http_error("persistence_unavailable", "反馈需要任务库（DR_DATABASE_URL）")
    feedback_id = uuid.uuid4().hex[:12]
    _store_call(store.create_feedback, feedback_id, user_id,
                category=req.category, message=req.message.strip(),
                contact=(req.contact or "").strip() or None, page=req.page,
                request_id=getattr(request.state, "request_id", None))
    _audit("feedback_submitted", request=request, actor_user_id=user_id,
           target_id=feedback_id,
           detail={"category": req.category, "page": req.page,
                   "message_chars": len(req.message)})
    return {"ok": True, "feedback_id": feedback_id}


@app.get("/api/help/faq")
def help_faq() -> dict:
    """帮助中心 FAQ（需求 25）：以仓库 `docs/help/faq.md` 为唯一来源。"""
    path = HELP_DIR / "faq.md"
    if not path.is_file():
        raise http_error("help_unavailable", "帮助文档尚未准备（请联系管理员）")
    return {"markdown": path.read_text(encoding="utf-8")}


@app.get("/api/legal/{doc}")
def legal_document(doc: str) -> dict:
    """隐私政策 / 用户协议（P7-A）：以仓库 `docs/legal/` 为唯一来源。"""
    files = {"privacy": "privacy-policy.md", "terms": "terms-of-service.md"}
    if doc not in files:
        raise http_error("invalid_request", "未知文档", detail=f"known={sorted(files)}")
    path = LEGAL_DIR / files[doc]
    if not path.is_file():
        raise http_error("invalid_request", "文档尚未准备（请联系管理员）")
    return {"doc": doc, "markdown": path.read_text(encoding="utf-8"),
            "version": _legal_version(path)}


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
            # P0-8b / P0-11：登记后由 Worker 异步处理。去重走**数据库唯一索引**
            # （`ON CONFLICT ... DO NOTHING`，见 store.create_ingestion + 0017）：
            # 并发上传不可能重复插入，冲突方回查既有记录并删除自己的隔离区文件。
            ingestion_id = uuid.uuid4().hex[:12]
            try:
                created = store.create_ingestion(
                    ingestion_id, doc_id, user_id=user_id, source=filename,
                    sha256=digest, size_bytes=size, stored_name=stored_name)
            except Exception as exc:  # noqa: BLE001
                os.remove(path)
                raise http_error(
                    "persistence_unavailable",
                    f"摄取登记失败：{type(exc).__name__}: {exc}"[:200],
                ) from exc
            if created is None:
                existing = _store_call(store.find_ingestion_by_doc, user_id, doc_id)
                os.remove(path)
                if existing is None:
                    raise http_error("persistence_unavailable", "摄取登记冲突，请重试")
                return JSONResponse(status_code=202, content={
                    "ingestion_id": existing["ingestion_id"], "doc_id": doc_id,
                    "source": existing["source"], "status": existing["status"],
                })
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
        "task": row.get("task") or "ingest",
        "chunks": row["chunks"],
        "attempts": row["attempts"],
        "scan_status": row["scan_status"],
        "active_generation": row.get("active_generation"),
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
    # 需求 23：三层 PG 数据一并删除；递增修订号使检索缓存失效（删除后不可命中）
    _store_call(store.delete_rag_layers, doc_id)
    _store_call(store.bump_rag_revision)
    return {"ok": True, "doc_id": doc_id}


def _rag_docs_from_qdrant(user_id: Optional[str]) -> dict:
    """本地零依赖回落：无任务库时从 Qdrant 聚合（无台账字段）。"""
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


@app.get("/api/rag/docs")
def rag_docs(request: Request) -> dict:
    """知识库文档清单（需求 23：**PG 台账为准**；本地零依赖回落 Qdrant 聚合）。

    返回状态 / 大小 / 块数 / 失败原因 / 活动版本 / 展示名 / 标签 —— 上传后不再是黑盒。
    """
    user_id = _require_user(request)
    if store is None:
        return _rag_docs_from_qdrant(user_id)
    rows = _store_call(store.list_rag_docs, user_id)
    return {"docs": [
        {
            "doc_id": row["doc_id"],
            "source": row["source"],
            "display_name": row.get("display_name"),
            "tags": list(row.get("tags") or []),
            "status": row["status"],
            "chunks": row.get("chunks") or 0,
            "size_bytes": row.get("size_bytes"),
            "created_at": _iso(row.get("created_at")),
            "error": row.get("last_error"),
            "active_generation": row.get("active_generation"),
        }
        for row in rows
    ]}


class RagDocMetaRequest(BaseModel):
    doc_id: str = Field(..., max_length=128)
    display_name: Optional[str] = Field(None, max_length=120)
    tags: Optional[list[str]] = Field(None, max_length=20)


class RagRebuildRequest(BaseModel):
    doc_id: str = Field(..., max_length=128)


def _rag_owned_doc(request: Request, doc_id: str) -> tuple[Optional[str], dict]:
    """需求 23 写操作前置：任务库 + 归属（非本人 404）+ CSRF + 台账行。"""
    if store is None:
        raise http_error("persistence_unavailable", "知识库管理需要任务库（DR_DATABASE_URL）")
    user_id = _require_user(request)
    _check_csrf(request)
    row = _store_call(store.get_rag_doc_for_user, doc_id, user_id)
    if row is None:
        raise http_error("ingestion_not_found", f"文档不存在：{doc_id}")
    return user_id, row


@app.get("/api/rag/docs/{doc_id}/chunks")
def rag_doc_chunks(doc_id: str, request: Request,
                   generation: Optional[int] = Query(None, ge=1),
                   offset: int = Query(0, ge=0),
                   limit: int = Query(20, ge=1, le=100)) -> dict:
    """分块预览（需求 23）：默认活动版本；locator 供引用回溯定位。"""
    if store is None:
        raise http_error("persistence_unavailable", "分块预览需要任务库（DR_DATABASE_URL）")
    user_id = _require_user(request)
    row = _store_call(store.get_rag_doc_for_user, doc_id, user_id)
    if row is None:
        raise http_error("ingestion_not_found", f"文档不存在：{doc_id}")
    active = row.get("active_generation")
    target = generation or active
    if target is None:
        return {"doc_id": doc_id, "generation": None, "active_generation": active,
                "total": 0, "chunks": []}
    chunks, total = _store_call(store.list_rag_chunks, doc_id, int(target),
                                offset=offset, limit=limit)
    return {
        "doc_id": doc_id,
        "generation": int(target),
        "active_generation": active,
        "total": total,
        "chunks": [
            {"chunk_index": row["chunk_index"], "chunk_id": row["chunk_id"],
             "text": row["text"], "locator": row.get("locator") or {}}
            for row in chunks
        ],
    }


@app.patch("/api/rag/docs")
def rag_update_doc(req: RagDocMetaRequest, request: Request) -> dict:
    """重命名 / 标签（需求 23）；`display_name` 为空字符串非法（不传字段 = 不变）。"""
    user_id, _ = _rag_owned_doc(request, req.doc_id)
    display_name = req.display_name.strip() if req.display_name is not None else None
    if display_name == "":
        raise http_error("invalid_request", "display_name 不能为空字符串")
    tags: Optional[list[str]] = None
    if req.tags is not None:
        cleaned = [tag.strip() for tag in req.tags if tag.strip()]
        if any(len(tag) > 32 for tag in cleaned):
            raise http_error("invalid_request", "单个标签不超过 32 字")
        tags = cleaned
    ok = _store_call(store.update_rag_doc_meta, req.doc_id, user_id,
                     display_name=display_name, tags=tags)
    if not ok:
        raise http_error("ingestion_not_found", f"文档不存在：{req.doc_id}")
    _audit("rag_doc_updated", request=request, actor_user_id=user_id,
           target_type="rag_doc", target_id=req.doc_id,
           detail={"has_display_name": display_name is not None,
                   "tag_count": len(tags) if tags is not None else None})
    return {"ok": True, "doc_id": req.doc_id}


def _rag_requeue(request: Request, doc_id: str, task: str) -> dict:
    """重建类操作共用：状态校验 + 快照前置 + 入队（复用租约/重试机制）。"""
    user_id, row = _rag_owned_doc(request, doc_id)
    if not RAG_UPLOAD_LIMITER.allow(f"rag-rebuild:{user_id or _client_key(request)}"):
        raise http_error("rate_limited", "操作过于频繁，稍后再试")
    if row["status"] != "ready":
        raise http_error("rag_not_ready", "文档当前状态不允许重建（处理中或已失败）",
                         detail=f"status={row['status']}")
    if task == "rechunk" and _store_call(store.count_parse_snapshot, doc_id) == 0:
        raise http_error("rag_no_snapshot",
                         "该文档没有解析快照（历史文档），请重新上传后再操作")
    if not _store_call(store.requeue_ingestion_for_task, row["ingestion_id"], task):
        raise http_error("rag_not_ready", "文档当前状态不允许重建",
                         detail=f"status={row['status']}")
    _audit(f"rag_{task}_requested", request=request, actor_user_id=user_id,
           target_type="rag_doc", target_id=doc_id, detail={"task": task})
    return {"ok": True, "doc_id": doc_id, "ingestion_id": row["ingestion_id"], "task": task}


@app.post("/api/rag/docs/rechunk")
def rag_rechunk(req: RagRebuildRequest, request: Request) -> dict:
    """重新分块（需求 23）：解析快照 → 分块 v2 → 新版本切换（无不可检索窗口）。"""
    return _rag_requeue(request, req.doc_id, "rechunk")


@app.post("/api/rag/docs/reembed")
def rag_reembed(req: RagRebuildRequest, request: Request) -> dict:
    """重新嵌入（需求 23）：同分块换向量 → 新版本切换。"""
    return _rag_requeue(request, req.doc_id, "reembed")


@app.get("/api/rag/usage")
def rag_usage(request: Request) -> dict:
    """知识库用量（需求 23）：已用字节 + 可选总配额（`DR_RAG_TOTAL_MB`，0=不限）。"""
    user_id = _require_user(request)
    used = _store_call(store.rag_usage_bytes, user_id) if store is not None else 0
    quota = int(RAG_TOTAL_MB * 1024 * 1024) if RAG_TOTAL_MB and RAG_TOTAL_MB > 0 else None
    return {"used_bytes": used, "quota_bytes": quota}


@app.post("/api/research/{run_id}/cancel")
def cancel(run_id: str, request: Request) -> dict:
    """立即返回（契约 C1：前端点取消后不必等后端确认就显示「正在停止」）。

    内存中没有该 run 时回落任务库（P2-C）：只落取消请求，执行者已不在 ⇒ 无实际执行可停。
    P4-A：鉴权开启时只允许取消自己的 run。
    需求 36：等待态任务同样可在库里直接终局（无执行者，不进入节点边界路径）。
    """
    _authorize_run(request, run_id)
    _check_csrf(request)
    if not manager.cancel(run_id):
        if store is None or _store_call(store.get_run, run_id) is None:
            raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
        _store_call(store.request_cancel, run_id)
    return {"ok": True}


class InteractionAnswerRequest(BaseModel):
    """需求 36：回答等待中的 HITL 交互（澄清 reply / 审批 approve|revise）。"""

    interaction_id: str = Field(..., min_length=1, max_length=64,
                                description="来自交互卡片（或快照 interaction）的稳定 ID")
    action: str = Field(..., min_length=1, max_length=16,
                        description="clarification: reply；plan_approval: approve | revise")
    text: str = Field(default="", max_length=2001,
                      description="澄清回答 / 修改计划的反馈（上限由 hitl.validate_answer 校验）")


@app.post("/api/research/{run_id}/interaction")
def answer_interaction(run_id: str, req: InteractionAnswerRequest, request: Request) -> dict:
    """回答等待中的 HITL 交互（需求 36 §12.2 恢复事务）。

    - 校验：所有权 + CSRF + 当前状态为等待态 + action/text 合法（`hitl.validate_answer`）；
    - 幂等：`interaction_id` 即幂等键——重复提交返回既有结果（`already_answered`），不重复写事件；
    - 冲突：旧卡片 / 已换代 ⇒ 409 `interaction_conflict`；非等待态 ⇒ 409 `interaction_not_pending`；
    - 成功后 run 回到 `QUEUED` 并投递 Redis 唤醒（可丢，PG 轮询兜底；§12.2）。
    """
    _authorize_run(request, run_id)
    _check_csrf(request)
    if store is None:
        raise http_error("persistence_unavailable", "回答交互需要任务库（DR_DATABASE_URL）")
    row = _store_call(store.get_run, run_id)
    if row is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    kind = KIND_BY_STATUS.get(row["status"])
    if kind is None:
        raise http_error(
            "interaction_not_pending", "该任务当前不在等待回答状态",
            detail=f"status={row['status']}")
    invalid = validate_answer(kind, req.action, req.text)
    if invalid:
        raise http_error("invalid_request", invalid)
    result = _store_call(
        store.answer_interaction,
        run_id,
        interaction_id=req.interaction_id,
        answer=build_resume_payload(kind, action=req.action, text=req.text),
        event_type=ANSWER_EVENT_BY_KIND[kind],
        event_payload=build_answer_event_payload(
            kind, interaction_id=req.interaction_id,
            round_no=int(row.get("interaction_round") or 1),
            action=req.action, text=req.text),
    )
    outcome = result.get("outcome")
    if outcome == "conflict":
        if result.get("reason") == "stale_interaction":
            raise http_error(
                "interaction_conflict",
                "交互卡片已过期或已被新的一轮替代：刷新后按最新卡片重新回答",
                detail=f"current={result.get('current_interaction_id')}")
        raise http_error(
            "interaction_not_pending", "该任务当前不在等待回答状态",
            detail=f"status={result.get('status')}")
    if outcome == "accepted" and queue is not None:
        try:
            queue.enqueue(run_id)  # 唤醒信号：丢失由 PG 轮询兜底（P0-2）
        except Exception:  # noqa: BLE001 —— 信号失败不影响任务正确性
            pass
    _audit("interaction_answered", request=request,
           actor_user_id=(_session_user(request) or {}).get("user_id"),
           target_type="run", target_id=run_id,
           detail={"kind": kind, "action": req.action, "outcome": outcome})
    return {"ok": True, "outcome": outcome, "status": result.get("status"), "kind": kind}


class FollowupRequest(BaseModel):
    """需求 36 H3：报告后追问（不重搜）。"""

    question: str = Field(..., min_length=1, max_length=followup_mod.MAX_QUESTION_CHARS)


@app.post("/api/research/{run_id}/follow-up")
def follow_up(run_id: str, req: FollowupRequest, request: Request) -> dict:
    """报告后追问（需求 36 H3 / §12.3）：证据池 grounding + 服务端零模型校验。

    - 前置：功能开关 + 所有权 + CSRF + 限流 + run 终局且有报告 + 输出 gate（flagged 阻断）；
    - 上下文只来自本 run 的 `report_md` + `evidence_manifest`（**不触发检索/图/新 run**）；
    - 引用 ID 逐字校验证据池（不在池 ⇒ `unverified_ids`，如实标注）；回答/引用设长度上限；
    - 记账：usage_ledger（store sink，role=follow_up）+ run 成本增量（月度预算口径同源）；
    - 审计：`FOLLOWUP_ASKED/FOLLOWUP_ANSWERED` 进 `run_events`（flagged 回放脱敏）；
    - 失败只影响本次追问，**不改变原 run 终态与报告**。
    """
    _authorize_run(request, run_id)
    _check_csrf(request)
    user_id = (_session_user(request) or {}).get("user_id")
    if not config.hitl.followup_enabled:
        raise http_error("invalid_request", "追问功能未开启（DR_HITL_FOLLOWUP=false）")
    if not FOLLOWUP_LIMITER.allow(f"followup:{user_id or _client_key(request)}"):
        raise http_error("rate_limited", "追问过于频繁，稍后再试")
    if store is None:
        raise http_error("persistence_unavailable", "追问需要任务库（DR_DATABASE_URL）")
    row = _store_call(store.get_run, run_id)
    if row is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    if not row.get("finished_at"):
        raise http_error("report_not_ready", "研究尚未结束，暂不能追问")
    _enforce_output_policy(run_id)  # flagged / under-review 阻断（与导出同口径）
    report = _artifact_text(run_id, "report_md")
    if not report:
        raise http_error("report_unavailable", "本次运行没有产出报告")
    manifest = None
    raw_manifest = _artifact_text(run_id, "evidence_manifest")
    if raw_manifest:
        try:
            manifest = json.loads(raw_manifest)
        except (TypeError, ValueError):
            manifest = None
    pool = followup_mod.pool_ids(manifest)
    context = followup_mod.build_context(report, manifest)
    question = req.question.strip()
    _store_call(store.append_event, run_id, FOLLOWUP_ASKED, {"question": question[:200]})
    attempt = int(row.get("attempt") or 1)
    usage_token = push_usage_sink(make_store_sink(
        store, run_id=run_id, attempt=attempt, detail={"role": "follow_up"}))
    try:
        data, token_delta = followup_mod._ask_llm(
            followup_mod.build_system(), f"{context}\n\n【追问】{question}")
    except Exception as exc:  # noqa: BLE001 —— 追问失败不影响原 run 终态/报告
        raise http_error(
            "followup_unavailable", f"追问调用失败：{type(exc).__name__}: {exc}"[:200]) from exc
    finally:
        pop_usage_sink(usage_token)
    result = followup_mod.validate_response(data, pool)
    if token_delta > 0:
        # 追问成本并入本 run 账目（与展示同口径的保守上界；月度预算闸按 runs 表汇总）
        new_tokens = int(row.get("token_used") or 0) + token_delta
        new_cost = round(float(row.get("cost_estimate_cny") or 0.0)
                         + _estimate_cost_cny(token_delta), 6)
        _store_call(store.update_usage, run_id, token_used=new_tokens,
                    cost_estimate_cny=new_cost, budget_used_cny=new_cost)
    _store_call(store.append_event, run_id, FOLLOWUP_ANSWERED, {
        "answer": result["answer"][:500],
        "citations": result["citations"][:10],
        "unverified_count": len(result["unverified_ids"]),
        "grounded": result["grounded"],
    })
    _audit("followup_answered", request=request, actor_user_id=user_id,
           target_type="run", target_id=run_id,
           detail={"tokens": token_delta, "citations": len(result["citations"]),
                   "unverified": len(result["unverified_ids"])})
    return {"ok": True, **result}


def _markdown_export_response(body: str, topic: Optional[str], run_id: str) -> Response:
    """需求 17（#77）：下载文件名 = 话题名；RFC 6266 双格式（filename* UTF-8 + ASCII 回退）。"""
    display, ascii_fallback = build_export_filename(topic, run_id)
    return Response(
        content=body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": content_disposition(display, ascii_fallback)},
    )


def _docx_export_response(body: bytes, topic: Optional[str], run_id: str) -> Response:
    """需求 38 F3：DOCX 同步导出（轻量、离线；生成在 API 进程内完成，不走作业队列）。"""
    display, ascii_fallback = build_export_filename(topic, run_id, ".docx")
    return Response(
        content=body,
        media_type=("application/vnd.openxmlformats-officedocument"
                    ".wordprocessingml.document"),
        headers={"Content-Disposition": content_disposition(display, ascii_fallback)},
    )


def _artifact_text(run_id: str, kind: str) -> Optional[str]:
    """读取文本产物（P1-6：对象存储与 PG 双路径；失败/缺失返回 None，不抛）。"""
    try:
        artifact = store.get_artifact_row(run_id, kind)
    except Exception:  # noqa: BLE001 —— 读取失败按「产物不可用」处理（由调用方转结构化错误）
        return None
    if not artifact:
        return None
    body = artifact.get("body") or ""
    if artifact.get("storage") == "s3":
        object_store = get_object_store()
        if object_store is None:
            return None
        try:
            body = object_store.get_text(artifact.get("object_key")) or ""
        except Exception:  # noqa: BLE001
            return None
    return body


def _export_from_store(run_id: str, fmt: str, row: dict) -> Response:
    """任务库导出回落（P2-C / 需求 38 F1）：与内存路径**同一装配与渲染口径**。

    历史报告导出的头部/引用清单从「缺」变「有」——读取时渲染修复，不改写历史产物
    （§12.5 已登记）；`export_json` 产物缺失的历史 run ⇒ 引用/来源显式标注不可用
    （不伪造数据）。P1-6：产物可能存对象存储，读取前已过鉴权与 flagged 闸。
    """
    if not row.get("finished_at"):
        raise http_error("report_not_ready", "研究尚未结束，暂无报告可导出")
    if fmt == "json":
        body = _artifact_text(run_id, "export_json")
        if not body:
            raise http_error("report_unavailable", "本次运行没有产出报告")
        try:
            return JSONResponse(json.loads(body))
        except (TypeError, ValueError) as exc:
            raise http_error("report_unavailable", "导出载荷损坏，无法解析") from exc
    doc = export_mod.document_from_store(
        row,
        export_json=_artifact_text(run_id, "export_json"),
        report_md=_artifact_text(run_id, "report_md"),
    )
    if not doc.report.strip():
        raise http_error("report_unavailable", "本次运行没有产出报告")
    if fmt == "docx":
        return _docx_export_response(docx_render_mod.render_docx(doc), row.get("topic"), run_id)
    return _markdown_export_response(export_mod.render_markdown(doc), row.get("topic"), run_id)


@app.get("/api/research/{run_id}/report")
def export_report(run_id: str, request: Request,
                  fmt: str = Query("md", alias="format", pattern="^(md|json|docx)$")) -> Response:
    """报告导出（P1-6）：`format=md` 下载 Markdown，`format=json` 取结构化载荷，
    `format=docx`（需求 38 F3）同步生成 Word 文档。

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
    if fmt == "docx":
        return _docx_export_response(
            docx_render_mod.render_docx(export_mod.document_from_payload(payload)),
            payload.get("topic"), run_id)
    return _markdown_export_response(
        manager.export_markdown(run_id) or "", payload.get("topic"), run_id)


# ---------------------------------------------------------------- PDF 异步导出作业（需求 38 F2）


#: 输入预检上限（与 export 服务同口径；早拒可避免无谓作业）
EXPORT_PDF_MAX_INPUT_BYTES = int(_env_number("DR_EXPORT_PDF_MAX_INPUT_BYTES", 2 * 1024 * 1024))
EXPORT_RESULT_TTL_SECONDS = int(_env_number("DR_EXPORT_RESULT_TTL_SECONDS", 900))
_EXPORT_REDIS_URL = (os.getenv("DR_EXPORT_REDIS_URL") or "").strip()
_EXPORT_WAKE_KEY = "dr:export:wake"
_EXPORT_RESULT_KEY_PREFIX = "dr:export:result:"
_export_result_redis = None
_export_wake_redis = None


def _export_result_store():
    """结果通道客户端（专用非持久化 Redis；未配置 ⇒ None ⇒ 结构化不可用）。"""
    global _export_result_redis
    if _export_result_redis is None and _EXPORT_REDIS_URL:
        import redis as redis_lib

        _export_result_redis = redis_lib.Redis.from_url(_EXPORT_REDIS_URL)
    return _export_result_redis


def _push_export_wake() -> None:
    """唤醒信号（主 Redis；best-effort——丢失由 export 服务 2s 轮询兜底）。"""
    global _export_wake_redis
    if not _LIMITER_REDIS_URL:
        return
    try:
        if _export_wake_redis is None:
            import redis as redis_lib

            _export_wake_redis = redis_lib.Redis.from_url(_LIMITER_REDIS_URL)
        _export_wake_redis.lpush(_EXPORT_WAKE_KEY, "1")
    except Exception:  # noqa: BLE001 —— 唤醒是旁路
        pass


class ExportJobRequest(BaseModel):
    """v1 仅支持 kind=pdf（DOCX 为轻量同步生成，不走作业）。"""

    kind: str = Field("pdf", pattern="^pdf$")


def _export_job_or_404(run_id: str, export_id: str) -> dict:
    es = _export_store()
    if es is None:
        raise http_error("persistence_unavailable", "导出作业需要任务库（DR_DATABASE_URL）")
    job = _store_call(es.get_export_job, export_id)
    if job is None or job.get("run_id") != run_id:
        raise http_error("export_job_not_found", f"导出作业不存在：{export_id}")
    return job


@app.post("/api/research/{run_id}/export-jobs", status_code=202)
def create_export_job(run_id: str, req: ExportJobRequest, request: Request) -> dict:
    """需求 38 F2：创建 PDF 导出作业（异步；202 + export_id）。

    - 前置：所有权 + CSRF + 输出 gate（flagged/under-review 阻断，与导出同口径）
      + run 终局且有报告 + **输入字节预检**（早拒）；
    - 幂等：同一 run 的进行中作业或未过期结果直接复用（request_hash 唯一约束兜底并发）；
    - 结果 bytes 由专用 export 服务写专用非持久化 Redis；PG 保存权威状态与结果元数据。
    """
    owner = _authorize_run(request, run_id) or ""
    _check_csrf(request)
    _enforce_output_policy(run_id)
    es = _export_store()
    if es is None:
        raise http_error("persistence_unavailable", "导出作业需要任务库（DR_DATABASE_URL）")
    row = _store_call(es.get_run, run_id)
    if row is None:
        raise http_error("run_id_not_found", f"run_id 不存在：{run_id}")
    if not row.get("finished_at"):
        raise http_error("report_not_ready", "研究尚未结束，暂无报告可导出")
    if store is None:  # 内存 / DR_DEMO：报告在 manager，无持久化产物可预检
        if not manager.has_result(run_id):
            raise http_error("report_unavailable", "本次运行没有产出报告")
    else:
        report_text = _artifact_text(run_id, "report_md") or ""
        if not report_text.strip():
            raise http_error("report_unavailable", "本次运行没有产出报告")
        if len(report_text.encode()) > EXPORT_PDF_MAX_INPUT_BYTES:
            raise http_error(
                "export_too_large",
                f"报告超过 PDF 导出上限（{EXPORT_PDF_MAX_INPUT_BYTES} 字节），请改用 md/json",
                detail=f"report_bytes={len(report_text.encode())}")
    request_hash = hashlib.sha256(f"{run_id}|pdf|{owner}".encode()).hexdigest()
    existing = _store_call(es.find_reusable_export_job, request_hash)
    if existing is not None:
        return {"export_id": existing["job_id"], "status": existing["status"], "reused": True}
    job_id = uuid.uuid4().hex[:12]
    try:
        job = _store_call(es.create_export_job, job_id, run_id=run_id,
                          user_id=owner, request_hash=request_hash)
    except Exception as exc:  # noqa: BLE001 —— 并发同指纹冲突（唯一索引）⇒ 复用既有作业
        if getattr(exc, "sqlstate", None) == "23505":
            existing = _store_call(es.find_reusable_export_job, request_hash)
            if existing is not None:
                return {"export_id": existing["job_id"], "status": existing["status"],
                        "reused": True}
        raise http_error(
            "persistence_unavailable",
            f"导出作业创建失败：{type(exc).__name__}: {exc}"[:200]) from exc
    _push_export_wake()
    _audit("export_job_created", request=request, actor_user_id=owner or None,
           target_type="run", target_id=run_id,
           detail={"export_id": job_id, "kind": "pdf"})
    return {"export_id": job["job_id"], "status": job["status"], "reused": False}


@app.get("/api/research/{run_id}/export-jobs/{export_id}")
def get_export_job(run_id: str, export_id: str, request: Request) -> dict:
    """查询 PDF 导出作业状态（所有权 + 输出 gate 同口径）。"""
    _authorize_run(request, run_id)
    _enforce_output_policy(run_id)
    job = _export_job_or_404(run_id, export_id)
    expires = job.get("result_expires_at")
    return {
        "export_id": job["job_id"],
        "status": job["status"],
        "error_code": job.get("error_code"),
        "result_bytes": job.get("result_bytes"),
        "result_expires_at": expires.isoformat(timespec="seconds") if expires else None,
    }


@app.get("/api/research/{run_id}/export-jobs/{export_id}/download")
def download_export_job(run_id: str, export_id: str, request: Request) -> Response:
    """下载 PDF 导出结果（**每次**复核所有权 + 输出 gate + 结果有效期）。

    结果过期/丢失 ⇒ `export_result_expired`（结构化错误；用户可重新申请再生成）；
    不退回 API 同步渲染。`Cache-Control: private, no-store`。
    """
    _authorize_run(request, run_id)
    _enforce_output_policy(run_id)
    job = _export_job_or_404(run_id, export_id)
    if job.get("status") != "SUCCEEDED":
        raise http_error("export_not_ready", "导出尚未完成",
                         detail=f"status={job.get('status')}")
    expires = job.get("result_expires_at")
    if expires is None or expires <= datetime.now(UTC):
        raise http_error("export_result_expired", "导出结果已过期，请重新申请")
    client = _export_result_store()
    if client is not None:
        try:
            pdf = client.get(_EXPORT_RESULT_KEY_PREFIX + export_id)
        except Exception as exc:  # noqa: BLE001
            raise http_error(
                "export_unavailable", f"结果通道读取失败：{type(exc).__name__}") from exc
    else:
        # DR_DEMO / 内存替身：结果随作业对象保存；真实模式未配通道 ⇒ 结构化不可用
        get_result = getattr(_export_store(), "get_result", None)
        if get_result is None:
            raise http_error("export_unavailable", "结果通道未配置（DR_EXPORT_REDIS_URL）")
        pdf = get_result(export_id)
    if not pdf:
        raise http_error("export_result_expired", "导出结果已过期或丢失，请重新申请")
    topic = None
    if store is not None:
        row = _store_call(store.get_run, run_id) or {}
        topic = row.get("topic")
    if not topic:
        try:  # 内存 / DR_DEMO：话题名从 manager 载荷取；取不到回退 run_id 文件名
            topic = (manager.export_payload(run_id) or {}).get("topic")
        except Exception:  # noqa: BLE001 —— 文件名回退不影响下载本体
            topic = None
    display, ascii_fallback = build_export_filename(topic, run_id, ".pdf")
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": content_disposition(display, ascii_fallback),
            "Cache-Control": "private, no-store",
        },
    )


# ------------------------------------------------------------------ 报告只读分享（需求 26）


class ShareCreateRequest(BaseModel):
    """expires_days：1/7/30 天；0 = 永不过期（显式选择，前端二次确认）。"""

    expires_days: int = Field(7, ge=0, le=30)


def _share_enabled_or_404() -> None:
    if not SHARE_ENABLED:
        raise http_error("share_not_found", "链接无效或已过期")


def _share_markdown(run_id: str) -> Optional[str]:
    """分享读取链（需求 26 / 38 F1）：**只读报告正文**；内存/store 正文来源一致。

    披露边界（需求 38 TBD-5）：分享**不含**审计元数据头与引用清单——是否包含属单独
    审批项；修正历史路径分叉时不做任何披露扩大（内存路径由此前的「导出 Markdown
    含头/清单」收窄为正文，与生产 store 路径对齐，行为变更已登记）。
    """
    if manager.exists(run_id):
        if not manager.has_result(run_id):
            return None
        payload = manager.export_payload(run_id)
        if payload is None:
            return None
        body = str((payload.get("result") or {}).get("report") or "").strip()
        return body or None
    if store is None:
        return None
    return _artifact_text(run_id, "report_md") or None


def _share_state(row: dict) -> dict:
    return {
        "share_id": row["share_id"],
        "permanent": row.get("expires_at") is None,
        "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "last_accessed_at": (row["last_accessed_at"].isoformat()
                             if row.get("last_accessed_at") else None),
        "access_count": row.get("access_count", 0),
    }


@app.post("/api/runs/{run_id}/share")
def create_run_share(run_id: str, req: ShareCreateRequest, request: Request) -> dict:
    """创建/重建报告只读分享：一 run 单活跃链接（重建即撤销旧的）；token 明文只回一次。"""
    _share_enabled_or_404()
    _authorize_run(request, run_id)
    _check_csrf(request)
    _enforce_output_policy(run_id)
    if store is None:
        raise http_error("persistence_unavailable", "分享需要任务库（DR_DATABASE_URL）")
    if _share_markdown(run_id) is None:
        raise http_error("report_unavailable", "本次运行没有产出报告")
    owner = _run_owner(run_id) or ""
    token = new_token()
    share_id = uuid.uuid4().hex[:12]
    expires_at = (None if req.expires_days == 0
                  else datetime.now(UTC) + timedelta(days=req.expires_days))
    _store_call(store.create_report_share, share_id, token_hash(token), run_id,
                owner, expires_at)
    _audit("share_created", request=request, actor_user_id=owner or None,
           target_id=share_id, detail={"run_id": run_id, "expires_days": req.expires_days})
    return {"ok": True, "share_id": share_id, "url": f"/s/{token}",
            "permanent": expires_at is None,
            "expires_at": expires_at.isoformat() if expires_at else None}


@app.get("/api/runs/{run_id}/share")
def get_run_share(run_id: str, request: Request) -> dict:
    """当前活跃分享状态（不含 token —— 明文不可恢复；重建即轮换）。"""
    _share_enabled_or_404()
    _authorize_run(request, run_id)
    if store is None:
        raise http_error("persistence_unavailable", "分享需要任务库（DR_DATABASE_URL）")
    row = _store_call(store.get_active_report_share, run_id)
    if row is None:
        return {"active": False}
    return {"active": True, **_share_state(row)}


@app.delete("/api/runs/{run_id}/share")
def revoke_run_share(run_id: str, request: Request) -> dict:
    """撤销该 run 的活跃分享（立即失效）。"""
    _share_enabled_or_404()
    _authorize_run(request, run_id)
    _check_csrf(request)
    if store is None:
        raise http_error("persistence_unavailable", "分享需要任务库（DR_DATABASE_URL）")
    revoked = _store_call(store.revoke_report_share, run_id)
    if revoked:
        _audit("share_revoked", request=request, actor_user_id=_run_owner(run_id),
               target_id=run_id)
    return {"ok": True, "revoked": revoked}


@app.get("/api/shares")
def list_my_shares(request: Request) -> dict:
    """当前用户的活跃分享列表（需求 26 集中管理页）。

    注意：token 明文不可恢复（只存 hash）——管理页只提供状态与撤销；
    重新生成请到对应报告/历史预览的「分享」入口。
    """
    _share_enabled_or_404()
    user_id = _require_user(request)
    if store is None:
        raise http_error("persistence_unavailable", "分享需要任务库（DR_DATABASE_URL）")
    rows = (_store_call(store.list_report_shares_for_user, user_id) if user_id
            else _store_call(store.list_report_shares, active_only=True))
    return {"shares": [{
        "share_id": row["share_id"],
        "run_id": row["run_id"],
        "topic": row.get("topic"),
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        "permanent": row.get("expires_at") is None,
        "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
        "last_accessed_at": (row["last_accessed_at"].isoformat()
                             if row.get("last_accessed_at") else None),
        "access_count": row.get("access_count", 0),
    } for row in rows]}


@app.get("/api/share/{token}")
def get_shared_report(token: str, request: Request) -> Response:
    """免登录只读分享：统一 404（不存在/撤销/过期/审核未过/无报告）；限流防枚举。"""
    _share_enabled_or_404()
    if not token or len(token) > 128:
        raise http_error("share_not_found", "链接无效或已过期")
    if not SHARE_LIMITER.allow(f"share:{_client_key(request)}"):
        raise http_error("rate_limited", "请求过于频繁，稍后再试")
    if store is None:
        raise http_error("share_not_found", "链接无效或已过期")
    row = _store_call(store.resolve_report_share, token_hash(token))
    if row is None:
        raise http_error("share_not_found", "链接无效或已过期")
    try:
        _enforce_output_policy(row["run_id"])
    except Exception:  # noqa: BLE001 —— 审核未过一律并进统一 404
        raise http_error("share_not_found", "链接无效或已过期") from None
    markdown = _share_markdown(row["run_id"])
    if markdown is None:
        raise http_error("share_not_found", "链接无效或已过期")
    _store_call(store.touch_report_share, token_hash(token))
    _audit("share_viewed", request=request, target_id=row["share_id"],
           detail={"run_id": row["run_id"]})
    return JSONResponse(
        {"topic": row.get("topic") or "研究报告", "markdown": markdown,
         "permanent": row.get("expires_at") is None,
         "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None},
        headers={
            "Cache-Control": "private, no-store",
            "X-Robots-Tag": "noindex, nofollow, noarchive",
            "Referrer-Policy": "no-referrer",
        },
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
    """任务库实时尾随（P2-C 回放 + **P2-4 LISTEN/NOTIFY 唤醒**）。

    先按 `sequence` 补发历史，再等待新事件通知（PG LISTEN/NOTIFY；无任务库或
    连接断开时按 `DR_SSE_POLL_SECONDS` 轮询兜底），直到 run 终局；期间每 15s
    发心跳注释帧。唤醒只影响延迟，正确性始终由查库决定。
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
            flagged = bool(row.get("moderation_status")) and row["moderation_status"] != "cleared"
            for event in events:
                cursor = event["sequence"]
                payload = event["payload"] or {}
                # P0-4：flagged / blocked 的历史回放不携带正文（原文只在产物里）
                if flagged and event["event_type"] == "RUN_FINISHED":
                    payload = _redact_report_payload(payload)
                elif flagged and event["event_type"] == REPORT_DELTA:
                    # 需求 35 R4：正文增量在 flagged 回放中同样脱敏（正文只在产物/管理通道）
                    payload = {"text": "", "offset": payload.get("offset", 0),
                               "output_under_review": True}
                elif flagged and event["event_type"] in INTERACTION_EVENT_TYPES:
                    # 需求 36：HITL 卡片/回答同样脱敏（回答可能含敏感原文）
                    payload = _redact_interaction_payload(payload)
                yield sse_frame(
                    event_id=cursor,
                    event_type=event["event_type"],
                    payload=payload,
                )
            # 需求 36（TBD-6）：等待态不在活跃集 ⇒ 卡片回放完即关闭本连接，
            # 不发送终态帧；客户端经状态查询/重连获知等待态（避免长连接陪等 TTL）。
            if row["status"] not in ACTIVE_STATUSES:
                return
            if events:
                continue  # 有新增就立即追平，不额外等一秒
            now = time.monotonic()
            if now - last_beat >= HEARTBEAT_SECONDS:
                last_beat = now
                yield HEARTBEAT_FRAME
            await _wait_for_run_events(run_id)
    finally:
        METRICS.sse_close()


async def _wait_for_run_events(run_id: str) -> None:
    """P2-4：等待新事件唤醒（PG LISTEN/NOTIFY）；无任务库时退化为固定轮询间隔。

    等待在 executor 线程进行（psycopg 阻塞读）；无论是否收到通知，醒来后都会
    重新查库，正确性不依赖通知（断线 / 竞态最多增加 `SSE_POLL_SECONDS` 延迟）。
    """
    notifier = _get_notifier()
    if notifier is None:
        await asyncio.sleep(SSE_POLL_SECONDS)
        return
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, notifier.wait_for, run_id, SSE_POLL_SECONDS)


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


if OTEL_ENABLED and PROMETHEUS_ENABLED:
    # P1-8：Prometheus 抓取端点（仅显式开启时挂载；生产应由反代限制来源）
    app.mount("/metrics", prometheus_asgi_app())


@app.on_event("shutdown")
async def _shutdown_notifier() -> None:
    """P2-4：进程退出时关闭 LISTEN 专连接（幂等；未启动过则不动作）。"""
    notifier = _notifier
    if notifier is not None:
        notifier.close()

FRONTEND_DIST = Path(__file__).resolve().parents[1] / "frontend" / "dist"


@app.get("/s/{token}")
def share_page_shell(token: str) -> Response:
    """SPA 分享页壳（需求 26）：只回 index.html；数据走 /api/share/{token}（统一 404）。"""
    index = FRONTEND_DIST / "index.html"
    if not index.is_file():
        raise http_error("share_not_found", "链接无效或已过期")
    return FileResponse(index)


# 需求 37 M2：对外 MCP Server（默认关；开启时挂载 /mcp 并把会话管理器接入 lifespan）
if config.mcp.server_enabled:
    from .mcp_server import build_mcp_asgi  # 延迟导入：关闭态零 footprint

    _mcp_asgi, _mcp_fastmcp = build_mcp_asgi()

    @app.api_route("/mcp", methods=["GET", "POST", "DELETE"], include_in_schema=False)
    async def _mcp_slash_redirect() -> RedirectResponse:
        """MCP 端点兼容：无尾斜杠 → 307 到 `/mcp/`（保留方法与请求体；SDK 客户端自动跟随）。

        Starlette 1.6 的 Mount 仅匹配带尾斜杠路径，`/mcp` 会落到静态目录挂载并 405；
        显式 307 让「配置 https://host/mcp」的客户端可用。
        """
        return RedirectResponse(url="/mcp/", status_code=307)

    app.mount("/mcp", _mcp_asgi)
    _prev_lifespan = app.router.lifespan_context

    @asynccontextmanager
    async def _lifespan_with_mcp(application):  # type: ignore[no-untyped-def]
        async with _mcp_fastmcp.session_manager.run():
            async with _prev_lifespan(application):
                yield

    app.router.lifespan_context = _lifespan_with_mcp


if FRONTEND_DIST.is_dir():
    app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")
