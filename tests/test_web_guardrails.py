"""P1 后端护栏测试：超时闸 / 并发限制 / 状态查询 / 结构化错误 / 报告导出。

**为什么单独成文件**：这五项是「运行护栏」，不是研究链路本身。放在 W9 的
`test_web_api.py` / `test_web_streaming.py` 里会和事件契约、取消契约的用例互相干扰
（尤其是并发闸会让用例之间互相挡）。本文件一律用**独立 RunManager 实例**，
不碰 `web.backend.main` 的单例。

**零 LLM、零 API key、零外部服务** —— 全部用假 graph 注入。
"""
from __future__ import annotations

import json
import queue
import threading
import time

import pytest
from fastapi.testclient import TestClient

from research_engine.state import Citation, ResearchState
from research_engine.streaming import (
    STOP_CANCELLED,
    STOP_COMPLETED,
    STOP_ERROR,
    STOP_TIMEOUT,
    RunStep,
)
from web.backend import main as api
from web.backend.errors import ERROR_SPECS, ApiError, error_payload
from web.backend.runner import (
    DEFAULT_MAX_CONCURRENT_RUNS,
    DEFAULT_RUN_TIMEOUT_SECONDS,
    RunManager,
)


class ReportGraph:
    """会产出报告与引用的假 graph，尊重 `should_cancel`（正常节点边界行为）。"""

    def __init__(self, steps: int = 2, delay: float = 0.01, report: str = "# 报告\n\n正文 [1]"):
        self.steps = steps
        self.delay = delay
        self.report = report

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
        state.report = self.report
        state.report_display = self.report
        state.citations = [
            Citation(claim="演示论断", source="https://example.com/a", source_type="web",
                     verified=True, existence=True, supported=True, confidence=0.9, note="ok")
        ]
        state.visited_sources = ["https://example.com/a"]
        state.validator_stats = {"citation_total": 1, "citation_verified": 1}
        yield RunStep(index=self.steps, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


class StuckGraph:
    """节点内部挂死：完全不理会 `should_cancel`（模拟一次 HTTP 调用卡住）。"""

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        state = ResearchState(topic=topic)
        state.progress.append({"stage": "n0", "msg": "m"})
        yield RunStep(index=0, node="n0", state=state, duration_ms=1)
        for _ in range(60):  # 最多 3s，够测试用，也不会拖住退出
            time.sleep(0.05)
        yield RunStep(index=1, node=None, state=state, terminal=True,
                      stop_reason=STOP_COMPLETED)


class LateCompletionGraph:
    """忽略 `should_cancel`：节点跑过协作式时限后仍**正常完成**（完成与超时同时发生）。"""

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        state = ResearchState(topic=topic)
        state.progress.append({"stage": "n0", "msg": "m"})
        yield RunStep(index=0, node="n0", state=state, duration_ms=1)
        time.sleep(0.3)
        state.report = "# 报告"
        state.report_display = "# 报告"
        yield RunStep(index=1, node=None, state=state, terminal=True,
                      stop_reason=STOP_COMPLETED)


class ErrorGraph:
    """终局走 STOP_ERROR（图内结构化异常路径），state.error 已按 W8 口径写入。"""

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        state = ResearchState(topic=topic)
        state.progress.append({"stage": "n0", "msg": "m"})
        yield RunStep(index=0, node="n0", state=state, duration_ms=1)
        state.error = {"code": "recursion_limit", "message": "图递归超限", "node": "research"}
        yield RunStep(index=1, node=None, state=state, terminal=True,
                      stop_reason=STOP_ERROR)


def _drain(run_id: str, mgr: RunManager, timeout: float = 5.0) -> list[dict]:
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


def _thread_alive(run_id: str) -> bool:
    return any(t.name == f"research-{run_id}" for t in threading.enumerate())


def _wait_thread_gone(run_id: str, timeout: float = 2.0) -> bool:
    """等 `_worker` 线程真正退出（sentinel 投递后还有几行收尾代码）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not _thread_alive(run_id):
            return True
        time.sleep(0.01)
    return False


# --------------------------------------------------------------------------- P1-2 超时闸


def test_timeout_stops_run_and_is_reported_as_timeout():
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=100, delay=0.05),
                     run_timeout_seconds=0.4)
    ev = _drain(mgr.start("t"), mgr)

    fin = ev[-1]
    assert fin["type"] == "RUN_FINISHED"
    assert fin["stop_reason"] == STOP_TIMEOUT
    # 超时是**系统闸**触发的停止，不是用户点取消 ⇒ 不得标成 cancelled
    assert fin["cancelled"] is False
    assert [e["type"] for e in ev].count("STEP_FINISHED") < 100


def test_timeout_is_not_a_failure_and_emits_no_run_error():
    """🚨 归因纪律：超时与取消同口径，不得进 RUN_ERROR、不得把 run_status 写成 failed。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=100, delay=0.05),
                     run_timeout_seconds=0.4)
    ev = _drain(mgr.start("t"), mgr)

    assert "RUN_ERROR" not in [e["type"] for e in ev]
    assert ev[-1]["run_status"] != "failed"


