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

from research_engine.eval.provenance import code_revision, config_snapshot

RESULTS_DIR = Path(__file__).resolve().parent / "results"
EVAL_REPORT_PATH = Path(__file__).resolve().parent.parent.parent / "docs" / "eval-report.md"

# 权威 W7 对照实验容器（六臂 × 3 区块）。趋势表据此给臂 run 打 🧪 标记；
# 若后续重做实验，改这一处即可（结论文档 docs/eval-w7-conclusion.md §2 记的是同一个目录名）。
W7_AUTHORITATIVE_EXPERIMENT = "w7_experiment_20260911_194151"


def _config_snapshot() -> Dict[str, Any]:
    """config 关键项快照（Q7：每轮 run 都记，baseline 只是 v0 的那份）。

    W8 §10.4：实现已迁至 `provenance.config_snapshot()`（唯一真相源，
    新增 `validator_model` / `python_version` / `experiment` 段）。
    此处保留薄封装以兼容既有调用方，勿再在此加字段。
    """
    return config_snapshot()


def _snapshot_hash(obj: Dict[str, Any]) -> str:
    """完整性校验指纹（Q7：防手误修改/文件损坏，非防篡改——单人项目无攻击者）。"""
    canonical = json.dumps(obj, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _git_head() -> str:
    """当前 HEAD 完整提交号（抓不到如实标 unknown）。

    W8 §10.4：实现已迁至 `provenance.code_revision()`（原为 3 份重复实现之一）。
    """
    return code_revision()["git_commit"]


def write_baseline(run_id: str, dataset_meta: Dict[str, Any]) -> Path:
    """v0 基线冻结快照（Q7：首个 run 自动落 baseline；后续仅当无 history 时生成）。"""
    run_dir = RESULTS_DIR / run_id
    baseline = {
        "baseline_id": "v0",
        "git_commit": _git_head(),
        "dataset_version": dataset_meta.get("version", "unknown"),
        "config_snapshot": _config_snapshot(),
        "eval_env": {
            # W8 §10.4：python 版本已收进 config_snapshot.python_version，此处不再重复
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
        # Arm 5 §5.5.1：verdict 置于最前（历史趋势表口径一致）
        "verdict": summary.get("verdict"),
        "verdict_reasons": summary.get("verdict_reasons") or [],
        "git_commit": _git_head(),
        "metrics": metrics_mean,
        # §5.5.4：历史 run 也要有离散度 ⇒ 跨 run 比较才有判据
        "metrics_stderr": summary.get("metrics_stderr") or {},
        # §5.5.2 新键（旧键 complete/partial/failed 由 summary 侧同时保留）
        "metrics_ok": summary.get("metrics_ok", summary.get("complete")),
        "metrics_partial": summary.get("metrics_partial", summary.get("partial")),
        "metrics_failed": summary.get("metrics_failed", summary.get("failed")),
        # W8 §10.4 第 2 条：每轮 run 都记 config 快照（history 亦不例外）
        # —— 只写 run 级一条，不按题目重复（配置在同一 run 内不变）
        "config_snapshot": summary.get("config_snapshot"),
        "delta_pp_from_prev": delta,
        "status": summary.get("struct", {}).get("regression", "PASS"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    history.append(record)
    history_path.write_text(json.dumps(history, ensure_ascii=False, indent=1), encoding="utf-8")
    return history_path


def _fmt_pct(x: float) -> str:
    return f"{x * 100:.1f}%"


def _insufficient_summary(results: List[Dict[str, Any]]) -> Dict[str, Any]:
    """聚合「信息不足」标注（W7 技术债③ 次要指标）。纯函数，便于单测锁定。"""
    rows = [
        (r.get("metrics") or {}).get("insufficient") or {}
        for r in results
        if isinstance((r.get("metrics") or {}).get("insufficient"), dict)
    ]
    sec_total = sum(int(x.get("section_count") or 0) for x in rows)
    sec_marked = sum(int(x.get("marked_sections") or 0) for x in rows)
    ph_total = sum(int(x.get("placeholder_count") or 0) for x in rows)
    return {
        "reports": len(rows),
        "section_total": sec_total,
        "section_marked": sec_marked,
        "placeholder_total": ph_total,
        "marker_ratio": (sec_marked / sec_total) if sec_total else 0.0,
    }


def _w7_experiment_section() -> str:
    """DoD §8「六臂对照实验记录」：读权威实验 manifest，输出轮次 / 配对差值 / 判定。

    刻意做成「实验记录」而不是「趋势图」：跨区块的 citation 类指标口径不一致
    （Block 0 跑在 `95adb77`+patch，Block 1/2 跑在 `ca51886`），故本表只呈现事实与
    **机械判定**，并显式声明不可比性；任何因果结论一律以 `docs/eval-w7-conclusion.md` 为准。
    manifest 缺失时返回空串，保证无实验产物的环境（如 CI）仍能生成报告。
    """
    manifest_path = RESULTS_DIR / W7_AUTHORITATIVE_EXPERIMENT / "manifest.json"
    if not manifest_path.exists():
        return ""
    try:
        manifest: Dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""

    arms: List[str] = list(manifest.get("arms", []))
    runs: List[Dict[str, Any]] = list(manifest.get("runs", []))
    planned = int(manifest.get("runs_per_arm", 0) or 0)
    if not arms or not runs:
        return ""

    grid: Dict[tuple, Dict[str, Any]] = {
        (r.get("arm"), r.get("block")): r for r in runs
    }
    done_blocks = sorted(
        {r.get("block") for r in runs if r.get("status") == "done" and r.get("block") is not None}
    )
    grid_blocks = sorted({b for (_a, b) in grid if b is not None})
    blocks = grid_blocks or done_blocks

    def metrics_of(key: tuple) -> Dict[str, Any]:
        return (grid.get(key) or {}).get("metrics") or {}

    # ---- 9.1 轮次 × 主指标 ----
    head = "| arm | 区块 | 状态 | coverage | citation_accuracy | retrieval_hit_rate | avg_steps |"
    sep = "|---|---|---|---:|---:|---:|---:|"
    body: List[str] = []
    skip_notes: List[str] = []
    for arm in arms:
        for b in blocks:
            rec = grid.get((arm, b))
            m = metrics_of((arm, b))
            if not rec:
                body.append(f"| `{arm}` | B{b} | 未执行（未登记） | — | — | — | — |")
                skip_notes.append(f"`{arm}` / B{b}：**未执行且 manifest 未登记原因**")
                continue
            status = rec.get("status", "unknown")
            if status != "done" or not m:
                detail = rec.get("gate_detail") or "manifest 未登记原因"
                body.append(
                    f"| `{arm}` | B{b} | ⏭ `{status}` | — | — | — | — |"
                )
                skip_notes.append(f"`{arm}` / B{b}：`{status}` —— {detail}")
                continue
            body.append(
                f"| `{arm}` | B{b} | done | {_fmt_pct(m.get('coverage', 0))} | "
                f"{_fmt_pct(m.get('citation_accuracy', 0))} | "
                f"{_fmt_pct(m.get('retrieval_hit_rate', 0))} | {m.get('avg_steps')} |"
            )

    # ---- 9.2 配对差值（相对 arm0 同区块）+ 机械判定 ----
    base_arm = arms[0]
    pair_head = "| arm | " + " | ".join(f"B{b}" for b in blocks) + " | 均值 | 区块方向 | 机械判定 |"
    pair_sep = "|---|" + "---:|" * len(blocks) + "---:|---|---|"
    step_base = [
        metrics_of((base_arm, b)).get("avg_steps")
        for b in blocks
        if metrics_of((base_arm, b)).get("avg_steps")
    ]
    step_base_mean = sum(step_base) / len(step_base) if step_base else 0.0

    pair_rows: List[str] = []
    for arm in arms:
        if arm == base_arm:
            continue
        diffs: List[Optional[float]] = []
        for b in blocks:
            mb, ma = metrics_of((base_arm, b)), metrics_of((arm, b))
            if mb and ma and mb.get("coverage") is not None and ma.get("coverage") is not None:
                diffs.append((ma["coverage"] - mb["coverage"]) * 100)
            else:
                diffs.append(None)
        real = [d for d in diffs if d is not None]
        cells = " | ".join("—" if d is None else f"{d:+.1f}" for d in diffs)
        if not real:
            pair_rows.append(f"| `{arm}` | {cells} | — | 无有效区块 | — |")
            continue
        mean_d = sum(real) / len(real)
        signs = {d > 0 for d in real}
        direction = "全同向(+)" if signs == {True} else (
            "全同向(−)" if signs == {False} else "**符号翻转**"
        )
        missing = sum(1 for d in diffs if d is None)
        steps = [
            metrics_of((arm, b)).get("avg_steps")
            for b in blocks
            if metrics_of((arm, b)).get("avg_steps")
        ]
        step_mean = sum(steps) / len(steps) if steps else 0.0
        # 本实验是区块配对设计 ⇒ 步数涨幅用【逐区块配对比值均值】为主口径；
        # 「比值之均值(ratio of means)」数值略有差异（117.3% vs 118.2%），一并列出以透明。
        ratios: List[float] = []
        for b in blocks:
            sb = metrics_of((base_arm, b)).get("avg_steps")
            sa = metrics_of((arm, b)).get("avg_steps")
            if sb and sa:
                ratios.append(sa / sb)
        paired_pct = (sum(ratios) / len(ratios) - 1) * 100 if ratios else None
        rom_pct = (
            (step_mean / step_base_mean - 1) * 100 if step_base_mean and step_mean else None
        )
        if paired_pct is None:
            budget = "avg_steps 不可比"
        else:
            budget = (
                f"avg_steps {step_mean:.2f} vs 基线 {step_base_mean:.2f}"
                f"（配对涨幅均值 **{paired_pct:+.1f}%**"
                + (f"；比值之均值口径 {rom_pct:+.1f}%" if rom_pct is not None else "")
                + "）"
            )
        over_budget = paired_pct is not None and paired_pct > 50.0
        if len(real) < 2:
            verdict = f"❌ 数据不足（仅 {len(real)} 个区块）"
        elif missing and "符号翻转" not in direction:
            verdict = f"⚠️ {direction}，但**缺 {missing} 格** ⇒ 不可裁决"
        elif "符号翻转" in direction:
            verdict = "❌ **不可判定**（区块间符号翻转）"
        elif over_budget:
            verdict = (
                f"⚠️ {direction}，但**预算不平衡、破守门线（≤+50%）**（{budget}）"
                "⇒ 属成本-覆盖度权衡，不能作为机制更优的证据"
            )
        else:
            verdict = f"⚠️ {direction}，但观测数仅 {len(real)} 次 ⇒ 不足以裁决"
        pair_rows.append(
            f"| `{arm}` | {cells} | {mean_d:+.1f} | {direction} | {verdict} |"
        )

    # ---- 9.3 不可比性声明 ----
    revisions = manifest.get("resumed_revisions") or []
    rev_txt = "、".join(f"`{r}`" for r in revisions) if revisions else "（manifest 未记录）"
    first_rev = manifest.get("code_revision") or "unknown"
    notes_lines = "\n".join(f"> - {n}" for n in [
        f"**跨 revision**：首轮 `code_revision={first_rev}`，续跑修订集合 = {rev_txt}。"
        "跨区块的 citation 类指标**不得合并比较**（引用归一化开关差异会让报告正文的裸 `[N]` 引用消失）。",
        "**缺格**：凡缺失格一律显式登记原因（见 9.1 与下方备忘），**不得静默当作 0**。",
        "**被测兼裁判**：`citation_accuracy` 直读主链路 validator；凡改动 validator 的 arm，其数值同时含"
        "「被测效应 + 裁判效应」⇒ 不可与其他 arm 直接比较（实测纯裁判效应 +7.53pp）。",
        "**预算不平衡**：`avg_steps` 分档且与实验开关共线 ⇒ 跨 arm 的 coverage 差**不可直接解释为机制优劣**；"
        "正确做法是同预算成本-效果比较。",
        "**结论归属**：本报告只做机械判定，**不主张任何因果结论**；权威判定见 `docs/eval-w7-conclusion.md`。",
    ])
    skip_block = ""
    if skip_notes:
        skip_block = "\n**缺格 / 未执行登记（原因不得省略）：**\n" + "\n".join(
            f"> - {s}" for s in skip_notes
        ) + "\n"

    return f"""
## 9. W7 技术债对照实验（权威容器 `{W7_AUTHORITATIVE_EXPERIMENT}`）

> 数据源：`research_engine/eval/results/{W7_AUTHORITATIVE_EXPERIMENT}/manifest.json`
> （{len(arms)} 臂 × {planned} 区块，共 {len(runs)} 条 run 记录：{len([r for r in runs if r.get('status') == 'done'])} done
> + {len([r for r in runs if r.get('status') != 'done'])} 非 done）。
> 本章是**实验记录**而非趋势——目的就是让「六臂实验跑在哪、缺哪格、能不能比」在报告里可查。

### 9.1 轮次（区块）× 主指标

{head}
{sep}
{chr(10).join(body)}
{skip_block}
### 9.2 配对差值（coverage，相对 `{base_arm}` 同区块，pp）与机械判定

{pair_head}
{pair_sep}
{chr(10).join(pair_rows)}

### 9.3 不可比性声明（本实验的硬约束）

{notes_lines}
"""


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

    # ---- 3. 指标表（§5.5.4：均值必须并列 stderr 与有效题数）----
    header = (
        "| 指标 | 目标 | 本轮（均值 ± stderr） | 有效题数 n | 达标 |\n"
        "|---|---|---|---|---|\n"
    )
    rows_tbl = []
    targets = {
        "completion_rate": "≥90%",
        "citation_accuracy": "≥85%（**严格口径** verified）",
        "citation_accuracy_relaxed": "记录基线（**宽松口径**，仅解释性附注）",
        "coverage": "≥90%",
        "retrieval_hit_rate": "记录基线",
        "avg_steps": "记录基线",
        "reflection_critic_stop_rate": "≥90%",
    }
    stderr_map = summary.get("metrics_stderr") or {}
    for key, target in targets.items():
        val = metrics_mean_effective.get(key)
        disp = stderr_map.get(key) or {}
        n = disp.get("n", 0)
        se = disp.get("stderr", 0.0)
        if key == "avg_steps":
            val_s = f"{val:.1f} 轮" if isinstance(val, (int, float)) else "—"
            if isinstance(val, (int, float)) and se:
                val_s += f" ± {se:.2f}"
        elif isinstance(val, float):
            val_s = f"{val * 100:.1f}%"
            if se:
                val_s += f" ± {se * 100:.1f}pp"
        else:
            val_s = "—"
        mark = "✅" if _target_met(key, val, results) else "⚠️"
        rows_tbl.append(f"| {key} | {target} | {val_s} | {n} | {mark} |")

    cost_total = summary.get("cost_phase1_total", {})
    per_model = cost_total.get("per_model") or {}
    per_model_str = ", ".join(
        f"{m}: {v['tokens']}tok ¥{v['cost']:.4f}" for m, v in per_model.items()
    )
    per_role_str = ", ".join(f"{r}: {t}tok" for r, t in (cost_total.get("per_role") or {}).items())
    # D1：成本降级时 **金额不可重建**，绝不能显示成 ¥0（那等于说「没花钱」）
    if cost_total.get("cost_degraded"):
        cost_yuan_s = f"⚠️ 不可重建（{cost_total.get('cost_basis')}：{cost_total.get('cost_degraded_reason')}）"
    else:
        cost_yuan_s = f"¥{cost_total.get('cost_yuan', 0)}"
    cost_line = (
        f"💰 总 token（Phase1 研究）：{cost_total.get('total_tokens', 0)}，"
        f"成本：{cost_yuan_s}"
        f"（Phase2 judge 另计 {summary.get('cost_phase2_judge_tokens', 0)} token）\n"
        f"  - 模型名桶：{per_model_str or '—'}\n"
        f"  - 职责桶：{per_role_str or '—'}\n"
    )

    # ---- 3b. 次要指标（只看不判）：W7 技术债③「信息不足」标注比例 ----
    _ins = _insufficient_summary(results)
    minor_line = (
        f"📐 次要指标（**只看不判**，无达标线）：**「信息不足」标注小节占比 "
        f"{_ins['marker_ratio'] * 100:.1f}%**"
        f"（{_ins['section_marked']}/{_ins['section_total']} 小节）；"
        f"引用位兜底标记 `[来源: 信息不足]` {_ins['placeholder_total']} 处"
        f"（统计覆盖 {_ins['reports']} 篇报告）。\n"
        "  - 读法：比例**极低**可能意味着模型改为编造而非承认缺口；比例**极高**意味着检索没喂饱。"
        "两种极端都值得人工抽检，但**不作为任何达标判据**。\n"
        + (
            ""
            if _ins["reports"]
            else "  - ⚠️ 本快照的 run 早于该指标实现 ⇒ 暂无数据（仅**新产生的 run** 会计入）。\n"
        )
    )

    # ---- 3a. 双口径与人工口径归属（W7 TBD-5 诚实披露；DoD 要求显式声明，不得省略）----
    dual_caliber_note = (
        "📏 **双口径与人工口径归属（W7 TBD-5 诚实披露，不得省略）：**\n"
        "  - **严格口径**（`citation_accuracy`）= `verified = existence AND faithful`"
        "（引对编号 **且** 忠实）—— W2 契约口径，跨版本对比**一律以此为准**。\n"
        "  - **宽松口径**（`citation_accuracy_relaxed`）= `existence AND (faithful OR supported)`"
        "——论断在系统内**能找到依据**即算通过（允许引错编号但内容真实）。\n"
        "  - **⚠️ W5 人工抽检 83~92% 属「宽松口径」**：人工判的是「这论断有没有依据」，"
        "**不逐条核对编号** ⇒ 与机器严格口径**不是同一件事**；二者差异的**绝大部分是口径差**，"
        "**不是 validator 误拒**。\n"
        "  - 人工抽检样本仅 **12 条**、置信区间极宽，**不作为真值**；宽松口径仅作**解释性附注**，"
        "不参与任何达标判定。\n"
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
        # W8 Arm 1：`error` 已由 Optional[str] 改为结构化 Dict{code,message,node}
        # ⇒ dict 不可切片，直接 [:200] 会 TypeError。统一走 str() 再截断。
        + (f" error={str(r.get('error'))[:200]}" if r.get("error") else "")
        for r in anomalies
    ) or "- 无"

    # ---- 7. 指标演进趋势表 ----
    # W7 对照实验的臂 run 与普通 run 混在同一张表里，其配置/裁判均不同（见 eval-w7-conclusion.md），
    # 这里给「权威对照实验」的臂 run 打上 🧪 标记，避免读者把实验臂的数字当成「主链路的演进」。
    # 只标记权威容器（六臂 × 3 区块，见 docs/eval-w7-conclusion.md §2）；09-09/09-10 的
    # 探索性 pilot 目录刻意不标记，否则本表会几乎整列带标记、失去提示意义。
    w7_arm_run_ids: set[str] = set()
    manifest_path = RESULTS_DIR / W7_AUTHORITATIVE_EXPERIMENT / "manifest.json"
    if manifest_path.exists():
        try:
            _m = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            _m = {}
        for _r in _m.get("runs", []):
            if _r.get("run_dir"):
                w7_arm_run_ids.add(Path(_r["run_dir"]).name)

    comparability_lines = [
        "> **可比性声明：**本报告是单 run 快照；不自动支持跨 run 因果比较。",
        "> - 跨 revision：需逐 run 校验 `code_revision` / 环境指纹；不一致时禁止合并。",
        "> - 缺 run / 缺格：必须显式登记原因；缺失结果不得静默当作 0。",
        "> - 预算：steps、tokens、cost 不平衡时，结果标记为不可直接比较。",
        "> - 闸门：被测组件不得兼任裁判；闸门失败只说明该格未过，不自动证明被测机制有害。",
        "> - 因果：仅在同题/同证据池/同预算/固定独立裁判等条件满足时支持因果解释。",
        "",
    ]
    trend_lines = [
        "> ⚠️ 本表数值由各 run 的 summary 直读**主链路 validator**裁决，而各 run 的实验配置与裁判模型并不一致；",
        "> 「vs 上轮(pp)」仅作记录，**不构成可比趋势**（W7 实测：同一引用集仅换裁判即产生 +7.53pp 差异）。",
    ]
    if w7_arm_run_ids:
        trend_lines.append(
            "> 🧪 标记的行属于 **W7 权威对照实验的臂**（六臂 × 3 区块，配置各异、非全部为基线模型），"
            "**不得与主链路 run 横向比较**；结论见 `docs/eval-w7-conclusion.md`。"
        )
    trend_lines.append("")
    if history_path.exists():
        history = json.loads(history_path.read_text(encoding="utf-8"))
        trend_lines.append("| 运行 | 完成率 | 引用准确率 | 覆盖度 | 检索命中率 | vs 上轮(pp) |")
        trend_lines.append("|---|---|---|---|---|---|")
        for rec in history:
            m = rec["metrics"]
            delta = rec.get("delta_pp_from_prev") or {}
            d_s = ", ".join(f"{k}:{v:+.1f}" for k, v in delta.items()) if delta else "—"
            mark = "🧪 " if rec["run_id"] in w7_arm_run_ids else ""
            trend_lines.append(
                f"| {mark}{rec['run_id']} | {m.get('completion_rate', 0) * 100:.1f}% | "
                f"{m.get('citation_accuracy', 0) * 100:.1f}% | {m.get('coverage', 0) * 100:.1f}% | "
                f"{m.get('retrieval_hit_rate', 0) * 100:.1f}% | {d_s} |"
            )

    # ---- Arm 5 §5.5.1：run 级质量闸（**只告警不阻断**）----
    verdict = summary.get("verdict", "unknown")
    verdict_reasons = summary.get("verdict_reasons") or []
    verdict_icon = {"ok": "✅", "suspicious": "⚠️", "broken": "🚫"}.get(verdict, "❔")
    reasons_s = "\n".join(f"  - {r}" for r in verdict_reasons) or "  - （无）"
    verdict_block = (
        f"## 0. run 级质量闸（Arm 5 §5.5.1，**只告警不阻断**）\n\n"
        f"**verdict：`{verdict}` {verdict_icon}**\n\n"
        f"触发原因：\n{reasons_s}\n\n"
        f"> `suspicious` = 指标跌破阈值，**被测可能已彻底降级**；"
        f"`broken` = **评测管线自身**大面积失败（尺子坏了，均值不可信）。\n"
        f"> 阈值为**外置参数**（`--thresholds-json`），默认组由 `run_20260910_173540` 反推，"
        f"**尚未在新主链路基线上校准** ⇒ 当前只告警、不阻断。\n"
    )

    # ---- 人工抽检模板（v0 首次生成占位，跑完后人工填写）----
    report = f"""# eval 报告（{run_id}）

> ⚠️ **本文件由 `research_engine/eval/report_gen.py` 自动生成，每次运行 `run.py` 都会被整体覆盖。**
> 它是**单次 run 的原始快照，不是项目结论**；下文指标表的「达标 ✅」只按本 run 的 summary 数值机械判定，
> 未纳入跨裁判复判。**项目结论以 `docs/eval-w7-conclusion.md` 为准。**
> 若需长期保存某次 run 的判定，请另存为结论文档，不要依赖本文件。

{chr(10).join(comparability_lines)}

{verdict_block}
## 1. 数据集说明
- version: {meta.get('version', 'unknown')} / created_at: {meta.get('created_at', 'unknown')}
- anchor_samples: {meta.get('anchor_samples', [])}
- 标注方法学: {meta.get('annotation', 'ai_draft + human_calibration')}

## 2. 运行环境与双锚
- git commit: {summary.get('git_commit')} / dataset version: {summary.get('dataset_version')}
- 墙钟/并发/统计: {summary.get('total')} 条，并发 3，生成于 {summary.get('generated_at')}

## 3. 指标表（7 项，Q8 含检索命中率）
{header}{chr(10).join(rows_tbl)}

{cost_line}
{dual_caliber_note}
{minor_line}

## 4. 失败与异常附录
- 指标齐全 {summary.get('metrics_ok', summary.get('complete'))} /
  指标部分 {summary.get('metrics_partial', summary.get('partial'))} /
  指标失败 {summary.get('metrics_failed', summary.get('failed'))}
  （§5.5.2 旧键 `complete/partial/failed` 仍写入，标 deprecated）
{anomaly_lines}

## 5. 人工抽检记录（两级：报告级 4~5 份 + 引用级 10~15 条，Q6）
- 抽检人: 于晏（单人，判定以标注规范为准；reviewer 字段可补二审）
- ⚠️ **口径提醒**：人工抽检判的是**宽松口径**（「这论断有没有依据」，不逐条核对编号），
  与 §3 机器严格口径（`verified = existence AND faithful`）**不是同一件事**；
  W5 历史抽检 83~92% 即属此宽松口径，**不得与机器严格口径直接对比**。
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
- **引用准确率的裁判未与被测对象解耦**：`citation_accuracy` 直读主链路 validator 裁决，凡改动 validator 的 run
  其数值同时含「被测效应 + 裁判效应」，**不可与其他 run 直接比较**（W7 实测裁判效应 +7.53pp 与被测效应同量级）
{_w7_experiment_section()}"""
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
