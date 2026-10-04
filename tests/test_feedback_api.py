"""需求 25：站内反馈 API 测试（FakeStore，零 PostgreSQL）。"""
from __future__ import annotations

import json

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
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=1),
                                   store=store, max_concurrent_runs=4))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "INVITE_ONLY", True)
    return TestClient(api.app)


def _register(client: TestClient, email: str = "fb@example.com"):
    return client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD,
                             "invite_code": "invite-1", "agree_terms": True})


def _csrf(client: TestClient) -> dict[str, str]:
    return {CSRF_HEADER: client.cookies.get(CSRF_COOKIE)}


def test_feedback_requires_login(client: TestClient):
    response = client.post("/api/feedback",
                           json={"category": "bug", "message": "登录前的反馈" * 2})
    assert response.status_code == 401


def test_feedback_submitted_and_audited_without_body(client: TestClient, store: FakeStore):
    _register(client)

    response = client.post("/api/feedback",
                           json={"category": "idea", "message": "希望支持批量导出报告",
                                 "contact": "user@example.com", "page": "workbench"},
                           headers=_csrf(client))

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True and len(body["feedback_id"]) == 12
    row = store.feedback[0]
    assert row["category"] == "idea" and row["page"] == "workbench"
    assert row["request_id"]
    audits = store.list_audit(action="feedback_submitted")
    assert audits and "希望支持批量导出" not in json.dumps(audits[0]["detail"])
    assert audits[0]["detail"]["message_chars"] == len("希望支持批量导出报告")


def test_feedback_validation(client: TestClient):
    _register(client)
    headers = _csrf(client)

    too_short = client.post("/api/feedback",
                            json={"category": "bug", "message": "太短"}, headers=headers)
    assert too_short.status_code == 422

    bad_category = client.post("/api/feedback",
                               json={"category": "spam", "message": "分类非法的反馈内容"},
                               headers=headers)
    assert bad_category.status_code == 422


def test_feedback_rate_limited(client: TestClient, monkeypatch):
    _register(client)

    class _DenyLimiter:
        def allow(self, key):
            return False

    monkeypatch.setattr(api, "SUBMIT_LIMITER", _DenyLimiter())
    response = client.post("/api/feedback",
                           json={"category": "other", "message": "限流场景下的反馈内容"},
                           headers=_csrf(client))
    assert response.status_code == 429
