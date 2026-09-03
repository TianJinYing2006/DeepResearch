# ADR-0006：BM25 中文分词修复（统一 tokenizer + 优雅降级）

## 基本信息

- **编号**：0006
- **标题**：BM25 中文分词修复（统一 tokenizer + 优雅降级）
- **日期**：2026-08-20
- **状态**：已采纳
- **涉及模块**：`research_engine/rag/retriever.py`、`research_engine/rag/tokenizer.py`（新增）

## 背景

ADR-0001 设计的混合检索 = 向量检索 + BM25 关键词检索，融合取并集。BM25 是 **token 级词法匹配**：文档建索引时切出的 token 必须与查询切出的 token 严格一致，否则两边无法对齐。

原 `retriever.py` 在两处都用 `str.split()` 按空白切分：

```python
self._bm25 = BM25Okapi([t.split() for t in self._all_texts])   # 建索引
scores = self._bm25.get_scores(query.split())                  # 查询
```

## 问题 / 动机

中文没有词间空格，`"多Agent检索".split()` 返回 `["多Agent检索"]`——整句被当成一个 token。结果是：

- 中文查询切出来是 1 个超长 token，文档建索引时也几乎不可能切出同样长的整句 token，**BM25 召回基本归零**
- "混合检索"名不副实：向量检索一路在干活，BM25 一路是死的
- 调试发现：实测用 `"向量数据库"` 这类纯中文查询，旧逻辑 4 个文档的分数全是 0.0

这违背了混合检索的设计意图——向量检索覆盖**语义相似**（同义不同形），BM25 覆盖**词法匹配**（精确词、专有名词、代码标识符）。两者互补是混合检索的核心价值，BM25 失效后只剩一路，专有名词召回会显著退化。

## 方案

新建 `research_engine/rag/tokenizer.py`，提供统一 `tokenize(text)` 函数：

```python
def tokenize(text: str) -> List[str]:
    tokens = []
    jieba = _get_jieba()           # 懒加载
    for seg in re.split(r"\s+", text.strip()):
        if not seg:
            continue
        if _CJK.search(seg):        # 含中文/日文
            if jieba is not None:
                tokens.extend(jieba.cut(seg))
            else:
                tokens.extend(_char_unigrams(seg))   # 降级
        else:
            tokens.append(seg.lower())   # 纯英文/数字
    return tokens
```

`retriever.py` 两处 `split()` 都替换为 `tokenize()`，保证**索引 token 与查询 token 来自同一函数**——这是 BM25 正确工作的硬约束。

降级 `_char_unigrams`：jieba 不可用时把 CJK 切成单字。不理想（停用词无法过滤、词边界丢失），但比"整句单 token"强——至少单字能命中，BM25 仍能产生非零分数。英文段在降级路径会被切到字符级（次要退化，可接受）。

## 理由与取舍

- **为什么不把 jieba 设为强依赖**：项目工程约定是"外部依赖懒加载 + 优雅降级"（ADR-0001，Qdrant 挂了不影响主链路）。BM25 一路本来就该能降级运行，不能因为 jieba 未装就 raise。降级到字符级是保守选择：宁可召回质量下降也不阻断
- **为什么文档和查询必须用同一函数**：BM25 的核心是词项倒排索引，索引和查询的 token 词表必须对齐。这是 token 级匹配的数学前提，不是工程偏好
- **为什么不直接对全文 `jieba.cut` 而是按空白分段后再切**：保留英文段的天然空格分词（`hello world` 不该被 jieba 重新切），混合语料下质量更稳
- **代价**：jieba 首次加载约 0.5s（建词典缓存），懒加载推迟到首次 BM25 查询时，不影响启动
- **不处理的问题**：jieba 默认词典对专业术语切分不理想（如 "LangGraph" 会被切成 "Lang"/"Graph"），后续可考虑加载自定义词典或 HMM 模式，但当前召回质量已显著好于"零"

## 影响

- 新增 `research_engine/rag/tokenizer.py`：统一分词入口，对外暴露 `tokenize`
- `retriever.py`：`_load_all_texts` 与 `retrieve` 两处 `split()` → `tokenize()`，import 增加 tokenizer
- `requirements.txt`：增加 `jieba>=0.42.1`（运行时仍懒加载，未装不会崩）
- 新增 `tests/test_bm25_chinese.py`：覆盖旧逻辑复现、新逻辑命中、空串/纯英文/混合边界、jieba 不可用降级路径，离线运行无 API 调用
- 评测建议：修完本 ADR 后用 `RetrievalEvaluator` 跑真实中文语料对比召回率，BM25 一路应从 0 提升至有效值

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-08-20 | 初始记录：统一 tokenizer（jieba + 字符级降级），retriever 两处 split 替换 |
