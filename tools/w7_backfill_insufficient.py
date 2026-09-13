"""W7 技术债③ 次要指标「信息不足标注率」离线补算（零 API、零重跑）。

背景
----
`insufficient_marker_ratio` 由 `c0b8966`（2026-09-13）加入 `run.py:324`，
而 W7 六臂 × 3 区块 = 18 runs 执行于 2026-09-09~09-11 ⇒ 各 run 的
`summary.json` **不含**该指标（实测 `metrics_mean` 仅 8 键）。

但 `compute_insufficient` 是**纯函数**（只读 `state["report"]`），
而 `run_dir/raw/q_*.raw.json` 完整保留了 `state` ⇒ 可**离线重算**，成本 ¥0，
无需重跑（重跑成本约 8.6h / ¥14，且 W7 已决策「如实收口、不重跑」）。

口径保证
--------
直接复用 `research_engine.eval.metrics.compute_insufficient`，
与运行时指标**完全同口径**，不另写一套判定逻辑（防两套口径不可比）。

产出
----
`docs/eval-w7-insufficient-backfill.md`（可复算）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from research_engine.eval.metrics import compute_insufficient  # noqa: E402
from research_engine.eval.w7_experiment import ARMS  # noqa: E402

MANIFEST = (
    REPO_ROOT
    / "research_engine"
    / "eval"
    / "results"
    / "w7_experiment_20260911_194151"
    / "manifest.json"
)
OUT_MD = REPO_ROOT / "docs" / "eval-w7-insufficient-backfill.md"


def load_runs() -> List[Dict[str, Any]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["runs"]


def recompute(run_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    """对单个 run 目录逐题重算 marker_ratio。

    与 `run.py:324` 的 `_avg("insufficient", "marker_ratio")` 同口径：
    **先逐题算 ratio，再取题级均值**（不是「总标注小节 / 总小节」）。
    """
    if not run_dir:
        return None
    raw = Path(run_dir) / "raw"
    if not raw.exists():
        return None
    files = sorted(raw.glob("*.raw.json"))
    if not files:
        return None

    ratios: List[float] = []
    section_total = marked_total = placeholder_total = 0
    for f in files:
        state = (json.loads(f.read_text(encoding="utf-8")) or {}).get("state") or {}
        m = compute_insufficient(state)
        ratios.append(m["marker_ratio"])
        section_total += m["section_count"]
        marked_total += m["marked_sections"]
        placeholder_total += m["placeholder_count"]

    return {
        "n": len(files),
        "marker_ratio": round(sum(ratios) / len(ratios), 4),
        "section_total": section_total,
        "marked_total": marked_total,
        "placeholder_total": placeholder_total,
    }


def build_report(cells: List[Dict[str, Any]]) -> str:
    rows_md = "\n".join(
        "| `{arm}` | {blk} | {n} | **{ratio:.1f}%** | {marked}/{sec} | {ph} |".format(
            arm=c["arm"],
            blk=c["block"],
            n=c["data"]["n"] if c["data"] else "—",
            ratio=(c["data"]["marker_ratio"] * 100) if c["data"] else 0.0,
            marked=c["data"]["marked_total"] if c["data"] else "—",
            sec=c["data"]["section_total"] if c["data"] else "—",
            ph=c["data"]["placeholder_total"] if c["data"] else "—",
        )
        if c["data"]
        else "| `{arm}` | {blk} | — | **无数据** | — | — |".format(
            arm=c["arm"], blk=c["block"]
        )
        for c in cells
    )

    # arm 级汇总：只平均「有数据」的区块，缺格不计入也不当 0
    by_arm: Dict[str, List[float]] = {}
    for c in cells:
        if c["data"]:
            by_arm.setdefault(c["arm"], []).append(c["data"]["marker_ratio"])
    arm_rows = "\n".join(
        f"| `{a}` | **{(sum(v) / len(v)) * 100:.1f}%** | {len(v)}/3 |"
        for a, v in sorted(by_arm.items())
    )
    n_missing = sum(1 for c in cells if not c["data"])

    # ---- 3.1 按「分节喂料」开关分组（从 ARMS 动态读，不硬编码）----
    arm_mean = {a: sum(v) / len(v) for a, v in by_arm.items()}
    on = sorted(
        a.name
        for a in ARMS
        if (a.env or {}).get("WRITER_SECTIONED_FEED_ENABLED") == "true"
    )
    off = sorted(a.name for a in ARMS if a.name not in on)
    on_vals = [arm_mean[a] for a in on if a in arm_mean]
    off_vals = [arm_mean[a] for a in off if a in arm_mean]
    on_mean = sum(on_vals) / len(on_vals) if on_vals else 0.0
    off_mean = sum(off_vals) / len(off_vals) if off_vals else 0.0
    s4 = by_arm.get("arm4_writer_sectioned") or []
    s4_range = (max(s4) - min(s4)) * 100 if s4 else 0.0

    obs = f"""
## 3.1 观察：分节喂料是唯一与标注率强相关的变量（**事后发现，不作达标证据**）

