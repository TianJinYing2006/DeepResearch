# ADR-0003：修复 qdrant-client 新版 API 兼容问题

## 基本信息

- **编号**：0003
- **标题**：修复 qdrant-client 新版 API 兼容问题
- **日期**：2026-08-19
- **状态**：已采纳
- **涉及模块**：`research_engine/rag/store.py`

## 背景

ADR-0001 设计 RAG 模块时，`VectorStore.search` 使用了 `client.search(collection_name=..., query_vector=..., limit=...)` 的旧版 qdrant-client API。

## 问题 / 动机

真实运行测试（启用 Qdrant 后）发现 RAG 检索报错：`AttributeError: 'QdrantClient' object has no attribute 'search'`。

原因：安装的 qdrant-client 版本为 **1.19.0**，该版本**移除了 `search` 方法**，改用 `query_points` 方法。旧 API 已废弃。

## 方案

将 `VectorStore.search` 从旧 API 迁移到新 API：

```python
# 旧（已废弃）
client.search(collection_name=..., query_vector=vector, limit=top_k)

# 新
resp = client.query_points(collection_name=..., query=vector, limit=top_k, with_payload=True)
# 结果从 resp.points 取，每个 point 有 .id / .score / .payload
```

## 理由与取舍

- **为什么用 query_points**：这是 qdrant-client 1.19.0 的官方推荐 API，`query` 参数直接接受向量列表
- **为什么保留 with_payload=True**：检索结果需要 payload 里的 `text` 字段用于 BM25 融合和 Writer 上下文
- **代价**：无，API 迁移是等价的

## 影响

- RAG 检索链路恢复正常（实测摄取 1 分块、检索成功返回匹配文档）
- 其他使用 `store.search` 的模块（`retriever.py`）无需改动，因为 `search` 方法签名未变

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-08-19 | 初始记录：迁移到 query_points 新 API |
