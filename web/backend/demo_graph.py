"""演示用假 graph（**仅在 DR_DEMO=1 时启用**）。

目的：让 UI 效果可以被立刻看到，而不必真跑一次研究（实测 48~51 分钟 / ¥0.79~0.92）。

**不触碰任何核心链路** —— 只产出符合 `iter_run()` 契约的 `RunStep`，
供 `web/backend/runner.py` 走同一条 SSE 管线。这条路径与真实运行**共用**后端全部代码，
因此演示里看到的事件序列、降级推送、取消行为都是真的，只有内容��假的。
"""
from __future__ import annotations

import random
import time
from typing import Callable, Iterator, Optional

from research_engine.state import Citation, ResearchFinding, ResearchState
from research_engine.streaming import STOP_CANCELLED, STOP_COMPLETED, RunStep

# 演示节点序列（对齐真实图：plan → (research→critic→revise)* → research → critic → write → validate → render）
DEMO_NODES = [
    ("plan", "已分解为 4 个子问题，种子查询入队"),
    ("research", "第 1/20 跳：+6 条新发现（web 4 / rag 2）"),
    ("critic", "depth=1 裁决=augment（gap=缺少实测数据）"),
    ("revise", "回填 2 条 query 继续研究"),
    ("research", "第 2/20 跳：+5 条新发现（web 3 / arxiv 2）"),
    ("critic", "depth=2 裁决=continue"),
    ("research", "第 3/20 跳：+4 条新发现（web 2 / rag 2）"),
    ("critic", "depth=3 裁决=stop（信息已充分）"),
    ("write", "生成报告（4 个章节）"),
    ("validate", "引用校验：13 条中 12 条通过存在性"),
    ("render", "渲染可审计版（类型标注 + 附录）"),
]

# 在第 4 个节点注入一次降级，用于演示「降级实时可见」这条硬伤的解药
DEMO_DEGRADE_AT = 4


class DemoGraph:
    """假 graph：模拟真实节奏地吐 `RunStep`。"""

    def __init__(self, step_seconds: float = 1.8):
        self.step_seconds = step_seconds

    def iter_run(
        self,
        topic: str,
        user_instructions: str = "",
        thread_id: Optional[str] = None,
        should_cancel: Optional[Callable[[], bool]] = None,
    ) -> Iterator[RunStep]:
        state = ResearchState(topic=topic, user_instructions=user_instructions)
        for i, (node, msg) in enumerate(DEMO_NODES):
            time.sleep(self.step_seconds)

            state.progress.append({"stage": node, "msg": msg})
            state.depth = min(i, 3)
            state.token_used += random.randint(1200, 3800)  # noqa: S311 —— 演示数据，非密码学用途
            if node == "research":
                source = (
                    "local://knowledge-base/market-notes"
                    if i == 1
                    else "https://example.com/industry-report"
                    if i == 4
                    else "https://arxiv.org/abs/2401.00001"
                )
                state.findings.append(
                    ResearchFinding(
                        content=f"演示发现：{msg}",
                        source=source,
                        source_type="rag" if source.startswith("local://") else "web",
                        confidence=0.82,
                    )
                )

            new_deg = ()
            if i == DEMO_DEGRADE_AT:
                state.add_degradation(
                    node="researcher",
                    component="web_search",
                    reason="provider_error",
                    detail="Bocha 返回 429，已跳过该源",
                    fallback_action="empty_list",
                )
                new_deg = (state.degradation_log[-1],)

            yield RunStep(index=i, node=node, state=state,
                          new_degradations=new_deg,
                          duration_ms=int(self.step_seconds * 1000))

            # 检查点在 yield **之后**（契约 C3：当前节点允许自然结束）
            if should_cancel is not None and should_cancel():
                # 🚨 取消路径：只标 stop_reason，**不写** run_status / degradation_log
                yield RunStep(index=i + 1, node=None, state=state,
                              terminal=True, stop_reason=STOP_CANCELLED)
                return

        state.visited_sources = [
            "https://example.com/industry-report",
            "https://arxiv.org/abs/2401.00001",
            "local://knowledge-base/market-notes",
        ]
        state.reflection_log = [
            {"depth": 1, "decision": "augment", "gap": "缺少可比的实测数据"},
            {"depth": 3, "decision": "stop", "reason": "核心问题已被多源覆盖"},
        ]
        state.citations = [
            Citation(
                claim="该主题的公开证据呈现持续增长趋势。",
                source=state.visited_sources[0],
                source_type="web",
                verified=True,
                existence=True,
                verified_relaxed=True,
                supported=True,
                confidence=0.91,
                note="来源存在，且论断与原文一致。",
            ),
            Citation(
                claim="近期研究提供了可复现的方法框架。",
                source=state.visited_sources[1],
                source_type="arxiv",
                verified=True,
                existence=True,
                verified_relaxed=True,
                confidence=0.86,
                note="论文元数据与摘要已核对。",
            ),
        ]
        state.validator_stats = {
            "citation_total": 2,
            "citation_verified": 2,
            "verification_rate": 1.0,
        }
        state.report = f"""# {topic}：演示研究报告

> 这是 `DR_DEMO=1` 生成的界面演示数据，不代表真实研究结论。

## 执行摘要

围绕 **{topic}** 的公开资料显示，核心趋势已经具备多源支撑，当前证据足以形成一份可审计的初步判断。[1]

## 关键发现

- 公开证据与研究资料给出了相互印证的趋势信号。[1]
- 方法层面的近期工作提供了可复现的分析框架。[2]
- 仍应在真实运行中补充一手数据与反例检索。

## 分维度对照

| 维度 | 公开证据 | 研究资料 | 置信度 |
| --- | --- | --- | --- |
| 趋势一致性 | 强（多源相互印证） | 中（样本偏少） | 0.82 |
| 方法可复现性 | 中（缺原始数据表） | 强（给出可复现框架） | 0.86 |
| 反例覆盖 | 弱（仅见单一反例） | 中（讨论了边界条件） | 0.55 |

## 延伸阅读

- 一份标题很长的行业报告：https://example.com/industry-reports/2026/very/long/path-segments/that/never/break/generated-by-a-cms-with-extremely-long-slugs-for-seo-purposes-audit-2026

## 风险与边界

演示模式不会调用真实检索器或大模型，因此报告内容只用于验证事件流、取消、降级与结果呈现。

## 建议

1. 使用真实模式重新运行同一主题。
2. 优先检查引用校验卡片中的低置信度项。
3. 将降级记录纳入最终结论的可信度判断。
"""
        state.report_display = state.report
        yield RunStep(index=len(DEMO_NODES), node=None, state=state,
                      terminal=True, stop_reason=STOP_COMPLETED)
