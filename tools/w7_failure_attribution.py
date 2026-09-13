# -*- coding: utf-8 -*-
"""W7 技术债② 失败分类归因表（DoD §8「失败分类（5 类）归因表产出，可解释提升来源」）。

数据源：`.workbuddy/diag/failed_citations.jsonl`（298 条未通过引用，来自 `run_v11_compare`）。
零 API、零 LLM：只读落盘产物 + 重放现行本地代码（`Validator._is_assertive`）。

产出：`docs/eval-w7-attribution.md`（markdown 归因表，可复算）。

诚实边界（务必随表呈现）：
- 归类是**可复核的启发式规则**，不是对 298 条逐条人工判读；互斥归类，可加总为 298；
- **R1（verdict/claim 错位）无法回溯度量**——判别字段 `claim_echo` 是 F2 修复时才引入的，
  旧产物里不存在；旧数据仅有 Jaccard 字面重合度，实测区分度不足（sim≤0.05 覆盖 80.9%），
  故 R1 只作为「已实现但不可回溯度量」登记，不参与计数；
- 修复上限是**算术上界**（假设修复完全生效且不产生新失败），不是效果承诺。
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

FIXTURE = REPO_ROOT / ".workbuddy" / "diag" / "failed_citations.jsonl"
OUT_MD = REPO_ROOT / "docs" / "eval-w7-attribution.md"

# 诊断期基线（docs/requirements/7-technical-debt-and-content.md §5.2 表）
TOTAL_CITATIONS = 1066
VERIFIED = 768

# 类别键 -> (中文名, 根因, 对应修复, 修复机制)
CLASSES: dict[str, tuple[str, str, str, str]] = {
    "R4": (
        "结构残片被当论断校验",
        "markdown 表格行/列表项残片进入校验",
        "**F3**",
        "`_is_assertive` 过滤：含 `|`、`#`/`-` 开头、元话语开头者不进校验（不产生引用记录）",
    ),
    "R2": (
        "claim 截断",
        "`claim[:100]` 截断后送 LLM，基于残缺论断判「不忠实」",
        "**F1**",
        "送 LLM 不再截断（claim 最长 195，可整送）",
    ),
    "R3": (
        "有依据但引错编号",
        "`supported=true` 未计入 `verified`",
        "**TBD-5**",
        "双口径呈现 `verified_relaxed`；**不改严格口径语义**",
    ),
    "HALLU": (
        "真幻觉（明确判虚构/无依据）",
        "writer 编造具体数字/术语/结论",
        "—",
        "**F1~F5 修不了** ⇒ 技术债③（writer 分节喂料 G1~G5）",
    ),
    "MISSRC": (
        "来源未含该信息 / 来源不存在",
        "引对编号但来源未提，或编号越界/来源编造",
        "—",
        "**F1~F5 修不了** ⇒ 技术债③",
    ),
    "OTHER": (
        "其他不忠实（无明确判据）",
        "混杂：含 R1/R5 残余影响与判定噪声",
        "—",
        "R1 由 **F2**（`claim_echo` 回显对齐）覆盖，但旧产物无法回溯计数",
    ),
}


def load_fixture() -> list[dict]:
    if not FIXTURE.exists():
        raise SystemExit(f"夹具不存在：{FIXTURE}")
    return [
        json.loads(line)
        for line in FIXTURE.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def classify(rows: list[dict]) -> tuple[list[dict], Counter]:
    """互斥归类（先命中先归属），保证各类可加总为 298。"""
    from research_engine.agents.validator import Validator

    tagged: list[dict] = []
    counter: Counter = Counter()
    for r in rows:
        claim = r.get("claim") or ""
        note = r.get("note") or ""
        cat = r.get("cat") or ""

        if not Validator._is_assertive(claim):
            cls = "R4"
        elif "截断" in note:
            cls = "R2"
        elif "supported" in note.lower() or "但[" in note:
            cls = "R3"
        elif "虚构" in note or "无依据" in note:
            cls = "HALLU"
        elif cat.startswith(("A", "B1", "B2")):
            cls = "MISSRC"
        else:
            cls = "OTHER"
        counter[cls] += 1
        tagged.append({**r, "_cls": cls})
    return tagged, counter


def build_report(tagged: list[dict], counter: Counter) -> str:
    n = len(tagged)
    r4, r2, r3 = counter["R4"], counter["R2"], counter["R3"]
    hallu, missrc, other = counter["HALLU"], counter["MISSRC"], counter["OTHER"]
    fixable = r4 + r2 + r3

    order = ["R4", "R2", "R3", "HALLU", "MISSRC", "OTHER"]
    rows_md = "\n".join(
        f"| **{k}** {CLASSES[k][0]} | {CLASSES[k][1]} | **{counter[k]}** | "
        f"{counter[k] / n * 100:.1f}% | {CLASSES[k][2]} | {CLASSES[k][3]} |"
        for k in order
    )

    base = VERIFIED / TOTAL_CITATIONS * 100
    f3_only = VERIFIED / (TOTAL_CITATIONS - r4) * 100
    f1_only = (VERIFIED + r2) / TOTAL_CITATIONS * 100
    both = (VERIFIED + r2) / (TOTAL_CITATIONS - r4) * 100

    samples: list[dict] = []
    seen: set[str] = set()
    for t in tagged:
        if t["_cls"] != "R4":
            continue
        key = (t["claim"] or "").strip()
        if key in seen:
            continue
        seen.add(key)
        samples.append(t)
        if len(samples) == 3:
            break
    sample_lines = "\n".join(
        f"| `{t['q_id']}` | `{(t['claim'] or '')[:46]}` | {t['claim_len']} |" for t in samples
    )

    return f"""# W7 技术债② 失败分类归因表

