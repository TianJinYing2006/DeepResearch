"""需求 36 Phase 2：真 PG「挂起-进程退出-回答-另一 worker 恢复」演练（CI infra lane）。

场景（§12.4.2）：
1. worker-A 执行到审批门 → interrupt 挂起（等待态 + 卡片事件同事务 + **释放租约**）；
2. 「进程退出」：A 实例丢弃，期间 run 在库中等待（无租约、不占并发槽、不被清扫误杀）；
3. 用户回答（恢复事务）→ `QUEUED`；
4. worker-B（另一实例）认领 → `resumed` + `Command(resume=answer)` 注入 →
   不重放已完成节点（n1）→ 正常收口。

零 LLM：图节点只做确定性算术并如实记一笔账（usage_ledger 为事实源）。
"""
from __future__ import annotations

import operator
import os
import time
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated, TypedDict

import pytest
from langgraph.types import Command, interrupt

from research_engine.state import CHECKPOINT_SCHEMA_VERSION, ResearchState
from research_engine.streaming import STOP_COMPLETED, STOP_SUSPENDED, RunStep

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()

pytestmark = pytest.mark.skipif(not DSN, reason="DR_TEST_DATABASE_URL 未设置（需要真实 PostgreSQL）")


def _build_app(saver):
    from langgraph.graph import END, START, StateGraph

    class _S(TypedDict, total=False):
        steps: Annotated[list, operator.add]
        token_used: int
        checkpoint_schema_version: int
        approval: str

    def n1(state):
        return {"steps": ["n1"], "token_used": int(state.get("token_used", 0)) + 10}

    def gate(state):
        decision = interrupt({
            "kind": "plan_approval",
            "round": 1,
            "content": {"subquestions": [
                {"id": "q1", "question": "Q", "rationale": "R"}]},
        })
        action = str((decision or {}).get("action") or "approve")
        return {"steps": ["gate"], "approval": action,
                "token_used": int(state.get("token_used", 0)) + 10}

    def n2(state):
        return {"steps": ["n2"], "token_used": int(state.get("token_used", 0)) + 10}

    builder = StateGraph(_S)
    builder.add_node("n1", n1)
    builder.add_node("gate", gate)
    builder.add_node("n2", n2)
    builder.add_edge(START, "n1")
    builder.add_edge("n1", "gate")
    builder.add_edge("gate", "n2")
    builder.add_edge("n2", END)
    return builder.compile(checkpointer=saver)


