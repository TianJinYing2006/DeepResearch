"""W8 命名三分（Arm 1 遗留项收口）：三层 `status` 的**键名契约唯一真相源**。

为什么需要这个模块
------------------
W8 之前，「status」一个词在三层各说各话（§4.5 实测取证）：

| 层 | 语义 | 原名 | 取值 | 落盘位置 |
| --- | --- | --- | --- | --- |
| ① | **流程健康度** | `run_status`（Arm 1 已改） | `success` / `degraded` / `failed` | `ResearchState`（`state.py`） |
| ② | **单次调用执行结果** | `status` | 见下方两套词汇表 | `raw/*.raw.json` **顶层** |
| ③ | **指标齐备性** | `status` | `ok` / `partial` / `failed` / `timeout` | `eval/*.eval.json` **顶层** |

层②③ 同名不同义 ⇒ 单看一份 raw 就会撞见两个 `status`（顶层 + 状态快照里的
`state.status`），取证时极易张冠李戴。本模块把层②③ 的键名钉死为
`invoke_status` / `metrics_status`，写侧只产出新键。

层② 的**两套词汇表并存是事实，不是笔误**
----------------------------------------
* `_run_one()` 的**返回值**（内存对象，不落盘）：`ok` = 拿到了 raw；`failed` = 重试后仍抛异常；
* `raw/*.raw.json` **顶层**（落盘）：`done` / `incomplete`（graph 是否产出报告）、
  `failed`（同上抛异常）、`timeout`（phase1 任务级超时）。

两者同层、粗细两种粒度：`ok` ⇔ `done|incomplete`、`failed` ⇔ `failed`。
单测对两套值集合分别锁定，防止「既然同名了就把值也悄悄合并」这类静默漂移。

为什么**不回填**历史产物（关键设计决策）
----------------------------------------
层②③ 的历史落盘记录里是裸 `status`，**一律不改写**，理由三条：

1. 它们是**证据不是缓存** —— Arm 7 台账纪律：产物只读，人工 review 前不得改动；
2. `tools/w7_backfill_*.py` 的「W7 零成本可复算」依赖 raw 逐字节不变；
3. 回填会抹掉「这批数产生于旧口径」这个事实（同 Arm 6 拒绝回填 `prompt_hash`）。

⇒ 方向是 **单向新写 + 双向读（dual-read）**：写侧只产出新键，读侧新键优先、回落旧键。
   层②③ 之外残留的裸 `status` 一律登记在 `BARE_STATUS_SITES`，由单测验证「登记为真」。
"""
from __future__ import annotations

from typing import Any, Dict, Mapping, Optional, Tuple

# ---------------------------------------------------------------- 键名

#: 层② —— 单次调用的执行结果。落盘位置：`raw/*.raw.json` **顶层**。
INVOKE_STATUS = "invoke_status"

#: 层③ —— 指标齐备性。落盘位置：`eval/*.eval.json` **顶层**。
METRICS_STATUS = "metrics_status"

#: 层②③ 历史产物里的旧键名（本次命名三分之前落盘）。**只读不写**，见模块 docstring。
LEGACY_STATUS = "status"

# ---------------------------------------------------------------- 取值集合

#: `_run_one()` 返回值的取值（内存，不落盘）。
INVOKE_STATUS_RETURN_VALUES = frozenset({"ok", "failed"})

#: `raw/*.raw.json` 顶层 `invoke_status` 的取值（落盘）。
INVOKE_STATUS_STORED_VALUES = frozenset({"done", "incomplete", "failed", "timeout"})

#: `eval/*.eval.json` 顶层 `metrics_status` 的取值。
#: `failed` / `timeout` 是**层② 透传**（无 state 可评时同值下传），不是层③ 自己判出来的。
METRICS_STATUS_VALUES = frozenset({"ok", "partial", "failed", "timeout"})

# ---------------------------------------------------------------- 读侧（dual-read）


def read_invoke_status(raw: Mapping[str, Any]) -> Optional[str]:
    """读层②：新键优先，回落旧键（历史 raw）。

    返回 ``None`` 表示两条键都没有 —— 调用方须自行决定兜底语义。
    例：`phase2` 把 ``None`` 与 ``done`` / ``incomplete`` 同视为「有 state 可评」，
    与改造前 ``raw.get("status")`` 的判据逐字等价。
    """
    if INVOKE_STATUS in raw:
        return raw[INVOKE_STATUS]
    return raw.get(LEGACY_STATUS)


def read_metrics_status(res: Mapping[str, Any]) -> Optional[str]:
    """读层③：新键优先，回落旧键。

    旧键路径必须保留：`phase2` 断点续跑时会直接 ``json.loads`` 既有
    `*.eval.json` 再交给 `_summarize`，那些历史文件里只有裸 `status`。
    """
    if METRICS_STATUS in res:
        return res[METRICS_STATUS]
    return res.get(LEGACY_STATUS)


# ---------------------------------------------------------------- 未收敛裸 status 登记表

#: **层②③ 之外**残留的裸 `status` 落点。每条都是独立语义、各自有主，**不在命名三分范围**。
#: 单测逐条验证「登记为真」（落点确实存在、语义确实如述）——
#: 目的是让「还有哪些 status 没收敛、为什么」成为**可执行的事实**而不是一句口头说明。
#:
#: ⚠️ **项被收敛后要从表里移除，不要留「已解决」的说明**（2026-09-19，决策 D-17）：
#: 原第 2 条 `results/history.json` 的 `status` 自称「回归判定」、实际写 `summary.struct.regression`，
#: 而全仓从未写入 `summary.struct` ⇒ 恒为 `"PASS"` 且零读取方。已**删除该字段**
#: （写侧不再产出 + 已跟踪产物全量剥离），并由 `test_history_has_no_vacuous_status_field`
#: 反向锁住「不再存在」。留一条「已解决」条目会让 `len(BARE_STATUS_SITES)` 永远虚高、
#: 也让人误以为还有活体落点。
BARE_STATUS_SITES: Tuple[Dict[str, str], ...] = (
    {
        "site": "`ResearchState.status` → 落盘在 raw 的 state 快照内，键路径 `state.status`",
        "semantics": "graph 节点流转状态（`pending`/`planning`/`researching`/`writing`/`validating`/`done`）",
        "why_kept": "层① 之外的独立语义；它是 Pydantic schema 字段，改名会与 state 模型脱钩。"
                    "落盘时**不是裸键**（嵌在 `state` 命名空间下），读法 `metrics.py` 的 "
                    "`state.get(\"status\")` 不会与顶层 `invoke_status` 混淆",
    },
    {
        "site": "W7 容器 manifest（`w7_experiment` 产出）的 `runs[].status`",
        "semantics": "该 run 是否跑成：`done` / `skipped_gate` / `no_summary`",
        "why_kept": "W7 实验容器的自有字段，属**历史证据**，口径以 "
                    "`docs/eval-w7-conclusion.md` 为准，不回改",
    },
    {
        "site": "Langfuse trace 的 `status`（`tools/verify_w3_trace.py`）",
        "semantics": "Langfuse 服务端的 trace 状态",
        "why_kept": "外部系统的字段，本仓不产生、不拥有",
    },
)
