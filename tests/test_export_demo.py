"""需求 38 F2b：DR_DEMO 导出替身单测（e2e 依赖的内存语义；零 PG / 零 Redis / 零渲染）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.demo_export import _TINY_PDF, DemoExportStore

RID = "demo-run-38f2"


def test_demo_store_semantics():
    store = DemoExportStore()
    job = store.create_export_job("j1", run_id=RID, user_id=None, request_hash="h1")
    assert job["status"] == "SUCCEEDED" and job["result_expires_at"] > datetime.now(UTC)
    assert store.get_export_job("j1") is job
    assert store.find_reusable_export_job("h1") is job
    assert store.get_result("j1") == _TINY_PDF

    # 过期 ⇒ 不可复用、结果不可取（与真实链路 TTL 口径一致）
    job["result_expires_at"] = datetime.now(UTC) - timedelta(seconds=1)
    assert store.find_reusable_export_job("h1") is None
    assert store.get_result("j1") is None


def _demo_client(monkeypatch, *, has_result: bool = True) -> TestClient:
    monkeypatch.setattr(api, "store", None)
    monkeypatch.setattr(api, "_demo_export_store", DemoExportStore())
    monkeypatch.setattr(api.manager, "exists", lambda rid: True)
    monkeypatch.setattr(api.manager, "owner", lambda rid: None)
    monkeypatch.setattr(api.manager, "has_result", lambda rid: has_result)
    monkeypatch.setattr(api.manager, "export_payload", lambda rid: {"topic": "演示话题"})
    return TestClient(api.app)


def test_demo_endpoints_full_flow(monkeypatch):
    """DR_DEMO 语义：store=None + manager 内存 + 替身 ⇒ 创建→状态→下载全流程可用。"""
    client = _demo_client(monkeypatch)
    created = client.post(f"/api/research/{RID}/export-jobs", json={})
    assert created.status_code == 202
    body = created.json()
    assert body["status"] == "SUCCEEDED" and body["reused"] is False
    jid = body["export_id"]

    status = client.get(f"/api/research/{RID}/export-jobs/{jid}")
    assert status.status_code == 200 and status.json()["status"] == "SUCCEEDED"

    download = client.get(f"/api/research/{RID}/export-jobs/{jid}/download")
    assert download.status_code == 200 and download.content == _TINY_PDF
    disposition = download.headers["content-disposition"]
    assert "deepresearch-demo-run-38f2.pdf" in disposition  # ASCII 回退名
    assert "filename*=UTF-8''" in disposition  # 中文展示名（RFC 5987）

    # 幂等复用：同 run 再申请 ⇒ 返回同一作业
    again = client.post(f"/api/research/{RID}/export-jobs", json={})
    assert again.status_code == 202 and again.json()["reused"] is True
    assert again.json()["export_id"] == jid


def test_demo_endpoints_guard_without_report(monkeypatch):
    client = _demo_client(monkeypatch, has_result=False)
    r = client.post(f"/api/research/{RID}/export-jobs", json={})
    assert r.status_code == 404 and r.json()["detail"]["code"] == "report_unavailable"