def test_timeout_default_is_generated_from_env_free_default():
    """默认值必须留足真实运行的余量（实测 48~51 分钟 ⇒ 默认 3600s）。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph())
    assert mgr.run_timeout_seconds == DEFAULT_RUN_TIMEOUT_SECONDS == 3600
    assert mgr.max_concurrent_runs == DEFAULT_MAX_CONCURRENT_RUNS == 1


def test_run_started_carries_timeout_so_frontend_can_countdown():
    mgr = RunManager(graph_factory=lambda: ReportGraph(), run_timeout_seconds=123)
    ev = _drain(mgr.start("t"), mgr)
    assert ev[0]["timeout_seconds"] == 123


def test_run_finished_carries_elapsed_seconds():
    """#14 运行级统计：终局事件带后端记录的耗时（刷新回放后同样权威）。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=3, delay=0.05))
    ev = _drain(mgr.start("t"), mgr)
    fin = ev[-1]
    assert fin["type"] == "RUN_FINISHED"
    assert 0.1 <= fin["elapsed_seconds"] < 5


def test_completion_past_deadline_is_not_rewritten_as_timeout():
    """完成与超时同时发生（完成先落终局）：不得改判 timeout、不得再补强制收口。"""
    mgr = RunManager(graph_factory=LateCompletionGraph, run_timeout_seconds=0.05,
                     forced_stop_grace_seconds=0.05, max_concurrent_runs=1)
    rid = mgr.start("t")
    ev = _drain(rid, mgr)  # 节点 0.3s 越过硬截止 0.1s，但完成先发生

    fin = [e for e in ev if e["type"] == "RUN_FINISHED"]
    assert fin and fin[-1]["stop_reason"] == STOP_COMPLETED
    assert "RUN_ERROR" not in [e["type"] for e in ev]
    assert mgr.force_stop_if_overdue(rid) is None, "已完成 ⇒ 收口必须放弃"
    assert mgr.active_runs == 0


def test_forced_stop_when_node_ignores_cooperative_checkpoint():
    """节点挂死 ⇒ 协作式闸失效 ⇒ 硬截止后由传输层补 RUN_ERROR(stop_forced) 收口。"""
    mgr = RunManager(graph_factory=StuckGraph, run_timeout_seconds=0.2,
                     forced_stop_grace_seconds=0.2)
    rid = mgr.start("t")
    # 硬截止前：不得提前收口
    time.sleep(0.15)
    assert mgr.force_stop_if_overdue(rid) is None

    time.sleep(0.25)  # 越过 0.4s 硬截止
    frame = mgr.force_stop_if_overdue(rid)
    assert frame is not None
    assert "RUN_ERROR" in frame
    assert "stop_forced" in frame
    assert mgr.is_finished(rid) is True
    # 幂等：重复调用不得再补一帧
    assert mgr.force_stop_if_overdue(rid) is None


