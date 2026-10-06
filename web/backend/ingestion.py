"""RAG 异步摄取执行器（P0-8b）：隔离区文件 → 解析 / embedding / Qdrant。

设计（业内实践：上传登记 + 隔离区 + 状态机 + 有限重试）：

- API 只登记 `rag_ingestions` 并把文件流式写入隔离区（`DR_RAG_QUARANTINE_DIR`）；
- 本模块在 Worker 进程里领取（`FOR UPDATE SKIP LOCKED` + 租约）并处理：
  可选 AV 扫描（`DR_CLAMSCAN_BIN` 未配置则如实标注 `scan_status=skipped`）→
  解析限额（`config.rag`）→ embedding + upsert（`doc_id` 内容寻址，幂等）；
- 解析类失败（超限 / 格式）⇒ `rejected`（不重试）；供应商类失败 ⇒ 指数退避重试，
  耗尽 ⇒ `rejected`（`last_error` 可见，绝不静默）；
- 终态后删除隔离区文件；账号注销与 90 天保留期清扫也会清理文件与向量。

单机部署下隔离区是本地目录；多实例/云上需换共享卷或对象存储（P1 已登记）。
"""
from __future__ import annotations

import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

from research_engine.usage import use_usage_sink

from .otel import run_span
from .store import RunStore
from .usage import make_store_sink

DEFAULT_LEASE_SECONDS = 300
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BATCH = 3
DEFAULT_BACKOFF_CAP_SECONDS = 900
DEFAULT_RETENTION_DAYS = 90


def _log(message: str) -> None:
    print(f"[ingestion] {message}", file=sys.stderr, flush=True)


def quarantine_dir() -> str:
    configured = (os.getenv("DR_RAG_QUARANTINE_DIR") or "").strip()
    if configured:
        path = configured
    else:
        root = Path(__file__).resolve().parents[2]
        path = str(root / "local-artifacts" / "rag-quarantine")
    os.makedirs(path, exist_ok=True)
    return path


def quarantine_path(stored_name: str) -> str:
    """隔离区文件绝对路径；`stored_name` 由服务端生成，仍按 basename 防御路径穿越。"""
    return os.path.join(quarantine_dir(), os.path.basename(stored_name))


def _remove(path: str) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def _scan_file(path: str) -> str:
    """可选 AV 扫描：返回 skipped / clean / infected；未配置扫描器时如实 skipped。"""
    binary = (os.getenv("DR_CLAMSCAN_BIN") or "").strip()
    if not binary:
        return "skipped"
    try:
        result = subprocess.run([binary, "--no-summary", path],
                                capture_output=True, text=True, timeout=60)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"clamscan unavailable: {exc}") from exc
    if result.returncode == 0:
        return "clean"
    if result.returncode == 1:
        return "infected"
    raise RuntimeError(f"clamscan rc={result.returncode}: {result.stderr[:200]}")


def _default_ingester_factory():
    from research_engine.rag.ingest import DocumentIngester

    return DocumentIngester()


def process_ingestions_once(
    store: RunStore,
    *,
    ingester_factory: Optional[Callable[[], Any]] = None,
    claimed_by: str = "ingestion-worker",
    lease_seconds: int = DEFAULT_LEASE_SECONDS,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    batch: int = DEFAULT_BATCH,
) -> dict[str, int]:
    """处理一批待摄取文档。返回 `{claimed, ready, rejected, retried}`。"""
    summary = {"claimed": 0, "ready": 0, "rejected": 0, "retried": 0}
    for _ in range(batch):
        item = store.claim_next_ingestion(claimed_by, lease_seconds)
        if item is None:
            break
        summary["claimed"] += 1
        # P1-8：后台任务独立根 span（未启用 OTel 时 no-op）
        with run_span("dr.ingest", **{"dr.ingestion_id": item["ingestion_id"],
                                      "dr.doc_id": item["doc_id"]}):
            _process_item(store, item, ingester_factory, max_attempts, summary)
    return summary