> 由 `tools/w7_failure_attribution.py` 生成（可复算）。数据源：`.workbuddy/diag/failed_citations.jsonl`
> （**{n}** 条未通过引用，来自 `run_v11_compare`，20 题 / 1066 条编号级引用）。
> 回答 DoD §8「失败分类（5 类）归因表产出，**可解释提升来源**」——把 72.0% → 预期 76~80% 的
> 提升拆到**具体机制**上，而不是只报一个总量差。

## 1. 基线（诊断期实测）

| 量 | 数值 |
|---|---|
| 编号级总引用 | {TOTAL_CITATIONS} |
| 机器口径通过（`verified`） | {VERIFIED} |
| **机器口径准确率** | **{base:.1f}%**（{VERIFIED}/{TOTAL_CITATIONS}） |
| 未通过 | {n} |
| 存在性失败（越界编号/编造来源） | 仅 17（1.6%）⇒ **误拒主因不在存在性阶段** |

## 2. 失败分类归因表（互斥归类，加总 = {n}）

| 类别 | 根因 | 条数 | 占比 | 修复 | 修复机制（零额外 LLM 调用） |
|---|---|---:|---:|---|---|
{rows_md}
| **合计** | | **{n}** | 100.0% | | |

**三档性质**：**技术性误拒** = R4+R2+R3 = **{fixable}（{fixable / n * 100:.1f}%）**，可由 F1/F3 与双口径直接消除；
**须技术债③治理** = HALLU+MISSRC = **{hallu + missrc}（{(hallu + missrc) / n * 100:.1f}%）**，是 writer 纪律问题；
**OTHER** = {other}（{other / n * 100:.1f}%）含 R1/R5 残余，旧产物无法进一步归因。

## 3. 提升来源（算术上界，非实测承诺）

