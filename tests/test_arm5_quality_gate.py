"""W8 Arm 5：run 级质量闸（§5.5.1）+ 指标离散度（§5.5.4）+ 字段重命名（§5.5.2）+ D1 成本守恒。

零 LLM（除 D1 降级通道读 raw 文件），全部离线可跑，可进 CI。
"""
from __future__ import annotations

import json

import pytest

from research_engine.eval.quality import (
    DEFAULT_THRESHOLDS,
    DEPRECATED_COUNT_KEYS,
    QualityThresholds,
    evaluate_run_verdict,
)
from research_engine.eval.run import _parse_thresholds, _summarize
from research_engine.eval.stats import (
    mean_stderr,
    summarize_metric,
)

# ---------------------------------------------------------------- 测试替身


def _row(completion=1.0, citation=0.8, steps=3.0, coverage=0.5, retrieval=0.6,
         existence=0.9, relaxed=0.85, insufficient=0.1, stop="critic_stop", status="ok"):
    return {
        # W8 命名三分：层③ 键名 = `metrics_status`（旧裸 `status` 的 dual-read 回落
        # 由 tests/test_status_naming.py 单独覆盖，此处一律走新键）
        "metrics_status": status,
        "metrics": {
            "completion": {"complete": completion},
            "citation": {"fidelity_rate": citation, "relaxed_rate": relaxed, "existence_rate": existence},
            "coverage": {"coverage": coverage},
            "retrieval_hit": {"retrieval_hit_rate": retrieval},
            "steps": {"steps": steps},
            "reflection": {"stop_type": stop},
            "insufficient": {"marker_ratio": insufficient},
        },
    }


def _make_run_dir(tmp_path, *, raw_rows=(), with_stats=True):
    """搭一个最小 run 目录：raw/ + 可选 phase1_global_stats.json。"""
    d = tmp_path / "run_test"
    (d / "raw").mkdir(parents=True)
    for i, r in enumerate(raw_rows):
        (d / "raw" / f"q{i}.raw.json").write_text(json.dumps({
            "q_id": f"q{i}",
            "token_used": r,
            "config_snapshot": {"experiment": {}},
            "git_commit": "abc123",
            "git_dirty": False,
            "git_diff_hash": "deadbeef",
        }, ensure_ascii=False), encoding="utf-8")
    if with_stats:
        (d / "phase1_global_stats.json").write_text(json.dumps({
            "model_stats": {"qwen-plus": 1000},
            "model_io_stats": {"qwen-plus": {"input": 600, "output": 400}},
            "role_stats": {"researcher": 1000},
        }), encoding="utf-8")
    return d


# ---------------------------------------------------------------- §5.5.1 质量闸三态


def test_verdict_ok_for_healthy_run():
    v, reasons = evaluate_run_verdict(
        {"completion_rate": 0.95, "citation_accuracy": 0.8, "avg_steps": 3.2},
        {"metrics_ok": 20, "metrics_partial": 0, "metrics_failed": 0}, 20)
    assert v == "ok" and reasons == []


def test_verdict_suspicious_when_metrics_collapse():
    """`run_20260910_173540` 的病：指标全 0 但管线没坏 ⇒ suspicious，不是 broken。"""
    v, reasons = evaluate_run_verdict(
        {"completion_rate": 0.0, "citation_accuracy": 0.0, "avg_steps": 1.0},
        {"metrics_ok": 20, "metrics_partial": 0, "metrics_failed": 0}, 20)
    assert v == "suspicious"
    assert any("completion_rate" in r for r in reasons)
    assert any("citation_accuracy" in r for r in reasons)
    assert any("avg_steps" in r for r in reasons)


def test_verdict_broken_when_pipeline_fails_mostly():
    """`run_20260910_171054` 的病：11/20 评测失败 ⇒ broken（尺子坏了）。"""
    v, reasons = evaluate_run_verdict(
        {"completion_rate": 1.0, "citation_accuracy": 0.78, "avg_steps": 3.6},
        {"metrics_ok": 0, "metrics_partial": 9, "metrics_failed": 11}, 20)
    assert v == "broken"
    assert any("metrics_failed_ratio" in r for r in reasons)


