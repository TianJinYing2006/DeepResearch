"""需求 26：报告只读分享 API 测试（FakeStore，零 PostgreSQL）。"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import token_hash
from web.backend.runner import RunManager


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    fake.create_run("run-share-1", "分享测试主题", {}, status="DONE", user_id="u1")
    fake.put_artifact("run-share-1", "report_md", "# 报告正文\n\n引用清单")
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=1),
                                   store=store, max_concurrent_runs=4))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    monkeypatch.setattr(api, "SHARE_ENABLED", True)
    return TestClient(api.app)


def _token_of(response) -> str:
    url = response.json()["url"]
    assert url.startswith("/s/")
    return url[len("/s/"):]


def test_share_create_view_revoke_flow(client: TestClient, store: FakeStore):
    created = client.post("/api/runs/run-share-1/share", json={"expires_days": 7})
    assert created.status_code == 200
    token = _token_of(created)
    assert created.json()["permanent"] is False

    status = client.get("/api/runs/run-share-1/share")
    assert status.status_code == 200 and status.json()["active"] is True

    viewed = client.get(f"/api/share/{token}")
    assert viewed.status_code == 200
    body = viewed.json()
    assert body["topic"] == "分享测试主题" and "报告正文" in body["markdown"]
    assert viewed.headers["cache-control"] == "private, no-store"
    assert viewed.headers["x-robots-tag"] == "noindex, nofollow, noarchive"
    assert viewed.headers["referrer-policy"] == "no-referrer"
    assert store.list_audit(action="share_viewed")

    revoked = client.delete("/api/runs/run-share-1/share")
    assert revoked.status_code == 200 and revoked.json()["revoked"] is True
    assert client.get(f"/api/share/{token}").status_code == 404
    assert store.list_audit(action="share_revoked")


def test_share_uniform_404_for_unknown_and_expired(client: TestClient, store: FakeStore):
    unknown = client.get("/api/share/definitely-not-a-real-token-123456")
    assert unknown.status_code == 404
    assert unknown.json()["detail"]["code"] == "share_not_found"

    created = client.post("/api/runs/run-share-1/share", json={"expires_days": 1})
    token = _token_of(created)
    store.report_shares[token_hash(token)]["expires_at"] = datetime.now(UTC) - timedelta(hours=1)
    expired = client.get(f"/api/share/{token}")
    assert expired.status_code == 404
    assert expired.json()["detail"]["code"] == "share_not_found"


def test_share_recreate_rotates_token(client: TestClient):
    first = _token_of(client.post("/api/runs/run-share-1/share", json={"expires_days": 7}))
    second = _token_of(client.post("/api/runs/run-share-1/share", json={"expires_days": 7}))

    assert first != second
    assert client.get(f"/api/share/{first}").status_code == 404
    assert client.get(f"/api/share/{second}").status_code == 200


def test_share_permanent_option(client: TestClient):
    created = client.post("/api/runs/run-share-1/share", json={"expires_days": 0})
    assert created.status_code == 200 and created.json()["permanent"] is True
    token = _token_of(created)

    assert client.get(f"/api/share/{token}").status_code == 200
    status = client.get("/api/runs/run-share-1/share").json()
    assert status["permanent"] is True and status["expires_at"] is None


def test_share_disabled_returns_404(client: TestClient, monkeypatch):
    monkeypatch.setattr(api, "SHARE_ENABLED", False)
    assert client.post("/api/runs/run-share-1/share", json={}).status_code == 404
    assert client.get("/api/share/whatever-token-123456").status_code == 404


def test_share_requires_report(client: TestClient, store: FakeStore):
    store.create_run("run-no-report", "无报告", {}, status="DONE", user_id="u1")
    response = client.post("/api/runs/run-no-report/share", json={})
    assert response.status_code >= 400
    assert response.json()["detail"]["code"] == "report_unavailable"


def test_share_view_rate_limited(client: TestClient, monkeypatch):
    token = _token_of(client.post("/api/runs/run-share-1/share", json={}))

    class _DenyLimiter:
        def allow(self, key):
            return False

    monkeypatch.setattr(api, "SHARE_LIMITER", _DenyLimiter())
    assert client.get(f"/api/share/{token}").status_code == 429


def test_runs_list_and_manage_endpoints_expose_share_state(client: TestClient):
    """需求 26：历史列表「已分享」徽标数据源 + 集中管理页列表/撤销。"""
    assert client.post("/api/runs/run-share-1/share", json={"expires_days": 7}).status_code == 200

    briefs = client.get("/api/runs").json()["runs"]
    row = next(item for item in briefs if item["run_id"] == "run-share-1")
    assert row["shared"] is True

    shares = client.get("/api/shares").json()["shares"]
    assert len(shares) == 1
    assert shares[0]["run_id"] == "run-share-1" and shares[0]["permanent"] is False

    assert client.delete("/api/runs/run-share-1/share").status_code == 200
    assert client.get("/api/shares").json()["shares"] == []
    after = next(item for item in client.get("/api/runs").json()["runs"]
                 if item["run_id"] == "run-share-1")
    assert after["shared"] is False
