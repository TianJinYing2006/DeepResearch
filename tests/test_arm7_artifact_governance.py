# -*- coding: utf-8 -*-
"""Arm 7「轨道 1」白名单治理的单测。

设计意图：这个 Arm 治的不是「仓库大小」而是**纪律**。
纪律若只写在文档里，下次照样漂移 ⇒ 必须有一处机器校验持续比对。
因此本文件的重心不是「happy path 通过」，而是**证明裁判抓得住违规** —— 包括那个会让 `!` 规则
整条失效的行尾注释坑（实测证据见 tools/check_results_whitelist.py 的文档串）。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "check_results_whitelist.py"


def _load_tool():
    """tools/ 不是包，按文件路径加载（conftest 只把仓库根塞进了 sys.path，够不到 tools/）。"""
    spec = importlib.util.spec_from_file_location("check_results_whitelist", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 防御性
        raise RuntimeError(f"无法加载 {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _load_tool()

HEADER = "# 头部说明（普通注释，不会被当成任何规则的出处）\n"


def _gi(*body: str) -> str:
    return "\n".join(body) + "\n"


def _violations(tmp_path: Path, gitignore_text: str, tracked: list[str], monkeypatch) -> list[str]:
    """在假仓库上跑检查：跟踪集合由参数注入（真实 git 路径另有用例覆盖）。"""
    monkeypatch.setattr(TOOL, "tracked_top_levels", lambda _root: tracked)
    (tmp_path / ".gitignore").write_text(gitignore_text, encoding="utf-8")
    return TOOL.check_repo(tmp_path)


def test_keep_and_bang_entries_are_parsed_with_proof():
    entries, violations = TOOL.parse_gitignore(
        _gi(
            HEADER,
            "research_engine/eval/results/run_*/",
            "# 出处：docs/eval-w7-conclusion.md（arm0 基线）",
            "!research_engine/eval/results/run_AAA/",
            "# keep: research_engine/eval/results/curated/ —— 读写方：eval/report_gen.py",
        )
    )
    assert violations == []
    assert [(e["kind"], e["path"]) for e in entries] == [
        ("bang", "research_engine/eval/results/run_AAA/"),
        ("keep", "research_engine/eval/results/curated/"),
    ]
    assert "docs/eval-w7-conclusion.md" in entries[0]["proof"]


def test_bang_line_with_trailing_comment_is_rejected():
    """踩过的坑：`.gitignore` 只认行首 `#`，行尾注释会变成 pattern 的一部分把 `!` 整条废掉。"""
    _, violations = TOOL.parse_gitignore(
        _gi(
            "research_engine/eval/results/run_*/",
            "!research_engine/eval/results/run_A/  # 出处：docs/x.md",
        )
    )
    assert any("行尾文字会被当成 pattern" in v for v in violations), violations


def test_blank_line_breaks_comment_ownership():
    """空行后的注释不得被认作下方规则的出处 —— 否则「写了注释」会被误判为「规则有出处」。 """
    entries, _ = TOOL.parse_gitignore(
        _gi(
            "research_engine/eval/results/run_*/",
            "# 这条注释属于上一节",
            "",
            "!research_engine/eval/results/run_A/",
        )
    )
    assert [e["proof"] for e in entries] == [""]


def _mkdirs(tmp_path: Path, *rel_paths: str) -> None:
    """预建路径，用来隔离掉「白名单路径在磁盘上不存在」这条不相关的断言。"""
    for rel in rel_paths:
        (tmp_path / rel).mkdir(parents=True, exist_ok=True)


def test_missing_proof_is_rejected(tmp_path: Path, monkeypatch):
    _mkdirs(tmp_path, "research_engine/eval/results/run_A")
    violations = _violations(
        tmp_path,
        _gi("research_engine/eval/results/run_*/", "!research_engine/eval/results/run_A/"),
        tracked=[],
        monkeypatch=monkeypatch,
    )
    assert any("没有任何出处说明" in v for v in violations), violations


def test_proof_must_cite_doc_or_reader(tmp_path: Path, monkeypatch):
    """「引用即入库」：要么引用到 docs/*.md，要么写明代码读写方。空泛描述不算证明。"""
    _mkdirs(tmp_path, "research_engine/eval/results/run_A")
    violations = _violations(
        tmp_path,
        _gi(
            "research_engine/eval/results/run_*/",
            "# 这是权威产物，大家都这么说的",
            "!research_engine/eval/results/run_A/",
        ),
        tracked=["run_A"],
        monkeypatch=monkeypatch,
    )
    assert any("不满足「引用即入库」" in v for v in violations), violations


