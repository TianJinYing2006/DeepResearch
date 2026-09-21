"""研究运行的管理器：线程池驱动 `iter_run()`，产出 SSE 帧（需求 9 §7.4）。

**为什么必须用线程**：`graph.iter_run()` 是**同步生成器**（LangGraph 的 `stream()` 同步），
在 async 协程里直接跑会阻塞整个事件循环 ⇒ 放进工作线程，用 `queue.Queue` 回传帧。

**取消**：`threading.Event` + `should_cancel` 谓词，检查点在节点边界（契约 C2/C3/C5）。
"""
from __future__ import annotations

import queue
import threading
import time
import uuid
from typing import Any, Callable, Dict, List, Optional

from research_engine.graph import DeepResearchGraph, create_graph
from research_engine.streaming import STOP_CANCELLED, STOP_ERROR, RunStep

from .agui import (
    DEGRADATION,
    RUN_ERROR,
    RUN_FINISHED,
    RUN_STARTED,
    STATE_DELTA,
    STEP_FINISHED,
    sse_frame,
)


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

    def __init__(self, graph_factory: Callable[[], DeepResearchGraph] = create_graph):
        self._graph_factory = graph_factory
        self._frames: Dict[str, List[str]] = {}   # run_id → 已发出的帧（重连回放用）
        self._queues: Dict[str, queue.Queue] = {}
        self._cancel: Dict[str, threading.Event] = {}
        self._threads: Dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._condition = threading.Condition(self._lock)
        self._finished: set[str] = set()

    # ------------------------------------------------------------------ 生命周期

    def start(self, topic: str, instructions: str = "", max_total_hops: int | None = None,
              search_provider: str | None = None,
              enable_arxiv: bool | None = None,
              max_subquestions: int | None = None) -> str:
        """启动一次研究，立即返回 `run_id`（不阻塞）。"""
        run_id = uuid.uuid4().hex[:12]
        self._frames[run_id] = []
        self._queues[run_id] = queue.Queue()
        self._cancel[run_id] = threading.Event()
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
        """请求取消。立即返回（契约 C1：前端不等后端确认）。"""
        ev = self._cancel.get(run_id)
        if ev is None:
            return False
        ev.set()
        return True

    def queue(self, run_id: str) -> Optional[queue.Queue]:
        return self._queues.get(run_id)

    def exists(self, run_id: str) -> bool:
        return run_id in self._frames

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

    # ------------------------------------------------------------------ 工作线程

    def _emit(self, run_id: str, event_type: str, payload: Dict[str, Any]) -> None:
        with self._condition:
            seq = len(self._frames[run_id])
            frame = sse_frame(event_id=seq, event_type=event_type, payload=payload)
            self._frames[run_id].append(frame)
            self._condition.notify_all()
        self._queues[run_id].put(frame)

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
            self._emit(run_id, RUN_STARTED, {
                "run_id": run_id,
                "topic": topic,
                "max_total_hops": config.research.max_total_hops,
                "max_subquestions": config.research.max_subquestions,
                "search_provider": config.search.provider,
                "enable_arxiv": config.search.enable_arxiv,
            })

            seen_progress = 0
            for step in graph.iter_run(topic, instructions, thread_id=run_id,
                                       should_cancel=self._cancel[run_id].is_set):
                if step.terminal:
                    self._emit_terminal(run_id, step)
                    break

                self._emit(run_id, STEP_FINISHED, {
                    "node": step.node,
                    "index": step.index,
                    "duration_ms": step.duration_ms,
                    "depth": step.state.depth,
                    "token_used": step.state.token_used,
                })

                # STATE_DELTA：只推 progress 新增项（全量 state 太大）
                added = step.state.progress[seen_progress:]
                seen_progress = len(step.state.progress)
                self._emit(run_id, STATE_DELTA, {
                    "progress_added": added,
                    "findings_count": len(step.state.findings),
                    "visited_sources_count": len(step.state.visited_sources),
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
            self._emit(run_id, RUN_ERROR, {
                "code": "runner_crash",
                "message": f"{type(exc).__name__}: {exc}"[:500],
                "node": None,
            })
        finally:
            with self._condition:
                self._finished.add(run_id)
                self._condition.notify_all()
            self._queues[run_id].put(None)  # sentinel：通知 SSE 端点流已结束

    def _emit_terminal(self, run_id: str, step: RunStep) -> None:
        """终局事件：异常走 RUN_ERROR，正常/取消走 RUN_FINISHED。

        🚨 取消**不写** run_status、不写 degradation_log（需求 9 §7.3.3），
        故此处只是**透传** state.run_status，不参与任何判定。
        """
        if step.stop_reason == STOP_ERROR:
            err = step.state.error or {}
            self._emit(run_id, RUN_ERROR, {
                "code": err.get("code", "unknown"),
                "message": err.get("message", ""),
                "node": err.get("node"),
            })
            return

        report = step.state.report_display or step.state.report
        self._emit(run_id, RUN_FINISHED, {
            # 取消与否由 stop_reason 表达，**不新增 run_status 第四态**
            "cancelled": step.stop_reason == STOP_CANCELLED,
            "stop_reason": step.stop_reason,
            "run_status": step.state.run_status,
            "token_used": step.state.token_used,
            # 成本估算（元）。口径见 _estimate_cost_cny：按最贵 output 单价计的**上界**。
            "cost_estimate_cny": _estimate_cost_cny(step.state.token_used),
            "degradation_count": len(step.state.degradation_log),
            "has_report": bool(report),
            "result": {
                "report": report,
                "citations": [citation.model_dump(mode="json") for citation in step.state.citations],
                "validator_stats": step.state.validator_stats,
                "depth": step.state.depth,
                "visited_sources": list(step.state.visited_sources),
                "reflection_log": list(step.state.reflection_log),
            },
        })
