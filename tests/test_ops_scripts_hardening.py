# -*- coding: utf-8 -*-
"""运维脚本健壮性回归（审计 #339）：backup-verify / restore / migrate。

- backup-verify：加密产物无口令/私钥时必须 exit 5「无法校验」，不得假报 ok；
- restore：默认 glob 覆盖生产加密备份，选中时给出明确解密指引（不静默）；
- migrate：DSN 不进 argv、迁移内容与 schema_migrations 记录同事务、sha256 重跑校验。

运行时行为由本文件的无 DB POSIX 用例 + CI `infra` job（迁移幂等/结构断言、备份→恢复
演练）共同覆盖；本机 Windows 无 POSIX sh 时行为用例跳过。
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- backup-verify


def test_backup_verify_encrypted_without_key_fails_loudly() -> None:
    text = (ROOT / "tools" / "backup-verify.sh").read_text(encoding="utf-8")
    assert "verify skipped: encrypted backup requires key" in text
    assert "exit 5" in text, "无口令/私钥时必须非 0 退出（旧实现 exit 0 假绿）"
    assert "verify structure-only ok" not in text, "旧的假绿输出必须删除"
    # 新鲜度过期=4 的既有档位保留（CI 脚本断言依赖它）
    assert "exit 4" in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="需要 POSIX sh（CI ubuntu 覆盖）")
def test_backup_verify_encrypted_without_key_returns_5(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    target = backups / "deepresearch_20260101_000000.dump.pem"
    target.write_bytes(b"not-a-real-ciphertext")

    env = os.environ.copy()
    for key in ("BACKUP_PASSPHRASE", "BACKUP_RECIPIENT_KEY", "BACKUP_TARGET"):
        env.pop(key, None)
    env["BACKUP_DIR"] = str(backups)
    result = subprocess.run(
        ["sh", str(ROOT / "tools" / "backup-verify.sh")],
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 5, f"stdout={result.stdout}\nstderr={result.stderr}"
    assert "verify skipped: encrypted backup requires key" in result.stderr


# ---------------------------------------------------------------- restore


def test_restore_default_glob_covers_encrypted_backups() -> None:
    text = (ROOT / "tools" / "restore.sh").read_text(encoding="utf-8")
    assert 'deepresearch_*.dump.pem' in text
    assert 'deepresearch_*.dump.enc' in text
    assert "需先解密" in text and "openssl smime -decrypt" in text
    assert "openssl enc -d -aes-256-cbc" in text


@pytest.mark.skipif(shutil.which("sh") is None, reason="需要 POSIX sh（CI ubuntu 覆盖）")
def test_restore_encrypted_backup_exits_with_guidance(tmp_path: Path) -> None:
    backups = tmp_path / "backups"
    backups.mkdir()
    (backups / "deepresearch_20260101_000000.dump.pem").write_bytes(b"ciphertext")

    env = os.environ.copy()
    env.update({
        "BACKUP_DIR": str(backups),
        "PGHOST": "db.invalid", "PGPORT": "5432", "PGUSER": "u",
        "PGPASSWORD": "p", "PGDATABASE": "db",
    })
    for key in ("DATABASE_URL",):
        env.pop(key, None)
    result = subprocess.run(
        ["sh", str(ROOT / "tools" / "restore.sh")],
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=60,
    )
    assert result.returncode == 2, f"stdout={result.stdout}\nstderr={result.stderr}"
    # 中文提示随 locale 编码不同，行为断言只认 ASCII 解密指引（静态用例另断言中文文案）
    assert "openssl smime -decrypt" in result.stderr
    assert "-inkey" in result.stderr


# ---------------------------------------------------------------- migrate


def test_migrate_dsn_not_in_argv_and_records_same_transaction() -> None:
    text = (ROOT / "tools" / "migrate.sh").read_text(encoding="utf-8")
    assert 'psql "$DATABASE_URL"' not in text, "含口令 DSN 不得进 psql argv（ps 可见）"
    assert "export PGPASSWORD" in text
    # 迁移内容 + 记录写入同一事务（stdin 喂给 --single-transaction）
    assert "--single-transaction" in text
    assert "-f -" in text
    assert "INSERT INTO schema_migrations (version, sha256)" in text
    # 重跑 hash 校验与失败口径
    assert "sha256" in text and "内容与记录不一致" in text
    assert "hash_file" in text
    # psql -c 不做变量插值（:'var' 会原样发给服务端）：SQL 字面量必须由脚本做字符集
    # 白名单校验后拼接（CI 实测踩过，防回归）
    assert ":'version'" not in text
    assert "WHERE version = '$version'" in text


def test_migration_0031_and_check_exist() -> None:
    migration = ROOT / "migrations" / "0031_schema_migrations_hash.sql"
    check = ROOT / "migrations" / "checks" / "0031_schema_assert.sql"
    assert migration.is_file()
    assert check.is_file()
    migration_text = migration.read_text(encoding="utf-8")
    assert "ADD COLUMN IF NOT EXISTS sha256" in migration_text
    check_text = check.read_text(encoding="utf-8")
    assert "RAISE EXCEPTION" in check_text and "ROLLBACK" in check_text


def test_ci_keeps_backup_verify_stale_exit_code_assertion() -> None:
    text = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert 'test "$rc" = "4"' in text, "CI 的「过期=4」断言不得被本 PR 改掉"
