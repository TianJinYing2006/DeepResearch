"""P2-7 Worker 压测图工厂选择测试（默认真图 / 假图开关 / 非法参数回落）。"""
from __future__ import annotations

from web.backend import worker as worker_mod
from web.backend.demo_graph import DemoGraph
from web.backend.worker import make_graph_factory


def test_default_factory_is_real_graph(monkeypatch):
    monkeypatch.delenv("DR_LOADTEST_GRAPH", raising=False)
    assert make_graph_factory() is worker_mod.create_graph


def test_loadtest_factory_builds_demo_graph(monkeypatch):
    monkeypatch.setenv("DR_LOADTEST_GRAPH", "1")
    monkeypatch.setenv("DR_LOADTEST_STEP_SECONDS", "0.07")
    factory = make_graph_factory()
    graph = factory()
    assert isinstance(graph, DemoGraph)
    assert graph.step_seconds == 0.07


def test_loadtest_factory_invalid_step_falls_back(monkeypatch):
    monkeypatch.setenv("DR_LOADTEST_GRAPH", "true")
    monkeypatch.setenv("DR_LOADTEST_STEP_SECONDS", "not-a-number")
    graph = make_graph_factory()()
    assert isinstance(graph, DemoGraph) and graph.step_seconds == 0.05
