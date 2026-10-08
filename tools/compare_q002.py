"""q_002 对照（需求 28 复算资产）：A4 前（mini 锚点基线）vs A4 后（新 run）。

口径（与需求 28 §4.1 一致）：
- budget_rejected = degradation_log 中 node=validator 且 detail 含 budget 的条目数
- 未校验 = citations 中 verification_failed=True（校验未完成：饥饿/失败）
- 未通过 = citations 中 verified=False（含判据拒绝）

用法（仓库根目录执行）：
  python tools/compare_q002.py --base <基线 raw/q_002.raw.json> --new <新 raw/q_002.raw.json>
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path


def summarize(raw_path: Path, label: str) -> dict:
    data = json.loads(raw_path.read_text(encoding="utf-8"))
    state = data["state"]
    degradations = state.get("degradation_log") or []
    cites = state.get("citations") or []
    stats = state.get("validator_stats") or {}
    token_used = data.get("token_used", state.get("token_used"))
    budget_rejected = sum(
        1
        for d in degradations
        if d.get("node") == "validator" and "budget" in json.dumps(d, ensure_ascii=False).lower()
    )
    return {
        "指标": label,
        "token": token_used,
        "cites": len(cites),
        "未校验": sum(1 for c in cites if c.get("verification_failed")),
        "未通过": sum(1 for c in cites if not c.get("verified")),
        "budget_rejected": budget_rejected,
        "llm_error": sum(1 for d in degradations if d.get("reason") == "llm_error"),
        "starved": stats.get("unverified_starved_count", "n/a(旧代码)"),
        "rejected": stats.get("verified_rejected_count", "n/a(旧代码)"),
        "commit": (data.get("git_commit") or "")[:8],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--base", type=Path, required=True, help="基线 raw（A4 前，如 run_20261008_003828）")
    parser.add_argument("--new", type=Path, required=True, help="对比 raw（A4 后，如 run_20261008_024805）")
    args = parser.parse_args()

    keys = ["指标", "token", "cites", "未校验", "未通过", "budget_rejected", "llm_error", "starved", "rejected", "commit"]
    for raw_path, label in ((args.base, "基线 A3(shard=0)"), (args.new, "对比 A4(shard=1)")):
        row = summarize(raw_path, label)
        print("  ".join(f"{k}={row[k]}" for k in keys))


if __name__ == "__main__":
    main()
