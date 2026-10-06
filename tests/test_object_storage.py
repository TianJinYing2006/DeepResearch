"""P1-6 对象存储单测（假 S3 客户端，零 MinIO）。"""
from __future__ import annotations

from fakes import FakeStore
from fastapi.testclient import TestClient

import web.backend.objectstore as objectstore_module
from web.backend import main as api
from web.backend.objectstore import ObjectStore, reset_object_store
from web.backend.persistence import persist_terminal
from web.backend.store import RunOwnership


class _FakeS3:
    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.buckets: set[str] = set()
        self.lifecycle = None

    def list_buckets(self):
        return {"Buckets": [{"Name": name} for name in self.buckets]}

    def create_bucket(self, Bucket):
        self.buckets.add(Bucket)

    def put_bucket_lifecycle_configuration(self, Bucket, LifecycleConfiguration):
        self.lifecycle = LifecycleConfiguration

    def put_object(self, Bucket, Key, Body, ContentType=None):
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):
        class _Body:
            def __init__(self, data):
                self._data = data

            def read(self):
                return self._data

        return {"Body": _Body(self.objects[Key])}

    def head_bucket(self, Bucket):
        return {}


def _object_store(fake: _FakeS3) -> ObjectStore:
    store = ObjectStore(endpoint="http://minio:9000", access_key="k", secret_key="s",
                        bucket="deepresearch")
    store._client = fake
    return store


def test_disabled_without_endpoint(monkeypatch):
    monkeypatch.delenv("DR_S3_ENDPOINT", raising=False)
    reset_object_store()
    try:
        assert objectstore_module.get_object_store() is None
    finally:
        reset_object_store()


def test_put_get_and_bucket_lifecycle():
    fake = _FakeS3()
    store = _object_store(fake)

    store.ensure_bucket(retention_days=90)
    info = store.put_text("runs/r1/report_md", "# 报告", content_type="text/markdown")

    assert fake.buckets == {"deepresearch"}
    rules = {rule["ID"]: rule for rule in fake.lifecycle["Rules"]}
    assert rules["expire-runs"]["Expiration"]["Days"] == 90
    assert rules["expire-runs"]["Filter"]["Prefix"] == "runs/"
    # 备份加密件（需求 20 §8）：PUT 会整体替换配置，两条规则必须一起提交
    assert rules["expire-backups"]["Expiration"]["Days"] == 90
    assert rules["expire-backups"]["Filter"]["Prefix"] == "backups/"
    assert info["key"] == "runs/r1/report_md"
    assert info["size_bytes"] == len("# 报告".encode("utf-8"))
    assert len(info["sha256"]) == 64
    assert store.get_text("runs/r1/report_md") == "# 报告"
    assert store.ping() is True


def test_persist_terminal_uses_object_store(monkeypatch):
    fake = _FakeS3()
    object_store = _object_store(fake)
    monkeypatch.setattr(objectstore_module, "get_object_store", lambda: object_store)

    store = FakeStore()
    store.create_run("objrun01", "t", {}, status="RUNNING")
    finalized = persist_terminal(
        store, "objrun01", 0, "RUN_FINISHED",
        {"stop_reason": "completed", "has_report": True},
        result={"report": "# 正文"}, report="# 正文",
        meta={"run_status": "success", "token_used": 1, "cost_estimate_cny": 0.0,
              "budget_used_cny": 0.0},
        topic="t")

    assert finalized is True
    row = store.get_artifact_row("objrun01", "report_md")
    assert row["storage"] == "s3" and row["body"] == ""
    assert row["object_key"] == "runs/objrun01/attempt-1/report_md"
    assert fake.objects[row["object_key"]] == "# 正文".encode("utf-8")


def test_persist_terminal_object_key_is_attempt_scoped(monkeypatch):
    """F04：产物对象键按 attempt 版本化——旧执行者的迟到上传不覆盖新执行者。"""
    fake = _FakeS3()
    object_store = _object_store(fake)
    monkeypatch.setattr(objectstore_module, "get_object_store", lambda: object_store)

    store = FakeStore()
    store.create_run("objrun04", "t", {}, status="RUNNING")
    store.runs["objrun04"]["worker_id"] = "worker-b"
    store.runs["objrun04"]["attempt"] = 2

    finalized = persist_terminal(
        store, "objrun04", 0, "RUN_FINISHED",
        {"stop_reason": "completed", "has_report": True},
        result={"report": "# 第二跳正文"}, report="# 第二跳正文",
        meta={"run_status": "success", "token_used": 1, "cost_estimate_cny": 0.0,
              "budget_used_cny": 0.0},
        topic="t", owner=RunOwnership("worker-b", 2))

    assert finalized is True
    row = store.get_artifact_row("objrun04", "report_md")
    assert row["object_key"] == "runs/objrun04/attempt-2/report_md"
    assert fake.objects[row["object_key"]] == "# 第二跳正文".encode("utf-8")


def test_s3_failure_falls_back_to_db(monkeypatch):
    class _Broken(_FakeS3):
        def put_object(self, **kwargs):
            raise RuntimeError("s3 down")

    monkeypatch.setattr(objectstore_module, "get_object_store",
                        lambda: _object_store(_Broken()))
    store = FakeStore()
    store.create_run("objrun02", "t", {}, status="RUNNING")

    finalized = persist_terminal(store, "objrun02", 0, "RUN_FINISHED",
                                 {"stop_reason": "completed", "has_report": True},
                                 report="# 兜底", topic="t")

    assert finalized is True
    row = store.get_artifact_row("objrun02", "report_md")
    assert row["storage"] == "db" and row["body"] == "# 兜底"


def test_export_reads_from_object_store(monkeypatch):
    fake = _FakeS3()
    object_store = _object_store(fake)
    monkeypatch.setattr(objectstore_module, "get_object_store", lambda: object_store)
    monkeypatch.setattr(api, "get_object_store", lambda: object_store)

    store = FakeStore()
    store.create_run("objrun03", "t", {}, status="RUNNING")
    persist_terminal(store, "objrun03", 0, "RUN_FINISHED",
                     {"stop_reason": "completed", "has_report": True},
                     report="# 来自 S3", topic="t")
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)

    response = TestClient(api.app).get("/api/research/objrun03/report?format=md")
    assert response.status_code == 200
    assert response.text == "# 来自 S3"


def test_readiness_probes_object_storage(monkeypatch):
    monkeypatch.setattr(api, "get_object_store", lambda: _object_store(_FakeS3()))
    monkeypatch.setattr(api, "store", None)
    monkeypatch.delenv("DR_DATABASE_URL", raising=False)
    monkeypatch.delenv("DR_REDIS_URL", raising=False)

    body = TestClient(api.app).get("/api/health/ready").json()
    assert body["checks"]["object_storage"]["status"] == "ok"

    class _Broken(_FakeS3):
        def head_bucket(self, Bucket):
            raise RuntimeError("minio down")

    monkeypatch.setattr(api, "get_object_store", lambda: _object_store(_Broken()))
    response = TestClient(api.app).get("/api/health/ready")
    assert response.status_code == 503
    assert response.json()["checks"]["object_storage"]["status"] == "unreachable"
