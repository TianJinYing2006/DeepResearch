"""实测「配对设计」的真实分辨率 —— 用 >=2 个同配置 run 反推 σ_within 与配对 SE。

背景
----
§3.3 断言「固定检索快照后 ρ≈0.9、n=20 题 ⇒ SE≈1.45pp」，但那是**假设值**，
且「固定检索快照」在真实 before/after 里做不到（检索是活的）。
本工具用**同配置多次 run 的真实数据**把这几个数实测出来，不依赖任何假设。

核心恒等式（单向随机效应模型）
----------------------------
设第 i 题第 r 轮观测 x_ir = μ + q_i + e_ir，q_i 为题目效应、e_ir 为 run 噪声。

  * 同一题两轮之差：   x_i1 − x_i2 = e_i1 − e_i2   ⇒ Var(差) = 2·σ_within²
        ⇒ **σ_within = sd(x_i1 − x_i2) / √2**     （无需假设，两轮即可估）

  * 跨轮相关：        ρ = Cov(x_·1, x_·2) / (σ_1·σ_2) = σ_q² / (σ_q² + σ_within²)

  * **配对差的标准误**（关键）：比较「before 均值（R_b 轮）」与「after 均值（R_a 轮）」时，
    题目效应 q_i 在**同一题内相减时被完全抵消**，故

        SE_paired = σ_within · √( 1/R_b + 1/R_a ) / √n

    ⇒ **与 σ_question 无关**。这正是配对设计相对独立两样本的全部价值所在。

  * 最小可检出效应（MDE，双侧 α≈0.05、β≈0.2 ⇒ t≈2.8）：
        MDE = 2.8 · SE_paired

用法
----
    python tools/measure_paired_rho.py --runs <dir1> <dir2> [--runs <dir3> ...]
                                       [--metric coverage] [--json]

指标取值路径 / 口径
------------------
    coverage            metrics.coverage.coverage                          ratio
    citation_accuracy   metrics.citation.verified / total_citations        ratio
    retrieval_hit_rate  metrics.retrieval_hit.retrieval_hit_rate           ratio
    steps               metrics.steps.steps                                count
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys
from typing import Dict, List, Optional, Sequence

T_THR = 2.8  # 双侧 α≈0.05、power≈0.8 的 t 值（n≈20 时实际 2.09，取 2.8 偏保守）

# 指标口径：ratio = 比率（乘 100 报 pp）；count = 计数（原单位，不可称 pp ——
# 2026-09-16 踩坑：steps 是步数，按比率乘 100 会输出「σ=306.60pp」这种荒谬值）
METRIC_KIND = {
    "coverage": "ratio",
    "citation_accuracy": "ratio",
    "retrieval_hit_rate": "ratio",
    "steps": "count",
}


def _dig(d: dict, path: str):
    cur = d
    for k in path.split("."):
        if not isinstance(cur, dict) or k not in cur:
            return None
        cur = cur[k]
    return cur


def load_run(run_dir: str, metric: str) -> Dict[str, float]:
    """读一个 run 的每题指标。只收 status=='ok' 的题（缺指标的题不参与配对）。"""
    out: Dict[str, float] = {}
    for p in sorted(glob.glob(os.path.join(run_dir, "eval", "*.json"))):
        try:
            with open(p, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception:  # noqa: BLE001
            continue
        if d.get("status") != "ok":
            continue
        qid = d.get("q_id")
        if not qid:
            continue
        m = d.get("metrics") or {}
        if metric == "coverage":
            v = _dig(m, "coverage.coverage")
        elif metric == "citation_accuracy":
            tot = _dig(m, "citation.total_citations")
            ver = _dig(m, "citation.verified")
            v = (ver / tot) if (tot and ver is not None) else None
        elif metric == "retrieval_hit_rate":
            v = _dig(m, "retrieval_hit.retrieval_hit_rate")
        elif metric == "steps":
            v = _dig(m, "steps.steps")
        else:  # pragma: no cover
            raise ValueError(f"未知指标: {metric}")
        if isinstance(v, (int, float)):
            out[qid] = float(v)
    return out


def _mean(xs: Sequence[float]) -> float:
    return sum(xs) / len(xs) if xs else float("nan")


def _sd(xs: Sequence[float]) -> float:
    if len(xs) < 2:
        return float("nan")
    m = _mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1))


def _pearson(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 2:
        return float("nan")
    ma, mb = _mean(a), _mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    da = math.sqrt(sum((x - ma) ** 2 for x in a))
    db = math.sqrt(sum((y - mb) ** 2 for y in b))
    return num / (da * db) if da and db else float("nan")


def analyze(runs: List[Dict[str, float]], metric: str) -> dict:
    scale = 100.0 if METRIC_KIND.get(metric, "ratio") == "ratio" else 1.0
    unit = "pp" if scale == 100.0 else "count"

    common = set(runs[0])
    for r in runs[1:]:
        common &= set(r)
    common = sorted(common)
    n = len(common)

    per_run_mean = [_mean([r[q] for q in common]) for r in runs]

    # σ_within：两两轮次内差的 sd / √2，再对轮次对取平均
    sig_withins: List[float] = []
    rhos: List[float] = []
    for i in range(len(runs)):
        for j in range(i + 1, len(runs)):
            a = [runs[i][q] for q in common]
            b = [runs[j][q] for q in common]
            diffs = [x - y for x, y in zip(a, b)]
            sig_withins.append(_sd(diffs) / math.sqrt(2))
            rhos.append(_pearson(a, b))
    sigma_within = _mean(sig_withins)
    rho = _mean(rhos)

    # σ_question：由 ρ 与 σ_within 反解（ρ = σ_q² / (σ_q² + σ_within²)）
    sigma_q = float("nan")
    if rho == rho and 0.0 < rho < 1.0:  # noqa: PLR0124 — NaN 自检
        sigma_q = sigma_within * math.sqrt(rho / (1.0 - rho))

    sigma_total = _mean([_sd([r[q] for q in common]) for r in runs])

    plans = [(1, 1), (3, 9), (3, 3), (9, 9)]

    def se_for(rb: int, ra: int) -> float:
        return sigma_within * math.sqrt(1.0 / rb + 1.0 / ra) / math.sqrt(n)

    return {
        "metric": metric,
        "unit": unit,
        "n_questions": n,
        "n_runs": len(runs),
        "per_run_mean": per_run_mean,
        "run_mean_range": (max(per_run_mean) - min(per_run_mean)) * scale,
        "sigma_within": sigma_within * scale,
        "sigma_question": sigma_q * scale,
        "sigma_total": sigma_total * scale,
        "rho": rho,
        "se": {f"{rb}v{ra}": se_for(rb, ra) * scale for rb, ra in plans},
        "mde": {f"{rb}v{ra}": T_THR * se_for(rb, ra) * scale for rb, ra in plans},
    }


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(
        description="实测配对设计的 σ_within / ρ / SE / MDE",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("--runs", nargs="+", action="append", required=True,
                    help="run 目录（每次 --runs 后接一批；可重复）")
    ap.add_argument("--metric", default="coverage",
                    choices=sorted(METRIC_KIND))
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    a = ap.parse_args(argv)

    dirs = [d for grp in a.runs for d in grp]
    if len(dirs) < 2:
        print("至少给 2 个 run 目录才能估 σ_within", file=sys.stderr)
        return 2

    runs = []
    for d in dirs:
        r = load_run(d, a.metric)
        if not r:
            print(f"{d} 里没读到任何 status=ok 的指标", file=sys.stderr)
            return 2
        runs.append(r)

    res = analyze(runs, a.metric)
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return 0

    unit = res["unit"]
    ul = "pp" if unit == "pp" else "步"
    f = (lambda v: v * 100.0) if unit == "pp" else (lambda v: v)

    print(f"\n=== 配对设计实测 · 指标 = {res['metric']}（{ul}）===")
    print(f"run 数 = {res['n_runs']}    配对题数 n = {res['n_questions']}")
    print()
    print("各 run 均值 :", "  ".join(f"{f(v):.2f}" for v in res["per_run_mean"]))
    print(f"轮次间极差   : {res['run_mean_range']:.2f}{ul}   ← 这就是「漂移」的直观量")
    print()
    print(f"σ_within（同题跨轮噪声）= {res['sigma_within']:.2f}{ul}")
    print(f"σ_question（题目效应）  = {res['sigma_question']:.2f}{ul}   （由 ρ 反解）")
    print(f"σ_total（单轮跨题散布） = {res['sigma_total']:.2f}{ul}")
    print(f"跨轮相关 ρ             = {res['rho']:.3f}")
    print()
    print(f"配对差的标准误 SE 与最小可检出效应 MDE（t={T_THR}）：")
    print(f"  {'设计(Rb v Ra)':>14} | {'SE(' + ul + ')':>10} | {'MDE(' + ul + ')':>10}")
    print(f"  {'-' * 14}-+-{'-' * 10}-+-{'-' * 10}")
    for k in res["se"]:
        print(f"  {k:>14} | {res['se'][k]:>10.2f} | {res['mde'][k]:>10.2f}")
    print()
    print("注：SE 与 σ_question 无关 —— 题目效应在同一题内相减时被完全抵消。")
    print("    若 MDE 大于期望检出的效应，唯一出路是加 runs（不是加题）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
