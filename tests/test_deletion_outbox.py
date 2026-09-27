"""P0-7 注销 outbox 单测（FakeStore + 假向量库，零 PostgreSQL / Qdrant）。

- 注销登记同事务：台账 + outbox + 删用户；
- 执行器：成功需通过「点数归零」验证；失败指数退避；耗尽 abandoned；
- 人工重试复位；幂等（重复执行不产生重复台账）。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fakes import FakeStore

from web.backend.deletion import process_deletions_once


class FakeVectorStore:
    def __init__(self, *, reason: str | None = None, remaining: int = 0):
        self.reason = reason
        self.remaining = remaining
        self.deleted: list[tuple[str, bool]] = []

    @property
    def unavailable_reason(self):
        return self.reason

    def delete_by_user(self, user_id: str, *, wait: bool = True) -> None:
        self.deleted.append((user_id, wait))

    def count_by_user(self, user_id: str) -> int:
        return self.remaining


def _request(store: FakeStore, request_id: str = "del000000001",
             user_id: str = "u-del-1") -> str:
    store.create_user(user_id, f"{user_id}@example.com", "hash")
    store.request_account_deletion(request_id, user_id)
    return request_id


def _advance_backoff(store: FakeStore) -> None:
    for item in store.deletion_outbox.values():
        item["next_attempt_at"] = datetime.now(UTC) - timedelta(seconds=1)


def test_request_is_atomic_and_deletes_user():
    store = FakeStore()
    _request(store)
    assert store.get_user("u-del-1") is None
    record = store.deletion_status("del000000001")
    assert record["status"] == "pending"
    assert record["targets"][0]["target"] == "qdrant"
    assert record["targets"][0]["payload"]["user_id"] == "u-del-1"


def test_processor_success_marks_completed_and_verifies():
    store = FakeStore()
    _request(store)
    vector_store = FakeVectorStore()

    summary = process_deletions_once(store, vector_store=vector_store)

    assert summary == {"claimed": 1, "done": 1, "retried": 0, "abandoned": 0}
    assert vector_store.deleted == [("u-del-1", True)]  # wait=True 供验证
    record = store.deletion_status("del000000001")
    assert record["status"] == "completed"
    assert record["completed_at"] is not None


def test_processor_retries_on_failure_with_backoff():
    store = FakeStore()
    _request(store)
    vector_store = FakeVectorStore(reason="not_configured")

    summary = process_deletions_once(store, vector_store=vector_store)
    assert summary["retried"] == 1

    record = store.deletion_status("del000000001")
    assert record["status"] == "in_progress"
    target = record["targets"][0]
    assert target["status"] == "pending" and target["attempts"] == 1
    assert target["next_attempt_at"] > datetime.now(UTC)  # 已退避
    assert process_deletions_once(store, vector_store=vector_store)["claimed"] == 0


def test_verification_failure_retries():
    """删完仍有残留点 ⇒ 不算成功（必须验证归零）。"""
    store = FakeStore()
    _request(store)
    vector_store = FakeVectorStore(remaining=3)

    summary = process_deletions_once(store, vector_store=vector_store)

    assert summary["retried"] == 1
    target = store.deletion_status("del000000001")["targets"][0]
    assert "still has 3 points" in target["last_error"]


def test_processor_abandons_after_max_attempts_and_retry_resets():
    store = FakeStore()
    _request(store)
    vector_store = FakeVectorStore(reason="down")

    for _ in range(3):
        _advance_backoff(store)
        process_deletions_once(store, vector_store=vector_store, max_attempts=3)

    record = store.deletion_status("del000000001")
    assert record["status"] == "abandoned"
    assert record["targets"][0]["status"] == "abandoned"
    assert "down" in record["last_error"]

    # 人工重试：复位为 pending 后可由执行器再次处理
    assert store.retry_deletion("del000000001") == 1
    assert store.deletion_status("del000000001")["status"] == "pending"
    summary = process_deletions_once(store, vector_store=FakeVectorStore())
    assert summary["done"] == 1
