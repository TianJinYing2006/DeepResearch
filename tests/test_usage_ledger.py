"""P1-4 用量账本单测（FakeStore / 假图，零 PostgreSQL / LLM）。

覆盖：emit no-op、LLMClient 发射、sink 计价与精度标注、RunManager/Worker/摄取
三条执行路径的 sink 注入。
"""
from __future__ import annotations

import time

from fakes import FakeQueue, FakeStore

from research_engine.llm.client import LLMClient
from research_engine.usage import UsageRecord, emit_usage, use_usage_sink
from web.backend.runner import RunManager
from web.backend.usage import make_store_sink
from web.backend.worker import Worker


def test_emit_without_sink_is_noop():
    emit_usage(UsageRecord(kind="llm", provider="x"))  # 不抛即通过


def test_llm_client_emits_usage_record():
    class _Usage:
        total_tokens = 120
        prompt_tokens = 100
        completion_tokens = 20

    class _Resp:
        usage = _Usage()
        id = "cmpl-1"

    records: list[UsageRecord] = []
    client = LLMClient(model="qwen-plus", role="critic")
    with use_usage_sink(records.append):
        client._accumulate_usage(_Resp(), None)

    assert records == [UsageRecord(
        kind="llm", provider="dashscope", model="qwen-plus", role="critic",
        input_tokens=100, output_tokens=20, total_tokens=120, request_id="cmpl-1")]


def test_make_store_sink_prices_llm_and_labels_others():
    store = FakeStore()
    sink = make_store_sink(store, run_id="r1", attempt=2)
    sink(UsageRecord(kind="llm", provider="dashscope", model="qwen-plus",
                     role="critic", total_tokens=1000))
    sink(UsageRecord(kind="search", provider="bocha", role="web"))

    rows = store.list_usage(run_id="r1")
    llm = next(row for row in rows if row["kind"] == "llm")
    assert llm["cost_source"] == "estimate"
    assert llm["cost_estimate_cny"] == 0.002  # qwen-plus output 0.002/1k × 1k
    assert llm["attempt"] == 2
    search = next(row for row in rows if row["kind"] == "search")
    assert search["cost_source"] == "per_call" and search["cost_estimate_cny"] == 0


class _UsageGraph:
    def iter_run(self, topic, user_instructions="", thread_id=None, should_cancel=None):
        from research_engine.state import ResearchState
        from research_engine.streaming import STOP_COMPLETED, RunStep
        emit_usage(UsageRecord(kind="llm", provider="dashscope", model="qwen-plus",
                               role="planner", total_tokens=100))
        state = ResearchState(topic=topic)
        yield RunStep(index=0, node=None, state=state, terminal=True,
                      stop_reason=STOP_COMPLETED)


def _wait_terminal(store: FakeStore, run_id: str, timeout: float = 5.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if store.get_run(run_id)["status"] in ("SUCCEEDED", "FAILED", "CANCELLED",
                                               "TIMED_OUT", "LOST"):
            return
        time.sleep(0.01)
    raise AssertionError("run not terminal in time")


def test_run_manager_wires_usage_sink():
    store = FakeStore()
    manager = RunManager(graph_factory=lambda: _UsageGraph(), store=store,
                         max_concurrent_runs=4)
    run_id = manager.start("t")
    _wait_terminal(store, run_id)

    rows = store.list_usage(run_id=run_id)
    assert rows and rows[0]["kind"] == "llm" and rows[0]["run_id"] == run_id
    assert rows[0]["attempt"] == 1


def test_worker_wires_usage_sink_with_attempt():
    store = FakeStore()
    store.create_run("usage-run-1", "t", {}, status="QUEUED")
    worker = Worker(store, FakeQueue(), graph_factory=lambda: _UsageGraph(),
                    worker_id="w-usage", lease_seconds=60, heartbeat_seconds=5,
                    poll_seconds=0)

    assert worker.run_once("usage-run-1") is True
    rows = store.list_usage(run_id="usage-run-1")
    assert rows and rows[0]["kind"] == "llm" and rows[0]["attempt"] == 1


def test_ingestion_worker_wires_usage_sink(monkeypatch, tmp_path):
    monkeypatch.setenv("DR_RAG_QUARANTINE_DIR", str(tmp_path))
    import web.backend.ingestion as ingestion_module

    store = FakeStore()
    path = ingestion_module.quarantine_path("f.md")
    with open(path, "wb") as handle:
        handle.write(b"# doc")
    store.create_ingestion("ing-usage-1", "u:abc", user_id="u", source="f.md",
                           sha256="abc", size_bytes=5, stored_name="f.md")

    class _EmittingIngester:
        def ingest_file(self, path, doc_id, *, user_id=None, tenant_id=None,
                        visibility="private"):
            emit_usage(UsageRecord(kind="embedding", provider="dashscope",
                                   model="text-embedding-v3", total_tokens=50))
            return 2

    summary = ingestion_module.process_ingestions_once(
        store, ingester_factory=lambda: _EmittingIngester())

    assert summary["ready"] == 1
    rows = store.list_usage()
    assert rows and rows[0]["kind"] == "embedding"
    assert rows[0]["run_id"] is None
    assert rows[0]["detail"]["doc_id"] == "u:abc"
