"""W8 Arm 1：运行状态三态 + 异常退出契约 + 失败原因枚举单向派生。

覆盖 `docs/requirements/8-fault-transparency-and-reproducibility.md`：
- §5.1.1 三态 `success`/`degraded`/`failed`（**无 `partial`**）
- §5.1.2 `degradation_log` 带 `operator.add` reducer；`resolve_run_status` 判定规则
- §5.1.2 失败原因枚举「一张表、两个产生点」+ 单向派生契约（Q8 B4）
- §5.1.4 异常退出契约：run() 捕获异常 ⇒ `failed` 可达；`get_state` 为空时构造最小 state，**绝不返回 None**
- §5.1.4 实现影响面：结构化 `error`（dict）不得被切片

全部零 LLM / 零 API。
"""
from __future__ import annotations

import operator
from typing import Annotated, get_args, get_origin
from unittest.mock import MagicMock

import pytest
from pydantic import BaseModel

from research_engine.eval import report_gen
from research_engine.failure_reasons import (
    ALL_REASONS,
    NON_TOOL_REASONS,
    TOOL_REASONS,
    FailureReason,
    classify_exception,
    is_tool_reason,
    is_valid_reason,
)
from research_engine.state import (
    RUN_STATUS_DEGRADED,
    RUN_STATUS_FAILED,
    RUN_STATUS_SUCCESS,
    RUN_STATUSES,
    DegradationEntry,
    ResearchState,
)

# ---------------------------------------------------------------- §5.1.1 三态


def test_run_status_is_three_state_without_partial():
    """三态：success/degraded/failed，且**没有 partial**（Q4 B2 拍板）。"""
    assert set(RUN_STATUSES) == {"success", "degraded", "failed"}
    assert "partial" not in RUN_STATUSES
    assert (RUN_STATUS_SUCCESS, RUN_STATUS_DEGRADED, RUN_STATUS_FAILED) == (
        "success",
        "degraded",
        "failed",
    )


def test_default_run_status_is_success():
    state = ResearchState(topic="t")
    assert state.run_status == RUN_STATUS_SUCCESS
    assert state.error is None
    assert state.degradation_log == []


# ---------------------------------------------------------------- §5.1.2 判定规则


def test_resolve_run_status_rules():
    """tracker 空 → success；非空 + 有报告 → degraded；非空 + 无报告 → failed。"""
    s = ResearchState(topic="t")
    assert s.resolve_run_status(has_report=True) == RUN_STATUS_SUCCESS

    s.add_degradation(node="researcher", component="web_search", reason=FailureReason.TIMEOUT.value)
    assert s.resolve_run_status(has_report=True) == RUN_STATUS_DEGRADED
    assert s.resolve_run_status(has_report=False) == RUN_STATUS_FAILED


def test_degradation_log_has_add_reducer():
    """必须带 operator.add reducer —— 否则 asyncio.gather 并发下会丢记录（Q4 优化点 ④）。

    Pydantic v2 会把 `Annotated[T, reducer]` 拆开：类型进 `.annotation`，
    元数据进 `.metadata`。对照 `progress` 字段（同文件既有写法）确保口径一致。
    """
    f = ResearchState.model_fields["degradation_log"]
    assert operator.add in f.metadata, "degradation_log 缺少 operator.add reducer"
    # 类型本体仍是 List[DegradationEntry]
    assert get_origin(f.annotation) is list
    assert DegradationEntry in get_args(f.annotation)

    # 与既有 progress 字段写法一致（照抄对象）
    p = ResearchState.model_fields["progress"]
    assert operator.add in p.metadata
    assert Annotated is not None  # 保持对 Annotated 的显式引用（文档口径）


def test_add_degradation_appends_and_keeps_order():
    s = ResearchState(topic="t")
    a = s.add_degradation("planner", "llm", FailureReason.LLM_ERROR.value, detail="boom")
    b = s.add_degradation("writer", "llm", FailureReason.PARSE_ERROR.value)
    assert [e.node for e in s.degradation_log] == ["planner", "writer"]
    assert a.reason == "llm_error" and b.reason == "parse_error"
    # 追加语义：不是覆盖
    assert len(s.degradation_log) == 2


