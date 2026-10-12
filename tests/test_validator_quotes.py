"""需求 34 E2：validator 引文落盘集成单测（零网络；LLM/store 全注入）。"""
from __future__ import annotations

from research_engine.agents.validator import Validator
from research_engine.evidence import ensure_evidence_identity
from research_engine.net.web_fetch import FetchBudget
from research_engine.snapshots import FetchContext, use_fetch_context
from research_engine.state import ResearchFinding

SUPPORT = "量子计算在2024年取得突破性进展。"
CLAIM = "量子计算于2024年取得突破"


class StubStore:
    def __init__(self, snapshots: dict):
        self._snapshots = snapshots
        self.loads: list = []

    def save_snapshot(self, **kw):  # pragma: no cover - E2 只读
        raise AssertionError("E2 不应写快照")

    def load_snapshot(self, sha256: str):
        self.loads.append(sha256)
        return self._snapshots.get(sha256)


def _ctx(store):
    return FetchContext(store=store, run_id="run-1",
                        budget=FetchBudget(max_fetches=15, max_bytes=1, max_seconds=1.0),
                        fetcher=None)


def _verdict(claim: str = CLAIM) -> dict:
    return {"citations": [{"finding_id": "1", "claim": claim, "claim_echo": claim,
                           "faithful": True, "supported": False,
                           "confidence": 0.9, "note": ""}]}


def _web_finding(snapshot_id: str | None = "sha-1") -> ResearchFinding:
    metadata = {"snapshot_status": "ok"}
    if snapshot_id:
        metadata["snapshot_id"] = snapshot_id
    return ResearchFinding(content="标题\n\n正文片段", source="https://e.com/a",
                           source_type="web", confidence=0.6, metadata=metadata)


def _validate(monkeypatch, findings, *, evidence=None, report=None):
    monkeypatch.setattr("research_engine.llm.client.LLMClient.chat_json",
                        lambda self, messages, state=None, schema=None: _verdict())
    validator = Validator()
    return validator, validator.validate(
        report or f"{CLAIM} [来源: 1]", findings, evidence=evidence)


def test_validate_attaches_verbatim_quote(monkeypatch):
    snapshot = f"背景资料。\n\n{SUPPORT}\n\n后续内容。"
    store = StubStore({"sha-1": snapshot})
    with use_fetch_context(_ctx(store)):
        validator, citations = _validate(monkeypatch, [_web_finding()])
    c = citations[0]
    assert c.quote_status == "located"
    assert c.snapshot_id == "sha-1"
    assert c.quote and snapshot[c.char_start:c.char_end] == c.quote  # 原文切片可复算
    assert SUPPORT in c.quote
    assert c.match_level in ("L0", "L1", "L2")
    stats = validator.last_validation_stats
    assert stats["quote_located_count"] == 1
    assert stats["quote_not_found_count"] == 0


def test_validate_quote_not_found_on_no_overlap(monkeypatch):
    store = StubStore({"sha-1": "完全无关的页面内容。"})
    with use_fetch_context(_ctx(store)):
        validator, citations = _validate(monkeypatch, [_web_finding()])
    c = citations[0]
    assert c.quote_status == "not_found"
    assert c.quote == "" and c.char_start == -1
    assert c.snapshot_id == "sha-1"  # 快照存在但引文定位失败（进 E5 分级）
    stats = validator.last_validation_stats
    assert stats["quote_located_count"] == 0
    assert stats["quote_not_found_count"] == 1


def test_validate_no_snapshot_fields_stay_empty(monkeypatch):
    validator, citations = _validate(monkeypatch, [_web_finding(snapshot_id=None)])
    c = citations[0]
    assert c.quote_status == "" and c.snapshot_id == "" and c.quote == ""
    assert validator.last_validation_stats["quote_located_count"] == 0


def test_validate_quote_via_origin_chain(monkeypatch):
    """压缩工作摘要：引文从 origin 链上的快照定位。"""
    snapshot = f"导语。\n\n{SUPPORT}\n\n结尾。"
    raw = ResearchFinding(content="原文片段", source="https://e.com/a",
                          source_type="web", confidence=0.6,
                          metadata={"snapshot_id": "sha-1", "snapshot_status": "ok"})
    ensure_evidence_identity([raw])
    working = ResearchFinding(content="压缩摘要", source="https://e.com/a",
                              source_type="web", confidence=0.6,
                              metadata={"origin_evidence_ids": [raw.evidence_id]})
    store = StubStore({"sha-1": snapshot})
    with use_fetch_context(_ctx(store)):
        validator, citations = _validate(monkeypatch, [working], evidence=[raw])
    c = citations[0]
    assert c.quote_status == "located"
    assert c.snapshot_id == "sha-1"
    assert snapshot[c.char_start:c.char_end] == c.quote
    assert SUPPORT in c.quote
