# -*- coding: utf-8 -*-
"""审计 F15 回归：独立引用裁判 / coverage 完整性 / 回答完整性 / 质量闸新阈值。

覆盖（`local-artifacts/agent-review-2026-10-05.md` F15）：

- coverage 按序号对齐：乱序回填仍正确；重复不重复计数；缺项/非法项 ⇒ judge_failed；
- 独立裁判严格口径：no_verdict 不计通过、llm_failed 显式失败、existence 与 fidelity 分离；
- answer_integrity：无引用事实占比 = 1 - citation_coverage；缺字段如实 None；
- 质量闸：新增 `citation_judge_fidelity_min` / `uncited_fact_ratio_max`（只告警）；
- 聚合：新指标进入 `METRIC_SOURCES`（离线回填与当期 run 共用同一份映射）。
"""
from __future__ import annotations

from typing import Any, Dict, List

from research_engine.eval import aggregate as A
from research_engine.eval import metrics as M
from research_engine.eval import quality as Q
from research_engine.eval.citation_judge import judge_citations


class FakeJudge:
    """最小裁判桩：按顺序吐出预设 JSON；空回复即报错（防意外 LLM 调用）。"""

    def __init__(self, replies: List[Any]):
        self.replies = list(replies)
        self.calls: List[Dict[str, Any]] = []

    def chat_json(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        if not self.replies:
            raise AssertionError("unexpected judge call")
        r = self.replies.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _finding(content: str = "发现内容", source: str = "https://e.com/a", i: int = 1) -> Dict[str, Any]:
    return {
        "content": content, "source": source, "source_type": "web",
        "evidence_id": f"ev_{i:016d}", "content_hash": f"h{i}", "retrieved_at": 1.0,
    }


def _citation(finding_id: str = "1", existence: bool = True,
              verified: bool = False) -> Dict[str, Any]:
    return {
        "claim": "论断", "source": "https://e.com/a", "finding_id": finding_id,
        "existence": existence, "verified": verified,
    }


def _state(**overrides) -> Dict[str, Any]:
    base = {
        "status": "done", "error": None,
        "report": "# 标题\n\n## 一、章节\n\n" + "内容" * 200,
        "depth": 3, "token_used": 1000, "replan_count": 0,
        "frontier": [{"sq_id": "s1", "query": "q"}],
        "critic_signal": "stop",
        "reflection_log": [{"decision": "stop"}],
        "findings": [], "citations": [],
    }
    base.update(overrides)
    return base


# ---------- coverage：按序号对齐 + 唯一 / 齐备 / 类型 ----------

def test_coverage_aligned_by_index_even_out_of_order():
    judge = FakeJudge([{"results": [
        {"index": 3, "covered": True, "reason": "r3"},
        {"index": 1, "covered": False, "reason": "r1"},
        {"index": 2, "covered": True, "reason": "r2"},
    ]}])
    res = M.compute_coverage(["a", "b", "c"], [_finding()], judge)
    assert res["judge_failed"] is False
    assert res["covered_count"] == 2
    assert res["coverage"] == round(2 / 3, 4)
    assert [p["covered"] for p in res["per_sub"]] == [False, True, True]
    assert res["per_sub"][0]["reason"] == "r1"


def test_coverage_duplicate_index_first_wins_not_double_counted():
    judge = FakeJudge([{"results": [
        {"index": 1, "covered": True, "reason": "first"},
        {"index": 1, "covered": False, "reason": "dup"},
        {"index": 2, "covered": True, "reason": "ok"},
    ]}])
    res = M.compute_coverage(["a", "b"], [_finding()], judge)
    assert res["duplicate_count"] == 1
    assert res["judge_failed"] is False  # 齐备且类型合法 ⇒ 口径可信（重复只计数不重复计分）
    assert res["covered_count"] == 2
    assert res["per_sub"][0]["reason"] == "first"


def test_coverage_missing_index_marks_judge_failed():
    judge = FakeJudge([{"results": [
        {"index": 1, "covered": True, "reason": ""},
        {"index": 2, "covered": True, "reason": ""},
    ]}])
    res = M.compute_coverage(["a", "b", "c"], [_finding()], judge)
    assert res["judge_failed"] is True
    assert res["missing_indices"] == [3]
    assert res["coverage"] == round(2 / 3, 4)
    assert res["per_sub"][2]["covered"] is False


def test_coverage_invalid_type_and_range_marked():
    judge = FakeJudge([{"results": [
        {"index": 1, "covered": "yes", "reason": "类型错"},
        {"index": 2, "covered": True, "reason": ""},
        {"index": 99, "covered": True, "reason": "越界"},
        "not-a-dict",
    ]}])
    res = M.compute_coverage(["a", "b"], [_finding()], judge)
    assert res["invalid_count"] == 3
    assert res["judge_failed"] is True
    assert res["missing_indices"] == [1]
    assert res["coverage"] == 0.5


def test_coverage_no_findings_skips_judge():
    judge = FakeJudge([])  # 若被调用即 AssertionError
    res = M.compute_coverage(["a"], [], judge)
    assert res["coverage"] == 0.0
    assert res["judge_failed"] is False
    assert judge.calls == []


# ---------- 独立裁判：严格口径 ----------

def test_citation_judge_rates_and_independence():
    judge = FakeJudge([{"citations": [
        {"idx": 0, "faithful": True, "supported": False, "confidence": 0.9, "note": ""},
        {"idx": 1, "faithful": False, "supported": False, "confidence": 0.8, "note": "夸大"},
    ]}])
    res = judge_citations([_citation("1"), _citation("1")], [_finding()], [_finding()], judge)
    assert res["total"] == 2 and res["existence"] == 2
    assert res["passed"] == 1 and res["no_verdict"] == 0
    assert res["existence_rate"] == 1.0
    assert res["fidelity_rate"] == 0.5
    assert res["end_to_end_pass_rate"] == 0.5
    assert res["llm_failed"] is False and res["independent"] is True


def test_citation_judge_no_verdict_not_counted_as_pass():
    # 第一次与重试都只回 idx 0 ⇒ idx 1 无裁决：严格口径不计通过
    judge = FakeJudge([
        {"citations": [{"idx": 0, "faithful": True, "note": ""}]},
        {"citations": [{"idx": 0, "faithful": True, "note": ""}]},
    ])
    res = judge_citations([_citation("1"), _citation("1")], [_finding()], [_finding()], judge)
    assert res["passed"] == 1
    assert res["no_verdict"] == 1
    assert res["no_verdict_rate"] == 0.5
    assert res["fidelity_rate"] == 0.5


def test_citation_judge_llm_failed_is_explicit():
    # A1（2026-10-07）：异常先重试 1 次（共 2 次尝试）；持续失败才显式 llm_failed
    judge = FakeJudge([RuntimeError("judge down"), RuntimeError("judge down")])
    res = judge_citations([_citation("1")], [_finding()], [_finding()], judge)
    assert res["llm_failed"] is True
    assert res["passed"] is None
    assert res["fidelity_rate"] is None
    assert "judge down" in res["error"]
    assert len(judge.calls) == 2


def test_citation_judge_retries_after_timeout_then_succeeds():
    """A1：首次超时重试成功 → llm_failed=False；默认 timeout=120s（不再是 60s）。"""
    judge = FakeJudge([
        TimeoutError("Request timed out."),
        {"citations": [{"idx": 0, "faithful": True, "note": ""}]},
    ])
    res = judge_citations([_citation("1")], [_finding()], [_finding()], judge)
    assert res["llm_failed"] is False
    assert res["fidelity_rate"] == 1.0
    assert len(judge.calls) == 2
    assert judge.calls[0]["kwargs"]["timeout"] == 120.0


def test_citation_judge_empty_citations_no_call():
    judge = FakeJudge([])
    res = judge_citations([], [_finding()], [_finding()], judge)
    assert res["total"] == 0 and res["llm_failed"] is False
    assert res["independent"] is True
    assert judge.calls == []


def test_citation_judge_existence_false_excluded_from_fidelity():
    judge = FakeJudge([{"citations": [{"idx": 0, "faithful": True, "note": ""}]}])
    res = judge_citations(
        [_citation("1", existence=True), _citation("1", existence=False)],
        [_finding()], [_finding()], judge)
    assert res["total"] == 2 and res["existence"] == 1
    assert res["fidelity_rate"] == 1.0          # 存在性口径内全过
    assert res["end_to_end_pass_rate"] == 0.5   # 端到端（含存在性失败）


# ---------- answer_integrity ----------

def test_answer_integrity_from_validator_stats():
    res = M.compute_answer_integrity({"validator_stats": {
        "fact_sentence_count": 8, "uncited_fact_sentence_count": 6,
        "citation_coverage": 0.25, "evidence_truncated_count": 1,
        "evidence_missing_count": 0,
    }})
    assert res["uncited_fact_ratio"] == 0.75
    assert res["evidence_truncated_count"] == 1
    assert res["evidence_missing_count"] == 0


def test_answer_integrity_absent_fields_stay_none():
    res = M.compute_answer_integrity({})
    assert res["uncited_fact_ratio"] is None
    assert res["citation_coverage"] is None


# ---------- compute_all 接线 ----------

def test_compute_all_wires_judge_and_integrity_with_delta():
    judge = FakeJudge([
        {"results": [{"index": 1, "covered": True, "reason": ""}]},        # coverage
        {"citations": [{"idx": 0, "faithful": False, "note": "不忠实"}]},  # citation_judge
    ])
    state = _state(
        findings=[_finding()],
        citations=[_citation("1", existence=True, verified=True)],
        validator_stats={"citation_coverage": 0.5, "fact_sentence_count": 4,
                         "uncited_fact_sentence_count": 2},
    )
    res = M.compute_all(
        state, {"expected_subquestions": ["子问题"], "gold_keywords": []}, judge=judge)
    assert res["citation"]["verified"] == 1
    cj = res["citation_judge"]
    assert cj["independent"] is True and cj["end_to_end_pass_rate"] == 0.0
    assert cj["end_to_end_delta_vs_main_link"] == -1.0  # 主链路 1/1 vs 独立裁判 0/1
    assert res["answer_integrity"]["uncited_fact_ratio"] == 0.5


# ---------- 质量闸新阈值（只告警）----------

def test_quality_gate_new_thresholds_trigger_suspicious():
    verdict, reasons = Q.evaluate_run_verdict(
        {"citation_judge_fidelity": 0.4, "uncited_fact_ratio": 0.6},
        {"metrics_failed": 0}, 5)
    assert verdict == Q.VERDICT_SUSPICIOUS
    assert any("citation_judge_fidelity" in r for r in reasons)
    assert any("uncited_fact_ratio" in r for r in reasons)


def test_quality_gate_ignores_missing_new_metrics():
    verdict, reasons = Q.evaluate_run_verdict(
        {"completion_rate": 0.9, "citation_accuracy": 0.9, "avg_steps": 3.0},
        {"metrics_failed": 0}, 5)
    assert verdict == Q.VERDICT_OK and reasons == []


# ---------- 聚合映射（离线回填与当期共用）----------

def test_aggregate_collects_new_metrics():
    results = [{"metrics": {
        "citation_judge": {"fidelity_rate": 0.5, "end_to_end_pass_rate": 0.25},
        "answer_integrity": {"uncited_fact_ratio": 0.1},
    }}]
    mean, stderr = A.compute_metrics(results)
    assert mean["citation_judge_fidelity"] == 0.5
    assert mean["citation_judge_end_to_end"] == 0.25
    assert mean["uncited_fact_ratio"] == 0.1
    assert stderr["citation_judge_fidelity"]["n"] == 1


def test_aggregate_skips_none_rates_from_llm_failed_runs():
    results = [{"metrics": {
        "citation_judge": {"fidelity_rate": None, "end_to_end_pass_rate": None},
    }}]
    mean, stderr = A.compute_metrics(results)
    # n=0 是有效题数口径；均值回落 summarize_metric([]) 的既有语义（0.0），
    # 消费方必须先看 n 再看均值（stderr 并列输出就是为此）。
    assert stderr["citation_judge_fidelity"]["n"] == 0
    assert mean["citation_judge_fidelity"] == 0.0
