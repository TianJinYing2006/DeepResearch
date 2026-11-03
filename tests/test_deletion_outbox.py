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


# ---- #320（审计 High）：注销链路完整性（同事务擦除 + 异步清理目标快照）----


def test_request_erases_account_links_and_revokes_shares(monkeypatch, tmp_path):
    """注销后：分享立即 404、consents 清空、ingestion 去关联、quota/export 无 user_id。"""
    from fastapi.testclient import TestClient

    from web.backend import main as api
    from web.backend.auth import token_hash
    from web.backend.ratelimit import FixedWindowLimiter

    monkeypatch.setenv("DR_RAG_QUARANTINE_DIR", str(tmp_path))
    store = FakeStore()
    store.create_user("u-del-2", "u-del-2@example.com", "hash")
    store.create_run("run-del-1", "主题", {}, status="DONE", user_id="u-del-2")
    store.put_artifact("run-del-1", "report_md", "# 正文")
    store.create_report_share("share-1", token_hash("share-token-1"), "run-del-1",
                              "u-del-2", None)
    store.save_user_consents("u-del-2", [{"doc_type": "terms", "version": "v1"},
                                         {"doc_type": "privacy", "version": "v1"}],
                             ip_hash="ip-hash-1")
    store.create_ingestion("ing000000009", "u-del-2:abc", user_id="u-del-2",
                           source="a.md", sha256="abc", size_bytes=2,
                           stored_name="orphan-in-quarantine.md")
    store.rag_snapshots["u-del-2:abc"] = [{"block_index": 0, "text": "x"}]
    store.quota_reservations["run-del-1"] = {
        "reservation_id": "qr_run-del-1", "run_id": "run-del-1", "user_id": "u-del-2",
        "reserved_cny": 1.5, "actual_cny": 0.0, "released_cny": 0.0,
        "status": "reserved", "created_at": datetime.now(UTC)}
    store.export_jobs["job-1"] = {"job_id": "job-1", "run_id": "run-del-1",
                                  "user_id": "u-del-2", "status": "QUEUED"}
    quarantine_file = tmp_path / "orphan-in-quarantine.md"
    quarantine_file.write_text("原文", encoding="utf-8")

    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "SHARE_ENABLED", True)
    monkeypatch.setattr(api, "SHARE_LIMITER", FixedWindowLimiter(1000))
    client = TestClient(api.app)
    assert client.get("/api/share/share-token-1").status_code == 200  # 注销前可读

    store.request_account_deletion("del000000002", "u-del-2")

    # 1) 分享立即失效（匿名 404）—— High 缺陷的主回归
    assert store.resolve_report_share(token_hash("share-token-1")) is None
    assert client.get("/api/share/share-token-1").status_code == 404
    # 2) 同意留档（含 ip_hash）已删
    assert store.list_user_consents("u-del-2") == []
    # 3) RAG 台账保留但去关联（并标记 deleted）
    ingestion = store.get_ingestion("ing000000009")
    assert ingestion["user_id"] is None and ingestion["status"] == "deleted"
    # 4) 配额预留 / 导出作业去关联（记录保留用于对账）
    assert store.quota_reservations["run-del-1"]["user_id"] is None
    assert store.export_jobs["job-1"]["user_id"] is None
    # 5) 用户已删
    assert store.get_user("u-del-2") is None

    # 6) outbox 按 payload 快照完成异步清理（隔离区文件 / RAG 三层数据）
    outbox = next(iter(store.deletion_outbox.values()))
    assert outbox["payload"]["quarantine_files"] == ["orphan-in-quarantine.md"]
    assert outbox["payload"]["rag_doc_ids"] == ["u-del-2:abc"]
    summary = process_deletions_once(store, vector_store=FakeVectorStore())
    assert summary["done"] == 1
    assert not quarantine_file.exists()
    assert store.rag_snapshots.get("u-del-2:abc") is None