def test_forced_stop_ignores_late_worker_frames():
    """后台线程收尾时不得在 stop_forced 后追加二次终局或报告。"""
    mgr = RunManager(graph_factory=StuckGraph, run_timeout_seconds=0.05,
                     forced_stop_grace_seconds=0.05)
    rid = mgr.start("t")
    time.sleep(0.15)
    assert mgr.force_stop_if_overdue(rid) is not None
    # StuckGraph 仍会继续运行；等它收尾后检查回放序列仍以强制错误收口。
    time.sleep(3.0)
    frames = mgr.replay(rid)
    assert sum('"type": "RUN_ERROR"' in frame for frame in frames) == 1
    assert '"code": "stop_forced"' in frames[-1]
    assert '"type": "RUN_FINISHED"' not in ''.join(frames)
    # 后台线程 finally 会二次 discard 并发位：集合语义下必须是幂等的（不得出现负数/复活）
    assert mgr.active_runs == 0
    assert _wait_thread_gone(rid, timeout=3.0), "后台线程收尾后必须退出，不得残留"


def test_stuck_run_still_releases_concurrency_slot_after_forced_stop():
    mgr = RunManager(graph_factory=StuckGraph, run_timeout_seconds=0.2,
                     forced_stop_grace_seconds=0.2, max_concurrent_runs=1)
    rid = mgr.start("t")
    time.sleep(0.5)
    mgr.force_stop_if_overdue(rid)
    assert mgr.active_runs == 0, "强制收口后必须释放并发位，否则进程永久不可用"
    assert mgr.start("t2"), "释放后的并发位必须可复用"


def test_forced_stop_before_late_terminal_write_cannot_expose_report(monkeypatch):
    """终局写入与强制收口必须互斥：越过检查点后收口，不得留下迟到报告或覆盖状态。"""
    import web.backend.runner as runner_mod

    gate, release = threading.Event(), threading.Event()
    orig_cost = runner_mod._estimate_cost_cny

    def blocked_cost(tokens):
        gate.set()
        assert release.wait(5), "测试自身等待超时"
        return orig_cost(tokens)

    monkeypatch.setattr(runner_mod, "_estimate_cost_cny", blocked_cost)
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=0, delay=0.01),
                     run_timeout_seconds=0.05, forced_stop_grace_seconds=0.05,
                     max_concurrent_runs=1)
    rid = mgr.start("t")
    assert gate.wait(5), "工作线程未到达终局写入前窗口"
    time.sleep(0.2)  # 越过硬截止
    assert mgr.force_stop_if_overdue(rid) is not None
    release.set()
    time.sleep(0.5)  # 等后台线程收尾

    assert mgr.has_result(rid) is False
    assert mgr.export_payload(rid) is None
    snap = mgr.snapshot(rid)
    assert snap["status"] == "finished"
    assert snap["stop_reason"] is None


def test_force_stop_cannot_append_second_terminal_after_run_finished(monkeypatch):
    """终局帧与 `_finished` 置位期间强制收口不得再补一帧。

    用 `_append_locked` 钩子把工作线程钉在「已写终局、尚未置位 finished」的临界区，
    再让收口线程入队（它会阻塞在同一把 condition 锁上）⇒ 收口必须观察到 finished 并放弃。
    """
    gate, release = threading.Event(), threading.Event()
    orig_append = RunManager._append_locked

    def blocked_append(self, run_id, event_type, payload):
        frame = orig_append(self, run_id, event_type, payload)
        if event_type == "RUN_FINISHED":
            gate.set()
            assert release.wait(5), "测试自身等待超时"
        return frame

    monkeypatch.setattr(RunManager, "_append_locked", blocked_append)
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=0, delay=0.01),
                     run_timeout_seconds=0.05, forced_stop_grace_seconds=0.05,
                     max_concurrent_runs=1)
    rid = mgr.start("t")
    assert gate.wait(5)
    time.sleep(0.2)  # 越过硬截止；此时 finished 尚未置位

    outcome: dict = {}
    stopper = threading.Thread(target=lambda: outcome.setdefault(
        "forced", mgr.force_stop_if_overdue(rid)))
    stopper.start()
    time.sleep(0.05)  # 让收口线程阻塞在 condition 锁上
    release.set()
    stopper.join(5)

    assert outcome["forced"] is None
    frames = mgr.replay(rid)
    assert sum('"type": "RUN_FINISHED"' in frame for frame in frames) == 1
    assert '"type": "RUN_ERROR"' not in ''.join(frames)


