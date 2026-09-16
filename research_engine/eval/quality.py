"""Arm 5 §5.5.1：run 级质量闸（**只告警、不阻断**）。

为什么是「闸」而不是「字段」
----------------------------
Q3=A 拍板时实测出的病是：`run_20260910_173540` 即使把 `complete` 拆成四字段，
拆完也是 `raw_written=20, quality_complete=0` —— **仍然需要一个人主动去看
`quality_complete` 是不是 0**。**加字段 ≠ 加护栏**。所以改为在 `_summarize()`
顶层直接出一个结论：`verdict`。

三态语义
--------
* ``ok``         —— 无异常；
* ``suspicious`` —— 指标跌破阈值，**被测可能已彻底降级**（值得人看一眼）；
* ``broken``     —— **评测管线自身**大面积失败（不是被测变差，是尺子坏了）。

阈值外置
--------
``DEFAULT_THRESHOLDS`` 的候选数字是**事后从 `run_20260910_173540` 单个 run 反推的**，
把它写进需求文档等于把一个过拟合的数字制度化。所以：

* 需求文档（§5.5.1）**不写死数字**；
* 默认组落在**本模块常量**里，调用方（CLI / 采基线脚本）可传自己的阈值。

只告警不阻断
------------
``suspicious`` 的语义是「值得人看一眼」，而阈值尚未在新基线上校准。
**在阈值可信之前就让它阻断采基线，风险大于收益** —— 故 `verdict` 只写入
summary 与报告，不 hard fail。是否升级为 hard fail 待基线校准后另开议题。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

VERDICT_OK = "ok"
VERDICT_SUSPICIOUS = "suspicious"
VERDICT_BROKEN = "broken"


@dataclass(frozen=True)
class QualityThresholds:
    """质量闸阈值（**外置参数**，调用方可覆盖）。

    语义：``*_min`` 为**下限，小于等于即触发**；``*_max`` 为**上限，大于等于即触发**。
    """

    #: 完成率下限。`<=` 即判 suspicious —— 0 意味着被测**一条都没跑完**
    completion_rate_min: float = 0.0
    #: 引用准确率下限。0 意味着引用校验整体没产出
    citation_accuracy_min: float = 0.0
    #: 平均步数下限。多跳研究若平均只有 1 步，说明检索循环没转起来
    avg_steps_min: float = 1.5
    #: 评测失败条数占比上限。`>=` 即判 **broken**（管线自身大面积失败）
    failed_ratio_max: float = 0.5


#: 默认阈值。
#: ⚠️ **由 `run_20260910_173540` 单个 run 反推，仅作初值** ——
#: **须在 §10.4 新采的主链路基线上复核后调整**（`avg_steps_min` 尤其可能因主链路
#: 步数分布不同而不适用）。在校准完成前，本闸**只告警不阻断**。
DEFAULT_THRESHOLDS = QualityThresholds()


def _fmt(v: Optional[float]) -> str:
    return "n/a" if v is None else f"{v:.4f}"


def evaluate_run_verdict(
    metrics_mean: Dict[str, Any],
    counts: Dict[str, int],
    total: int,
    thresholds: QualityThresholds = DEFAULT_THRESHOLDS,
) -> Tuple[str, List[str]]:
    """由均值与计数推导 ``(verdict, verdict_reasons)``。

    **判定顺序有意为之**：`broken`（尺子坏了）优先于 `suspicious`（被测变差）——
    管线大面积失败时，均值本身就是不可信的，不该拿它去判被测。

    ``counts`` 键：``metrics_ok`` / ``metrics_partial`` / ``metrics_failed``。
    """
    reasons: List[str] = []

    # ---- ① broken：评测管线自身大面积失败 ----
    if total <= 0:
        return VERDICT_BROKEN, ["total==0：本 run 无任何可评测条目"]
    failed = int(counts.get("metrics_failed", 0))
    failed_ratio = failed / total
    if failed_ratio >= thresholds.failed_ratio_max:
        return VERDICT_BROKEN, [
            f"metrics_failed_ratio={failed_ratio:.2%} >= {thresholds.failed_ratio_max:.2%}"
            f"（{failed}/{total} 条评测失败，均值不可信）"
        ]

    # ---- ② suspicious：指标跌破阈值（被测可能已彻底降级）----
    def _below(key: str, limit: float) -> bool:
        v = metrics_mean.get(key)
        if not isinstance(v, (int, float)):
            return False  # 指标没算出 ⇒ 不据此判被测，交给 partial/failed 计数
        if v <= limit:
            reasons.append(f"{key}={_fmt(v)} <= {limit}")
            return True
        return False

    _below("completion_rate", thresholds.completion_rate_min)
    _below("citation_accuracy", thresholds.citation_accuracy_min)
    _below("avg_steps", thresholds.avg_steps_min)

    return (VERDICT_SUSPICIOUS, reasons) if reasons else (VERDICT_OK, [])


#: `complete/partial/failed` → 新键名（§5.5.2 字段重命名）
#: 旧键**同时写入**并标 deprecated —— 兼容 `docs/eval-report.md` 历史趋势表
#: 与 `tools/w7_backfill_*.py`；待下游全部改完后移除（不在本 Arm 的 DoD 内）。
DEPRECATED_COUNT_KEYS: Dict[str, str] = {
    "complete": "metrics_ok",
    "partial": "metrics_partial",
    "failed": "metrics_failed",
}
