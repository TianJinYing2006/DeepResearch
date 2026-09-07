"""W5 后验诊断：从已落盘 raw 挖报告外的深层结论。"""
import glob
import json
from pathlib import Path

with open("research_engine/eval/dataset.jsonl", encoding="utf-8") as f:
    raw_rows = [json.loads(line) for line in f]
rows = [r for r in raw_rows if "_meta" not in r]
dataset = {r["id"]: r for r in rows}

raw_dir = Path("research_engine/eval/results/run_20260906_184156/raw")
evals = {
    Path(p).stem.replace(".eval", ""): json.loads(open(p, encoding="utf-8").read())
    for p in glob.glob("research_engine/eval/results/run_v11_compare/eval/*.eval.json")
}

print("=" * 70)
print("[诊断 1] planner 实际子问题数 vs 标注期望子问题数")
print("=" * 70)
plan_total, exp_total, gap_total = 0, 0, 0
examples = []
for qid, _ev in sorted(evals.items()):
    raw = json.loads((raw_dir / f"{qid}.raw.json").read_text(encoding="utf-8"))
    plan = len(raw["state"].get("subquestions", []))
    exp = len(dataset[qid].get("expected_subquestions", []))
    gap = exp - plan
    plan_total += plan
    exp_total += exp
    gap_total += gap
    if gap > 0 and len(examples) < 6:
        examples.append((qid, plan, exp, dataset[qid]["query"][:30]))
print(f"planner 分解总数: {plan_total} | 标注期望总数: {exp_total} | 缺口: {gap_total}")
print("缺口举例 (qid: planner vs 期望):")
for ex in examples:
    print(f"  {ex[0]}: {ex[1]} vs {ex[2]}  | {ex[3]}")

print()
print("=" * 70)
print("[诊断 2] critic 主动停时，覆盖度分布（早停候选）")
print("=" * 70)
early = late = ok_stop = 0
covs = []
for _qid, ev in sorted(evals.items()):
    m = ev["metrics"]
    ref = m["reflection"]
    cov = m["coverage"]["coverage"]
    covs.append(cov)
    if ref["stop_type"] == "critic_stop":
        if cov < 0.9:
            early += 1
        else:
            ok_stop += 1
    elif ref["stop_type"] == "hard_stop":
        late += 1
print(f"critic_stop 中覆盖度<90% (早停候选): {early} 条")
print(f"critic_stop 中覆盖度≥90% (停得对): {ok_stop} 条")
print(f"hard_stop: {late} 条")
print(f"全 20 条平均覆盖度: {sum(covs) / len(covs):.3f}")

print()
print("=" * 70)
print("[诊断 3] 引用忠实度 vs 覆盖度分组对比")
print("=" * 70)
low_cov = [
    ev["metrics"]["citation"]["fidelity_rate"]
    for _qid, ev in evals.items()
    if ev["metrics"]["coverage"]["coverage"] < 0.6
]
high_cov = [
    ev["metrics"]["citation"]["fidelity_rate"]
    for _qid, ev in evals.items()
    if ev["metrics"]["coverage"]["coverage"] >= 0.6
]
print(f"覆盖度<60% 组的引用忠实度均值: {sum(low_cov) / len(low_cov) if low_cov else 0:.3f} (n={len(low_cov)})")
print(f"覆盖度≥60% 组的引用忠实度均值: {sum(high_cov) / len(high_cov) if high_cov else 0:.3f} (n={len(high_cov)})")

print()
print("=" * 70)
print("[诊断 4] 未通过校验 citations 的失败原因分布（Top5）")
print("=" * 70)
notes = {}
for _qid, ev in sorted(evals.items()):
    for k, v in ev["metrics"]["citation"].get("failed_note_distribution", {}).items():
        notes[k] = notes.get(k, 0) + v
for k, v in sorted(notes.items(), key=lambda x: -x[1])[:5]:
    print(f"  {v} 次 | {k}")
