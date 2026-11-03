# -*- coding: utf-8 -*-
"""迁移 ↔ 结构断言一致性守卫（需求 42 #329）。

背景：`migrations/` 与 `migrations/checks/` 此前只靠人工纪律同步，CI `infra` job
只遍历「已存在的」`checks/*.sql` —— 缺一个 check 文件时循环少一次、静默通过。
0018–0021 四批结构 / 约束 / 级联语义就是这样长期没有断言（审计发现）。

本测试把「每个迁移必须有对应结构断言」变成 pytest 门禁（lint-and-test job 即生效，
不新增 CI 步骤 / 不新增 tools/check_*.py —— 那会扩大 CONTRIBUTING §6、figures 的
守卫清单同步面）：

1. `migrations/NNNN_*.sql` 的编号集合 ⊆ `migrations/checks/NNNN_schema_assert.sql`；
2. 每个 check 都有对应迁移（防孤儿断言 / 编号写错）；
3. 每个 check 必须包含 `RAISE EXCEPTION`（失败必须响亮）与 `ROLLBACK`
   （断言写入不得残留），且以 `BEGIN;` 开启；
4. 迁移与 check 的 NNNN 编号不得重复（同号两个文件会让集合比较静默漏算）。

结构断言的真实 SQL 行为由 CI `infra` job 在真实 PostgreSQL 上执行
（`.github/workflows/ci.yml`「迁移结构断言（checks/）」），本机无 PG 时只做静态校验。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MIGRATIONS_DIR = ROOT / "migrations"
CHECKS_DIR = ROOT / "migrations" / "checks"

MIGRATION_NAME_RE = re.compile(r"^(?P<number>\d{4})_[a-z0-9_]+\.sql$")
CHECK_NAME_RE = re.compile(r"^(?P<number>\d{4})_schema_assert\.sql$")


def _index_sql(directory: Path, pattern: re.Pattern[str], *, kind: str) -> dict[str, str]:
    """返回 {4 位编号: 文件名}。

    不符合命名约定或编号重复都直接失败：否则文件会从集合比较中「静默消失」，
    守卫变成永远绿的摆设（与 tools/check_doc_consistency.py 的防空转口径一致）。
    """
    found: dict[str, str] = {}
    unexpected: list[str] = []
    duplicated: list[str] = []
    for path in sorted(directory.glob("*.sql")):
        match = pattern.match(path.name)
        if match is None:
            unexpected.append(path.name)
            continue
        number = match.group("number")
        if number in found:
            duplicated.append(f"{number}: {found[number]} / {path.name}")
            continue
        found[number] = path.name
    assert not unexpected, (
        f"{directory.relative_to(ROOT)} 存在不符合命名约定的 SQL 文件：{unexpected}；"
        f"约定为迁移 NNNN_slug.sql、断言 NNNN_schema_assert.sql（否则本守卫会漏算）"
    )
    assert not duplicated, f"{kind}编号重复（同号多文件会让 parity 比较静默漏算）：{duplicated}"
    return found


def _migrations() -> dict[str, str]:
    return _index_sql(MIGRATIONS_DIR, MIGRATION_NAME_RE, kind="迁移")


def _checks() -> dict[str, str]:
    return _index_sql(CHECKS_DIR, CHECK_NAME_RE, kind="结构断言")


def test_every_migration_has_schema_assert() -> None:
    """0018–0021 的教训：缺 check 时 CI 循环少跑一次，结构回归完全无断言。"""
    migrations = _migrations()
    checks = _checks()
    missing = sorted(set(migrations) - set(checks))
    assert not missing, (
        "以下迁移缺少结构断言，请在 migrations/checks/ 补 NNNN_schema_assert.sql："
        + "、".join(f"{number}（{migrations[number]}）" for number in missing)
        + "。CONTRIBUTING §9.4：新结构必须同步 migrations/checks/*.sql"
    )


def test_every_schema_assert_has_migration() -> None:
    """孤儿断言会让「覆盖了几个迁移」的盘点失真，也是编号写错的信号。"""
    migrations = _migrations()
    checks = _checks()
    orphan = sorted(set(checks) - set(migrations))
    assert not orphan, (
        "以下结构断言没有对应迁移（编号写错或迁移已删除）："
        + "、".join(f"{number}（{checks[number]}）" for number in orphan)
    )


def test_schema_asserts_fail_loudly_and_do_not_persist() -> None:
    """防空跑：没有 RAISE EXCEPTION 的 check 即使结构缺失也不会红；没有 ROLLBACK 会落脏数据。"""
    problems: list[str] = []
    for number, name in sorted(_checks().items()):
        text = (CHECKS_DIR / name).read_text(encoding="utf-8")
        upper = text.upper()
        if "BEGIN;" not in upper:
            problems.append(f"{name}: 缺少 BEGIN;（断言写入必须可整体回滚）")
        if "ROLLBACK" not in upper:
            problems.append(f"{name}: 缺少 ROLLBACK（断言数据会残留）")
        if "RAISE EXCEPTION" not in upper:
            problems.append(
                f"{name}: 缺少 RAISE EXCEPTION（断言失败不会让 job 红，等于空跑）"
            )
    assert not problems, "结构断言不满足「失败响亮 + 不落数据」契约：\n" + "\n".join(problems)
