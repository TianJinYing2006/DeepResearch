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
import sys
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeoutError
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from research_engine.eval.aggregate import compute_metrics, sum_tokens_from_raw
from research_engine.eval.metrics import _make_judge, compute_all, compute_cost
from research_engine.eval.provenance import UNKNOWN, code_revision, run_provenance
from research_engine.eval.quality import (
    DEFAULT_THRESHOLDS,
    DEPRECATED_COUNT_KEYS,
    QualityThresholds,
    evaluate_run_verdict,
)
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
    """运行时 git commit（Q1 双锚；抓不到如实标 unknown）。

    W8 §10.4：实现已迁至 `provenance.code_revision()`（原为 3 份重复实现之一，
    与 `report_gen._git_head` 是同一段代码的整段复制）。
    """
    return code_revision()["git_commit"]


# ---------- Phase 1：运行（Graph 只存 raw）----------

def _run_one(
    row: Dict[str, Any],
    meta: Dict[str, Any],
    run_dir: Path,
    graph,
    prov: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """单条 Graph 运行：返回 (status, raw_dict)。异常重试 1 次（Q4 三档）。

    成本归因：类级计数器在并发下是全局累计，单条落盘快照会互相污染——
    因此单条成本用 state.token_used（该 run 自己的硬闸计数），
    全局研究成本在 Phase1 收尾时由类级桶快照统一给出（phase1_global_stats.json）。

    `prov`（W8 §10.4）：整轮 run 的 provenance，由 phase1 算**一次**后传进来，
    不按条目各自调 git（21 条 × 3 次子进程太慢，且中途工作区变化会让条目间不一致）。
    未传则现算一次（兼容直接调用 / 单测）。
    """
    q_id = row["id"]
    topic = row["query"]
    p = prov or run_provenance()
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
                "git_commit": p["git_commit"],
                "git_dirty": p["git_dirty"],
                "git_diff_hash": p["git_diff_hash"],
                # W8 §10.4 第 6 条：内嵌**同一份** config_snapshot 对象（不是手抄副本）
                # ⇒ 单条 raw 自包含，但配置只有一个产生点，不会分叉
                "config_snapshot": p["config_snapshot"],
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
            "git_commit": p["git_commit"], "git_dirty": p["git_dirty"],
            "git_diff_hash": p["git_diff_hash"], "config_snapshot": p["config_snapshot"],
            "dataset_version": meta.get("version", "unknown"),
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
        # W8 §10.4：整轮 run 只抓一次 provenance（跑批时刻的 git + config），按条目复用
        prov = run_provenance()
        done_ok = done_failed = 0
        timeout_ids: List[str] = []
        with ThreadPoolExecutor(max_workers=concurrency) as pool:
            futures = {
                pool.submit(_run_one, row, meta, run_dir, graph, prov): row["id"]
                for row in pending
            }
            for fut in futures:
                q_id = futures[fut]
                try:
                    res = fut.result(timeout=TASK_TIMEOUT_S)
                except FutureTimeoutError:
                    # Q4：任务级超时——放弃等待（Python 线程杀不掉，eval 单进程可接受）
                    timeout_ids.append(q_id)
                    print(f"  [timeout] {q_id}（>{TASK_TIMEOUT_S}s，释放槽位）")
                    # W8 §10.4：超时条目同样落 provenance —— 超时恰是要诊断的场景，
                    # 没有配置快照就无法判断「是配置问题还是偶发」
                    (raw_dir / f"{q_id}.raw.json").write_text(
                        json.dumps({"q_id": q_id, "status": "timeout", "error": "task timeout",
                                    "git_commit": prov["git_commit"], "git_dirty": prov["git_dirty"],
                                    "git_diff_hash": prov["git_diff_hash"],
                                    "config_snapshot": prov["config_snapshot"]}, ensure_ascii=False),
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


def phase2(
    dataset: Dict[str, Any],
    run_dir: Path,
    force_revalidate: bool = False,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> Dict[str, Any]:
    """Phase 2：读 raw 跑七指标；断点续跑（跳过已有 eval）；产出 summary + 报告。

    ``thresholds``（Arm 5 §5.5.1）：run 级质量闸阈值**外置为参数** ——
    默认组只在代码里，需求文档不写死数字；调用方可传自己的阈值而无需改文档。
    """
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

    summary = _summarize(results, meta, run_dir, thresholds=thresholds)
    (run_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8")

    from research_engine.eval.report_gen import generate_report  # 延迟导入避免环

    generate_report(run_dir, summary, results, dataset, force_revalidate=force_revalidate)
    return summary


def _provenance_from_raw(run_dir: Path) -> Dict[str, Any]:
    """从本 run 的 raw 里读回**跑批时刻**的 provenance，而不是汇总时刻现抓。

    W7 教训：补跑时工作树已变（Block 0 在 `95adb77`+patch、Block 1/2 在 `ca51886`），
    跨区块引用口径不可合并却直到分析阶段才发现。若汇总时现抓 git，记下的是
    「汇总时刻」而非「跑批时刻」，同一个洞换个位置继续漏。

    ⇒ 一律以 raw 里落盘的那份为准。raw 全部缺失 / 为 §10.4 之前的历史产物时，
    如实返回 unknown 与 `config_snapshot=None`，**不伪造**。
    """
    raw_dir = run_dir / "raw"
    if raw_dir.exists():
        for rp in sorted(raw_dir.glob("*.raw.json")):
            try:
                raw = json.loads(rp.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                continue
            if isinstance(raw, dict) and raw.get("config_snapshot") is not None:
                return {
                    "git_commit": raw.get("git_commit", UNKNOWN),
                    "git_dirty": raw.get("git_dirty", False),
                    "git_diff_hash": raw.get("git_diff_hash", ""),
                    "config_snapshot": raw["config_snapshot"],
                }
    return {
        "git_commit": UNKNOWN,
        "git_dirty": False,
        "git_diff_hash": "",
        "config_snapshot": None,
    }


def _read_phase1_cost(run_dir: Path) -> Dict[str, Any]:
    """读 Phase1 全局成本快照（类级桶，收尾统一抓——并发下单条不可归因）。

    ⚠️ **W8 已知缺陷 D1 修复**：改造前文件缺失时返回 ``{}`` ⇒ 调用方直接取
    ``{"total_tokens": 0, "total_cost": 0.0}``，**shape 与真实结果完全相同、无任何标记**，
    下游永远发现不了（before 基线第 3 轮就是这样丢了 ¥0.95：raw 里 1,017,784 token
    却记 cost=¥0）。现在改为**显式降级**，绝不静默返 0。
    """
    p = run_dir / "phase1_global_stats.json"
    data: Dict[str, Any] = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = {}
    if data:
        return {"stats": data, "degraded": False, "basis": "phase1_global_stats", "reason": ""}
    return {
        "stats": None,
        "degraded": True,
        "basis": "missing",
        "reason": "phase1_global_stats.json 缺失或为空（常见于进程中断），无法按模型拆分成本",
    }


def _build_cost_block(run_dir: Path) -> Dict[str, Any]:
    """组装成本块；缺失时降级为 raw 累加 + 显式 ``cost_degraded`` 标记。"""
    info = _read_phase1_cost(run_dir)
    if not info["degraded"]:
        st = info["stats"]
        cg = compute_cost(
            st.get("model_io_stats") or {}, st.get("model_stats") or {}, st.get("role_stats") or {}
        )
        return {
            "total_tokens": cg["total_tokens"],
            "cost_yuan": cg["total_cost"],
            "per_model": cg["per_model"],
            "per_role": cg["per_role"],
            "cost_degraded": False,
            "cost_basis": info["basis"],
            "cost_degraded_reason": "",
        }
    tokens = sum_tokens_from_raw(run_dir)
    print(
        f"⚠️ [D1] {run_dir.name}：{info['reason']} ⇒ 成本降级为 raw 累加 "
        f"（total_tokens={tokens}，cost_yuan 不可重建记为 None），已标 cost_degraded=true",
        file=sys.stderr,
    )
    return {
        "total_tokens": tokens,
        # 金额不可重建 —— 用 None 而不是 0.0，否则「没花钱」与「算不出」又分不开了
        "cost_yuan": None,
        "per_model": {},
        "per_role": {},
        "cost_degraded": True,
        "cost_basis": "raw_token_sum",
        "cost_degraded_reason": info["reason"],
    }


def _summarize(
    results: List[Dict[str, Any]],
    meta: Dict[str, Any],
    run_dir: Path,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> Dict[str, Any]:
    """聚合汇总：N 完整 / M 部分 / K 失败 + 七指标**均值与离散度** + run 级质量闸。

    W8 Arm 5：

    * §5.5.1 顶层出 ``verdict`` / ``verdict_reasons``（**只告警不阻断**）；
    * §5.5.2 ``complete/partial/failed`` → ``metrics_ok/metrics_partial/metrics_failed``，
      旧键**同时写入**并标 deprecated（兼容 `tools/w7_backfill_*.py` 与历史趋势表）；
    * §5.5.4 ``metrics_stderr`` 每个指标并列输出 bootstrap stderr + 有效题数 n。
    """
    # W8 §10.4：provenance 取**跑批时刻**（存在 raw 里那份），不是汇总时刻现抓
    _prov = _provenance_from_raw(run_dir)
    cost_block = _build_cost_block(run_dir)

    if not results:
        verdict, verdict_reasons = evaluate_run_verdict({}, {}, 0, thresholds)
        return {"run_dir": str(run_dir), "total": 0,
                "metrics_ok": 0, "metrics_partial": 0, "metrics_failed": 0,
                "complete": 0, "partial": 0, "failed": 0,  # deprecated（见 DEPRECATED_COUNT_KEYS）
                "deprecated_keys": DEPRECATED_COUNT_KEYS,
                "verdict": verdict, "verdict_reasons": verdict_reasons,
                "metrics_mean": {}, "metrics_stderr": {}, "cost_total": {}, "struct": {},
                "cost_phase1_total": cost_block,
                "cost_degraded": cost_block["cost_degraded"],
                "eval_tokens": LLMClient.tokens_total,
                "git_commit": _prov["git_commit"], "git_dirty": _prov["git_dirty"],
                "git_diff_hash": _prov["git_diff_hash"], "config_snapshot": _prov["config_snapshot"]}

    complete = [r for r in results if r.get("status") == "ok"]
    partial = [r for r in results if r.get("status") == "partial"]
    failed = [r for r in results if r.get("status") != "ok" and r.get("status") != "partial"]

    # 指标聚合的**一处定义**（`eval/aggregate.py`）——离线回填脚本共用，避免两处口径分叉
    metrics_mean, metrics_stderr = compute_metrics(results)

    counts = {"metrics_ok": len(complete), "metrics_partial": len(partial), "metrics_failed": len(failed)}
    verdict, verdict_reasons = evaluate_run_verdict(metrics_mean, counts, len(results), thresholds)

    return {
        "run_dir": str(run_dir),
        "run_id": run_dir.name,
        "total": len(results),
        # §5.5.2 新键
        "metrics_ok": counts["metrics_ok"],
        "metrics_partial": counts["metrics_partial"],
        "metrics_failed": counts["metrics_failed"],
        # 旧键同时写入（deprecated）—— 兼容 docs/eval-report.md 历史趋势表与 tools/w7_backfill_*.py
        "complete": counts["metrics_ok"],
        "partial": counts["metrics_partial"],
        "failed": counts["metrics_failed"],
        "deprecated_keys": DEPRECATED_COUNT_KEYS,
        # §5.5.1 run 级质量闸（只告警不阻断）
        "verdict": verdict,
        "verdict_reasons": verdict_reasons,
        "metrics_mean": metrics_mean,
        "metrics_stderr": metrics_stderr,
        "cost_phase1_total": cost_block,
        "cost_phase2_judge_tokens": LLMClient.tokens_total,
        # W8 §10.4 第 2 条：config 快照进 summary.json（每轮都记，不再只写 baseline 一次）
        "git_commit": _prov["git_commit"],
        "git_dirty": _prov["git_dirty"],
        "git_diff_hash": _prov["git_diff_hash"],
        "config_snapshot": _prov["config_snapshot"],
        "dataset_version": meta.get("version", "unknown"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }


# ---------- CLI ----------

def _parse_thresholds(raw: Optional[str]) -> QualityThresholds:
    """解析 `--thresholds-json`（Arm 5 §5.5.1：阈值外置，改数字不必改需求文档）。"""
    if not raw:
        return DEFAULT_THRESHOLDS
    try:
        data = json.loads(raw)
    except ValueError as e:
        raise SystemExit(f"--thresholds-json 不是合法 JSON：{e}")
    if not isinstance(data, dict):
        raise SystemExit("--thresholds-json 必须是 JSON 对象")
    known = {
        "completion_rate_min", "citation_accuracy_min", "avg_steps_min", "failed_ratio_max",
    }
    unknown = sorted(set(data) - known)
    if unknown:
        raise SystemExit(f"--thresholds-json 含未知字段 {unknown}；可用字段：{sorted(known)}")
    return QualityThresholds(**data)


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
    parser.add_argument(
        "--thresholds-json",
        default=None,
        help="Arm 5 §5.5.1 质量闸阈值（JSON 对象，覆盖默认组）；"
             "字段：completion_rate_min / citation_accuracy_min / avg_steps_min / failed_ratio_max",
    )
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
        summary = phase2(
            dataset, run_dir,
            force_revalidate=args.force_revalidate,
            thresholds=_parse_thresholds(args.thresholds_json),
        )
        print(json.dumps(summary, ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    sys.exit(main())