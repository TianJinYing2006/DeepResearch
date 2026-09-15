"""§10.5.4：零成本实测「题目间方差」σ_question（只读，零 LLM）。

为什么需要它
------------
`docs/requirements/8-*.md` §10.5.3 的「题目采样 SE≈11.2pp」是按 p=0.47 **假设**算出来的
二项 SE（√(p(1-p)/n)），不是实测。它决定了 after 基线该扩到 60 题还是 100 题 ——
**一个假设值在驱动 ¥32~¥54 的决策**，所以必须实测。

统计方法（one-way random effects 方差成分分解）
---------------------------------------------
单次 run 内 20 题 coverage 的离散度 = 题目真实差异 + 该次运行的噪声，直接取标准差会**高估**
题目效应。正确做法是把两者分开：

    对题目 i，跨 R 个 block 的观测值 x_ir = μ + a_i + e_ir
        a_i ~ N(0, σ_question²)   题目效应（我们想要的）
        e_ir ~ N(0, σ_drift²)     同一题目在不同时间点的漂移

    题目均值 m_i = mean_r(x_ir)，其方差 = σ_question² + σ_drift²/R
    ⇒ σ_question² = Var(m_i) − σ_within²/R      （σ_within² 由题目内跨 block 方差估计）

本脚本同时报告**未修正**与**修正后**的 σ_question，让高估幅度透明。

用法
----
    python tools/measure_question_variance.py                 # 默认 coverage
    python tools/measure_question_variance.py --metric citation
"""
from __future__ import annotations

import argparse
import json
import math
import statistics
from pathlib import Path
from typing import Dict, List

RESULTS = Path(__file__).resolve().parent.parent / "research_engine" / "eval" / "results"
W7_MANIFEST = RESULTS / "w7_experiment_20260911_194151" / "manifest.json"

# 指标取值路径：(metrics 内的段, 段内键)
METRICS = {
    "coverage": ("coverage", "coverage"),
    "citation": ("citation", "fidelity_rate"),
    "retrieval_hit": ("retrieval_hit", "retrieval_hit_rate"),
}

# §10.5.2 实测单轮成本口径
COST_PER_QUESTION_RUN = 1.2 / 20      # ¥/题/轮（20 题单轮 ≈¥1.2）
MIN_PER_QUESTION_RUN = 43 / 20        # min/题/轮（20 题单轮 ≈43min）
SIGMA_DRIFT = 0.145                   # §10.5.3：block 间漂移 σ≈14.5pp


def _per_question(run_dir: Path, metric: str) -> Dict[str, float]:
    """读一个 run 的 20 条 eval，返回 {q_id: 指标值}。"""
    seg, key = METRICS[metric]
    out: Dict[str, float] = {}
    eval_dir = run_dir / "eval"
    if not eval_dir.exists():
        return out
    for p in sorted(eval_dir.glob("*.eval.json")):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        m = (d.get("metrics") or {}).get(seg)
        if isinstance(m, dict) and isinstance(m.get(key), (int, float)):
            out[d.get("q_id", p.stem.replace(".eval", ""))] = float(m[key])
    return out


def _load_arms(metric: str) -> Dict[str, Dict[int, Dict[str, float]]]:
    """{arm: {block: {q_id: value}}}"""
    manifest = json.loads(W7_MANIFEST.read_text(encoding="utf-8"))
    arms: Dict[str, Dict[int, Dict[str, float]]] = {}
    for r in manifest.get("runs", []):
        if r.get("status") != "done":
            continue
        rd = Path(r["run_dir"])
        if not rd.exists():
            continue
        pq = _per_question(rd, metric)
        if pq:
            arms.setdefault(r["arm"], {})[r["block"]] = pq
    return arms


