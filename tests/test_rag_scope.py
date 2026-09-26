"""P5 RAG 多租户隔离测试（零 Qdrant / 零 LLM）。

覆盖：作用域判定语义（owner / 匿名 / tenant / visibility / 历史数据）、摄入 payload 打标、
向量检索「服务端过滤下推 + 后置过滤兜底」、BM25 按作用域取语料（缓存不串数据）、
执行器（RunManager / Worker）在每次研究前设置作用域。
"""
from __future__ import annotations

import time

from fakes import FakeQueue, FakeStore

from config import config
from research_engine.rag.ingest import DocumentIngester
from research_engine.rag.retriever import HybridRetriever
from research_engine.rag.scope import (
    RagScope,
    current_scope,
    payload_matches,
    set_scope,
    use_rag_scope,
)
from research_engine.rag.store import VectorStore
from research_engine.state import ResearchState
from research_engine.streaming import STOP_COMPLETED, RunStep
from web.backend.runner import RunManager
from web.backend.worker import Worker

OWNER_DOC = {"text": "t", "user_id": "u1", "visibility": "private", "source": "a.md"}
OTHER_DOC = {"text": "t2", "user_id": "u2", "visibility": "private", "source": "b.md"}
LEGACY_DOC = {"text": "t3", "source": "old.md"}  # P5 之前的历史块：无 user_id / visibility
TENANT_DOC = {"text": "t4", "user_id": "u1", "visibility": "private",
              "tenant_id": "t1", "source": "c.md"}


# ---------------------------------------------------------------- 判定语义


def test_owner_scope_sees_only_own_private_docs():
    scope = RagScope(user_id="u1")
    assert payload_matches(OWNER_DOC, scope) is True
    assert payload_matches(OTHER_DOC, scope) is False
    assert payload_matches(LEGACY_DOC, scope) is False  # owner 看不到无主历史块


def test_anonymous_scope_sees_only_ownerless_docs():
    scope = RagScope()
    assert payload_matches(LEGACY_DOC, scope) is True
    assert payload_matches(OWNER_DOC, scope) is False


def test_visibility_stays_private_only_and_tenant_must_match():
    assert payload_matches({**OWNER_DOC, "visibility": "shared"}, RagScope(user_id="u1")) is False
    assert payload_matches(TENANT_DOC, RagScope(user_id="u1")) is False
    assert payload_matches(TENANT_DOC, RagScope(user_id="u1", tenant_id="t1")) is True


def test_scope_context_manager_and_set_scope():
    assert current_scope().user_id is None
    with use_rag_scope(user_id="u9"):
        assert current_scope().user_id == "u9"
    assert current_scope().user_id is None

    set_scope(user_id="u1")
    assert current_scope().user_id == "u1"
    set_scope(user_id="u2")  # 执行器逐 run 覆盖
    assert current_scope().user_id == "u2"
    set_scope()
    assert current_scope().user_id is None


# ---------------------------------------------------------------- 向量库过滤


class _FakePoint:
    def __init__(self, pid, payload):
        self.id = pid
        self.score = 0.9
        self.payload = payload


class _FakeResponse:
    def __init__(self, points):
        self.points = points


class _FakeQdrant:
    def __init__(self, payloads):
        self.payloads = payloads
        self.last_filter = "unset"

    def query_points(self, **kwargs):
        self.last_filter = kwargs.get("query_filter")
        return _FakeResponse([_FakePoint(i, p) for i, p in enumerate(self.payloads)])

    def scroll(self, **kwargs):
        return [_FakePoint(i, p) for i, p in enumerate(self.payloads)], None


def test_store_search_pushes_down_owner_filter_and_post_filters(monkeypatch):
    store = VectorStore()
    fake = _FakeQdrant([OWNER_DOC, OTHER_DOC, LEGACY_DOC])
    monkeypatch.setattr(store, "_get_client", lambda: fake)

    hits = store.search([0.1], top_k=5, scope=RagScope(user_id="u1"))
    assert fake.last_filter is not None
    assert {condition.key for condition in fake.last_filter.must} == {"user_id", "visibility"}
    assert [h["payload"]["source"] for h in hits] == ["a.md"]

    # 服务端「失手」返回他人块时，后置过滤必须兜住
    fake.payloads = [OWNER_DOC, OTHER_DOC]
    hits = store.search([0.1], top_k=5, scope=RagScope(user_id="u1"))
    assert [h["payload"]["source"] for h in hits] == ["a.md"]

    # 匿名作用域不下推服务端过滤，但后置只留无主块
    fake.payloads = [OWNER_DOC, LEGACY_DOC]
    hits = store.search([0.1], top_k=5, scope=RagScope())
    assert fake.last_filter is None
    assert [h["payload"]["source"] for h in hits] == ["old.md"]


