# ADR-0007：修复 Qdrant 点 ID 跨进程不稳定（hash 改 hashlib）

## 基本信息

- **编号**：0007
- **标题**：修复 Qdrant 点 ID 跨进程不稳定（hash() 改 hashlib）
- **日期**：2026-08-20
- **状态**：已采纳
- **涉及模块**：`research_engine/rag/ingest.py`、`research_engine/rag/ids.py`（新增）

## 背景

ADR-0001 的摄取器用 `hash()` 生成 Qdrant 点 ID：

```python
PointStruct(id=hash(f"{doc_id}:{i}") % (2**63), ...)
```

Qdrant 的 `upsert` 按 ID 覆盖——相同 ID 写入新数据会替换旧数据，不同 ID 则新增。这套语义本应支持"同一文档重摄取 = 覆盖更新"，达成摄取幂等性。

## 问题 / 动机

Python 内置 `hash()` 对字符串每个进程随机加盐（环境变量 `PYTHONHASHSEED`，默认 random）。同一字符串在不同进程里返回**不同**的 hash 值。后果链：

1. Web 进程 A 摄取 `report.pdf` → 生成 ID1 → 写入 Qdrant
2. Web 进程 B（重启后或不同 worker）再次摄取同一 `report.pdf` → 生成 ID2（≠ID1）
3. Qdrant upsert 按 ID 覆盖，但 ID1 ≠ ID2 → **不覆盖，新增重复点**
4. 重复点堆积，向量检索可能返回同一文档的多个过时版本，BM25 倒排表里同一文本出现多次，分数失真

实测复现（`tests/test_stable_point_id.py` 场景 2）：
```
PYTHONHASHSEED=1 : hash("doc1:0") = 7386541451227589972
PYTHONHASHSEED=2 : hash("doc1:0") = 2043187423484051542   ← 完全不同
```

这是一个**静默数据污染**：摄取不报错、检索不报错，但语料里悄悄累积脏数据。在生产 RAG 系统里这类 bug 极难排查（数据是"对的"，只是不幂等）。

## 方案

新建 `research_engine/rag/ids.py`，用 SHA1 摘要生成稳定 ID：

```python
def stable_id(doc_id: str, chunk_index: int) -> int:
    raw = f"{doc_id}:{chunk_index}".encode("utf-8")
    digest = hashlib.sha1(raw).digest()       # 20 bytes
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)
```

`ingest.py` 改为 `from research_engine.rag.ids import stable_id`。

## 理由与取舍

- **为什么抽到独立模块 `ids.py` 而非留在 ingest.py**：ingest.py 重依赖 qdrant_client / openai，而 ID 生成是纯计算。独立模块后，单元测试不装这些重依赖也能直接验证"跨进程稳定"这个核心断言——这正是测试聚焦的需求。关注点分离是附带收益
- **为什么 SHA1 而非 MD5**：MD5 已不推荐用于任何安全相关场景；SHA1 在非加密用途（ID 生成）完全够用且普及。也可以用 SHA256 截断，但无实质差异
- **为什么取前 8 字节（64-bit）再 mask 到 63-bit**：Qdrant 的 unsigned int ID 上限是 2^63-1（实际为 Qdrant 内部 storage 约束）。8 字节给 64 bit，mask 掉最高位落在 63-bit 内。碰撞空间仍有 2^63，对文档分块场景足够
- **为什么不直接用 UUID5 字符串**：Qdrant 同时接受 int 和 UUID 字符串 ID。当前代码已经是 int 形式，改 hashlib 保持类型不变，改动最小
- **代价**：摄取前 ID 算 SHA1 多一次哈希（μs 级，可忽略）。换取的是真正的幂等性

## 影响

- 新增 `research_engine/rag/ids.py`：`stable_id()` 函数，无外部依赖
- `ingest.py`：删去本地 hash()，改为 import；删除多余的 `import hashlib`（已移入 ids.py）
- 新增 `tests/test_stable_point_id.py`：5 场景验证——跨进程稳定、旧 bug 对照复现、无碰撞退化、合法范围、幂等推断。**用 subprocess 启动新进程直接验证"跨进程一致"这个核心断言**（单进程测试无法复现 PYTHONHASHSEED 问题）
- 行为变化：同一文档重摄取现在会**覆盖**旧点而非新增，BM25 倒排表不再累积同一文本的多份副本

## 变更记录

| 日期 | 变更说明 |
|------|---------|
| 2026-08-20 | 初始记录：hash() → SHA1 稳定 ID，独立 ids.py 模块，跨进程幂等摄取 |
