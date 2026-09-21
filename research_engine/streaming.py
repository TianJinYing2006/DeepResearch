"""W9 流式运行的事件载体（需求 9 §7.1）。

**为什么单独成文件**：`iter_run()` 需要一种「逐个节点交付快照」的载体，
但 W8 已冻结 `research_engine/state.py` 的判定口径 ⇒ 新增类型放进**独立模块**，
既不污染 `state.py` 的字段语义，也不污染 `graph.py` 的编排逻辑。

**本模块不承载任何判定逻辑** —— 只做数据搬运与增量切片。
所有故障判定仍由 `state.py` / `_recover_from_exception` 负责。
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Optional, Tuple

if TYPE_CHECKING:  # 仅类型检查期导入，避免运行期循环依赖
    from research_engine.state import DegradationEntry, ResearchState

# 终止原因 —— 与 graph.DeepResearchGraph.last_stop_reason 配套。
# ⚠️ 注意：这里**没有** "cancelled" 对应的 run_status。
# 取消是传输层/UI 层的终止语义，**不得**写入 ResearchState.run_status
# （RUN_STATUSES 只有 success/degraded/failed 三态；把取消记成故障会污染
#  W8「故障可归因率 100%」这条 A 类验收）。见需求 9 §7.3.3。
STOP_RUNNING = "running"
STOP_COMPLETED = "completed"
STOP_CANCELLED = "cancelled"
STOP_ERROR = "error"

STOP_REASONS = (STOP_RUNNING, STOP_COMPLETED, STOP_CANCELLED, STOP_ERROR)


@dataclass(frozen=True)
class RunStep:
    """一次 graph 节点执行完成后的交付单元。

    Attributes:
        index: 节点序号（0-based）；终局 step 复用最后一个序号。
        node: 刚完成的节点名（`plan` / `research` / `critic` / `revise` /
            `write` / `validate` / `render`）。取自 `progress[-1]["stage"]` ——
            依据是实测的「`progress` 条目与 graph 节点执行 1:1」（需求 9 §5.4.1）。
            终局 step 与异常 step 为 ``None``。
        state: 该节点完成后的**完整** state 快照（来自 `stream_mode="values"`）。
        new_degradations: 本节点**新增**的降级条目（增量，非全量）。
            利用 `degradation_log` 的 ``operator.add`` reducer 做切片得到。
        duration_ms: 本节点耗时（毫秒），落地 §5.4.1 的时间戳行动项 ——
            用于校准「取消最坏等待时间」。
        terminal: 是否为终局 step（正常结束 / 取消 / 异常都会产出且仅产出一个）。
        stop_reason: ``STOP_*`` 之一。非终局 step 恒为 ``STOP_RUNNING``。
    """

    index: int
    node: Optional[str]
    state: ResearchState
    new_degradations: Tuple[DegradationEntry, ...] = ()
    duration_ms: int = 0
    terminal: bool = False
    stop_reason: str = STOP_RUNNING

    @property
    def run_status(self) -> str:
        """透传当前快照的 `run_status`（三态之一），本类不参与判定。"""
        return self.state.run_status
