"""P1（L3-A）健康探针与 CORS 环境变量化测试。

零 LLM、零外部依赖：ready 的「可达」用例用本进程监听的临时端口模拟，不启动真实 PG/Redis。
"""
from __future__ import annotations

import socket

from fastapi.testclient import TestClient

from web.backend import main as api


def _free_port() -> int:
    """拿一个当前空闲的端口（用于模拟“依赖不可达”）。"""
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def test_liveness_is_always_ok():
    client = TestClient(api.app)
    r = client.get("/api/health/live")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "check": "live"}


def test_readiness_without_configured_dependencies_is_ready(monkeypatch):
    monkeypatch.delenv("DR_DATABASE_URL", raising=False)
    monkeypatch.delenv("DR_REDIS_URL", raising=False)
    r = TestClient(api.app).get("/api/health/ready")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["status"] == "ready"
    assert body["checks"]["postgres"] == {"status": "not_configured"}
    assert body["checks"]["redis"] == {"status": "not_configured"}


def test_readiness_reports_unreachable_dependency(monkeypatch):
    monkeypatch.setenv("DR_DATABASE_URL", f"postgresql://u:p@127.0.0.1:{_free_port()}/db")
    monkeypatch.delenv("DR_REDIS_URL", raising=False)
    r = TestClient(api.app).get("/api/health/ready")
    assert r.status_code == 503
    body = r.json()
    assert body["ok"] is False
    assert body["status"] == "not_ready"
    assert body["checks"]["postgres"]["status"] == "unreachable"


def test_readiness_reports_invalid_url(monkeypatch):
    monkeypatch.setenv("DR_REDIS_URL", "not-a-url")
    monkeypatch.delenv("DR_DATABASE_URL", raising=False)
    r = TestClient(api.app).get("/api/health/ready")
    assert r.status_code == 503
    assert r.json()["checks"]["redis"]["status"] == "invalid_url"


def test_readiness_marks_dependency_ok_when_port_open(monkeypatch):
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    port = server.getsockname()[1]
    monkeypatch.setenv("DR_REDIS_URL", f"redis://127.0.0.1:{port}/0")
    monkeypatch.delenv("DR_DATABASE_URL", raising=False)
    try:
        r = TestClient(api.app).get("/api/health/ready")
    finally:
        server.close()
    assert r.status_code == 200
    assert r.json()["checks"]["redis"] == {"status": "ok", "target": f"127.0.0.1:{port}"}


def test_cors_origins_default_and_env_override(monkeypatch):
    monkeypatch.delenv("DR_CORS_ORIGINS", raising=False)
    assert api._cors_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]

    monkeypatch.setenv("DR_CORS_ORIGINS", "https://dr.example.cn, https://staging.dr.example.cn")
    assert api._cors_origins() == ["https://dr.example.cn", "https://staging.dr.example.cn"]

    monkeypatch.setenv("DR_CORS_ORIGINS", "   ")
    assert api._cors_origins() == ["http://localhost:5173", "http://127.0.0.1:5173"]
