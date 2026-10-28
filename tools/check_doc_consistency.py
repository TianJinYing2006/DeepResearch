#!/usr/bin/env python3
"""文档漂移守卫（2026-10-27 建立）。

为什么存在：本项目把「文档状态漂移」列为最现实的管理风险，但此前只有人工纪律。
2026-10-27 的 A 类收口实测同时发现：figures 仍写「仅 v0.1.0 / dev 领先 30」（实际 v0.2.0、
dev==master）、CONTRIBUTING §2.3/§8.4 与需求 10 仍写「邀请制 / 不开放注册」（实际已开放注册）、
README 仍写已删除的 `?invite=CODE`、CI 实际跑 5 个守卫而 CONTRIBUTING 只列 4 个、figures 记 4 个。
这些都不是「有人写错」，而是「改了 A 没同步 B」——只能靠机器持续比对。

本守卫只做**确定性**校验（零 LLM、零网络；除 git 元数据外只读仓库文件）：

1. figures ↔ git：`版本 tag` 行必须与 `git tag` 集合一致；
2. 备案号一致性：`BeianLink.tsx` 的 ICP 号必须同时出现在 figures / production-readiness / domain-cutover；
3. 守卫清单一致性：CI 实际调用的 `tools/check_*.py` == CONTRIBUTING §6 表 == figures「CI 守卫」计数；
4. 注册口径一致性：代码存在开放注册开关 ⇒ `.env.example` 与 compose 透传对应变量，
   规则层文档（CONTRIBUTING / ADR-0017）写「开放注册」且无矛盾表述；README 不得再宣传 `?invite=`；
5. ADR 索引完整性：`docs/decisions/0*.md` 与索引链接双向一致、无重复；
6. README 对外数字 == figures（pytest 收集数 / E2E 用例与 spec 数）。

设计纪律（防「永远绿的摆设」，与 `tests/test_ui_pattern_guard.py` 同口径）：
- 必需文件/表格行缺失 = **失败**，不跳过；
- shallow 克隆 = **失败**并提示 `fetch-depth: 0`，不静默降级（否则 tag 集合恒为当前 ref，等于空转）；
- 每个检查都打印 OK/FAIL，失败项给出文件与期望值；退出码 1。

需要完整 tag 历史 ⇒ CI 的 `lint-and-test` job 使用 `actions/checkout@v7` + `fetch-depth: 0`。

用法：
    python tools/check_doc_consistency.py [--root <repo>]
"""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# 行内代码里的 `|` 不是表格分隔符（与 tools/check_md_tables.py 同口径）
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_INLINE_CODE = re.compile(r"`[^`\n]*`")

ICP_RE = re.compile(r"([\u4e00-\u9fa5]{1,2})\s*ICP\s*备\s*(\d+)\s*号")
SEMVER_TAG_RE = re.compile(r"\bv\d+\.\d+\.\d+\b")
CI_GUARD_SCRIPT_RE = re.compile(r"tools/(check_[a-z0-9_]+\.py)")
BACKTICKED_GUARD_RE = re.compile(r"`(check_[a-z0-9_]+\.py)`")
ADR_LINK_RE = re.compile(r"\]\((0\d{3}-[^)\s]+\.md)\)")

# 与当前开放注册口径矛盾的表述：出现即视为漂移（只在「当前状态」文档里查）
FORBIDDEN_OPEN_REGISTRATION_PHRASES = (
    "不开放注册",
    "不开放自由注册",
    "公网开放注册",
    "公开注册（绕过邀请）",
)


class GuardError(RuntimeError):
    """守卫自身的确定性失败（缺文件 / 格式不认识 / git 不可用）。"""


def read_text(path: Path) -> str:
    if not path.is_file():
        raise GuardError(f"缺少必需文件：{_rel(path)}")
    return path.read_text(encoding="utf-8")


def _rel(path: Path) -> str:
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:  # pragma: no cover - 仅 tmp 仓库测试会走不到
        return str(path)


def _list_adr_names(root: Path) -> list[str]:
    """ADR 文件清单（单测可替换为内存清单，避免依赖真实文件系统）。"""
    decisions = root / "docs" / "decisions"
    return sorted(p.name for p in decisions.glob("0[0-9][0-9][0-9]-*.md") if p.is_file())


def _file_exists(root: Path, rel: str) -> bool:
    return (root / rel).is_file()


