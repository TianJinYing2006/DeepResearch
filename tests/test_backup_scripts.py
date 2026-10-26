# -*- coding: utf-8 -*-
"""P0-4 备份/恢复凭据处理的静态回归守卫。

策略：连接口令不得进入 pg_dump / pg_restore / psql 的 argv，也不得进入 docker CLI argv。
运行时行为由 CI `infra` 的「备份 → 清库 → 恢复」真实演练覆盖。
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_backup_uses_pg_env_not_dsn_argv() -> None:
    text = (ROOT / "tools" / "backup.sh").read_text(encoding="utf-8")
    assert 'pg_dump -Fc "$DATABASE_URL"' not in text
    assert "pg_dump -Fc -f" in text
    assert "BACKUP_REQUIRE_ENCRYPTION" in text
    assert "export PGPASSWORD" in text


def test_restore_uses_pg_env_not_dsn_argv() -> None:
    text = (ROOT / "tools" / "restore.sh").read_text(encoding="utf-8")
    assert 'psql "$DATABASE_URL"' not in text
    assert 'pg_restore --no-owner --no-privileges -d "$DATABASE_URL"' not in text
    assert "export PGPASSWORD" in text


def test_backup_cron_uses_env_file_not_docker_argv() -> None:
    text = (ROOT / "tools" / "backup-cron.sh").read_text(encoding="utf-8")
    assert "--env-file" in text
    assert 'DATABASE_URL=postgresql://' not in text
    assert "BACKUP_REQUIRE_ENCRYPTION=1" in text
