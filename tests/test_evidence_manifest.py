"""需求 35 R2：evidence manifest 构建与终局落盘单测（零网络）。"""
from __future__ import annotations

import json

from fakes import FakeStore

from research_engine.evidence_manifest import MANIFEST_VERSION, build_evidence_manifest
from research_engine.runtime_profile import RuntimeProfile, use_profile
from research_engine.state import Citation, ResearchFinding, ResearchState
from web.backend.persistence import persist_terminal


def _web_finding(snapshot_id: str = "sha-1", **kw) -> ResearchFinding:
    metadata = {"snapshot_id": snapshot_id, "snapshot_status": "ok"}
    metadata.update(kw.pop("metadata", {}))
    defaults = {"content": "网页正文", "source": "https://e.com/a", "source_type": "web",
                "confidence": 0.6}
    defaults.update(kw)
    return ResearchFinding(metadata=metadata, **defaults)


def _rag_finding() -> ResearchFinding:
    return ResearchFinding(
        content="知识库正文", source="rag:report.pdf", source_type="rag", confidence=0.7,
        metadata={"doc_id": "u1:aaaa", "generation": 2, "chunk_id": "u1:aaaa:g2:3",
                  "locator": {"page": 4}})


# ---- 有序工作清单与 origin 链（可复现编号）----

def test_ordered_working_list_and_origin_retrieval():
    raw = _web_finding(evidence_id="ev_raw_a", content_hash="hash_a")
    working = ResearchFinding(content="压缩摘要", source="https://e.com/a", source_type="web",
                              metadata={"origin_evidence_ids": ["ev_raw_a"]})
    state = ResearchState(topic="t", findings=[raw], working_findings=[working])
    manifest = build_evidence_manifest("run-1", state)
    assert manifest["manifest_version"] == MANIFEST_VERSION
    assert manifest["run_id"] == "run-1"
    assert manifest["working_count"] == 1
    entry = manifest["working_findings"][0]
    assert entry["n"] == 1
    assert entry["origin_evidence_ids"] == ["ev_raw_a"]
    # 压缩工作证据的原文可取得性沿 origin 链解析
    assert entry["retrieval"] == {"mode": "via_origins",
                                  "origins": [{"mode": "snapshot", "snapshot_id": "sha-1"}]}


def test_working_order_preserved_and_falls_back_to_raw():
    a = _web_finding(snapshot_id="sha-a", evidence_id="ev_a")
    b = _web_finding(snapshot_id="sha-b", evidence_id="ev_b", source="https://e.com/b")
    state = ResearchState(topic="t", findings=[a, b])
    manifest = build_evidence_manifest("run-2", state)
    assert [e["n"] for e in manifest["working_findings"]] == [1, 2]
    assert manifest["working_findings"][0]["retrieval"]["snapshot_id"] == "sha-a"


# ---- 原文可取得性模式 ----

def test_retrieval_modes_direct():
    web = _web_finding(evidence_id="ev_w")
    rag = _rag_finding()
    rag.evidence_id = "ev_r"
    plain = ResearchFinding(content="x", source="https://arxiv.org/abs/1", source_type="arxiv",
                            evidence_id="ev_p")
    state = ResearchState(topic="t", findings=[web, rag, plain])
    manifest = build_evidence_manifest("run-3", state)
    modes = [e["retrieval"]["mode"] for e in manifest["working_findings"]]
    assert modes == ["snapshot", "rag_generation", "content_hash_only"]
    rag_ref = manifest["working_findings"][1]["retrieval"]
    assert rag_ref["doc_id"] == "u1:aaaa" and rag_ref["generation"] == 2
    assert rag_ref["locator"] == {"page": 4}


# ---- 引用 claim span / 修复记录 / 规格版本 ----

def test_citations_and_repair_and_spec_embedded():
    citation = Citation(claim="论断", source="https://e.com/a", finding_id="1",
                        existence=True, verified=True, claim_start=0, claim_end=5,
                        quote="网页正文", char_start=10, char_end=14, snapshot_id="sha-1",
                        quote_status="located", match_level="L1")
    state = ResearchState(topic="t", findings=[_web_finding(evidence_id="ev_a")],
                          citations=[citation], repair_count=1,
                          repair_log=[{"removed_claims": 1, "removed_spans": [[0, 5]]}])
    profile = RuntimeProfile(name="quick", version="v1", max_total_hops=6, max_subquestions=2,
                             token_budget=60000, timeout_seconds=900, model="qwen-plus",
                             run_budget_cny=0.75, max_replan=1, per_subq_hop_cap=3)
    with use_profile(profile):
        manifest = build_evidence_manifest("run-4", state)
    dumped = manifest["citations"][0]
    assert dumped["claim_start"] == 0 and dumped["char_start"] == 10
    assert dumped["quote"] == "网页正文" and dumped["match_level"] == "L1"
    assert manifest["repair"]["repair_count"] == 1
    assert manifest["repair"]["log"][0]["removed_spans"] == [[0, 5]]
    assert manifest["spec"] == {"profile": "quick", "profile_version": "v1"}


def test_deterministic_except_timestamp():
    state = ResearchState(topic="t", findings=[_web_finding(evidence_id="ev_a")])
    first = build_evidence_manifest("run-5", state)
    second = build_evidence_manifest("run-5", state)
    first.pop("generated_at"), second.pop("generated_at")
    assert first == second


# ---- 终局落盘（产物 + 导出并入；FakeStore 事务语义）----

def test_persist_terminal_writes_manifest_artifact_and_export():
    store = FakeStore()
    run_id = "manifestrun01"
    store.create_run(run_id, "t", {"instructions": ""})
    assert store.update_status(run_id, "RUNNING", allowed_from=("CREATED",))
    manifest = {"manifest_version": 1, "run_id": run_id, "working_count": 0}
    result = {"report": "正文", "citations": [], "evidence_index": []}
    ok = persist_terminal(store, run_id, None, "RUN_FINISHED",
                          {"stop_reason": "completed"},
                          result=result, report="正文", meta={"run_status": "success"},
                          evidence_manifest=manifest)
    assert ok is True
    stored = json.loads(store.get_artifact(run_id, "evidence_manifest"))
    assert stored["manifest_version"] == 1
    export = json.loads(store.get_artifact(run_id, "export_json"))
    assert export["result"]["evidence_manifest"]["run_id"] == run_id  # 导出可读
