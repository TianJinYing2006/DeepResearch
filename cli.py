# -*- coding: utf-8 -*-
"""CLI 入口。

用法：
    python cli.py "研究主题" [--instructions "附加要求"]
"""
from __future__ import annotations

import argparse
import json

from research_engine.graph import create_graph
from research_engine.observability import (  # W3（Q4/Q5/Q6）：知情打印 + flush/URL + 成本
    flush_and_get_url,
    format_cost_report,
    status_line,
)


def main():
    parser = argparse.ArgumentParser(description="DeepResearch 深度研究 Agent")
    parser.add_argument("topic", help="研究主题")
    parser.add_argument("--instructions", default="", help="用户附加要求")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整状态")
    args = parser.parse_args()

    # W3（Q4）：启动知情打印（数据去向 + 脱敏策略）
    print(status_line())

    graph = create_graph()
    print(f"开始研究：{args.topic}\n")
    try:
        result = graph.run(args.topic, args.instructions)
    finally:
        # W3（Q5）：CLI 是生命周期终点，退出前 flush + 回显 trace URL。
        # graph.trace_id 在 run 内进入前已赋值（自持主键），成功/异常（含硬闸）均可取到。
        if graph.trace_id:
            url = flush_and_get_url(graph.trace_id)
            if url:
                print(f"🔗 Trace URL: {url}")
            else:
                print("⚠️ Trace URL 不可用（观测未初始化或 flush 未尽，见日志）")

    if args.json:
        print(json.dumps(result.model_dump(), ensure_ascii=False, indent=2))
    else:
        # W2（Q1/Q6=A）：展示 render 节点产出的可审计版（类型标注+⚠️+附录+溯源），双口径计数
        print("=" * 60)
        print(result.report_display or result.report)
        print("=" * 60)
        citations = result.citations
        total = len(citations)
        existence = sum(1 for c in citations if c.existence)
        faithful = sum(1 for c in citations if c.verified)
        print(f"\n引用校验（双口径）：存在性 {existence}/{total} 条通过；"
              f"忠实度 {faithful}/{existence} 条通过（存在性通过子集上判定）")

    # W3（Q6）：成本展示（精确 token + 保守上界，人民币为主）
    print(format_cost_report(result.token_used))
    # W3（Q3=D'）：run 级对账差值（>0 = 本 run 存在未传 state 的 LLM 调用；0 = 完全一致）
    diff = graph.tokens_diff
    if diff is None:
        pass  # 异常路径：不在 CLI 打印对账（trace URL 已兜底回显）
    elif diff > 0:
        print(f"⚠️ [对账] 本 run 有 {diff:,} tokens 的 LLM 调用未计入预算（疑漏传 state，见 Q3=D'）")
    else:
        print(f"✅ [对账] 本 run 本地 token 计数与预算口径一致（{result.token_used:,} tokens）")


if __name__ == "__main__":
    main()


if __name__ == "__main__":
    main()
