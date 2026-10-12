"""需求 35 R3：Worker 级 kill→接管→续跑演练（真 PG + 真 langgraph saver；CI infra lane）。

场景 A（resumed）：执行者 kill（租约过期）→ 清扫接管（attempt=2）→ 从 checkpoint 续跑：
- 不重放已完成节点（终局 checkpoint steps == [n1, n2, n3]）；
- 预算不低估：已死尝试账本 100 注入 + 续跑调用 20 ⇒ 终局 token_used == 120。

场景 B（replayed）：无 checkpoint（如 checkpoint 基础设施不可用）+ 已死尝试账本 100：
- 显式降级标注（reason=no_checkpoint，不算恢复成功）；从头重跑；
- 终局成本与账本同口径：state 重算 30 + 已死账本 100 ⇒ 终局 token_used == 130（max 口径）。
"""
from __future__ import annotations

import operator
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, TypedDict

import pytest

from research_engine.state import CHECKPOINT_SCHEMA_VERSION, ResearchState
from research_engine.streaming import STOP_COMPLETED, RunStep

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")


def _build_app(saver):
    from langgraph.graph import END, START, StateGraph

    class _S(TypedDict, total=False):
        steps: Annotated[list, operator.add]
        token_used: int
        checkpoint_schema_version: int

    def n1(state):
        return {"steps": ["n1"], "token_used": int(state.get("token_used", 0)) + 10}

    def n2(state):
        return {"steps": ["n2"], "token_used": int(state.get("token_used", 0)) + 10}

    def n3(state):
        return {"steps": ["n3"], "token_used": int(state.get("token_used", 0)) + 10}

    builder = StateGraph(_S)
    builder.add_node("n1", n1)
    builder.add_node("n2", n2)
    builder.add_node("n3", n3)
    builder.add_edge(START, "n1")
    builder.add_edge("n1", "n2")
    builder.add_edge("n2", "n3")
    builder.add_edge("n3", END)
    return builder.compile(checkpointer=saver)


