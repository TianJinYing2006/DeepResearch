"""需求 38 F2：PDF 导出作业 API 单测（零网络；store / 结果通道全注入）。

覆盖：创建（202 / 幂等复用 / 输入早拒 / 未终局拒绝）、状态查询、
下载（成功 / 未就绪 / 过期 / 通道缺失 / 结果丢失）、kind 校验、作业归属校验。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from web.backend import main as api

RID = "run-38f2-api"


def _row(**overrides) -> dict:
    row = {
        "run_id": RID,
        "topic": "PDF 作业主题",
        "finished_at": datetime(2026, 10, 16, 8, 0, 30, tzinfo=UTC),
        "created_at": datetime(2026, 10, 16, 8, 0, 0, tzinfo=UTC),
        "research_status": "success",
        "stop_reason": "completed",
        "token_used": 100,
        "cost_estimate_cny": 0.001,
        "moderation_status": None,
        "request": {},
        "user_id": "u-38f2",
    }
    row.update(overrides)
    return row


class _JobStore:
    """作业 fake（内存）；只实现本链路接口（含审计 best-effort 接收）。"""

    def __init__(self, row: dict, report_md: str = "# 报告\n\n正文。"):
        self.row = row
        self.report_md = report_md
        self.jobs: dict = {}

    def get_run(self, run_id):
        return self.row if run_id == self.row["run_id"] else None

    def get_artifact_row(self, run_id, kind):
        if run_id != self.row["run_id"]:
            return None
        if kind == "report_md" and self.report_md:
            return {"body": self.report_md, "storage": "pg", "object_key": None}
        return None

    def record_audit(self, *args, **kwargs):
        return None

    def find_reusable_export_job(self, request_hash):
        now = datetime.now(UTC)
        for job in self.jobs.values():
            if job["request_hash"] != request_hash:
                continue
            if job["status"] in ("QUEUED", "RUNNING"):
                return job
            if (job["status"] == "SUCCEEDED" and job["result_expires_at"]
                    and job["result_expires_at"] > now):
                return job
        return None

    def create_export_job(self, job_id, *, run_id, user_id, request_hash, max_attempts=2):
        job = {
            "job_id": job_id, "run_id": run_id, "user_id": user_id, "kind": "pdf",
            "status": "QUEUED", "attempt": 0, "max_attempts": max_attempts,
            "request_hash": request_hash, "lease_owner": None, "lease_expires_at": None,
            "error_code": None, "error_detail": None, "result_key": None,
            "result_bytes": None, "result_hash": None, "result_expires_at": None,
            "created_at": datetime.now(UTC), "started_at": None, "finished_at": None,
        }
        self.jobs[job_id] = job
        return job

    def get_export_job(self, job_id):
        return self.jobs.get(job_id)


class _ResultRedis:
    def __init__(self, data: dict):
        self.data = data

    def get(self, key):
        return self.data.get(key)


def _client(monkeypatch, store) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api.manager, "exists", lambda rid: False)
    return TestClient(api.app)


def _succeed(store, job_id: str, *, expires_in: int = 900, pdf: bytes = b"%PDF-1.4 ok") -> None:
    job = store.jobs[job_id]
    job["status"] = "SUCCEEDED"
    job["result_bytes"] = len(pdf)
    job["result_hash"] = "h"
    job["result_key"] = "dr:export:result:" + job_id
    job["result_expires_at"] = datetime.now(UTC) + timedelta(seconds=expires_in)


def test_create_job_202_and_idempotent_reuse(monkeypatch):
    store = _JobStore(_row())
    client = _client(monkeypatch, store)
    r1 = client.post(f"/api/research/{RID}/export-jobs", json={})
    assert r1.status_code == 202
    body = r1.json()
    assert body["status"] == "QUEUED" and body["reused"] is False and body["export_id"]
    r2 = client.post(f"/api/research/{RID}/export-jobs", json={"kind": "pdf"})
    assert r2.status_code == 202
    assert r2.json()["reused"] is True and r2.json()["export_id"] == body["export_id"]


def test_create_job_kind_validation(monkeypatch):
    client = _client(monkeypatch, _JobStore(_row()))
    r = client.post(f"/api/research/{RID}/export-jobs", json={"kind": "docx"})
    assert r.status_code == 422


def test_create_job_guards(monkeypatch):
    client = _client(monkeypatch, _JobStore(_row(finished_at=None)))
    r = client.post(f"/api/research/{RID}/export-jobs", json={})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "report_not_ready"

    client2 = _client(monkeypatch, _JobStore(_row(), report_md=""))
    r2 = client2.post(f"/api/research/{RID}/export-jobs", json={})
    assert r2.status_code == 404 and r2.json()["detail"]["code"] == "report_unavailable"

    client3 = _client(monkeypatch, _JobStore(_row(), report_md="x" * 100))
    monkeypatch.setattr(api, "EXPORT_PDF_MAX_INPUT_BYTES", 10)
    r3 = client3.post(f"/api/research/{RID}/export-jobs", json={})
    assert r3.status_code == 413 and r3.json()["detail"]["code"] == "export_too_large"


def test_status_endpoint(monkeypatch):
    store = _JobStore(_row())
    client = _client(monkeypatch, store)
    jid = client.post(f"/api/research/{RID}/export-jobs", json={}).json()["export_id"]

    r = client.get(f"/api/research/{RID}/export-jobs/{jid}")
    assert r.status_code == 200
    assert r.json() == {"export_id": jid, "status": "QUEUED", "error_code": None,
                        "result_bytes": None, "result_expires_at": None}

    assert client.get(f"/api/research/{RID}/export-jobs/nope").status_code == 404
    # 作业归属：不属于该 run 的 export_id 同样 404
    other = client.get("/api/research/other-run/export-jobs/%s" % jid)
    assert other.status_code == 404


def test_download_success(monkeypatch):
    store = _JobStore(_row())
    client = _client(monkeypatch, store)
    jid = client.post(f"/api/research/{RID}/export-jobs", json={}).json()["export_id"]
    _succeed(store, jid)
    monkeypatch.setattr(api, "_export_result_store",
                        lambda: _ResultRedis({"dr:export:result:" + jid: b"%PDF-1.4 ok"}))

    r = client.get(f"/api/research/{RID}/export-jobs/{jid}/download")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/pdf")
    assert ".pdf" in r.headers["content-disposition"]
    assert r.headers["cache-control"] == "private, no-store"
    assert r.content == b"%PDF-1.4 ok"


def test_download_not_ready_and_expired_and_missing(monkeypatch):
    store = _JobStore(_row())
    client = _client(monkeypatch, store)
    jid = client.post(f"/api/research/{RID}/export-jobs", json={}).json()["export_id"]
    url = f"/api/research/{RID}/export-jobs/{jid}/download"

    r = client.get(url)  # 尚在 QUEUED
    assert r.status_code == 409 and r.json()["detail"]["code"] == "export_not_ready"

    _succeed(store, jid, expires_in=-1)  # 已过期（PG 元数据）
    r2 = client.get(url)
    assert r2.status_code == 410 and r2.json()["detail"]["code"] == "export_result_expired"

    _succeed(store, jid)  # 未过期但结果丢失
    monkeypatch.setattr(api, "_export_result_store", lambda: _ResultRedis({}))
    r3 = client.get(url)
    assert r3.status_code == 410 and r3.json()["detail"]["code"] == "export_result_expired"


def test_download_channel_unconfigured(monkeypatch):
    store = _JobStore(_row())
    client = _client(monkeypatch, store)
    jid = client.post(f"/api/research/{RID}/export-jobs", json={}).json()["export_id"]
    _succeed(store, jid)
    monkeypatch.setattr(api, "_export_result_store", lambda: None)
    r = client.get(f"/api/research/{RID}/export-jobs/{jid}/download")
    assert r.status_code == 503 and r.json()["detail"]["code"] == "export_unavailable"
