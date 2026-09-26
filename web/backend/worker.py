"""独立 Worker（P3-A）：从 Redis 队列领取任务 → 执行 graph → 写任务库。

与 RunManager（进程内执行）共用 `persistence.persist_terminal`，保证同一停止原因
写出同一状态；差别只在事件写入方式：

- Worker 是单 run 的唯一写者 ⇒ 事件序号由数据库分配（`MAX(sequence)+1`，`seq=None`）；
- 没有内存帧 —— SSE 由 API 从 `run_events` 实时尾随（`main._stored_event_gen`）。

租约（P3-A 范围）：
- 认领时写 `worker_id` + `lease_expires_at`（默认 120s），执行期由心跳线程续租（默认 30s）；
- 节点在飞时单节点最长实测约 31s（需求 9 §5.4.1）⇒ 租约覆盖有余；
- **崩溃后任务的接管**（租约超时扫描 / 重试 / `LOST`）属 P3-B，本批只保证租约持续续期。

启动：`python -m web.backend.worker`（需 `DR_DATABASE_URL` + `DR_REDIS_URL`）。
"""
from __future__ import annotations

import os
import signal
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from typing import Any, Callable, Optional

from config import config
from research_engine.graph import DeepResearchGraph, create_graph
from research_engine.streaming import (
    STOP_CANCELLED,
    STOP_ERROR,
    STOP_TIMEOUT,
    RunStep,
)

from .agui import (
    DEGRADATION,
    RUN_ERROR,
    RUN_FINISHED,
    RUN_STARTED,
    STATE_DELTA,
    STEP_FINISHED,
)
from .errors import error_payload
from .persistence import persist_terminal
from .queue import RunQueue
from .runner import _env_int, _estimate_cost_cny
from .store import RunStore

DEFAULT_LEASE_SECONDS = 120
DEFAULT_HEARTBEAT_SECONDS = 30
DEFAULT_POLL_SECONDS = 5.0
DEFAULT_SWEEP_SECONDS = 30
DEFAULT_MAX_ATTEMPTS = 2
DEFAULT_RUN_TIMEOUT_SECONDS = 3600


def _log(message: str) -> None:
    print(f"[worker] {message}", file=sys.stderr, flush=True)


class Worker:
    def __init__(
        self,
        store: RunStore,
        queue: RunQueue,
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

    # ------------------------------------------------------------------ 主循环

    def stop(self) -> None:
        self._stop.set()

    def run_forever(self) -> None:
        next_sweep = 0.0  # 启动即清扫一次：接管上次进程崩溃留下的过期租约
        while not self._stop.is_set():
            if time.monotonic() >= next_sweep:
                try:
                    swept = self.sweep_and_requeue()
                    if swept:
                        _log(f"sweep: {swept}")
                except Exception as exc:  # noqa: BLE001 —— 清扫失败不拖垮消费循环
                    _log(f"sweep failed: {type(exc).__name__}: {exc}")
                next_sweep = time.monotonic() + self.sweep_seconds
            try:
                run_id = self._queue.dequeue(self.poll_seconds)
            except Exception as exc:  # noqa: BLE001 —— Redis 抖动不应打死 Worker
                _log(f"dequeue failed: {type(exc).__name__}: {exc}；5s 后重试")
                time.sleep(5)
                continue
            if run_id is None:
                continue
            try:
                self.run_once(run_id)
            except Exception as exc:  # noqa: BLE001 —— 单个任务失败不得拖垮 Worker
                _log(f"run_once({run_id}) failed: {type(exc).__name__}: {exc}")

    def sweep_and_requeue(self) -> list[dict[str, Any]]:
        """租约超时清扫（P3-B）：接管停滞任务，并把可重试的重新入队。"""
        results = self._store.sweep_stale_runs(self.max_attempts)
        for item in results:
            if item["action"] == "requeued":
                try:
                    self._queue.enqueue(item["run_id"])
                except Exception as exc:  # noqa: BLE001 —— 入队失败留给下轮清扫
                    _log(f"requeue {item['run_id']} failed: {type(exc).__name__}: {exc}")
        return results

    def run_once(self, run_id: str) -> bool:
        """认领并执行一个任务；认领失败（已被领走 / 非 QUEUED）返回 False。"""
        row = self._store.claim_run(run_id, self.worker_id, self.lease_seconds)
        if row is None:
            return False
        hb_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, args=(run_id, hb_stop),
            name=f"worker-hb-{run_id}", daemon=True,
        )
        heartbeat.start()
        try:
            self._execute(run_id, row)
        except Exception as exc:  # noqa: BLE001 —— 兜底：任务必须落到终局或留给租约清扫
            self._mark_crashed(run_id, exc)
        finally:
            hb_stop.set()
            heartbeat.join(timeout=1.0)
        return True

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
        self._apply_request_overrides(request)
        graph = self._graph_factory()
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
            "max_total_hops": config.research.max_total_hops,
            "max_subquestions": config.research.max_subquestions,
            "search_provider": config.search.provider,
            "enable_arxiv": config.search.enable_arxiv,
            "timeout_seconds": timeout_seconds,
        })

        seen_progress = 0
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
            persist_terminal(self._store, run_id, None, RUN_ERROR, payload, topic=topic)
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
        persist_terminal(self._store, run_id, None, RUN_FINISHED, payload,
                         result=result, report=report, meta=meta, topic=topic)

    def _mark_crashed(self, run_id: str, exc: Exception) -> None:
        payload = error_payload("runner_crash", f"{type(exc).__name__}: {exc}"[:500])
        try:
            persist_terminal(self._store, run_id, None, RUN_ERROR, payload)
        except Exception as persist_exc:  # noqa: BLE001 —— 留给 P3-B 租约清扫
            _log(f"persist crash for {run_id} failed: {type(persist_exc).__name__}: {persist_exc}")

    @staticmethod
    def _apply_request_overrides(request: dict[str, Any]) -> None:
        """与 RunManager._worker 相同的运行期覆盖（建图前生效）。"""
        if request.get("max_total_hops") is not None:
            config.research.max_total_hops = request["max_total_hops"]
        if request.get("max_subquestions") is not None:
            config.research.max_subquestions = request["max_subquestions"]
        if request.get("search_provider") is not None:
            config.search.provider = request["search_provider"]
        if request.get("enable_arxiv") is not None:
            config.search.enable_arxiv = request["enable_arxiv"]


def main() -> int:
    dsn = (os.getenv("DR_DATABASE_URL") or "").strip()
    redis_url = (os.getenv("DR_REDIS_URL") or "").strip()
    if not dsn or not redis_url:
        _log("DR_DATABASE_URL / DR_REDIS_URL 均为必填（任务库 + 队列）")
        return 2
    worker = Worker(RunStore(dsn), RunQueue(redis_url))

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
