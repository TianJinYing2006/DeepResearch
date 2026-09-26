"""P2-C 持久化接线测试（FakeStore，零 PostgreSQL）。

覆盖两件事：
1. **写透**：RunManager 在配置仓储时把创建 / 事件 / 终局 / 产物写入仓储，
   失败只留痕（`persistence_error`）而不打断研究；
2. **读侧回落**：内存未命中时，`main.py` 的查询 / 列表 / 导出 / SSE 回放 / 取消
   走任务库（进程重启后的场景）。

真实 PostgreSQL 的仓储契约与端到端见 `tests/test_run_store.py`（需 DR_TEST_DATABASE_URL）。
"""
from __future__ import annotations

import json
import time
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from research_engine.state import ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED, RunStep
from web.backend import main as api
from web.backend.runner import RunManager

SEEDED_RUN = "seededrun12"


class TinyGraph:
    """最小假 graph：可配节点数 / 延迟 / 报告正文（本文件与 test_web_api 各自独立）。"""

    def __init__(self, steps: int = 3, delay: float = 0.01, report: str = ""):
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
        yield RunStep(index=self.steps, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


class FakeStore:
    """内存版 RunStore：语义与 web/backend/store.py 对齐，供接线测试使用。"""

    def __init__(self) -> None:
        self.runs: dict[str, dict] = {}
        self.events: dict[str, list[dict]] = {}
        self.artifacts: dict[str, dict[str, str]] = {}
        self.fail_events = False

    def ping(self) -> None:
        return None

    def create_run(self, run_id, topic, request=None, *, user_id=None, tenant_id=None,
                   idempotency_key=None, status="CREATED", timeout_at=None,
                   budget_limit_cny=None):
        if idempotency_key is not None:
            for row in self.runs.values():
                if row["user_id"] == user_id and row["idempotency_key"] == idempotency_key:
                    return row, False
        now = datetime.now(UTC)
        row = {
            "run_id": run_id, "user_id": user_id, "tenant_id": tenant_id, "status": status,
            "research_status": None, "stop_reason": None, "current_node": None,
            "topic": topic, "request": request or {}, "token_used": 0,
            "cost_estimate_cny": 0.0, "budget_limit_cny": budget_limit_cny,
            "budget_used_cny": 0.0, "attempt": 1, "retry_of": None,
            "idempotency_key": idempotency_key, "worker_id": None, "worker_status": None,
            "lease_expires_at": None, "timeout_at": timeout_at, "hard_deadline_at": None,
            "cancel_requested_at": None, "created_at": now, "queued_at": None,
            "started_at": None, "finished_at": None,
        }
        self.runs[run_id] = row
        self.events[run_id] = []
        return row, True

    def update_status(self, run_id, new_status, *, allowed_from, **fields):
        row = self.runs.get(run_id)
        if row is None or row["status"] not in tuple(allowed_from):
            return False
        row["status"] = new_status
        row.update(fields)
        return True

    def get_run(self, run_id):
        return self.runs.get(run_id)

    def get_run_by_idempotency(self, user_id, idempotency_key):
        for row in self.runs.values():
            if row["user_id"] == user_id and row["idempotency_key"] == idempotency_key:
                return row
        return None

    def list_runs(self, *, user_id=None, statuses=None, limit=20, offset=0):
        rows = [r for r in self.runs.values()
                if (user_id is None or r["user_id"] == user_id)
                and (not statuses or r["status"] in statuses)]
        rows.sort(key=lambda r: (r["created_at"], r["run_id"]), reverse=True)
        out = []
        for row in rows[offset:offset + limit]:
            item = dict(row)
            item["has_report"] = "report_md" in self.artifacts.get(row["run_id"], {})
            out.append(item)
        return out

    def request_cancel(self, run_id):
        row = self.runs.get(run_id)
        if row is None:
            return None
        now = datetime.now(UTC)
        if row["status"] in ("CREATED", "QUEUED"):
            row.update(status="CANCELLED", stop_reason="user_cancelled",
                       cancel_requested_at=now, finished_at=now)
            return "CANCELLED"
        if row["status"] == "RUNNING":
            row.update(status="CANCEL_REQUESTED", cancel_requested_at=now)
            return "CANCEL_REQUESTED"
        return row["status"]

    def append_event(self, run_id, event_type, payload=None, *, sequence=None):
        if self.fail_events:
            raise RuntimeError("db down")
        if run_id not in self.runs:
            raise LookupError(f"run not found: {run_id}")
        seq = len(self.events[run_id]) if sequence is None else sequence
        if any(item["sequence"] == seq for item in self.events[run_id]):
            return seq
        self.events[run_id].append({"run_id": run_id, "sequence": seq,
                                    "event_type": event_type, "payload": payload or {}})
        self.events[run_id].sort(key=lambda item: item["sequence"])
        return seq

    def get_events(self, run_id, after=None):
        return [item for item in self.events.get(run_id, [])
                if after is None or item["sequence"] > after]

    def count_events(self, run_id):
        return len(self.events.get(run_id, []))

    def put_artifact(self, run_id, kind, body):
        self.artifacts.setdefault(run_id, {})[kind] = body

    def get_artifact(self, run_id, kind):
        return self.artifacts.get(run_id, {}).get(kind)

    def has_artifact(self, run_id, kind):
        return kind in self.artifacts.get(run_id, {})

    def mark_stale_as_lost(self, reason="lost"):
        count = 0
        for row in self.runs.values():
            if row["status"] in ("CREATED", "QUEUED", "RUNNING", "CANCEL_REQUESTED"):
                row.update(status="LOST", stop_reason=reason, finished_at=datetime.now(UTC))
                count += 1
        return count


def _manager(store, *, steps=3, delay=0.01, report="") -> RunManager:
    return RunManager(
        graph_factory=lambda: TinyGraph(steps=steps, delay=delay, report=report),
        store=store,
        max_concurrent_runs=8,
    )


def _wait_finished(manager: RunManager, run_id: str, timeout: float = 5.0) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = manager.snapshot(run_id)
        if snap is not None and snap["status"] == "finished":
            return snap
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


_TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST")


def _wait_persisted(store: FakeStore, run_id: str, timeout: float = 5.0) -> dict:
    """等待落库终局：内存终局先于 DB 写入（锁外 I/O）⇒ 断言前必须等库。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = store.get_run(run_id)
        if row is not None and row["status"] in _TERMINAL:
            return row
        time.sleep(0.01)
    raise AssertionError("run did not reach a terminal state in store")


def _wait_artifact(store: FakeStore, run_id: str, kind: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.has_artifact(run_id, kind):
            return
        time.sleep(0.01)
    raise AssertionError(f"artifact {kind} not written in time")


def _seed_run(store: FakeStore, *, status: str = "SUCCEEDED", report: str = "# 历史报告") -> str:
    """造一条「上一个进程留下的」终局 run（含事件与产物）。"""
    started = datetime.now(UTC) - timedelta(minutes=5)
    row, _ = store.create_run(SEEDED_RUN, "历史主题", {"instructions": ""},
                              timeout_at=started + timedelta(seconds=3600))
    store.update_status(SEEDED_RUN, "RUNNING", allowed_from=("CREATED",), started_at=started)
    store.append_event(SEEDED_RUN, "RUN_STARTED", {"topic": "历史主题"}, sequence=0)
    store.append_event(SEEDED_RUN, "RUN_FINISHED",
                       {"stop_reason": "completed", "has_report": True}, sequence=1)
    if status == "SUCCEEDED":
        store.update_status(SEEDED_RUN, "SUCCEEDED", allowed_from=("RUNNING",),
                            stop_reason="completed", research_status="success",
                            finished_at=started + timedelta(minutes=4))
        if report:
            store.put_artifact(SEEDED_RUN, "report_md", report)
            store.put_artifact(SEEDED_RUN, "export_json",
                               json.dumps({"run_id": SEEDED_RUN, "result": {"report": report}}))
    return SEEDED_RUN


# ---------------------------------------------------------------- 写透（RunManager）


def test_write_through_on_success():
    store = FakeStore()
    manager = _manager(store, report="# 正文")
    run_id = manager.start("写透测试")
    _wait_finished(manager, run_id)

    row = _wait_persisted(store, run_id)
    assert row["status"] == "SUCCEEDED"
    assert row["research_status"] == "success"
    assert row["stop_reason"] == "completed"
    assert row["started_at"] is not None and row["finished_at"] is not None

    events = store.get_events(run_id)
    assert [item["sequence"] for item in events] == list(range(len(events)))
    assert events[0]["event_type"] == "RUN_STARTED"
    assert events[-1]["event_type"] == "RUN_FINISHED"
    _wait_artifact(store, run_id, "report_md")
    assert store.get_artifact(run_id, "report_md") == "# 正文"
    _wait_artifact(store, run_id, "export_json")
    payload = json.loads(store.get_artifact(run_id, "export_json") or "{}")
    assert payload["run_id"] == run_id


def test_idempotent_start_returns_existing_run():
    store = FakeStore()
    manager = _manager(store)
    first = manager.start("幂等", idempotency_key="key-1")
    second = manager.start("幂等", idempotency_key="key-1")
    assert first == second
    assert len(store.runs) == 1
    _wait_finished(manager, first)


def test_cancel_request_is_persisted():
    store = FakeStore()
    manager = _manager(store, steps=20, delay=0.05)
    run_id = manager.start("取消")
    time.sleep(0.1)
    assert manager.cancel(run_id) is True
    assert store.get_run(run_id)["status"] in ("CANCEL_REQUESTED", "CANCELLED")
    _wait_finished(manager, run_id)
    row = _wait_persisted(store, run_id)
    assert row["status"] == "CANCELLED"
    assert row["stop_reason"] == "user_cancelled"


def test_persistence_failure_is_recorded_not_fatal():
    store = FakeStore()
    store.fail_events = True
    manager = _manager(store, report="# 仍然完成")
    run_id = manager.start("故障降级")
    snap = _wait_finished(manager, run_id)
    assert snap["persistence_error"].startswith("RuntimeError")
    assert snap["has_report"] is True


# ---------------------------------------------------------------- 读侧回落（HTTP）


def _client_with_store(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager", _manager(store))
    return TestClient(api.app)


def test_snapshot_falls_back_to_store(monkeypatch):
    store = FakeStore()
    _seed_run(store)
    client = _client_with_store(monkeypatch, store)

    body = client.get(f"/api/research/{SEEDED_RUN}").json()
    assert body["persisted"] is True
    assert body["status"] == "finished"
    assert body["has_report"] is True
    assert body["stop_reason"] == "completed"
    assert body["event_count"] == 2
    assert body["elapsed_seconds"] >= 0


def test_list_runs_endpoint(monkeypatch):
    store = FakeStore()
    _seed_run(store)
    client = _client_with_store(monkeypatch, store)

    body = client.get("/api/runs", params={"limit": 10}).json()
    assert body["limit"] == 10
    assert [row["run_id"] for row in body["runs"]] == [SEEDED_RUN]
    assert body["runs"][0]["has_report"] is True

    hit = client.get("/api/runs", params={"status": "SUCCEEDED"}).json()
    assert len(hit["runs"]) == 1
    miss = client.get("/api/runs", params={"status": "RUNNING"}).json()
    assert miss["runs"] == []


def test_list_runs_503_without_store(monkeypatch):
    monkeypatch.setattr(api, "store", None)
    response = TestClient(api.app).get("/api/runs")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"


def test_report_falls_back_to_store(monkeypatch):
    store = FakeStore()
    _seed_run(store, report="# 历史报告")
    client = _client_with_store(monkeypatch, store)

    md = client.get(f"/api/research/{SEEDED_RUN}/report", params={"format": "md"})
    assert md.status_code == 200
    assert md.text == "# 历史报告"

    payload = client.get(f"/api/research/{SEEDED_RUN}/report", params={"format": "json"}).json()
    assert payload["run_id"] == SEEDED_RUN


def test_stream_replays_from_store(monkeypatch):
    store = FakeStore()
    _seed_run(store)
    client = _client_with_store(monkeypatch, store)

    with client.stream("GET", f"/api/research/{SEEDED_RUN}/stream") as response:
        assert response.headers["content-type"].startswith("text/event-stream")
        types = [json.loads(line[6:])["type"]
                 for line in response.iter_lines() if line.startswith("data: ")]
    assert types == ["RUN_STARTED", "RUN_FINISHED"]

    with client.stream("GET", f"/api/research/{SEEDED_RUN}/stream",
                       headers={"Last-Event-ID": "0"}) as response:
        resumed = [json.loads(line[6:])["type"]
                   for line in response.iter_lines() if line.startswith("data: ")]
    assert resumed == ["RUN_FINISHED"]


def test_cancel_falls_back_to_store(monkeypatch):
    store = FakeStore()
    _seed_run(store, status="RUNNING")
    client = _client_with_store(monkeypatch, store)

    assert client.post(f"/api/research/{SEEDED_RUN}/cancel").json()["ok"] is True
    assert store.get_run(SEEDED_RUN)["status"] == "CANCEL_REQUESTED"

    unknown = client.post("/api/research/no-such-run/cancel")
    assert unknown.status_code == 404


def test_start_idempotent_via_http(monkeypatch):
    store = FakeStore()
    client = _client_with_store(monkeypatch, store)

    first = client.post("/api/research", json={"topic": "t", "idempotency_key": "abc"}).json()["run_id"]
    second = client.post("/api/research", json={"topic": "t", "idempotency_key": "abc"}).json()["run_id"]
    assert first == second
    assert len(store.runs) == 1


def test_start_returns_503_when_store_down(monkeypatch):
    class DownStore(FakeStore):
        def create_run(self, *args, **kwargs):
            raise RuntimeError("db down")

    store = DownStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager", _manager(store))
    response = TestClient(api.app).post("/api/research", json={"topic": "t"})
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "persistence_unavailable"
