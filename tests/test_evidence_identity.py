"""需求 35 R1：证据身份（版本化位置）与压缩分组键单测（零网络）。

验收（TBD-0/1/2 拍板）：
- 同名不同文档同片段 ⇒ 身份不同（不再误去重）；不同 generation ⇒ 身份不同；
- 同一位置内容变化 ⇒ 身份不变、content_hash 变化；
- chunk_id 格式锁定（`{doc_id}:g{n}:{i}`）与 legacy 回落（内容寻址）；
- 压缩分组按 `(doc_id, generation, sq_id)`（同名不同文档不混编）。
"""
from __future__ import annotations

import research_engine.context.manager as manager_module
from research_engine.context.manager import ContextManager
from research_engine.evidence import (
    compute_evidence_id,
    dedupe_new_findings,
    ensure_evidence_identity,
    evidence_group_key,
    rag_chunk_generation,
)
from research_engine.rag.scope import use_rag_scope
from research_engine.state import ResearchFinding


def _rag(doc_id: str, chunk_id: str, content: str, *, sq_id: str = "sq1",
         generation=None, source: str = "rag:report.pdf") -> ResearchFinding:
    metadata = {"doc_id": doc_id, "chunk_id": chunk_id, "locator": {}, "rank": 0}
    if generation is not None:
        metadata["generation"] = generation
    return ResearchFinding(content=content, source=source, source_type="rag",
                           confidence=0.7, sq_id=sq_id, metadata=metadata)


# ---- 身份分派（TBD-1）----

def test_same_filename_different_docs_not_deduped():
    a = _rag("u1:aaaa", "u1:aaaa:g1:0", "相同正文片段")
    b = _rag("u1:bbbb", "u1:bbbb:g1:0", "相同正文片段")
    with use_rag_scope(user_id="u1"):
        ensure_evidence_identity([a, b])
    assert a.evidence_id != b.evidence_id
    kept, stats = dedupe_new_findings([b], [a])
    assert stats["dropped_duplicates"] == 0 and kept == [b]


def test_same_doc_different_generation_ids_differ():
    g1 = _rag("u1:aaaa", "u1:aaaa:g1:0", "正文")
    g2 = _rag("u1:aaaa", "u1:aaaa:g2:0", "正文")
    with use_rag_scope(user_id="u1"):
        ensure_evidence_identity([g1, g2])
    assert g1.evidence_id != g2.evidence_id


def test_identity_stable_when_content_changes():
    before = _rag("u1:aaaa", "u1:aaaa:g1:0", "旧正文")
    after = _rag("u1:aaaa", "u1:aaaa:g1:0", "新正文")
    with use_rag_scope(user_id="u1"):
        ensure_evidence_identity([before, after])
    assert before.evidence_id == after.evidence_id      # 身份 = 位置（不含内容哈希）
    assert before.content_hash != after.content_hash    # 哈希单独承担复核


def test_scope_participates_in_identity():
    f1 = _rag("d:1", "d:1:g1:0", "正文")
    f2 = _rag("d:1", "d:1:g1:0", "正文")
    with use_rag_scope(user_id="u1"):
        ensure_evidence_identity([f1])
    with use_rag_scope(user_id="u2"):
        ensure_evidence_identity([f2])
    assert f1.evidence_id != f2.evidence_id


def test_legacy_rag_falls_back_to_content_addressing():
    legacy = _rag("u1:aaaa", "", "正文")  # 无 chunk_id（legacy）
    ensure_evidence_identity([legacy])
    assert legacy.evidence_id == compute_evidence_id(legacy.source, legacy.content)
    # legacy 同源同文 ⇒ 同 id（历史行为如实保留）
    legacy2 = _rag("u1:bbbb", "", "正文")
    ensure_evidence_identity([legacy2])
    assert legacy.evidence_id == legacy2.evidence_id


def test_web_keeps_content_addressing():
    web = ResearchFinding(content="网页正文", source="https://e.com/a", source_type="web")
    ensure_evidence_identity([web])
    assert web.evidence_id == compute_evidence_id(web.source, web.content)


def test_stored_id_is_not_recomputed():
    f = _rag("u1:aaaa", "u1:aaaa:g1:0", "正文")
    f.evidence_id = "ev_fixed0000000000"
    ensure_evidence_identity([f])
    assert f.evidence_id == "ev_fixed0000000000"  # 回放/导出用存储值，不重算


# ---- chunk_id 格式锁（验收）----

def test_chunk_id_format_lock():
    assert rag_chunk_generation("u1:aaaa:g3:7") == 3
    assert rag_chunk_generation("u1:aaaa:g0:0") == 0
    assert rag_chunk_generation("legacy-content-hash") is None
    assert rag_chunk_generation("") is None


# ---- 分组键（TBD-2）----

def test_group_key_rag_versioned():
    f = _rag("u1:aaaa", "u1:aaaa:g2:5", "正文", generation=2, sq_id="sq9")
    assert evidence_group_key(f) == ("u1:aaaa", 2, "sq9")
    # generation 缺失 ⇒ 从 chunk_id 解析
    f2 = _rag("u1:aaaa", "u1:aaaa:g4:0", "正文", sq_id="sq9")
    assert evidence_group_key(f2) == ("u1:aaaa", 4, "sq9")
    # 非 rag 保持 (source, sq_id)
    web = ResearchFinding(content="x", source="https://e.com/a", source_type="web", sq_id="sq1")
    assert evidence_group_key(web) == ("https://e.com/a", "sq1")


def test_compress_does_not_mix_same_name_docs(monkeypatch):
    calls = []

    class _Router:
        def fast_chat(self, system, user, state=None):  # noqa: ARG002
            calls.append(user)
            return "摘要"

    monkeypatch.setattr(manager_module, "get_router", lambda: _Router())
    a = _rag("u1:aaaa", "u1:aaaa:g1:0", "文档A的独有内容")
    b = _rag("u1:bbbb", "u1:bbbb:g1:0", "文档B的独有内容")
    with use_rag_scope(user_id="u1"):
        ensure_evidence_identity([a, b])
    compressed = ContextManager(max_findings=0).compress([a, b], "主题")
    assert len(calls) == 2  # 两个文档各自成组（不混编）
    assert any("文档A的独有内容" in c and "文档B" not in c for c in calls)
    assert any("文档B的独有内容" in c and "文档A" not in c for c in calls)
    assert len(compressed) == 2
