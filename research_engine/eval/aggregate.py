"""Arm 5：指标聚合的**一处定义**（`_summarize` 与离线回填脚本共用）。

为什么单独拆一个模块
--------------------
§5.5.4 要求「离线回填历史 run 的 stderr」。若回填脚本自己抄一份「指标 → (key, subkey)」
映射，就会出现**两处定义**：将来加指标时只改一处 ⇒ 历史 run 与当期 run 的口径静默分叉，
而「让历史数据第一次具备可比性判据」这个目标正好被自己的实现破坏掉。
故把聚合逻辑抽出来，`research_engine/eval/run.py` 与 `tools/w8_backfill_stderr.py` 都 import 它。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from research_engine.eval.stats import summarize_metric

#: **评分器（scorer）版本** —— 跨 run 可比性的第一道闸（W8 Arm 6）。
#:
#: 语义：两个 run 的 `scorer_version` 不同 ⇒ 其指标数值**不可直接相减**。
#: 它回答的是「这两份数是同一把尺子量的吗」——比任何 delta 计算都先一步。
#:
#: 何时 +1（**口径变了**，数值含义不同）：
#:   - `METRIC_SOURCES` 的映射变了（换指标 / 换分母 / 换字段）
#:   - 判定阈值变了（`SEMANTIC_SIM_THRESHOLD`、完成率的 300 字 / 2 节门槛）
#:   - 覆盖度 / 引用指标的**计算代码**变了（含去重口径、缺失值处理）
#: 何时 **不** +1（纯重构，数值逐条不变）：
#:   - 抽函数、改命名、加类型注解、补文档
#:
#: ⚠️ **裁判提示词的变化不归这里管** —— coverage 由 LLM 裁判，
#: 提示词改了 ⇒ `prompt_hash` 会变，由它单独捕获（两者都要看）。
SCORER_VERSION = "w8.1"

#: 指标名 → (metrics 段名, 段内键名)。**新增指标只改这里**。
METRIC_SOURCES: Tuple[Tuple[str, str, str], ...] = (
    ("completion_rate", "completion", "complete"),
    ("citation_accuracy", "citation", "fidelity_rate"),  # W2 忠实度口径
    ("citation_accuracy_relaxed", "citation", "relaxed_rate"),  # W7 TBD-5 宽松口径
    ("existence_rate", "citation", "existence_rate"),
    ("coverage", "coverage", "coverage"),
    ("retrieval_hit_rate", "retrieval_hit", "retrieval_hit_rate"),
    ("avg_steps", "steps", "steps"),
)


def collect_metric(results: List[Dict[str, Any]], key: str, subkey: Optional[str] = None) -> List[float]:
    """取某指标的**逐条**数值（离散度需要逐条值，不能只看均值）。"""
    vals: List[float] = []
    for r in results:
        m = (r.get("metrics") or {}).get(key)
        if isinstance(m, dict) and subkey:
            v = m.get(subkey)
            if isinstance(v, (int, float)):
                vals.append(float(v))
        elif isinstance(m, (int, float)):
            vals.append(float(m))
    return vals


def reflection_stop_values(results: List[Dict[str, Any]]) -> List[float]:
    """反思有效性占比的逐条值（critic_stop ⇒ 1，其它 stop_type ⇒ 0；无 stop_type 的条不计入）。

    结构性口径：`_avg` 对 dict 字段不适用，只能按「有 stop_type 的条」单独算。
    """
    return [
        1.0 if (r.get("metrics") or {}).get("reflection", {}).get("stop_type") == "critic_stop" else 0.0
        for r in results
        if (r.get("metrics") or {}).get("reflection", {}).get("stop_type")
    ]


def compute_metrics(results: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """返回 ``(metrics_mean, metrics_stderr)``。

    * ``metrics_mean[metric]`` = 均值（**形状不变**，兼容 `report_gen` / `w7_analysis`
      / `w7_experiment` / `tools/` 所有既有消费方）；
    * ``metrics_stderr[metric]`` = ``{"n", "stderr", "ci95"}`` —— §5.5.4 并列输出，
      ``n`` 是**有效题数**（指标没算出的条目不计入）。
    """
    metrics_mean: Dict[str, Any] = {}
    metrics_stderr: Dict[str, Any] = {}

    def _add(name: str, vals: List[float]) -> None:
        d = summarize_metric(vals)
        metrics_mean[name] = d["mean"]
        metrics_stderr[name] = {"n": d["n"], "stderr": d["stderr"], "ci95": d["ci95"]}

    for name, key, sub in METRIC_SOURCES:
        _add(name, collect_metric(results, key, sub))
    _add("reflection_critic_stop_rate", reflection_stop_values(results))
    # W7 技术债③ 次要指标（只看不判）：报告「信息不足」标注小节占比
    _add("insufficient_marker_ratio", collect_metric(results, "insufficient", "marker_ratio"))
    return metrics_mean, metrics_stderr


def sum_tokens_from_raw(run_dir) -> int:
    """D1 降级通道：从 raw 累加 ``token_used``（单条硬闸计数，进程内可归因）。

    放在这里是为了让「D1 扫描」与「D1 降级」共用同一份读法 —— 两条路径算出不同的
    数就等于没有数。**注意**：raw 只有 token 总量，没有按模型的 input/output 拆分
    ⇒ **成本金额不可重建**，调用方须如实标 ``cost_yuan=None``，不许用混合单价编。
    """
    import json
    from pathlib import Path

    total = 0
    raw_dir = Path(run_dir) / "raw"
    if not raw_dir.exists():
        return 0
    for rp in sorted(raw_dir.glob("*.raw.json")):
        try:
            raw = json.loads(rp.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        v = raw.get("token_used")
        if isinstance(v, (int, float)):
            total += int(v)
    return total


def read_eval_results(run_dir) -> List[Dict[str, Any]]:
    """读一个 run 目录下所有 ``eval/*.eval.json``（离线回填 / 重算用，只读）。"""
    import json
    from pathlib import Path

    eval_dir = Path(run_dir) / "eval"
    if not eval_dir.exists():
        return []
    out: List[Dict[str, Any]] = []
    for p in sorted(eval_dir.glob("*.eval.json")):
        try:
            out.append(json.loads(p.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            continue
    return out
