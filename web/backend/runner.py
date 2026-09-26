"""研究运行的管理器：线程池驱动 `iter_run()`，产出 SSE 帧（需求 9 §7.4）。

**为什么必须用线程**：`graph.iter_run()` 是**同步生成器**（LangGraph 的 `stream()` 同步），
在 async 协程里直接跑会阻塞整个事件循环 ⇒ 放进工作线程，用 `queue.Queue` 回传帧。

**取消**：`threading.Event` + `should_cancel` 谓词，检查点在节点边界（契约 C2/C3/C5）。

P1 增补（仍严格落在 D-19「前台跑 + 可取消」模型内，**不引入任务持久化 / 队列**）：

- **运行超时闸**（P1-2）：单次 run 有墙钟时限，到点后在**节点边界**停止。
- **单进程并发限制**（P1-3）：同时活跃的 run 数封顶，超出直接拒绝（429）。
- **状态查询**（P1-4）：`snapshot()` 给出内存态的运行画像（不含报告正文）。
- **结构化错误**（P1-5）：`RUN_ERROR` 与 HTTP 错误共用 `web.backend.errors` 的载荷结构。
- **报告导出**（P1-6）：终局时把报告留在内存供 `/report` 导出（**不落盘**）。

🚨 **超时 / 强制收口都是协作式的**：Python 线程无法被 kill，因此若某个节点内部
（如一次 HTTP 调用）挂死，闸只能在**下一个节点边界**生效；硬截止（`_hard_deadline`）
只保证**传输层**把流收口、客户端不再干等，后台线程可能仍在收尾。
这条限制必须写在文档里，不能假装是抢占式超时。
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Callable, Dict, List, Optional

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
    sse_frame,
)
from .errors import ApiError, error_payload
from .export import build_export_payload, render_markdown
from .store import RunStore

# --- P1-2 / P1-3 默认值 --------------------------------------------------------
# 依据：单轮实测 48~51 分钟（W8 after 基线）⇒ 时限必须给出**真实运行的余量**，
# 否则闸会把正常研究掐死。3600s = 1h，约为实测上限的 1.2 倍。
DEFAULT_RUN_TIMEOUT_SECONDS = 3600
# 硬截止宽限：协作式停止失效（节点挂死）后，传输层最多再等这么久就强制收口。
DEFAULT_FORCED_STOP_GRACE_SECONDS = 60
# D-19 前台模型 ⇒ 同一进程一次只跑一个研究。调大即为「允许并发」，但每个 run
# 都会各开一条 LLM/搜索链路，成本与限流风险自担。
DEFAULT_MAX_CONCURRENT_RUNS = 1


def _env_int(name: str, default: int, minimum: int = 1) -> int:
    """读整数型环境变量；缺失 / 非法 / 越界一律回落默认值（不抛异常打断启动）。"""
    raw = os.getenv(name)
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    return value if value >= minimum else default


def _estimate_cost_cny(token_used: int) -> float:
    """按 ``config.llm.pricing`` 估算本次 run 的 LLM 成本（元）。

    ⚠️ 口径（必须与展示一致，否则等于又造一个假数字）：
    state 只有 ``token_used`` 总数、**没有 input/output 拆分**，因此统一按
    **最贵的 output 档**计价 ⇒ 得到的是**保守上界**，真实成本只会更低。
    与 observability 模块的成本展示口径一致（W3 Q6）。

    未计入：搜索 API 按次计费（不占 token），本估算只覆盖 LLM。
    """
    from config import config

    pricing = config.llm.pricing or {}
    unit = max((tier.get("output", 0.0) for tier in pricing.values()), default=0.0)
    return round(token_used / 1000.0 * unit, 4)


class RunManager:
    """管理多个前台运行的生命周期（**不做**任务持久化 —— ADR-0001 + D-19）。"""

    def __init__(self, graph_factory: Callable[[], DeepResearchGraph] = create_graph,
                 run_timeout_seconds: Optional[int] = None,
                 max_concurrent_runs: Optional[int] = None,
                 forced_stop_grace_seconds: Optional[int] = None,
                 store: Optional[RunStore] = None):
        self._graph_factory = graph_factory
        # P2-C：可选持久化仓储。不传（本地 / 测试 / DR_DEMO）时行为与 P1 完全一致。
        self._store = store
        self.run_timeout_seconds = (
            run_timeout_seconds
            if run_timeout_seconds is not None
            else _env_int("DR_RUN_TIMEOUT_SECONDS", DEFAULT_RUN_TIMEOUT_SECONDS)
        )
        self.max_concurrent_runs = (
            max_concurrent_runs
            if max_concurrent_runs is not None
            else _env_int("DR_MAX_CONCURRENT_RUNS", DEFAULT_MAX_CONCURRENT_RUNS)
        )
        self.forced_stop_grace_seconds = (
            forced_stop_grace_seconds
            if forced_stop_grace_seconds is not None
            else _env_int("DR_FORCED_STOP_GRACE_SECONDS", DEFAULT_FORCED_STOP_GRACE_SECONDS)
        )
        self._frames: Dict[str, List[str]] = {}   # run_id → 已发出的帧（重连回放用）
        self._queues: Dict[str, queue.Queue] = {}
        self._cancel: Dict[str, threading.Event] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._finished: set[str] = set()
        # --- P1 新增状态（全部内存态，进程退出即消失） ---
        self._status: Dict[str, Dict[str, Any]] = {}    # 运行画像（状态查询用）
        self._results: Dict[str, Dict[str, Any]] = {}   # 终局 result（导出用）
        self._reports: Dict[str, str] = {}              # 终局报告正文（导出用）
        self._meta: Dict[str, Dict[str, Any]] = {}      # 终局元数据（导出用）
        self._deadlines: Dict[str, float] = {}          # 协作式超时时刻（monotonic）
        self._hard_deadlines: Dict[str, float] = {}     # 传输层强制收口时刻
        self._timed_out: set[str] = set()
        self._forced: set[str] = set()
        self._active: set[str] = set()

    # ------------------------------------------------------------------ 生命周期

    def start(self, topic: str, instructions: str = "", max_total_hops: int | None = None,
              search_provider: str | None = None,
              enable_arxiv: bool | None = None,
              max_subquestions: int | None = None,
              idempotency_key: str | None = None) -> str:
        """启动一次研究，立即返回 `run_id`（不阻塞）。

        配置了仓储（P2-C）时：
        - 先落 `runs`（携带 `idempotency_key`），重复提交返回**既有 run_id** 且不重复执行；
        - 随后把状态推进到 `RUNNING`；持久化失败按 `persistence_error` 记入运行画像，
          不打断研究本身（读接口的降级语义见 main.py）。

        Raises:
            ApiError: 并发上限已满（`concurrency_limit`）或仓储不可用（`persistence_unavailable`）。
        """
        run_id = uuid.uuid4().hex[:12]
        now = time.monotonic()
        with self._lock:
            if len(self._active) >= self.max_concurrent_runs:
                raise ApiError(
                    "concurrency_limit",
                    f"已有 {len(self._active)} 个研究在运行，上限 {self.max_concurrent_runs}",
                    detail=f"active={len(self._active)}; limit={self.max_concurrent_runs}",
                )
            if self._store is not None:
                try:
                    row, created = self._store.create_run(
                        run_id, topic,
                        {
                            "instructions": instructions,
                            "max_total_hops": max_total_hops,
                            "search_provider": search_provider,
                            "enable_arxiv": enable_arxiv,
                            "max_subquestions": max_subquestions,
                        },
                        idempotency_key=idempotency_key,
                        timeout_at=datetime.now(UTC) + timedelta(seconds=self.run_timeout_seconds),
                    )
                except Exception as exc:  # noqa: BLE001 —— 持久化是硬前提，失败即明确报错
                    raise ApiError(
                        "persistence_unavailable",
                        f"任务创建失败：{type(exc).__name__}: {exc}"[:300],
                    ) from exc
                if not created:
                    return row["run_id"]
                run_id = row["run_id"]
            self._frames[run_id] = []
            self._queues[run_id] = queue.Queue()
            self._cancel[run_id] = threading.Event()
            self._active.add(run_id)
            self._deadlines[run_id] = now + self.run_timeout_seconds
            self._hard_deadlines[run_id] = (
                now + self.run_timeout_seconds + self.forced_stop_grace_seconds)
            self._status[run_id] = {
                "run_id": run_id,
                "topic": topic,
                "status": "running",
                "started_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "_started_monotonic": now,
                "timeout_seconds": self.run_timeout_seconds,
                "stop_reason": None,
                "cancelled": False,
                "run_status": None,
                "token_used": 0,
                "cost_estimate_cny": 0.0,
                "degradation_count": 0,
                "depth": 0,
                "findings_count": 0,
                "event_count": 0,
                "last_event_type": None,
                "has_report": False,
            }
            if self._store is not None:
                try:
                    self._store.update_status(
                        run_id, "RUNNING", allowed_from=("CREATED",),
                        started_at=datetime.now(UTC),
                    )
                except Exception as exc:  # noqa: BLE001 —— 不打断研究，画像里留痕
                    self._status[run_id]["persistence_error"] = (
                        f"{type(exc).__name__}: {exc}"[:300])
            t = threading.Thread(
                target=self._worker,
                args=(run_id, topic, instructions, max_total_hops,
                      search_provider, enable_arxiv, max_subquestions),
                name=f"research-{run_id}",
                daemon=True,
            )
            self._threads[run_id] = t
            t.start()
        return run_id

    def cancel(self, run_id: str) -> bool:
        """请求取消。立即返回（契约 C1：前端不等后端确认）。

        同时把**硬截止**提前到 `now + 宽限` ⇒ 万一节点挂死导致协作式取消不生效，
        传输层也会在宽限后把流收口（否则前端会永远停在「正在停止」）。
        """
        with self._lock:
            ev = self._cancel.get(run_id)
            if ev is None:
                return False
            ev.set()
            forced = time.monotonic() + self.forced_stop_grace_seconds
            current = self._hard_deadlines.get(run_id)
            self._hard_deadlines[run_id] = forced if current is None else min(current, forced)
        # P2-C：取消请求落库（CREATED/QUEUED → CANCELLED；RUNNING → CANCEL_REQUESTED）。
        self._persist_call(run_id, "request_cancel", run_id)
        return True

    def queue(self, run_id: str) -> Optional[queue.Queue]:
        return self._queues.get(run_id)

    def exists(self, run_id: str) -> bool:
        return run_id in self._frames

    def is_finished(self, run_id: str) -> bool:
        return run_id in self._finished

    @property
    def active_runs(self) -> int:
        with self._lock:
            return len(self._active)

    def replay(self, run_id: str, after: int | None = None) -> List[str]:
        """断线重连时回放已发出的帧（防重跑整个研究 = 双倍烧钱，风险 §5.8）。

        Args:
            after: 客户端收到的最后一个事件 id；只回放其后的帧。
                帧序号即列表下标，故切片即可。
        """
        with self._lock:
            frames = self._frames.get(run_id, [])
            if after is None:
                return list(frames)
            return list(frames[after + 1:])

    def wait_for_frame(self, run_id: str, after: int, timeout: float) -> tuple[Optional[str], bool]:
        """Wait for the next frame without sharing a destructive consumer cursor."""
        deadline = time.monotonic() + timeout
        with self._condition:
            while len(self._frames.get(run_id, [])) <= after + 1 and run_id not in self._finished:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return None, False
                self._condition.wait(remaining)

            frames = self._frames.get(run_id, [])
            if len(frames) > after + 1:
                return frames[after + 1], False
            return None, run_id in self._finished

    # ------------------------------------------------------------------ 状态查询（P1-4）

    def snapshot(self, run_id: str) -> Optional[Dict[str, Any]]:
        """一次运行的内存画像（**不含报告正文**，正文走导出接口）。

        返回 ``None`` 表示 run_id 不在本进程内存里（D-19：不做持久化，重启即失）。
        """
        with self._lock:
            st = self._status.get(run_id)
            if st is None:
                return None
            out = {k: v for k, v in st.items() if not k.startswith("_")}
            finished = run_id in self._finished
            started = st["_started_monotonic"]
            ended = st.get("_finished_monotonic")

        now = ended if (finished and ended is not None) else time.monotonic()
        elapsed = max(0.0, now - started)
        out["status"] = "finished" if finished else "running"
        out["elapsed_seconds"] = round(elapsed, 1)
        out["remaining_seconds"] = round(max(0.0, out["timeout_seconds"] - elapsed), 1)
        return out

    def overdue_reason(self, run_id: str) -> Optional[str]:
        """是否已越过**硬截止**；返回 ``"cancel"`` / ``"timeout"`` / ``None``。

        硬截止 = 协作式时限 + 宽限（或取消请求 + 宽限，取更早者）。
        越过它说明节点内部挂死、协作式停止没生效 ⇒ 只能在传输层强制收口。
        """
        with self._lock:
            if run_id in self._finished:
                return None
            hard = self._hard_deadlines.get(run_id)
            if hard is None or time.monotonic() <= hard:
                return None
            ev = self._cancel.get(run_id)
            return "cancel" if (ev is not None and ev.is_set()) else "timeout"

    def force_stop_if_overdue(self, run_id: str) -> Optional[str]:
        """越过硬截止时补发一帧 ``RUN_ERROR(stop_forced)`` 并收口；否则返回 ``None``。

        🚨 这只是**传输层**收口：后台线程可能仍在跑（Python 无法 kill 线程）。
        不写 `run_status`、不写 `degradation_log` —— 与取消同口径，不污染故障归因。
        """
        reason = self.overdue_reason(run_id)
        if reason is None:
            return None
        with self._condition:
            if run_id in self._forced or run_id in self._finished:
                return None
            self._forced.add(run_id)
            seq = len(self._frames.get(run_id, []))
            payload = error_payload(
                "stop_forced",
                f"研究未在宽限期内响应停止请求（reason={reason}），已在传输层强制收口",
                detail=f"reason={reason}; grace_seconds={self.forced_stop_grace_seconds}",
                retryable=True,
            )
            frame = sse_frame(event_id=seq, event_type=RUN_ERROR, payload=payload)
            self._frames.setdefault(run_id, []).append(frame)
            self._finished.add(run_id)
            st = self._status.get(run_id)
            if st is not None:
                st["status"] = "finished"
                st["_finished_monotonic"] = time.monotonic()
            self._active.discard(run_id)
            self._condition.notify_all()
        self._queues[run_id].put(None)
        self._persist_forced(run_id, seq, payload, reason)
        return frame

    # ------------------------------------------------------------------ 报告导出（P1-6）

    def has_result(self, run_id: str) -> bool:
        return run_id in self._results

    def export_payload(self, run_id: str) -> Optional[Dict[str, Any]]:
        """导出的结构化载荷（元数据 + result），没有终局结果时返回 ``None``。"""
        with self._lock:
            result = self._results.get(run_id)
            meta = self._meta.get(run_id)
            st = self._status.get(run_id)
        if result is None or meta is None or st is None:
            return None
        return build_export_payload(run_id=run_id, topic=st["topic"], meta=meta, result=result)

    def export_markdown(self, run_id: str) -> Optional[str]:
        payload = self.export_payload(run_id)
        return None if payload is None else render_markdown(payload)

    # ------------------------------------------------------------------ 工作线程

    def _append_locked(self, run_id: str, event_type: str, payload: Dict[str, Any]) -> tuple[str, int]:
        seq = len(self._frames[run_id])
        frame = sse_frame(event_id=seq, event_type=event_type, payload=payload)
        self._frames[run_id].append(frame)
        st = self._status.get(run_id)
        if st is not None:
            st["event_count"] = seq + 1
            st["last_event_type"] = event_type
        return frame, seq

    def _emit(self, run_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        with self._condition:
            # A forced stop closes the transport while the worker thread may still
            # unwind in the background. Do not append post-terminal frames: they
            # would make replay expose events after RUN_ERROR(stop_forced), and a
            # late terminal frame could overwrite the forced-stop semantics.
            if run_id in self._forced or run_id in self._finished:
                return
            frame, seq = self._append_locked(run_id, event_type, payload)
            self._condition.notify_all()
        self._queues[run_id].put(frame)
        self._persist_call(run_id, "append_event", run_id, event_type, payload, sequence=seq)

    def _emit_terminal_frame(self, run_id: str, event_type: str, payload: Dict[str, Any], *,
                             status_fields: Dict[str, Any],
                             result: Optional[Dict[str, Any]] = None,
                             report: Optional[str] = None,
                             meta: Optional[Dict[str, Any]] = None) -> Optional[int]:
        """终局帧与其结果/状态写入必须原子完成（同一把 condition 锁）。

        否则会与 `force_stop_if_overdue()` 形成 TOCTOU：强制收口后仍导出迟到报告，
        或在其后追加第二个终局帧、覆盖 stop_reason。

        返回终局帧的序号（`seq`）；被强制收口抢先时返回 `None`（调用方据此跳过落库）。
        """
        with self._condition:
            if run_id in self._forced or run_id in self._finished:
                return None
            if result is not None:
                self._results[run_id] = result
                self._reports[run_id] = report or ""
                self._meta[run_id] = meta or {}
            frame, seq = self._append_locked(run_id, event_type, payload)
            st = self._status.get(run_id)
            if st is not None:
                st.update(status_fields)
                st["status"] = "finished"
                st["_finished_monotonic"] = time.monotonic()
            self._finished.add(run_id)
            self._active.discard(run_id)
            self._condition.notify_all()
        self._queues[run_id].put(frame)
        return seq

    def _update_status(self, run_id: str, **fields: Any) -> None:
        with self._lock:
            st = self._status.get(run_id)
            if st is not None:
                st.update(fields)

    # ------------------------------------------------------------------ 持久化（P2-C）

    def _set_persistence_error(self, run_id: str, exc: Exception) -> None:
        """持久化失败只留痕（运行画像 `persistence_error`），绝不打断研究本身。"""
        with self._lock:
            st = self._status.get(run_id)
            if st is not None:
                st["persistence_error"] = f"{type(exc).__name__}: {exc}"[:300]

    def _persist_call(self, run_id: str, method: str, *args: Any, **kwargs: Any) -> Any:
        if self._store is None:
            return None
        try:
            return getattr(self._store, method)(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            self._set_persistence_error(run_id, exc)
            return None

    def _persist_terminal(self, run_id: str, seq: int, event_type: str,
                          payload: Dict[str, Any],
                          result: Optional[Dict[str, Any]] = None,
                          report: Optional[str] = None,
                          meta: Optional[Dict[str, Any]] = None) -> None:
        """终局落库：事件（显式帧号对齐）+ 状态 + 产物。整体失败只留痕。

        ⚠️ 时序：本方法在**内存终局之后**执行（不在 condition 锁内做 DB I/O，避免
        持久化抖动拖住传输层）。因此同一进程内，内存已 `finished` 与库中已终局之间
        可能有几十毫秒窗口；读侧的「重启后查询」场景不受影响（那时内存早已没有该 run）。
        """
        store = self._store
        if store is None:
            return
        try:
            store.append_event(run_id, event_type, payload, sequence=seq)
            if event_type == RUN_ERROR:
                new_status, stop_reason = "FAILED", "error"
            else:
                stop_reason = str(payload.get("stop_reason") or "completed")
                if stop_reason == "cancelled":
                    # 传输层值是 cancelled；库里按 §5.9.1 记 user_cancelled（区分停止原因）。
                    stop_reason = "user_cancelled"
                new_status = {
                    "completed": "SUCCEEDED",
                    "cancelled": "CANCELLED",
                    "user_cancelled": "CANCELLED",
                    "timeout": "TIMED_OUT",
                    "budget_exceeded": "CANCELLED",
                }.get(stop_reason, "SUCCEEDED")
            fields: Dict[str, Any] = {"stop_reason": stop_reason, "finished_at": datetime.now(UTC)}
            if meta:
                fields["research_status"] = meta.get("run_status")
                fields["token_used"] = meta.get("token_used")
                fields["cost_estimate_cny"] = meta.get("cost_estimate_cny")
            store.update_status(
                run_id, new_status,
                allowed_from=("CREATED", "QUEUED", "RUNNING", "CANCEL_REQUESTED"),
                **{key: value for key, value in fields.items() if value is not None},
            )
            if report:
                store.put_artifact(run_id, "report_md", report)
            if result is not None and meta is not None:
                with self._lock:
                    topic = (self._status.get(run_id) or {}).get("topic", "")
                export = build_export_payload(run_id=run_id, topic=topic, meta=meta, result=result)
                store.put_artifact(run_id, "export_json", json.dumps(export, ensure_ascii=False))
        except Exception as exc:  # noqa: BLE001
            self._set_persistence_error(run_id, exc)

    def _persist_forced(self, run_id: str, seq: int, payload: Dict[str, Any], reason: str) -> None:
        """传输层强制收口落库（与内存语义一致：不写 research_status）。"""
        store = self._store
        if store is None:
            return
        try:
            store.append_event(run_id, RUN_ERROR, payload, sequence=seq)
            store.update_status(
                run_id,
                "CANCELLED" if reason == "cancel" else "TIMED_OUT",
                allowed_from=("CREATED", "QUEUED", "RUNNING", "CANCEL_REQUESTED"),
                stop_reason="user_cancelled" if reason == "cancel" else "timeout",
                finished_at=datetime.now(UTC),
            )
        except Exception as exc:  # noqa: BLE001
            self._set_persistence_error(run_id, exc)

    def _elapsed_seconds(self, run_id: str) -> float:
        """运行已跑时长（秒，一位小数）。未记录 `_finished_monotonic` 时按当前时刻计。"""
        with self._lock:
            st = self._status.get(run_id)
            if st is None:
                return 0.0
            started = st["_started_monotonic"]
            ended = st.get("_finished_monotonic")
            finished = run_id in self._finished
        now = ended if (finished and ended is not None) else time.monotonic()
        return round(max(0.0, now - started), 1)

    def _worker(self, run_id: str, topic: str, instructions: str,
                max_total_hops: int | None,
                search_provider: str | None = None,
                enable_arxiv: bool | None = None,
                max_subquestions: int | None = None) -> None:
        try:
            from config import config

            if max_total_hops is not None:
                config.research.max_total_hops = max_total_hops
            # 子问题数上限：与跳数同构的运行期覆盖。Planner 在 plan() 里现读
            # config 拼 system prompt（build_planner_system），所以同样必须设在建图前。
            if max_subquestions is not None:
                config.research.max_subquestions = max_subquestions
            # 搜索引擎 / 学术检索：本次 run 的运行期覆盖。
            # ⚠️ 必须设在 `self._graph_factory()` **之前** —— Researcher 在 __init__
            # 里由工厂装配 provider，建图后再改 config 对本场 run 无效。
            if search_provider is not None:
                config.search.provider = search_provider
            if enable_arxiv is not None:
                config.search.enable_arxiv = enable_arxiv

            graph = self._graph_factory()
            # 把跳数上限随 RUN_STARTED 下发 ⇒ 前端才能算**真实的**检索阶段进度
            # （depth / max_total_hops），而不是做一个只会动的假条。
            # 搜索源 / 学术检索同样下发 ⇒ 前端显示的是**实际生效**的配置，不是用户点的那个。
            # 时限也一并下发 ⇒ 前端能显示剩余时间，而不是让用户干等。
            self._emit(run_id, RUN_STARTED, {
                "run_id": run_id,
                "topic": topic,
                "max_total_hops": config.research.max_total_hops,
                "max_subquestions": config.research.max_subquestions,
                "search_provider": config.search.provider,
                "enable_arxiv": config.search.enable_arxiv,
                "timeout_seconds": self.run_timeout_seconds,
            })

            cancel_event = self._cancel[run_id]
            deadline = self._deadlines.get(run_id)

            def should_stop() -> bool:
                """取消（用户意图）与超时（系统闸）共用同一个节点边界检查点。"""
                if cancel_event.is_set():
                    return True
                return deadline is not None and time.monotonic() >= deadline

            seen_progress = 0
            for step in graph.iter_run(topic, instructions, thread_id=run_id,
                                       should_cancel=should_stop):
                if step.terminal:
                    # 停止原因是 STOP_CANCELLED 但用户**没**点取消 ⇒ 是超时闸触发的。
                    # （graph 只有一个「边界停止」出口，重标只能在这一层做。）
                    if (step.stop_reason == STOP_CANCELLED and not cancel_event.is_set()
                            and deadline is not None and time.monotonic() >= deadline):
                        self._timed_out.add(run_id)
                    self._emit_terminal(run_id, step)
                    break

                self._emit(run_id, STEP_FINISHED, {
                    "node": step.node,
                    "index": step.index,
                    "duration_ms": step.duration_ms,
                    "depth": step.state.depth,
                    "token_used": step.state.token_used,
                })
                self._update_status(
                    run_id,
                    depth=step.state.depth,
                    token_used=step.state.token_used,
                    cost_estimate_cny=_estimate_cost_cny(step.state.token_used),
                    run_status=step.state.run_status,
                    degradation_count=len(step.state.degradation_log),
                    findings_count=len(step.state.findings),
                )

                # STATE_DELTA：只推 progress 新增项（全量 state 太大）
                added = step.state.progress[seen_progress:]
                seen_progress = len(step.state.progress)
                self._emit(run_id, STATE_DELTA, {
                    "progress_added": added,
                    "findings_count": len(step.state.findings),
                    "visited_sources_count": len(step.state.visited_sources),
                    "planner_events_count": len(step.state.planner_events),
                    "run_status": step.state.run_status,
                })

                # 降级实时推送 —— 硬伤 3（降级事后才可见）的解药
                for d in step.new_degradations:
                    self._emit(run_id, DEGRADATION, {
                        "node": d.node,
                        "component": d.component,
                        "reason": d.reason,
                        "detail": d.detail,
                        "fallback_action": d.fallback_action,
                    })
        except Exception as exc:  # noqa: BLE001 —— 兜底：不得让工作线程静默死掉
            payload = error_payload("runner_crash", f"{type(exc).__name__}: {exc}"[:500])
            seq = self._emit_terminal_frame(run_id, RUN_ERROR, payload, status_fields={})
            if seq is not None:
                self._persist_terminal(run_id, seq, RUN_ERROR, payload)
        finally:
            with self._condition:
                self._finished.add(run_id)
                self._active.discard(run_id)
                st = self._status.get(run_id)
                if st is not None and st.get("status") != "finished":
                    st["status"] = "finished"
                    st["_finished_monotonic"] = time.monotonic()
                self._condition.notify_all()
            self._queues[run_id].put(None)  # sentinel：通知 SSE 端点流已结束

    def _emit_terminal(self, run_id: str, step: RunStep) -> None:
        """终局事件：异常走 RUN_ERROR，正常/取消/超时走 RUN_FINISHED。

        🚨 取消与超时**都不写** run_status、不写 degradation_log（需求 9 §7.3.3），
        故此处只是**透传** state.run_status，不参与任何判定。
        """
        if step.stop_reason == STOP_ERROR:
            err = step.state.error or {}
            payload = error_payload(
                err.get("code", "unknown") if err else "unknown",
                err.get("message", "") if err else "",
                node=err.get("node") if err else None,
                component="graph",
            )
            seq = self._emit_terminal_frame(
                run_id, RUN_ERROR, payload,
                status_fields={"stop_reason": STOP_ERROR, "cancelled": False,
                               "has_report": False},
            )
            if seq is not None:
                self._persist_terminal(run_id, seq, RUN_ERROR, payload)
            return

        timed_out = run_id in self._timed_out
        stop_reason = STOP_TIMEOUT if timed_out else step.stop_reason
        cancelled = step.stop_reason == STOP_CANCELLED and not timed_out
        report = step.state.report_display or step.state.report
        result = {
            "report": report,
            "citations": [citation.model_dump(mode="json") for citation in step.state.citations],
            "validator_stats": step.state.validator_stats,
            "depth": step.state.depth,
            "visited_sources": list(step.state.visited_sources),
            "reflection_log": list(step.state.reflection_log),
        }
        cost = _estimate_cost_cny(step.state.token_used)
        elapsed = self._elapsed_seconds(run_id)
        payload = {
            # 取消与否由 stop_reason 表达，**不新增 run_status 第四态**；
            # 超时是协作式闸触发的停止，同样不是「取消」，也不是故障。
            "cancelled": cancelled,
            "stop_reason": stop_reason,
            "run_status": step.state.run_status,
            "token_used": step.state.token_used,
            # 成本估算（元）。口径见 _estimate_cost_cny：按最贵 output 单价计的**上界**。
            "cost_estimate_cny": cost,
            # 运行级统计（#14）：总耗时由后端出，刷新回放后同样权威
            "elapsed_seconds": elapsed,
            "degradation_count": len(step.state.degradation_log),
            "has_report": bool(report),
            "result": result,
        }
        meta = {
            "run_status": step.state.run_status,
            "stop_reason": stop_reason,
            "cancelled": cancelled,
            "token_used": step.state.token_used,
            "cost_estimate_cny": cost,
            "elapsed_seconds": elapsed,
            "degradation_count": len(step.state.degradation_log),
            "depth": step.state.depth,
        }
        seq = self._emit_terminal_frame(
            run_id, RUN_FINISHED, payload,
            status_fields={
                "stop_reason": stop_reason,
                "cancelled": cancelled,
                "has_report": bool(report),
                "run_status": step.state.run_status,
            },
            result=result,
            report=report,
            meta=meta,
        )
        if seq is not None:
            self._persist_terminal(run_id, seq, RUN_FINISHED, payload,
                                   result=result, report=report, meta=meta)