def test_add_reducer_concatenates_two_lists():
    """直接验证 reducer 的并发安全语义（LangGraph 对无 reducer 字段取覆盖）。"""
    a = [DegradationEntry(node="a", component="llm", reason="llm_error")]
    b = [DegradationEntry(node="b", component="llm", reason="internal")]
    assert len(operator.add(a, b)) == 2


# ---------------------------------------------------------------- 结构化 error


def test_structured_error_shape_and_success_semantics():
    """error = {code, message, node}；且**成功时为 None**（metrics.py:45 完成率依赖此语义）。"""
    s = ResearchState(topic="t")
    assert s.error is None

    err = s.set_error(code=FailureReason.INTERNAL.value, message="炸了", node="run")
    assert set(err) == {"code", "message", "node"}
    assert err["code"] == "internal" and err["node"] == "run"
    # set_error 同步置 failed
    assert s.run_status == RUN_STATUS_FAILED


def test_structured_error_is_not_a_str():
    """防回归：`error` 由 Optional[str] 改为 Dict 后，任何 `error[:200]` 都会崩。"""
    s = ResearchState(topic="t")
    s.set_error(code="internal", message="x")
    assert isinstance(s.error, dict)
    # dict 切片抛 KeyError（不是 TypeError）—— 无论如何都会炸，故报告层必须 str() 兜住
    with pytest.raises((TypeError, KeyError)):
        _ = s.error[:200]  # type: ignore[index]


def test_report_gen_does_not_slice_error():
    """§5.1.4 实现影响面第 4 条：report_gen 对 dict error 必须走 str() 再截断。"""
    src = open(report_gen.__file__, encoding="utf-8").read()
    assert "r.get('error')[:200]" not in src, "report_gen 仍在直接切片 error（dict 会 TypeError）"
    assert "str(r.get('error'))[:200]" in src


# ---------------------------------------------------------------- 失败原因枚举 / 单向派生


def test_reason_table_is_partitioned():
    """一张表、两个产生点、按来源分组；两组不重叠且并集为全表。"""
    assert TOOL_REASONS | NON_TOOL_REASONS == ALL_REASONS
    assert not (TOOL_REASONS & NON_TOOL_REASONS)
    assert len(ALL_REASONS) == 9  # 工具层 5 + 非工具层 4
    assert len(TOOL_REASONS) == 5
    assert len(NON_TOOL_REASONS) == 4


def test_tool_vs_non_tool_classification():
    for r in TOOL_REASONS:
        assert is_tool_reason(r)
    for r in NON_TOOL_REASONS:
        assert not is_tool_reason(r)
    assert is_valid_reason("timeout")
    assert not is_valid_reason("手写的原因")


def test_classify_exception_maps_to_non_tool_reasons_only():
    """run() 异常路径只产生非工具类 4 值 —— 工具异常应在工具层就地转成 failure_reason。"""
    assert classify_exception(RuntimeError("boom")) == "internal"
    assert classify_exception(ValueError("context length exceeded")) == "token_limit"

    rec = type("GraphRecursionError", (Exception,), {})("loop")
    assert classify_exception(rec) == "recursion_limit"

    for got in (
        classify_exception(RuntimeError("x")),
        classify_exception(ValueError("context length")),
        classify_exception(rec),
    ):
        assert got in NON_TOOL_REASONS


def test_degradation_reason_derived_from_failure_reason():
    """单向派生契约：DegradationEntry.reason 取 SearchResponse.failure_reason，不重写字面量。"""
    from research_engine.search.base import SearchResponse

    resp = SearchResponse(query="q", results=[], failure_reason=FailureReason.TIMEOUT.value)
    assert resp.ok is False

    # 派生（唯一正确写法）：直接引用，不写第二个 "timeout"
    entry = DegradationEntry(
        node="researcher",
        component="web_search",
        reason=resp.failure_reason,
        detail=resp.failure_detail,
    )
    assert entry.reason == resp.failure_reason == "timeout"
    assert entry.reason in TOOL_REASONS

    ok = SearchResponse(query="q", results=[])
    assert ok.ok is True and ok.failure_reason is None


