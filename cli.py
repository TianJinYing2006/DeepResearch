# -*- coding: utf-8 -*-
"""CLI 入口。

用法：
    python cli.py "研究主题" [--instructions "附加要求"]
"""
from __future__ import annotations

import argparse
import json

from research_engine.graph import create_graph


def main():
    parser = argparse.ArgumentParser(description="DeepResearch 深度研究 Agent")
    parser.add_argument("topic", help="研究主题")
    parser.add_argument("--instructions", default="", help="用户附加要求")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出完整状态")
    args = parser.parse_args()

    graph = create_graph()
    print(f"开始研究：{args.topic}\n")
    result = graph.run(args.topic, args.instructions)

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


if __name__ == "__main__":
    main()
