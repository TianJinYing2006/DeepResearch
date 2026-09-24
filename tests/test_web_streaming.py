"""W9 流式骨架的契约测试（需求 9 §7.1~§7.3）。

**零 LLM、零 API key、零外部服务** —— 用 FakeGraph 注入，只验证管线与契约。

本文件最重要的三条断言（对应 §7.3.3 **取消不得走异常兜底**）：

- 取消后**不再启动新节点**（C2/C5）
- 取消**不产生** `RUN_ERROR`（取消不是故障）
- 取消**不把** `run_status` 写成 `failed`（否则污染 W8「故障可归因率 100%」）
"""
from __future__ import annotations

import json
import queue
import time

import pytest

from research_engine.state import DegradationEntry, ResearchState
from research_engine.streaming import (
    STOP_CANCELLED,
    STOP_COMPLETED,
    STOP_ERROR,
    RunStep,
)
from web.backend.agui import HEARTBEAT_FRAME, sse_frame
from web.backend.runner import RunManager


class FakeGraph:
    """假流式 graph：只吐节点，不碰 LLM。"""

    def __init__(self, steps: int = 5, delay: float = 0.02, degrade_at: int | None = None):
        self.steps = steps
        self.delay = delay
        self.degrade_at = degrade_at

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        state = ResearchState(topic=topic)
        for i in range(self.steps):
            time.sleep(self.delay)
            state.progress.append({"stage": f"node{i}", "msg": f"m{i}"})
            new_deg = ()
            if self.degrade_at is not None and i == self.degrade_at:
                d = DegradationEntry(node="researcher", component="web_search",
                                     reason="provider_error", detail="boom",
                                     fallback_action="empty_list")
                state.add_degradation(node=d.node, component=d.component, reason=d.reason,
                                      detail=d.detail, fallback_action=d.fallback_action)
                new_deg = (d,)
            yield RunStep(index=i, node=f"node{i}", state=state,
                          new_degradations=new_deg, duration_ms=50)
            if should_cancel is not None and should_cancel():
                # 🚨 取消路径：只标 stop_reason，**不写** run_status / degradation_log
                yield RunStep(index=i + 1, node=None, state=state,
                              terminal=True, stop_reason=STOP_CANCELLED)
                return
        yield RunStep(index=self.steps, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


def _drain(run_id: str, mgr: RunManager, timeout: float = 5.0) -> list[dict]:
    """把队列里的帧全部取出并解析为事件 dict。"""
    q = mgr.queue(run_id)
    out: list[dict] = []
    while True:
        try:
            frame = q.get(timeout=timeout)
        except queue.Empty:  # noqa: PERF203
            break
        if frame is None:
            break
        for line in frame.splitlines():
            if line.startswith("data: "):
                out.append(json.loads(line[6:]))
    return out


# --------------------------------------------------------------------------- 正常路径


def test_normal_run_emits_expected_sequence():
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=4))
    ev = _drain(mgr.start("t"), mgr)
    types = [e["type"] for e in ev]

    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert types.count("STEP_FINISHED") == 4
    assert types.count("STATE_DELTA") == 4


def test_run_finished_payload_on_normal_completion():
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=2))
    ev = _drain(mgr.start("t"), mgr)
    fin = ev[-1]
    assert fin["cancelled"] is False
    assert fin["stop_reason"] == STOP_COMPLETED
    assert set(fin["result"]) == {
        "report",
        "citations",
        "validator_stats",
        "depth",
        "visited_sources",
        "reflection_log",
    }


def test_degradation_is_pushed_incrementally():
    """降级必须**实时**推送（硬伤 3 的解药），不能只在终局给。"""
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=4, degrade_at=1))
    ev = _drain(mgr.start("t"), mgr)
    deg = [e for e in ev if e["type"] == "DEGRADATION"]
    assert len(deg) == 1
    assert deg[0]["reason"] == "provider_error"
    # 降级事件必须出现在终局之前
    assert [e["type"] for e in ev].index("DEGRADATION") < len(ev) - 1


# --------------------------------------------------------------------------- 取消契约


def test_cancel_stops_before_all_nodes():
    """C2/C5：取消后不再启动下一节点。"""
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=8, delay=0.1))
    rid = mgr.start("t")
    time.sleep(0.3)
    assert mgr.cancel(rid) is True
    ev = _drain(rid, mgr)

    steps = [e for e in ev if e["type"] == "STEP_FINISHED"]
    assert 0 < len(steps) < 8, "取消后不应继续跑满所有节点"


