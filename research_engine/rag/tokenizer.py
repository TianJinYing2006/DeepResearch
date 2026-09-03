# -*- coding: utf-8 -*-
"""统一分词器：BM25 的文档索引与查询分词共用。

背景：BM25 是 token 级的词法匹配，要求"建索引用的 token"与"查询用的 token"
严格一致。原 retriever/ingest 用 `str.split()` 按空白切分，对中文完全无效
——中文无词间空格，整句会被当成一个 token，BM25 召回基本归零。

本模块用 jieba 切中文，正则切英文，并为 jieba 未安装时提供字符级 unigram
降级（不理想但能让 BM25 部分工作），遵循项目"外部依赖懒加载 + 优雅降级"
的工程约定（ADR-0006）。
"""
from __future__ import annotations

import re
from typing import List

# 匹配 CJK 统一表意文字（基本区 + 扩展 A），含中文、日文假名也走分词路径
_CJK = re.compile(r"[\u3040-\u9fff\uf900-\ufaff]")

_jieba = None  # 懒加载：None=未尝试，False=不可用，否则为 jieba 模块


def _get_jieba():
    """惰性加载 jieba，导入失败返回 None。"""
    global _jieba
    if _jieba is None:
        try:
            import jieba  # type: ignore

            _jieba = jieba
        except Exception:  # noqa: BLE001
            _jieba = False
    return _jieba if _jieba is not False else None


def _char_unigrams(text: str) -> List[str]:
    """降级分词：中文/日文按单字切，英文按空白切。

    jieba 不可用时用。字符级 BM25 召回质量明显弱于词级（无词边界、
    停用词无法过滤），但比 split 把整句当单 token 强——至少单字能命中。
    """
    tokens = []
    for seg in re.split(r"\s+", text):
        if not seg:
            continue
        for ch in seg:
            if ch.isalnum() or _CJK.match(ch):
                tokens.append(ch.lower())
    return tokens


def tokenize(text: str) -> List[str]:
    """统一分词：中文走 jieba（不可用降级到单字），英文走空白切分。

    用法：
        BM25Okapi([tokenize(d) for d in docs])         # 建索引
        bm25.get_scores(tokenize(query))               # 查询
    保证两端用同一函数，token 一致是 BM25 正确工作的前提。
    """
    if not text:
        return []
    tokens: List[str] = []
    jieba = _get_jieba()
    for seg in re.split(r"\s+", text.strip()):
        if not seg:
            continue
        if _CJK.search(seg):
            # 含 CJK 的段走分词
            if jieba is not None:
                tokens.extend(t.strip().lower() for t in jieba.cut(seg) if t.strip())
            else:
                tokens.extend(_char_unigrams(seg))
        else:
            # 纯英文/数字段直接保留
            tokens.append(seg.lower())
    return tokens
