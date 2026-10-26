# -*- coding: utf-8 -*-
"""P0-1 反代信任的配置回归守卫（compose 默认 CIDR / Caddy 覆盖 XFF / 部署 reload）。

这些是「部署才能验证」的约束，用文本断言把关键行钉住；运行时语义仍由
`tests/test_distributed_ratelimit.py` 的 `_client_key` 单测覆盖。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_compose_trusts_docker_bridge_by_default() -> None:
    text = (ROOT / "docker-compose.staging.yml").read_text(encoding="utf-8")
    assert 'DR_TRUSTED_PROXY_CIDRS: "${DR_TRUSTED_PROXY_CIDRS:-172.16.0.0/12}"' in text


def test_caddy_overwrites_forwarded_headers() -> None:
    text = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert text.count("header_up X-Forwarded-For {http.request.remote.host}") == 2
    assert text.count("header_up X-Forwarded-Proto {scheme}") == 2


def test_deploy_reloads_caddy_after_rolling_api() -> None:
    text = (ROOT / "tools" / "deploy.sh").read_text(encoding="utf-8")
    assert "caddy reload --config /etc/caddy/Caddyfile" in text
    assert text.rindex("caddy reload") > text.index("up -d --wait api")


def test_caddy_blocks_public_metrics() -> None:
    text = (ROOT / "Caddyfile").read_text(encoding="utf-8")
    assert "path /metrics /metrics/*" in text
    assert 'respond "metrics endpoint is internal-only" 403' in text