# ---------------------------------------------------------------- §5.1.4 异常退出契约


class _FakeGraph:
    """最小替身：invoke 抛异常，get_state 可配置返回空 dict（模拟未 invoke 过的 thread）。"""

    def __init__(self, values=None, get_state_raises: bool = False):
        self._values = values
        self._raises = get_state_raises

    def invoke(self, initial, cfg):  # noqa: ARG002
        raise RuntimeError("节点炸了")

    def get_state(self, cfg):  # noqa: ARG002
        if self._raises:
            raise RuntimeError("get_state 也炸了")
        snap = MagicMock()
        snap.values = self._values
        return snap


def _make_graph_with(fake: _FakeGraph):
    from research_engine.graph import DeepResearchGraph

    g = DeepResearchGraph.__new__(DeepResearchGraph)  # 跳过 _build（不触碰真实图）
    g.graph = fake
    g.trace_id = None
    g.tokens_diff = None
    g.last_exception = None
    return g


def _patch_no_observability(monkeypatch):
    """把 W3 观测与 token 对账打桩，保证零 LLM / 零网络。"""
    import research_engine.graph as G

    monkeypatch.setattr(G, "get_langfuse", lambda: None)
    monkeypatch.setattr(G.LLMClient, "tokens_total", 0, raising=False)


def test_failed_is_reachable_via_exception_contract(monkeypatch):
    """Q4 B1：图结构下 `failed` 的唯一落点 —— run() 捕获异常并转结构化状态。"""
    _patch_no_observability(monkeypatch)
    g = _make_graph_with(_FakeGraph(values=None))

    out = g.run("题目")

    assert out is not None, "run() 绝不返回 None"
    assert isinstance(out, BaseModel)
    assert out.run_status == RUN_STATUS_FAILED
    assert out.status == "failed"
    assert out.error is not None and out.error["code"] == "internal"
    assert g.last_exception is not None
    assert len(out.degradation_log) == 1
    assert out.degradation_log[0].reason == "internal"


def test_recover_handles_empty_get_state_values(monkeypatch):
    """⚠️ 坑：get_state 对未 invoke 过的 thread 返回**空 dict 而非 None** ⇒ 必须 `values or {}`。"""
    _patch_no_observability(monkeypatch)
    # 显式传空 dict，正是 LangGraph 的真实行为
    g = _make_graph_with(_FakeGraph(values={}))

    out = g.run("题目")
    assert out is not None
    assert out.run_status == RUN_STATUS_FAILED
    assert out.topic == "题目", "兜底应保留原始 topic"


def test_recover_survives_get_state_raising(monkeypatch):
    """get_state 自身抛错时也不得二次崩溃（否则「转结构化状态」变「换个地方崩」）。"""
    _patch_no_observability(monkeypatch)
    g = _make_graph_with(_FakeGraph(get_state_raises=True))

    out = g.run("题目")
    assert out is not None
    assert out.run_status == RUN_STATUS_FAILED


def test_recover_preserves_checkpoint_state(monkeypatch):
    """能捞到 checkpoint 时，已产出的 findings / report 不得丢。"""
    _patch_no_observability(monkeypatch)
    g = _make_graph_with(_FakeGraph(values={"topic": "题目", "report": "半份报告"}))

    out = g.run("题目")
    assert out.report == "半份报告"
    assert out.run_status == RUN_STATUS_FAILED  # 无完整报告 + 有降级记录
    # Q3=D'：异常路径仍会算 tokens_diff ⇒ token_used 必须有默认值，否则在此处二次抛错
    assert isinstance(out.token_used, int)
    assert g.tokens_diff is not None


def test_recursion_error_maps_to_recursion_limit(monkeypatch):
    _patch_no_observability(monkeypatch)
    import research_engine.graph as G

    class GraphRecursionError(Exception):
        pass

    fake = _FakeGraph(values={})
    fake.invoke = lambda initial, cfg: (_ for _ in ()).throw(GraphRecursionError("too many"))  # noqa: ARG005
    g = _make_graph_with(fake)

    out = g.run("题目")
    assert out.error["code"] == "recursion_limit"
    assert G.classify_exception(GraphRecursionError("x")) == "recursion_limit"