def _workflow_files(root: Path) -> list[Path]:
    """CI workflow 清单（单测可替换为内存清单）。"""
    workflow_dir = root / ".github" / "workflows"
    if not workflow_dir.is_dir():
        raise GuardError("缺少 .github/workflows/ 目录")
    return sorted(workflow_dir.glob("*.yml"))


def table_rows(md_text: str) -> list[list[str]]:
    """返回所有表格行的单元格列表（首列 = 标签列）。"""
    rows: list[list[str]] = []
    for line in md_text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = _INLINE_CODE.sub("CODE", stripped)
        if cells.startswith("|"):
            cells = cells[1:]
        if cells.endswith("|"):
            cells = cells[:-1]
        rows.append([c.strip() for c in _CELL_SPLIT.split(cells)])
    return rows


def find_row(rows: list[list[str]], label: str, *, source: str) -> list[str]:
    """取唯一的「| label | ... |」表格行；缺失或重复都视为漂移。"""
    hits = [cells for cells in rows if cells and cells[0] == label]
    if not hits:
        raise GuardError(f"{source} 缺少必需表格行「{label}」")
    if len(hits) > 1:
        raise GuardError(f"{source} 表格行「{label}」出现 {len(hits)} 次（应唯一）")
    return hits[0]


# ---------------------------------------------------------------------------
# git 元数据
# ---------------------------------------------------------------------------
def _git(root: Path, *args: str) -> str:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args],
            capture_output=True,
            text=True,
            check=True,
        )
    except FileNotFoundError as exc:  # pragma: no cover - CI/本机都有 git
        raise GuardError("未找到 git 可执行文件，无法校验 tag 集合") from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip().splitlines()[:1]
        raise GuardError(f"git {' '.join(args)} 失败：{detail}") from exc
    return proc.stdout


def assert_full_clone(root: Path) -> None:
    """shallow 克隆下 tag 集合不可信 ⇒ 显式失败，绝不静默降级。"""
    shallow = _git(root, "rev-parse", "--is-shallow-repository").strip()
    if shallow != "false":
        raise GuardError(
            "当前是 shallow 克隆：tag/历史不可信，守卫会空转。"
            "CI 的 lint-and-test job 必须为 actions/checkout 设置 fetch-depth: 0"
        )


def git_tags(root: Path) -> set[str]:
    return {tag for tag in _git(root, "tag", "--list").split() if tag}


# ---------------------------------------------------------------------------
# 检查 1：figures ↔ git 版本 tag
# ---------------------------------------------------------------------------
def check_figures_tags(root: Path) -> list[str]:
    assert_full_clone(root)
    rows = table_rows(read_text(root / "docs/figures.md"))
    cells = find_row(rows, "版本 tag", source="docs/figures.md")
    if len(cells) < 2:
        raise GuardError("docs/figures.md「版本 tag」行缺少取值列")
    documented = set(SEMVER_TAG_RE.findall(cells[1]))
    actual = git_tags(root)
    if documented != actual:
        return [
            f"figures「版本 tag」与 git tag 不一致：figures={sorted(documented)} "
            f"git={sorted(actual)}（发版后请更新 figures §1）"
        ]
    return []


# ---------------------------------------------------------------------------
# 检查 2：ICP 备案号一致性
# ---------------------------------------------------------------------------
ICP_SOURCE = "web/frontend/src/components/BeianLink.tsx"
ICP_REQUIRED_FILES = (
    "docs/figures.md",
    "docs/operations/production-readiness.md",
    "docs/operations/domain-cutover.md",
)


def check_icp_consistency(root: Path) -> list[str]:
    link = read_text(root / ICP_SOURCE)
    match = ICP_RE.search(link)
    if match is None:
        return [f"{ICP_SOURCE} 未找到 ICP 备案号（首页合规展示缺失）"]
    icp = f"{match.group(1)}ICP备{match.group(2)}号"
    errors: list[str] = []
    if "https://beian.miit.gov.cn/" not in link:
        errors.append(f"{ICP_SOURCE} 缺少指向 https://beian.miit.gov.cn/ 的超链接（展示不规范）")
    for rel in ICP_REQUIRED_FILES:
        text = read_text(root / rel).replace(" ", "")
        if icp not in text:
            errors.append(f"{rel} 缺少备案号 {icp}（与 {ICP_SOURCE} 不一致）")
    return errors