class _SuspendableGraph:
    """Worker 契约的可挂起真图：n1 → interrupt 门 → n2；支持 Command(resume=...)。"""

    def __init__(self, saver, store):
        self.graph = _build_app(saver)  # resolve_execution_mode 经 .graph 读 checkpoint
        self._store = store
        self.resume_values: list = []

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None,
                 *, resume=False, resume_value=None):
        cfg = {"configurable": {"thread_id": thread_id}}
        initial = {"steps": [], "token_used": 0,
                   "checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION}
        if resume_value is not None:
            stream_input = Command(resume=resume_value)
            self.resume_values.append(resume_value)
        else:
            stream_input = None if resume else initial
        state = ResearchState(topic=topic)
        index = 0
        first = True
        for chunk in self.graph.stream(stream_input, cfg, stream_mode="values"):
            if isinstance(chunk, dict) and "__interrupt__" in chunk:
                interrupts = chunk.get("__interrupt__") or ()
                value = interrupts[0].value if interrupts else {}
                yield RunStep(index=index, node=None, state=state, terminal=True,
                              stop_reason=STOP_SUSPENDED, interrupt=value)
                return
            if resume and first:
                # 恢复首帧 = checkpoint 快照（DeepResearchGraph 的 skip_leading 语义）
                state.token_used = int(chunk.get("token_used", 0) or 0)
                first = False
                continue
            first = False
            node = chunk["steps"][-1] if chunk.get("steps") else "init"
            if node == "init":
                continue
            # 模拟真实 LLM 调用如实入账（usage_ledger 为事实源）
            self._store.record_usage(run_id=thread_id, kind="llm", provider="drill",
                                     model="drill", input_tokens=5, output_tokens=5,
                                     total_tokens=10)
            state.token_used = int(chunk.get("token_used", 0) or 0)
            state.progress.append({"stage": node, "msg": "m"})
            yield RunStep(index=index, node=node, state=state, duration_ms=1)
            index += 1
        state.report = f"# hitl drill {topic}"
        state.report_display = state.report
        yield RunStep(index=index, node=None, state=state, terminal=True,
                      stop_reason=STOP_COMPLETED)


def _new_queued(store, topic: str) -> str:
    run_id = "hitl-drill-" + uuid.uuid4().hex[:8]
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


def _last_event(store, run_id: str, event_type: str):
    rows = [e for e in store.get_events(run_id) if e["event_type"] == event_type]
    return rows[-1]["payload"] if rows else None


def test_suspend_exit_answer_resume_drill():
    from web.backend.checkpoint import get_pg_checkpointer
    from web.backend.store import RunStore
    from web.backend.worker import Worker

    store = RunStore(DSN)
    saver = get_pg_checkpointer(DSN)
    assert saver is not None
    run_id = _new_queued(store, "HITL 挂起-退出-回答-恢复演练")
    try:
        # 1) worker-A 执行到审批门 → 挂起
        graph_a = _SuspendableGraph(saver, store)
        worker_a = Worker(store, None, graph_factory=lambda: graph_a, worker_id="hitl-a",
                          lease_seconds=60, heartbeat_seconds=5, poll_seconds=0)
        assert worker_a.run_once(run_id) is True

        row = store.get_run(run_id)
        assert row["status"] == "AWAITING_PLAN_APPROVAL"
        assert row["worker_id"] is None and row["lease_expires_at"] is None
        interaction_id = row["interaction_id"]
        assert interaction_id and row["awaiting_expires_at"] is not None
        assert row["interaction_payload"]["kind"] == "plan_approval"
        card = _last_event(store, run_id, "PLAN_PROPOSED")
        assert card["interaction_id"] == interaction_id
        assert _last_event(store, run_id, "RUN_FINISHED") is None  # 挂起不是终局

        # 等待态：不被租约清扫误杀（等待的是人，不是 worker）
        swept = store.sweep_stale_runs()
        assert all(item["run_id"] != run_id for item in swept)
        assert store.get_run(run_id)["status"] == "AWAITING_PLAN_APPROVAL"

        # 2) 「进程退出」：丢弃 worker_a / graph_a —— 无需任何清理动作（租约已释放）

        # 3) 用户回答（恢复事务）→ QUEUED
        answer = {"kind": "plan_approval", "action": "approve", "text": "", "auto": False}
        result = store.answer_interaction(
            run_id, interaction_id=interaction_id, answer=answer,
            event_type="PLAN_DECISION",
            event_payload={"interaction_id": interaction_id, "action": "approve"})
        assert result["outcome"] == "accepted"
        assert store.get_run(run_id)["status"] == "QUEUED"

        # 4) worker-B（另一实例）认领并恢复
        graph_b = _SuspendableGraph(saver, store)
        worker_b = Worker(store, None, graph_factory=lambda: graph_b, worker_id="hitl-b",
                          lease_seconds=60, heartbeat_seconds=5, poll_seconds=0)
        assert worker_b.run_once(run_id) is True
        row = _wait_terminal(store, run_id)
        assert row["status"] == "SUCCEEDED"

        # 续跑语义：回答值被注入；n1 不重放（gate 只执行一次）
        assert graph_b.resume_values == [answer]
        values = _build_app(saver).get_state(
            {"configurable": {"thread_id": run_id}}).values
        assert values["steps"] == ["n1", "gate", "n2"]
        assert values["approval"] == "approve"

        # 执行模式 + 账本对账：n1(10) + gate(10) + n2(10) = 30
        assert _last_event(store, run_id, "RUN_STARTED")["execution_mode"] == "resumed"
        finished = _last_event(store, run_id, "RUN_FINISHED")
        assert finished["execution_mode"] == "resumed"
        assert row["token_used"] == 30
        assert _last_event(store, run_id, "PLAN_DECISION")["action"] == "approve"
    finally:
        try:
            saver.delete_thread(run_id)
        except Exception:  # noqa: BLE001
            pass
        with store._connect() as conn, conn.cursor() as cur:
            cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))
        store.close()
