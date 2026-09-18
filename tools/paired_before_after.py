"""before / after 配对判定（W8 after 基线专用，只读、零 API）。

为什么不是简单相减
------------------
before(3 轮) 与 after(R 轮) 的指标差值 Δ 必须配上「配对差的标准误」才有意义：
题目效应 q_i 在同一题内相减时被完全抵消，所以

    Var(配对差) = σ_b²/Rb + σ_a²/Ra      （逐题，两侧 σ_within 各估）
    SE_paired   = sqrt(σ_b²/Rb + σ_a²/Ra) / sqrt(n)

两侧 σ_within 分别估，而不是 pooled —— 因为 W8 的修复本身可能让 run 内噪声变小
（Arm 2 让 code_exec 从「144 条全失败」变成真的产出结果），pooled 会把它抹平。

判定口径（§10.5.6 / D4）
------------------------
    |Δ| > MDE(=2.8·SE)  ⇒  可判定，可以说「提升/下降 Δ」
    |Δ| <= MDE          ⇒  不可判定，**禁止**报「提升 Δ」，只能报「Δ ± SE，不可判定」

    ⚠️ 与「覆盖率提升 Xpp」的区别：D4 明令禁止用 coverage 提升验收 W8，
       本脚本照旧算出 coverage，但结论列会照口径标「不可判定」。

用法
----
    python .workbuddy/paired_before_after.py \
        --before results/run_A results/run_B results/run_C \
        --after  results/run_D results/run_E ... \
        [--metric coverage] [--json]
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Sequence

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

import measure_paired_rho as mpr  # noqa: E402

METRICS = ["coverage", "citation_accuracy", "retrieval_hit_rate", "steps"]
T_THR = mpr.T_THR


def _sigma_within(runs: List[Dict[str, float]], common: Sequence[str]) -> float:
    """一组同配置 run 内部的 run 噪声 σ_within（两两轮次差 sd/√2 后取平均）。"""
    if len(runs) < 2:
        return float("nan")
    vals: List[float] = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            diffs = [runs[i][q] - runs[j][q] for q in common]
            vals.append(mpr._sd(diffs) / math.sqrt(2))
    return mpr._mean(vals)


def compare(before: List[Dict[str, float]], after: List[Dict[str, float]], metric: str) -> dict:
    scale = 100.0 if mpr.METRIC_KIND.get(metric, "ratio") == "ratio" else 1.0
    unit = "pp" if scale == 100.0 else ""

    common = set(before[0])
    for r in before[1:] + after:
        common &= set(r)
    common = sorted(common)
    n = len(common)

    b_mean = {q: mpr._mean([r[q] for r in before]) for q in common}
    a_mean = {q: mpr._mean([r[q] for r in after]) for q in common}
    diffs = [(a_mean[q] - b_mean[q]) * scale for q in common]

    delta = mpr._mean(diffs)
    sd_d = mpr._sd(diffs)

    sig_b = _sigma_within(before, common) * scale
    sig_a = _sigma_within(after, common) * scale
    rb, ra = len(before), len(after)

    # 逐题配对差的标准误（两侧 σ_within 各估，不 pooled）
    if rb and ra and sig_b == sig_b and sig_a == sig_a:
        se = math.sqrt(sig_b**2 / rb + sig_a**2 / ra) / math.sqrt(n)
    else:  # 一侧只有 1 轮 ⇒ σ_within 估不出，退化为「差的 sd / √n」
        se = sd_d / math.sqrt(n) if n else float("nan")

    mde = T_THR * se
    t = delta / se if se else float("nan")
    lo, hi = delta - 1.96 * se, delta + 1.96 * se
    decidable = bool(abs(delta) > mde) if se == se else False

    return {
        "metric": metric,
        "unit": unit,
        "n_questions": n,
        "before_mean": mpr._mean([r[q] for q in common for r in before]) * scale,
        "after_mean": mpr._mean([r[q] for q in common for r in after]) * scale,
        "delta": delta,
        "sd_of_paired_diff": sd_d,
        "sigma_within_before": sig_b,
        "sigma_within_after": sig_a,
        "se": se,
        "ci95": [lo, hi],
        "t": t,
        "mde": mde,
        "decidable": decidable,
        "verdict": ("可判定" if decidable else "不可判定"),
    }


def main() -> int:
    ap = argparse.ArgumentParser(description="before/after 配对判定")
    ap.add_argument("--before", nargs="+", required=True)
    ap.add_argument("--after", nargs="+", required=True)
    ap.add_argument("--metric", default=None, help=f"默认全指标；可选 {METRICS}")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    metrics = [args.metric] if args.metric else METRICS
    results = []
    for m in metrics:
        b = [mpr.load_run(d, m) for d in args.before]
        a = [mpr.load_run(d, m) for d in args.after]
        if any(not x for x in b + a):
            print(f"[skip] {m}: 有 run 读不到有效题（{sum(1 for x in b+a if not x)} 个空）", file=sys.stderr)
            continue
        results.append(compare(b, a, m))

    if args.json:
        print(json.dumps(results, ensure_ascii=False, indent=1))
        return 0

    print(f"\n=== before({len(args.before)} 轮) vs after({len(args.after)} 轮) · 配对判定 ===")
    hdr = f"{'指标':<20}{'before':>9}{'after':>9}{'Δ':>9}{'SE':>8}{'MDE':>8}{'t':>7}{'95%CI':>20}{'判定':>10}"
    print(hdr)
    print("-" * len(hdr))
    for r in results:
        u = r["unit"]
        ci = f"[{r['ci95'][0]:+.1f},{r['ci95'][1]:+.1f}]"
        print(
            f"{r['metric']:<20}{r['before_mean']:>8.1f}{u}{r['after_mean']:>8.1f}{u}"
            f"{r['delta']:>+8.1f}{u}{r['se']:>8.2f}{r['mde']:>8.2f}{r['t']:>+7.2f}"
            f"{ci:>20}{r['verdict']:>10}"
        )
    print("\n注：|Δ| <= MDE 即不可判定，不得写成「提升 Δ」。"
          "coverage 按 D4 即便可判定也不作为 W8 验收依据。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