| 情形 | 算式 | 准确率 | Δ |
|---|---|---|---|
| 基线 | 768 / 1066 | {base:.1f}% | — |
| 仅 F3（移除 {r4} 条结构残片，缩小分母） | 768 / {TOTAL_CITATIONS - r4} | {f3_only:.1f}% | **+{f3_only - base:.1f}pp** |
| 仅 F1（恢复 {r2} 条截断误拒，增大分子） | {VERIFIED + r2} / {TOTAL_CITATIONS} | {f1_only:.1f}% | **+{f1_only - base:.1f}pp** |
| F1 + F3 同时生效 | {VERIFIED + r2} / {TOTAL_CITATIONS - r4} | {both:.1f}% | **+{both - base:.1f}pp** |

**提升来源的结论**：**F3（结构残片）是最大单项杠杆（+{f3_only - base:.1f}pp），大于 F1（+{f1_only - base:.1f}pp）**，
且两者合计上界 {both:.1f}%，与 §5.2「预期 76~80%」的诚实预期**方向一致、上端略高**。
剩余 {hallu + missrc} 条（{(hallu + missrc) / n * 100:.1f}%）必须靠技术债③（writer 纪律）——
这就是"靠修 validator 冲到 90% 不可能"的定量解释，也说明 ② 与 ③ 必须联合验证。

### 3.1 被 F3 过滤的样例（现行代码重放，前 3 条）

| q_id | claim 片段 | 原长度 |
|---|---|---|
{sample_lines}

## 4. 对原诊断的一处修正（本次核验新发现）

§5.2 结论 2 曾写「**真正的误拒**（截断 37 + 编号错配 12）**仅约 16%**」。
按现行代码重放 {n} 条后，**技术性误拒实际为 {fixable} 条（{fixable / n * 100:.1f}%）**——
差异来源：当时**识别出了 R4（结构残片）为真 bug，却未把它的量计入「误拒」分子**（{r4} 条，占 {r4 / n * 100:.1f}%）。
⇒ **原诊断低估了技术性误拒，也就低估了 validator 修复的天花板**（16% → {fixable / n * 100:.1f}%）。
结论方向不变（**多数失败仍是 writer 幻觉**，{hallu + missrc} 条 = {(hallu + missrc) / n * 100:.1f}%），
但"修 validator 没用"这个读法应当收回：**它能拿到的分比原先估计的多**。

## 5. 诚实边界（不得省略）

1. **归类是启发式、不是逐条人工判读**：规则见 `tools/w7_failure_attribution.py::classify`，可复核可重算；
   互斥归类（先命中先归属）故可加总为 {n}。
2. **R1（verdict/claim 错位）无法回溯度量**：F2 引入的判别字段 `claim_echo` 在旧产物中**不存在**；
   旧数据仅有 claim 与被引 finding 的 Jaccard 字面重合度，实测区分度不足
   （sim≤0.05 覆盖 80.9%、sim=0 达 16.1%）⇒ **无法据此计数**，其残余落在 OTHER 行。
3. **R5（claim 丢失主语）同样无法回溯**：需重建原报告文本才能重放 `_claim_text`，旧产物只存 claim 结果。
4. **上界 ≠ 承诺**：§3 假设"修复完全生效且不产生新失败"。真实效果应以对照实验为准——
   而 TBD-8 对照实验已判定**不可判定**（见 `docs/eval-w7-conclusion.md`），故本表
   **不主张任何效果幅度**，只主张**机制归因**。
5. **分母口径**：本表为严格口径（`verified = existence AND faithful`）。W5 人工抽检 83~92% 属**宽松口径**，
   二者不是同一件事（详见 §5.2 TBD-5）。

## 6. 复算方式

```bash
python tools/w7_failure_attribution.py   # 重算并覆盖本文件
```
"""


def main() -> int:
    rows = load_fixture()
    tagged, counter = classify(rows)
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text(build_report(tagged, counter), encoding="utf-8")
    n = len(tagged)
    print("=" * 68)
    print(f"失败分类归因：n={n}")
    for k, v in counter.most_common():
        print(f"  {k:8s} {v:4d}  ({v / n * 100:5.1f}%)")
    print("=" * 68)
    print(f"已写出：{OUT_MD}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
