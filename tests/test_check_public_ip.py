# -*- coding: utf-8 -*-
"""公开仓脱敏守卫（tools/check_public_ip.py）单测。"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parent.parent / "tools" / "check_public_ip.py"
_spec = importlib.util.spec_from_file_location("check_public_ip", _SCRIPT)
assert _spec is not None and _spec.loader is not None
checker = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(checker)


def _ip(*parts: str) -> str:
    """在运行时拼出 IP 字面量，避免守卫扫描本测试文件时误报（自指陷阱）。"""
    return ".".join(parts)


def test_find_public_ips_detects_public() -> None:
    leak = _ip("82", "156", "88", "246")
    assert checker.find_public_ips(f"host {leak}") == [(1, leak)]

    public = _ip("8", "8", "8", "8")
    # 1.1.1.1 / 1.2.3.4 等夹具在白名单内，只应命中公开示例
    assert checker.find_public_ips(f"a {public}\nb 1.1.1.1") == [(1, public)]


def test_find_public_ips_allows_private_and_doc() -> None:
    assert checker.find_public_ips("127.0.0.1 10.1.2.3 192.168.1.1 0.0.0.0") == []
    assert checker.find_public_ips("192.0.2.10 198.51.100.7 203.0.113.9") == []
    assert checker.find_public_ips("无 IP") == []


def test_fixture_allowlist_and_version_false_positive() -> None:
    fixture_text = "1.2.3.4 2.2.2.2 9.9.9.9 93.184.216.34"
    assert checker.find_public_ips(fixture_text) == []
    # NIST SP 800-63B-4 §3.1.1.2 是版本号，不是 IP
    assert checker.find_public_ips("NIST SP 800-63B-4 §3.1.1.2") == []


def test_lockfiles_are_skipped() -> None:
    assert checker.should_skip("requirements-lock.txt") is True
    assert checker.should_skip("web/frontend/package-lock.json") is True
    assert checker.should_skip("docs/figures.md") is False


def test_baseline_is_warning_not_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "leak.md").write_text(f"真实 host {_ip('1', '2', '0', '9')} 不应出现\n", encoding="utf-8")
    (tmp_path / "deploy.sh").write_text(f"host {_ip('3', '3', '3', '3')}\n", encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_files", lambda root: ["docs/leak.md", "deploy.sh"])
    monkeypatch.setattr(checker, "BASELINE_PATHS", {"deploy.sh": "测试基线"})

    errors, warnings = checker.scan(tmp_path)

    assert [item[0] for item in errors] == ["docs/leak.md"]
    assert [item[0] for item in warnings] == ["deploy.sh"]


def test_main_exit_codes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "ok.md").write_text("127.0.0.1\n", encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_files", lambda root: ["ok.md"])
    monkeypatch.setattr(checker, "BASELINE_PATHS", {})
    assert checker.main(["--root", str(tmp_path)]) == 0

    (tmp_path / "bad.md").write_text(f"{_ip('82', '156', '88', '246')}\n", encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_files", lambda root: ["bad.md"])
    assert checker.main(["--root", str(tmp_path)]) == 1


def test_stale_baseline_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "ok.md").write_text("127.0.0.1\n", encoding="utf-8")
    monkeypatch.setattr(checker, "tracked_files", lambda root: ["ok.md"])
    monkeypatch.setattr(checker, "BASELINE_PATHS", {"ok.md": "测试基线"})

    assert checker.main(["--root", str(tmp_path)]) == 0
    assert "基线过期" in capsys.readouterr().out
