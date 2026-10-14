"""需求 36：CLI 端 HITL 口径（离线单测）。

CLI 单次运行没有「挂起-回答-恢复」通道 ⇒ 与 API 非队列模式同口径：
入口强制关闭图上 HITL 开关（显式返回被关闭项，由 main 打告警）；
`graph.run()` 内的 interrupt 防御仍保留为兜底（见 test_hitl_graph.py）。
"""
from __future__ import annotations

from config import config


def test_cli_disables_graph_hitl_switches(monkeypatch):
    import cli

    monkeypatch.setattr(config.hitl, "clarify_enabled", True)
    monkeypatch.setattr(config.hitl, "plan_approval_enabled", True)
    disabled = cli._disable_unavailable_hitl()
    assert disabled == ["clarify_enabled", "plan_approval_enabled"]
    assert config.hitl.clarify_enabled is False
    assert config.hitl.plan_approval_enabled is False


def test_cli_keeps_followup_untouched(monkeypatch):
    """追问（H3）不依赖队列模式：CLI 口径只关图上两开关，不碰它。"""
    import cli

    monkeypatch.setattr(config.hitl, "followup_enabled", True)
    assert cli._disable_unavailable_hitl() == []
    assert config.hitl.followup_enabled is True


def test_cli_noop_when_already_off():
    import cli

    assert cli._disable_unavailable_hitl() == []