def test_cited_doc_must_exist(tmp_path: Path, monkeypatch):
    """引用出处指向已删除的文档 = 效据失效，必须被抓住，否则注释会变成摆设。"""
    _mkdirs(tmp_path, "research_engine/eval/results/run_A", "docs")
    violations = _violations(
        tmp_path,
        _gi(
            "research_engine/eval/results/run_*/",
            "# 出处：docs/已被删除的文档.md",
            "!research_engine/eval/results/run_A/",
        ),
        tracked=["run_A"],
        monkeypatch=monkeypatch,
    )
    assert any("引用的文档不存在" in v for v in violations), violations


def test_detects_tracked_but_not_allowlisted(tmp_path: Path, monkeypatch):
    """Arm 7 的真实病灶：跟踪了一个既没有 `!` 白名单、也没有 keep 声明的 run。"""
    _mkdirs(tmp_path, "research_engine/eval/results/run_GHOST", "research_engine/eval/results/curated")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "ref.md").write_text("x", encoding="utf-8")

    violations = _violations(
        tmp_path,
        _gi(
            "research_engine/eval/results/run_*/",
            "# keep: research_engine/eval/results/curated/ —— 出处：docs/ref.md",
        ),
        tracked=["curated", "run_GHOST"],
        monkeypatch=monkeypatch,
    )
    assert any("跟踪了但不在白名单" in v and "run_GHOST" in v for v in violations), violations


def test_auto_enumerated_doc_alone_is_not_proof(tmp_path: Path, monkeypatch):
    """`docs/eval-report.md` 由 report_gen 自动写出，实测含 74 个 run id（≈ 无差别枚举）。

    只出现在这种表里的 run 不构成「有人挑选过它」的证据；若允许它当证明，判据自我作废。
    """
    _mkdirs(tmp_path, "research_engine/eval/results/run_A", "docs")
    (tmp_path / "docs" / "eval-report.md").write_text("x", encoding="utf-8")
    violations = _violations(
        tmp_path,
        _gi(
            "research_engine/eval/results/run_*/",
            "# 出处：docs/eval-report.md（趋势表里有它）",
            "!research_engine/eval/results/run_A/",
        ),
        tracked=["run_A"],
        monkeypatch=monkeypatch,
    )
    assert any("自动枚举文档" in v for v in violations), violations


def test_manual_doc_plus_auto_table_is_accepted(tmp_path: Path, monkeypatch):
    """既有手写引用又落在自动表里 —— 以手写为准，不应被误伤。"""
    _mkdirs(tmp_path, "research_engine/eval/results/run_A", "docs")
    for name in ("eval-report.md", "eval-w7-conclusion.md"):
        (tmp_path / "docs" / name).write_text("x", encoding="utf-8")
    violations = _violations(
        tmp_path,
        _gi(
            "research_engine/eval/results/run_*/",
            "# 出处：docs/eval-w7-conclusion.md（arm0 基线）",
            "!research_engine/eval/results/run_A/",
        ),
        tracked=["run_A"],
        monkeypatch=monkeypatch,
    )
    assert violations == [], violations


def test_detects_allowlisted_but_not_tracked(tmp_path: Path, monkeypatch):
    """反向偏离也要报：白名单写了但根本没跟踪，说明规则与实际已经脱节。"""
    _mkdirs(tmp_path, "research_engine/eval/results/run_A", "docs")
    (tmp_path / "docs" / "ref.md").write_text("x", encoding="utf-8")
    violations = _violations(
        tmp_path,
        _gi(
            "research_engine/eval/results/run_*/",
            "# 出处：docs/ref.md",
            "!research_engine/eval/results/run_A/",
        ),
        tracked=[],
        monkeypatch=monkeypatch,
    )
    assert any("白名单了但没跟踪" in v for v in violations), violations


def test_top_level_collapses_to_first_segment():
    assert TOOL.top_level("research_engine/eval/results/run_A/raw/q_001.json") == "run_A"
    assert TOOL.top_level("research_engine/eval/results/history.json") == "history.json"


def test_real_repository_is_consistent():
    """活体守卫：本仓库必须始终满足「白名单集合 ≡ 跟踪集合」。"""
    violations = TOOL.check_repo(REPO_ROOT)
    assert violations == [], violations


def test_real_gitignore_has_no_trailing_hash_on_bang_rules():
    lines = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    offenders = [ln for ln in lines if ln.strip().startswith("!") and "#" in ln]
    assert offenders == [], f"存在带行尾 `#` 的 `!` 规则，会把规则整条废掉：{offenders}"
