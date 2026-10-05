# -*- coding: utf-8 -*-
"""需求 19 / #76：validator 分批并行（判定口径不变 + 批失败隔离 + 上下文注入）单测。零 API。

覆盖：
- 分批切分与单批等价（verdict 与串行一致）
- `DR_VALIDATE_BATCH_SIZE=0` 回退单次调用
- 单批失败隔离：失败批降级留痕、成功批结果保留
- usage sink（contextvars）注入工作线程，不漏记账
"""
from __future__ import annotations

import re

from research_engine.agents import validator as validator_mod
from research_engine.agents.validator import Validator
from research_engine.state import ResearchFinding
from research_engine.usage import UsageRecord, emit_usage, use_usage_sink


class FakeLLMClient:
    """按提示词中的 finding_id/claim 返回 verdict；可按内容指定失败批次。"""

    calls: list = []
    fail_if_prompt_contains: str | None = None

    def __init__(self, model=None, role=None):
        pass

    def chat_json(self, messages, state=None, schema=None):
        prompt = messages[1]["content"]
        FakeLLMClient.calls.append(re.findall(r"finding_id: (\S+) \|", prompt))
        if (FakeLLMClient.fail_if_prompt_contains
                and FakeLLMClient.fail_if_prompt_contains in prompt):
            raise RuntimeError("模拟批次失败")
        pairs = re.findall(r"finding_id: (\S+) \| claim: (.+?) \| source:", prompt)
        return {"citations": [
            {
                "finding_id": fid,
                "claim": claim,
                "claim_echo": claim,
                "faithful": fid != "2",
                "supported": False,
                "confidence": 0.9,
                "is_meta": False,
                "note": "" if fid != "2" else "不忠实",
            }
            for fid, claim in pairs
        ]}


def _reset_fake(monkeypatch):
    monkeypatch.setattr(validator_mod, "LLMClient", FakeLLMClient)
    FakeLLMClient.calls = []
    FakeLLMClient.fail_if_prompt_contains = None


def _findings(n: int = 6):
    return [
        ResearchFinding(content=f"发现内容{i}", source=f"https://example.com/{i}",
                        source_type="web")
        for i in range(1, n + 1)
    ]


def _report(n: int = 6):
    return "".join(f"论断{i}的内容足够长用于校验。[来源: {i}]" for i in range(1, n + 1))


def test_batching_keeps_verdicts_equivalent(monkeypatch):
    """6 条引用 + 批 2 → 3 次调用；verified 结果与判定规则一致。"""
    monkeypatch.setenv("DR_VALIDATE_BATCH_SIZE", "2")
    monkeypatch.setenv("DR_VALIDATE_CONCURRENCY", "2")
    _reset_fake(monkeypatch)

    result = Validator().validate(_report(6), _findings(6))

    assert len(FakeLLMClient.calls) == 3
    assert all(len(ids) <= 2 for ids in FakeLLMClient.calls)
    assert sum(len(ids) for ids in FakeLLMClient.calls) == 6
    assert [c.verified for c in result] == [True, False, True, True, True, True]


def test_batch_size_zero_falls_back_to_single_call(monkeypatch):
    monkeypatch.setenv("DR_VALIDATE_BATCH_SIZE", "0")
    _reset_fake(monkeypatch)

    result = Validator().validate(_report(6), _findings(6))

    assert len(FakeLLMClient.calls) == 1
    assert len(FakeLLMClient.calls[0]) == 6
    assert [c.verified for c in result] == [True, False, True, True, True, True]


def test_failed_batch_is_isolated_and_logged(monkeypatch):
    """第二批（id 3/4）失败：该批降级通过并留痕；其余批正常判定。"""
    monkeypatch.setenv("DR_VALIDATE_BATCH_SIZE", "2")
    monkeypatch.setenv("DR_VALIDATE_CONCURRENCY", "2")
    _reset_fake(monkeypatch)
    FakeLLMClient.fail_if_prompt_contains = "finding_id: 3"

    v = Validator()
    result = v.validate(_report(6), _findings(6))
    by_id = {c.finding_id: c for c in result}

    assert "LLM 校验失败" in by_id["3"].note
    # 审计 P1#2：失败批不得判通过 —— 校验未完成态（仅保留存在性）
    assert by_id["3"].verified is False
    assert by_id["3"].verification_failed is True
    assert "LLM 校验失败" in by_id["4"].note
    assert by_id["4"].verified is False and by_id["4"].verification_failed is True
    assert by_id["1"].note == "" and by_id["1"].verified is True
    assert by_id["2"].verified is False  # 第一批正常判定（忠实度生效）

    degradations = v.drain_degradations()
    assert len(degradations) == 1
    assert degradations[0].fallback_action == "existence_only"
    assert degradations[0].node == "validator"


def test_usage_sink_propagates_into_batch_threads(monkeypatch):
    """contextvars（usage sink）必须注入工作线程：3 批 → 3 条记账，不得漏账。"""
    monkeypatch.setenv("DR_VALIDATE_BATCH_SIZE", "2")
    monkeypatch.setenv("DR_VALIDATE_CONCURRENCY", "2")
    _reset_fake(monkeypatch)

    records: list = []

    class SinkFakeClient(FakeLLMClient):
        def chat_json(self, messages, state=None, schema=None):
            emit_usage(UsageRecord(kind="llm", provider="dashscope", model="m",
                                   role="validator", total_tokens=1))
            return super().chat_json(messages, state=state, schema=schema)

    monkeypatch.setattr(validator_mod, "LLMClient", SinkFakeClient)
    SinkFakeClient.calls = []

    with use_usage_sink(records.append):
        Validator().validate(_report(6), _findings(6))

    assert len(records) == 3
    assert all(r.role == "validator" for r in records)
