"""RAG 检索评测脚手架（需求 23 §8）：消融矩阵 + 分层指标 + Markdown 报告。

用法（仓库根目录）：

    python -m tools.rag_eval --dataset tools/eval_data/rag_golden_v1.json --dry-run
    python -m tools.rag_eval --dataset tools/eval_data/rag_golden_v1.json \
        --modes dense,bm25,rrf,rrf_rerank --report /tmp/rag-eval.md

口径（与需求 23 §8 一致）：

- 语料：`dataset.corpus` 的本地文件 → 结构快照 → 分块 v2；
- 指标：召回 **Recall@20**；最终排序 **MRR@5 / Hit@5**；
- golden 锚定「文档名 + 关键词（contains）」**不绑定 chunk_id**——分块版本变化后评测仍有效；
- 无答案 / 越权负例用 `forbidden` 关键词统计「伪命中」，不计入 MRR；
- `--dry-run`：确定性哈希伪向量 + 本地 BM25（零网络、零费用），验证评测管线可跑；
  真实 dense / rerank 需配置 `DASHSCOPE_API_KEY`（dense 走 API、rerank 走 gte-rerank）。

消融矩阵回答的问题（§8.2）：dense / BM25 / RRF 谁带来增益；分块 v1/v2 对比需换
`--chunker v1|v2`（v1 = 字符打包基线）；RRF / RRF+rerank 是否值得成本。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from research_engine.rag.chunker_v2 import chunk_blocks  # noqa: E402
from research_engine.rag.snapshot import snapshot_file  # noqa: E402
from research_engine.rag.tokenizer import tokenize  # noqa: E402

DIM = 256


def _hash_embed(text: str) -> List[float]:
    """确定性伪向量（dry-run 用）：字符 3-gram 哈希到 256 维并归一化。"""
    vector = [0.0] * DIM
    normalized = re.sub(r"\s+", " ", text.strip())
    for index in range(max(1, len(normalized) - 2)):
        gram = normalized[index:index + 3]
        digest = hashlib.sha1(gram.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:2], "big") % DIM] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine(a: List[float], b: List[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def _v1_chunks(text: str, doc: str) -> List[dict]:
    """分块 v1 基线（字符打包 800/无 overlap），用于 v1/v2 对照。"""
    paragraphs = [part.strip() for part in text.split("\n\n") if part.strip()]
    chunks: List[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) > 800 and current:
            chunks.append(current)
            current = para
        else:
            current = (current + "\n\n" + para) if current else para
    if current:
        chunks.append(current)
    return [{"doc": doc, "text": chunk, "embed_text": chunk, "locator": {}} for chunk in chunks]


def build_corpus(dataset: dict, chunker: str) -> List[dict]:
    chunks: List[dict] = []
    for relative in dataset["corpus"]:
        path = REPO / relative
        if chunker == "v1":
            chunks.extend(_v1_chunks(path.read_text(encoding="utf-8"), path.name))
            continue
        drafts = chunk_blocks(snapshot_file(str(path)))
        for draft in drafts:
            chunks.append({"doc": path.name, "text": draft.text,
                           "embed_text": draft.embed_text, "locator": draft.locator})
    for index, chunk in enumerate(chunks):
        chunk["identity"] = f"{chunk['doc']}#{index}"
    return chunks


def _rank_dense(query: str, chunks: List[dict], embed) -> List[dict]:
    vector = embed(query)
    scored = [{"chunk": chunk, "score": _cosine(vector, chunk["_vector"])} for chunk in chunks]
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored


def _rank_bm25(query: str, chunks: List[dict], bm25) -> List[dict]:
    scores = bm25.get_scores(tokenize(query))
    order = sorted(range(len(chunks)), key=lambda index: scores[index], reverse=True)
    return [{"chunk": chunks[index], "score": float(scores[index])} for index in order]


def _rrf(dense: List[dict], bm25: List[dict], k: int = 60, top: int = 20) -> List[dict]:
    slots: Dict[str, dict] = {}
    for rank, item in enumerate(dense[:top], start=1):
        slot = slots.setdefault(item["chunk"]["identity"], {"chunk": item["chunk"], "score": 0.0})
        slot["score"] += 1.0 / (k + rank)
    for rank, item in enumerate(bm25[:top], start=1):
        slot = slots.setdefault(item["chunk"]["identity"], {"chunk": item["chunk"], "score": 0.0})
        slot["score"] += 1.0 / (k + rank)
    return sorted(slots.values(), key=lambda item: item["score"], reverse=True)


def _evaluate(dataset: dict, chunks: List[dict], embed, bm25, mode: str,
              rerank_fn=None) -> dict:
    recall_hits = recall_total = 0
    reciprocal = 0.0
    hit5 = 0
    graded = 0
    violations = 0
    per_category: Dict[str, Dict[str, int]] = {}

    for query in dataset["queries"]:
        category = query.get("category", "general")
        bucket = per_category.setdefault(category, {"n": 0, "recall_hit": 0, "recall_total": 0,
                                                    "mrr_hit": 0, "violations": 0})
        bucket["n"] += 1
        dense = _rank_dense(query["query"], chunks, embed)
        bm25_ranked = _rank_bm25(query["query"], chunks, bm25)
        if mode == "dense":
            ranked = dense
        elif mode == "bm25":
            ranked = bm25_ranked
        else:
            ranked = _rrf(dense, bm25_ranked)
            if mode == "rrf_rerank" and rerank_fn is not None:
                reranked = rerank_fn(query["query"], [item["chunk"]["embed_text"]
                                                      for item in ranked[:20]])
                if reranked:
                    order = [index for index, _ in reranked if 0 <= index < len(ranked)]
                    ranked = [ranked[index] for index in order]

        top20 = ranked[:20]
        top5 = ranked[:5]

        # 负例 / 无答案：检查 forbidden 关键词是否伪命中
        forbidden = query.get("forbidden") or []
        if forbidden:
            violated = any(
                any(keyword in item["chunk"]["text"] for keyword in forbidden)
                for item in top5
            )
            if violated:
                violations += 1
                bucket["violations"] += 1
            continue

        expected = query.get("expected") or []
        if not expected:
            continue
        graded += 1
        matched_rank: Optional[int] = None
        for item in expected:
            recall_total += 1
            bucket["recall_total"] += 1
            found_rank = None
            for rank, candidate in enumerate(top20, start=1):
                if (candidate["chunk"]["doc"] == item["doc"]
                        and item["contains"] in candidate["chunk"]["text"]):
                    found_rank = rank
                    break
            if found_rank is not None:
                recall_hits += 1
                bucket["recall_hit"] += 1
                if matched_rank is None or found_rank < matched_rank:
                    matched_rank = found_rank
        if matched_rank is not None:
            if matched_rank <= 5:
                reciprocal += 1.0 / matched_rank
                hit5 += 1
            bucket["mrr_hit"] += 1 if matched_rank <= 5 else 0

    return {
        "mode": mode,
        "queries": len(dataset["queries"]),
        "graded": graded,
        "recall_at_20": round(recall_hits / recall_total, 4) if recall_total else None,
        "mrr_at_5": round(reciprocal / graded, 4) if graded else None,
        "hit_at_5": round(hit5 / graded, 4) if graded else None,
        "violations": violations,
        "per_category": per_category,
    }


def _render_report(results: List[dict], *, chunker: str, dry_run: bool,
                   dataset_path: str) -> str:
    lines = [
        "# RAG 检索评测报告（需求 23 §8）",
        "",
        f"- 数据集：`{dataset_path}`；分块：`{chunker}`；"
        f"模式：{'dry-run（哈希伪向量）' if dry_run else '真实（dense=API）'}",
        "",
        "| 模式 | Recall@20 | MRR@5 | Hit@5 | 负例伪命中 |",
        "|---|---|---|---|---|",
    ]
    for result in results:
        recall = "—" if result["recall_at_20"] is None else f"{result['recall_at_20']:.4f}"
        mrr = "—" if result["mrr_at_5"] is None else f"{result['mrr_at_5']:.4f}"
        hit = "—" if result["hit_at_5"] is None else f"{result['hit_at_5']:.4f}"
        lines.append(f"| {result['mode']} | {recall} | {mrr} | {hit} | {result['violations']} |")
    lines += ["", "## 分类别明细", ""]
    for result in results:
        lines.append(f"### {result['mode']}")
        lines.append("")
        lines.append("| 类别 | n | Recall | 命中@5 | 伪命中 |")
        lines.append("|---|---|---|---|---|")
        for category, bucket in sorted(result["per_category"].items()):
            recall = (f"{bucket['recall_hit'] / bucket['recall_total']:.2f}"
                      if bucket["recall_total"] else "—")
            lines.append(f"| {category} | {bucket['n']} | {recall} | "
                         f"{bucket['mrr_hit']} | {bucket['violations']} |")
        lines.append("")
    lines += [
        "> 说明：dry-run 只验证评测管线；真实结论须配置密钥后重跑（dense 走 DashScope，",
        "> rerank 走 gte-rerank）并按 §8.4 决策规则使用。参数（k=60 / top-20）为初始值。",
    ]
    return "\n".join(lines)


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="tools.rag_eval", description="RAG 检索评测脚手架")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--modes", default="dense,bm25,rrf,rrf_rerank")
    parser.add_argument("--chunker", choices=("v1", "v2"), default="v2")
    parser.add_argument("--dry-run", action="store_true",
                        help="哈希伪向量 + 本地 BM25（零网络）；rerank 跳过并注明")
    parser.add_argument("--report", default="")
    args = parser.parse_args(argv)

    dataset = json.loads((REPO / args.dataset).read_text(encoding="utf-8"))
    chunks = build_corpus(dataset, args.chunker)
    if not chunks:
        print("语料为空：检查 dataset.corpus 路径", file=sys.stderr)
        return 2

    if args.dry_run:
        embed = _hash_embed
    else:
        from research_engine.rag.retriever import HybridRetriever

        retriever = HybridRetriever()
        embed = retriever.embed_query

    from rank_bm25 import BM25Okapi

    bm25 = BM25Okapi([tokenize(chunk["text"]) for chunk in chunks])
    if args.dry_run:
        for chunk in chunks:
            chunk["_vector"] = embed(chunk["embed_text"])

    rerank_fn = None
    if not args.dry_run and "rrf_rerank" in args.modes.split(","):
        from research_engine.rag.rerank import rerank

        rerank_fn = rerank

    results = []
    for mode in [item.strip() for item in args.modes.split(",") if item.strip()]:
        if mode == "rrf_rerank" and rerank_fn is None:
            print("dry-run：跳过真实 rerank（报告按 RRF 口径标注）", file=sys.stderr)
        results.append(_evaluate(dataset, chunks, embed, bm25, mode, rerank_fn))

    report = _render_report(results, chunker=args.chunker, dry_run=args.dry_run,
                            dataset_path=args.dataset)
    if args.report:
        (REPO / args.report).write_text(report, encoding="utf-8")
        print(f"report written: {args.report}")
    else:
        print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