def test_store_scroll_all_respects_scope(monkeypatch):
    store = VectorStore()
    fake = _FakeQdrant([OWNER_DOC, LEGACY_DOC])
    monkeypatch.setattr(store, "_get_client", lambda: fake)
    assert [p["source"] for p in store.scroll_all(scope=RagScope(user_id="u1"))] == ["a.md"]
    assert [p["source"] for p in store.scroll_all(scope=RagScope())] == ["old.md"]


# ---------------------------------------------------------------- 检索（BM25 路）


class _FakeStore:
    def __init__(self, payloads):
        self.payloads = payloads
        self.unavailable_reason = None
        self.last_error = None

    def scroll_all(self, limit=10000, *, scope=None):
        return [p for p in self.payloads if scope is None or payload_matches(p, scope)]

    def search(self, vector, top_k=5, scope=None):
        return []


def test_retriever_bm25_isolates_scopes_and_caches_per_scope(monkeypatch):
    monkeypatch.setattr(config.llm, "api_key", "")  # 向量路不可用，专测 BM25

    def _group(user_id: str | None, prefix: str) -> list[dict]:
        # 三篇同组语料：让目标词只在其中一篇出现 ⇒ BM25 得分为正（单文档语料 IDF 非正）
        user_field = {"user_id": user_id} if user_id else {}
        return [
            {"text": f"{prefix}widget alpha", "source": f"{prefix}1.md", "visibility": "private", **user_field},
            {"text": f"{prefix}gadget beta", "source": f"{prefix}2.md", "visibility": "private", **user_field},
            {"text": f"{prefix}gismo gamma", "source": f"{prefix}3.md", "visibility": "private", **user_field},
        ]

    payloads = _group("u1", "mine") + _group("u2", "other") + [
        {"text": "legacywidget alpha", "source": "legacy1.md"},
        {"text": "legacygadget beta", "source": "legacy2.md"},
        {"text": "legacygismo gamma", "source": "legacy3.md"},
    ]
    retriever = HybridRetriever()
    retriever.store = _FakeStore(payloads)

    mine = retriever.retrieve("minewidget", scope=RagScope(user_id="u1")).items
    assert [item["doc"] for item in mine] == ["mine1.md"]

    legacy = retriever.retrieve("legacywidget", scope=RagScope()).items
    assert [item["doc"] for item in legacy] == ["legacy1.md"]

    # 缓存按作用域隔离：u2 不会串到 u1 的语料
    other = retriever.retrieve("otherwidget", scope=RagScope(user_id="u2")).items
    assert [item["doc"] for item in other] == ["other1.md"]


# ---------------------------------------------------------------- 摄入打标


def test_ingest_payload_carries_scope_fields(monkeypatch, tmp_path):
    ingester = DocumentIngester()
    path = tmp_path / "doc.md"
    path.write_text("段落一\n\n段落二", encoding="utf-8")
    monkeypatch.setattr(ingester, "embed", lambda texts: [[0.0] * 4 for _ in texts])
    captured: dict = {}
    monkeypatch.setattr(ingester.store, "upsert", lambda points: captured.update({"points": points}))

    assert ingester.ingest_file(str(path), "doc1", user_id="u1", tenant_id="t1") == 1
    payload = captured["points"][0].payload
    assert payload["user_id"] == "u1" and payload["tenant_id"] == "t1"
    assert payload["visibility"] == "private"

    # 匿名摄入：不写 user_id / tenant_id（保持无主块形态，向后兼容）
    assert ingester.ingest_file(str(path), "doc2") == 1
    payload = captured["points"][0].payload
    assert "user_id" not in payload and "tenant_id" not in payload
    assert payload["visibility"] == "private"


# ---------------------------------------------------------------- 执行器接线


class _ScopeProbeGraph:
    def __init__(self):
        self.seen: list[str | None] = []

    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        self.seen.append(current_scope().user_id)
        state = ResearchState(topic=topic)
        state.report = "# r"
        state.report_display = "# r"
        yield RunStep(index=1, node=None, state=state, terminal=True, stop_reason=STOP_COMPLETED)


def _wait_finished(manager: RunManager, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snap = manager.snapshot(run_id)
        if snap is not None and snap["status"] == "finished":
            return
        time.sleep(0.02)
    raise AssertionError("run did not finish in time")


def test_run_manager_sets_rag_scope_from_owner():
    probe = _ScopeProbeGraph()
    manager = RunManager(graph_factory=lambda: probe, store=FakeStore(), max_concurrent_runs=4)
    run_id = manager.start("作用域", user_id="u-scope")
    _wait_finished(manager, run_id)
    assert probe.seen == ["u-scope"]


def test_worker_sets_rag_scope_from_run_row():
    store = FakeStore()
    probe = _ScopeProbeGraph()
    row, _ = store.create_run("scoped01", "作用域", {}, user_id="u-worker", status="QUEUED")
    worker = Worker(store, FakeQueue(), graph_factory=lambda: probe,
                    worker_id="scope-worker", lease_seconds=60,
                    heartbeat_seconds=5, poll_seconds=0)
    assert worker.run_once(row["run_id"]) is True
    assert probe.seen == ["u-worker"]
