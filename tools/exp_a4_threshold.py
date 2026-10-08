"""A4 阈值实验（需求 28 复算资产）：固定基线 q_002 的重型数据，只切
DR_VALIDATE_SHARD_CONTEXT / DR_VALIDATE_BATCH_SIZE 与 state.token_used，
用真实 LLM 跑一轮 Validator.validate，测「校验跑完所需的剩余预算」。

数据来源（本地复算资产，见 docs/requirements/28-validator-sharded-context.md §12）：
  ...\\opencode\\dr-mini\\research_engine\\eval\\results\\run_20261008_003828\\raw\\q_002.raw.json

用法（仓库根目录执行；会真实调用 LLM，逐组跑一次校验，共 len(cases) 次）：
  python tools/exp_a4_threshold.py --raw <q_002.raw.json 路径>
  python tools/exp_a4_threshold.py --raw <...> --budget 200000
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from dotenv import load_dotenv  # noqa: E402

load_dotenv(REPO_ROOT / ".env", override=False)

from research_engine.agents.validator import Validator  # noqa: E402
from research_engine.state import ResearchFinding, ResearchState  # noqa: E402

# (shard, batch, token_used)；剩余预算 = budget - token_used
# 前两组为 q_002 真实终局（剩余 1,517）的分片/现状对照；后五组为 30k/40k/50k 与批 32/48 梯度
DEFAULT_CASES: list[tuple[str, str, int]] = [
    ("1", "16", 198_483),
    ("0", "16", 198_483),
    ("1", "16", 170_000),
    ("1", "16", 160_000),
    ("1", "16", 150_000),
    ("1", "32", 150_000),
    ("1", "48", 150_000),
]


def load_base(raw_path: Path):
    """从基线 run 的 raw json 还原 report / findings / working_findings。"""
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    state = data["state"]
    report = state["report"]
    findings = [ResearchFinding.model_validate(f) for f in state["findings"]]
    working = [ResearchFinding.model_validate(f) for f in state["working_findings"]]
    topic = state.get("topic") or "2026年 RAG 技术的主要进展有哪些"
    return report, findings, working, topic


def run_case(shard: str, batch: str, token_used: int, report, findings, working, topic, budget: int) -> dict:
    os.environ["DR_VALIDATE_SHARD_CONTEXT"] = shard
    os.environ["DR_VALIDATE_BATCH_SIZE"] = batch
    os.environ["DR_VALIDATE_CONCURRENCY"] = "3"
    state = ResearchState(topic=topic, findings=findings, working_findings=working, token_used=token_used)
    validator = Validator()
    before = state.token_used
    cites = validator.validate(report, working, state, evidence=findings)
    degradations = validator.drain_degradations()
    budget_rejected = sum(1 for d in degradations if "budget" in (d.detail or "").lower())
    return {
        "shard": shard,
        "batch": batch,
        "剩余预算": budget - before,
        "实际消耗": state.token_used - before,
        "cites": len(cites),
        "未校验": sum(1 for c in cites if c.verification_failed),
        "budget_rejected": budget_rejected,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--raw", type=Path, required=True, help="基线 run 的 raw/q_002.raw.json 路径")
    parser.add_argument("--budget", type=int, default=200_000, help="token 预算总量（默认 200000）")
    args = parser.parse_args()

    report, findings, working, topic = load_base(args.raw)
    print(f"基线数据：report={len(report)}字符 findings={len(findings)} working={len(working)}")
    header = ("shard", "batch", "剩余预算", "实际消耗", "cites", "未校验", "budget_rejected")
    print("  ".join(header))
    for shard, batch, used in DEFAULT_CASES:
        row = run_case(shard, batch, used, report, findings, working, topic, args.budget)
        print("  ".join(str(row[k]) for k in header))


if __name__ == "__main__":
    main()
