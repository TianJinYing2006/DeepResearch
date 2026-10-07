# -*- coding: utf-8 -*-
"""需求 19 / #76：validator 分批并行（判定口径不变 + 批失败隔离 + 上下文注入）单测。零 API。

覆盖：
- 分批切分与单批等价（verdict 与串行一致）
- `DR_VALIDATE_BATCH_SIZE=0` 回退单次调用
- 单批失败隔离：失败批降级留痕、成功批结果保留
- usage sink（contextvars）注入工作线程，不漏记账
- A4（#154）：按批分片上下文 —— 多批只喂本批引用涉及的 finding 行；单批逐字等价；
  `DR_VALIDATE_SHARD_CONTEXT=0` 回退；未校验按「饥饿」/「判据拒绝」拆分统计
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
        if (self.fail_if_prompt_contains
                and self.fail_if_prompt_contains in prompt):
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


# ---- A4（#154）：按批分片上下文 + 未校验归因拆分 ----

class FeedCaptureClient(FakeLLMClient):
    """在 FakeLLMClient 基础上记录每批发到的「研究发现」段（分片喂料的观察点）。"""

    feeds: list = []
    feed_texts: list = []

    def chat_json(self, messages, state=None, schema=None):
        prompt = messages[1]["content"]
        body = prompt.split("待校验引用")[0]
        FeedCaptureClient.feeds.append(re.findall(r"^- \[(\d+)\]", body, flags=re.M))
        FeedCaptureClient.feed_texts.append(body)
        return super().chat_json(messages, state=state, schema=schema)


def _reset_capture(monkeypatch, **env):
    monkeypatch.setattr(validator_mod, "LLMClient", FeedCaptureClient)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    FeedCaptureClient.calls = []
    FeedCaptureClient.feeds = []
    FeedCaptureClient.feed_texts = []
    FeedCaptureClient.fail_if_prompt_contains = None


def test_build_findings_lines_matches_legacy_text():
    """行索引版本与旧的整段拼装严格一致（既有回归的锚）。"""
    findings = _findings(6)
    to_check = [{"finding_id": str(i), "source": f"https://example.com/{i}"}
                for i in range(1, 7)]

    text, trimmed = Validator._build_findings_text(findings, to_check)
    lines, lines_trimmed = Validator._build_findings_lines(findings, to_check)

    assert "\n".join(lines.values()) == text
    assert trimmed is True and lines_trimmed is True
    # 分片子集：只取前两条引用 ⇒ 只涉及编号 1、2
    ids = Validator._used_finding_ids(to_check[:2], Validator._source_to_ids(findings))
    assert ids == {"1", "2"}


def test_sharded_feed_only_includes_batch_findings(monkeypatch):
    """A4 核心：多批时每批只喂本批引用涉及的 finding 行（不再重复整段上下文）。"""
    _reset_capture(monkeypatch, DR_VALIDATE_BATCH_SIZE="2", DR_VALIDATE_CONCURRENCY="1")

    Validator().validate(_report(6), _findings(6))

    assert len(FeedCaptureClient.feeds) == 3
    assert FeedCaptureClient.feeds[0] == ["1", "2"]
    assert FeedCaptureClient.feeds[1] == ["3", "4"]
    assert FeedCaptureClient.feeds[2] == ["5", "6"]


def test_shard_context_off_keeps_full_feed(monkeypatch):
    """DR_VALIDATE_SHARD_CONTEXT=0 回到旧行为（每批仍带全量 findings）——对照/回退开关。"""
    _reset_capture(monkeypatch, DR_VALIDATE_BATCH_SIZE="2", DR_VALIDATE_CONCURRENCY="1",
                   DR_VALIDATE_SHARD_CONTEXT="0")

    Validator().validate(_report(6), _findings(6))

    assert len(FeedCaptureClient.feeds) == 3
    assert all(feed == ["1", "2", "3", "4", "5", "6"] for feed in FeedCaptureClient.feeds)


def test_single_batch_feed_is_full_and_unaffected_by_shard(monkeypatch):
    """单批场景分片开关不产生差异：喂料仍是全量（逐字等价的边界）。"""
    _reset_capture(monkeypatch, DR_VALIDATE_BATCH_SIZE="0")

    Validator().validate(_report(6), _findings(6))

    assert len(FeedCaptureClient.feeds) == 1
    assert FeedCaptureClient.feeds[0] == ["1", "2", "3", "4", "5", "6"]


def test_unverified_split_starved_vs_rejected(monkeypatch):
    """未校验必须按「饥饿」/「判据拒绝」分开统计，否则 72% 这类数字无法归因。"""
    _reset_capture(monkeypatch, DR_VALIDATE_BATCH_SIZE="2", DR_VALIDATE_CONCURRENCY="1")
    FeedCaptureClient.fail_if_prompt_contains = "finding_id: 3"  # 第二批调用失败

    v = Validator()
    v.validate(_report(6), _findings(6))
    stats = v.last_validation_stats

    # 引用 3、4 因批调用失败未校验 ⇒ 饥饿（工程缺陷）
    assert stats["unverified_starved_count"] == 2
    # 引用 2 拿到裁决且判不忠实 ⇒ 判据拒绝（质量信号）；5、6 判忠实不计入
    assert stats["verified_rejected_count"] == 1


def test_sharded_empty_batch_ids_falls_back_to_full_feed(monkeypatch):
    """A4 加固（#154）：某批反查不到任何编号时回退全量喂料（防「空证据 prompt」）。"""
    _reset_capture(monkeypatch, DR_VALIDATE_BATCH_SIZE="2", DR_VALIDATE_CONCURRENCY="1")
    # 强制「本批反查」恒为空 —— 模拟 finding_id 为空且 source 未映射的极端边界
    monkeypatch.setattr(
        Validator, "_used_finding_ids",
        classmethod(lambda cls, to_check, source_to_ids: set()),
    )

    Validator().validate(_report(6), _findings(6))

    assert len(FeedCaptureClient.feeds) == 3
    assert all(feed == ["1", "2", "3", "4", "5", "6"] for feed in FeedCaptureClient.feeds)
