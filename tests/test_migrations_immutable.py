# -*- coding: utf-8 -*-
"""已应用迁移不可变守卫（审计 #378：合并态部署事故的事后防线）。

背景：迁移执行器 `tools/migrate.sh` 会记录每个已应用迁移文件的 sha256，并在后续部署时
校验；若已应用迁移文件被修改（哪怕只改注释），部署会在「迁移」步骤中止（fail-closed，
旧版本继续在线）。这类问题在「全新库」的 CI infra job 里**测不出来**——全新库每次都是
按新 hash 应用——只有在已有历史的库（staging/生产）才暴露。本测试把该约束前移到 PR CI。

铁律：`migrations/*.sql` 一旦提交并可能在任一环境应用过，就不得再修改；需要修 schema
时另写前向迁移，并把新文件登记进 `migrations/APPLIED_SHA256.txt`（本测试强制校验）。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "migrations" / "APPLIED_SHA256.txt"
MIGRATIONS_DIR = ROOT / "migrations"

_ROW_RE = re.compile(r"^([0-9a-f]{64})\s{2}(\S+\.sql)$")


def _manifest_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in MANIFEST.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _ROW_RE.match(stripped)
        assert match, f"迁移指纹清单格式非法（应为 '<sha256>  <文件名>.sql'）：{stripped!r}"
        digest, name = match.group(1), match.group(2)
        assert name not in entries, f"迁移指纹清单重复登记：{name}"
        entries[name] = digest
    return entries


def _sha256_normalized(path: Path) -> str:
    """按 LF 规范化后计算 sha256（与迁移执行器在 Linux 上的原始字节计算一致）。

    Windows 工作区若被 core.autocrlf 转成 CRLF，规范化后仍与清单一致；CI（Linux）本来就是 LF。
    """
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def test_manifest_covers_every_migration() -> None:
    entries = _manifest_entries()
    actual = {p.name for p in MIGRATIONS_DIR.glob("*.sql")}
    missing = sorted(actual - set(entries))
    stale = sorted(set(entries) - actual)
    assert not missing, (
        "新增迁移未登记指纹：%s。请在 migrations/APPLIED_SHA256.txt 追加对应 "
        "<sha256>  <文件名> 行（sha256 按 LF 规范化计算）。" % missing
    )
    assert not stale, f"迁移指纹清单存在已不存在的文件：{stale}"


def test_applied_migrations_are_immutable() -> None:
    entries = _manifest_entries()
    changed: list[str] = []
    for name, expected in entries.items():
        path = MIGRATIONS_DIR / name
        if not path.is_file():
            continue  # 由上一个用例报告缺失
        actual = _sha256_normalized(path)
        if actual != expected:
            changed.append(f"{name}（记录={expected} 实际={actual}）")
    assert not changed, (
        "已应用迁移文件内容被修改，禁止（线上 migrate.sh 会 hash 校验失败并中止部署）：\n  "
        + "\n  ".join(changed)
        + "\n处置：恢复该文件原内容；如需修 schema，请新增前向迁移并登记 APPLIED_SHA256.txt。"
    )