class _ResumableTinyGraph:
    """Worker 契约的最小真图：3 节点 langgraph + PG saver；每节点如实记一笔 LLM 用量。"""

    def __init__(self, saver, store):
        self.graph = _build_app(saver)  # resolve_execution_mode 经 .graph 读 checkpoint
        self._store = store

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None,
                 *, resume=False):
        cfg = {"configurable": {"thread_id": thread_id}}
        initial = {"steps": [], "token_used": 0,
                   "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION}
        state = ResearchState(topic=topic)
        index = 0
        for chunk in self.graph.stream(None if resume else initial, cfg, stream_mode="values"):
            if resume and index == 0:
                # 恢复首帧 = checkpoint 快照（DeepResearchGraph.iter_run 的 skip_leading 语义）
                state.token_used = int(chunk.get("token_used", 0) or 0)
                index += 1
                continue
            node = chunk["steps"][-1] if chunk.get("steps") else "init"
            if node != "init":
                # 模拟真实 LLM 调用如实入账（usage_ledger 为事实源）
                self._store.record_usage(run_id=thread_id, kind="llm", provider="drill",
                                         model="drill", input_tokens=5, output_tokens=5,
                                         total_tokens=10)
            state.token_used = int(chunk.get("token_used", 0) or 0)
            state.progress.append({"stage": node, "msg": "m"})
            yield RunStep(index=index, node=node, state=state, duration_ms=1)
            index += 1
        state.report = f"# drill {topic}"
        state.report_display = state.report
        yield RunStep(index=index, node=None, state=state, terminal=True,
                      stop_reason=STOP_COMPLETED)


def _new_queued(store, topic: str) -> str:
    run_id = "drill-" + uuid.uuid4().hex[:10]
    store.create_run(run_id, topic, {"instructions": ""}, status="QUEUED",
                     timeout_at=datetime.now(UTC) + timedelta(seconds=3600))
    return run_id


def _wait_terminal(store, run_id: str, timeout: float = 15.0) -> dict:
    deadline = time.monotonic() + timeout
    row = store.get_run(run_id)
    while time.monotonic() < deadline and row["status"] not in (
            "SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST"):
        time.sleep(0.05)
        row = store.get_run(run_id)
    return row


def _events(store, run_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for ev in store.get_events(run_id):
        out.setdefault(ev["event_type"], ev["payload"])
    return out


def _dead_attempt_usage(store, run_id: str) -> None:
    """模拟已死尝试（attempt=1）在 kill 前已发生的 LLM 调用：账本合计 100。"""
    store.record_usage(run_id=run_id, attempt=1, kind="llm", provider="dashscope",
                       model="qwen-plus", input_tokens=40, output_tokens=20,
                       total_tokens=60)
    store.record_usage(run_id=run_id, attempt=1, kind="llm", provider="dashscope",
                       model="qwen-plus", input_tokens=25, output_tokens=15,
                       total_tokens=40)


def test_kill_then_takeover_resumes_from_checkpoint():
    from web.backend.checkpoint import get_pg_checkpointer
    from web.backend.store import RunStore
    from web.backend.worker import Worker

    store = RunStore(DSN)
    saver = get_pg_checkpointer(DSN)
    assert saver is not None
    run_id = _new_queued(store, "kill-接管-续跑")
    cfg = {"configurable": {"thread_id": run_id}}
    try:
        # 第一执行者：认领 → 完成 n1 后「被杀」（租约立即过期；checkpoint 已在节点边界落盘）
        assert store.claim_run(run_id, "dead-worker", 0) is not None
        _dead_attempt_usage(store, run_id)
        stream = _build_app(saver).stream(
            {"steps": [], "token_used": 0,
             "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION},
            cfg, stream_mode="values")
        next(stream)  # 初始帧
        next(stream)  # n1 完成
        stream.close()

        # 清扫接管（attempt=2）→ 恢复执行
        worker = Worker(store, None,
                        graph_factory=lambda: _ResumableTinyGraph(saver, store),
                        worker_id="retry-worker", lease_seconds=60,
                        heartbeat_seconds=5, poll_seconds=0)
        assert worker.sweep_and_requeue() == [
            {"run_id": run_id, "action": "requeued", "attempt": 2}]
        assert worker.run_once(run_id) is True
        row = _wait_terminal(store, run_id)
        assert row["status"] == "SUCCEEDED" and row["attempt"] == 2

        # 续跑语义：不重放已完成节点 + 执行模式落库
        assert _build_app(saver).get_state(cfg).values["steps"] == ["n1", "n2", "n3"]
        events = _events(store, run_id)
        assert events["RUN_STARTED"]["execution_mode"] == "resumed"
        recon = events["RUN_FINISHED"]["budget_reconciliation"]
        assert recon["checkpoint_tokens"] == 10 and recon["reconciled"] is True
        # 预算不低估：账本 100 注入 + n2/n3 各 10 ⇒ 终局 120
        assert row["token_used"] == 120
        assert events["RUN_FINISHED"]["token_used"] == 120
    finally:
        try:
            saver.delete_thread(run_id)
        except Exception:  # noqa: BLE001
            pass
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
        store.close()


def test_no_checkpoint_takeover_replays_with_ledger_reconciliation():
    from web.backend.checkpoint import get_pg_checkpointer
    from web.backend.store import RunStore
    from web.backend.worker import Worker

    store = RunStore(DSN)
    saver = get_pg_checkpointer(DSN)
    assert saver is not None
    run_id = _new_queued(store, "无 checkpoint 降级重跑")
    try:
        # 已死尝试：无 checkpoint（如 checkpoint 基础设施不可用），但 LLM 调用已入账 100
        assert store.claim_run(run_id, "dead-worker", 0) is not None
        _dead_attempt_usage(store, run_id)

        worker = Worker(store, None,
                        graph_factory=lambda: _ResumableTinyGraph(saver, store),
                        worker_id="retry-worker", lease_seconds=60,
                        heartbeat_seconds=5, poll_seconds=0)
        assert worker.sweep_and_requeue() == [
            {"run_id": run_id, "action": "requeued", "attempt": 2}]
        assert worker.run_once(run_id) is True
        row = _wait_terminal(store, run_id)
        assert row["status"] == "SUCCEEDED" and row["attempt"] == 2

        # 显式降级：不算恢复成功；从头重跑（n1 亦重执行）
        events = _events(store, run_id)
        assert events["RUN_STARTED"]["execution_mode"] == "replayed"
        recon = events["RUN_FINISHED"]["budget_reconciliation"]
        assert recon["mode"] == "replayed" and recon["reason"] == "no_checkpoint"
        # 终局成本与账本同口径：已死 100 + 重跑 30（n1/n2/n3）⇒ 130
        assert recon["ledger_tokens"] == 130 and recon["final_tokens"] == 130
        assert row["token_used"] == 130
        assert _build_app(saver).get_state(
            {"configurable": {"thread_id": run_id}}).values["steps"] == ["n1", "n2", "n3"]
    finally:
        try:
            saver.delete_thread(run_id)
        except Exception:  # noqa: BLE001
            pass
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
        store.close()
