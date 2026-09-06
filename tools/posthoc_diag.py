# -*- coding: utf-8 -*-
"""W5 后验诊断：从已落盘 raw 挖报告外的深层结论。"""
import json, glob
from pathlib import Path

rows = [json.loads(l) for l in open('research_engine/eval/dataset.jsonl', encoding='utf-8') if '_meta' not in json.loads(l)]
dataset = {r['id']: r for r in rows}

raw_dir = Path('research_engine/eval/results/run_20260906_184156/raw')
evals = {Path(p).stem.replace('.eval', ''): json.loads(open(p, encoding='utf-8').read())
         for p in glob.glob('research_engine/eval/results/run_v11_compare/eval/*.eval.json')}

print("=" * 70)
print("[诊断 1] planner 实际子问题数 vs 标注期望子问题数")
print("=" * 70)
plan_total, exp_total, gap_total = 0, 0, 0
examples = []
for qid, ev in sorted(evals.items()):
    raw = json.loads(open(raw_dir / f"{qid}.raw.json", encoding='utf-8').read())
    plan = len(raw['state'].get('subquestions', []))
    exp = len(dataset[qid].get('expected_subquestions', []))
    gap = exp - plan
    plan_total += plan; exp_total += exp; gap_total += gap
    if gap > 0 and len(examples) < 6:
        examples.append((qid, plan, exp, dataset[qid]['query'][:30]))
print("planner 分解总数: %d | 标注期望总数: %d | 缺口: %d" % (plan_total, exp_total, gap_total))
print("缺口举例 (qid: planner vs 期望):")
for e in examples:
    print("  %s: %d vs %d  | %s" % (e[0], e[1], e[2], e[3]))

print()
print("=" * 70)
print("[诊断 2] critic 主动停时，覆盖度分布（早停候选）")
print("=" * 70)
early = late = ok_stop = 0
covs = []
for qid, ev in sorted(evals.items()):
    m = ev['metrics']
    ref = m['reflection']
    cov = m['coverage']['coverage']
    covs.append(cov)
    if ref['stop_type'] == 'critic_stop':
        if cov < 0.9:
            early += 1
        else:
            ok_stop += 1
    elif ref['stop_type'] == 'hard_stop':
        late += 1
print("critic_stop 中覆盖度<90%% (早停候选): %d 条" % early)
print("critic_stop 中覆盖度≥90%% (停得对): %d 条" % ok_stop)
print("hard_stop: %d 条" % late)
print("全 20 条平均覆盖度: %.3f" % (sum(covs) / len(covs)))

print()
print("=" * 70)
print("[诊断 3] 引用忠实度 vs 覆盖度分组对比")
print("=" * 70)
low_cov = [ev['metrics']['citation']['fidelity_rate'] for qid, ev in evals.items()
           if ev['metrics']['coverage']['coverage'] < 0.6]
high_cov = [ev['metrics']['citation']['fidelity_rate'] for qid, ev in evals.items()
            if ev['metrics']['coverage']['coverage'] >= 0.6]
print("覆盖度<60%% 组的引用忠实度均值: %.3f (n=%d)" % (sum(low_cov) / len(low_cov) if low_cov else 0, len(low_cov)))
print("覆盖度≥60%% 组的引用忠实度均值: %.3f (n=%d)" % (sum(high_cov) / len(high_cov) if high_cov else 0, len(high_cov)))

print()
print("=" * 70)
print("[诊断 4] 未通过校验 citations 的失败原因分布（Top5）")
print("=" * 70)
notes = {}
for qid, ev in sorted(evals.items()):
    for k, v in ev['metrics']['citation'].get('failed_note_distribution', {}).items():
        notes[k] = notes.get(k, 0) + v
for k, v in sorted(notes.items(), key=lambda x: -x[1])[:5]:
    print("  %d 次 | %s" % (v, k))