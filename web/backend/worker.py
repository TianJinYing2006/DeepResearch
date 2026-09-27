"""独立 Worker（P3-A；P0-2 起以任务库为派发权威）：执行 QUEUED 任务 → 写任务库。

与 RunManager（进程内执行）共用 `persistence.persist_terminal`，保证同一停止原因
写出同一状态；差别只在事件写入方式：

- Worker 是单 run 的唯一写者 ⇒ 事件序号由数据库分配（`MAX(sequence)+1`，`seq=None`）；
- 没有内存帧 —— SSE 由 API 从 `run_events` 实时尾随（`main._stored_event_gen`）。

派发（P0-2）：
- 领取走 `RunStore.claim_next_queued`（`FOR UPDATE SKIP LOCKED`），**不再**从 Redis
  BRPOP 取任务 —— 不存在「先出队、后认领」的丢失窗口；
- Redis（如配置）只作**唤醒信号**：`enqueue`/`dequeue` 丢失或重复都不影响正确性，
  无 Redis 时退化为按 `poll_seconds` 轮询；
- 旧实现遗留的孤儿 QUEUED 会被下一轮领取自动回收（过期则被清扫为 `TIMED_OUT`）。

租约：
- 认领时写 `worker_id` + `lease_expires_at`（默认 120s），执行期由心跳线程续租（默认 30s）；
- 崩溃后任务的接管（租约超时扫描 / 重试 / `LOST`）见 `sweep_stale_runs`。

启动：`python -m web.backend.worker`（需 `DR_DATABASE_URL`；`DR_REDIS_URL` 可选）。
"""
from __future__ import annotations

import os
import signal
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from config import config
from research_engine.graph import DeepResearchGraph, create_graph
from research_engine.rag.scope import set_scope
from research_engine.runtime_profile import (
    RuntimeProfile,
    effective_research_config,
    set_profile,
)
from research_engine.streaming import (
    STOP_CANCELLED,
    STOP_ERROR,
    STOP_TIMEOUT,
    RunStep,
)
from research_engine.usage import pop_usage_sink, push_usage_sink

from .agui import (
    DEGRADATION,
    RUN_ERROR,
    RUN_FINISHED,
    RUN_STARTED,
    STATE_DELTA,
    STEP_FINISHED,
)
from .alerts import collect_alerts, default_thresholds, process_deliveries_once, sync_alerts
from .deletion import process_deletions_once
from .errors import error_payload
from .ingestion import process_ingestions_once, purge_expired_documents
from .moderation import apply_output_gate, flag_report
from .otel import run_span, setup_otel
from .persistence import persist_terminal
from .queue import RunQueue
from .retention import run_retention
from .runner import _env_int, _estimate_cost_cny
from .store import RunStore
from .usage import make_store_sink

DEFAULT_LEASE_SECONDS = 120
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_SWEEP_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RUN_TIMEOUT_SECONDS = 3600
DEFAULT_ALERT_CHECK_SECONDS = 60


def _log(message: str) -> None:
    print(f"[worker] {message}", file=sys.stderr, flush=True)


