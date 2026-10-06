"""P2-6 告警判定与外送（webhook 触达值班人）。

口径（生产就绪清单 §3.7「告警触达值班人（IM/邮件至少一条链路）」与 §8）：

- **判定**：`collect_alerts` 与 `/api/ops/alerts` 同一来源（阈值全部环境变量化）。
  API 进程传入自己的阈值常量与 HTTP 指标；Worker 后台同步（`http=None`）判定
  queue_depth / stale_leases / deletion_abandoned / worker_heartbeat_missing /
  monthly_budget（5xx 是 API 进程本地指标，Worker 判不了，仅 API 展示）。
- **状态机（fingerprint 去重）**：`alert_states` 按告警码收敛 —— firing 首次立即
  入队；持续 firing 在冷却（`DR_ALERT_REPEAT_MINUTES`，默认 30）内不重复；条件
  消失即 resolved 入队（必发）。防刷屏且不丢恢复通知。
- **外送**：`DR_ALERT_WEBHOOK_URL` 未配置时只维护状态（零副作用）；配置后按 URL
  识别飞书 / 钉钉 / 企业微信 / 通用 JSON。失败指数退避（30s×2ⁿ，上限 30min），
  `DR_ALERT_MAX_ATTEMPTS`（默认 5）次后放弃（infinity），CLI 可手动重试。
- **payload 只带告警元数据**（code/severity/message/value/threshold），不含
  用户内容（PII 不出域）。
"""
from __future__ import annotations

import logging
import os
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests

_log = logging.getLogger("deepresearch.alerts")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, str(default)))
    except ValueError:
        return default


def _env_int(name: str, default: int) -> int:
    try:
        return int(float(os.getenv(name, str(default))))
    except ValueError:
        return default


def default_thresholds() -> dict[str, float]:
    """Worker 后台同步用的阈值（与 API 进程 `ALERT_*` 常量同名同默认值）。"""
    return {
        "http_5xx_rate_pct": _env_float("DR_ALERT_5XX_RATE_PCT", 2.0),
        "queue_depth": _env_float("DR_ALERT_QUEUE_DEPTH", 20),
        "stale_runs": _env_float("DR_ALERT_STALE_RUNS", 1),
        "monthly_pct": _env_float("DR_ALERT_MONTHLY_PCT", 80.0),
    }


def alert_webhook_url() -> str:
    return (os.getenv("DR_ALERT_WEBHOOK_URL") or "").strip()


def repeat_minutes() -> int:
    return max(1, _env_int("DR_ALERT_REPEAT_MINUTES", 30))


def max_attempts() -> int:
    return max(1, _env_int("DR_ALERT_MAX_ATTEMPTS", 5))


def retry_base_seconds() -> int:
    return max(1, _env_int("DR_ALERT_RETRY_BASE_SECONDS", 30))


def retry_max_seconds() -> int:
    return max(1, _env_int("DR_ALERT_RETRY_MAX_SECONDS", 1800))


# ------------------------------------------------------------------ 判定

def collect_alerts(
    *,
    thresholds: dict[str, float],
    http: Optional[dict[str, Any]] = None,
    queue_depth: Optional[float] = None,
    stale_leases: float = 0,
    deletions: Optional[dict[str, int]] = None,
    month_cost: float = 0.0,
    month_budget: Optional[float] = None,
    workers_live: Optional[int] = None,
    execution_mode: str = "inprocess",
    overdue_appeals: int = 0,
) -> list[dict[str, Any]]:
    """纯函数判定（无副作用）：返回当前触发的告警列表（与 `/api/ops/alerts` 同构）。"""
    alerts: list[dict[str, Any]] = []

    def _check(code: str, severity: str, value: float, threshold: float,
               message: str) -> None:
        if threshold and value >= threshold:
            alerts.append({
                "code": code, "severity": severity,
                "value": round(value, 4), "threshold": threshold, "message": message,
            })

    if http is not None:
        total = http["2xx"] + http["4xx"] + http["5xx"]
        rate_5xx = (http["5xx"] / total * 100) if total else 0.0
        _check("http_5xx_rate", "high", rate_5xx, thresholds["http_5xx_rate_pct"],
               "5xx 比例超过阈值")
    if queue_depth is not None:
        _check("queue_depth", "medium", queue_depth, thresholds["queue_depth"],
               "队列积压超过阈值")
    _check("stale_leases", "high", stale_leases, thresholds["stale_runs"],
           "存在租约过期未被接管的任务")
    _check("deletion_abandoned", "high", int((deletions or {}).get("abandoned", 0)), 1,
           "存在被放弃的注销清理（外部数据可能残留，需人工介入）")
    _check("appeal_sla_overdue", "medium", overdue_appeals, 1,
           "存在超过 SLA 未完成复核的申诉（DR_APPEAL_SLA_HOURS）")
    if execution_mode == "queue" and workers_live is not None and int(workers_live) < 1:
        alerts.append({
            "code": "worker_heartbeat_missing", "severity": "high",
            "value": int(workers_live), "threshold": 1,
            "message": "队列模式无活跃 worker（心跳过期），任务不会被执行",
        })
    if month_budget:
        monthly_pct = month_cost / month_budget * 100
        _check("monthly_budget", "high", monthly_pct, thresholds["monthly_pct"],
               "月度预算消耗达到阈值")
    return alerts


# ------------------------------------------------------------------ 状态收敛

