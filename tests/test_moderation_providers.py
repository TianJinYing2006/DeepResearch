"""P2-5a 审核 provider 抽象测试（接口 / 环境选择 / 降级 / 留痕 provider）。"""
from __future__ import annotations

import pytest
from fakes import FakeStore
from fastapi.testclient import TestClient

from web.backend import main as api
from web.backend import moderation_providers as providers
from web.backend.moderation import apply_output_gate, flag_report, scan, scan_text
from web.backend.moderation_providers import LocalRulesProvider, ModerationResult


@pytest.fixture(autouse=True)
def _reset_provider():
    providers.set_provider(None)
    yield
    providers.set_provider(None)


class FlagAllProvider:
    name = "test_flag_all"

    def scan(self, text):
        return ModerationResult(provider=self.name, matches=["flagged-by-test"])


class BrokenProvider:
    name = "test_broken"

    def scan(self, text):
        raise RuntimeError("provider down")


def test_default_provider_is_local_rules(monkeypatch):
    monkeypatch.delenv("DR_MODERATION_PROVIDER", raising=False)
    assert providers.active_provider().name == "local_rules"


def test_unknown_provider_falls_back_with_warning(monkeypatch, caplog):
    monkeypatch.setenv("DR_MODERATION_PROVIDER", "aliyun")
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", "badword")
    with caplog.at_level("WARNING", logger="deepresearch.moderation"):
        result = scan_text("contains badword")
    assert result.provider == "local_rules"
    assert result.matches == ["badword"]
    assert "unknown moderation provider" in caplog.text


def test_broken_provider_falls_back_to_local(monkeypatch, caplog):
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", "badword")
    providers.set_provider(BrokenProvider())
    with caplog.at_level("WARNING", logger="deepresearch.moderation"):
        result = scan_text("contains badword")
    assert result.provider == "local_rules" and result.matches == ["badword"]
    assert "falling back to local_rules" in caplog.text


def test_custom_provider_flags_input_and_records_provider(monkeypatch):
    store = FakeStore()
    monkeypatch.setattr(api, "store", store)
    monkeypatch.setattr(api, "AUTH_REQUIRED", False)
    providers.set_provider(FlagAllProvider())
    client = TestClient(api.app)

    response = client.post("/api/research", json={"topic": "clean topic"})

    assert response.status_code == 400
    assert response.json()["detail"]["code"] == "content_blocked"
    record = store.list_moderation(kind="input_blocked")[0]
    assert record["detail"]["provider"] == "test_flag_all"


def test_output_gate_and_flag_report_record_provider(monkeypatch):
    store = FakeStore()
    store.create_run("provrun0001", "t", {})
    providers.set_provider(FlagAllProvider())

    payload = {"result": {"report": "whatever"}}
    assert apply_output_gate(payload, "whatever") == ["flagged-by-test"]
    assert flag_report(store, "provrun0001", None, "whatever") == ["flagged-by-test"]
    record = store.list_moderation(kind="output_flagged")[0]
    assert record["detail"]["provider"] == "test_flag_all"


def test_broken_provider_marks_degraded_with_default_quarantine(monkeypatch):
    """P0-3：provider 失败不再等于放行 —— 结果带 degraded + 生效策略。"""
    monkeypatch.setenv("DR_MODERATION_BLOCKLIST", "badword")
    monkeypatch.delenv("DR_MODERATION_DEGRADED_POLICY", raising=False)
    providers.set_provider(BrokenProvider())

    result = scan_text("contains badword")

    assert result.degraded is True
    assert result.policy == "quarantine"
    assert result.failure_reason
    assert result.provider == "local_rules" and result.matches == ["badword"]


def test_unknown_provider_is_degraded_too(monkeypatch):
    monkeypatch.setenv("DR_MODERATION_PROVIDER", "aliyun")
    monkeypatch.setenv("DR_MODERATION_DEGRADED_POLICY", "fail_closed")

    result = scan_text("clean text")

    assert result.degraded is True and result.policy == "fail_closed"
    assert result.requested_provider == "aliyun"


def test_invalid_degraded_policy_falls_back_to_quarantine(monkeypatch):
    monkeypatch.setenv("DR_MODERATION_DEGRADED_POLICY", "nonsense")
    assert providers.degraded_policy() == "quarantine"


def test_scan_legacy_signature_still_supports_explicit_blocklist():
    assert scan("ABc badword", blocklist=["badword", "abc"]) == ["badword", "abc"]
    assert scan("") == []
    assert LocalRulesProvider().name == "local_rules"
