"""W7 TBD-8 技术债对照实验 Runner。

设计目标：在统一代码版本下，通过环境变量开关独立启用/禁用各 W7 arm，
隔离 critic gap / validator fixes / writer sectioned feed / validator trim 的边际贡献。

检索环境：真实 Qdrant（QDRANT_URL=http://127.0.0.1:6333），RAG 检索可用。
（2026-09-12 修正：原文写「强制 :memory:，RAG 检索降级为空」与代码事实相反。）

⚠️ 解释效力提醒（2026-09-12 复查）：
    1. 引用准确率的裁判 = 主链路 validator 自己（metrics.py:5 直读 state.citations），
       而 arm6 把 validator 换成 qwen-turbo ⇒ 「被测兼裁判」。跨裁判的 citation 比较
       不成立，需用 w7_rejudge.py 做固定裁判复判。
    2. 单区块无法分离 arm 效应与时间漂移（实测同 arm 跨区块 citation 波动 ±8~12pp），
       区块数建议 >=3，且必须跑满 `--runs` 才有解释力。

用法：
    python -m research_engine.eval.w7_experiment --runs 3 --concurrency 3
    # 断点续跑：从第 2 个区块接着跑（0-based）
    python -m research_engine.eval.w7_experiment --runs 3 --start-block 1
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

RESULTS_DIR = Path(__file__).resolve().parent / "results"
DATASET = Path(__file__).resolve().parent / "dataset.jsonl"


@dataclass
class Arm:
    name: str
    env: Dict[str, str]
    description: str
    condition: Optional[str] = None
    # 机器可读的守门条件（2026-09-12 修复：原 condition 只是文档字符串，
    # main() 从不检查它 ⇒ arm6 在 arm3 未达标时仍被执行，违反自身实验协议）
    gate: Optional[Dict[str, object]] = None


# 注意：config.py 中所有开关默认 true；要得到 v1.1-like 基线，必须显式把其余开关置 false。
ARMS: List[Arm] = [
    Arm(
        name="arm0_baseline",
        env={
            "CRITIC_GAP_ENABLED": "false",
            "VALIDATOR_FIXES_ENABLED": "false",
            "WRITER_SECTIONED_FEED_ENABLED": "false",
            "VALIDATOR_TRIM_ENABLED": "false",
            "VALIDATOR_MODEL": "",
        },
        description="v1.1-like 基线：所有 W7 arm 关闭",
    ),
    Arm(
        name="arm1_critic_gap",
        env={
            "CRITIC_GAP_ENABLED": "true",
            "VALIDATOR_FIXES_ENABLED": "false",
            "WRITER_SECTIONED_FEED_ENABLED": "false",
            "VALIDATOR_TRIM_ENABLED": "false",
            "VALIDATOR_MODEL": "",
        },
        description="仅启用 Critic knowledge_gap 硬规则（TBD-1 Step1）",
    ),
    Arm(
        name="arm3_validator_fixes",
        env={
            "CRITIC_GAP_ENABLED": "false",
            "VALIDATOR_FIXES_ENABLED": "true",
            "WRITER_SECTIONED_FEED_ENABLED": "false",
            "VALIDATOR_TRIM_ENABLED": "false",
            "VALIDATOR_MODEL": "",
        },
        description="仅启用 Validator 五项修复 + 双口径（TBD-4/TBD-5）",
    ),
    Arm(
        name="arm4_writer_sectioned",
        env={
            "CRITIC_GAP_ENABLED": "false",
            "VALIDATOR_FIXES_ENABLED": "false",
            "WRITER_SECTIONED_FEED_ENABLED": "true",
            "VALIDATOR_TRIM_ENABLED": "false",
            "VALIDATOR_MODEL": "",
        },
        description="仅启用 Writer 分节喂料（TBD-6）",
    ),
    Arm(
        name="arm5_validator_trim",
        env={
            "CRITIC_GAP_ENABLED": "false",
            "VALIDATOR_FIXES_ENABLED": "false",
            "WRITER_SECTIONED_FEED_ENABLED": "false",
            "VALIDATOR_TRIM_ENABLED": "true",
            "VALIDATOR_MODEL": "",
        },
        description="仅启用 Validator 喂料裁剪 A'（TBD-7）",
    ),
    Arm(
        name="arm6_validator_turbo",
        env={
            "CRITIC_GAP_ENABLED": "true",
            "VALIDATOR_FIXES_ENABLED": "true",
            "WRITER_SECTIONED_FEED_ENABLED": "true",
            "VALIDATOR_TRIM_ENABLED": "true",
            "VALIDATOR_MODEL": "qwen-turbo",
        },
        description="所有 W7 arm 开启 + Validator 降档 qwen-turbo（TBD-7 C，条件触发）",
        condition="仅在 Arm3 达标后执行，守门线 citation_accuracy >= 71.0%",
        gate={"arm": "arm3_validator_fixes", "metric": "citation_accuracy",
              "op": ">=", "value": 0.71},
    ),
]


def _run_eval(arm: Arm, run_index: int, concurrency: int, timeout_s: float, dataset_path: Path) -> Path:
    """为单个 arm 跑一次完整 eval（Phase1+Phase2），返回 run_dir。"""
    env = os.environ.copy()
    # 使用真实 Qdrant（已验证 127.0.0.1:6333 可用）；如需降级改 :memory:
    env["QDRANT_URL"] = "http://127.0.0.1:6333"
    env["LANGFUSE_ENABLED"] = "false"
    env.update({k: v for k, v in arm.env.items() if v})
    # VALIDATOR_MODEL 为空时移除，让代码使用默认回落
    if "VALIDATOR_MODEL" in env and not env["VALIDATOR_MODEL"]:
        env.pop("VALIDATOR_MODEL")

    python_exe = sys.executable
    cmd = [
        python_exe,
        "-u",  # 子进程也走无缓冲，避免被 kill 时丢日志（2026-09-12）
        "-m",
        "research_engine.eval.run",
        "--dataset",
        str(dataset_path),
        "--concurrency",
        str(concurrency),
        "--timeout-s",
        str(timeout_s),
    ]
    print(f"\n{'='*60}")
    print(f"启动 {arm.name} 第 {run_index+1} 轮")
    print(f"描述：{arm.description}")
    print(f"环境：{ {k: env.get(k) for k in arm.env.keys()} }")
    print(f"命令：{' '.join(cmd)}")
    print("=" * 60)

    t0 = time.time()
    proc = subprocess.run(
        cmd,
        env=env,
        cwd=str(Path(__file__).resolve().parent.parent.parent),
        text=True,
        encoding="utf-8",
    )
    elapsed = time.time() - t0
    print(f"{arm.name} 第 {run_index+1} 轮完成，耗时 {elapsed/60:.1f} min，返回码 {proc.returncode}")

    # 解析最新 run_dir：按创建时间取最新（避免 run_v11_compare 因 ASCII 排序始终排后被误选）
    runs = [p for p in RESULTS_DIR.iterdir() if p.is_dir() and p.name.startswith("run_")]
    if not runs:
        raise RuntimeError(f"{arm.name} 第 {run_index+1} 轮未生成 run_ 目录")
    latest = max(runs, key=lambda p: p.stat().st_mtime)
    return latest


def _load_summary(run_dir: Path) -> Optional[Dict]:
    p = run_dir / "summary.json"
    if not p.exists():
        return None
    return json.loads(p.read_text(encoding="utf-8"))


def _extract_key_metrics(summary: Optional[Dict]) -> Dict[str, float]:
    if summary is None:
        return {}
    mm = summary.get("metrics_mean", {})
    return {
        "completion_rate": mm.get("completion_rate", 0.0),
        "citation_accuracy": mm.get("citation_accuracy", 0.0),
        "citation_accuracy_relaxed": mm.get("citation_accuracy_relaxed", 0.0),
        "existence_rate": mm.get("existence_rate", 0.0),
        "coverage": mm.get("coverage", 0.0),
        "retrieval_hit_rate": mm.get("retrieval_hit_rate", 0.0),
        "avg_steps": mm.get("avg_steps", 0.0),
        "reflection_critic_stop_rate": mm.get("reflection_critic_stop_rate", 0.0),
        "cost_yuan": summary.get("cost_phase1_total", {}).get("cost_yuan", 0.0),
        "cost_phase1_tokens": summary.get("cost_phase1_total", {}).get("total_tokens", 0),
        "cost_phase2_tokens": summary.get("cost_phase2_judge_tokens", 0),
    }


def _gate_satisfied(arm: Arm, manifest: Dict, block_index: int) -> tuple[bool, str]:
    """判定条件臂的守门条件是否满足（2026-09-12 新增，原为只写不查的文档字段）。

    优先用「同一区块」内被依赖 arm 的结果（配对可比）；该区块尚无结果时回落
    到全部已完成 run 的均值。无任何可用数据时判为不满足（保守）。
    """
    gate = arm.gate
    if not gate:
        return True, ""
    dep, metric = str(gate["arm"]), str(gate["metric"])
    value = float(gate["value"])  # type: ignore[arg-type]
    done = [r for r in manifest["runs"]
            if r["arm"] == dep and r.get("status") == "done" and metric in r.get("metrics", {})]
    same_block = [r for r in done if r.get("block") == block_index]
    pool = same_block or done
    if not pool:
        return False, f"守门 arm {dep} 尚无 {metric} 数据"
    vals = [float(r["metrics"][metric]) for r in pool]
    cur = sum(vals) / len(vals)
    scope = "同区块" if same_block else f"全量 {len(vals)} 轮"
    ok = cur >= value
    return ok, f"{dep} {metric}={cur:.4f}（{scope}）{'≥' if ok else '<'} 守门线 {value}"


def main() -> int:
    # 行缓冲：被 kill 时也能留下已打印的轨迹（原事故：块缓冲 + kill ⇒ 日志全丢）
    try:
        sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
    except Exception:  # noqa: BLE001
        pass

    parser = argparse.ArgumentParser(description="W7 TBD-8 技术债对照实验")
    parser.add_argument("--runs", type=int, default=3, help="每 arm 跑几轮（默认 3）")
    parser.add_argument("--concurrency", type=int, default=3, help="Phase1 并发数（默认 3）")
    parser.add_argument("--timeout-s", type=float, default=900, help="单题超时秒数（默认 900）")
    parser.add_argument("--skip-conditional", action="store_true", help="跳过 Arm6（降档）条件臂")
    parser.add_argument("--force-conditional", action="store_true",
                        help="无视守门条件强制执行条件臂（会在 manifest 留痕）")
    parser.add_argument("--arms", type=str, default="", help="只跑指定 arm，逗号分隔，如 arm0_baseline,arm1_critic_gap")
    parser.add_argument("--dataset", type=Path, default=DATASET, help="指定 dataset 路径（默认 20 题完整集）")
    parser.add_argument("--start-block", type=int, default=0,
                        help="断点续跑：从第 N 个区块开始（0-based，默认 0）")
    parser.add_argument("--experiment-dir", type=Path, default=None,
                        help="复用已有实验目录续跑（默认新建）")
    args = parser.parse_args()

    arms = ARMS
    if args.arms:
        allowed = {a.name for a in arms}
        selected = [s.strip() for s in args.arms.split(",")]
        for s in selected:
            if s not in allowed:
                raise SystemExit(f"未知 arm：{s}，可用：{allowed}")
        arms = [a for a in arms if a.name in selected]
    if args.skip_conditional:
        arms = [a for a in arms if a.name != "arm6_validator_turbo"]

    dataset_path = args.dataset
    if not dataset_path.exists():
        raise SystemExit(f"dataset 不存在：{dataset_path}")

    RESULTS_DIR.mkdir(parents=True, exist_ok=True)

    if args.experiment_dir:
        experiment_dir = args.experiment_dir
        if not experiment_dir.exists():
            raise SystemExit(f"续跑目录不存在：{experiment_dir}")
        manifest_path = experiment_dir / "manifest.json"
        if not manifest_path.exists():
            raise SystemExit(f"续跑目录缺少 manifest.json：{experiment_dir}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.setdefault("runs", [])
        manifest["resumed_at"] = datetime.now().isoformat()
        print(f"[续跑] 复用实验目录 {experiment_dir}（已有 {len(manifest['runs'])} 条 run）")
    else:
        experiment_dir = RESULTS_DIR / f"w7_experiment_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        experiment_dir.mkdir(parents=True, exist_ok=True)
        manifest_path = experiment_dir / "manifest.json"
        manifest = {
            "started_at": datetime.now().isoformat(),
            "arms": [a.name for a in arms],
            "runs_per_arm": args.runs,
            "concurrency": args.concurrency,
            "timeout_s": args.timeout_s,
            "notes": [
                "QDRANT_URL=http://127.0.0.1:6333（真实 Qdrant，RAG 检索可用）",
                f"区块配对设计：{args.runs} blocks × {len(arms)} arms，每 block 内所有 arm 紧挨着跑，",
                "同一 block 内 arm 间可比（控制检索漂移 / API 质量随时间波动）",
                "⚠️ 引用准确率的裁判 = 主链路 validator（metrics.py:5）；arm6 换裁判 ⇒ 跨裁判的 citation 不可直接比较，需 w7_rejudge.py 复判。",
            ],
            "runs": [],
        }

    total_start = time.time()
    # 区块配对设计：每个区块内依次跑全部 arm，确保同一时间窗口内 arm 间可比
    for block_index in range(args.start_block, args.runs):
        manifest["notes"].append(f"Block {block_index}: 开始于 {datetime.now().isoformat()}")
        print(f"\n{'#'*60}")
        print(f"# Block {block_index + 1}/{args.runs}")
        print(f"{'#'*60}")
        for arm in arms:
            # 守门条件检查（原为「只写不查」的文档字段，2026-09-12 修复）
            if arm.gate and not args.force_conditional:
                ok, detail = _gate_satisfied(arm, manifest, block_index)
                if not ok:
                    print(f"⏭ 跳过 {arm.name}：守门条件未满足 — {detail}")
                    manifest["runs"].append({
                        "arm": arm.name, "run_index": block_index, "block": block_index,
                        "run_dir": None, "metrics": {}, "status": "skipped_gate",
                        "gate_detail": detail,
                    })
                    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")
                    continue
                print(f"✅ {arm.name} 守门条件满足 — {detail}")
            elif arm.gate and args.force_conditional:
                print(f"⚠️ {arm.name} 被 --force-conditional 强制执行（守门条件不作数，manifest 留痕）")

            run_dir = _run_eval(arm, block_index, args.concurrency, args.timeout_s, dataset_path)
            summary = _load_summary(run_dir)
            entry = {
                "arm": arm.name,
                "run_index": block_index,
                "block": block_index,
                "run_dir": str(run_dir),
                "metrics": _extract_key_metrics(summary),
                "status": "done" if summary else "no_summary",
                **({"forced_conditional": True} if (arm.gate and args.force_conditional) else {}),
            }
            manifest["runs"].append(entry)
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    manifest["finished_at"] = datetime.now().isoformat()
    manifest["total_wall_minutes"] = round((time.time() - total_start) / 60, 1)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=1), encoding="utf-8")

    print(f"\n实验完成，总耗时 {manifest['total_wall_minutes']} min")
    print(f"manifest：{manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
