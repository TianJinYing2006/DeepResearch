# -*- coding: utf-8 -*-
"""2026-11 审计 #340a 低危正确性修复回归（行为级，零 LLM / 零网络）。

覆盖：
- D-07 `HybridRetriever._effective_revision` 不再调用不存在的 `VectorStore.get_rag_revision`
  （旧实现恒 AttributeError 被吞成死代码）；
- D-08 可信声明降级警告改为检测结构化 `verification_failed`（旧字符串标记是死代码）；
- D-09 LLM 客户端在 `_get_client` 失败时结算预算预占（`token_used` 不残留多计）；
- D-17 流式生成器被提前 close 时 finally 落库（`last_stop_reason`/`tokens_diff` 不陈旧）；
- D-15 `_is_assertive` 文档与实现对齐（行为不变）。
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from research_engine.agents.validator import Validator
from research_engine.graph import DeepResearchGraph
from research_engine.llm.client import LLMClient
from research_engine.rag.retriever import HybridRetriever
from research_engine.rag.scope import RagScope
from research_engine.render import ReportRenderer
from research_engine.state import Citation, ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED

# ---- D-07：_effective_revision 不再依赖不存在的 VectorStore 方法 ----


def _retriever_with_store(store) -> HybridRetriever:  # noqa: ANN001
    retriever = HybridRetriever()  # 不连接 Qdrant（懒加载）
    retriever.store = store
    return retriever


def test_effective_revision_without_store_method_returns_zero():
    """VectorStore 的真实形态没有 get_rag_revision ⇒ 返回 0 且不走异常路径。"""
    retriever = _retriever_with_store(SimpleNamespace())
    assert retriever._effective_revision(RagScope()) == 0


def test_effective_revision_reads_store_when_available():
    retriever = _retriever_with_store(SimpleNamespace(get_rag_revision=lambda: 5))
    assert retriever._effective_revision(RagScope()) == 5
    # 显式 revision 优先（runner/worker 注入的任务库修订号）
    assert retriever._effective_revision(RagScope(revision=7)) == 7


def test_effective_revision_swallows_read_errors():
    def _boom() -> int:
        raise RuntimeError("db down")

    retriever = _retriever_with_store(SimpleNamespace(get_rag_revision=_boom))
    assert retriever._effective_revision(RagScope()) == 0


# ---- D-08：可信声明的降级警告改检测 verification_failed ----


def test_trust_statement_warns_when_verification_incomplete():
    citations = [
        Citation(claim="甲", source="s1", existence=True, verified=True),
        Citation(claim="乙", source="s2", existence=True, verified=False,
                 verification_failed=True),
    ]
    stmt = ReportRenderer().build_trust_statement(citations)
    assert "未完成条目" in stmt, "校验未完成（UNKNOWN）必须显式告警，不能静默按已校验口径呈现"


def test_trust_statement_no_warning_for_normal_citations():
    citations = [Citation(claim="甲", source="s1", existence=True, verified=True)]
    stmt = ReportRenderer().build_trust_statement(citations)
    assert "未完成条目" not in stmt


# ---- D-09：_get_client 失败时必须结算预占 ----


def test_llm_client_releases_reservation_when_sdk_client_unavailable(monkeypatch):
    from config import config

    monkeypatch.setattr(config.llm, "api_key", "")
    state = SimpleNamespace(token_used=0)
    client = LLMClient(model="qwen-plus", role="test")

    with pytest.raises(RuntimeError):
        client.chat([{"role": "user", "content": "hi"}], state=state)

    assert state.token_used == 0, "SDK 客户端初始化失败不得把预占 token 永久计入"


# ---- D-17：生成器提前 close 的 finally 落库 ----


class _FakeGraph:
    def __init__(self, chunks):  # noqa: ANN001
        self._chunks = chunks

    def stream(self, *args, **kwargs):  # noqa: ANN002, ANN003
        yield from self._chunks


def _graph_with_fake_stream(chunks) -> DeepResearchGraph:  # noqa: ANN001
    obj = DeepResearchGraph.__new__(DeepResearchGraph)  # 绕过真实图构建（零 LLM）
    obj.graph = _FakeGraph(chunks)
    obj.last_exception = None
    obj.last_stop_reason = STOP_COMPLETED
    obj.tokens_diff = None
    return obj


def _plan_chunk() -> dict:
    return {"topic": "t", "progress": [{"stage": "plan", "msg": "planned"}]}


def test_iter_steps_early_close_records_cancelled_and_tokens_diff():
    token_base = LLMClient.tokens_total
    obj = _graph_with_fake_stream([_plan_chunk(), _plan_chunk()])
    gen = obj._iter_steps(None, {}, None, token_base,
                          fallback_state=ResearchState(topic="t"))
    step = next(gen)
    assert step.node == "plan" and step.terminal is False

    gen.close()  # 模拟消费方提前停止（GeneratorExit）
    assert obj.last_stop_reason == STOP_CANCELLED, "提前 close 不得停留在旧 stop_reason"
    assert obj.tokens_diff == 0, "提前 close 也必须落 tokens_diff 对账"


def test_iter_steps_normal_completion_emits_terminal_and_records_diff():
    token_base = LLMClient.tokens_total
    obj = _graph_with_fake_stream([_plan_chunk()])
    steps = list(obj._iter_steps(None, {}, None, token_base,
                                 fallback_state=ResearchState(topic="t")))
    assert steps[-1].terminal is True
    assert steps[-1].stop_reason == STOP_COMPLETED
    assert obj.last_stop_reason == STOP_COMPLETED
    assert obj.tokens_diff == 0


# ---- D-15：文档与实现对齐（行为不变）----


def test_is_assertive_doc_matches_implementation():
    doc = Validator._is_assertive.__doc__ or ""
    assert "- 长度 >=" not in doc, "文档不得把实现没有的长度过滤写成规则"
    # 行为不变：短 claim 照旧进入校验；表格/标题仍被过滤
    assert Validator._is_assertive("短句") is True
    assert Validator._is_assertive("| 表格 |") is False
    assert Validator._is_assertive("# 标题") is False
