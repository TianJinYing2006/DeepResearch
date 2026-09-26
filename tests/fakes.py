"""测试用内存替身：FakeStore（RunStore 语义子集）与 TinyGraph（零 LLM 假图）。

供 `test_web_persistence.py`（P2-C 接线）与 `test_worker.py`（P3-A Worker）共用；
真实 PostgreSQL / Redis 的集成测试见 `test_run_store.py` 与 `test_worker_integration.py`。
"""
from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta
from typing import Optional

from research_engine.state import ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED, RunStep

ACTIVE = ("CREATED", "QUEUED", "RUNNING", "CANCEL_REQUESTED")
TERMINAL = ("SUCCEEDED", "FAILED", "CANCELLED", "TIMED_OUT", "LOST")


class TinyGraph:
    """最小假 graph：可配节点数 / 延迟 / 报告正文（零 LLM）。"""

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


class BoomGraph:
    """第一步就抛异常：验证 Worker 的 crash 兜底。"""

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        raise RuntimeError("boom")
        yield  # pragma: no cover —— 生成器语法占位


class FakeQueue:
    def __init__(self) -> None:
        self.items: list[str] = []

    def enqueue(self, run_id: str) -> int:
        self.items.insert(0, run_id)
        return len(self.items)

    def dequeue(self, timeout: float = 0) -> Optional[str]:
        return self.items.pop() if self.items else None

    def depth(self) -> int:
        return len(self.items)

    def ping(self) -> bool:
        return True


class FakeStore:
    """内存版 RunStore：语义与 web/backend/store.py 对齐，供接线 / Worker 测试使用。"""

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
            "cancel_requested_at": None, "created_at": now,
            "queued_at": now if status == "QUEUED" else None,
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

    def claim_run(self, run_id, worker_id, lease_seconds):
        row = self.runs.get(run_id)
        if row is None or row["status"] != "QUEUED":
            return None
        row["status"] = "RUNNING"
        row["worker_id"] = worker_id
        row["worker_status"] = "alive"
        row["started_at"] = row["started_at"] or datetime.now(UTC)
        row["queued_at"] = row["queued_at"] or row["created_at"]
        row["lease_expires_at"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        return row

    def renew_lease(self, run_id, worker_id, lease_seconds):
        row = self.runs.get(run_id)
        if row is None or row["worker_id"] != worker_id:
            return False
        if row["status"] not in ("RUNNING", "CANCEL_REQUESTED"):
            return False
        row["lease_expires_at"] = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        row["worker_status"] = "alive"
        return True

    def count_active(self, user_id=None):
        return sum(1 for row in self.runs.values()
                   if row["status"] in ACTIVE and (user_id is None or row["user_id"] == user_id))

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

    def count_events(self, run_id, event_type=None):
        return sum(1 for item in self.events.get(run_id, [])
                   if event_type is None or item["event_type"] == event_type)

    def last_event_type(self, run_id):
        items = self.events.get(run_id) or []
        return items[-1]["event_type"] if items else None

    def put_artifact(self, run_id, kind, body):
        self.artifacts.setdefault(run_id, {})[kind] = body

    def get_artifact(self, run_id, kind):
        return self.artifacts.get(run_id, {}).get(kind)

    def has_artifact(self, run_id, kind):
        return kind in self.artifacts.get(run_id, {})

    def mark_stale_as_lost(self, reason="lost"):
        count = 0
        for row in self.runs.values():
            if row["status"] in ACTIVE:
                row.update(status="LOST", stop_reason=reason, finished_at=datetime.now(UTC))
                count += 1
        return count


def wait_terminal(store, run_id, timeout: float = 5.0) -> dict:
    """等待库内终局（内存终局先于落库的时序在 FakeStore 上同样成立）。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        row = store.get_run(run_id)
        if row is not None and row["status"] in TERMINAL:
            return row
        time.sleep(0.01)
    raise AssertionError("run did not reach a terminal state in store")


def event_types(store, run_id) -> list[str]:
    return [item["event_type"] for item in store.get_events(run_id)]