# ---------------------------------------------------------------------------
# 检查 3：CI 守卫清单一致性
# ---------------------------------------------------------------------------
def ci_guard_scripts(root: Path) -> set[str]:
    scripts: set[str] = set()
    for path in _workflow_files(root):
        for lineno, line in enumerate(read_text(path).splitlines(), 1):
            if line.lstrip().startswith("#"):
                continue
            scripts.update(CI_GUARD_SCRIPT_RE.findall(line))
    if not scripts:
        raise GuardError("CI workflow 未找到任何 tools/check_*.py 调用（守卫清单无法校验）")
    return scripts


def check_ci_guard_inventory(root: Path) -> list[str]:
    scripts = ci_guard_scripts(root)
    contributing = read_text(root / "CONTRIBUTING.md")
    # 注意：不能用 table_rows()——它会把行内代码替换成 CODE，反引号里的脚本名就丢了。
    guard_lines = [
        line for line in contributing.splitlines() if line.strip().startswith("| 守卫 |")
    ]
    if len(guard_lines) != 1:
        raise GuardError("CONTRIBUTING §6 缺少唯一的「| 守卫 |」表行")
    listed = set(BACKTICKED_GUARD_RE.findall(guard_lines[0]))
    figures = table_rows(read_text(root / "docs/figures.md"))
    count_cells = find_row(figures, "CI 守卫", source="docs/figures.md")
    count_match = re.search(r"(\d+)\s*个", count_cells[1] if len(count_cells) > 1 else "")
    if count_match is None:
        raise GuardError("docs/figures.md「CI 守卫」行缺少「N 个」计数")
    documented_count = int(count_match.group(1))

    errors: list[str] = []
    if listed != scripts:
        errors.append(
            "CONTRIBUTING §6「守卫」清单与 CI 实际调用不一致："
            f"CONTRIBUTING={sorted(listed)} CI={sorted(scripts)}"
        )
    if documented_count != len(scripts):
        errors.append(
            f"figures「CI 守卫」计数为 {documented_count}，CI 实际调用 {len(scripts)} 个：{sorted(scripts)}"
        )
    for name in sorted(scripts):
        if not _file_exists(root, f"tools/{name}"):
            errors.append(f"CI 引用的守卫 tools/{name} 不存在")
    return errors


# ---------------------------------------------------------------------------
# 检查 4：开放注册口径一致性
# ---------------------------------------------------------------------------
OPEN_REGISTRATION_ENV_RE = re.compile(
    r"DR_(REGISTRATION_ENABLED|REGISTRATION_DAILY_LIMIT|EMAIL_DOMAIN_BLOCKLIST)"
)
RULE_DOCS = ("CONTRIBUTING.md", "docs/decisions/0017-boundary-and-productization.md")


def check_registration_consistency(root: Path) -> list[str]:
    backend = read_text(root / "web/backend/main.py")
    env_names = sorted({"DR_" + name for name in OPEN_REGISTRATION_ENV_RE.findall(backend)})
    if not env_names:
        raise GuardError(
            "代码中未找到开放注册开关（DR_REGISTRATION_ENABLED 等）——"
            "若注册能力已删除，请同步更新本守卫与文档口径，不得静默跳过"
        )

    errors: list[str] = []
    env_example = read_text(root / ".env.example")
    compose = read_text(root / "docker-compose.staging.yml")
    for name in env_names:
        if name not in env_example:
            errors.append(f".env.example 未文档化 {name}（代码已读取）")
        if name not in compose:
            errors.append(f"docker-compose.staging.yml 未透传 {name}（代码已读取）")

    for rel in RULE_DOCS:
        text = read_text(root / rel)
        if "开放注册" not in text:
            errors.append(f"{rel} 未写明「开放注册」（代码已开放注册）")
        for phrase in FORBIDDEN_OPEN_REGISTRATION_PHRASES:
            if phrase in text:
                errors.append(f"{rel} 仍含与开放注册矛盾的表述：「{phrase}」")

    figures = table_rows(read_text(root / "docs/figures.md"))
    scale_cells = find_row(figures, "内测规模", source="docs/figures.md")
    if "开放注册" not in " ".join(scale_cells[1:]):
        errors.append("figures「内测规模」行未写明「开放注册」")

    readme = read_text(root / "README.md")
    if "?invite=" in readme:
        errors.append("README 仍宣传已删除的邀请链接参数 `?invite=`")
    return errors