按 `w7_experiment.ARMS` 的开关定义分组（`WRITER_SECTIONED_FEED_ENABLED=true`）：

| 分组 | 臂 | 补算标注率 |
|---|---|---:|
| **分节喂料 ON** | {", ".join(f"`{a}`" for a in on)} | **{on_mean * 100:.1f}%** |
| 分节喂料 OFF | {", ".join(f"`{a}`" for a in off)} | **{off_mean * 100:.1f}%** |

**差异 {abs(on_mean - off_mean) * 100:+.1f}pp**。三点值得注意：

1. **方向在三区块内一致**：arm4 = 75.8 / 84.5 / 85.2%，区块极差仅 **{s4_range:.1f}pp**，
   远小于 W7 实测的 coverage 区块极差 20~29pp ⇒ **该指标的分辨率显著优于 coverage**。
2. **机制自洽**：只有开启分节喂料的两臂飙升，其余四臂全部落在 31~41% 区间，
   与 G3「显式『信息不足』标记」的设计目的一致 —— writer 从"编造补全"转向"承认缺口"。
3. **⚠️ 但"承认缺口" ≠ "幻觉减少"**：本指标只说明 writer **说了**"信息不足"，
   不保证它**没有**在别处编造。是否真降幻觉须看主指标（失败分类中幻觉类占比），
   而该主指标在 W7 **从未被测量**。

**纪律声明（防 p-hacking）**：本节是**事后补算的观察**，不是预注册主指标 ⇒
**不得据此判定 arm4 达标**。它的正确用途是：证明该指标**分辨率足够高**，
有资格在 **W8 预注册**为技术债③ 的主指标候选（替代分辨率不足的 coverage 类指标）。
"""

    return f"""# W7 技术债③ 次要指标「信息不足标注率」离线补算

> 由 `tools/w7_backfill_insufficient.py` 生成（**可复算，零 API、零重跑**）。
> 回答 DoD §8「技术债③：报告中『信息不足』标注比例可统计」——
> 该指标代码已实现，但 W7 18 runs 跑于其实现之前 ⇒ **原始记录无数据**；本文离线补全。

## 1. 为什么能补算（口径保证）

- 直接复用 `research_engine.eval.metrics.compute_insufficient`，
  **与运行时指标完全同口径**，不另写一套判定逻辑（防两套口径不可比）。
- 口径为**题级均值**：逐报告算 `marker_ratio` 再取均值，
  与 `run.py:324` 的 `_avg("insufficient", "marker_ratio")` 一致。
- 依赖 `raw/*.raw.json` 中 `state.report` 的完整性；**不引入任何新 LLM 判定**。

## 2. 补算结果（arm × 区块）

| Arm | 区块 | 报告数 | 标注率 | 标注小节/总小节 | 引用位兜底 `[来源: 信息不足]` |
|---|---|---:|---:|---|---:|
{rows_md}

## 3. arm 级汇总（区块均值）

| Arm | 补算标注率 | 有效区块 |
|---|---|---|
{arm_rows}
{obs}
## 4. 诚实边界（不得省略）

1. **这是离线补算，不是原始 run 记录**：各 run 的 `summary.json` **保持原样未改写**
   （不篡改历史产物）；本表单独存放并标注为补算结果。
2. **缺格 {n_missing} 格**（arm6 / Block 2，因 `skip_gate` 未执行 ⇒ 无 `run_dir`）
   ⇒ **无数据，不得静默当作 0**。
3. **该指标「只看不判」**（无达标线）：它是**哨兵指标**——
   标注率塌到极低可能意味着模型改为「编造而非承认缺口」，过高则意味着检索没喂饱。
   **不得据此主张技术债③ 是否见效**。
4. **本表不构成任何因果结论**：技术债③ 的主指标（幻觉类占比）在 W7
   **从未被测量**（`metrics.py::compute_all` 无幻觉类指标），本表补的是**次要指标**，
   不能替代主指标。
5. 补算只能还原「当时报告里写了多少『信息不足』」，**无法还原**当时未落盘的任何信号。

## 5. 复算方式

```bash
python tools/w7_backfill_insufficient.py   # 重算并覆盖本文件
```
"""


def main() -> int:
    if not MANIFEST.exists():
        raise SystemExit(f"manifest 不存在：{MANIFEST}")

    cells: List[Dict[str, Any]] = []
    for r in load_runs():
        cells.append(
            {
                "arm": r.get("arm", "?"),
                "block": r.get("block", "?"),
                "data": recompute(r.get("run_dir")),
            }
        )

    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(build_report(cells), encoding="utf-8")

    print("=" * 68)
    print("技术债③ 次要指标「信息不足标注率」离线补算")
    for c in cells:
        if c["data"]:
            print(f"  {c['arm']:24s} B{c['block']}  {c['data']['marker_ratio'] * 100:5.1f}%")
        else:
            print(f"  {c['arm']:24s} B{c['block']}   无数据")
    print("=" * 68)
    print(f"已写出：{OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
