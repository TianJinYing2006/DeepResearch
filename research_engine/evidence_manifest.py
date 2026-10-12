"""版本化 evidence manifest（需求 35 R2 / TBD-3 拍板，2026-10-12）。

两个验收层（DoD R2）：

- **(a) 完整性可校验（任意时点）**：Writer 实际收到的**有序工作证据清单**
  （``Finding N`` ↔ 稳定 ID / origin IDs / content_hash / 来源身份）+ 引用 claim span +
  修复前后记录 + 规格版本 —— 压缩/顺序变化后编号仍可重建、内容可核验；
- **(b) 原文可重新取得（保留期内）**：web 经 ``snapshot_id`` 回查（快照保留期）；
  RAG 经 ``doc_id/generation/chunk_id/locator`` 回查（generation 保留期）；
  超期后只保证 (a)（``retrieval.mode = content_hash_only``）。

存储（TBD-3）：``run_artifacts`` kind=``evidence_manifest``（JSON；终局**同事务**写入，
见 ``web/backend/persistence.py``）；纯函数构建（零 LLM/零网络）、确定性
（除 ``generated_at``）。
"""
from __future__ import annotations

import time
from typing import Any, Dict, List

from research_engine.runtime_profile import current_profile

MANIFEST_VERSION = 1


def _retrieval_ref(finding: Any) -> Dict[str, Any]:
    """单条证据的「原文可取得」引用（mode + 定位要素；不承担保留期判断）。"""
    metadata = getattr(finding, "metadata", None) or {}
    source_type = getattr(finding, "source_type", "")
    if source_type == "web" and metadata.get("snapshot_id"):
        return {"mode": "snapshot", "snapshot_id": str(metadata["snapshot_id"])}
    if source_type == "rag" and metadata.get("doc_id"):
        return {
            "mode": "rag_generation",
            "doc_id": str(metadata.get("doc_id")),
            "generation": metadata.get("generation"),
            "chunk_id": str(metadata.get("chunk_id") or ""),
            "locator": metadata.get("locator") or {},
        }
    return {"mode": "content_hash_only"}


def _resolve_retrieval(finding: Any, raw_index: Dict[str, Any]) -> Dict[str, Any]:
    """解析「原文可取得」引用：自身直取；压缩工作证据沿 origin 链取（via_origins）。"""
    direct = _retrieval_ref(finding)
    if direct["mode"] != "content_hash_only":
        return direct
    metadata = getattr(finding, "metadata", None) or {}
    origins: List[Dict[str, Any]] = []
    for evidence_id in metadata.get("origin_evidence_ids") or []:
        raw = raw_index.get(str(evidence_id))
        if raw is None:
            continue
        ref = _retrieval_ref(raw)
        if ref["mode"] != "content_hash_only":
            origins.append(ref)
    if origins:
        return {"mode": "via_origins", "origins": origins}
    return direct


def build_evidence_manifest(run_id: str, state: Any) -> Dict[str, Any]:
    """从终局 state 构建 manifest（调用方：runner / worker 终局路径）。

    ``working_findings``（Writer 编号基准；无压缩时 = 原文层）**有序**入清单——
    这是「数字引用可复现」的关键：仅存 origin IDs 无法在压缩/顺序变化后重建编号。
    压缩工作证据的原文可取得性沿 origin 链解析（``retrieval.mode=via_origins``）。
    """
    findings: List[Any] = list(getattr(state, "findings", None) or [])
    raw_index = {str(getattr(f, "evidence_id", "")): f for f in findings if getattr(f, "evidence_id", "")}
    working: List[Any] = list(
        getattr(state, "working_findings", None) or findings or [])
    entries: List[Dict[str, Any]] = []
    for number, finding in enumerate(working, 1):
        metadata = getattr(finding, "metadata", None) or {}
        entries.append({
            "n": number,
            "evidence_id": getattr(finding, "evidence_id", ""),
            "content_hash": getattr(finding, "content_hash", ""),
            "source": finding.source,
            "source_type": finding.source_type,
            "sq_id": getattr(finding, "sq_id", ""),
            "origin_evidence_ids": list(metadata.get("origin_evidence_ids") or []),
            "retrieval": _resolve_retrieval(finding, raw_index),
        })
    profile = current_profile()
    return {
        "manifest_version": MANIFEST_VERSION,
        "run_id": run_id,
        "generated_at": time.time(),
        "spec": {
            "profile": getattr(profile, "name", "") or "default",
            "profile_version": getattr(profile, "version", "") or "",
        },
        "retrieval_policy": {
            "snapshot": "web 快照经 snapshot_id 可回查（本体清理任务为 E1 设计留项，未实现前长期保留）",
            "rag_generation": "RAG generation 保留期（旧 generation 清理后仅可校验）",
            "content_hash_only": "仅完整性可校验（原文不可重新取得）",
        },
        "working_count": len(entries),
        "working_findings": entries,
        "citations": [
            citation.model_dump(mode="json")
            for citation in (getattr(state, "citations", None) or [])
        ],
        "repair": {
            "repair_count": int(getattr(state, "repair_count", 0) or 0),
            "log": list(getattr(state, "repair_log", None) or []),
        },
    }
