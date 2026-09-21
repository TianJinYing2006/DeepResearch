"""运行选项（搜索引擎切换 + 学术检索开关）单测。

覆盖三层：
1. `/api/options` 只把**已配 key** 的搜索源列为可用；
2. 启动校验：未知源 / 未配 key 的源必须 **400 早失败**（否则整场研究每跳降级为零
   结果，跑完才发现白跑 —— 博查额度耗尽正是这个情形）；
3. `config.search.enable_arxiv=False` 时 Researcher 不再调度 arXiv。
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from config import config
from research_engine.state import DegradationSink, ResearchState
from web.backend import main as api


class _OneStepGraph:
    """最小假 graph：立刻结束。只验证接口契约，不跑真实研究（成本高）。

    刻意**不复用** `tests.test_web_api` 的夹具：跨测试模块 import 依赖 rootdir
    是否在 sys.path，CI 上易碎；本文件只需一个「能启动」的 graph，内联最稳。
    """

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        from research_engine.streaming import STOP_COMPLETED, RunStep
        state = ResearchState(topic=topic)
        yield RunStep(index=0, node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)


@pytest.fixture()
def client():
    api.manager._graph_factory = _OneStepGraph
    return TestClient(api.app)


# ---- 1) /api/options ----

def test_options_lists_known_providers(client):
    data = client.get("/api/options").json()
    names = {p["value"] for p in data["search_providers"]}
    assert {"bocha", "tavily"} <= names
    assert data["default_provider"] in names
    assert isinstance(data["enable_arxiv_default"], bool)


def test_options_marks_provider_unavailable_without_key(client, monkeypatch):
    """未配 key 的源要标 unavailable —— 前端据此禁用选项，避免选了白跑。"""
    monkeypatch.setattr(config.search, "tavily_api_key", "")
    data = client.get("/api/options").json()
    tavily = next(p for p in data["search_providers"] if p["value"] == "tavily")
    assert tavily["available"] is False


def test_options_marks_provider_available_with_key(client, monkeypatch):
    monkeypatch.setattr(config.search, "bocha_api_key", "fake-key")
    data = client.get("/api/options").json()
    bocha = next(p for p in data["search_providers"] if p["value"] == "bocha")
    assert bocha["available"] is True


# ---- 2) 启动校验：必须早失败 ----

def test_start_rejects_unknown_provider(client):
    resp = client.post("/api/research", json={"topic": "t", "search_provider": "nope"})
    assert resp.status_code == 400
    assert "未知搜索引擎" in resp.json()["detail"]


def test_start_rejects_provider_without_key(client, monkeypatch):
    monkeypatch.setattr(config.search, "bocha_api_key", "")
    resp = client.post("/api/research", json={"topic": "t", "search_provider": "bocha"})
    assert resp.status_code == 400
    assert "未配置 API key" in resp.json()["detail"]


def test_start_accepts_valid_provider(client, monkeypatch):
    monkeypatch.setattr(config.search, "tavily_api_key", "fake-key")
    resp = client.post("/api/research", json={
        "topic": "t", "search_provider": "tavily", "enable_arxiv": False,
    })
    assert resp.status_code == 200
    assert resp.json()["run_id"]


def test_start_without_options_still_works(client):
    """不传新参数时必须照旧可用（向后兼容）。"""
    resp = client.post("/api/research", json={"topic": "t"})
    assert resp.status_code == 200


# ---- 3) 学术检索开关 ----

def _bare_researcher():
    from research_engine.agents.researcher import Researcher
    r = Researcher.__new__(Researcher)  # 绕过 __init__（避免真建 provider / 连 Qdrant）
    r.degradations = DegradationSink()
    return r


def test_arxiv_skipped_when_disabled(monkeypatch):
    monkeypatch.setattr(config.search, "enable_arxiv", False)
    r = _bare_researcher()
    calls: list[str] = []
    monkeypatch.setattr(r, "_search_web", lambda q: [])
    monkeypatch.setattr(r, "_search_rag", lambda q: [])
    monkeypatch.setattr(r, "_search_arxiv", lambda q: (calls.append(q), [])[1])
    r.search_once("普通查询不涉及数值")
    assert calls == [], "关闭后不得再调度 arXiv"


def test_arxiv_runs_when_enabled(monkeypatch):
    monkeypatch.setattr(config.search, "enable_arxiv", True)
    r = _bare_researcher()
    calls: list[str] = []
    monkeypatch.setattr(r, "_search_web", lambda q: [])
    monkeypatch.setattr(r, "_search_rag", lambda q: [])
    monkeypatch.setattr(r, "_search_arxiv", lambda q: (calls.append(q), [])[1])
    r.search_once("普通查询不涉及数值")
    assert calls == ["普通查询不涉及数值"]


if __name__ == "__main__":
    pytest.main([__file__, "-q"])
