"""需求 36 Phase 2：Worker 挂起/恢复路径（离线；FakeStore + 可挂起假图）。

覆盖：挂起释放租约（不写终局）/ 回答后另一 worker 携带 resume_value 续跑 /
非法 interrupt kind 显式失败。
"""
from __future__ import annotations

from fakes import FakeQueue, FakeStore, HitlTinyGraph

from research_engine.state import ResearchState
from research_engine.streaming import STOP_SUSPENDED, RunStep
from web.backend.store import RunOwnership
from web.backend.worker import Worker


def _worker(store, graph, worker_id):
    return Worker(store, FakeQueue(), graph_factory=lambda: graph, worker_id=worker_id,
                  lease_seconds=60, heartbeat_seconds=5, poll_seconds=0)


def test_worker_suspends_and_releases_lease():
    store = FakeStore()
    graph = HitlTinyGraph()
    run_id = "hitlw-0001"
    store.create_run(run_id, "t", {}, status="QUEUED")
    assert _worker(store, graph, "w1").run_once(run_id) is True

    row = store.get_run(run_id)
    assert row["status"] == "AWAITING_CLARIFICATION"
    assert row["worker_id"] is None and row["lease_expires_at"] is None
    assert row["interaction_id"]
    assert row["awaiting_expires_at"] is not None
    assert row["interaction_payload"]["kind"] == "clarification"
    events = [e["event_type"] for e in store.get_events(run_id)]
    assert events[-1] == "CLARIFY_REQUESTED"
    assert "RUN_FINISHED" not in events  # 挂起不是终局
    assert store.count_awaiting() == 1


def test_worker_resumes_with_answer_and_completes():
    store = FakeStore()
    graph = HitlTinyGraph()
    run_id = "hitlw-0002"
    store.create_run(run_id, "t", {}, status="QUEUED")
    assert _worker(store, graph, "w1").run_once(run_id) is True

    interaction_id = store.get_run(run_id)["interaction_id"]
    answer = {"kind": "clarification", "action": "reply", "text": "范围=近三个月",
              "auto": False}
    result = store.answer_interaction(
        run_id, interaction_id=interaction_id, answer=answer,
        event_type="CLARIFY_ANSWERED",
        event_payload={"interaction_id": interaction_id, "action": "reply"})
    assert result["outcome"] == "accepted"

    # 另一 worker 实例（进程边界语义）续跑：必须携带回答值（resume_value）
    assert _worker(store, graph, "w2").run_once(run_id) is True
    row = store.get_run(run_id)
    assert row["status"] == "SUCCEEDED"
    assert graph.resume_values == [answer]
    events = [e["event_type"] for e in store.get_events(run_id)]
    assert "CLARIFY_ANSWERED" in events and "RUN_FINISHED" in events


def test_suspend_invalid_kind_fails_explicitly():
    store = FakeStore()
    run_id = "hitlw-0003"
    store.create_run(run_id, "t", {}, status="QUEUED")
    assert store.claim_run(run_id, "w1", 60) is not None
    worker = _worker(store, HitlTinyGraph(), "w1")
    state = ResearchState(topic="t")
    step = RunStep(index=0, node=None, state=state, terminal=True,
                   stop_reason=STOP_SUSPENDED, interrupt={"kind": "bogus"})
    worker._suspend(run_id, store.get_run(run_id), step, RunOwnership("w1", 1))
    row = store.get_run(run_id)
    assert row["status"] == "FAILED"
    assert row["stop_reason"] == "error"


def test_worker_expire_awaiting_requeues_clarified_auto():
    store = FakeStore()
    graph = HitlTinyGraph()
    queue = FakeQueue()
    worker = Worker(store, queue, graph_factory=lambda: graph, worker_id="w1",
                    lease_seconds=60, heartbeat_seconds=5, poll_seconds=0)
    run_id = "hitlw-0004"
    store.create_run(run_id, "t", {}, status="QUEUED")
    assert worker.run_once(run_id) is True

    # 拨快到期时间 → 到期收口：澄清空回答继续（QUEUED + 留痕 + 唤醒信号）
    from datetime import UTC, datetime, timedelta

    store.get_run(run_id)["awaiting_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    results = worker.expire_awaiting()
    assert results == [{"run_id": run_id, "action": "clarified_auto"}]
    row = store.get_run(run_id)
    assert row["status"] == "QUEUED" and row["interaction_answer"]["auto"] is True
    assert queue.items == [run_id]
    assert store.get_events(run_id)[-1]["event_type"] == "CLARIFY_EXPIRED"
