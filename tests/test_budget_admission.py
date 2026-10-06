# -*- coding: utf-8 -*-
"""审计 F13 回归：token 预算准入、预留、压缩边界与调用时限。

- 预留：研究循环硬闸在 `token_budget - reserve` 处停；绝对预算仍是每次调用准入线；
- 准入：预算耗尽时 LLMClient 不发起 SDK 调用（TokenBudgetExceeded）；
- 时限：默认超时 + 任务剩余时限收窄；时限已过拒绝调用（DeadlineExceeded）；
- 压缩：字符体积触发、组数上限、预算耗尽保持原文；
- 降级：validator 跳过忠实度（existence_only）、writer 走兜底报告。

全部离线：mock LLMClient / monkeypatch router，零 API key。
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import research_engine.context.manager as manager_module
from config import config
from research_engine.agents.validator import Validator
from research_engine.budget import (
    DeadlineExceeded,
    TokenBudgetExceeded,
    research_token_ceiling,
)
from research_engine.context.manager import ContextManager
from research_engine.critic import hard_gate
from research_engine.failure_reasons import FailureReason
from research_engine.llm.client import LLMClient
from research_engine.runtime_profile import (
    reset_task_deadline,
    set_task_deadline,
    task_deadline_remaining,
)
from research_engine.state import ResearchFinding, ResearchState


def _seed() -> list:
    return [{"sq_id": "s1", "query": "q"}]


def _fake_response():
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="ok"))],
        usage=None, id="resp-1",
    )


# ---------------------------------------------------------------- 预留与硬闸

def test_research_ceiling_and_hard_gate():
    rc = config.research
    ceiling = research_token_ceiling(rc)
    assert ceiling == rc.token_budget - rc.token_budget_reserve
    assert ceiling > 0

    at_ceiling = ResearchState(topic="t", frontier=_seed(), token_used=ceiling)
    assert hard_gate(at_ceiling, config) == "stop", "研究循环应在 ceiling 处提前停止"

    below = ResearchState(topic="t", frontier=_seed(), token_used=ceiling - 1)
    assert hard_gate(below, config) is None


# ---------------------------------------------------------------- 调用准入

def test_llm_admission_blocks_sdk_call_when_budget_exhausted(monkeypatch):
    client = LLMClient(model="m", role="r")
    monkeypatch.setattr(client, "_get_client", lambda: pytest.fail("SDK 不应被调用"))
    state = SimpleNamespace(token_used=config.research.token_budget)
    with pytest.raises(TokenBudgetExceeded):
        client.chat([{"role": "user", "content": "hi"}], state=state)


def test_llm_default_timeout_and_deadline_narrowing(monkeypatch):
    client = LLMClient(model="m", role="r")
    fake = MagicMock()
    fake.chat.completions.create.return_value = _fake_response()
    monkeypatch.setattr(client, "_get_client", lambda: fake)

    client.chat([{"role": "user", "content": "hi"}])
    assert fake.chat.completions.create.call_args.kwargs["timeout"] == pytest.approx(
        config.llm.request_timeout_seconds)

    token = set_task_deadline(3)
    try:
        client.chat([{"role": "user", "content": "hi"}])
    finally:
        reset_task_deadline(token)
    narrowed = fake.chat.completions.create.call_args.kwargs["timeout"]
    assert 0 < narrowed <= 3, "任务时限更紧时必须收窄调用超时"


def test_llm_explicit_timeout_wins(monkeypatch):
    client = LLMClient(model="m", role="r")
    fake = MagicMock()
    fake.chat.completions.create.return_value = _fake_response()
    monkeypatch.setattr(client, "_get_client", lambda: fake)
    token = set_task_deadline(2)
    try:
        client.chat([{"role": "user", "content": "hi"}], timeout=7)
    finally:
        reset_task_deadline(token)
    assert fake.chat.completions.create.call_args.kwargs["timeout"] == 7


def test_llm_deadline_expired_blocks_call(monkeypatch):
    client = LLMClient(model="m", role="r")
    monkeypatch.setattr(client, "_get_client", lambda: pytest.fail("SDK 不应被调用"))
    token = set_task_deadline(-1)
    try:
        with pytest.raises(DeadlineExceeded):
            client.chat([{"role": "user", "content": "hi"}])
    finally:
        reset_task_deadline(token)


# ---------------------------------------------------------------- 压缩边界

def _group_findings(sources: dict) -> list:
    out = []
    for source, count in sources.items():
        out += [ResearchFinding(content=f"{source} 材料 {i}", source=source,
                                source_type="web", sq_id="q1") for i in range(count)]
    return out


def test_compress_skips_llm_when_budget_exhausted(monkeypatch):
    calls: list = []

    class _Router:
        def fast_chat(self, system, user, state=None):
            calls.append(user)
            return "[摘要]"

    monkeypatch.setattr(manager_module, "get_router", lambda: _Router())
    findings = _group_findings({"s1": 31})
    state = SimpleNamespace(token_used=config.research.token_budget)
    compressed = ContextManager(max_findings=30).compress(findings, "主题", state)

    assert calls == [], "预算耗尽不得发起压缩调用"
    assert len(compressed) == 31, "预算耗尽应保持原文"


def test_compress_group_cap_bounds_calls(monkeypatch):
    calls: list = []

    class _Router:
        def fast_chat(self, system, user, state=None):
            calls.append(user)
            return "[摘要]"

    monkeypatch.setattr(manager_module, "get_router", lambda: _Router())
    monkeypatch.setattr(config.research, "compress_max_groups", 1)
    findings = _group_findings({"s1": 11, "s2": 11, "s3": 11})  # 33 条 / 3 组
    compressed = ContextManager(max_findings=30).compress(findings, "主题")

    assert len(calls) == 1, "组数上限必须约束单节点 LLM 调用数"
    assert len(compressed) == 1 + 22, "上限之外的组保持原文"


def test_compress_triggered_by_char_volume(monkeypatch):
    calls: list = []

    class _Router:
        def fast_chat(self, system, user, state=None):
            calls.append(user)
            return "[摘要]"

    monkeypatch.setattr(manager_module, "get_router", lambda: _Router())
    monkeypatch.setattr(config.research, "compress_trigger_chars", 100)
    findings = [ResearchFinding(content="x" * 30, source="s1", source_type="web",
                                sq_id="q1") for _ in range(5)]  # 5 条 <= 30 但 150 字符
    compressed = ContextManager(max_findings=30).compress(findings, "主题")

    assert len(calls) == 1, "字符体积超限也应触发压缩"
    assert len(compressed) == 1


# ---------------------------------------------------------------- 降级收口

def test_validator_skips_fidelity_when_budget_exhausted(monkeypatch):
    calls = {"n": 0}

    def _chat_json(self, messages, state=None, schema=None):
        calls["n"] += 1
        return {"citations": []}

    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json", _chat_json)
    validator = Validator()
    findings = [ResearchFinding(content="证据", source="https://a", source_type="web")]
    state = SimpleNamespace(token_used=config.research.token_budget)

    citations = validator.validate("足够长的论断句可以校验 [来源: 1]", findings, state)

    assert calls["n"] == 0, "预算耗尽不得发起忠实度调用"
    assert citations and citations[0].verification_failed is True
    logs = validator.drain_degradations()
    assert logs and logs[0].reason == FailureReason.TOKEN_LIMIT.value
    assert logs[0].fallback_action == "existence_only"


def test_writer_degrades_without_llm_when_budget_exhausted():
    from research_engine.agents.writer import Writer
    from research_engine.state import SubQuestion

    writer = Writer()
    state = SimpleNamespace(token_used=config.research.token_budget)
    report = writer.write("主题", [SubQuestion(id="q1", question="问题", rationale="r")],
                          [], state)

    assert isinstance(report, str) and report, "预算耗尽必须走兜底报告"
    logs = writer.drain_degradations()
    assert logs and logs[0].fallback_action == "fallback_report"


# ---------------------------------------------------------------- 执行器时限复位

def test_worker_clears_task_deadline_after_run():
    from fakes import FakeQueue, FakeStore, TinyGraph

    from web.backend.worker import Worker

    store = FakeStore()
    store.create_run("budget-run-1", "t", {}, status="QUEUED",
                     timeout_at=datetime.now(UTC) + timedelta(seconds=60))
    worker = Worker(store, FakeQueue(), graph_factory=lambda: TinyGraph(steps=1),
                    worker_id="w-budget", lease_seconds=60, heartbeat_seconds=5,
                    poll_seconds=0)
    assert worker.run_once("budget-run-1") is True
    assert task_deadline_remaining() is None, "运行结束必须复位时限，避免污染后续 run"
