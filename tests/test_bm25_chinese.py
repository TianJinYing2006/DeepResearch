# -*- coding: utf-8 -*-
r"""ADR-0006 修复验证：BM25 中文分词。

离线测试，不调用真实 LLM / Qdrant / Embedding。
运行：python tests/test_bm25_chinese.py

背景：BM25 是 token 级词法匹配，要求"建索引 token"与"查询 token"一致。
原 retriever 用 str.split() 按空白切分，中文无词间空格，整句被当成
单 token，BM25 召回基本归零——所谓"混合检索"实际只有向量一路在干活。
本测试复现该 bug 并验证 jieba 分词修复。
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from rank_bm25 import BM25Okapi

from research_engine.rag.tokenizer import tokenize


# 4 条中文文档（模拟 RAG 切块后的语料）
DOCS = [
    "大模型 Agent 系统的多跳检索架构设计",
    "Qdrant 向量数据库的混合检索方案对比",
    "LangGraph 状态机驱动的多 Agent 编排流程",
    "BM25 关键词检索在中文场景下的局限性",
]


def old_tokenize(text: str):
    """旧逻辑：纯空白切分。"""
    return text.split()


def main():
    print("========== 旧逻辑（str.split）建索引与查询 ==========")
    old_bm = BM25Okapi([old_tokenize(d) for d in DOCS])
    for q in ["多Agent检索", "向量数据库", "中文场景"]:
        scores = old_bm.get_scores(old_tokenize(q))
        top = scores.argmax()
        nonzero = sum(1 for s in scores if s > 0)
        print(f"  query='{q}' → scores={scores.round(2).tolist()} nonzero={nonzero} top=doc{top}")
    # 关键断言：中文查询在旧逻辑下几乎无法命中（query 是整串 1 个 token，
    # 与文档的单 token 几乎不可能相等）
    assert max(old_bm.get_scores(old_tokenize("向量数据库"))) == 0, "旧逻辑该命中为 0，实际却大于 0"
    print("  ✅ 复现：旧 split 切不出中文查询，BM25 召回归零")

    print("\n========== 新逻辑（jieba 分词）建索引与查询 ==========")
    new_bm = BM25Okapi([tokenize(d) for d in DOCS])
    cases = [
        ("向量数据库", 1),    # 应命中 doc1（Qdrant 向量数据库）
        ("多Agent编排", 2),  # 应命中 doc2（LangGraph 多 Agent 编排）
        ("BM25 中文", 3),     # 应命中 doc3（BM25 中文场景）
    ]
    for q, expected_doc in cases:
        scores = new_bm.get_scores(tokenize(q))
        top = int(scores.argmax())
        ok = top == expected_doc and scores[top] > 0
        print(f"  query='{q}' → top=doc{top} (期望 doc{expected_doc}) score={scores[top]:.3f} {'✅' if ok else '❌'}")
        assert ok, f"查询 '{q}' 未命中期望文档 doc{expected_doc}"

    print("\n========== 边界：空串、纯英文、混合 ==========")
    assert tokenize("") == []
    assert tokenize("hello world") == ["hello", "world"]
    mixed = tokenize("Agent 编排 流程")
    assert "agent" in mixed and "编排" in mixed and "流程" in mixed
    print(f"  空串=[]  纯英文小写  混合={mixed} ✅")

    print("\n========== 降级路径：jieba 不可用时仍能工作 ==========")
    import research_engine.rag.tokenizer as tok_mod
    original = tok_mod._get_jieba()
    tok_mod._jieba = False  # 模拟 jieba 未安装
    try:
        degr = tokenize("多Agent检索")
        assert degr and all(len(t) == 1 for t in degr if not t.isascii())
        print(f"  jieba=False → 字符级 unigram：{degr}")
        # 字符级降级仍能让 BM25 跑通（虽召回弱于词级）
        deg_bm = BM25Okapi([tokenize(d) for d in DOCS])
        deg_scores = deg_bm.get_scores(tokenize("向量数据库"))
        assert max(deg_scores) > 0, "字符级降级应能产生非零分数"
        print(f"  字符级 BM25 查询'向量数据库' max score={max(deg_scores):.3f} ✅")
    finally:
        tok_mod._jieba = None  # 恢复，让后续调用重新加载真实 jieba
        _ = original  # 仅消除未使用变量告警

    print("\n========== 全部断言通过：BM25 中文分词正确 ==========")


if __name__ == "__main__":
    main()
