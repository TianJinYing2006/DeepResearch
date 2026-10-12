"""需求 35 R3：执行模式解析与预算对账单测（零网络）。"""
from __future__ import annotations

from fakes import FakeStore

from research_engine.state import CHECKPOINT_SCHEMA_VERSION
from web.backend.worker import resolve_execution_mode


class _StubSnapshot:
    def __init__(self, values):
        self.values = values


class _StubInnerGraph:
    def __init__(self, values=None, fail: bool = False):
        self._values = values or {}
        self._fail = fail
        self.updated: list = []

    def get_state(self, cfg):  # noqa: ARG002
        if self._fail:
            raise RuntimeError("checkpoint unreadable")
        return _StubSnapshot(self._values)

    def update_state(self, cfg, values):  # noqa: ARG002
        self.updated.append(values)
        self._values = {**self._values, **values}


class _StubGraph:
    def __init__(self, inner):
        self.graph = inner


def test_fresh_when_no_checkpoint_first_attempt():
    mode, info = resolve_execution_mode(_StubGraph(_StubInnerGraph({})), "r1", 1, FakeStore())
    assert mode == "fresh" and info == {}


def test_replayed_when_no_checkpoint_on_retry():
    mode, info = resolve_execution_mode(_StubGraph(_StubInnerGraph({})), "r1", 2, FakeStore())
    assert mode == "replayed" and info["reason"] == "no_checkpoint"


def test_replayed_when_version_incompatible():
    inner = _StubInnerGraph({"checkpoint_schema_version": 999, "token_used": 100})
    mode, info = resolve_execution_mode(_StubGraph(inner), "r1", 2, FakeStore())
    assert mode == "replayed" and info["reason"] == "version_incompatible"
    assert inner.updated == []  # 版本不兼容不注入预算


def test_resumed_with_ledger_reconciliation():
    inner = _StubInnerGraph({"checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
                             "token_used": 100})
    store = FakeStore()
    store.record_usage(run_id="r1", kind="llm", total_tokens=40)
    store.record_usage(run_id="r1", kind="llm", input_tokens=30, output_tokens=35)  # max→65
    store.record_usage(run_id="r1", kind="search", total_tokens=999)  # 非 llm 不计
    mode, info = resolve_execution_mode(_StubGraph(inner), "r1", 2, store)
    assert mode == "resumed"
    assert info == {"checkpoint_tokens": 100, "ledger_tokens": 105, "reconciled": True}
    assert inner.updated == [{"token_used": 105}]  # 账本口径注入（不低估）


def test_resumed_without_reconcile_when_ledger_not_higher():
    inner = _StubInnerGraph({"checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
                             "token_used": 100})
    mode, info = resolve_execution_mode(_StubGraph(inner), "r1", 2, FakeStore())
    assert mode == "resumed" and info["reconciled"] is False
    assert inner.updated == []


def test_stub_graph_without_inner_graph_uses_attempt():
    class _NoInner:
        pass

    assert resolve_execution_mode(_NoInner(), "r1", 1, FakeStore())[0] == "fresh"
    assert resolve_execution_mode(_NoInner(), "r1", 2, FakeStore())[0] == "replayed"


def test_unreadable_checkpoint_treated_as_missing():
    mode, info = resolve_execution_mode(
        _StubGraph(_StubInnerGraph(fail=True)), "r1", 2, FakeStore())
    assert mode == "replayed" and info["reason"] == "no_checkpoint"


def test_resumed_also_on_first_attempt_when_checkpoint_exists():
    """手动重排（attempt 未递增）时 checkpoint 兼容 ⇒ 仍按 resumed 续跑。"""
    inner = _StubInnerGraph({"checkpoint_schema_version": CHECKPOINT_SCHEMA_VERSION,
                             "token_used": 5})
    mode, _ = resolve_execution_mode(_StubGraph(inner), "r1", 1, FakeStore())
    assert mode == "resumed"