class Worker:
    def __init__(
        self,
        store: RunStore,
        queue: Optional[RunQueue] = None,
        *,
        graph_factory: Callable[[], DeepResearchGraph] = create_graph,
        worker_id: Optional[str] = None,
        lease_seconds: Optional[int] = None,
        heartbeat_seconds: Optional[int] = None,
        poll_seconds: float = DEFAULT_POLL_SECONDS,
        sweep_seconds: Optional[int] = None,
        max_attempts: Optional[int] = None,
    ):
        self._store = store
        self._queue = queue
        self._graph_factory = graph_factory
        self.worker_id = worker_id or f"worker-{uuid.uuid4().hex[:8]}"
        self.lease_seconds = (
            lease_seconds if lease_seconds is not None
            else _env_int("DR_WORKER_LEASE_SECONDS", DEFAULT_LEASE_SECONDS)
        )
        self.heartbeat_seconds = (
            heartbeat_seconds if heartbeat_seconds is not None
            else _env_int("DR_WORKER_HEARTBEAT_SECONDS", DEFAULT_HEARTBEAT_SECONDS)
        )
        self.poll_seconds = poll_seconds
        self.sweep_seconds = (
            sweep_seconds if sweep_seconds is not None
            else _env_int("DR_WORKER_SWEEP_SECONDS", DEFAULT_SWEEP_SECONDS)
        )
        self.max_attempts = (
            max_attempts if max_attempts is not None
            else _env_int("DR_WORKER_MAX_ATTEMPTS", DEFAULT_MAX_ATTEMPTS)
        )
        self._stop = threading.Event()
        # P1-3：worker registry（心跳 best-effort；draining → stopped 由 stop()/run_forever 收口）
        self.version = (os.getenv("DR_WORKER_VERSION") or "dev").strip()[:64]
        self.hostname = socket.gethostname()[:128]
        self.registry_heartbeat_seconds = _env_int(
            "DR_WORKER_REGISTRY_HEARTBEAT_SECONDS", 15)
        self._current_run_id: Optional[str] = None
        # P2-6：告警状态同步与外送周期（webhook 未配置时只维护状态）
        self.alert_check_seconds = _env_int("DR_ALERT_CHECK_SECONDS",
                                            DEFAULT_ALERT_CHECK_SECONDS)
        self.worker_heartbeat_max_age_seconds = _env_int(
            "DR_WORKER_HEARTBEAT_MAX_AGE_SECONDS", 90)

    # ------------------------------------------------------------------ 主循环

    def stop(self) -> None:
        """停止信号：先标记 draining（不再领新任务），当前任务自然收口。"""
        if not self._stop.is_set():
            _log("停止信号：进入 draining（不再领取新任务，等待当前任务收口）")
            self._mark_status("draining")
        self._stop.set()

    def register(self) -> None:
        """注册 / 复活本 worker（best-effort：注册表是运维元数据，绝不打断主循环）。"""
        try:
            self._store.register_worker(self.worker_id, version=self.version,
                                        hostname=self.hostname)
        except Exception as exc:  # noqa: BLE001
            _log(f"register failed (best-effort): {type(exc).__name__}: {exc}")

    def _registry_beat(self) -> None:
        try:
            alive = self._store.heartbeat_worker(
                self.worker_id,
                in_flight=1 if self._current_run_id else 0,
                current_run_id=self._current_run_id,
            )
        except Exception as exc:  # noqa: BLE001
            _log(f"registry heartbeat failed: {type(exc).__name__}: {exc}")
            return
        if not alive:
            _log("worker row missing; re-registering")
            self.register()

    def _registry_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self.registry_heartbeat_seconds):
            self._registry_beat()

    def _mark_status(self, status: str) -> None:
        try:
            self._store.mark_worker_status(self.worker_id, status)
        except Exception as exc:  # noqa: BLE001
            _log(f"mark_worker_status({status}) failed: {type(exc).__name__}: {exc}")

    def run_forever(self) -> None:
        self.register()
        registry_stop = threading.Event()
        registry_thread = threading.Thread(
            target=self._registry_loop, args=(registry_stop,),
            name="worker-registry", daemon=True,
        )
        registry_thread.start()
        next_sweep = 0.0  # 启动即清扫一次：接管上次进程崩溃留下的过期租约
        next_retention = 0.0  # P0-8b：保留期清理（默认每日一次）
        next_alerts = 0.0  # P2-6：告警判定 + 状态收敛 + webhook 外送
        try:
            while not self._stop.is_set():
                if time.monotonic() >= next_sweep:
                    try:
                        swept = self.sweep_and_requeue()
                        if swept:
                            _log(f"sweep: {swept}")
                    except Exception as exc:  # noqa: BLE001 —— 清扫失败不拖垮消费循环
                        _log(f"sweep failed: {type(exc).__name__}: {exc}")
                    try:
                        # P0-7：注销 outbox（Qdrant 清理 + 验证）随清扫周期推进
                        deletions = process_deletions_once(self._store)
                        if deletions["claimed"]:
                            _log(f"deletions: {deletions}")
                    except Exception as exc:  # noqa: BLE001 —— 注销清理失败不拖垮消费循环
                        _log(f"deletion outbox failed: {type(exc).__name__}: {exc}")
                    try:
                        # P0-8b：异步摄取（隔离区 → 解析/embedding/Qdrant）
                        ingestions = process_ingestions_once(self._store)
                        if ingestions["claimed"]:
                            _log(f"ingestions: {ingestions}")
                    except Exception as exc:  # noqa: BLE001 —— 摄取失败不拖垮消费循环
                        _log(f"ingestion worker failed: {type(exc).__name__}: {exc}")
                    if time.monotonic() >= next_retention:
                        try:
                            purged = purge_expired_documents(self._store)
                            if purged["expired"]:
                                _log(f"retention: {purged}")
                        except Exception as exc:  # noqa: BLE001
                            _log(f"retention purge failed: {type(exc).__name__}: {exc}")
                        try:
                            # P2-2：数据保留期清理（事件 30d / 运行 90d / 审计等 180d）
                            summary = run_retention(self._store)
                            if summary["deleted_total"]:
                                _log(f"retention purge: deleted={summary['deleted_total']}")
                            aborted = [r["table"] for r in summary["results"] if r["aborted"]]
                            if aborted:
                                _log(f"retention guardrail aborted tables: {aborted}")
                        except Exception as exc:  # noqa: BLE001
                            _log(f"data retention failed: {type(exc).__name__}: {exc}")
                        next_retention = time.monotonic() + 24 * 3600
                    if time.monotonic() >= next_alerts:
                        try:
                            alert_summary = self.sync_alerts_once()
                            if (alert_summary["firing"] or alert_summary["repeat"]
                                    or alert_summary["resolved"]
                                    or alert_summary["delivery"]["delivered"]
                                    or alert_summary["delivery"]["given_up"]):
                                _log(f"alerts: {alert_summary}")
                        except Exception as exc:  # noqa: BLE001 —— 告警失败不拖垮消费循环
                            _log(f"alert sync failed: {type(exc).__name__}: {exc}")
                        next_alerts = time.monotonic() + self.alert_check_seconds
                    next_sweep = time.monotonic() + self.sweep_seconds
                if self.claim_next():
                    continue
                self._wait_for_signal()
        finally:
            registry_stop.set()
            self._mark_status("stopped")

    def sync_alerts_once(self) -> dict[str, Any]:
        """P2-6：告警判定 + 状态收敛 + webhook 外送（Worker 可判定项）。

        5xx 比例是 API 进程本地指标，Worker 判不了（`http=None`），由 API 展示；
        外送走 `DR_ALERT_WEBHOOK_URL`（未配置时只维护 alert_states，零外呼）。
        """
        try:
            month_budget = float(os.getenv("DR_MONTHLY_BUDGET_CNY", "1500") or 0)
        except ValueError:
            month_budget = 0.0
        alerts = collect_alerts(
            thresholds=default_thresholds(),
            http=None,
            queue_depth=(self._queue.depth() if self._queue is not None else None),
            stale_leases=self._store.count_stale_leases(),
            deletions=self._store.count_deletions_by_status(),
            month_cost=self._store.month_cost_cny(),
            month_budget=month_budget,
            workers_live=self._store.count_live_workers(
                within_seconds=self.worker_heartbeat_max_age_seconds),
            execution_mode="queue",
        )
        summary = sync_alerts(self._store, alerts)
        summary["delivery"] = process_deliveries_once(self._store)
        return summary

    def claim_next(self) -> bool:
        """从任务库原子领取下一条 QUEUED 并执行（P0-2 派发权威）。

        返回是否领到任务。Redis 唤醒信号只影响领取延迟，不参与正确性。
        """
        try:
            row = self._store.claim_next_queued(self.worker_id, self.lease_seconds)
        except Exception as exc:  # noqa: BLE001 —— 库抖动不应打死 Worker
            _log(f"claim_next failed: {type(exc).__name__}: {exc}；5s 后重试")
            time.sleep(5)
            return False
        if row is None:
            return False
        self._run_claimed(row)
        return True

    def _wait_for_signal(self) -> None:
        """空闲等待：有 Redis 时阻塞等唤醒信号（可丢），否则按 poll_seconds 轮询。"""
        if self._queue is not None:
            try:
                self._queue.dequeue(self.poll_seconds)
                return
            except Exception as exc:  # noqa: BLE001 —— 信号失败退化为轮询
                _log(f"dequeue hint failed: {type(exc).__name__}: {exc}")
        time.sleep(self.poll_seconds)

    def sweep_and_requeue(self) -> list[dict[str, Any]]:
        """租约超时清扫（P3-B）：接管停滞任务，并唤醒可重试的任务（Redis 信号，可丢）。"""
        results = self._store.sweep_stale_runs(self.max_attempts)
        for item in results:
            if item["action"] == "requeued" and self._queue is not None:
                try:
                    self._queue.enqueue(item["run_id"])
                except Exception as exc:  # noqa: BLE001 —— 信号失败由 PG 轮询兜底
                    _log(f"requeue {item['run_id']} signal failed: {type(exc).__name__}: {exc}")
        return results

    def run_once(self, run_id: str) -> bool:
        """按 id 认领并执行（兼容入口 / 测试）；认领失败（已被领走 / 非 QUEUED）返回 False。"""
        row = self._store.claim_run(run_id, self.worker_id, self.lease_seconds)
        if row is None:
            return False
        self._run_claimed(row)
        return True

    def _run_claimed(self, row: dict[str, Any]) -> None:
        """执行已认领的任务（心跳 + 执行 + 崩溃兜底）。"""
        run_id = row["run_id"]
        self._current_run_id = run_id
        # P1-4：逐调用用量落进 usage_ledger（attempt = 本行 attempt）
        usage_token = push_usage_sink(make_store_sink(
            self._store, run_id=run_id, attempt=int(row.get("attempt") or 1)))
        hb_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, args=(run_id, hb_stop),
            name=f"worker-hb-{run_id}", daemon=True,
        )
        heartbeat.start()
        try:
            # P1-8：后台任务独立根 span（未启用 OTel 时 no-op）
            with run_span("dr.run", **{"dr.run_id": run_id,
                                       "dr.attempt": int(row.get("attempt") or 1)}):
                self._execute(run_id, row)
        except Exception as exc:  # noqa: BLE001 —— 兜底：任务必须落到终局或留给租约清扫
            self._mark_crashed(run_id, exc)
        finally:
            pop_usage_sink(usage_token)
            hb_stop.set()
            heartbeat.join(timeout=1.0)
            self._current_run_id = None

    def _heartbeat_loop(self, run_id: str, stop: threading.Event) -> None:
        while not stop.wait(self.heartbeat_seconds):
            try:
                if not self._store.renew_lease(run_id, self.worker_id, self.lease_seconds):
                    return
            except Exception as exc:  # noqa: BLE001 —— 续租失败不能中断执行
                _log(f"renew_lease({run_id}) failed: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ 执行

    def _execute(self, run_id: str, row: dict[str, Any]) -> None:
        request = row.get("request") or {}
        topic = row["topic"]
        self._apply_profile(request)
        graph = self._graph_factory()
        effective = effective_research_config()
        timeout_at = row.get("timeout_at")

        def cancel_requested() -> bool:
            fresh = self._store.get_run(run_id)
            return bool(fresh and fresh["status"] == "CANCEL_REQUESTED")

        def should_stop() -> bool:
            if timeout_at is not None and datetime.now(UTC) >= timeout_at:
                return True
            return cancel_requested()

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            self._store.append_event(run_id, event_type, payload)

        timeout_seconds = int((timeout_at - row["created_at"]).total_seconds()) if timeout_at else DEFAULT_RUN_TIMEOUT_SECONDS
        emit(RUN_STARTED, {
            "run_id": run_id,
            "topic": topic,
            "max_total_hops": effective.max_total_hops,
            "max_subquestions": effective.max_subquestions,
            "search_provider": config.search.provider,
            "enable_arxiv": config.search.enable_arxiv,
            "timeout_seconds": timeout_seconds,
        })

        seen_progress = 0
        # P5：检索作用域 = 本 run 的所有者（Worker 串行执行，每次开跑前覆盖）
        set_scope(user_id=row.get("user_id"))
        budget_limit = row.get("budget_limit_cny")
        last_state = None
        budget_exceeded = False
        for step in graph.iter_run(
            topic, request.get("instructions", ""), thread_id=run_id, should_cancel=should_stop
        ):
            if step.terminal:
                timed_out = (
                    step.stop_reason == STOP_CANCELLED
                    and timeout_at is not None and datetime.now(UTC) >= timeout_at
                    and not cancel_requested()
                )
                self._finish(run_id, row, step, timed_out)
                return

            emit(STEP_FINISHED, {
                "node": step.node,
                "index": step.index,
                "duration_ms": step.duration_ms,
                "depth": step.state.depth,
                "token_used": step.state.token_used,
            })
            added = step.state.progress[seen_progress:]
            seen_progress = len(step.state.progress)
            emit(STATE_DELTA, {
                "progress_added": added,
                "findings_count": len(step.state.findings),
                "visited_sources_count": len(step.state.visited_sources),
                "planner_events_count": len(step.state.planner_events),
                "run_status": step.state.run_status,
            })
            for degradation in step.new_degradations:
                emit(DEGRADATION, {
                    "node": degradation.node,
                    "component": degradation.component,
                    "reason": degradation.reason,
                    "detail": degradation.detail,
                    "fallback_action": degradation.fallback_action,
                })

            # 预算闸（P3-B）：单 run 预算在**节点边界**检查（与取消/超时同一检查点）。
            # 计量回写用 update_usage（不动状态，避免把 CANCEL_REQUESTED 覆盖回 RUNNING）。
            last_state = step.state
            cost = _estimate_cost_cny(step.state.token_used)
            self._store.update_usage(
                run_id,
                token_used=step.state.token_used,
                cost_estimate_cny=cost,
                budget_used_cny=cost,
            )
            if budget_limit is not None and cost >= float(budget_limit):
                budget_exceeded = True
                break

        if budget_exceeded and last_state is not None:
            # 与取消/超时同口径：预算停止不写 research_status；stop_reason=budget_exceeded。
            self._persist_result(run_id, row, last_state, "budget_exceeded", cancelled=False)

    def _finish(self, run_id: str, row: dict[str, Any], step: RunStep, timed_out: bool) -> None:
        topic = row["topic"]
        if step.stop_reason == STOP_ERROR:
            err = step.state.error or {}
            payload = error_payload(
                err.get("code", "unknown") if err else "unknown",
                err.get("message", "") if err else "",
                node=err.get("node") if err else None,
                component="graph",
            )
            if not persist_terminal(self._store, run_id, None, RUN_ERROR, payload, topic=topic):
                _log(f"finalize {run_id}: terminal_conflict，错误终局写入整体回滚")
            return

        stop_reason = STOP_TIMEOUT if timed_out else step.stop_reason
        cancelled = step.stop_reason == STOP_CANCELLED and not timed_out
        self._persist_result(run_id, row, step.state, stop_reason, cancelled=cancelled)

    def _persist_result(self, run_id: str, row: dict[str, Any], state, stop_reason: str, *,
                        cancelled: bool) -> None:
        """把一次执行的结果写成终局（正常完成 / 取消 / 超时 / 预算停止共用）。"""
        topic = row["topic"]
        report = state.report_display or state.report
        result = {
            "report": report,
            "citations": [citation.model_dump(mode="json") for citation in state.citations],
            "validator_stats": state.validator_stats,
            "depth": state.depth,
            "visited_sources": list(state.visited_sources),
            "reflection_log": list(state.reflection_log),
        }
        cost = _estimate_cost_cny(state.token_used)
        started = row.get("started_at") or row["created_at"]
        elapsed = round(max(0.0, (datetime.now(UTC) - started).total_seconds()), 1)
        payload = {
            "cancelled": cancelled,
            "stop_reason": stop_reason,
            "run_status": state.run_status,
            "token_used": state.token_used,
            "cost_estimate_cny": cost,
            "elapsed_seconds": elapsed,
            "degradation_count": len(state.degradation_log),
            "has_report": bool(report),
            "result": result,
        }
        meta = {
            "run_status": state.run_status,
            "stop_reason": stop_reason,
            "cancelled": cancelled,
            "token_used": state.token_used,
            "cost_estimate_cny": cost,
            "budget_used_cny": cost,
            "elapsed_seconds": elapsed,
            "degradation_count": len(state.degradation_log),
            "depth": state.depth,
        }
        # P0-4：输出侧统一闸 —— 命中词表则**发帧与落库前**脱敏（正文只留产物）。
        matches = apply_output_gate(payload, report)
        # P1-9：导出载荷随附 run 创建时固化的数据流向快照
        egress = (row.get("request") or {}).get("egress")
        finalized = persist_terminal(self._store, run_id, None, RUN_FINISHED, payload,
                                     result=result, report=report, meta=meta, topic=topic,
                                     moderation_status="flagged" if matches else None,
                                     egress=egress)
        if not finalized:
            # 状态已被清扫 / 强制收口抢先：终局写入整体回滚（P0-6）
            _log(f"finalize {run_id}: terminal_conflict，终局写入整体回滚")
            return
        # P7-A：输出侧内容标记（命中词表 ⇒ flagged + 审核记录；不删除正文）
        flag_report(self._store, run_id, row.get("user_id"), report)

    def _mark_crashed(self, run_id: str, exc: Exception) -> None:
        payload = error_payload("runner_crash", f"{type(exc).__name__}: {exc}"[:500])
        try:
            persist_terminal(self._store, run_id, None, RUN_ERROR, payload)
        except Exception as persist_exc:  # noqa: BLE001 —— 留给 P3-B 租约清扫
            _log(f"persist crash for {run_id} failed: {type(persist_exc).__name__}: {persist_exc}")

    @staticmethod
    def _apply_profile(request: dict[str, Any]) -> None:
        """把 run 创建时固化的档位快照装进本线程运行作用域（P0 profile 固化）。

        旧行（P3 之前创建，request 里只有 max_total_hops 等覆盖字段）不回放
        客户端覆盖值：快照缺失时按全局 config 运行（安全默认），
        超时 / 单次预算仍按该行已落库的 `timeout_at` / `budget_limit_cny` 生效。
        """
        snapshot = request.get("profile")
        if isinstance(snapshot, dict):
            try:
                set_profile(RuntimeProfile.from_snapshot(snapshot))
                return
            except (KeyError, TypeError):
                pass
        set_profile(None)


def main() -> int:
    dsn = (os.getenv("DR_DATABASE_URL") or "").strip()
    if not dsn:
        _log("DR_DATABASE_URL 必填（任务库；P0-2 起派发权威在 PostgreSQL）")
        return 2
    redis_url = (os.getenv("DR_REDIS_URL") or "").strip()
    queue = RunQueue(redis_url) if redis_url else None
    setup_otel("deepresearch-worker")  # P1-8：默认关闭，配置导出端点才启用
    worker = Worker(RunStore(dsn), queue)
    if queue is None:
        _log("未配置 DR_REDIS_URL：仅按任务库轮询领取（唤醒信号降级）")

    def _handle_stop(_signum, _frame):
        _log("收到停止信号：完成当前任务后退出")
        worker.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _handle_stop)
        except (ValueError, OSError):
            pass

    _log(f"{worker.worker_id} 启动（lease={worker.lease_seconds}s, heartbeat={worker.heartbeat_seconds}s）")
    worker.run_forever()
    _log("已退出")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
