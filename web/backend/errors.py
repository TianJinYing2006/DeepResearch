"""结构化错误详情（P1-5）。

**为什么单独成文件**：W9 初版后端错误只有一句字符串（`detail="run_id 不存在"`），
前端拿到后只能整句显示，用户无从判断「能不能重试 / 该怎么修 / 是不是我的锅」。
本模块把 **错误码 → HTTP 状态码 / 是否可重试 / 修复建议 / 归因组件** 收成**唯一真相源**，
HTTP 响应体与 SSE 的 `RUN_ERROR` 帧共用同一份 payload 结构。

🚨 边界（写死，不得越线）：这里只描述**传输层 / UI 层**的错误。
研究链路内部的故障判定（`ResearchState.run_status`、`degradation_log`）仍由 W8
冻结的口径负责，本模块**不参与**任何 `run_status` 判定 ——
否则就是把传输层问题记成系统故障，直接污染 W8「故障可归因率 100%」这条 A 类验收。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from fastapi import HTTPException


@dataclass(frozen=True)
class ErrorSpec:
    """一条错误码的静态画像。

    Attributes:
        http_status: HTTP 端点用的状态码；``None`` 表示该错误只出现在 SSE 里
            （如 `run_timeout` 通过 `RUN_FINISHED` 表达，不是 HTTP 错误）。
        retryable: **同一个请求原样重发**是否有意义（不是「换个参数再试」）。
        hint: 给用户的下一步动作，直接展示在前端错误卡里。
        component: 归因组件（`web` / `runner` / `graph` / `search`）。
    """

    http_status: Optional[int]
    retryable: bool
    hint: str
    component: str = "web"


ERROR_SPECS: Dict[str, ErrorSpec] = {
    # --- 启动前校验（HTTP 400） ------------------------------------------
    "empty_topic": ErrorSpec(
        400, False, "填写研究主题后再启动。", component="web"),
    "unknown_search_provider": ErrorSpec(
        400, False, "请从运行选项里列出的搜索源中选择（列表来自 /api/options）。",
        component="web"),
    "missing_search_key": ErrorSpec(
        400, False,
        "该搜索源未配置 API key：在 .env 中补上后**重启服务**再试（配置只在启动时读取）。",
        component="search"),
    "invalid_request": ErrorSpec(
        422, False,
        "请求参数不合法（如导出 format 只接受 md|json）：按 detail 里的字段提示修正后重发。",
        component="web"),
    # --- 资源不存在 / 不可用（HTTP 404 / 409 / 429） ----------------------
    "run_id_not_found": ErrorSpec(
        404, False,
        "run_id 既不在当前进程内存中，也不在任务库中：确认 id 是否拼错，或重新发起一次研究。"
        "（任务库只保存已落库的运行；服务重启后内存中未落库的运行不可恢复。）",
        component="web"),
    "report_not_ready": ErrorSpec(
        409, True, "研究尚未结束，报告还没生成。等 RUN_FINISHED 后再导出。",
        component="web"),
    "report_unavailable": ErrorSpec(
        404, False,
        "本次运行没有产出报告（被取消 / 超时 / 失败）。可调整参数后重新运行。",
        component="web"),
    # --- 任务列表管理（需求 22） ------------------------------------------
    "run_not_retryable": ErrorSpec(
        409, False,
        "仅失败 / 失联 / 超时的任务支持一键重试：进行中任务请等它结束，成功或已取消的任务请重新提交。",
        component="web"),
    "run_active": ErrorSpec(
        409, False,
        "进行中的任务不能归档：等待任务结束或先取消，再归档。",
        component="web"),
    "concurrency_limit": ErrorSpec(
        429, True,
        "已有研究在运行：前台模型下一次只跑一个（D-19）。等它结束或点「停止」后再启动；"
        "确需并发请调大 DR_MAX_CONCURRENT_RUNS。",
        component="web"),
    "persistence_unavailable": ErrorSpec(
        503, True,
        "任务持久化不可用（PostgreSQL 未配置或连接失败）：检查 DR_DATABASE_URL 与数据库状态。",
        component="web"),
    # --- 账号与会话（P4-A） ----------------------------------------------
    "unauthenticated": ErrorSpec(
        401, False, "请先登录（httpOnly Session Cookie 已失效或未携带）。",
        component="auth"),
    "invalid_credentials": ErrorSpec(
        401, False, "邮箱或密码不正确（不区分用户不存在 / 密码错 / 已封禁）。",
        component="auth"),
    "email_taken": ErrorSpec(
        409, False, "该邮箱已注册：直接登录，或更换邮箱。", component="auth"),
    "invite_invalid": ErrorSpec(
        400, False, "邀请码无效、已使用或已过期：向管理员索取新邀请码。", component="auth"),
    "csrf_failed": ErrorSpec(
        403, False,
        "CSRF 校验失败：写操作需携带与 dr_csrf Cookie 一致的 X-CSRF-Token。",
        component="auth"),
    "quota_exceeded": ErrorSpec(
        429, True,
        "已超出配额（每日运行次数 / 单用户并发 / 月度预算）：等预算周期或已有任务结束后再试。",
        component="web"),
    "rate_limited": ErrorSpec(
        429, True, "请求过于频繁（登录 / 提交限流）：稍后再试。", component="web"),
    "idempotency_conflict": ErrorSpec(
        409, False,
        "幂等键已用于不同请求：换一个新 key 重试，或原样重发首次请求（同键不同载荷会被拒绝）。",
        component="web"),
    # --- RAG 知识库（P6-A） ----------------------------------------------
    "rag_unavailable": ErrorSpec(
        503, True,
        "知识库不可用：确认 QDRANT_URL 可达、embedding key 已配置后重试。",
        component="rag"),
    "rag_ingest_failed": ErrorSpec(
        503, True,
        "文档摄取失败（解析 / embedding / 向量库）：按 message 排查后可重试同一文件。",
        component="rag"),
    # --- 内容安全（P7-A / P0-4）------------------------------------------
    "content_blocked": ErrorSpec(
        400, False,
        "输入命中内容安全预检：请修改主题或附加要求后重试；如认为误判可提交申诉。",
        component="moderation"),
    "output_under_review": ErrorSpec(
        403, False,
        "报告命中内容安全预检，正在等待人工复核：复核通过或申诉处理前不开放查看与导出；"
        "如认为误判可提交申诉。",
        component="moderation"),
    "appeal_not_applicable": ErrorSpec(
        409, False,
        "该任务没有需要复核的标记（未被 flagged）：无需申诉；如对输入预检有异议可不带 run_id 提交申诉。",
        component="moderation"),
    "appeal_duplicate": ErrorSpec(
        409, False,
        "同一任务的申诉已在处理中：请等待复核结果，不要重复提交。",
        component="moderation"),
    "https_required": ErrorSpec(
        400, False,
        "生产环境仅接受 HTTPS 请求：请通过 TLS 入口（反向代理）访问，并确保透传 X-Forwarded-Proto。",
        component="web"),
    "unsupported_file_type": ErrorSpec(
        400, False,
        "不支持的文件类型或文件内容与扩展名不符：只接受 PDF / DOCX / Markdown / 纯文本。",
        component="rag"),
    "payload_too_large": ErrorSpec(
        413, False,
        "文件或文档规模超过上限：压缩/拆分后重试（大小上限见 DR_RAG_MAX_FILE_MB）。",
        component="rag"),
    "document_limit_exceeded": ErrorSpec(
        422, False,
        "文档超过解析限额（页数 / 字符数 / 分块数 / 解析时长）：拆分文档或联系管理员调整限额。",
        component="rag"),
    "ingestion_not_found": ErrorSpec(
        404, False,
        "摄取记录不存在（或不属于当前用户）：确认 ingestion_id，或重新上传。",
        component="rag"),
    # --- 需求 23：知识库管理与版本重建 -----------------------------------
    "rag_no_snapshot": ErrorSpec(
        409, False,
        "该文档没有解析快照（历史文档）：无法重新分块，请重新上传该文档。"
        "（分块预览与重新嵌入仍可用）",
        component="rag"),
    "rag_not_ready": ErrorSpec(
        409, False,
        "文档当前状态不允许该操作（处理中 / 已失败）：等状态就绪后重试。",
        component="rag"),
    # --- 运行期（SSE，不是 HTTP 错误） -----------------------------------
    "run_timeout": ErrorSpec(
        None, False,
        "研究超过时限已被自动停止（节点边界协作式，最坏多等一个节点）。"
        "可调大 DR_RUN_TIMEOUT_SECONDS 或下调 max_total_hops 后重跑。",
        component="runner"),
    "stop_forced": ErrorSpec(
        None, False,
        "研究未在宽限期内响应停止请求，已在**传输层**强制收口；"
        "后台线程可能仍在收尾，进程重启才会彻底释放。",
        component="runner"),
    "runner_crash": ErrorSpec(
        None, True, "运行线程异常退出。可原样重试；若稳定复现请看 message 里的异常类型。",
        component="runner"),
    "graph_error": ErrorSpec(
        None, True, "研究链路报错并被结构化捕获，run_status 已按 W8 口径写入 state。",
        component="graph"),
    "unknown": ErrorSpec(
        None, False, "未知错误，详情见 message。", component="web"),
}

#: SSE 帧里出现的错误码没有 HTTP 状态码，默认按 500 兜底（仅用于转成 HTTPException）
_FALLBACK_HTTP_STATUS = 500


def error_payload(
    code: str,
    message: str,
    *,
    node: Optional[str] = None,
    component: Optional[str] = None,
    detail: Optional[str] = None,
    retryable: Optional[bool] = None,
    hint: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    """把错误码 + 原始消息组装成**结构化** payload。

    返回体恒含 `code` / `message` / `component` / `node` / `detail` /
    `retryable` / `hint` 七键（缺的填 ``None`` 或规格默认值）——
    前端因此可以**只认键**，不必解析 message 文本猜类型。
    """
    spec = ERROR_SPECS.get(code) or ERROR_SPECS["unknown"]
    payload: Dict[str, Any] = {
        "code": code,
        "message": message,
        "component": component or spec.component,
        "node": node,
        "detail": detail,
        "retryable": spec.retryable if retryable is None else retryable,
        "hint": hint or spec.hint,
    }
    payload.update(extra)
    return payload


def http_error(code: str, message: str, **kwargs: Any) -> HTTPException:
    """按错误码生成 `HTTPException`（`detail` 是**结构化 dict**，不再是裸字符串）。"""
    spec = ERROR_SPECS.get(code) or ERROR_SPECS["unknown"]
    return HTTPException(
        status_code=spec.http_status or _FALLBACK_HTTP_STATUS,
        detail=error_payload(code, message, **kwargs),
    )


class ApiError(Exception):
    """非 HTTP 层（如 `RunManager`）抛出的可结构化错误，由路由层转成 HTTP 响应。"""

    def __init__(self, code: str, message: str, **kwargs: Any) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.kwargs = kwargs

    def to_http(self) -> HTTPException:
        return http_error(self.code, self.message, **self.kwargs)
