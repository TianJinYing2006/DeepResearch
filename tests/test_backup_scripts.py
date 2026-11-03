# -*- coding: utf-8 -*-
"""P0-4 备份/恢复凭据处理的静态回归守卫（审计 #326 起含明文边界与 DSN 解析）。

策略：连接口令不得进入 pg_dump / pg_restore / psql 的 argv，也不得进入 docker CLI argv；
明文 dump / 临时回验文件不得留在 backups/ 或进入 COS。运行时行为由 CI `infra` 的
「备份 → 清库 → 恢复」真实演练与下方 POSIX 桩测试覆盖。
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

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


# ---------------------------------------------------------------- 审计 #326（备份明文边界）


def test_backup_cleans_plaintext_on_all_paths() -> None:
    """明文 dump 只允许存在于加密前的窗口；失败/中断路径由 trap 兜底删除。"""
    text = (ROOT / "tools" / "backup.sh").read_text(encoding="utf-8")
    assert "umask 077" in text, "备份产物必须仅属主可读（0644 明文窗口期可被同机用户读）"
    assert "trap cleanup EXIT HUP INT TERM" in text, "加密失败/中断必须删除明文 dump"
    assert 'plain=""' in text and 'verify=""' in text, "trap 变量必须先初始化（set -u）"
    assert 'rm -f "$verify" "$plain"' in text, "对称回验后必须删除明文与回验文件"
    # 未加密模式（本地开发）时 plain 是最终产物，trap 不得误删
    assert "final=\"$plain\"" in text and "# 未加密模式" in text


def test_backup_cron_syncs_only_encrypted_and_snapshot() -> None:
    """COS 只接收加密 DB 备份与源码快照；明文 dump / 临时文件永不上传。"""
    text = (ROOT / "tools" / "backup-cron.sh").read_text(encoding="utf-8")
    assert "--include 'deepresearch_*.dump.pem'" in text
    assert "--include 'deepresearch_*.dump.enc'" in text
    assert "--include 'src-pre-*.tar.gz'" in text
    assert "--immutable" in text
    assert "rclone --config \"$RCLONE_CONF\" copy" in text
    # 不再无过滤同步整个 backups 目录
    assert 'copy --immutable "$OUT" "${REMOTE}' not in text


def _write_stub(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


@pytest.mark.skipif(shutil.which("sh") is None, reason="需要 POSIX sh（CI ubuntu 覆盖）")
def test_backup_url_parsing_decodes_and_strips_query(tmp_path: Path) -> None:
    """审计 #326（L9）：DATABASE_URL 的 %40/%3A 解码 + ?sslmode= 剥离（真跑脚本 + 桩）。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "record.env"
    _write_stub(
        bin_dir / "pg_dump",
        "#!/bin/sh\n"
        "out=''\n"
        "while [ \"$#\" -gt 0 ]; do\n"
        "  case \"$1\" in -f) shift; out=\"$1\" ;; esac\n"
        "  shift\n"
        "done\n"
        "[ -n \"$out\" ] && : > \"$out\"\n"
        "{ echo \"PGHOST=$PGHOST\"; echo \"PGPORT=$PGPORT\"; echo \"PGUSER=$PGUSER\";\n"
        "  echo \"PGPASSWORD=$PGPASSWORD\"; echo \"PGDATABASE=${PGDATABASE:-}\"; }"
        " > \"${STUB_RECORD:?}\"\n",
    )
    _write_stub(bin_dir / "pg_restore", "#!/bin/sh\nexit 0\n")

    env = os.environ.copy()
    for key in ("PGHOST", "PGPORT", "PGUSER", "PGPASSWORD", "PGDATABASE",
                "BACKUP_ENCRYPT", "BACKUP_RECIPIENT_CERT"):
        env.pop(key, None)
    env.update({
        "PATH": str(bin_dir) + os.pathsep + env.get("PATH", ""),
        # 口令含 @ 与 :（URL 编码）；库名带 ?sslmode= 查询串（旧实现会整串当库名）
        "DATABASE_URL": (
            "postgresql://dr%40user:p%40ss%3Aword@db.internal:6543/"
            "deepresearch?sslmode=require"
        ),
        "BACKUP_DIR": str(tmp_path / "backups"),
        "BACKUP_KEEP": "0",
        "STUB_RECORD": str(record),
    })
    result = subprocess.run(
        ["sh", str(ROOT / "tools" / "backup.sh")],
        env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, f"backup.sh 失败：{result.stderr}"

    parsed = dict(
        line.split("=", 1) for line in record.read_text(encoding="utf-8").splitlines() if "=" in line
    )
    assert parsed["PGPASSWORD"] == "p@ss:word", "口令中的 %40/%3A 必须 percent-decode"
    assert parsed["PGUSER"] == "dr@user"
    assert parsed["PGDATABASE"] == "deepresearch", "?sslmode= 查询串必须剥离"
    assert parsed["PGHOST"] == "db.internal" and parsed["PGPORT"] == "6543"

    dumps = list((tmp_path / "backups").glob("deepresearch_*.dump"))
    assert len(dumps) == 1
    assert stat.S_IMODE(dumps[0].stat().st_mode) == 0o600, "umask 077 必须让 dump 仅属主可读写"
