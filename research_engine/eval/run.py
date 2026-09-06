# -*- coding: utf-8 -*-
"""W5 eval 两阶段管线（grill Q8 拍板落地）。

阶段划分（Q8）：
- Phase 1 `--run-only`：并发 3 跑 Graph，只存原始产出（raw），不做评估。
- Phase 2 `--eval-only`：读 raw 跑七项指标（metrics.py），产出 eval 结果 + 汇总报告。
- 默认无参数：两阶段连续执行（Q3 DoD「一条命令出指标表」保持）。

容错（Q4）：
- 失败三档：瞬态/致命重试 1 次 → 仍失败记 failed + 错误栈；judge 局部失败记 partial + missing_metrics；
  任务级超时 15min（future.result(900)）记 timeout 释放槽位。
- 断点续跑：Phase1 跳过已有 raw 的条；Phase2 跳过已有 eval 的条（条级 + 阶段级双恢复）。

用法：
    python -m research_engine.eval.run --dataset research_engine/eval/dataset.jsonl
    python -m research_engine.eval.run --dataset ... --run-only
    python -m research_engine.eval.run --dataset ... --eval-only --run-dir results/run_20260906_1430
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config import config
from research_engine.eval.metrics import compute_all, compute_cost, _make_judge
from research_engine.graph import create_graph
from research_engine.llm.client import LLMClient

DEFAULT_DATASET = Path(__file__).resolve().parent / "dataset.jsonl"
DEFAULT_CONCURRENCY = 3  # Q4：并发 3（ThreadPoolExecutor，不引 asyncio）
TASK_TIMEOUT_S = 900  # Q4：任务级超时 15min（单条均值 6~9min 的 2 倍）
RETRY_SLEEP_S = 2.0  # Q4：Level 1 瞬态重试前的等待

RESULTS_DIR = Path(__file__).resolve().parent / "results"


# ---------- dataset 加载 ----------

def load_dataset(path: Path) -> Dict[str, Any]:
    """加载 dataset.jsonl；首行为元数据头（{"_meta": {...}}），其余为数据行。"""
    meta: Dict[str, Any] = {}
    rows: List[Dict[str, Any]] = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            row = json.loads(line)
            if "_meta" in row:
                meta = row["_meta"]
            else:
                rows.append(row)
    return {"meta": meta, "rows": rows}


def git_head() -> str:
    """运行时 git commit（Q1 双锚；抓不到如实标 unknown）。"""
    try:
        return subprocess.run(
            ["git", "rev-parse", "HEAD"], capture_output=True, text=True, timeout=10
        ).stdout.strip() or "unknown"
    except Exception:  # noqa: BLE001
        return "unknown"


# ---------- Phase 1：运行（Graph 只存 raw）----------

def _run_one(
    row: Dict[str, Any],
    meta: Dict[str, Any],
    run_dir: Path,
    graph,
) -> Dict[str, Any]:
    """单条 Graph 运行：返回 (status, raw_dict)。异常重试 1 次（Q4 三档）。

    成本归因：类级计数器在并发下是全局累计，单条落盘快照会互相污染——
    因此单条成本用 state.token_used（该 run 自己的硬闸计数），
    全局研究成本在 Phase1 收尾时由类级桶快照统一给出（phase1_global_stats.json）。
    """
    q_id = row["id"]
    topic = row["query"]
    attempt = 0
    last_err: Optional[str] = None
    while attempt <= 1:
        try:
            t0 = time.time()
            # eval 环境禁止 Langfuse 观测干扰（fail-silent 主路径不受影响，trace 归 CLI/Web）
            state = graph.run(topic, thread_id=f"eval-{q_id}-{uuid.uuid4().hex[:6]}")
            raw = {
                "q_id": q_id,
                "query": topic,
                "difficulty": row.get("difficulty", ""),
                "type": row.get("type", ""),
                "state": state.model_dump(),
                "token_used": state.token_used,  # 单条成本 = 该 run 自身硬闸计数（validator 漏计由全局差值补）
                "git_commit": git_head(),
                "dataset_version": meta.get("version", "unknown"),
                "wall_clock_s": round(time.time() - t0, 1),
                "status": "done" if state.status == "done" else "incomplete",
                "error": state.error,
            }
            return {"status": "ok", "raw": raw}
        except Exception as exc:  # noqa: BLE001
            last_err = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            attempt += 1
            if attempt <= 1:  # Level 1/3：瞬态与致命统一重试 1 次（Q4）
                time.sleep(RETRY_SLEEP_S)
    return {
        "status": "failed",
        "raw": {
            "q_id": q_id, "query": topic, "error": last_err, "status": "failed",
            "git_commit": git_head(), "dataset_version": meta.get("version", "unknown"),
        },
    }


def phase1(dataset: Dict[str, Any], run_dir: Path, concurrency: int) -> None:
    """Phase 1：并发 3 跑 Graph，断点续跑（跳过已有 raw）。"""
    rows = dataset["rows"]
    meta = dataset["meta"]
    raw_dir = run_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    pending = []
    for row in rows:
        out = raw_dir / f"{row['id']}.raw.json"
        if out.exists():
            print(f"  [skip] {row['id']}（已有 raw，断点续跑）")
            continue
        pending.append(row)

    if pending:
        print(f"Phase 1: {len(pending)} 条待跑（并发 {concurrency}，已跳过 {len(rows) - len(pending)} 条）")
        LLMClient.reset_stats()  # Q2/Q8：研究成本隔离（Phase 1 桶快照进 raw）
        graph = create_graph()
        done_ok = done_failed = 0
        timeout_ids: List[str] = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {pool.submit(_run_one, row, meta, run_dir, graph): row["id"] for row in pending}
            for fut in futures:
                q_id = futures[fut]
                try:
                    res = fut.result(timeout=TASK_TIMEOUT_S)
                except FutureTimeoutError:
                    # Q4：任务级超时——放弃等待（Python 线程杀不掉，eval 单进程可接受）
                    timeout_ids.append(q_id)
                    print(f"  [timeout] {q_id}（>{TASK_TIMEOUT_S}s，释放槽位）")
                    (raw_dir / f"{q_id}.raw.json").write_text(
                        json.dumps({"q_id": q_id, "status": "timeout", "error": "task timeout"}, ensure_ascii=False),
                        encoding="utf-8",
                    )
                    done_failed += 1
                    continue
                if res["status"] == "ok":
                    (raw_dir / f"{res['raw']['q_id']}.raw.json").write_text(
                        json.dumps(res["raw"], ensure_ascii=False, indent=1), encoding="utf-8"
                    )
                    done_ok += 1
                    if res["raw"]["status"] != "done":
                        done_failed += 1
                else:
                    (raw_dir / f"{res['raw']['q_id']}.raw.json").write_text(
                        json.dumps(res["raw"], ensure_ascii=False, indent=1), encoding="utf-8"
                    )
                    done_failed += 1
        # 成本归因（Q2）：并发下单条快照互相污染 → 全局研究成本统一在 Phase1 收尾抓类级桶
        global_stats = {
            "model_stats": dict(LLMClient.model_stats),
            "role_stats": dict(LLMClient.role_stats),
            "model_io_stats": {k: dict(v) for k, v in LLMClient.model_io_stats.items()},
        }
        (run_dir / "phase1_global_stats.json").write_text(
            json.dumps(global_stats, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        print(f"Phase 1 完成：完整 {done_ok} / 异常（failed+incomplete+timeout）{done_failed}")
    else:
        print("Phase 1: 全部已存在 raw，无需重跑")


# ---------- Phase 2：评估（读 raw 跑七指标）----------

def _evaluate_one(row: Dict[str, Any], raw: Dict[str, Any], judge, eval_dir: Path) -> Dict[str, Any]:
    """单条评估：七指标聚合；judge 局部失败记 partial + missing_metrics（Q4 Level 2）。

    成本：单条用 raw.token_used（该 run 自身硬闸计数，类级快照在并发下不可归因）；
    全局研究成本由 phase1_global_stats.json 在 summary 层给出。
    """
    q_id = row["id"]
    try:
        evaluate_state = dict(raw.get("state") or {})
        evaluate_state["_token_used_single"] = raw.get("token_used", 0)
        metrics = compute_all(evaluate_state, row, judge=judge)
    except Exception:  # noqa: BLE001
        return {
            "q_id": q_id, "status": "failed",
            "error": f"evaluate 异常：{traceback.format_exc()}",
        }

    missing: List[str] = []
    if metrics["coverage"].get("judge_failed"):
        missing.append("coverage")
    # 注：报告质量（report_eval RACE）暂不接入七指标主表——完成率≠质量（Q2），质量面由人工报告级抽检兜底

    partial = bool(missing)
    return {
        "q_id": q_id,
        "status": "partial" if partial else "ok",
        "missing_metrics": missing,
        "metrics": metrics,
    }


def phase2(dataset: Dict[str, Any], run_dir: Path, force_revalidate: bool = False) -> Dict[str, Any]:
    """Phase 2：读 raw 跑七指标；断点续跑（跳过已有 eval）；产出 summary + 报告。"""
    rows = {r["id"]: r for r in dataset["rows"]}
    meta = dataset["meta"]
    raw_dir = run_dir / "raw"
    eval_dir = run_dir / "eval"
    eval_dir.mkdir(parents=True, exist_ok=True)

    LLMClient.reset_stats()  # Q2/Q8：评估成本隔离（judge 桶单独快照）
    judge = _make_judge()

    results: List[Dict[str, Any]] = []
    raw_paths = sorted(raw_dir.glob("*.raw.json"))
    for rp in raw_paths:
        q_id = rp.stem.replace(".raw", "")
        out = eval_dir / f"{q_id}.eval.json"
        if out.exists():
            results.append(json.loads(out.read_text(encoding="utf-8")))
            continue
        raw = json.loads(rp.read_text(encoding="utf-8"))
        if raw.get("status") not in ("done", "incomplete", None):
            # failed / timeout：无 state 可评，直接透传
            res = {"q_id": q_id, "status": raw.get("status", "failed"), "missing_metrics": [], "metrics": None,
                   "error": raw.get("error")}
        elif rows.get(q_id) is None:
            res = {"q_id": q_id, "status": "failed", "missing_metrics": [], "metrics": None,
                   "error": "dataset 中无对应行"}
        else:
            res = _evaluate_one(rows[q_id], raw, judge, eval_dir)
        (eval_dir / f"{q_id}.eval.json").write_text(
            json.dumps(res, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        results.append(res)

    summary = _summarize(results, meta, run_dir)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    from research_engine.eval.report_gen import generate_report  # 延迟导入避免环

    generate_report(run_dir, summary, results, dataset, force_revalidate=force_revalidate)
    return summary


def _read_phase1_cost(run_dir: Path) -> Dict[str, Any]:
    """读 Phase1 全局成本快照（类级桶，收尾统一抓——并发下单条不可归因）。"""
    p = run_dir / "phase1_global_stats.json"
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return {}


def _summarize(results: List[Dict[str, Any]], meta: Dict[str, Any], run_dir: Path) -> Dict[str, Any]:
    """聚合汇总：N 完整 / M 部分 / K 失败 + 七指标均值/合计（Q4 报告口径不混数字）。"""
    # Phase1 全局成本（类级桶快照，收尾统一抓；并发下单条成本不可归因，走全局口径）
    ph1 = _read_phase1_cost(run_dir)
    cost_global = (
        compute_cost(ph1.get("model_io_stats") or {}, ph1.get("model_stats") or {}, ph1.get("role_stats") or {})
        if ph1 else {"total_tokens": 0, "total_cost": 0.0, "per_model": {}, "per_role": {}}
    )
    if not results:
        return {"run_dir": str(run_dir), "total": 0, "complete": 0, "partial": 0, "failed": 0,
                "metrics_mean": {}, "cost_total": {}, "struct": {}, "eval_tokens": LLMClient.tokens_total}

    complete = [r for r in results if r.get("status") == "ok"]
    partial = [r for r in results if r.get("status") == "partial"]
    failed = [r for r in results if r.get("status") != "ok" and r.get("status") != "partial"]

    def _avg(key: str, subkey: Optional[str] = None) -> float:
        vals = []
        for r in results:
            m = (r.get("metrics") or {}).get(key)
            if isinstance(m, dict) and subkey:
                v = m.get(subkey)
                if isinstance(v, (int, float)):
                    vals.append(v)
            elif isinstance(m, (int, float)):
                vals.append(m)
        return round(sum(vals) / len(vals), 4) if vals else 0.0

    # 反思有效性占比：结构性口径（critic_stop / 有 stop_type 的条）——_avg 对 dict 字段不适用，单独算
    stop_rows = [r for r in results if (r.get("metrics") or {}).get("reflection", {}).get("stop_type")]
    critic_stops = sum(
        1 for r in stop_rows
        if (r.get("metrics") or {}).get("reflection", {}).get("stop_type") == "critic_stop"
    )
    reflection_rate = (critic_stops / len(stop_rows)) if stop_rows else 0.0
    return {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "total": len(results),
        "complete": len(complete),
        "partial": len(partial),
        "failed": len(failed),
        "metrics_mean": {
            "completion_rate": _avg("completion", "complete"),
            "citation_accuracy": _avg("citation", "fidelity_rate"),  # W2 忠实度口径
            "existence_rate": _avg("citation", "existence_rate"),
            "coverage": _avg("coverage", "coverage"),
            "retrieval_hit_rate": _avg("retrieval_hit", "retrieval_hit_rate"),
            "avg_steps": _avg("steps", "steps"),
            "reflection_critic_stop_rate": round(reflection_rate, 4),
        },
        "cost_phase1_total": {
            "total_tokens": cost_global["total_tokens"],
            "cost_yuan": cost_global["total_cost"],
            "per_model": cost_global["per_model"],
            "per_role": cost_global["per_role"],
        },
        "cost_phase2_judge_tokens": LLMClient.tokens_total,
        "git_commit": git_head(),
        "dataset_version": meta.get("version", "unknown"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------- CLI ----------

def _latest_run_dir() -> Path:
    if not RESULTS_DIR.exists():
        raise SystemExit("无 results/ 历史 run，请先执行 Phase 1（--run-only）")
    runs = sorted([p for p in RESULTS_DIR.iterdir() if p.is_dir() and p.name.startswith("run_")])
    if not runs:
        raise SystemExit("results/ 下无 run_* 目录，请先执行 Phase 1（--run-only）")
    return runs[-1]


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="W5 eval 两阶段管线")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--run-only", action="store_true", help="只跑 Phase 1（Graph → raw）")
    parser.add_argument("--eval-only", action="store_true", help="只跑 Phase 2（raw → 指标）")
    parser.add_argument("--run-dir", type=Path, default=None, help="Phase 2 指定 run 目录（默认最新）")
    parser.add_argument("--concurrency", type=int, default=DEFAULT_CONCURRENCY)
    parser.add_argument("--timeout-s", type=float, default=TASK_TIMEOUT_S, dest="timeout_s")
    parser.add_argument("--force-revalidate", action="store_true", help="Q8 逃生门：强制重新 running validator 校验")
    args = parser.parse_args(argv)

    if not args.dataset.exists():
        raise SystemExit(f"dataset 不存在：{args.dataset}（先用 --dataset 指定或构建 dataset.jsonl）")
    dataset = load_dataset(args.dataset)
    if not dataset["rows"]:
        raise SystemExit("dataset 为空")

    do_phase1 = not args.eval_only
    do_phase2 = not args.run_only

    if do_phase1:
        run_dir = RESULTS_DIR / f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        print(f"🔍 Phase 1 开始：{run_dir}")
        phase1(dataset, run_dir, args.concurrency)
        if not do_phase2:
            print(f"Phase 1 完成，raw 落盘：{run_dir / 'raw'}。后续可执行 --eval-only --run-dir {run_dir.name}")
            return 0
    else:
        run_dir = args.run_dir if args.run_dir else _latest_run_dir()
        if not run_dir.is_absolute():
            run_dir = RESULTS_DIR / run_dir

    if do_phase2:
        print(f"📊 Phase 2 开始：{run_dir}")
        summary = phase2(dataset, run_dir, force_revalidate=args.force_revalidate)
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())