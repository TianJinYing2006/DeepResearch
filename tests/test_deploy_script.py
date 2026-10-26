# -*- coding: utf-8 -*-
"""部署脚本静态回归守卫（P0-5 / 部署可观测性）。"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_deploy_has_step_timing_and_timeouts() -> None:
    text = (ROOT / "tools" / "deploy.sh").read_text(encoding="utf-8")
    assert "step_start()" in text
    assert "step_done()" in text
    assert "run_timeout" in text
    assert "run_timeout 1800" in text  # build
    assert "run_timeout 900" in text   # backup


def test_deploy_reloads_exact_compose_caddy_and_fails_hard() -> None:
    text = (ROOT / "tools" / "deploy.sh").read_text(encoding="utf-8")
    assert "ps -q caddy" in text
    assert "caddy reload --config /etc/caddy/Caddyfile" in text
    assert "WARN Caddy reload" not in text  # 旧行为：失败只 WARN；现在必须失败


def test_deploy_smoke_has_curl_timeouts() -> None:
    text = (ROOT / "tools" / "deploy.sh").read_text(encoding="utf-8")
    assert text.count("--max-time 15") >= 3
