"""需求 23 摄取管道 v2：解析快照 → 分块 v2 → 分代构建 → 校验 → 切换 → 清理。

任务类型（`rag_ingestions.task`）：

- ``ingest``：隔离区文件 → 结构快照 → 分块 v2 → 向量；
- ``rechunk``：解析快照 → 分块 v2（分块器升级）→ 向量；
- ``reembed``：活动代 chunks → 新代向量（同分块换 embedding 模型 / 修复索引）。

版本化重建（§3.3）：

1. 新代先写（向量 ``active=false``，检索不可见）；
2. 校验：PG 块数 == 期望；Qdrant 该代点数 == 期望；
3. Qdrant 标记新代 ``active=true``（旧代此刻仍 active —— 短暂双可见，无不可检索窗口）；
4. PG **单事务切换**（旧代 → retired，提交条件校验防过期 worker）；
5. Qdrant 退役旧代；清理由 Worker 周期任务兜底（失败可重试）。

失败路径：``building → failed``，检索继续使用旧 active；孤儿向量由清扫按代删除。
"""
from __future__ import annotations

import sys
from typing import Any, Optional

from qdrant_client.models import PointStruct

from config import config
from research_engine.rag.chunker_v2 import ChunkDraft, chunk_blocks
from research_engine.rag.ids import stable_id
from research_engine.rag.ingest import IngestLimitExceeded
from research_engine.rag.snapshot import SnapshotBlock, snapshot_file

#: text-embedding-v3 默认维度；**维度变化 ⇒ 新 collection**（§3.3，禁止原地换维）
EMBEDDING_DIM = 1024


class NoSnapshotError(ValueError):
    """rechunk 前置缺失（无解析快照）→ API 409 / 队列 rejected（不重试）。"""


class NoActiveChunksError(ValueError):
    """reembed 前置缺失（无活动分块）。"""


def _log(message: str) -> None:
    print(f"[rag-pipeline] {message}", file=sys.stderr, flush=True)


def _rows_from_text(text: str) -> tuple:
    lines = [line for line in text.splitlines() if line.strip()]
    return tuple(tuple(line.split("\t")) for line in lines)


def blocks_from_snapshot_rows(rows: list[dict[str, Any]]) -> list[SnapshotBlock]:
    """PG 快照行 → 结构块（表格行从 TSV 文本还原，供分块 v2 重复表头）。"""
    blocks: list[SnapshotBlock] = []
    for row in rows:
        kind = row["kind"]
        blocks.append(SnapshotBlock(
            kind=kind,
            text=row["text"],
            title_path=tuple(row.get("title_path") or []),
            locator=dict(row.get("locator") or {}),
            rows=_rows_from_text(row["text"]) if kind in ("table", "sheet") else (),
        ))
    return blocks


def _persist_generation(store, ingester, *, doc_id: str, source_name: str,
                        drafts: list[ChunkDraft], user_id: Optional[str],
                        tenant_id: Optional[str], visibility: str) -> dict[str, Any]:
    generation = store.create_index_generation(
        doc_id,
        chunker_version=config.rag.chunker_version,
        embedding_model=config.rag.embedding_model,
        embedding_dim=EMBEDDING_DIM,
    )
    chunk_rows = []
    for index, draft in enumerate(drafts):
        chunk_rows.append({
            "chunk_index": index,
            "chunk_id": f"{doc_id}:g{generation}:{index}",
            "text": draft.text,
            "embed_text": draft.embed_text,
            "title_path": list(draft.title_path),
            "locator": draft.locator,
        })
    try:
        vectors = ingester.embed([row["embed_text"] for row in chunk_rows])
        points = []
        for index, row in enumerate(chunk_rows):
            payload = {
                "doc_id": doc_id,
                "generation": generation,
                "chunk_index": index,
                "chunk_id": row["chunk_id"],
                "text": row["text"],
                "title_path": row["title_path"],
                "locator": row["locator"],
                "source": source_name,
                "visibility": visibility,
                "active": False,  # 切换前不可检索
            }
            if user_id:
                payload["user_id"] = user_id
            if tenant_id:
                payload["tenant_id"] = tenant_id
            points.append(PointStruct(
                id=stable_id(f"{doc_id}:g{generation}", index),
                vector=vectors[index],
                payload=payload,
            ))
        ingester.store.upsert(points)
        store.insert_rag_chunks(doc_id, generation, chunk_rows)
        if store.count_rag_chunks(doc_id, generation) != len(chunk_rows):
            raise RuntimeError("chunk count mismatch (pg)")
        if ingester.store.count_by_generation(doc_id, generation) != len(points):
            raise RuntimeError("chunk count mismatch (qdrant)")
        ingester.store.set_generation_active(doc_id, generation, True)
        try:
            retired = store.activate_index_generation(doc_id, generation)
        except Exception:
            # PG 提交失败：立即撤销新代活动标记，不留「失败代与旧代同时可见」窗口
            # （后台清扫仍会兜底重试；此处保证提交失败即刻回滚可见性）
            try:
                ingester.store.set_generation_active(doc_id, generation, False)
            except Exception:  # noqa: BLE001 —— 回滚失败交给清扫
                pass
            raise
        for old_generation in retired:
            ingester.store.set_generation_active(doc_id, old_generation, False)
        return {"generation": generation, "chunks": len(chunk_rows), "retired": retired}
    except Exception as exc:  # noqa: BLE001 —— 构建失败留现场，旧版本继续可用
        store.fail_index_generation(doc_id, generation, f"{type(exc).__name__}: {exc}")
        raise


