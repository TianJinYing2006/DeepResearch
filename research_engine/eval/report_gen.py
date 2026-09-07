"""W5 eval 报告生成（grill Q7 拍板落地）。

产出：
- results/run_{ts}/baseline.json —— v0 冻结快照（config + 双锚 + 完整性 hash）
- results/history.json          —— run 级追加式历史（趋势表数据源，delta 用百分点 pp）
- docs/eval-report.md           —— 8 节骨架报告
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config

RESULTS_DIR = Path(__file__).resolve().parent / "results"
EVAL_REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-report.md"


def _config_snapshot() -> Dict[str, Any]:
    """config 关键项快照（Q7：每轮 run 都记，baseline 只是 v0 的那份）。"""
    return {
        "planner_model": config.llm.planner_model,
        "critic_model": config.llm.critic_model,
        "fast_model": config.llm.fast_model,
        "smart_model": config.llm.smart_model,
        "token_budget": config.research.token_budget,
        "max_total_hops": config.research.max_total_hops,
        "per_subq_hop_cap": config.research.per_subq_hop_cap,
        "max_replan": config.research.max_replan,
        "search_provider": config.search.provider,
    }


def _snapshot_hash(obj: Dict[str, Any]) -> str:
    """完整性校验指纹（Q7：防手误修改/文件损坏，非防篡改——单人项目无攻击者）。"""
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _git_head() -> str:
    try:
        import subprocess

        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


def write_baseline(run_id: str, dataset_meta: Dict[str, Any]) -> Path:
    """v0 基线冻结快照（Q7：首个 run 自动落 baseline；后续仅当无 history 时生成）。"""
    run_dir = RESULTS_DIR / run_id
    baseline = {
        "baseline_id": "v0",
        "git_commit": _git_head(),
        "dataset_version": dataset_meta.get("version", "unknown"),
        "config_snapshot": _config_snapshot(),
        "eval_env": {
            "python": __import__("sys").version.split()[0],
            "os": __import__("platform").system(),
            "concurrency": 3,
            "wall_clock": "40~60min（并发 3，20 条）",
        },
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    baseline["hash"] = _snapshot_hash(baseline)
    path = run_dir / "baseline.json"
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def append_history(run_id: str, summary: Dict[str, Any], metrics_mean: Dict[str, Any]) -> Path:
    """history.json 追加式（Q7：只追加不覆盖；delta 用百分点）。

    首条自动成为 v0 baseline（等价于基线仅记录，A/B 实验留后续按需做）。
    """
    history_path = RESULTS_DIR / "history.json"
    history: List[Dict[str, Any]] = []
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))

    prev = history[-1]["metrics"] if history else None
    delta = None
    if prev:
        delta = {
            k: round((metrics_mean.get(k, 0) - prev.get(k, 0)) * 100, 1)  # pp（百分点）
            for k in ("completion_rate", "citation_accuracy", "coverage", "retrieval_hit_rate")
            if k in metrics_mean and k in prev
        }
    record = {
        "run_id": run_id,
        "git_commit": _git_head(),
        "metrics": metrics_mean,
        "delta_pp_from_prev": delta,
        "status": summary.get("struct", {}).get("regression", "PASS"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    history.append(record)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return history_path


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def generate_report(
    run_dir: Path,
    summary: Dict[str, Any],
    results: List[Dict[str, Any]],
    dataset: Dict[str, Any],
    force_revalidate: bool = False,
) -> Path:
    """生成 docs/eval-report.md（8 节骨架）+ baseline/history（Q7 拍板）。"""
    run_id = run_dir.name
    metrics_mean = summary["metrics_mean"]
    meta = dataset["meta"]

    # 反思有效性结构性占比先算（summary 层 _avg 对 dict 不适用），供指标表与 history 共用
    sts = [r for r in results if (r.get("metrics") or {}).get("reflection", {}).get("stop_type")]
    critic_stop_count = sum(
        1 for r in sts if (r.get("metrics") or {}).get("reflection", {}).get("stop_type") == "critic_stop"
    )
    reflection_rate = (critic_stop_count / len(sts)) if sts else 0.0
    metrics_mean_effective = dict(metrics_mean)
    metrics_mean_effective["reflection_critic_stop_rate"] = reflection_rate

    # baseline：无 history 即 v0，自动冻结（results 为空时不上抛——空集合不产生基线，防污染趋势表）
    if not results:
        print("⚠️ results 为空，跳过 baseline/history 写入")
        return EVAL_REPORT_PATH
    baseline_path = write_baseline(run_id, meta)
    history_path = append_history(run_id, summary, metrics_mean_effective)

    # ---- 3. 指标表 ----
    header = (
        "| 指标 | 目标 | 本轮 | 达标 |\n"
        "|---|---|---|---|\n"
    )
    rows_tbl = []
    targets = {
        "completion_rate": "≥90%",
        "citation_accuracy": "≥85%（忠实度口径）",
        "coverage": "≥90%",
        "retrieval_hit_rate": "记录基线",
        "avg_steps": "记录基线",
        "reflection_critic_stop_rate": "≥90%",
    }
    for key, target in targets.items():
        val = metrics_mean_effective.get(key)
        if key == "avg_steps":
            val_s = f"{val:.1f} 轮" if isinstance(val, (int, float)) else "—"
        else:
            val_s = f"{val * 100:.1f}%" if isinstance(val, float) else "—"
        mark = "✅" if _target_met(key, val, results) else "⚠️"
        rows_tbl.append(f"| {key} | {target} | {val_s} | {mark} |")

    cost_total = summary.get("cost_phase1_total", {})
    per_model = cost_total.get("per_model") or {}
    per_model_str = ", ".join(
        f"{m}: {v['tokens']}tok ¥{v['cost']:.4f}" for m, v in per_model.items()
    )
    per_role_str = ", ".join(f"{r}: {t}tok" for r, t in (cost_total.get("per_role") or {}).items())
    cost_line = (
        f"💰 总 token（Phase1 研究）：{cost_total.get('total_tokens', 0)}，"
        f"成本：¥{cost_total.get('cost_yuan', 0)}"
        f"（Phase2 judge 另计 {summary.get('cost_phase2_judge_tokens', 0)} token）\n"
        f"  - 模型名桶：{per_model_str or '—'}\n"
        f"  - 职责桶：{per_role_str or '—'}\n"
    )

    # ---- 4. 失败与异常附录 ----
    anomalies = []
    for r in results:
        if r.get("status") != "ok":
            anomalies.append(r)
            continue
        c = (r.get("metrics") or {}).get("cost", {})
        if c.get("single_token_only"):
            if c.get("total_tokens", 0) > 60_000:  # 单条软上限（Q3：超限标红不中止）
                anomalies.append(r)
        elif c.get("total_tokens", 0) > 60_000:
            anomalies.append(r)
    anomaly_lines = "\n".join(
        f"- {r.get('q_id')} [{r.get('status')}]"
        + (f" {((r.get('metrics') or {}).get('cost') or {}).get('total_tokens', '')}tok"
           f" ¥{((r.get('metrics') or {}).get('cost') or {}).get('total_cost', '')}"
           if r.get("metrics") else "")
        + (f" error={r.get('error')[:200]}" if r.get("error") else "")
        for r in anomalies
    ) or "- 无"

    # ---- 7. 指标演进趋势表 ----
    trend_lines = []
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
        trend_lines.append("| 运行 | 完成率 | 引用准确率 | 覆盖度 | 检索命中率 | vs 上轮(pp) |")
        trend_lines.append("|---|---|---|---|---|---|")
        for rec in history:
            m = rec["metrics"]
            delta = rec.get("delta_pp_from_prev") or {}
            d_s = ", ".join(f"{k}:{v:+.1f}" for k, v in delta.items()) if delta else "—"
            trend_lines.append(
                f"| {rec['run_id']} | {m.get('completion_rate', 0) * 100:.1f}% | "
                f"{m.get('citation_accuracy', 0) * 100:.1f}% | {m.get('coverage', 0) * 100:.1f}% | "
                f"{m.get('retrieval_hit_rate', 0) * 100:.1f}% | {d_s} |"
            )

    # ---- 人工抽检模板（v0 首次生成占位，跑完后人工填写）----
    report = f"""# eval 报告（{run_id}）

