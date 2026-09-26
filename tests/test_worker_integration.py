"""P3-A 队列 + Worker + API 实时尾随的集成测试（真实 PostgreSQL + Redis）。

跳过条件：`DR_TEST_DATABASE_URL` / `DR_TEST_REDIS_URL` 未设置；
CI 的 `infra` job 在 compose 的 PG / Redis 上执行（本地用端口映射后的 URL）。
"""
from __future__ import annotations

import json
import os
import time
import uuid

import psycopg
import pytest
from fakes import TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.queue import RunQueue
from web.backend.store import RunStore
from web.backend.worker import Worker

DSN = os.getenv("DR_TEST_DATABASE_URL", "").strip()
REDIS_URL = os.getenv("DR_TEST_REDIS_URL", "").strip()

pytestmark = pytest.mark.skipif(
    not (DSN and REDIS_URL),
    reason="需要 DR_TEST_DATABASE_URL + DR_TEST_REDIS_URL（真实 PostgreSQL / Redis）",
)


@pytest.fixture()
def store() -> RunStore:
    # 防线：清掉上游套件（test_run_store）可能遗留的 test-store-* 任务，
    # 否则活跃/过期租约行会污染并发闸与 sweep 断言（实测踩坑）。
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE user_id LIKE 'test-store-%'")
    return RunStore(DSN)


@pytest.fixture()
def queue() -> RunQueue:
    # 每个测试用独立队列键，避免与共享 Redis 上的其它数据串扰
    return RunQueue(REDIS_URL, key=f"dr:test:queue:{uuid.uuid4().hex[:8]}")


def _cleanup(run_id: str) -> None:
    with psycopg.connect(DSN) as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM runs WHERE run_id = %s", (run_id,))


def test_api_queue_mode_to_worker_to_stream(monkeypatch, store: RunStore, queue: RunQueue):
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "queue", queue)
    monkeypatch.setattr(api, "EXECUTION_MODE", "queue")
    client = TestClient(api.app)

    response = client.post("/api/research", json={"topic": "队列端到端", "idempotency_key": "it-key"})
    assert response.status_code == 200
    run_id = response.json()["run_id"]
    try:
        row = store.get_run(run_id)
        assert row is not None and row["status"] == "QUEUED"
        assert queue.depth() == 1

        # 幂等：重复提交返回同一 run_id，且不重复入队
        again = client.post("/api/research", json={"topic": "队列端到端", "idempotency_key": "it-key"})
        assert again.json()["run_id"] == run_id
        assert queue.depth() == 1

        worker = Worker(
            store, queue,
            graph_factory=lambda: TinyGraph(steps=4, delay=0.01, report="# 队列端到端报告"),
            worker_id="it-worker", lease_seconds=60, heartbeat_seconds=5, poll_seconds=1,
        )
        claimed = queue.dequeue(timeout=2)
        assert claimed == run_id
        assert worker.run_once(claimed) is True

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.get_run(run_id)["status"] == "SUCCEEDED":
                break
            time.sleep(0.05)
        assert store.get_run(run_id)["status"] == "SUCCEEDED"
        assert store.get_artifact(run_id, "report_md") == "# 队列端到端报告"
        assert store.get_run(run_id)["worker_id"] == "it-worker"

        # API 从 run_events 实时尾随：终局后连接 ⇒ 回放全部帧并收口
        with client.stream("GET", f"/api/research/{run_id}/stream") as stream:
            types = [
                json.loads(line[6:])["type"]
                for line in stream.iter_lines() if line.startswith("data: ")
            ]
        assert types[0] == "RUN_STARTED"
        assert types[-1] == "RUN_FINISHED"
        assert types.count("STEP_FINISHED") == 4
    finally:
        _cleanup(run_id)


def test_worker_requeues_nothing_when_claim_conflicts(store: RunStore, queue: RunQueue):
    """同一 run_id 重复入队时，只有一个 Worker 能认领（第二个认领返回 False 且不执行）。"""
    run_id = f"queue{uuid.uuid4().hex[:6]}"
    store.create_run(run_id, "重复入队", {}, status="QUEUED")
    try:
        queue.enqueue(run_id)
        queue.enqueue(run_id)
        worker = Worker(store, queue, graph_factory=lambda: TinyGraph(steps=1, delay=0.01),
                        worker_id="dup-worker", lease_seconds=60, heartbeat_seconds=5)
        assert worker.run_once(queue.dequeue(timeout=2)) is True
        assert worker.run_once(queue.dequeue(timeout=2)) is False
        assert store.get_run(run_id)["status"] == "SUCCEEDED"
    finally:
        _cleanup(run_id)


def test_queue_empty_dequeue_returns_none(queue: RunQueue):
    """空队列阻塞领取必须返回 None。

    redis-py 8 的阻塞命令会在客户端读超时（与阻塞超时同值）抛 `TimeoutError`，
    `RunQueue.dequeue` 已把它兜底成 None —— 这个用例防止该行为回归。
    """
    started = time.monotonic()
    assert queue.dequeue(timeout=1) is None
    assert time.monotonic() - started < 5


def test_stale_lease_is_swept_and_retried(store: RunStore, queue: RunQueue):
    """P3-B：Worker 崩溃（租约过期）→ 清扫接管 → 重新入队 → 第二 attempt 成功。"""
    run_id = f"stale{uuid.uuid4().hex[:6]}"
    store.create_run(run_id, "租约接管", {"instructions": ""}, status="QUEUED")
    try:
        assert store.claim_run(run_id, "dead-worker", 0) is not None  # 租约立即过期
        worker = Worker(
            store, queue,
            graph_factory=lambda: TinyGraph(steps=2, delay=0.01, report="# 重试成功"),
            worker_id="retry-worker", lease_seconds=60, heartbeat_seconds=5, poll_seconds=1,
        )
        assert worker.sweep_and_requeue() == [
            {"run_id": run_id, "action": "requeued", "attempt": 2}]
        claimed = queue.dequeue(timeout=2)
        assert claimed == run_id
        assert worker.run_once(claimed) is True

        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if store.get_run(run_id)["status"] == "SUCCEEDED":
                break
            time.sleep(0.05)
        row = store.get_run(run_id)
        assert row["status"] == "SUCCEEDED"
        assert row["attempt"] == 2
        assert store.get_artifact(run_id, "report_md") == "# 重试成功"
    finally:
        _cleanup(run_id)
