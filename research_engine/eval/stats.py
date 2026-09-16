"""Arm 5 §5.5.4：指标离散度（stderr / bootstrap 区间）。

为什么必须有这个模块
--------------------
W7 事后才发现「噪声（20~29pp）吞没效应（4~10pp）」，**直接原因是仪器没装刻度**：
跑了、算了、报了，但没人知道这个数字的可信区间有多宽。`metrics_mean` 只有均值，
于是「45.2%」和「45.2% ±6.3pp」在报告里长得一模一样。

行业横向对照（§6.7 实时取证）：

* **openai/evals**：``get_bootstrap_accuracy_std(events, num_samples=1000)``
  —— 内置在指标层的半采样 bootstrap std；
* **EleutherAI lm-evaluation-harness**：``mean_stderr()`` / ``acc_all_stderr()``
  —— 每个指标都带 stderr。

⇒ **「报指标必须带 stderr」是行业通例，不是加分项。**

实现要点
--------
* **零 LLM**：纯离线，直接对已有 per-row 数值做重采样；
* **可复现**：固定随机种子 ⇒ 同一份 raw 永远得出同一个 stderr（否则「回填历史 run」
  会和当期结果不一致，历史数据反而失去可比性）；
* **不引入 numpy**：仓库锁里没有 numpy，且 n≤几百时纯 Python 完全够快。
"""
from __future__ import annotations

import math
import random
from typing import Any, Dict, Sequence

#: 对齐 openai/evals 默认 1000 次重采样
BOOTSTRAP_SAMPLES = 1000
#: 固定种子 —— 保证「离线回填历史 run」与「当期计算」得出同一个 stderr
BOOTSTRAP_SEED = 20260915


def mean(values: Sequence[float]) -> float:
    """算术均值；空序列返回 0.0。"""
    return sum(values) / len(values) if values else 0.0


def pstdev(values: Sequence[float]) -> float:
    """总体标准差（ddof=0，与 ``numpy.std`` 默认一致；openai/evals 用的就是这个）。"""
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    return math.sqrt(sum((v - m) ** 2 for v in values) / n)


def mean_stderr(values: Sequence[float]) -> float:
    """均值的**解析**标准误（样本标准差 / √n），n<2 时为 0.0。

    与 :func:`bootstrap_stderr` 二选一即可；§5.5.4 选 bootstrap 是为了对齐
    openai/evals，但不要求分布假设 —— 本项目指标多为 0/1 或比例，bootstrap 更稳。
    """
    n = len(values)
    if n < 2:
        return 0.0
    m = mean(values)
    var = sum((v - m) ** 2 for v in values) / (n - 1)  # 样本方差（ddof=1）
    return math.sqrt(var / n)


def bootstrap_means(
    values: Sequence[float],
    num_samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> list:
    """半采样重采样均值列表（有放回，样本量 = n）。"""
    n = len(values)
    rng = random.Random(seed)
    return [sum(values[rng.randrange(n)] for _ in range(n)) / n for _ in range(num_samples)]


def summarize_metric(
    values: Sequence[float],
    num_samples: int = BOOTSTRAP_SAMPLES,
    seed: int = BOOTSTRAP_SEED,
) -> Dict[str, Any]:
    """给单个指标算「均值 + 离散度」，供 ``metrics_stderr[metric]`` 直接落盘。

    返回 ``{"n", "mean", "stderr", "ci95"}``：

    * ``n``     —— **有效题数**（不是总题数：指标没算出的条目不计入）
    * ``stderr`` —— bootstrap 标准误（重采样均值的总体标准差，对齐 openai/evals）
    * ``ci95``  —— 重采样均值的 2.5%/97.5% 分位区间

    ⚠️ 消费方约束：**stderr 大于所声称的效应 ⇒ 该结论不得表述为「提升 Xpp」**。
    """
    n = len(values)
    if n == 0:
        return {"n": 0, "mean": 0.0, "stderr": 0.0, "ci95": [0.0, 0.0]}
    m = mean(values)
    if n == 1:
        # 单样本无法估计离散度 —— 如实给 0，不要假装知道误差
        return {"n": 1, "mean": round(m, 4), "stderr": 0.0, "ci95": [round(m, 4), round(m, 4)]}
    boots = bootstrap_means(values, num_samples=num_samples, seed=seed)
    stderr = pstdev(boots)
    boots_sorted = sorted(boots)
    lo = boots_sorted[int(0.025 * len(boots_sorted))]
    hi = boots_sorted[min(int(0.975 * len(boots_sorted)), len(boots_sorted) - 1)]
    return {"n": n, "mean": round(m, 4), "stderr": round(stderr, 4),
            "ci95": [round(lo, 4), round(hi, 4)]}


def is_conclusion_supported(effect: float, stderr: float, min_ratio: float = 1.0) -> bool:
    """「提升 Xpp」这类结论是否**有统计支撑**（效应 > ``min_ratio`` 倍 stderr）。

    §5.5.4 消费方约束的代码化：默认要求效应至少与 stderr 同量级（1 倍）。
    返回 False 时报告不得写成「提升了 Xpp」。
    """
    if stderr <= 0:
        return True  # 无离散度信息时不做拦截（n=1 等情形由 n 字段暴露）
    return abs(effect) >= min_ratio * stderr