def _build_from_blocks(store, ingester, *, doc_id: str, source_name: str,
                       blocks: list[SnapshotBlock], user_id: Optional[str],
                       tenant_id: Optional[str], visibility: str) -> dict[str, Any]:
    drafts = chunk_blocks(blocks, max_tokens=int(config.rag.chunk_tokens),
                          overlap_tokens=int(config.rag.chunk_overlap_tokens))
    if len(drafts) > config.rag.max_chunks:
        raise IngestLimitExceeded(f"分块数 {len(drafts)} 超过上限 {config.rag.max_chunks}")
    if not drafts:
        return {"generation": None, "chunks": 0, "retired": []}
    return _persist_generation(store, ingester, doc_id=doc_id, source_name=source_name,
                               drafts=drafts, user_id=user_id, tenant_id=tenant_id,
                               visibility=visibility)


def build_from_file(store, ingester, *, doc_id: str, path: str, source_name: str,
                    user_id: Optional[str] = None, tenant_id: Optional[str] = None,
                    visibility: str = "private") -> dict[str, Any]:
    """ingest：文件 → 快照（落库）→ 分块 v2 → 向量（版本化）。"""
    blocks = snapshot_file(path)
    total_chars = sum(len(block.text) for block in blocks)
    if total_chars > config.rag.max_chars:
        raise IngestLimitExceeded(
            f"抽取字符数超过上限 {config.rag.max_chars}（当前 {total_chars}）")
    store.replace_parse_snapshot(doc_id, [
        {"block_index": index, "kind": block.kind,
         "title_path": list(block.title_path), "locator": block.locator, "text": block.text}
        for index, block in enumerate(blocks)
    ])
    return _build_from_blocks(store, ingester, doc_id=doc_id, source_name=source_name,
                              blocks=blocks, user_id=user_id, tenant_id=tenant_id,
                              visibility=visibility)


def build_from_snapshot(store, ingester, *, doc_id: str, source_name: str,
                        user_id: Optional[str] = None, tenant_id: Optional[str] = None,
                        visibility: str = "private") -> dict[str, Any]:
    """rechunk：解析快照 → 分块 v2（换分块器）。"""
    rows = store.get_parse_snapshot(doc_id)
    if not rows:
        raise NoSnapshotError("该文档没有解析快照（历史文档或快照缺失），请重新上传后再操作")
    blocks = blocks_from_snapshot_rows(rows)
    return _build_from_blocks(store, ingester, doc_id=doc_id, source_name=source_name,
                              blocks=blocks, user_id=user_id, tenant_id=tenant_id,
                              visibility=visibility)


def build_from_chunks(store, ingester, *, doc_id: str, source_name: str,
                      user_id: Optional[str] = None, tenant_id: Optional[str] = None,
                      visibility: str = "private") -> dict[str, Any]:
    """reembed：活动代 chunks → 新代向量（同分块）。"""
    generation = store.get_active_generation(doc_id)
    if generation is None:
        raise NoActiveChunksError("该文档没有活动分块，无法重新嵌入")
    rows = store.get_rag_chunk_rows(doc_id, generation)
    if not rows:
        raise NoActiveChunksError("该文档没有活动分块，无法重新嵌入")
    drafts = [
        ChunkDraft(text=row["text"], embed_text=row["embed_text"],
                   title_path=tuple(row.get("title_path") or []),
                   locator=dict(row.get("locator") or {}))
        for row in rows
    ]
    return _persist_generation(store, ingester, doc_id=doc_id, source_name=source_name,
                               drafts=drafts, user_id=user_id, tenant_id=tenant_id,
                               visibility=visibility)


def cleanup_stale_generations(store, *, vector_store: Optional[Any] = None,
                              limit: int = 20) -> dict[str, int]:
    """清理 retired / failed 代：删向量（验证归零）→ 删产物与状态行（可重试）。"""
    if vector_store is None:
        from research_engine.rag.store import VectorStore

        vector_store = VectorStore()
    rows = store.list_retired_generations(limit=limit)
    summary = {"scanned": len(rows), "cleaned": 0, "failed": 0}
    for row in rows:
        doc_id, generation = row["doc_id"], int(row["generation"])
        try:
            reason = vector_store.unavailable_reason
            if reason:
                raise RuntimeError(f"qdrant unavailable: {reason}")
            vector_store.delete_by_generation(doc_id, generation, wait=True)
            if vector_store.count_by_generation(doc_id, generation) != 0:
                raise RuntimeError("qdrant still has points")
            store.mark_generation_cleaned(doc_id, generation)
            summary["cleaned"] += 1
        except Exception as exc:  # noqa: BLE001 —— 下轮清扫重试（at-least-once）
            summary["failed"] += 1
            _log(f"cleanup failed doc={doc_id} g={generation}: {type(exc).__name__}: {exc}")
    return summary
