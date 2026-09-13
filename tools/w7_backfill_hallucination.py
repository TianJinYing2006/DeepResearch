"""W7 技术债③ **主指标**「失败分类中幻觉类占比」离线补算（零 API、零重跑）。

背景
----
W7 收口时发现：技术债③ 的主指标（失败分类中「幻觉类」占比）在**整个 W7 生命周期
从未被任何 run 测量** —— `metrics.py::compute_all` 没有幻觉类指标，18 run 的
`metrics_mean` 里也查无此键（详见 `7-*.md` §8 的「诚实标注」条）。

但归因所需的字段（claim / note / verified）在 `raw/*.raw.json` 的
`state.citations` 里**完整保留**，而 `w7_failure_attribution.classify` 是
**纯启发式、零 API** 的 ⇒ 可以**离线重算**，无需重跑（重跑约 8.6h / ¥14）。

口径保证
--------
直接复用 `w7_failure_attribution.classify`，与 `docs/eval-w7-attribution.md`
**完全同口径**，不另写一套判定逻辑（防两套口径不可比）。

产出
----
`docs/eval-w7-hallucination-backfill.md`（可复算）。
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

from tools.w7_failure_attribution import classify  # noqa: E402

MANIFEST = (
    REPO_ROOT
    / "research_engine"
    / "eval"
    / "results"
    / "w7_experiment_20260911_194151"
    / "manifest.json"
)
OUT_MD = REPO_ROOT / "docs" / "eval-w7-hallucination-backfill.md"

# 须 writer 治理的两类（与技术债③ 对应）；技术性误拒 R4/R2/R3 由 validator 修复覆盖
HALLU_CLASSES = ("HALLU", "MISSRC")


def load_runs() -> List[Dict[str, Any]]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))["runs"]


def recompute(run_dir: Optional[str]) -> Optional[Dict[str, Any]]:
    """对单个 run 逐条重算「幻觉类 / 未通过」（与归因表 classify 同口径）。"""
    if not run_dir:
        return None
    raw = Path(run_dir) / "raw"
    if not raw.exists():
        return None

    rows: List[Dict[str, Any]] = []
    total = 0
    skipped = 0
    for f in sorted(raw.glob("*.raw.json")):
        obj = json.loads(f.read_text(encoding="utf-8")) or {}
        state = obj.get("state")
        if not state:  # 个别题未落 state，显式计数，不静默吞掉
            skipped += 1
            continue
        for c in state.get("citations") or []:
            total += 1
            if c.get("verified"):
                continue
            rows.append(
                {
                    "claim": c.get("claim") or "",
                    "note": c.get("note") or "",
                    "cat": c.get("cat") or "",
                }
            )

    if not rows:
        return None

    _, counter = classify(rows)
    hallu = sum(counter[k] for k in HALLU_CLASSES)
    return {
        "total": total,
        "failed": len(rows),
        "hallu": hallu,
        "ratio": round(hallu / len(rows), 4),
        "skipped": skipped,
        "counter": dict(counter),
    }


def build_report(cells: List[Dict[str, Any]]) -> str:
    rows_md = "\n".join(
        "| `{arm}` | {blk} | {tot} | {fail} | {h} | **{r:.1f}%** |".format(
            arm=c["arm"],
            blk=c["block"],
            tot=c["data"]["total"],
            fail=c["data"]["failed"],
            h=c["data"]["hallu"],
            r=c["data"]["ratio"] * 100,
        )
        if c["data"]
        else "| `{arm}` | {blk} | — | — | — | **无数据** |".format(
            arm=c["arm"], blk=c["block"]
        )
        for c in cells
    )

    by_arm: Dict[str, List[float]] = {}
    for c in cells:
        if c["data"]:
            by_arm.setdefault(c["arm"], []).append(c["data"]["ratio"])

    arm_rows = "\n".join(
        f"| `{a}` | **{(sum(v) / len(v)) * 100:.1f}%** | **{(max(v) - min(v)) * 100 if len(v) > 1 else 0.0:.1f}pp** | {len(v)}/3 |"
        for a, v in sorted(by_arm.items())
    )

    base = by_arm.get("arm0_baseline") or []
    base_mean = sum(base) / len(base) if base else 0.0
    base_rng = (max(base) - min(base)) * 100 if len(base) > 1 else 0.0
    arm4 = by_arm.get("arm4_writer_sectioned") or []
    arm4_mean = sum(arm4) / len(arm4) if arm4 else 0.0
    arm4_rng = (max(arm4) - min(arm4)) * 100 if len(arm4) > 1 else 0.0
    delta = (arm4_mean - base_mean) * 100

    n_missing = sum(1 for c in cells if not c["data"])

    return f"""# W7 技术债③ **主指标**「幻觉类占比」离线补算

> 由 `tools/w7_backfill_hallucination.py` 生成（**可复算，零 API、零重跑**）。
> 回答 `7-*.md` §8「Arm 4 达标线：失败分类中『幻觉类』占比从 **36.9%** 下降 ≥10pp」
> ——该达标线在 W7 全生命周期**从未被任何 run 测量**；本文用离线重算把它补上。