def sync_alerts(store, alerts: list[dict[str, Any]], *,
                now: Optional[datetime] = None,
                webhook_enabled: Optional[bool] = None) -> dict[str, Any]:
    """把本轮判定收敛进 `alert_states`，并按状态机入队外送。

    返回 `{firing, repeat, resolved, webhook_configured}`（fingerprint 列表）。
    """
    moment = now or datetime.now(UTC)
    enabled = (alert_webhook_url() != "") if webhook_enabled is None else webhook_enabled
    cooldown = timedelta(minutes=repeat_minutes())
    seen: set[str] = set()
    fired: list[str] = []
    repeated: list[str] = []
    resolved: list[str] = []

    for alert in alerts:
        code = str(alert["code"])
        seen.add(code)
        severity = str(alert.get("severity") or "medium")
        state = store.get_alert_state(code)
        if state is None or state["status"] != "firing":
            store.upsert_alert_state(code, severity=severity, status="firing",
                                     detail=alert, notified_at=moment)
            if enabled:
                store.enqueue_alert_delivery(code, "firing", severity, payload=alert,
                                             max_attempts=max_attempts())
            fired.append(code)
            continue
        last_notified = state.get("last_notified_at")
        due = last_notified is None or (moment - last_notified) >= cooldown
        store.upsert_alert_state(code, severity=severity, status="firing", detail=alert,
                                 notified_at=moment if due else None)
        if due:
            if enabled:
                store.enqueue_alert_delivery(code, "repeat", severity, payload=alert,
                                             max_attempts=max_attempts())
            repeated.append(code)

    for state in store.list_alert_states(status="firing", limit=1000):
        code = state["fingerprint"]
        if code in seen:
            continue
        payload = {"code": code, "resolved": True,
                   "detail": state.get("detail") or {}}
        store.upsert_alert_state(code, severity=state["severity"], status="resolved",
                                 detail=payload, notified_at=moment)
        if enabled:
            store.enqueue_alert_delivery(code, "resolved", state["severity"],
                                         payload=payload, max_attempts=max_attempts())
        resolved.append(code)

    return {"firing": fired, "repeat": repeated, "resolved": resolved,
            "webhook_configured": enabled}


# ------------------------------------------------------------------ 外送

def _delivery_text(delivery: dict[str, Any]) -> str:
    payload = delivery.get("payload") or {}
    parts = [f"[DeepResearch] {str(delivery['severity']).upper()} "
             f"{delivery['kind']}: {delivery['fingerprint']}"]
    if payload.get("message"):
        parts.append(str(payload["message"]))
    if "value" in payload:
        parts.append(f"value={payload['value']} threshold={payload.get('threshold')}")
    if delivery["kind"] == "resolved":
        parts.append("已恢复")
    return " | ".join(parts)


def build_webhook_body(url: str, delivery: dict[str, Any]) -> dict[str, Any]:
    """按 webhook 主机识别 IM 平台体格式；未知平台用通用 JSON（便于自建桥接）。"""
    text = _delivery_text(delivery)
    host = urlparse(url).netloc.lower()
    if "feishu" in host or "larksuite" in host:
        return {"msg_type": "text", "content": {"text": text}}
    if "dingtalk" in host:
        return {"msgtype": "markdown",
                "markdown": {"title": "DeepResearch 告警", "text": text}}
    if "weixin" in host or "wecom" in host:
        return {"msgtype": "markdown", "markdown": {"content": text}}
    return {
        "source": "deepresearch",
        "fingerprint": delivery["fingerprint"],
        "kind": delivery["kind"],
        "severity": delivery["severity"],
        "text": text,
        "payload": delivery.get("payload") or {},
    }


def _post_json(url: str, body: dict[str, Any]) -> None:
    response = requests.post(url, json=body, timeout=10)
    response.raise_for_status()


def process_deliveries_once(
    store,
    *,
    transport: Optional[Callable[[str, dict[str, Any]], Any]] = None,
    limit: int = 20,
    lease_seconds: int = 120,
) -> dict[str, Any]:
    """处理一批到期交付：成功置 delivered；失败按 `attempts` 退避（超限放弃）。"""
    summary: dict[str, Any] = {"claimed": 0, "delivered": 0, "failed": 0,
                               "given_up": 0, "skipped": ""}
    url = alert_webhook_url()
    if not url:
        summary["skipped"] = "no_webhook"
        return summary
    post = transport or _post_json
    claimed = store.claim_due_alert_deliveries(limit=limit, lease_seconds=lease_seconds)
    summary["claimed"] = len(claimed)
    for delivery in claimed:
        try:
            post(url, build_webhook_body(url, delivery))
            store.finish_alert_delivery(delivery["delivery_id"])
            summary["delivered"] += 1
            _log.info("alert delivered: %s %s", delivery["fingerprint"], delivery["kind"])
        except Exception as exc:  # noqa: BLE001 —— 单条失败不影响批次其余
            attempts = int(delivery["attempts"])
            limit_attempts = int(delivery["max_attempts"])
            if attempts >= limit_attempts:
                store.fail_alert_delivery(
                    delivery["delivery_id"], delay_seconds=0,
                    error=f"give up after {attempts} attempts: {exc}")
                summary["given_up"] += 1
            else:
                delay = min(
                    retry_base_seconds() * (2 ** (attempts - 1)),
                    retry_max_seconds(),
                )
                store.fail_alert_delivery(delivery["delivery_id"], delay_seconds=delay,
                                          error=f"{type(exc).__name__}: {exc}")
                summary["failed"] += 1
            _log.warning("alert delivery failed (%s %s, attempt %d): %s",
                         delivery["fingerprint"], delivery["kind"], attempts, exc)
    return summary