def test_cancel_during_node_finishes_current_node_then_stops():
    """C3：取消时**当前同步节点允许自然结束**，之后不再启动新节点。"""
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=4, delay=0.4))
    rid = mgr.start("t")
    time.sleep(0.1)  # 此刻 node0 正在执行（sleep 0.4 内）
    t0 = time.perf_counter()
    mgr.cancel(rid)
    ev = _drain(rid, mgr)

    steps = [e for e in ev if e["type"] == "STEP_FINISHED"]
    assert len(steps) == 1, "在飞节点应自然结束，且不得启动 node1"
    assert time.perf_counter() - t0 >= 0.25, "当前节点是自然结束，不是被抢占"
    fin = [e for e in ev if e["type"] == "RUN_FINISHED"][-1]
    assert fin["cancelled"] is True
    assert fin["stop_reason"] == STOP_CANCELLED


def test_cancel_marks_cancelled_and_no_run_error():
    """取消不是故障 ⇒ 不得产生 RUN_ERROR。"""
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=8, delay=0.1))
    rid = mgr.start("t")
    time.sleep(0.3)
    mgr.cancel(rid)
    ev = _drain(rid, mgr)

    types = [e["type"] for e in ev]
    assert "RUN_ERROR" not in types, "取消被误判为故障！"
    fin = [e for e in ev if e["type"] == "RUN_FINISHED"]
    assert fin and fin[-1]["cancelled"] is True
    assert fin[-1]["stop_reason"] == STOP_CANCELLED


def test_cancel_does_not_pollute_run_status():
    """🚨 最关键的断言：取消不得把 run_status 写成 failed。

    否则用户主动取消会被记成系统故障，直接污染 W8 的「故障可归因率 100%」。
    """
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=8, delay=0.1))
    rid = mgr.start("t")
    time.sleep(0.3)
    mgr.cancel(rid)
    ev = _drain(rid, mgr)

    fin = [e for e in ev if e["type"] == "RUN_FINISHED"]
    assert fin[-1]["run_status"] != "failed"
    assert fin[-1]["run_status"] == "success"  # FakeGraph 无降级 ⇒ 保持 success


def test_cancel_unknown_run_id_returns_false():
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=1))
    assert mgr.cancel("nonexistent") is False


def test_cancel_returns_immediately():
    """C1：cancel() 必须立即返回，前端不等后端确认。"""
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=8, delay=0.2))
    rid = mgr.start("t")
    time.sleep(0.25)
    t0 = time.perf_counter()
    mgr.cancel(rid)
    assert time.perf_counter() - t0 < 0.05


# --------------------------------------------------------------------------- 回放与帧格式


def test_replay_returns_frames_after_given_id():
    mgr = RunManager(graph_factory=lambda: FakeGraph(steps=3))
    rid = mgr.start("t")
    _drain(rid, mgr)
    total = len(mgr.replay(rid))
    assert total > 1
    assert len(mgr.replay(rid, after=0)) == total - 1
    assert mgr.replay(rid, after=total - 1) == []


def test_sse_frame_format():
    frame = sse_frame(event_id=7, event_type="RUN_STARTED", payload={"run_id": "abc"})
    assert frame.startswith("id: 7\n")
    assert "event: RUN_STARTED\n" in frame
    assert '"type": "RUN_STARTED"' in frame
    assert frame.endswith("\n\n")
    # 单帧只含一个 data 行
    assert sum(1 for ln in frame.splitlines() if ln.startswith("data: ")) == 1


def test_heartbeat_is_comment_not_event():
    """心跳必须是 SSE 注释行 ⇒ 不触发前端 onmessage。"""
    assert HEARTBEAT_FRAME.startswith(":")
    assert "data:" not in HEARTBEAT_FRAME


# --------------------------------------------------------------------------- RunStep 载体


def test_run_step_terminal_semantics():
    s = ResearchState(topic="t")
    step = RunStep(index=0, node="plan", state=s, duration_ms=12)
    assert step.terminal is False
    assert step.stop_reason == "running"
    assert step.run_status == s.run_status  # 透传，不参与判定


@pytest.mark.parametrize("reason", [STOP_COMPLETED, STOP_CANCELLED, STOP_ERROR])
def test_stop_reasons_are_closed_set(reason):
    """终止原因是封闭集合，防止出现第四态污染 run_status。"""
    from research_engine.streaming import STOP_REASONS
    assert reason in STOP_REASONS