# --------------------------------------------------------------------------- P1-3 并发限制


def test_second_run_is_rejected_when_limit_is_one():
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=20, delay=0.05),
                     max_concurrent_runs=1)
    rid = mgr.start("t")
    with pytest.raises(ApiError) as exc:
        mgr.start("t2")
    assert exc.value.code == "concurrency_limit"
    assert exc.value.to_http().status_code == 429
    mgr.cancel(rid)
    _drain(rid, mgr)


def test_slot_is_released_after_natural_finish():
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=2, delay=0.01),
                     max_concurrent_runs=1)
    _drain(mgr.start("t"), mgr)
    assert mgr.active_runs == 0
    assert mgr.start("t2")  # 不再被拒


def test_slot_is_released_after_cancel():
    """取消路径也必须释放并发位（否则「取消后无法再发起」）。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=20, delay=0.05),
                     max_concurrent_runs=1)
    rid = mgr.start("t")
    time.sleep(0.1)
    mgr.cancel(rid)
    _drain(rid, mgr)
    assert mgr.active_runs == 0
    assert mgr.start("t2")


def test_slot_is_released_after_runner_crash():
    """工作线程崩溃也必须释放并发位（异常不得把槽带走）。"""
    class Boom:
        def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
            raise RuntimeError("boom")

    mgr = RunManager(graph_factory=Boom, max_concurrent_runs=1)
    rid = mgr.start("t")
    _drain(rid, mgr)
    assert mgr.active_runs == 0
    assert mgr.start("t2")


# --------------------------------------------------------------------------- P1 资源：线程释放


def test_worker_thread_is_released_after_all_terminal_paths():
    """完成 / 取消 / 崩溃三条终局路径都不得留下 `research-*` 工作线程。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=2, delay=0.01))
    rid = mgr.start("t")
    _drain(rid, mgr)
    assert _wait_thread_gone(rid), "正常完成必须释放线程"

    mgr2 = RunManager(graph_factory=lambda: ReportGraph(steps=20, delay=0.05))
    rid2 = mgr2.start("t")
    time.sleep(0.1)
    mgr2.cancel(rid2)
    _drain(rid2, mgr2)
    assert _wait_thread_gone(rid2), "取消路径必须释放线程"

    class Boom:
        def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
            raise RuntimeError("boom")

    mgr3 = RunManager(graph_factory=Boom)
    rid3 = mgr3.start("t")
    _drain(rid3, mgr3)
    assert _wait_thread_gone(rid3), "崩溃路径必须释放线程"


def test_run_completes_without_any_consumer():
    """无人消费事件（≈客户端断开）时运行照常完成，帧留内存可回放。

    ⚠️ 不用 TestClient「提前 break」模拟中途断连：Starlette TestClient 会把整段
    响应缓冲完再交给调用方，断不开流中途；真正的中途断连由浏览器 E2E 覆盖。
    """
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=3, delay=0.01))
    rid = mgr.start("t")  # 不 drain、不读队列
    deadline = time.monotonic() + 3
    while not mgr.is_finished(rid) and time.monotonic() < deadline:
        time.sleep(0.02)

    assert mgr.is_finished(rid)
    assert mgr.snapshot(rid)["status"] == "finished"
    frames = mgr.replay(rid)
    assert sum('"type": "RUN_FINISHED"' in frame for frame in frames) == 1


