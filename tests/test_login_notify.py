"""需求 20 P1-1b：新设备登录提醒测试（FakeStore + FakeMailer，零 PostgreSQL / 网络）。

覆盖：首次/同设备不发；新设备（UA 变化）发一封 + 审计 `login_notified`；
SMTP 未配置 ⇒ 显式降级 `login_notify_skipped`；投递失败 ⇒ `login_notify_failed` 且登录不受影响；
设备指纹与「新设备」判定的纯函数单测。
"""
from __future__ import annotations

import pytest
from fakes import FakeStore, TinyGraph
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend.auth import device_key, is_new_device
from web.backend.runner import RunManager

PASSWORD = "password-123456"


class FakeMailer:
    def __init__(self) -> None:
        self.sent: list[dict] = []
        self.fail_with: Exception | None = None

    def send(self, to, subject, text, html=None) -> None:
        if self.fail_with is not None:
            raise self.fail_with
        self.sent.append({"to": to, "subject": subject, "text": text, "html": html})


@pytest.fixture()
def mailer() -> FakeMailer:
    return FakeMailer()


@pytest.fixture()
def store() -> FakeStore:
    fake = FakeStore()
    return fake


@pytest.fixture()
def client(monkeypatch, store: FakeStore, mailer: FakeMailer) -> TestClient:
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager",
                        RunManager(graph_factory=lambda: TinyGraph(steps=1),
                                   store=store, max_concurrent_runs=4))
    monkeypatch.setattr(api, "queue", None)
    monkeypatch.setattr(api, "EXECUTION_MODE", "inprocess")
    monkeypatch.setattr(api, "AUTH_REQUIRED", True)
    monkeypatch.setattr(api, "get_mailer", lambda: mailer)
    return TestClient(api.app)


def _register(client: TestClient, email: str, ua: str):
    return client.post("/api/auth/register",
                       json={"email": email, "password": PASSWORD,
                             "agree_terms": True},
                       headers={"user-agent": ua})


def _login(client: TestClient, email: str, ua: str):
    return client.post("/api/auth/login", json={"email": email, "password": PASSWORD},
                       headers={"user-agent": ua})


def _actions(store: FakeStore) -> list[str]:
    return [row["action"] for row in store.audits]


# ----------------------------------------------------------------- 纯函数

def test_device_key_is_coarse():
    # IPv4 收敛到 /24：同网段视为同设备
    assert device_key("UA", "1.2.3.4") == device_key("UA", "1.2.3.99")
    # UA 大小写不敏感
    assert device_key("Mozilla", "1.2.3.4") == device_key("mozilla", "1.2.3.4")
    # IPv6 收敛到前四组
    assert device_key("ua", "2001:db8:1:2:3:4:5:6") == device_key("ua", "2001:db8:1:2:ff:ff:ff:ff")
    # 不同设备（UA）不同键
    assert device_key("A", "1.2.3.4") != device_key("B", "1.2.3.4")


def test_is_new_device_rules():
    assert is_new_device([], "UA", "1.2.3.4") is False           # 无历史 ⇒ 不算新（避免注册即打扰）
    known = device_key("UA", "1.2.3.4")
    assert is_new_device([known], "UA", "1.2.3.4") is False      # 已知设备
    assert is_new_device([known], "OTHER", "1.2.3.4") is True    # 新 UA ⇒ 新设备
    assert is_new_device([known], "UA", "9.9.9.9") is True       # 新网段 ⇒ 新设备


# ----------------------------------------------------------------- 接口行为

def test_no_email_on_first_login_or_same_device(client: TestClient, store: FakeStore,
                                                mailer: FakeMailer):
    assert _register(client, "a@example.com", ua="DeviceA").status_code == 200
    assert mailer.sent == []                                     # 注册即首次登录，不发
    assert _login(client, "a@example.com", ua="DeviceA").status_code == 200
    assert mailer.sent == []                                     # 同设备，不发
    assert "login_notified" not in _actions(store)


def test_notifies_on_new_device(client: TestClient, store: FakeStore, mailer: FakeMailer):
    assert _register(client, "a@example.com", ua="DeviceA").status_code == 200
    response = _login(client, "a@example.com", ua="DeviceB")
    assert response.status_code == 200
    assert len(mailer.sent) == 1
    assert mailer.sent[0]["to"] == "a@example.com"
    assert "新设备" in mailer.sent[0]["subject"]
    assert "DeviceB" in mailer.sent[0]["text"]
    assert _actions(store).count("login_notified") == 1

    # 再次从 DeviceB 登录（已知设备）⇒ 不再发
    assert _login(client, "a@example.com", ua="DeviceB").status_code == 200
    assert len(mailer.sent) == 1


def test_smtp_unconfigured_degrades_explicitly(monkeypatch, client: TestClient,
                                               store: FakeStore, mailer: FakeMailer):
    monkeypatch.setattr(api, "get_mailer", lambda: None)
    assert _register(client, "a@example.com", ua="DeviceA").status_code == 200
    assert _login(client, "a@example.com", ua="DeviceB").status_code == 200
    assert mailer.sent == []
    assert _actions(store).count("login_notify_skipped") == 1    # 显式降级，不静默


def test_send_failure_does_not_block_login(client: TestClient, store: FakeStore,
                                           mailer: FakeMailer):
    assert _register(client, "a@example.com", ua="DeviceA").status_code == 200
    mailer.fail_with = RuntimeError("smtp down")
    assert _login(client, "a@example.com", ua="DeviceB").status_code == 200   # 登录不受影响
    assert _actions(store).count("login_notify_failed") == 1
