"""W5 数据集标注草案生成器（grill Q1：AI 草案 + 人工校准双审制）。

用法：
    python tools/gen_eval_drafts.py --queries data.json --out /tmp/drafts.jsonl
    python tools/gen_eval_drafts.py --sample   # 打印样例 Q 清单（不调 LLM）

输入：queries JSON 数组 [{id, difficulty, type, query}]（id 用 q_0NN 统一编号）。
输出：每条 = 原字段 + expected_subquestions（2~4 条）+ gold_keywords（5~8 个）+ annotation。
人工校准：审查草案 → 删不合理子问题 / 补遗漏关键词 / 修表述 → 落库（annotation 保持 ai_draft + human_calibration）。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List

# tools/ 脚本需把项目根加入 sys.path（直接 python tools/xxx.py 运行时）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from research_engine.llm.client import LLMClient, build_messages  # noqa: E402

DRAFT_SYSTEM = (
    "你是评测数据集标注助手。给定一条研究型 query，生成标注草案："
    "1) expected_subquestions：这份报告为了回答该 query 应当分解出的 2~4 个子问题（具体、可回答）；"
    "2) gold_keywords：5~8 个检索时应当命中内容中的关键词（术语/实体/方法名，中文优先）。"
    '只输出 JSON：{"subquestions": [...], "gold_keywords": [...]}，不要解释。'
)


def draft_one(query: str, client: LLMClient) -> Dict[str, Any]:
    data = client.chat_json(
        build_messages(DRAFT_SYSTEM, f"query: {query}"),
        timeout=60.0,
    )
    subs = [s for s in data.get("subquestions", []) if isinstance(s, str)][:4]
    kws = [k for k in data.get("gold_keywords", []) if isinstance(k, str)][:8]
    return {"subquestions": subs, "gold_keywords": kws}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--queries", type=Path, required=True, help="queries JSON 数组")
    ap.add_argument("--out", type=Path, required=True, help="草案 jsonl 输出")
    ap.add_argument("--sample", action="store_true", help="只打印样例清单不调 LLM")
    args = ap.parse_args()

    queries: List[Dict[str, Any]] = json.loads(args.queries.read_text(encoding="utf-8"))
    if args.sample:
        for q in queries:
            print(json.dumps(q, ensure_ascii=False))
        return 0

    client = LLMClient()  # smart 档（qwen-plus）：成本低且够用
    rows = []
    for q in queries:
        draft = draft_one(q["query"], client)
        rows.append({
            **q,
            "expected_subquestions": draft["subquestions"],
            "gold_keywords": draft["gold_keywords"],
            "annotation": "ai_draft + human_calibration",
        })
        print(f"  [draft] {q['id']} {q['query'][:40]} …")
    with open(args.out, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(f"草案落盘 {args.out}：{len(rows)} 条，请人工校准后合并进 dataset.jsonl")
    return 0


if __name__ == "__main__":
    sys.exit(main())