"""W7 TBD-8 实验结果分析脚本。

读取 w7_experiment_*/manifest.json，按 arm 聚合三轮均值/标准差，
输出 markdown 对比表并判定是否触发 Arm 2/6。

用法：
    python -m research_engine.eval.w7_analysis --manifest research_engine/eval/results/w7_experiment_*/manifest.json --output docs/eval-report.md
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path
from typing import Dict, List

ARM_LABELS = {
    "arm0_baseline": "Arm 0 基线",
    "arm1_critic_gap": "Arm 1 Critic gap",
    "arm3_validator_fixes": "Arm 3 Validator fixes",
    "arm4_writer_sectioned": "Arm 4 Writer 分节喂料",
    "arm5_validator_trim": "Arm 5 Validator 裁剪",
    "arm6_validator_turbo": "Arm 6 Validator turbo 降档",
}

# TBD-8 判定门槛
THRESHOLDS = {
    "arm1_critic_gap": {"metric": "coverage", "delta": 0.05, "guard": [("completion_rate", -0.05), ("avg_steps", 0.5)]},
    "arm3_validator_fixes": {"metric": "citation_accuracy", "delta": 0.04, "guard": []},
    "arm4_writer_sectioned": {"metric": "citation_accuracy", "delta": -0.10, "guard": [], "target": "幻觉类占比 -10pp"},
    "arm5_validator_trim": {"metric": "cost_yuan", "delta": -0.057, "guard": [("citation_accuracy", -0.01)]},
    "arm6_validator_turbo": {"metric": "cost_yuan", "delta": -0.10, "guard": [("citation_accuracy", -0.01)]},
}


def _arm_means(runs: List[Dict]) -> Dict[str, float]:
    keys = [
        "completion_rate", "citation_accuracy", "citation_accuracy_relaxed",
        "existence_rate", "coverage", "retrieval_hit_rate", "avg_steps",
        "reflection_critic_stop_rate", "cost_yuan", "cost_phase1_tokens", "cost_phase2_tokens",
    ]
    out: Dict[str, float] = {}
    for k in keys:
        vals = [r["metrics"][k] for r in runs if r.get("status") == "done" and k in r.get("metrics", {})]
        if vals:
            out[f"{k}_mean"] = round(sum(vals) / len(vals), 4)
            out[f"{k}_std"] = round(statistics.stdev(vals), 4) if len(vals) > 1 else 0.0
    return out


def _format(v: float) -> str:
    if v == 0.0:
        return "0"
    if v < 0.01:
        return f"{v:.4f}"
    return f"{v:.2%}" if v < 1 else f"{v:.2f}"


def _delta_str(delta: float) -> str:
    sign = "+" if delta >= 0 else ""
    return f"{sign}{delta*100:.1f}pp"


def analyze(manifest_path: Path) -> str:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    runs = manifest.get("runs", [])

    by_arm: Dict[str, List[Dict]] = {}
    for r in runs:
        by_arm.setdefault(r["arm"], []).append(r)

    # 基线
    baseline = by_arm.get("arm0_baseline", [])
    baseline_means = _arm_means(baseline) if baseline else {}

    lines: List[str] = []
    lines.append("## W7 技术债对照实验结果\n")
    lines.append(f"- 实验目录：`{manifest_path.parent}`\n")
    lines.append(f"- 启动时间：{manifest.get('started_at', 'unknown')}\n")
    lines.append(f"- 完成时间：{manifest.get('finished_at', 'unknown')}\n")
    lines.append(f"- 总墙钟：{manifest.get('total_wall_minutes', 'unknown')} min\n")
    lines.append("- 说明：`QDRANT_URL=http://127.0.0.1:6333`（真实 Qdrant，RAG 检索可用）；"
                 "arm 间相对差异有效。\n")
    # Bug 修复（2026-09-12）：原文硬写「QDRANT_URL 强制 :memory: / RAG 检索为空」与事实相反，
    # 会把错误前提写进 docs/eval-report.md。改为从 manifest 的 notes 读取真实环境说明。
    for note in manifest.get("notes", []):
        if "QDRANT_URL" in note:
            lines.append(f"- 环境（取自 manifest）：{note}\n")
            break

    # 数据完整性：区块数不足时显式警告，避免拿单区块当结论
    blocks_done = len({r.get("block") for r in runs if r.get("status") == "done"})
    blocks_planned = manifest.get("runs_per_arm", 0)
    if blocks_done < blocks_planned:
        lines.append(
            f"- ⚠️ **数据不完整**：计划 {blocks_planned} 个区块，"
            f"实际只有 {blocks_done} 个区块完成（{len(runs)} 条 run）。\n"
            f"  单区块无法分离 arm 效应与时间漂移——实测同 arm 跨区块引用准确率波动"
            f" ±8~12pp，而判定阈值只有 4~10pp。**下表不可作为 arm 取舍依据。**\n"
        )
    else:
        lines.append(f"- 数据完整：{blocks_done} 个区块全部完成（{len(runs)} 条 run）。\n")

    # 汇总表（标题按实际轮数动态生成，避免 1 个区块却写「三轮」）
    lines.append(f"### 各 arm 均值（每个区块 1 轮，共 {blocks_done} 轮）\n")
    lines.append("| arm | 完成率 | 引用准确率(严格) | 引用准确率(宽松) | 覆盖度 | 命中率 | 平均步数 | Phase1 成本(¥) | Phase1 tokens | Phase2 tokens |\n")
    lines.append("|-----|--------|------------------|----------------|--------|--------|----------|----------------|---------------|---------------|\n")

    decisions: Dict[str, str] = {}
    for name in ARM_LABELS:
        if name not in by_arm:
            continue
        means = _arm_means(by_arm[name])
        if not means:
            continue
        lines.append(
            f"| {ARM_LABELS[name]} | "
            f"{_format(means.get('completion_rate_mean', 0))} | "
            f"{_format(means.get('citation_accuracy_mean', 0))} | "
            f"{_format(means.get('citation_accuracy_relaxed_mean', 0))} | "
            f"{_format(means.get('coverage_mean', 0))} | "
            f"{_format(means.get('retrieval_hit_rate_mean', 0))} | "
            f"{means.get('avg_steps_mean', 0):.2f} | "
            f"¥{means.get('cost_yuan_mean', 0):.4f} | "
            f"{int(means.get('cost_phase1_tokens_mean', 0))} | "
            f"{int(means.get('cost_phase2_tokens_mean', 0))} |\n"
        )

    # 差异表
    if baseline_means:
        lines.append("\n### 相对 Arm 0 基线的配对差异\n")
        lines.append("| arm | 覆盖度 Δ | 引用准确率(严格) Δ | 命中率 Δ | 平均步数 Δ | Phase1 成本 Δ | 判定 |\n")
        lines.append("|-----|----------|--------------------|----------|------------|---------------|------|\n")
        for name, spec in THRESHOLDS.items():
            if name not in by_arm:
                continue
            means = _arm_means(by_arm[name])
            if not means:
                continue
            metric = spec["metric"]
            delta = means.get(f"{metric}_mean", 0) - baseline_means.get(f"{metric}_mean", 0)
            coverage_delta = means.get("coverage_mean", 0) - baseline_means.get("coverage_mean", 0)
            cit_delta = means.get("citation_accuracy_mean", 0) - baseline_means.get("citation_accuracy_mean", 0)
            hit_delta = means.get("retrieval_hit_rate_mean", 0) - baseline_means.get("retrieval_hit_rate_mean", 0)
            steps_delta = means.get("avg_steps_mean", 0) - baseline_means.get("avg_steps_mean", 0)
            cost_delta = means.get("cost_yuan_mean", 0) - baseline_means.get("cost_yuan_mean", 0)

            # 简单判定逻辑
            passed = abs(delta) >= abs(spec["delta"]) and (delta * spec["delta"] >= 0)
            guard_ok = True
            for g_metric, g_delta in spec.get("guard", []):
                g_val = means.get(f"{g_metric}_mean", 0) - baseline_means.get(f"{g_metric}_mean", 0)
                if g_metric in ("citation_accuracy",):
                    if g_val < g_delta:
                        guard_ok = False
                elif g_metric in ("completion_rate",):
                    if g_val < g_delta:
                        guard_ok = False
                elif g_metric in ("avg_steps",):
                    if g_val > g_delta:
                        guard_ok = False

            if passed and guard_ok:
                decision = "✅ 达标"
            elif not guard_ok:
                decision = "❌ 守门线未过"
            else:
                decision = f"⏸ 未达 Δ 门槛（需 {_delta_str(spec['delta'])}，实际 {_delta_str(delta)}）"
            decisions[name] = decision

            lines.append(
                f"| {ARM_LABELS[name]} | "
                f"{_delta_str(coverage_delta)} | "
                f"{_delta_str(cit_delta)} | "
                f"{_delta_str(hit_delta)} | "
                f"{steps_delta:+.2f} | "
                f"¥{cost_delta:+.4f} | "
                f"{decision} |\n"
            )

        # 条件触发建议
        lines.append("\n### 条件触发判定\n")
        if decisions.get("arm1_critic_gap", "").startswith("✅"):
            lines.append("- **Arm 2（persona 视角发现）**：建议触发，因 Arm 1 达标。\n")
        else:
            lines.append("- **Arm 2（persona 视角发现）**：不触发，Arm 1 未达标。\n")
        if decisions.get("arm3_validator_fixes", "").startswith("✅"):
            lines.append("- **Arm 6（Validator 降档 turbo）**：建议触发，因 Arm 3 达标。\n")
        else:
            lines.append("- **Arm 6（Validator 降档 turbo）**：不触发，Arm 3 未达标。\n")

    # 原始三轮数字附录
    lines.append("\n### 原始数据（三轮）\n")
    for name in ARM_LABELS:
        if name not in by_arm:
            continue
        lines.append(f"\n#### {ARM_LABELS[name]}\n")
        lines.append("| run | 完成率 | 引用准确率 | 覆盖度 | 命中率 | 步数 | Phase1 ¥ |\n")
        lines.append("|-----|--------|------------|--------|--------|------|----------|\n")
        for r in by_arm[name]:
            m = r.get("metrics", {})
            lines.append(
                f"| {r['run_index']+1} | "
                f"{_format(m.get('completion_rate', 0))} | "
                f"{_format(m.get('citation_accuracy', 0))} | "
                f"{_format(m.get('coverage', 0))} | "
                f"{_format(m.get('retrieval_hit_rate', 0))} | "
                f"{m.get('avg_steps', 0):.2f} | "
                f"¥{m.get('cost_yuan', 0):.4f} |\n"
            )

    return "".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="W7 TBD-8 实验结果分析")
    parser.add_argument("--manifest", type=Path, required=True, help="manifest.json 路径")
    parser.add_argument("--output", type=Path, default=None, help="输出 markdown 路径（默认仅打印）")
    args = parser.parse_args()

    report = analyze(args.manifest)
    if args.output:
        text = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        marker = "## W7 技术债对照实验结果"
        if marker in text:
            # 替换已有章节
            parts = text.split(marker)
            new_text = parts[0] + report
        else:
            new_text = text + "\n\n" + report
        args.output.write_text(new_text, encoding="utf-8")
        print(f"报告已写入：{args.output}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
