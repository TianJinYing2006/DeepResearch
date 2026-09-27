"""usage sink 落库实现（P1-4）：把 `research_engine.usage` 的 emit 写进 `usage_ledger`。

成本口径（与展示一致，必须诚实标注）：
- `llm`：本地价格表按**最贵 output 档**估算（上界）⇒ `cost_source=estimate`；
- `embedding` / `search`：按次计费，本地未建模金额 ⇒ 仅记请求数与 tokens，
  `cost_source=per_call`（金额 0，不得假装精确）；
- 未来接入供应商实报成本时写 `cost_source=provider`。
"""
from __future__ import annotations

from typing import Any, Optional

from config import config
from research_engine.usage import UsageRecord


def _llm_cost_cny(model: str, total_tokens: int) -> float:
    pricing = (config.llm.pricing or {}).get(model or "", {})
    unit = float(pricing.get("output", 0.0))
    return round(total_tokens / 1000.0 * unit, 6)


def make_store_sink(store, *, run_id: Optional[str], attempt: int,
                    detail: Optional[dict[str, Any]] = None):
    """构造写入任务库的 usage sink（sink 异常由 emit_usage 静默吞掉）。"""

    def sink(record: UsageRecord) -> None:
        if record.kind == "llm":
            cost, source = _llm_cost_cny(record.model, record.total_tokens), "estimate"
        else:
            cost, source = 0.0, "per_call"
        store.record_usage(
            run_id=run_id, attempt=attempt, kind=record.kind, provider=record.provider,
            model=record.model, role=record.role, input_tokens=record.input_tokens,
            output_tokens=record.output_tokens, total_tokens=record.total_tokens,
            cost_estimate_cny=cost, cost_source=source,
            request_id=record.request_id, detail=detail)

    return sink