def test_broken_takes_precedence_over_suspicious():
    """管线大面积失败时均值不可信 ⇒ broken 优先，不能拿它去判被测。"""
    v, _ = evaluate_run_verdict(
        {"completion_rate": 0.0, "citation_accuracy": 0.0, "avg_steps": 1.0},
        {"metrics_ok": 5, "metrics_partial": 0, "metrics_failed": 15}, 20)
    assert v == "broken"


def test_empty_run_is_broken():
    v, reasons = evaluate_run_verdict({}, {}, 0)
    assert v == "broken" and reasons


def test_missing_metric_does_not_trigger_suspicious():
    """指标没算出（非数值）不应据此判被测变差 —— 那是 partial/failed 计数的事。"""
    v, reasons = evaluate_run_verdict(
        {"completion_rate": None, "citation_accuracy": "n/a"},
        {"metrics_ok": 0, "metrics_partial": 20, "metrics_failed": 0}, 20)
    assert v == "ok" and reasons == []


# ---------------------------------------------------------------- 阈值外置：必须真生效


def test_thresholds_are_effective_not_hardcoded():
    """DoD：极严 / 极松阈值 ⇒ verdict 随之变化（证明参数真生效）。"""
    mean = {"completion_rate": 0.5, "citation_accuracy": 0.4, "avg_steps": 2.0}
    counts = {"metrics_ok": 20, "metrics_partial": 0, "metrics_failed": 0}
    strict = QualityThresholds(completion_rate_min=0.9, citation_accuracy_min=0.9, avg_steps_min=5.0)
    loose = QualityThresholds(completion_rate_min=0.0, citation_accuracy_min=0.0, avg_steps_min=0.0)
    assert evaluate_run_verdict(mean, counts, 20, strict)[0] == "suspicious"
    assert evaluate_run_verdict(mean, counts, 20, loose)[0] == "ok"
    assert evaluate_run_verdict(mean, counts, 20, DEFAULT_THRESHOLDS)[0] == "ok"


def test_parse_thresholds_rejects_unknown_field():
    with pytest.raises(SystemExit):
        _parse_thresholds('{"not_a_field": 1}')
    with pytest.raises(SystemExit):
        _parse_thresholds('not json')
    assert _parse_thresholds(None) is DEFAULT_THRESHOLDS
    assert _parse_thresholds('{"avg_steps_min": 9}').avg_steps_min == 9


# ---------------------------------------------------------------- §5.5.4 bootstrap stderr


def test_summarize_metric_edges():
    assert summarize_metric([]) == {"n": 0, "mean": 0.0, "stderr": 0.0, "ci95": [0.0, 0.0]}
    one = summarize_metric([0.7])
    assert one["n"] == 1 and one["stderr"] == 0.0, "n=1 无法估计离散度，如实给 0"


def test_bootstrap_is_deterministic():
    """固定种子 ⇒ 同一份 raw 永远同一个 stderr（否则离线回填与当期结果会不一致）。"""
    vals = [1.0, 0.0, 1.0, 0.5, 1.0, 0.0, 1.0, 1.0, 0.0, 0.5]
    a = summarize_metric(vals)
    b = summarize_metric(vals)
    assert a == b
    assert a["n"] == 10 and a["stderr"] > 0


def test_bootstrap_stderr_in_same_ballpark_as_analytic():
    """bootstrap stderr 与解析标准误同量级（0.5~2 倍）—— 兜住实现错误。"""
    vals = [1.0, 0.0, 1.0, 0.5, 1.0, 0.0, 1.0, 1.0, 0.0, 0.5] * 3
    boot = summarize_metric(vals)["stderr"]
    ana = mean_stderr(vals)
    assert ana > 0
    assert 0.5 * ana <= boot <= 2.0 * ana, f"bootstrap={boot} 与解析={ana} 不在同量级"


# ---------------------------------------------------------------- _summarize 集成