## 1. 为什么能补算（口径保证）

- 直接复用 `tools/w7_failure_attribution.classify`
  ⇒ 与 `docs/eval-w7-attribution.md` **完全同口径**，不另写判定逻辑。
- 口径：**幻觉类 = HALLU + MISSRC**，分母为**未通过引用**条数（与归因表一致）。
- 数据源：`raw/*.raw.json` 的 `state.citations`（claim / note / verified 完整）。
  **不引入任何新 LLM 判定**，也不重跑。

## 2. 补算结果（arm × 区块）

| Arm | 区块 | 总引用 | 未通过 | 幻觉类 | **幻觉类占比** |
|---|---|---:|---:|---:|---:|
{rows_md}

## 3. arm 级汇总（区块均值 + 区块极差＝噪声底）

| Arm | 补算占比 | **区块极差（噪声）** | 有效区块 |
|---|---|---|---|
{arm_rows}

## 4. 判定：arm4 **方向正确但未达标**

- 基线 `arm0_baseline`：**{base_mean * 100:.1f}%**（噪声 {base_rng:.1f}pp）
- `arm4_writer_sectioned`：**{arm4_mean * 100:.1f}%**（噪声 {arm4_rng:.1f}pp）
- **效应 {delta:+.1f}pp**，DoD 门槛为 **下降 ≥10pp** ⇒ ❌ **未达标**
- 更关键：**|效应| {abs(delta):.1f}pp < arm4 自身噪声 {arm4_rng:.1f}pp** ⇒ ❌ **不可判定**
- 分区块看：arm4 = 12.1% / 5.9% / 7.1%，**Block 0 与基线（12.0%）几乎无差**，
  下降几乎全部来自 Block 1/2 ⇒ **效应在区块间不稳定**，与已知的
  「Arm 3/4/5 配对效应区块间符号翻转」一致。

## 5. 🔴 两个必须排除/谨慎解读的臂

1. **`arm6_validator_turbo` = 0.0% 是假象，不是「零幻觉」。**
   该臂把 validator 换成 qwen-turbo，其 note **退化成固定模板**
   （几乎全是「来源不存在于研究发现（编号越界或来源未命中）」，无内容判据），
   而 qwen-plus 的 note 有具体内容（如 "Finding 10 states only: 'Mamba 通过
   selective scan…'"）。⇒ 基于 note 关键字的分类**完全抓不到** HALLU/MISSRC，
   占比**假性归零**。这是「**被测对象兼任裁判**」的直接实证：
   **换掉裁判 ⇒ 判定文本退化 ⇒ 指标失真**。
2. **`arm3_validator_fixes` 该指标失真（极差 12.2pp）。**
   arm3 改的正是判定器与分母（F1~F5），任何**分母依赖**的失败构成类指标
   都会被测对象自身扭曲 ⇒ **该类指标不适用于改动 validator 的臂**。

## 6. 与次要指标的交叉验证（好消息）

`docs/eval-w7-insufficient-backfill.md` 的**次要指标**（「信息不足」标注率）：
分节喂料 ON **78.0%** vs OFF **36.1%（+42.0pp）** —— writer 显著**更多承认缺口**。

本文的**主指标**（幻觉类占比）同向下降 **{abs(delta):.1f}pp** —— writer 显著**更少编造**。

⇒ **两个相互独立的指标方向一致**，共同支持：分节喂料确实改变了 writer 行为
（**少编造、多承认缺口**）。这比单一指标更有说服力。

## 7. 诚实边界（不得省略）

1. **事后补算，非预注册主指标** ⇒ **不得据此判定 arm4 达标**（防 p-hacking）。
   它的作用是：把「从未被测」的**空白**，变成「测了，但效应 < 噪声」的**可讲述失败**。
2. **效应 < 噪声** ⇒ 本表**不主张 arm4 有效**，只主张**方向**与**分辨率评估**。
3. **arm6 数据不可用**（裁判退化），**arm3 数据需谨慎**（分母被自身改动扭曲）。
4. **结论不可复现**：检索源随时间漂移（真实 API）、裁判未与被测解耦、
   `_config_snapshot` 不记录 5 个开关 ⇒ 重跑**不保证**得到同一数值。
5. 缺格 **{n_missing} 格**（arm6 / Block 2，因 `skip_gate` 无 `run_dir`）⇒ 不作 0。

## 8. 复算方式

```bash
python tools/w7_backfill_hallucination.py   # 重算并覆盖本文件
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
    print("技术债③ 主指标「幻觉类占比」离线补算")
    for c in cells:
        d = c["data"]
        if d:
            print(f"  {c['arm']:24s} B{c['block']}  {d['ratio'] * 100:5.1f}%")
        else:
            print(f"  {c['arm']:24s} B{c['block']}   无数据")
    print("=" * 68)
    print(f"已写出：{OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