def analyse(arms: Dict[str, Dict[int, Dict[str, float]]]) -> List[Dict[str, float]]:
    rows = []
    for arm, blocks in sorted(arms.items()):
        if len(blocks) < 2:
            continue  # 至少 2 个 block 才能分离题目效应与漂移
        common = set.intersection(*[set(b) for b in blocks.values()])
        if len(common) < 5:
            continue
        means, within_vars = [], []
        for q in sorted(common):
            vals = [blocks[b][q] for b in sorted(blocks)]
            means.append(sum(vals) / len(vals))
            if len(vals) > 1:
                within_vars.append(statistics.variance(vals))
        n_q, R = len(means), len(blocks)
        var_m = statistics.variance(means)          # Var(m_i) = σ_q² + σ_drift²/R
        var_w = (sum(within_vars) / len(within_vars)) if within_vars else 0.0
        sigma_raw = math.sqrt(var_m)
        var_corrected = var_m - var_w / R
        sigma_corr = math.sqrt(var_corrected) if var_corrected > 0 else 0.0
        rows.append({
            "arm": arm, "n_questions": n_q, "blocks": R,
            "sigma_raw": sigma_raw, "sigma_corrected": sigma_corr,
            "sigma_within": math.sqrt(var_w) if var_w > 0 else float("nan"),
            "se20_raw": sigma_raw / math.sqrt(n_q),
            "se20_corrected": sigma_corr / math.sqrt(n_q),
        })
    return rows


def curve(sigma: float) -> List[Dict[str, float]]:
    """题数-精度-成本曲线（按修正后 σ_question 外推）。"""
    out = []
    for n in (20, 40, 60, 100, 150):
        se = sigma / math.sqrt(n)
        for runs in (3, 9):
            out.append({
                "questions": n, "runs": runs, "se_question": se,
                "total_se": math.sqrt(SIGMA_DRIFT**2 / runs + se**2),
                "cost": n * runs * COST_PER_QUESTION_RUN,
                "hours": n * runs * MIN_PER_QUESTION_RUN / 60,
            })
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description="§10.5.4 实测题目间方差")
    ap.add_argument("--metric", default="coverage", choices=list(METRICS))
    ap.add_argument("--json", action="store_true", help="输出 JSON（便于落档）")
    args = ap.parse_args()

    arms = _load_arms(args.metric)
    rows = analyse(arms)
    if not rows:
        print("无足够数据（需要某 arm 至少 2 个 block 且有共同题目）")
        return 1

    print(f"\n=== §10.5.4 实测题目间方差（metric={args.metric}，数据源：W7 权威容器，只读）===\n")
    print("| arm | 题数 | blocks | σ_question(未修正) | σ_question(修正后) | SE@20(修正后) |")
    print("| --- | --- | --- | --- | --- | --- |")
    for r in rows:
        print(f"| {r['arm']} | {r['n_questions']} | {r['blocks']} | "
              f"{r['sigma_raw']*100:.1f}pp | **{r['sigma_corrected']*100:.1f}pp** | "
              f"{r['se20_corrected']*100:.1f}pp |")

    # 只取 3 个 block 的 arm 做汇总：block=2 时方差分解不可靠（σ_within 只有 1 个自由度）
    solid = [r for r in rows if r["blocks"] >= 3] or rows
    sigma = statistics.mean([r["sigma_corrected"] for r in solid])
    sigma_raw = statistics.mean([r["sigma_raw"] for r in solid])
    print(f"\n各 arm 均值（仅 {len(solid)} 个 3-block arm，排除 block=2 的不可靠估计）：")
    print(f"  σ_question(修正后) = **{sigma*100:.1f}pp**"
          f"（未修正 {sigma_raw*100:.1f}pp ⇒ 直接取单次 run 标准差会高估 "
          f"{(sigma_raw/sigma-1)*100:.0f}%）" if sigma > 0 else
          f"  σ_question(修正后) = **0.0pp** ⚠️ 信号弱于噪声，方差分解被下界截断 ⇒ "
          f"只能给出上界 {sigma_raw*100:.1f}pp")
    print("  原题设（二项假设 p=0.47, n=20）= 11.2pp")
    print("\n⚠️ 读数提醒：coverage 与 citation 的 σ_question 差一个数量级，"
          "**必须按指标分别看**，不能混着取平均。\n")

    print("| 题数 | runs | 题目 SE | 总 SE | 成本 | 墙钟 |")
    print("| --- | --- | --- | --- | --- | --- |")
    for c in curve(sigma):
        print(f"| {c['questions']} | {c['runs']} | {c['se_question']*100:.1f}pp | "
              f"**{c['total_se']*100:.1f}pp** | ¥{c['cost']:.0f} | {c['hours']:.1f}h |")

    if args.json:
        print("\n" + json.dumps({"metric": args.metric, "sigma_question": sigma,
                                 "sigma_question_raw": sigma_raw, "rows": rows,
                                 "curve": curve(sigma)}, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
