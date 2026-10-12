"""需求 34 E3：validator 快照窗口喂裁判单测（零网络；store 全注入）。"""
from __future__ import annotations

from research_engine.agents.validator import Validator
from research_engine.net.web_fetch import FetchBudget
from research_engine.snapshots import FetchContext, use_fetch_context
from research_engine.state import ResearchFinding

SUPPORT = "量子计算在2024年取得突破性进展。"
CLAIM = "量子计算于2024年取得突破"


class StubStore:
    """只读快照 store（E3 不写）。"""

    def __init__(self, snapshots: dict, *, fail_load: bool = False):
        self._snapshots = snapshots
        self._fail_load = fail_load
        self.loads: list = []

    def save_snapshot(self, **kw):  # pragma: no cover - E3 只读
        raise AssertionError("E3 不应写快照")

    def load_snapshot(self, sha256: str):
        self.loads.append(sha256)
        if self._fail_load:
            raise RuntimeError("pg down")
        return self._snapshots.get(sha256)


def _ctx(store):
    return FetchContext(store=store, run_id="run-1",
                        budget=FetchBudget(max_fetches=15, max_bytes=1, max_seconds=1.0),
                        fetcher=None)


def _long_content(prefix: str = "无关键内容段。") -> str:
    return prefix * 700 + "…[截断]"  # >4000 字符，且不含支持片段


def _web_finding(snapshot_id: str = "sha-1", *, content: str | None = None,
                 metadata_extra: dict | None = None) -> ResearchFinding:
    metadata = {"snapshot_id": snapshot_id, "snapshot_status": "ok"}
    metadata.update(metadata_extra or {})
    return ResearchFinding(
        content=content if content is not None else _long_content(),
        source="https://e.com/a", source_type="web", confidence=0.6,
        metadata=metadata,
    )


def _to_check():
    return [{"claim": CLAIM, "finding_id": "1", "source": "https://e.com/a"}]


def _render(findings, to_check, *, evidence_index=None, stats=None, statuses=None):
    lines, _ = Validator._build_findings_lines(
        findings, to_check, evidence_index or {}, stats, statuses)
    return "\n".join(lines.values())


def test_windowed_when_snapshot_available():
    snapshot = "导语。" * 100 + SUPPORT + "结尾。" * 100
    store = StubStore({"sha-1": snapshot})
    stats: dict = {}
    statuses: dict = {}
    with use_fetch_context(_ctx(store)):
        rendered = _render([_web_finding()], _to_check(), stats=stats, statuses=statuses)
    assert "快照相关窗口" in rendered
    assert SUPPORT in rendered
    assert statuses["1"] == "windowed"
    assert stats["windowed"] == 1
    assert store.loads == ["sha-1"]


def test_fallback_truncation_without_context():
    stats: dict = {}
    statuses: dict = {}
    rendered = _render([_web_finding()], _to_check(), stats=stats, statuses=statuses)
    assert "证据截断" in rendered
    assert SUPPORT not in rendered
    assert statuses["1"] == "truncated"
    assert stats["truncated"] == 1


def test_fallback_when_snapshot_missing_or_load_fails():
    for store in (StubStore({}), StubStore({}, fail_load=True)):
        stats: dict = {}
        statuses: dict = {}
        with use_fetch_context(_ctx(store)):
            rendered = _render([_web_finding()], _to_check(), stats=stats, statuses=statuses)
        assert "证据截断" in rendered and "快照相关窗口" not in rendered
        assert statuses["1"] == "truncated"


def test_fallback_when_no_claim_overlap():
    store = StubStore({"sha-1": "完全无关的页面内容。" * 200})
    statuses: dict = {}
    with use_fetch_context(_ctx(store)):
        rendered = _render([_web_finding()], _to_check(), statuses=statuses)
    assert "证据截断" in rendered
    assert statuses["1"] == "truncated"


def test_short_evidence_untouched():
    short = "量子计算在2024年取得突破性进展（短证据直出）。"
    finding = _web_finding(content=short)
    store = StubStore({"sha-1": "任意快照" * 100})
    statuses: dict = {}
    with use_fetch_context(_ctx(store)):
        rendered = _render([finding], _to_check(), statuses=statuses)
    assert short in rendered
    assert "快照相关窗口" not in rendered and "证据截断" not in rendered
    assert statuses["1"] == "raw"


def test_windowed_via_origin_chain_keeps_partial_unknown():
    """压缩工作摘要走 origin 链：窗口化生效但 partial 状态不得被掩盖（R04）。"""
    origin = ResearchFinding(
        content=_long_content("原文背景段。"), source="https://e.com/a",
        source_type="web", confidence=0.6,
        metadata={"snapshot_id": "sha-1", "snapshot_status": "ok"},
        evidence_id="ev_origin",
    )
    working = ResearchFinding(
        content="压缩摘要", source="https://e.com/a", source_type="web",
        confidence=0.6,
        metadata={"origin_evidence_ids": ["ev_origin", "ev_missing"]},
    )
    index = {"ev_origin": origin}
    snapshot = "导语。" * 100 + SUPPORT + "结尾。" * 100
    store = StubStore({"sha-1": snapshot})
    stats: dict = {}
    statuses: dict = {}
    with use_fetch_context(_ctx(store)):
        rendered = _render([working], _to_check(), evidence_index=index,
                           stats=stats, statuses=statuses)
    assert "快照相关窗口" in rendered and SUPPORT in rendered
    assert statuses["1"] == "partial"      # UNKNOWN 判定不被窗口化掩盖
    assert stats.get("windowed", 0) == 1   # 喂料形态独立计数
    assert stats.get("partial", 0) == 1    # origin 健康度独立计数
