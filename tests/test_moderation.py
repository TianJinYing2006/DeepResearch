"""P7-A 内容安全单测（纯规则 + FakeStore，零外部服务）。"""
from __future__ import annotations

from fakes import FakeStore

from web.backend.moderation import flag_report, load_blocklist, scan


def test_scan_is_case_insensitive_and_dedupes():
    assert scan("ABc badword BADWORD", blocklist=["badword", "abc", "abc"]) == ["badword", "abc"]


def test_scan_default_blocklist_is_empty(monkeypatch):
    monkeypatch.delenv("DR_MODERATION_BLOCKLIST", raising=False)
    assert scan("任何内容都不应被拦") == []


def test_load_blocklist_from_env(monkeypatch):
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", " Foo , ,Bar ")
    assert load_blocklist() == ("foo", "bar")


def test_flag_report_records_and_marks(monkeypatch):
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", "badword")
    store = FakeStore()
    store.create_run("flagged01", "测试主题", {})

    matches = flag_report(store, "flagged01", "u1", "报告里包含 badword 字样")
    assert matches == ["badword"]
    assert store.get_run("flagged01")["moderation_status"] == "flagged"
    records = store.list_moderation(kind="output_flagged")
    assert records[0]["run_id"] == "flagged01"
    assert records[0]["detail"]["matches"] == ["badword"]


def test_flag_report_no_match_writes_nothing(monkeypatch):
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", "badword")
    store = FakeStore()
    store.create_run("clean01", "测试主题", {})

    assert flag_report(store, "clean01", None, "干净的报告") == []
    assert store.moderation == []
    assert store.get_run("clean01")["moderation_status"] is None
