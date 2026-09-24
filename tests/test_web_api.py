"""W9 后端 HTTP 层测试（零 LLM，用 FakeGraph 注入）。

只验证接口契约：**能启动、能推 SSE、能取消**。真实研究链路不在这里跑（成本高）。
"""
from __future__ import annotations

import json
import time

from fastapi.testclient import TestClient

from research_engine.state import ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED, RunStep
from web.backend import main as api


class TinyGraph:
    """最小假 graph（本文件自用，避免与 test_web_streaming 的夹具耦合）。"""

    def __init__(self, steps: int = 3, delay: float = 0.02):
        self.steps = steps
        self.delay = delay

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        state = ResearchState(topic=topic)
        for i in range(self.steps):
            time.sleep(self.delay)
            state.progress.append({"stage": f"n{i}", "msg": "m"})
            yield RunStep(index=i, node=f"n{i}", state=state, duration_ms=1)
            if should_cancel is not None and should_cancel():
                yield RunStep(index=i + 1, node=None, state=state,
                              terminal=True, stop_reason=STOP_CANCELLED)
                return
        yield RunStep(index=self.steps, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


def _setup(steps=3, delay=0.02):
    api.manager._graph_factory = lambda: TinyGraph(steps=steps, delay=delay)
    # P1-3：默认并发闸是 1（前台模型一次只跑一个）。HTTP 层的用例是**串行**的，
    # 但上一个用例的工作线程可能还没退出 ⇒ 这里放开闸，避免用例之间互相挡。
    # 闸本身的行为由 tests/test_web_guardrails.py 用独立 RunManager 单独覆盖。
    api.manager.max_concurrent_runs = 8
    return TestClient(api.app)


def test_health():
    client = _setup()
    assert client.get("/api/health").json()["ok"] is True


def test_start_returns_run_id():
    client = _setup()
    r = client.post("/api/research", json={"topic": "测试"})
    assert r.status_code == 200
    assert r.json()["run_id"]


def test_start_rejects_empty_topic():
    client = _setup()
    assert client.post("/api/research", json={"topic": "   "}).status_code == 400


def test_stream_emits_sse_with_agui_events():
    client = _setup(steps=3)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]

    with client.stream("GET", f"/api/research/{rid}/stream") as resp:
        assert resp.headers["content-type"].startswith("text/event-stream")
        types = []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                types.append(json.loads(line[6:])["type"])

    assert types[0] == "RUN_STARTED"
    assert types[-1] == "RUN_FINISHED"
    assert types.count("STEP_FINISHED") == 3


def test_stream_resumes_after_last_event_id_without_restarting():
    client = _setup(steps=3)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]

    with client.stream("GET", f"/api/research/{rid}/stream") as resp:
        all_events = [
            json.loads(line[6:])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]

    assert all_events[0]["type"] == "RUN_STARTED"
    assert all_events[-1]["type"] == "RUN_FINISHED"
    assert set(all_events[-1]["result"]) == {
        "report",
        "citations",
        "validator_stats",
        "depth",
        "visited_sources",
        "reflection_log",
    }

    with client.stream(
        "GET",
        f"/api/research/{rid}/stream",
        headers={"Last-Event-ID": "0"},
    ) as resp:
        resumed_events = [
            json.loads(line[6:])
            for line in resp.iter_lines()
            if line.startswith("data: ")
        ]

    assert resumed_events
    assert resumed_events[0]["type"] != "RUN_STARTED"
    assert resumed_events[-1]["type"] == "RUN_FINISHED"


def test_cancel_endpoint_and_stream_marks_cancelled():
    client = _setup(steps=20, delay=0.05)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    time.sleep(0.15)

    assert client.post(f"/api/research/{rid}/cancel").json()["ok"] is True

    with client.stream("GET", f"/api/research/{rid}/stream") as resp:
        types, fins = [], []
        for line in resp.iter_lines():
            if line.startswith("data: "):
                ev = json.loads(line[6:])
                types.append(ev["type"])
                if ev["type"] == "RUN_FINISHED":
                    fins.append(ev)

    assert fins and fins[-1]["cancelled"] is True
    assert "RUN_ERROR" not in types
    assert types.count("STEP_FINISHED") < 20


def test_cancel_unknown_run_returns_404():
    client = _setup()
    assert client.post("/api/research/does-not-exist/cancel").status_code == 404


def test_stream_unknown_run_returns_404():
    client = _setup()
    assert client.get("/api/research/does-not-exist/stream").status_code == 404
