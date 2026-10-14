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
from research_engine.evidence import serialize_evidence_index
from research_engine.evidence_manifest import build_evidence_manifest  # 需求 35 R2
from research_engine.graph import DeepResearchGraph, create_graph
from research_engine.rag.scope import set_scope
from research_engine.report_stream import pop_report_sink, push_report_sink
from research_engine.runtime_profile import (
    RuntimeProfile,
    effective_research_config,
    reset_task_deadline,
    set_profile,
    set_task_deadline,
)
from research_engine.snapshots import (
    FetchContext,
    pop_fetch_context,
    push_fetch_context,
)
from research_engine.streaming import (
    STOP_CANCELLED,
    STOP_ERROR,
    STOP_SUSPENDED,
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
from .checkpoint import get_pg_checkpointer  # 需求 35 R3：PG checkpoint（跨进程恢复）
from .deletion import process_deletions_once
from .errors import error_payload
from .hitl import (
    KIND_CLARIFICATION,
    new_interaction_id,
)
from .hitl import (
    KINDS as HITL_KINDS,
)
from .ingestion import process_ingestions_once, purge_expired_documents
from .moderation import apply_output_gate, evaluate_output
from .otel import run_span, setup_otel
from .persistence import persist_terminal
from .queue import RunQueue
from .rag_pipeline import cleanup_stale_generations
from .report_events import ReportEventBuffer  # 需求 35 R4：报告流式事件缓冲
from .retention import run_retention
from .runner import _env_int, _estimate_cost_cny
from .snapshots import PgSnapshotStore
from .store import LeaseLostError, RunOwnership, RunStore
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


def _env_flag(name: str, default: str = "false") -> bool:
    return (os.getenv(name) or default).strip().lower() in {"1", "true", "yes", "on"}


def make_graph_factory(checkpointer: Any = None) -> Callable[[], Any]:
    """Worker 图工厂（P2-7）：默认真实研究图；`DR_LOADTEST_GRAPH=1` 换演示假图。

    假图（`DemoGraph`）不调用 LLM / 检索（零费用、零外呼），事件序列与真实图同管线，
    专用于 Locust 容量标定与 staging 联调；**生产环境不得设置该开关**。

    需求 35 R3（TBD-5）：真实图传入 PG checkpointer（跨进程恢复）；None ⇒ MemorySaver。
    """
    if _env_flag("DR_LOADTEST_GRAPH"):
        from .demo_graph import DemoGraph

        try:
            step_seconds = float(os.getenv("DR_LOADTEST_STEP_SECONDS", "0.05"))
        except ValueError:
            step_seconds = 0.05
        _log(f"loadtest graph enabled（DemoGraph step={step_seconds}s，零 LLM 调用）")

        def factory() -> DemoGraph:
            return DemoGraph(step_seconds=step_seconds)

        return factory

    def factory() -> DeepResearchGraph:
        return create_graph(checkpointer=checkpointer)

    return factory


def resolve_execution_mode(graph: Any, run_id: str, attempt: int,
                           store: Any) -> tuple:
    """需求 35 R3（TBD-4/5）：解析本次执行模式与预算对账。

    - checkpoint 存在且版本兼容 ⇒ ``resumed``（从节点续跑）；预算以 ``usage_ledger``
      汇总为口径（``update_state`` 注入——防「节点已调用 LLM、未完成即崩溃」后的低估）；
    - checkpoint 存在但版本不兼容 ⇒ ``replayed``（``reason=version_incompatible``）；
    - attempt≥2 且无 checkpoint ⇒ ``replayed``（``reason=no_checkpoint``，降级重跑，
      **不算恢复成功**）；
    - 首跑无 checkpoint ⇒ ``fresh``。

    返回 ``(mode, info)``；info 含 checkpoint_tokens / ledger_tokens / reconciled / reason。
    测试替身（无 ``.graph``）按 attempt 判定（不触库）。
    """
    from research_engine.state import CHECKPOINT_SCHEMA_VERSION

    inner = getattr(graph, "graph", None)
    has_checkpoint = False
    version_ok = False
    checkpoint_tokens = 0
    if inner is not None:
        try:
            snapshot = inner.get_state({"configurable": {"thread_id": run_id}})
            values = getattr(snapshot, "values", None) or {}
            if values:
                has_checkpoint = True
                version_ok = (values.get("checkpoint_schema_version")
                              == CHECKPOINT_SCHEMA_VERSION)
                checkpoint_tokens = int(values.get("token_used", 0) or 0)
        except Exception as exc:  # noqa: BLE001 —— 读取失败按「无 checkpoint」处理
            _log(f"resume {run_id}: checkpoint 读取失败（按无 checkpoint）：{exc}")
            has_checkpoint = False
    if has_checkpoint and version_ok:
        ledger_tokens = checkpoint_tokens
        try:
            ledger_tokens = int(store.sum_llm_tokens(run_id) or 0)
        except Exception as exc:  # noqa: BLE001 —— 账本不可读时退回 checkpoint 值
            _log(f"resume {run_id}: 账本汇总失败（退回 checkpoint 值）：{exc}")
        reconciled = ledger_tokens > checkpoint_tokens
        if reconciled and inner is not None:
            try:
                # TBD-4：恢复时 state 预算以账本汇总初始化（不重置、不低估）
                inner.update_state({"configurable": {"thread_id": run_id}},
                                   {"token_used": ledger_tokens})
            except Exception as exc:  # noqa: BLE001 —— 对账写回失败不阻塞执行
                _log(f"resume {run_id}: 预算对账写回失败（忽略）：{exc}")
        return "resumed", {"checkpoint_tokens": checkpoint_tokens,
                           "ledger_tokens": ledger_tokens, "reconciled": reconciled}
    if has_checkpoint and not version_ok:
        return "replayed", {"checkpoint_tokens": checkpoint_tokens,
                            "reason": "version_incompatible"}
    if attempt >= 2:
        return "replayed", {"reason": "no_checkpoint"}
    return "fresh", {}


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
        # 需求 35 R3：本次执行的模式与预算对账信息（fresh/resumed/replayed；终局 payload 用）
        self._execution_info: dict[str, Any] = {}
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
                        # 需求 36（TBD-3）：等待态到期收口（澄清→空回答继续；审批→TIMED_OUT）
                        expired = self.expire_awaiting()
                        if expired:
                            _log(f"expire awaiting: {expired}")
                    except Exception as exc:  # noqa: BLE001 —— 到期收口失败不拖垮消费循环
                        _log(f"expire awaiting failed: {type(exc).__name__}: {exc}")
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
                    try:
                        # 需求 23：退役 / 失败代清理（删向量 → 删产物；失败下轮重试）
                        cleaned = cleanup_stale_generations(self._store)
                        if cleaned["scanned"]:
                            _log(f"rag generations: {cleaned}")
                    except Exception as exc:  # noqa: BLE001
                        _log(f"rag generation cleanup failed: {type(exc).__name__}: {exc}")
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
            overdue_appeals=self._store.count_appeals_overdue(datetime.now(UTC)),
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

    def expire_awaiting(self) -> list[dict[str, Any]]:
        """需求 36（TBD-3）：等待态到期收口，并唤醒「澄清到期自动继续」的任务。

        澄清到期 ⇒ 库里已转 `QUEUED`（空回答留痕），这里补发 Redis 唤醒（可丢，PG 轮询兜底）；
        审批到期 ⇒ 库里已收为 `TIMED_OUT`（`approval_expired`），无需唤醒。
        """
        results = self._store.expire_awaiting_runs()
        for item in results:
            if item.get("action") == "clarified_auto" and self._queue is not None:
                try:
                    self._queue.enqueue(item["run_id"])
                except Exception as exc:  # noqa: BLE001 —— 信号失败由 PG 轮询兜底
                    _log(f"expire requeue {item['run_id']} signal failed: "
                         f"{type(exc).__name__}: {exc}")
        return results

    def run_once(self, run_id: str) -> bool:
        """按 id 认领并执行（兼容入口 / 测试）；认领失败（已被领走 / 非 QUEUED）返回 False。"""
        row = self._store.claim_run(run_id, self.worker_id, self.lease_seconds)
        if row is None:
            return False
        self._run_claimed(row)
        return True

    def _run_claimed(self, row: dict[str, Any]) -> None:
        """执行已认领的任务（心跳 + 执行 + 崩溃兜底）。

        审计 F04：本次执行的所有写入携带 `RunOwnership(worker_id, attempt)`；租约
        被接管后 `lease_lost` 置位 ⇒ 节点边界停止，且不再写事件 / 用量 / 终局
        （旧执行者的迟到写入在 store 层被条件更新拒绝）。
        """
        run_id = row["run_id"]
        self._current_run_id = run_id
        attempt = int(row.get("attempt") or 1)
        ownership = RunOwnership(self.worker_id, attempt)
        lease_lost = threading.Event()
        # F13（审计）：任务剩余时限注入运行作用域（LLM 调用按剩余时间收窄超时；
        # 时限已过则拒绝新调用——节点最大耗时因此可证有界）
        timeout_at = row.get("timeout_at")
        deadline_token = None
        fetch_token = None
        if timeout_at is not None:
            deadline_token = set_task_deadline(
                (timeout_at - datetime.now(UTC)).total_seconds())
        # P1-4：逐调用用量落进 usage_ledger（attempt = 本行 attempt）
        usage_token = push_usage_sink(make_store_sink(
            self._store, run_id=run_id, attempt=attempt))
        # 需求 34 E1：本 run 的网页抓取上下文（预算三闸 + robots + 熔断 + 快照落盘）
        if config.web_snapshot.enabled:
            fetch_token = push_fetch_context(FetchContext.create(
                store=PgSnapshotStore(self._store), run_id=run_id))
        # 需求 35 R4（TBD-6）：报告正文流式旁路（REPORT_DELTA / REPORT_RESET 落 run_events）
        report_buffer = None
        report_token = None
        if config.report_stream.enabled:
            def _emit_report_event(event_type: str, payload: dict[str, Any]) -> None:
                try:
                    self._store.append_event(run_id, event_type, payload, owner=ownership)
                except LeaseLostError:
                    pass  # 租约已失效：流式旁路静默（执行侧在节点边界收口）
                except Exception as exc:  # noqa: BLE001 —— 体感旁路不打断主链路
                    _log(f"run {run_id}: report event append failed: "
                         f"{type(exc).__name__}: {exc}")

            report_buffer = ReportEventBuffer(
                _emit_report_event, flush_chars=config.report_stream.flush_chars)
            report_token = push_report_sink(report_buffer.on_delta, report_buffer.on_reset)
        hb_stop = threading.Event()
        heartbeat = threading.Thread(
            target=self._heartbeat_loop, args=(run_id, hb_stop, lease_lost, attempt),
            name=f"worker-hb-{run_id}", daemon=True,
        )
        heartbeat.start()
        try:
            # P1-8：后台任务独立根 span（未启用 OTel 时 no-op）
            with run_span("dr.run", **{"dr.run_id": run_id, "dr.attempt": attempt}):
                self._execute(run_id, row, ownership, lease_lost, report_buffer)
        except LeaseLostError:
            # 租约已被接管（或任务已终局）：本执行者放弃全部写入，由新执行者收口
            _log(f"run {run_id}: 租约已失效（attempt={attempt} 被接管），放弃本次执行")
        except Exception as exc:  # noqa: BLE001 —— 兜底：任务必须落到终局或留给租约清扫
            if lease_lost.is_set():
                _log(f"run {run_id}: 租约已失效，跳过崩溃落库（{type(exc).__name__}: {exc}）")
            else:
                self._mark_crashed(run_id, exc, ownership)
        finally:
            if deadline_token is not None:
                reset_task_deadline(deadline_token)
            if fetch_token is not None:
                pop_fetch_context(fetch_token)
            if report_buffer is not None:
                report_buffer.flush()  # 兜底冲刷（异常路径也不丢尾部增量）
            if report_token is not None:
                pop_report_sink(report_token)
            pop_usage_sink(usage_token)
            hb_stop.set()
            heartbeat.join(timeout=1.0)
            self._current_run_id = None
            self._execution_info = {}

    def _heartbeat_loop(self, run_id: str, stop: threading.Event,
                        lease_lost: threading.Event, attempt: int) -> None:
        """续租心跳；失败或超期未续上 ⇒ 置位 `lease_lost`（执行侧在节点边界收口）。

        R10：续租携带本执行的 ``attempt`` —— 同 worker_id 的旧 attempt 不能续租。
        """
        last_ok = time.monotonic()
        while not stop.wait(self.heartbeat_seconds):
            try:
                if not self._store.renew_lease(run_id, self.worker_id, self.lease_seconds,
                                               attempt=attempt):
                    _log(f"renew_lease({run_id}) rejected: 租约已被接管或任务已终局")
                    lease_lost.set()
                    return
                last_ok = time.monotonic()
            except Exception as exc:  # noqa: BLE001 —— 续租失败不能中断执行（本轮）
                # 数据库不可达时无法确认租约：超过一个租约时长仍未续上 ⇒ 视为丢失
                # （新执行者可能已被清扫接管），本地止损（审计 F04）。
                if time.monotonic() - last_ok >= self.lease_seconds:
                    _log(f"renew_lease({run_id}) 超过 {self.lease_seconds}s 未成功"
                         f"（{type(exc).__name__}: {exc}），视为租约丢失")
                    lease_lost.set()
                    return
                _log(f"renew_lease({run_id}) failed: {type(exc).__name__}: {exc}")

    # ------------------------------------------------------------------ 执行

    def _execute(self, run_id: str, row: dict[str, Any], ownership: RunOwnership,
                 lease_lost: threading.Event,
                 report_buffer: Any = None) -> None:
        request = row.get("request") or {}
        topic = row["topic"]
        self._apply_profile(request)
        # P5：检索作用域须在 create_graph **之前**设置（Researcher 构建时捕获作用域，
        # 避免 LangGraph 并行线程丢失 ContextVar → 运行期 RAG 恒零命中）；
        # 修订号从任务库读（缓存键必须拿真实修订号，上传/删除后才会失效）
        try:
            rag_revision = self._store.get_rag_revision()
        except Exception:  # noqa: BLE001
            rag_revision = 0
        set_scope(user_id=row.get("user_id"), revision=rag_revision)
        graph = self._graph_factory()
        # 需求 35 R3（TBD-4/5）：解析执行模式（fresh/resumed/replayed）+ 预算对账
        execution_mode, resume_info = resolve_execution_mode(
            graph, run_id, ownership.attempt, self._store)
        self._execution_info = {"mode": execution_mode, **resume_info}
        if execution_mode != "fresh":
            _log(f"run {run_id}: 执行模式={execution_mode} attempt={ownership.attempt} {resume_info}")
        effective = effective_research_config()
        timeout_at = row.get("timeout_at")

        def cancel_requested() -> bool:
            fresh = self._store.get_run(run_id)
            return bool(fresh and fresh["status"] == "CANCEL_REQUESTED")

        def should_stop() -> bool:
            if lease_lost.is_set():
                return True
            if timeout_at is not None and datetime.now(UTC) >= timeout_at:
                return True
            return cancel_requested()

        def emit(event_type: str, payload: dict[str, Any]) -> None:
            # 审计 F04：事件追加携带所有权；租约被接管时 store 抛 LeaseLostError
            self._store.append_event(run_id, event_type, payload, owner=ownership)

        timeout_seconds = int((timeout_at - row["created_at"]).total_seconds()) if timeout_at else DEFAULT_RUN_TIMEOUT_SECONDS
        emit(RUN_STARTED, {
            "run_id": run_id,
            "topic": topic,
            "max_total_hops": effective.max_total_hops,
            "max_subquestions": effective.max_subquestions,
            "search_provider": config.search.provider,
            "enable_arxiv": config.search.enable_arxiv,
            "timeout_seconds": timeout_seconds,
            # 需求 35 R3（TBD-5）：执行模式显式落库（run_events）
            "execution_mode": execution_mode,
        })

        seen_progress = 0
        # P5：作用域已在 create_graph 之前设置（见 _execute 开头）
        budget_limit = row.get("budget_limit_cny")
        last_state = None
        budget_exceeded = False
        # 需求 36（§12.2 恢复事务）：HITL 回答载荷随 resumed 注入（Command(resume=...)）；
        # 崩溃恢复（R3）无回答载荷 ⇒ resume_value=None 走 stream(None) 纯续跑。
        resume_value = None
        if execution_mode == "resumed":
            fresh = self._store.get_run(run_id)
            answer = (fresh or {}).get("interaction_answer")
            if isinstance(answer, dict):
                resume_value = answer
        run_kwargs: dict[str, Any] = {"resume": execution_mode == "resumed"}
        if resume_value is not None:
            # 仅 HITL 恢复携带回答值；R3 崩溃恢复 / 兼容替身不传该 kwarg
            run_kwargs["resume_value"] = resume_value
        for step in graph.iter_run(
            topic, request.get("instructions", ""), thread_id=run_id,
            should_cancel=should_stop, **run_kwargs,
        ):
            if step.terminal:
                if lease_lost.is_set():
                    # 租约已被接管：不写终局（新执行者负责收口），直接放弃
                    _log(f"run {run_id}: 租约已失效，跳过终局写入（等待新执行者）")
                    return
                if step.stop_reason == STOP_SUSPENDED:
                    # 需求 36（§12.2 挂起事务）：持久化等待态 + 交互卡（同事务）并释放租约；
                    # 本次 attempt 到此结束（不写终局）；回答后由新 attempt 以 resumed 续跑。
                    if report_buffer is not None:
                        report_buffer.flush()
                    self._suspend(run_id, row, step, ownership)
                    return
                if report_buffer is not None:
                    report_buffer.flush()  # 终局前冲刷尾部增量（RUN_FINISHED 之前）
                timed_out = (
                    step.stop_reason == STOP_CANCELLED
                    and timeout_at is not None and datetime.now(UTC) >= timeout_at
                    and not cancel_requested()
                )
                self._finish(run_id, row, step, timed_out, ownership)
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
            try:
                self._store.update_usage(
                    run_id,
                    token_used=step.state.token_used,
                    cost_estimate_cny=cost,
                    budget_used_cny=cost,
                    owner=ownership,
                )
            except LeaseLostError:
                # 审计 F04：旧 attempt 的用量回写被拒 ⇒ 停止执行，不覆盖新执行者计量
                lease_lost.set()
                _log(f"run {run_id}: 用量回写被拒（租约已失效），停止本跳")
                return
            if budget_limit is not None and cost >= float(budget_limit):
                budget_exceeded = True
                break

        if lease_lost.is_set():
            return  # 租约已失效：不写预算终局，交给新执行者
        if budget_exceeded and last_state is not None:
            # 与取消/超时同口径：预算停止不写 research_status；stop_reason=budget_exceeded。
            self._persist_result(run_id, row, last_state, "budget_exceeded",
                                 cancelled=False, ownership=ownership)

    def _suspend(self, run_id: str, row: dict[str, Any], step: RunStep,
                 ownership: RunOwnership) -> None:
        """需求 36（§12.2 挂起事务）：把 interrupt 挂起持久化为等待态 + 卡片，并释放租约。

        - 非法 kind ⇒ 按崩溃收口（错误可归因，不静默）；
        - `suspend_run` 抛 `LeaseLostError` ⇒ 向上传播（本执行者放弃全部写入）；
        - 返回 None（状态已非 RUNNING）⇒ 按当前状态收口：`CANCEL_REQUESTED` 走取消终局，
          其余（已被强制收口/终局）放弃执行。
        """
        payload = step.interrupt or {}
        kind = str(payload.get("kind") or "")
        if kind not in HITL_KINDS:
            self._mark_crashed(run_id,
                               RuntimeError(f"invalid HITL interrupt kind: {kind!r}"),
                               ownership)
            return
        ttl = (config.hitl.clarify_ttl_seconds if kind == KIND_CLARIFICATION
               else config.hitl.approval_ttl_seconds)
        content = payload.get("content")
        updated = self._store.suspend_run(
            run_id,
            owner=ownership,
            kind=kind,
            interaction_id=new_interaction_id(),
            round_no=int(payload.get("round") or 1),
            content=content if isinstance(content, dict) else {},
            ttl_seconds=ttl,
        )
        if updated is None:
            fresh = self._store.get_run(run_id)
            status = (fresh or {}).get("status")
            if status == "CANCEL_REQUESTED":
                # 挂起与取消在同一边界竞争：尊重用户，按取消收口（绝不挂起）
                self._persist_result(run_id, row, step.state, "cancelled",
                                     cancelled=True, ownership=ownership)
            else:
                _log(f"run {run_id}: 挂起被拒（status={status}），放弃本次执行")
            return
        _log(f"run {run_id}: 已挂起（{kind} round={updated.get('interaction_round')}，"
             f"到期 {updated.get('awaiting_expires_at')}）")

    def _finish(self, run_id: str, row: dict[str, Any], step: RunStep, timed_out: bool,
                ownership: RunOwnership) -> None:
        topic = row["topic"]
        if step.stop_reason == STOP_ERROR:
            err = step.state.error or {}
            payload = error_payload(
                err.get("code", "unknown") if err else "unknown",
                err.get("message", "") if err else "",
                node=err.get("node") if err else None,
                component="graph",
            )
            if not persist_terminal(self._store, run_id, None, RUN_ERROR, payload,
                                    topic=topic, owner=ownership):
                _log(f"finalize {run_id}: terminal_conflict，错误终局写入整体回滚")
            return

        stop_reason = STOP_TIMEOUT if timed_out else step.stop_reason
        cancelled = step.stop_reason == STOP_CANCELLED and not timed_out
        self._persist_result(run_id, row, step.state, stop_reason, cancelled=cancelled,
                             ownership=ownership)

    def _persist_result(self, run_id: str, row: dict[str, Any], state, stop_reason: str, *,
                        cancelled: bool, ownership: RunOwnership) -> None:
        """把一次执行的结果写成终局（正常完成 / 取消 / 超时 / 预算停止共用）。"""
        topic = row["topic"]
        report = state.report_display or state.report
        # 需求 35 R3（TBD-4）：终局成本与账本对账——账本为累计事实源，绝不低估。
        # replayed 重跑路径 state 从零累计，但已死尝试的调用已入账（usage_ledger 含 attempt）；
        # resumed 路径亦有兜底（注入值 vs 账本在竞态下的守恒校验）。
        try:
            ledger_tokens = int(self._store.sum_llm_tokens(run_id) or 0)
        except Exception:  # noqa: BLE001 —— 对账失败不阻塞终局写入
            ledger_tokens = 0
        token_used = max(int(state.token_used or 0), ledger_tokens)
        if ledger_tokens > int(state.token_used or 0):
            self._execution_info = {**self._execution_info,
                                    "ledger_tokens": ledger_tokens,
                                    "final_tokens": token_used}
        result = {
            "report": report,
            "citations": [citation.model_dump(mode="json") for citation in state.citations],
            "validator_stats": state.validator_stats,
            "depth": state.depth,
            "visited_sources": list(state.visited_sources),
            "reflection_log": list(state.reflection_log),
            # F08（审计）：轻量证据索引（稳定 ID/内容 hash/定位/检索时间，不含正文）
            # —— 与 RunManager 路径同口径，导出可复核证据链；摘要层可经 origin_evidence_ids 回指这些条目
            "evidence_index": serialize_evidence_index(state.findings),
            # 需求 35 R3（TBD-4/5）：执行模式与预算对账（导出可审计）
            "execution_mode": self._execution_info.get("mode", "fresh"),
            "budget_reconciliation": self._execution_info,
        }
        cost = _estimate_cost_cny(token_used)
        started = row.get("started_at") or row["created_at"]
        elapsed = round(max(0.0, (datetime.now(UTC) - started).total_seconds()), 1)
        payload = {
            "cancelled": cancelled,
            "stop_reason": stop_reason,
            "run_status": state.run_status,
            "token_used": token_used,
            "cost_estimate_cny": cost,
            "elapsed_seconds": elapsed,
            "degradation_count": len(state.degradation_log),
            "has_report": bool(report),
            "result": result,
            # 需求 35 R3（TBD-5）：执行模式 + 预算对账（终局 payload 显式留痕）
            "execution_mode": self._execution_info.get("mode", "fresh"),
            "budget_reconciliation": self._execution_info,
        }
        meta = {
            "run_status": state.run_status,
            "stop_reason": stop_reason,
            "cancelled": cancelled,
            "token_used": token_used,
            "cost_estimate_cny": cost,
            "budget_used_cny": cost,
            "elapsed_seconds": elapsed,
            "degradation_count": len(state.degradation_log),
            "depth": state.depth,
        }
        # P0-4：输出侧统一闸 —— **唯一一次**审核调用（不可变决定），
        # 事件流 / 传输层按决定脱敏后再落库；正文只留产物，供审核/管理员复核。
        decision = evaluate_output(report)
        apply_output_gate(payload, decision)
        # P1-9：导出载荷随附 run 创建时固化的数据流向快照
        egress = (row.get("request") or {}).get("egress")
        finalized = persist_terminal(self._store, run_id, None, RUN_FINISHED, payload,
                                     result=result, report=report, meta=meta, topic=topic,
                                     moderation=decision,
                                     egress=egress, owner=ownership,
                                     evidence_manifest=build_evidence_manifest(run_id, state))
        if not finalized:
            # 状态已被清扫 / 强制收口抢先 / 所有权已失效：终局写入整体回滚（P0-6 / F04）
            _log(f"finalize {run_id}: terminal_conflict 或租约已失效，终局写入整体回滚")
            return

    def _mark_crashed(self, run_id: str, exc: Exception, ownership: RunOwnership) -> None:
        payload = error_payload("runner_crash", f"{type(exc).__name__}: {exc}"[:500])
        try:
            persist_terminal(self._store, run_id, None, RUN_ERROR, payload, owner=ownership)
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
    # 需求 35 R3（TBD-5）：PG checkpoint（不可用 ⇒ None，回落 MemorySaver/重跑语义）
    worker = Worker(RunStore(dsn), queue,
                    graph_factory=make_graph_factory(get_pg_checkpointer(dsn)))
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