def test_summarize_emits_stderr_and_verdict(tmp_path):
    d = _make_run_dir(tmp_path, raw_rows=[100, 200])
    # 均值全塌（completion 0 / citation 0 / steps 1.0）⇒ suspicious
    results = [_row(completion=0.0, citation=0.0, steps=1.0, coverage=0.0, retrieval=0.0),
               _row(completion=0.0, citation=0.0, steps=1.0, coverage=0.2, retrieval=0.4)]
    s = _summarize(results, {"version": "v1"}, d)

    assert set(s["metrics_mean"]) == set(s["metrics_stderr"]), "每个指标都必须并列给出 stderr"
    assert s["metrics_stderr"]["coverage"]["stderr"] > 0
    assert s["metrics_stderr"]["coverage"]["n"] == 2
    assert s["verdict"] == "suspicious"
    assert s["verdict_reasons"]


def test_mean_alone_hides_spread_but_stderr_shows_it():
    """同一个均值、完全不同的分布 ⇒ 均值看不出，stderr 必须能看出（仪器刻度）。"""
    tight = [0.5, 0.5, 0.5, 0.5, 0.5, 0.5]
    loose = [1.0, 0.0, 1.0, 0.0, 1.0, 0.0]
    tight_d = summarize_metric(tight)
    loose_d = summarize_metric(loose)
    assert tight_d["mean"] == loose_d["mean"] == 0.5, "均值确实相同 ⇒ 只看均值会误判"
    assert tight_d["stderr"] == 0.0
    assert loose_d["stderr"] > tight_d["stderr"] and loose_d["stderr"] > 0.15


def test_summarize_renames_counts_and_keeps_deprecated(tmp_path):
    """§5.5.2：新键 + 旧键同时在，并标 deprecated（兼容 tools/w7_backfill_*.py）。"""
    d = _make_run_dir(tmp_path, raw_rows=[10])
    results = [_row(), _row(status="partial"), _row(status="failed")]
    s = _summarize(results, {"version": "v1"}, d)

    assert (s["metrics_ok"], s["metrics_partial"], s["metrics_failed"]) == (1, 1, 1)
    assert (s["complete"], s["partial"], s["failed"]) == (1, 1, 1), "旧键必须同时写入"
    assert s["deprecated_keys"] == DEPRECATED_COUNT_KEYS


# ---------------------------------------------------------------- D1：成本不得静默归零


def test_cost_degraded_when_stats_missing(tmp_path):
    """D1：文件缺失 ⇒ 降级为 raw 累加 + 显式标记，**绝不**返回 cost_yuan=0.0。"""
    d = _make_run_dir(tmp_path, raw_rows=[500, 700], with_stats=False)
    s = _summarize([_row()], {"version": "v1"}, d)

    c = s["cost_phase1_total"]
    assert c["cost_degraded"] is True
    assert c["total_tokens"] == 1200, "token 必须能从 raw 累加回来（原实现是 0）"
    assert c["cost_yuan"] is None, "金额不可重建 ⇒ 用 None，不能用 0.0 假装没花钱"
    assert "raw_token_sum" == c["cost_basis"]
    assert c["cost_degraded_reason"]


def test_cost_normal_when_stats_present(tmp_path):
    d = _make_run_dir(tmp_path, raw_rows=[500], with_stats=True)
    s = _summarize([_row()], {"version": "v1"}, d)
    c = s["cost_phase1_total"]
    assert c["cost_degraded"] is False
    assert c["cost_basis"] == "phase1_global_stats"
    assert c["total_tokens"] == 1000 and c["cost_yuan"] >= 0


# ---------------------------------------------------------------- 回归验收（真实历史 run）


def _historical(run_id):
    from research_engine.eval.run import RESULTS_DIR

    p = RESULTS_DIR / run_id / "summary.json"
    if not p.exists():
        pytest.skip(f"本地无 {run_id}（results/ 大多未入库，CI 会跳过）")
    return json.loads(p.read_text(encoding="utf-8"))


def test_regression_173540_is_suspicious():
    """DoD 回归：`run_20260910_173540` ⇒ suspicious 且 reasons 非空。"""
    d = _historical("run_20260910_173540")
    counts = {"metrics_ok": d.get("complete", d.get("metrics_ok", 0)),
              "metrics_partial": d.get("partial", d.get("metrics_partial", 0)),
              "metrics_failed": d.get("failed", d.get("metrics_failed", 0))}
    v, reasons = evaluate_run_verdict(d["metrics_mean"], counts, d.get("total", 0))
    assert v == "suspicious", f"实际 {v}；reasons={reasons}"
    assert reasons


