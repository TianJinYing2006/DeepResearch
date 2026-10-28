# -*- coding: utf-8 -*-
"""文档漂移守卫（`tools/check_doc_consistency.py`）的单测。

设计意图与 `test_ui_pattern_guard.py` 相同：守卫的价值全在「它会不会漏」。
一个在必需文件缺失、格式变化、shallow 克隆时仍 exit 0 的守卫，在 CI 里永远绿、永远没用。
因此本文件的重点是**负向用例**：逐一破坏每个检查依赖的事实，断言守卫必须报错。

**为什么用内存仓库而不是 `tmp_path`**：本项目的本机环境（DSH 沙箱）禁止在临时目录
创建/遍历文件，`tmp_path` 会 EACCES（2026-10-27 实测）。守卫的读取面很小，因此把
`read_text` / `_list_adr_names` / `_file_exists` 三个 IO 入口替换成内存字典即可，
既零磁盘依赖、又在 CI/Linux 与 Windows 本机行为一致。

只有「真实仓库应通过」一例会读真实文件与 git，是本守卫的最终判据。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "check_doc_consistency.py"
FAKE_ROOT = Path("/fake/repo")
ICP = "皖ICP备2026036120号"


def _load_tool():
    """tools/ 不是包，按文件路径加载（与 test_ui_pattern_guard.py 同法）。"""
    spec = importlib.util.spec_from_file_location("check_doc_consistency", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 防御性
        raise RuntimeError(f"无法加载 {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _load_tool()


DEFAULTS: dict[str, str] = {
    "CONTRIBUTING.md": (
        "# CONTRIBUTING\n\n"
        "当前阶段：开放注册内测。\n\n"
        "| 项 | 内容 | 归属 |\n"
        "| --- | --- | --- |\n"
        "| 守卫 | `check_public_ip.py` | CI |\n"
    ),
    "README.md": (
        "# README\n\n"
        "| 指标 | 数值 | 口径 |\n"
        "| --- | --- | --- |\n"
        "| 单元测试 | **100 项（JUnit 执行统计，2026-10-27）** | 详见 figures |\n\n"
        "当前 **10 个用例 / 2 个 spec**（desktop + mobile）\n"
    ),
    "docs/figures.md": (
        "# figures\n\n"
        "| 指标 | 当前值 | 口径 | 来源 | 日期 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 版本 tag | v1.0.0 | 测试用 | `git tag` | 2026-10-27 |\n"
        "| CI 守卫 | 1 个 | 公开仓 IP | `tools/check_*.py` | 2026-10-27 |\n"
        "| pytest 收集/执行 | **100** 项 | 测试用 | `pytest --collect-only` | 2026-10-27 |\n"
        "| 浏览器 E2E | **10 tests / 2 spec** | 测试用 | `playwright --list` | 2026-10-27 |\n"
        "| 内测规模 | **开放注册（受配额/预算约束）** | 测试用 | 需求文档 | 2026-10-27 |\n"
        "| 对外入口 | `https://example.test` | 测试用 | 线上实测 | 2026-10-27 |\n"
        f"\n备案号：{ICP}\n"
    ),
    "docs/decisions/README.md": (
        "# ADR\n\n"
        "| 编号 | 标题 | 状态 | 日期 |\n"
        "| --- | --- | --- | --- |\n"
        "| 0001 | [测试](0001-test.md) | 已采纳 | 2026-01-01 |\n"
        "| 0017 | [边界](0017-boundary-and-productization.md) | 已采纳 | 2026-01-01 |\n"
    ),
    "docs/decisions/0001-test.md": "# 0001\n",
    "docs/decisions/0017-boundary-and-productization.md": (
        "# 0017\n\n当前阶段允许邮箱开放注册。\n"
    ),
    "docs/operations/production-readiness.md": f"# readiness\n\nICP：{ICP}\n",
    "docs/operations/domain-cutover.md": f"# cutover\n\nICP：{ICP}\n",
    "web/frontend/src/components/BeianLink.tsx": (
        "const ICP_BEIAN = '" + ICP + "'\n"
        "const ICP_QUERY_URL = 'https://beian.miit.gov.cn/'\n"
    ),
    "web/backend/main.py": (
        'REGISTRATION_ENABLED = _env_flag("DR_REGISTRATION_ENABLED", "true")\n'
        'REGISTRATION_DAILY_LIMIT = int(_env_number("DR_REGISTRATION_DAILY_LIMIT", 100))\n'
        '_email_blocklist_raw = (os.getenv("DR_EMAIL_DOMAIN_BLOCKLIST") or "").strip()\n'
    ),
    ".env.example": (
        "# DR_REGISTRATION_ENABLED=true\n"
        "# DR_REGISTRATION_DAILY_LIMIT=100\n"
        "# DR_EMAIL_DOMAIN_BLOCKLIST=mailinator.com\n"
    ),
    "docker-compose.staging.yml": (
        "DR_REGISTRATION_ENABLED: \"${DR_REGISTRATION_ENABLED:-true}\"\n"
        "DR_REGISTRATION_DAILY_LIMIT: \"${DR_REGISTRATION_DAILY_LIMIT:-100}\"\n"
        "DR_EMAIL_DOMAIN_BLOCKLIST: \"${DR_EMAIL_DOMAIN_BLOCKLIST:-}\"\n"
    ),
    ".github/workflows/ci.yml": (
        "jobs:\n"
        "  lint:\n"
        "    steps:\n"
        "      - name: 公开仓脱敏守卫\n"
        "        run: python tools/check_public_ip.py --strict\n"
    ),
    "tools/check_public_ip.py": "# fake guard\n",
}


@pytest.fixture()
def repo(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    """内存仓库 + 隔离 git：默认 tag 与 figures 一致、非 shallow。"""
    files = dict(DEFAULTS)

    def _read(path: Path) -> str:
        try:
            rel = path.relative_to(FAKE_ROOT).as_posix()
        except ValueError:  # pragma: no cover - 防御性
            rel = str(path)
        if rel not in files:
            raise TOOL.GuardError(f"缺少必需文件：{rel}")
        return files[rel]

    def _list_adr(_root: Path) -> list[str]:
        return sorted(
            Path(rel).name
            for rel in files
            if rel.startswith("docs/decisions/0") and rel.endswith(".md")
        )

    def _exists(_root: Path, rel: str) -> bool:
        return rel in files

    def _workflows(_root: Path) -> list[Path]:
        return [FAKE_ROOT / ".github" / "workflows" / "ci.yml"]

    monkeypatch.setattr(TOOL, "read_text", _read)
    monkeypatch.setattr(TOOL, "_list_adr_names", _list_adr)
    monkeypatch.setattr(TOOL, "_file_exists", _exists)
    monkeypatch.setattr(TOOL, "_workflow_files", _workflows)
    monkeypatch.setattr(TOOL, "assert_full_clone", lambda _root: None)
    monkeypatch.setattr(TOOL, "git_tags", lambda _root: {"v1.0.0"})
    return files


def _errors() -> list[str]:
    return TOOL.run(FAKE_ROOT)


def test_clean_fake_repo_passes(repo: dict[str, str]) -> None:
    assert _errors() == []


def test_main_exit_code_zero_for_clean_repo(repo: dict[str, str]) -> None:
    assert TOOL.main(["--root", str(FAKE_ROOT)]) == 0


def test_figures_tag_drift_detected(repo: dict[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TOOL, "git_tags", lambda _root: {"v1.0.0", "v2.0.0"})
    assert any("版本 tag" in error for error in _errors())


def test_missing_required_figures_row_fails_loud(repo: dict[str, str]) -> None:
    figures = repo["docs/figures.md"]
    repo["docs/figures.md"] = "\n".join(
        line for line in figures.splitlines() if not line.startswith("| CI 守卫 ")
    )
    errors = _errors()
    assert any("CI 守卫" in error and "缺少" in error for error in errors)


def test_icp_mismatch_detected(repo: dict[str, str]) -> None:
    repo["docs/figures.md"] = repo["docs/figures.md"].replace(ICP, "京ICP备1号")
    assert any("备案号" in error for error in _errors())


def test_ci_guard_inventory_mismatch_detected(repo: dict[str, str]) -> None:
    repo["CONTRIBUTING.md"] = repo["CONTRIBUTING.md"].replace(
        "`check_public_ip.py`", "`check_other_guard.py`"
    )
    errors = _errors()
    assert any("CONTRIBUTING" in error and "不一致" in error for error in errors)


def test_registration_contradiction_detected(repo: dict[str, str]) -> None:
    repo["CONTRIBUTING.md"] += "注意：不开放注册。\n"
    assert any("矛盾" in error for error in _errors())


def test_missing_registration_env_doc_detected(repo: dict[str, str]) -> None:
    repo[".env.example"] = "# 空\n"
    assert any("DR_REGISTRATION_ENABLED" in error for error in _errors())


def test_adr_index_missing_entry_detected(repo: dict[str, str]) -> None:
    repo["docs/decisions/README.md"] = "\n".join(
        line
        for line in repo["docs/decisions/README.md"].splitlines()
        if "0017" not in line
    )
    assert any("ADR 索引缺少" in error for error in _errors())


def test_adr_index_dead_link_detected(repo: dict[str, str]) -> None:
    repo["docs/decisions/README.md"] += (
        "| 0099 | [x](0099-missing.md) | 草稿 | 2026-01-01 |\n"
    )
    assert any("不存在的文件" in error for error in _errors())


def test_readme_number_drift_detected(repo: dict[str, str]) -> None:
    repo["README.md"] = repo["README.md"].replace("**100 项", "**99 项")
    assert any("README 单元测试" in error for error in _errors())


def test_readme_e2e_number_drift_detected(repo: dict[str, str]) -> None:
    repo["README.md"] = repo["README.md"].replace(
        "10 个用例 / 2 个 spec", "11 个用例 / 2 个 spec"
    )
    assert any("E2E" in error for error in _errors())


def test_shallow_clone_fails_loud(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(TOOL, "_git", lambda _root, *_args: "true")
    with pytest.raises(TOOL.GuardError, match="fetch-depth"):
        TOOL.assert_full_clone(REPO_ROOT)


def test_real_repo_passes() -> None:
    """真仓库是最终判据；若失败，说明本 PR 的文档同步还没做全。"""
    assert TOOL.run(REPO_ROOT) == []
