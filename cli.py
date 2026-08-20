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
        print("=" * 60)
        print(result.report)
        print("=" * 60)
        verified = sum(1 for c in result.citations if c.verified)
        print(f"\n引用校验：{verified}/{len(result.citations)} 条通过存在性校验")


if __name__ == "__main__":
    main()
