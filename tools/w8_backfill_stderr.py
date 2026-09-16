"""W8 Arm 5 §5.5.4：离线回填历史 run 的 **stderr / verdict**（只读优先）。

为什么需要它
------------
历史 run 的 ``summary.json`` 只有均值、没有离散度 —— 于是 W7 事后才发现
「噪声（20~29pp）吞没效应（4~10pp）」时，**已经无法回头判断当时那个数字有多可信**。
本脚本用**现有 eval/*.eval.json**（逐条指标）重算 stderr 与 run 级 verdict 并回填，
⇒ **让历史数据第一次具备可比性判据**。

零 LLM、纯离线。指标聚合走 `research_engine/eval/aggregate.py`（**一处定义**），
不会与 `_summarize()` 的口径分叉。

用法
----
    python tools/w8_backfill_stderr.py                      # 默认 dry-run：只报告不落盘
    python tools/w8_backfill_stderr.py --apply              # 真正写回 summary.json
    python tools/w8_backfill_stderr.py --run-dir run_xxx    # 只处理单个 run

⚠️ 默认**只读**：改 75 份历史产物是不可逆操作，先看 dry-run 输出再决定。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from research_engine.eval.aggregate import read_eval_results, sum_tokens_from_raw  # noqa: E402
from research_engine.eval.quality import (  # noqa: E402
    DEFAULT_THRESHOLDS,
    DEPRECATED_COUNT_KEYS,
    evaluate_run_verdict,
)

RESULTS_DIR = ROOT / "research_engine" / "eval" / "results"


def _counts(summary: dict) -> tuple:
    """兼容新旧键名（旧 run 只有 complete/partial/failed）。"""
    total = int(summary.get("total", 0) or 0)
    ok = int(summary.get("metrics_ok", summary.get("complete", 0)) or 0)
    partial = int(summary.get("metrics_partial", summary.get("partial", 0)) or 0)
    failed = int(summary.get("metrics_failed", summary.get("failed", 0)) or 0)
    return total, {"metrics_ok": ok, "metrics_partial": partial, "metrics_failed": failed}


def backfill_one(run_dir: Path, apply: bool = False) -> dict:
    """回填单个 run；返回统计行。"""
    summary_path = run_dir / "summary.json"
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    results = read_eval_results(run_dir)
    if not results:
        return {"run_id": run_dir.name, "status": "skip（无 eval/*.eval.json）", "verdict": "-"}

    from research_engine.eval.aggregate import compute_metrics

    metrics_mean, metrics_stderr = compute_metrics(results)
    total, counts = _counts(summary)
    verdict, reasons = evaluate_run_verdict(metrics_mean, counts, total, DEFAULT_THRESHOLDS)

    changed = []
    if summary.get("metrics_stderr") != metrics_stderr:
        changed.append("metrics_stderr")
    if summary.get("verdict") != verdict:
        changed.append("verdict")
    if summary.get("deprecated_keys") != DEPRECATED_COUNT_KEYS:
        changed.append("deprecated_keys")

    # D1 侧扫 + **修复**：summary 记的 token 比 raw 累加出来的少 ⇒ 被成本静默归零
    # 缺陷污染过（该缺陷把 total_tokens 也一起归零，故判据是「与 raw 对账」而非
    # 「cost_yuan==0」）。修复取向与 `_build_cost_block()` 完全一致：
    # token 取 raw 累加、**金额记 None**（raw 无按模型拆分，编不出来）、显式标 degraded。
    cost = summary.get("cost_phase1_total") or {}
    raw_tokens = sum_tokens_from_raw(run_dir)
    recorded = int(cost.get("total_tokens") or 0)
    d1_hit = not cost.get("cost_degraded") and raw_tokens > recorded
    if d1_hit:
        changed.append("cost_phase1_total(D1修复)")

    if apply and changed:
        summary["metrics_stderr"] = metrics_stderr
        summary["verdict"] = verdict
        summary["verdict_reasons"] = reasons
        # §5.5.2 新键（旧键保留，标 deprecated）
        summary.setdefault("metrics_ok", counts["metrics_ok"])
        summary.setdefault("metrics_partial", counts["metrics_partial"])
        summary.setdefault("metrics_failed", counts["metrics_failed"])
        # 旧键（complete/partial/failed）保留但**必须带 deprecated 说明**
        summary["deprecated_keys"] = DEPRECATED_COUNT_KEYS
        if d1_hit:
            summary["cost_phase1_total"] = {
                "total_tokens": raw_tokens,
                "cost_yuan": None,
                "per_model": {},
                "per_role": {},
                "cost_degraded": True,
                "cost_basis": "raw_token_sum",
                "cost_degraded_reason": (
                    f"回填修复：原记 {recorded} token 少于 raw 实际 {raw_tokens}"
                    "（phase1_global_stats.json 缺失导致静默归零），金额不可重建"
                ),
            }
        summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    stderr_cov = (metrics_stderr.get("coverage") or {}).get("stderr")
    return {
        "run_id": run_dir.name,
        "status": "written" if (apply and changed) else ("would-write" if changed else "unchanged"),
        "verdict": verdict,
        "total": total,
        "coverage_stderr": f"{stderr_cov * 100:.1f}pp" if isinstance(stderr_cov, (int, float)) else "-",
        "d1_suspected": "⚠️" if d1_hit else "",
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Arm 5 §5.5.4 离线回填 stderr / verdict")
    ap.add_argument("--apply", action="store_true", help="真正写回 summary.json（默认只读 dry-run）")
    ap.add_argument("--run-dir", default=None, help="只处理指定 run 目录名")
    args = ap.parse_args(argv)

    if args.run_dir:
        dirs = [RESULTS_DIR / args.run_dir]
    else:
        dirs = sorted(p for p in RESULTS_DIR.iterdir() if p.is_dir() and p.name.startswith("run_"))

    rows = []
    for d in dirs:
        if not (d / "summary.json").exists():
            continue
        try:
            rows.append(backfill_one(d, apply=args.apply))
        except (OSError, ValueError) as e:
            rows.append({"run_id": d.name, "status": f"ERROR {e}", "verdict": "-",
                         "total": "-", "coverage_stderr": "-", "d1_suspected": ""})

    mode = "APPLY（已写盘）" if args.apply else "DRY-RUN（未写盘）"
    print(f"\n=== Arm 5 §5.5.4 离线回填 · {mode} · {len(rows)} 个 run ===")
    print(f"{'run_id':<28}{'status':<14}{'verdict':<12}{'n':>4}{'σ(coverage)':>13}  D1")
    for r in rows:
        print(f"{r['run_id']:<28}{r['status']:<14}{r.get('verdict','-'):<12}"
              f"{str(r.get('total','-')):>4}{r.get('coverage_stderr','-'):>13}  {r.get('d1_suspected','')}")

    verdicts = {}
    for r in rows:
        verdicts[r.get("verdict")] = verdicts.get(r.get("verdict"), 0) + 1
    print("\nverdict 分布：", verdicts)
    d1 = [r["run_id"] for r in rows if r.get("d1_suspected")]
    if d1:
        print(f"⚠️ 疑似被 D1（成本静默归零）污染的 run {len(d1)} 个：{d1}")
    if not args.apply:
        print("\n（默认只读。确认无误后加 --apply 写回。）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
