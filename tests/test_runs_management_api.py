"""需求 22 任务列表增强 API 测试（FakeStore，零 PostgreSQL）。

覆盖：关键词搜索 / 归档过滤 / 重命名（越权 404、CSRF、空名 400）/ 置顶排序 /
归档（进行中 409）/ 失败重试（血缘、幂等、终态校验）。
"""
from __future__ import annotations

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import CSRF_COOKIE, CSRF_HEADER, token_hash
from web.backend.runner import RunManager

PASSWORD = "password-123456"


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    fake.create_invite(token_hash("invite-1"), created_by="test")
    fake.create_invite(token_hash("invite-2"), created_by="test")
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=2, delay=0.01),
                                   store=store, max_concurrent_runs=8))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", True)
    # 用例内直接用 FakeStore 预置种子 run，会占满默认日配额/并发闸 —— 放开（闸行为另有用例覆盖）
    monkeypatch.setattr(api, "DAILY_RUNS_PER_USER", 0)
    monkeypatch.setattr(api, "MAX_USER_CONCURRENT", 8)
    return TestClient(api.app)


def _register(client: TestClient, email: str, code: str = "invite-1"):
    return client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD, "invite_code": code,
                             "agree_terms": True})


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def _user_id(client: TestClient) -> str:
    return client.get("/api/auth/session").json()["user"]["user_id"]


def _seed(store: FakeStore, *, user_id: str, status: str, topic: str,
          request=None) -> dict:
    run_id = f"seed{len(store.runs):04d}"
    row, _ = store.create_run(run_id, topic, request or {"instructions": "ins"},
                              user_id=user_id, status=status)
    return row


def test_search_and_archived_filter(client: TestClient, store: FakeStore):
    assert _register(client, "a@example.com").status_code == 200
    uid = _user_id(client)
    _seed(store, user_id=uid, status="SUCCEEDED", topic="量子计算综述")
    _seed(store, user_id=uid, status="SUCCEEDED", topic="蛋白质折叠")
    archived = _seed(store, user_id=uid, status="SUCCEEDED", topic="量子退火")
    assert store.set_archived(archived["run_id"], True, user_id=uid) is True

    default = client.get("/api/runs").json()["runs"]
    assert {r["topic"] for r in default} == {"量子计算综述", "蛋白质折叠"}

    found = client.get("/api/runs", params={"q": "量子"}).json()["runs"]
    assert {r["topic"] for r in found} == {"量子计算综述"}

    archived_rows = client.get("/api/runs", params={"archived": "true"}).json()["runs"]
    assert {r["topic"] for r in archived_rows} == {"量子退火"}


def test_rename_pin_and_ordering(client: TestClient, store: FakeStore):
    assert _register(client, "a@example.com").status_code == 200
    uid = _user_id(client)
    first = _seed(store, user_id=uid, status="SUCCEEDED", topic="第一")
    _seed(store, user_id=uid, status="SUCCEEDED", topic="第二")

    # CSRF / 空名
    assert client.patch(f"/api/runs/{first['run_id']}",
                        json={"topic": "x"}).status_code == 403
    empty = client.patch(f"/api/runs/{first['run_id']}", json={"topic": "   "},
                         headers=_csrf(client))
    assert empty.status_code == 400 and empty.json()["detail"]["code"] == "empty_topic"

    renamed = client.patch(f"/api/runs/{first['run_id']}", json={"topic": "新名字"},
                           headers=_csrf(client))
    assert renamed.status_code == 200 and renamed.json()["topic"] == "新名字"

    assert client.post(f"/api/runs/{first['run_id']}/pin",
                       headers=_csrf(client)).status_code == 200
    rows = client.get("/api/runs").json()["runs"]
    assert rows[0]["run_id"] == first["run_id"] and rows[0]["pinned_at"]
    assert client.post(f"/api/runs/{first['run_id']}/unpin",
                       headers=_csrf(client)).status_code == 200
    assert client.get("/api/runs").json()["runs"][0]["pinned_at"] is None


def test_archive_blocks_active_and_restores(client: TestClient, store: FakeStore):
    assert _register(client, "a@example.com").status_code == 200
    uid = _user_id(client)
    running = _seed(store, user_id=uid, status="RUNNING", topic="进行中")
    done = _seed(store, user_id=uid, status="SUCCEEDED", topic="已完成")

    blocked = client.post(f"/api/runs/{running['run_id']}/archive", headers=_csrf(client))
    assert blocked.status_code == 409
    assert blocked.json()["detail"]["code"] == "run_active"

    assert client.post(f"/api/runs/{done['run_id']}/archive",
                       headers=_csrf(client)).status_code == 200
    remaining = client.get("/api/runs").json()["runs"]
    assert [r["run_id"] for r in remaining] == [running["run_id"]]
    assert client.post(f"/api/runs/{done['run_id']}/unarchive",
                       headers=_csrf(client)).status_code == 200
    assert len(client.get("/api/runs").json()["runs"]) == 2


def test_retry_clones_request_with_lineage_and_idempotency(
        client: TestClient, store: FakeStore):
    assert _register(client, "a@example.com").status_code == 200
    uid = _user_id(client)
    failed = _seed(store, user_id=uid, status="FAILED", topic="失败任务",
                   request={"instructions": "详细点", "profile": {"name": "quick"}})
    done = _seed(store, user_id=uid, status="SUCCEEDED", topic="成功任务")

    not_retryable = client.post(f"/api/runs/{done['run_id']}/retry", headers=_csrf(client))
    assert not_retryable.status_code == 409
    assert not_retryable.json()["detail"]["code"] == "run_not_retryable"

    first = client.post(f"/api/runs/{failed['run_id']}/retry", headers=_csrf(client))
    assert first.status_code == 200
    new_id = first.json()["run_id"]
    assert new_id != failed["run_id"]
    new_row = store.get_run(new_id)
    assert new_row["retry_of"] == failed["run_id"]
    assert new_row["topic"] == "失败任务"
    assert new_row["request"]["instructions"] == "详细点"

    # 幂等：重复点击返回同一个新 run
    again = client.post(f"/api/runs/{failed['run_id']}/retry", headers=_csrf(client))
    assert again.status_code == 200 and again.json()["run_id"] == new_id


def test_cross_user_actions_are_404(client: TestClient, store: FakeStore):
    assert _register(client, "owner@example.com", code="invite-1").status_code == 200
    owner_id = _user_id(client)
    row = _seed(store, user_id=owner_id, status="FAILED", topic="owner 的任务")

    client.cookies.clear()
    assert _register(client, "other@example.com", code="invite-2").status_code == 200

    assert client.patch(f"/api/runs/{row['run_id']}", json={"topic": "盗改"},
                        headers=_csrf(client)).status_code == 404
    assert client.post(f"/api/runs/{row['run_id']}/pin", headers=_csrf(client)).status_code == 404
    assert client.post(f"/api/runs/{row['run_id']}/archive",
                       headers=_csrf(client)).status_code == 404
    assert client.post(f"/api/runs/{row['run_id']}/retry", headers=_csrf(client)).status_code == 404
