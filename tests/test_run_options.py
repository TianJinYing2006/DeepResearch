"""运行选项与档位（P0 profile 固化）单测。

覆盖五层：
1. `/api/options` 只把**已配 key** 的搜索源列为可用，并下发可选档位；
2. 档位解析：未知档位 400 早失败；`quick` / `standard` 可启动；
3. 旧底层参数（`max_total_hops` / `max_subquestions` / `search_provider` /
   `enable_arxiv`）一律**忽略**：不影响本场 run 生效值，且写入
   `run.request.ignored_overrides` 作审计留痕（需求 10 §5.6）；
4. 档位真正生效：Planner 提示词拿到档位值，且**不污染全局 config**；
5. `config.search.enable_arxiv=False` 时 Researcher 不再调度 arXiv。
"""
from __future__ import annotations

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from config import config
from research_engine.agents.planner import build_planner_system
from research_engine.runtime_profile import use_profile
from research_engine.state import DegradationSink, ResearchState
from web.backend import main as api
from web.backend.profiles import resolve_profile
from web.backend.runner import RunManager


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
    # P1-3：并发闸默认 1，用例串行但上一个 run 的工作线程可能还没退出 ⇒ 放开闸
    api.manager.max_concurrent_runs = 8
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


def test_options_exposes_profiles(client):
    """P0：前端只展示档位（底层参数不可提交）。"""
    data = client.get("/api/options").json()
    values = [item["value"] for item in data["profiles"]]
    assert values == ["quick", "standard"]
    assert data["default_profile"] == "quick"
    quick = data["profiles"][0]
    assert quick["max_total_hops"] == 6
    assert quick["max_subquestions"] == 2
    assert quick["timeout_seconds"] == 15 * 60


# ---- 2) 档位解析：未知档位早失败 ----

def test_start_rejects_unknown_profile(client):
    resp = client.post("/api/research", json={"topic": "t", "profile": "nope"})
    # 项目错误映射：invalid_request → 422（与 FastAPI 参数校验同码）
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_request"


def test_start_accepts_standard_profile(client):
    resp = client.post("/api/research", json={"topic": "t", "profile": "standard"})
    assert resp.status_code == 200
    assert resp.json()["run_id"]


def test_start_without_profile_defaults_to_quick(client):
    """不传档位时必须照旧可用（默认 quick）。"""
    resp = client.post("/api/research", json={"topic": "t"})
    assert resp.status_code == 200


# ---- 3) 旧底层参数：忽略 + 留痕 ----

def test_http_start_ignores_legacy_overrides(client, monkeypatch):
    store = FakeStore()
    manager = RunManager(graph_factory=_OneStepGraph, store=store, max_concurrent_runs=8)
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "manager", manager)

    resp = client.post("/api/research", json={
        "topic": "t", "profile": "quick",
        "max_total_hops": 50, "max_subquestions": 8,
        "search_provider": "tavily", "enable_arxiv": True,
    })
    assert resp.status_code == 200
    row = store.get_run(resp.json()["run_id"])
    snapshot = row["request"]["profile"]
    assert snapshot["name"] == "quick"
    assert snapshot["max_total_hops"] == 6          # 客户端 50 被忽略
    assert snapshot["max_subquestions"] == 2        # 客户端 8 被忽略
    assert row["request"]["ignored_overrides"] == {
        "max_total_hops": 50, "max_subquestions": 8,
        "search_provider": "tavily", "enable_arxiv": True,
    }


def test_profile_snapshot_persisted_with_run():
    store = FakeStore()
    manager = RunManager(graph_factory=_OneStepGraph, store=store, max_concurrent_runs=8)
    run_id = manager.start("t", "附加", resolve_profile("standard"),
                           ignored_overrides={"max_total_hops": 50})
    row = store.get_run(run_id)
    assert row["request"]["profile"]["name"] == "standard"
    assert row["request"]["profile"]["model"] == "qwen-plus"
    assert row["request"]["ignored_overrides"] == {"max_total_hops": 50}


# ---- 4) 档位生效但不污染全局 config ----

def test_profile_drives_planner_prompt_without_global_mutation(monkeypatch):
    """Planner 的 system prompt 现读**本场档位**；全局 config 保持原值。"""
    monkeypatch.setattr(config.research, "max_subquestions", 4)
    with use_profile(resolve_profile("standard")):
        assert "3 个以内" in build_planner_system()
    assert "4 个以内" in build_planner_system()
    assert config.research.max_subquestions == 4


# ---- 5) 学术检索开关（服务端配置，客户端不可覆盖）----

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