## 1. 数据集说明
- version: {meta.get('version', 'unknown')} / created_at: {meta.get('created_at', 'unknown')}
- anchor_samples: {meta.get('anchor_samples', [])}
- 标注方法学: {meta.get('annotation', 'ai_draft + human_calibration')}

## 2. 运行环境与双锚
- git commit: {summary.get('git_commit')} / dataset version: {summary.get('dataset_version')}
- 墙钟/并发/统计: {summary.get('total')} 条，并发 3，生成于 {summary.get('generated_at')}

## 3. 指标表（7 项，Q8 含检索命中率）
{header}{''.join(rows_tbl)}
{cost_line}

## 4. 失败与异常附录
- 完整 {summary.get('complete')} / 部分 {summary.get('partial')} / 失败 {summary.get('failed')}
{anomaly_lines}

## 5. 人工抽检记录（两级：报告级 4~5 份 + 引用级 10~15 条，Q6）
- 抽检人: 于晏（单人，判定以标注规范为准；reviewer 字段可补二审）
- [ ] 报告级抽检 4~5 份（质量观感 + 反思质量面 + 每份 2~3 条引用精读）
- [ ] 引用级抽检 10~15 条（引用准确率人类口径）
- [ ] 锚点桶人工复核 6~8 条（确认"真回归 vs 数据漂移"）
- （跑完 v0 后在此填写逐条 verdict）

## 6. 可复现性（3 条重跑 Δ）
- 待完成：选 易/中/难 各 1 条重跑 1 次，记录单条 Δ（目标 ≤10%）与均值 Δ（≤5%）

## 7. 指标演进趋势（自 v0，pp 口径）
{''.join(line + chr(10) for line in trend_lines)}

## 8. 已知局限
- 单人标注/单人抽检（标注者=评估者同源偏倚，如实声明）
- 真实 API 非确定性（博查/arXiv 结果随时间漂移）
- 成本为精确加权（input/output 拆分 × W3 pricing 表），价格有时效
"""
    EVAL_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    EVAL_REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"📄 报告落盘：{EVAL_REPORT_PATH} / baseline：{baseline_path} / history：{history_path}")
    return EVAL_REPORT_PATH


def _target_met(key: str, val: Optional[float], results: List[Dict[str, Any]]) -> bool:
    """目标判定；反思有效性用结构性占比（critic_stop / 有效判定总数）。"""
    if key == "reflection_critic_stop_rate":
        stops = [r for r in results if (r.get("metrics") or {}).get("reflection", {}).get("stop_type")]
        if not stops:
            return False
        critic_stops = sum(
            1 for r in stops
            if (r.get("metrics") or {}).get("reflection", {}).get("stop_type") == "critic_stop"
        )
        return (critic_stops / len(stops)) >= 0.9
    if val is None:
        return False
    thresholds = {"completion_rate": 0.9, "citation_accuracy": 0.85, "coverage": 0.9}
    if key in thresholds:
        return val >= thresholds[key]
    return True  # 记录基线类指标不判达标