# ---------------------------------------------------------------------------
# 检查 5：ADR 索引完整性
# ---------------------------------------------------------------------------
def check_adr_index(root: Path) -> list[str]:
    files = set(_list_adr_names(root))
    if not files:
        raise GuardError("docs/decisions/ 未找到任何 ADR 文件")
    index = read_text(root / "docs" / "decisions" / "README.md")
    links = ADR_LINK_RE.findall(index)
    linked = set(links)

    errors: list[str] = []
    missing = sorted(files - linked)
    dead = sorted(linked - files)
    duplicated = sorted({name for name in links if links.count(name) > 1})
    if missing:
        errors.append(f"ADR 索引缺少：{missing}")
    if dead:
        errors.append(f"ADR 索引指向不存在的文件：{dead}")
    if duplicated:
        errors.append(f"ADR 索引重复列出：{duplicated}")
    return errors


# ---------------------------------------------------------------------------
# 检查 6：README 对外数字 == figures
# ---------------------------------------------------------------------------
def check_readme_numbers(root: Path) -> list[str]:
    figures = table_rows(read_text(root / "docs/figures.md"))

    pytest_cells = find_row(figures, "pytest 收集/执行", source="docs/figures.md")
    pytest_match = re.search(r"\*\*(\d+)\*\*", pytest_cells[1] if len(pytest_cells) > 1 else "")
    if pytest_match is None:
        raise GuardError("figures「pytest 收集/执行」行缺少加粗数字")
    figure_pytest = int(pytest_match.group(1))

    e2e_cells = find_row(figures, "浏览器 E2E", source="docs/figures.md")
    e2e_match = re.search(
        r"\*\*(\d+)\s*tests\s*/\s*(\d+)\s*spec\*\*", e2e_cells[1] if len(e2e_cells) > 1 else ""
    )
    if e2e_match is None:
        raise GuardError("figures「浏览器 E2E」行缺少「N tests / M spec」加粗数字")
    figure_e2e = (int(e2e_match.group(1)), int(e2e_match.group(2)))

    readme = read_text(root / "README.md")
    unit_cells = find_row(table_rows(readme), "单元测试", source="README.md")
    unit_match = re.search(r"\*\*(\d+)\s*项", unit_cells[1] if len(unit_cells) > 1 else "")
    if unit_match is None:
        raise GuardError("README「单元测试」行缺少「**N 项」数字")

    errors: list[str] = []
    readme_pytest = int(unit_match.group(1))
    if readme_pytest != figure_pytest:
        errors.append(f"README 单元测试 {readme_pytest} 项 != figures pytest {figure_pytest} 项")

    documented_pairs = {
        (int(a), int(b))
        for a, b in re.findall(r"(\d+)\s*个用例\s*/\s*(\d+)\s*个 spec", readme)
    }
    if not documented_pairs:
        errors.append("README 未找到「N 个用例 / M 个 spec」形式的 E2E 数字（格式变了请同步本守卫）")
    elif documented_pairs != {figure_e2e}:
        errors.append(
            f"README E2E 数字 {sorted(documented_pairs)} != figures {figure_e2e}"
        )
    return errors


CHECKS = (
    ("figures <-> git 版本 tag", check_figures_tags),
    ("ICP 备案号一致性", check_icp_consistency),
    ("CI 守卫清单一致性", check_ci_guard_inventory),
    ("开放注册口径一致性", check_registration_consistency),
    ("ADR 索引完整性", check_adr_index),
    ("README 对外数字 == figures", check_readme_numbers),
)


def run(root: Path = REPO_ROOT) -> list[str]:
    """执行全部检查，返回错误列表（空 = 通过）。"""
    all_errors: list[str] = []
    for label, check in CHECKS:
        try:
            errors = check(root)
        except GuardError as exc:
            errors = [f"{exc}"]
        if errors:
            print(f"[FAIL] {label}")
            for error in errors:
                print(f"   - {error}")
            all_errors.extend(errors)
        else:
            print(f"[OK] {label}")
    print("---")
    if all_errors:
        print(f"文档漂移 {len(all_errors)} 处：修复后重跑本守卫")
    else:
        print("文档一致性检查全部通过")
    return all_errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="文档漂移守卫（figures/ADR/注册口径/README 数字）")
    parser.add_argument("--root", type=Path, default=REPO_ROOT, help="仓库根目录（默认本文件上一级）")
    args = parser.parse_args(argv)
    return 1 if run(args.root) else 0


if __name__ == "__main__":
    raise SystemExit(main())