def test_wait_for_frame_timeout_is_non_destructive():
    """SSE 等待超时（心跳路径）不得影响运行：返回 (None, False) 且状态仍是 running。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=20, delay=0.05))
    rid = mgr.start("t")
    frame, finished = mgr.wait_for_frame(rid, after=999, timeout=0.1)

    assert frame is None
    assert finished is False
    assert mgr.snapshot(rid)["status"] == "running"
    mgr.cancel(rid)
    _drain(rid, mgr)


def test_concurrency_limit_is_visible_over_http(monkeypatch):
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: ReportGraph(steps=20, delay=0.05),
                                   max_concurrent_runs=1))
    client = TestClient(api.app)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    r = client.post("/api/research", json={"topic": "t2"})
    assert r.status_code == 429
    assert r.json()["detail"]["code"] == "concurrency_limit"
    client.post(f"/api/research/{rid}/cancel")
    _drain(rid, api.manager)


# --------------------------------------------------------------------------- P1-4 状态查询


def test_snapshot_reports_running_then_finished():
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=3, delay=0.05),
                     run_timeout_seconds=600)
    rid = mgr.start("t")
    time.sleep(0.08)
    snap = mgr.snapshot(rid)
    assert snap["status"] == "running"
    assert snap["timeout_seconds"] == 600
    assert 0 <= snap["elapsed_seconds"] < 600
    assert 0 < snap["remaining_seconds"] <= 600
    assert snap["event_count"] >= 1
    assert snap["last_event_type"] in {"RUN_STARTED", "STEP_FINISHED", "STATE_DELTA"}

    _drain(rid, mgr)
    snap = mgr.snapshot(rid)
    assert snap["status"] == "finished"
    assert snap["stop_reason"] == STOP_COMPLETED
    assert snap["has_report"] is True
    assert snap["run_status"] == "success"


def test_snapshot_returns_none_for_unknown_run():
    mgr = RunManager(graph_factory=lambda: ReportGraph())
    assert mgr.snapshot("nope") is None
    assert mgr.exists("nope") is False


def test_status_endpoint_returns_snapshot(monkeypatch):
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=2, delay=0.01))
    monkeypatch.setattr(api, "manager", mgr)
    client = TestClient(api.app)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    body = client.get(f"/api/research/{rid}").json()
    assert body["run_id"] == rid
    assert body["topic"] == "t"
    assert "elapsed_seconds" in body
    _drain(rid, mgr)


def test_status_endpoint_404_is_structured(monkeypatch):
    monkeypatch.setattr(api, "manager", RunManager(graph_factory=lambda: ReportGraph()))
    client = TestClient(api.app)
    r = client.get("/api/research/does-not-exist")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "run_id_not_found"


# --------------------------------------------------------------------------- P1-5 结构化错误


def test_error_payload_has_stable_keys_and_hint():
    payload = error_payload("missing_search_key", "bocha 未配置 key", node=None)
    assert set(payload) >= {"code", "message", "component", "node", "detail",
                            "retryable", "hint"}
    assert payload["retryable"] is False
    assert payload["hint"] == ERROR_SPECS["missing_search_key"].hint
    assert payload["component"] == "search"


def test_unknown_code_falls_back_to_unknown_spec():
    payload = error_payload("totally_new_code", "x")
    assert payload["code"] == "totally_new_code"
    assert payload["hint"] == ERROR_SPECS["unknown"].hint
    assert payload["component"] == "web"


def test_http_errors_are_structured_not_bare_strings(monkeypatch):
    monkeypatch.setattr(api, "manager", RunManager(graph_factory=lambda: ReportGraph()))
    client = TestClient(api.app)
    r = client.post("/api/research", json={"topic": "   "})
    assert r.status_code == 400
    detail = r.json()["detail"]
    assert isinstance(detail, dict)
    assert detail["code"] == "empty_topic"
    assert detail["retryable"] is False
    assert detail["hint"]


def test_runner_crash_payload_is_structured():
    class Boom:
        def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
            raise RuntimeError("boom")

    mgr = RunManager(graph_factory=Boom)
    ev = _drain(mgr.start("t"), mgr)
    err = ev[-1]
    assert err["type"] == "RUN_ERROR"
    assert err["code"] == "runner_crash"
    assert err["component"] == "runner"
    assert "boom" in err["message"]


def test_graph_error_terminal_is_structured_and_releases_slot():
    """图内 STOP_ERROR（结构化异常路径）：错误帧带 code/归因/节点，且不产出报告、释放并发位。"""
    mgr = RunManager(graph_factory=ErrorGraph, max_concurrent_runs=1)
    rid = mgr.start("t")
    ev = _drain(rid, mgr)

    err = ev[-1]
    assert err["type"] == "RUN_ERROR"
    assert err["code"] == "recursion_limit"
    assert err["component"] == "graph"
    assert err["node"] == "research"
    assert mgr.has_result(rid) is False
    snap = mgr.snapshot(rid)
    assert snap["status"] == "finished"
    assert snap["stop_reason"] == STOP_ERROR
    assert mgr.active_runs == 0


# --------------------------------------------------------------------------- P1-6 报告导出


def test_export_markdown_carries_audit_metadata_and_citations():
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=2, delay=0.01))
    rid = mgr.start("主题甲")
    _drain(rid, mgr)

    md = mgr.export_markdown(rid)
    assert md is not None
    assert rid in md
    assert "主题甲" in md
    assert "# 报告" in md
    assert "## 引用清单" in md
    assert "https://example.com/a" in md
    assert "run_status" in md
    assert "耗时" in md  # #14：导出元数据带后端记录的耗时


def test_export_payload_shape():
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=2, delay=0.01))
    rid = mgr.start("t")
    _drain(rid, mgr)
    payload = mgr.export_payload(rid)
    assert payload["run_id"] == rid
    assert payload["stop_reason"] == STOP_COMPLETED
    assert payload["cancelled"] is False
    assert set(payload["result"]) == {"report", "citations", "validator_stats",
                                      "depth", "visited_sources", "reflection_log"}


def test_report_endpoint_markdown_and_json(monkeypatch):
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=2, delay=0.01))
    monkeypatch.setattr(api, "manager", mgr)
    client = TestClient(api.app)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    _drain(rid, mgr)

    md_resp = client.get(f"/api/research/{rid}/report")
    assert md_resp.status_code == 200
    assert md_resp.headers["content-type"].startswith("text/markdown")
    assert "attachment" in md_resp.headers["content-disposition"]
    assert "# 报告" in md_resp.text

    json_resp = client.get(f"/api/research/{rid}/report?format=json")
    assert json_resp.status_code == 200
    assert json_resp.json()["run_id"] == rid
    assert json_resp.json()["result"]["report"]


def test_report_endpoint_409_while_running(monkeypatch):
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: ReportGraph(steps=20, delay=0.05)))
    client = TestClient(api.app)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    r = client.get(f"/api/research/{rid}/report")
    assert r.status_code == 409
    assert r.json()["detail"]["code"] == "report_not_ready"
    client.post(f"/api/research/{rid}/cancel")
    _drain(rid, api.manager)


def test_report_endpoint_404_when_no_report(monkeypatch):
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=1, delay=0.01, report=""))
    monkeypatch.setattr(api, "manager", mgr)
    client = TestClient(api.app)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    _drain(rid, mgr)

    r = client.get(f"/api/research/{rid}/report")
    assert r.status_code == 404
    assert r.json()["detail"]["code"] == "report_unavailable"


def test_export_invalid_format_is_structured_422(monkeypatch):
    """#11：FastAPI 参数校验错误也必须走结构化契约（而不是默认的 422 裸形状）。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=1, delay=0.01))
    monkeypatch.setattr(api, "manager", mgr)
    client = TestClient(api.app)
    rid = client.post("/api/research", json={"topic": "t"}).json()["run_id"]
    _drain(rid, mgr)

    r = client.get(f"/api/research/{rid}/report?format=xml")
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["code"] == "invalid_request"
    assert detail["retryable"] is False
    assert "format" in detail["detail"]


def test_export_never_touches_disk(monkeypatch, tmp_path):
    """D-19：导出只走内存，不得产生任何落盘产物。"""
    mgr = RunManager(graph_factory=lambda: ReportGraph(steps=2, delay=0.01))
    monkeypatch.setattr(api, "manager", mgr)
    monkeypatch.chdir(tmp_path)
    rid = mgr.start("t")
    _drain(rid, mgr)
    mgr.export_markdown(rid)
    assert list(tmp_path.iterdir()) == []