def test_regression_171054_is_broken():
    """DoD 回归：`run_20260910_171054`（11/20 管线失败）⇒ broken。"""
    d = _historical("run_20260910_171054")
    counts = {"metrics_ok": d.get("complete", d.get("metrics_ok", 0)),
              "metrics_partial": d.get("partial", d.get("metrics_partial", 0)),
              "metrics_failed": d.get("failed", d.get("metrics_failed", 0))}
    v, _ = evaluate_run_verdict(d["metrics_mean"], counts, d.get("total", 0))
    assert v == "broken"


# ---------------------------------------------------------------- 回填回归（历史评测产物迁移）


def _load_backfill_tool():
    """按文件路径加载 `tools/w8_backfill_stderr.py`（tools/ 不是包）。"""
    import importlib.util
    from pathlib import Path

    p = Path(__file__).resolve().parent.parent / "tools" / "w8_backfill_stderr.py"
    spec = importlib.util.spec_from_file_location("w8_backfill_stderr", p)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_backfill_repairs_d1_and_is_idempotent(tmp_path):
    """回填回归：缺 stats + raw 有 token ⇒ 不得再是 0，且必须 `cost_degraded=true`；二次执行幂等。"""
    bf = _load_backfill_tool()
    d = tmp_path / "run_d1_legacy"
    (d / "raw").mkdir(parents=True)
    (d / "raw" / "q0.raw.json").write_text(
        json.dumps({"q_id": "q0", "token_used": 12345}), encoding="utf-8")
    (d / "eval").mkdir(parents=True)
    (d / "eval" / "q0.eval.json").write_text(json.dumps(_row()), encoding="utf-8")
    # 一份被 D1 污染的旧 summary：token 与金额都被静默归零
    (d / "summary.json").write_text(json.dumps({
        "run_id": d.name, "total": 1, "complete": 1, "partial": 0, "failed": 0,
        "metrics_mean": {"completion_rate": 1.0, "citation_accuracy": 0.8, "avg_steps": 3.0},
        "cost_phase1_total": {"total_tokens": 0, "cost_yuan": 0.0, "per_model": {}, "per_role": {}},
    }, ensure_ascii=False), encoding="utf-8")

    r1 = bf.backfill_one(d, apply=True)
    assert r1["status"] == "written"
    s = json.loads((d / "summary.json").read_text(encoding="utf-8"))

    # ① 统计字段齐备
    assert s["metrics_stderr"], "必须回填 stderr"
    assert s["verdict"] in ("ok", "suspicious", "broken")
    assert "verdict_reasons" in s
    # ② 新旧计数键并存且标 deprecated
    assert (s["metrics_ok"], s["metrics_partial"], s["metrics_failed"]) == (1, 0, 0)
    assert (s["complete"], s["partial"], s["failed"]) == (1, 0, 0)
    assert s["deprecated_keys"] == DEPRECATED_COUNT_KEYS
    # ③ D1 修复：token 回到 raw 实际值，金额记 None（不是 0.0），显式标 degraded
    c = s["cost_phase1_total"]
    assert c["total_tokens"] == 12345, "token 必须等于 raw 累加，不能还是 0"
    assert c["cost_yuan"] is None, "金额不可重建 ⇒ None，不能是 0.0（那等于说没花钱）"
    assert c["cost_degraded"] is True and c["cost_basis"] == "raw_token_sum"

    # ④ 幂等性：再跑一次不应再产生变更
    r2 = bf.backfill_one(d, apply=True)
    assert r2["status"] == "unchanged", f"回填必须幂等，实际 {r2['status']}"


def test_backfill_skips_run_without_eval(tmp_path):
    """没有 eval/*.eval.json 的 run 必须跳过（不写坏产物）。"""
    bf = _load_backfill_tool()
    d = tmp_path / "run_no_eval"
    d.mkdir(parents=True)
    (d / "summary.json").write_text(json.dumps({"total": 0}), encoding="utf-8")
    r = bf.backfill_one(d, apply=True)
    assert "skip" in r["status"]