def test_legacy_outbox_row_without_snapshot_still_cleans(monkeypatch, tmp_path):
    """向后兼容：#320 之前入队的 outbox（payload 只有 user_id）仍能按 user_id 回查清理。"""
    monkeypatch.setenv("DR_RAG_QUARANTINE_DIR", str(tmp_path))
    store = FakeStore()
    store.create_user("u-legacy", "legacy@example.com", "hash")
    store.create_ingestion("ing000000010", "u-legacy:def", user_id="u-legacy",
                           source="b.md", sha256="def", size_bytes=2,
                           stored_name="legacy.bin")
    (tmp_path / "legacy.bin").write_text("x", encoding="utf-8")

    now = datetime.now(UTC)
    store.deletions["dell00000001"] = {
        "request_id": "dell00000001", "user_id": "u-legacy", "status": "pending",
        "attempts": 0, "last_error": None, "requested_at": now,
        "completed_at": None, "updated_at": now,
    }
    store._deletion_outbox_seq += 1
    store.deletion_outbox[("dell00000001", "qdrant")] = {
        "id": store._deletion_outbox_seq, "request_id": "dell00000001",
        "target": "qdrant", "payload": {"user_id": "u-legacy"}, "status": "pending",
        "attempts": 0, "next_attempt_at": now, "lease_expires_at": None,
        "claimed_by": None, "last_error": None,
    }

    summary = process_deletions_once(store, vector_store=FakeVectorStore())

    assert summary["done"] == 1
    assert not (tmp_path / "legacy.bin").exists()  # 回退路径按 user_id 找到隔离区文件
    assert store.get_ingestion("ing000000010")["user_id"] is None


def test_request_deletion_sql_covers_all_erasures_in_one_transaction(monkeypatch):
    """#320 契约：真实 RunStore 的注销事务必须覆盖 6 类擦除，且清理目标快照先于置 NULL。

    行为用例跑在 FakeStore 镜像上；本用例用假连接断言**真实 SQL 语句集与顺序**，
    防止 store.py 实现回退（本机零依赖 lane 不连 PostgreSQL）。
    """
    from web.backend.store import RunStore

    statements: list[tuple[str, object]] = []

    class _Cursor:
        def __init__(self) -> None:
            self._rows: list = []

        def execute(self, sql, params=None):
            statements.append((" ".join(sql.split()), params))
            if "SELECT stored_name" in sql:
                self._rows = [{"stored_name": "q.bin"}]
            elif "SELECT DISTINCT doc_id" in sql:
                self._rows = [{"doc_id": "u:abc"}]
            else:
                self._rows = []

        def fetchall(self):
            return self._rows

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _Conn:
        def cursor(self):
            return _Cursor()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    store = object.__new__(RunStore)
    monkeypatch.setattr(store, "_connect", lambda: _Conn())
    store.request_account_deletion("req-320", "u-320")

    sqls = [sql for sql, _ in statements]
    joined = "\n".join(sqls)
    # 清理目标快照必须先于 rag_ingestions.user_id 置 NULL（否则 Worker 无法定位）
    snapshot_at = next(i for i, sql in enumerate(sqls) if "SELECT stored_name" in sql)
    null_at = next(i for i, sql in enumerate(sqls) if "UPDATE rag_ingestions" in sql)
    assert snapshot_at < null_at
    # 六类擦除必须都在本事务内
    assert "UPDATE report_shares SET revoked_at = now() " \
           "WHERE created_by = %s AND revoked_at IS NULL" in joined
    assert "DELETE FROM user_consents WHERE user_id = %s" in joined
    assert "UPDATE rag_ingestions SET status = 'deleted'" in joined and "user_id = NULL" in joined
    assert "UPDATE quota_reservations SET user_id = NULL WHERE user_id = %s" in joined
    assert "UPDATE export_jobs SET user_id = NULL WHERE user_id = %s" in joined
    # 清理目标必须写进 outbox payload（置 NULL 之后 Worker 只能靠它定位）
    outbox_insert = next(params for sql, params in statements
                         if "INSERT INTO deletion_outbox" in sql)
    outbox_payload = outbox_insert[2].obj
    assert outbox_payload["quarantine_files"] == ["q.bin"]
    assert outbox_payload["rag_doc_ids"] == ["u:abc"]
    # 删用户是最后一步（此前必须完成全部关联擦除）
    assert sqls[-1] == "DELETE FROM users WHERE user_id = %s"