def _process_item(store: RunStore, item: dict[str, Any],
                  ingester_factory: Optional[Callable[[], Any]],
                  max_attempts: int, summary: dict[str, int]) -> None:
    from research_engine.rag.ingest import IngestLimitExceeded

    from .rag_pipeline import (
        NoActiveChunksError,
        NoSnapshotError,
        build_from_chunks,
        build_from_file,
        build_from_snapshot,
    )

    ingestion_id = item["ingestion_id"]
    task = (item.get("task") or "ingest").strip() or "ingest"
    path = quarantine_path(item["stored_name"])
    keep_file = task == "ingest"  # 仅 ingest 使用隔离区文件（rechunk/reembed 走 PG 层）
    try:
        scan = "skipped"
        if keep_file:
            scan = _scan_file(path)
            if scan == "infected":
                store.mark_ingestion_rejected(ingestion_id, "malware_detected",
                                              scan_status="infected")
                _remove(path)
                summary["rejected"] += 1
                _log(f"REJECTED ingestion={ingestion_id}: malware detected")
                return
            if not os.path.isfile(path):
                raise FileNotFoundError(f"quarantine file missing: {item['stored_name']}")
        factory = ingester_factory or _default_ingester_factory
        ingester = factory()
        # P1-4：摄取期 embedding 调用逐次记账（run_id=None；doc_id 进 detail）
        with use_usage_sink(make_store_sink(
                store, run_id=None, attempt=int(item["attempts"]),
                detail={"doc_id": item["doc_id"], "task": task})):
            if task == "rechunk":
                # 需求 23：从解析快照重新分块（分块器升级）
                result = build_from_snapshot(store, ingester, doc_id=item["doc_id"],
                                             source_name=item["source"],
                                             user_id=item["user_id"])
            elif task == "reembed":
                # 需求 23：同分块换向量（模型升级 / 修复索引）
                result = build_from_chunks(store, ingester, doc_id=item["doc_id"],
                                           source_name=item["source"],
                                           user_id=item["user_id"])
            else:
                # 需求 23 管道 v2：文件 → 结构快照（落库）→ 分块 v2 → 版本化向量
                result = build_from_file(store, ingester, doc_id=item["doc_id"], path=path,
                                         source_name=item["source"],
                                         user_id=item["user_id"])
        chunks = int(result.get("chunks") or 0)
        if not chunks:
            store.mark_ingestion_rejected(ingestion_id, "empty_document", scan_status=scan)
            _remove(path)
            summary["rejected"] += 1
            return
        store.mark_ingestion_ready(ingestion_id, chunks, scan_status=scan)
        _remove(path)
        summary["ready"] += 1
    except (NoSnapshotError, NoActiveChunksError) as exc:
        # 前置缺失（无快照 / 无活动分块）：重试无意义，如实拒绝
        store.mark_ingestion_rejected(ingestion_id, f"{type(exc).__name__}: {exc}")
        _remove(path)
        summary["rejected"] += 1
        _log(f"REJECTED ingestion={ingestion_id}: {exc}")
    except IngestLimitExceeded as exc:
        store.mark_ingestion_rejected(ingestion_id, str(exc))
        _remove(path)
        summary["rejected"] += 1
        _log(f"REJECTED ingestion={ingestion_id}: {exc}")
    except ValueError as exc:
        # 解析/格式类错误：重试无意义，直接 rejected
        store.mark_ingestion_rejected(ingestion_id, f"{type(exc).__name__}: {exc}")
        _remove(path)
        summary["rejected"] += 1
        _log(f"REJECTED ingestion={ingestion_id}: {exc}")
    except Exception as exc:  # noqa: BLE001 —— 供应商/IO 类失败进入退避
        error = f"{type(exc).__name__}: {exc}"[:300]
        backoff = min(30 * (2 ** max(0, int(item["attempts"]) - 1)), DEFAULT_BACKOFF_CAP_SECONDS)
        new_status = store.mark_ingestion_retry(
            ingestion_id, error, backoff_seconds=backoff, max_attempts=max_attempts)
        if new_status == "rejected":
            summary["rejected"] += 1
            _remove(path)
            _log(f"REJECTED ingestion={ingestion_id} after retries: {error}")
        else:
            summary["retried"] += 1
            _log(f"retry ingestion={ingestion_id} attempt={item['attempts']}: {error}")


def purge_expired_documents(store: RunStore, *, vector_store: Optional[Any] = None,
                            days: Optional[int] = None, limit: int = 50) -> dict[str, int]:
    """保留期清理（隐私政策：上传文档 90 天）：删向量（验证归零）→ 删文件 → 置 deleted。

    向量删除失败时行保持 ready，下轮清扫重试（at-least-once）。
    """
    retention_days = days if days is not None else int(
        os.getenv("DR_RAG_RETENTION_DAYS", str(DEFAULT_RETENTION_DAYS)))
    before = datetime.now(UTC) - timedelta(days=retention_days)
    rows = store.list_expired_ingestions(before, limit=limit)
    summary = {"expired": len(rows), "purged": 0, "failed": 0}
    for row in rows:
        try:
            _delete_vectors(row["doc_id"], vector_store)
        except Exception as exc:  # noqa: BLE001 —— 失败保持 ready，下轮重试
            summary["failed"] += 1
            _log(f"retention purge failed doc={row['doc_id']}: {type(exc).__name__}: {exc}")
            continue
        _remove(quarantine_path(row["stored_name"]))
        store.mark_ingestion_deleted(row["ingestion_id"])
        # 需求 23：三层数据随保留期删除；修订号递增使检索缓存失效（删除后不可命中）
        store.delete_rag_layers(row["doc_id"])
        store.bump_rag_revision()
        summary["purged"] += 1
    return summary


def _delete_vectors(doc_id: str, vector_store: Optional[Any]) -> None:
    store = vector_store
    if store is None:
        from research_engine.rag.store import VectorStore

        store = VectorStore()
    reason = store.unavailable_reason
    if reason:
        raise RuntimeError(f"qdrant unavailable: {reason}")
    store.delete_by_doc(doc_id, wait=True)
    remaining = store.count_by_doc(doc_id)
    if remaining:
        raise RuntimeError(f"qdrant still has {remaining} points for doc {doc_id}")
