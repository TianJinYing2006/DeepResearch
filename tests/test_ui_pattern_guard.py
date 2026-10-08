# -*- coding: utf-8 -*-
"""UI 反模式守卫（`tools/check_ui_patterns.py`）的单测。

设计意图：这个守卫的价值**全在「它会不会漏」**。
一个「检测器没装就 exit 0」的守卫在 CI 里永远绿、永远没用 —— 所以本文件的重心是
证明三类漏网都被抓住：**发现漏报**（A）、**工具缺失放行**（B）、**工具改版后输出变形**（C）。

假 CLI 用 `[sys.executable, <临时脚本>]` 注入，而不是造可执行垫片：
跨平台一致、不需要 chmod、也不需要真的装 impeccable（那些用例在 CI 的
`lint-and-test` job 里跑，那里没有 npm ci）。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
TOOL_PATH = REPO_ROOT / "tools" / "check_ui_patterns.py"


def _load_tool():
    """tools/ 不是包，按文件路径加载（conftest 只把仓库根塞进了 sys.path，够不到 tools/）。"""
    spec = importlib.util.spec_from_file_location("check_ui_patterns", TOOL_PATH)
    if spec is None or spec.loader is None:  # pragma: no cover - 防御性
        raise RuntimeError(f"无法加载 {TOOL_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


TOOL = _load_tool()

FINDING = {
    "antipattern": "side-tab",
    "name": "Side-tab accent border",
    "description": "Thick colored border on one side of a card ...",
    "severity": "warning",
    "category": "slop",
    "file": "web/frontend/src/index.css",
    "line": 187,
    "snippet": "border-l-2",
}


def _fake_cli(tmp_path: Path, *, stdout: str, exit_code: int = 0, crash: bool = False) -> list[str]:
    """造一个假检测器：忽略 `detect --json <target>` 参数，固定产出 stdout。"""
    script = tmp_path / "fake_impeccable.py"
    if crash:
        body = "import sys; sys.stderr.write('boom\\n'); sys.exit(3)"
    else:
        body = (
            "import sys\n"
            f"sys.stdout.write({stdout!r})\n"
            f"sys.exit({exit_code})\n"
        )
    script.write_text(body, encoding="utf-8")
    return [sys.executable, str(script)]


def _src(tmp_path: Path) -> Path:
    """建一个假的扫描目标目录，隔离掉「目标不存在」这条不相关断言。"""
    target = tmp_path / "web" / "frontend" / "src"
    target.mkdir(parents=True, exist_ok=True)
    return target


# --- A：发现必须被报出来（核心判据） ---------------------------------------


def test_reports_findings_with_location_and_rule(tmp_path: Path):
    cli = _fake_cli(tmp_path, stdout=f"[{json.dumps(FINDING)}]", exit_code=2)
    violations = TOOL.check_repo(tmp_path, cli=cli, target=_src(tmp_path))
    assert len(violations) == 1, violations
    msg = violations[0]
    assert "index.css:187" in msg and "side-tab" in msg and "border-l-2" in msg


def test_clean_scan_passes(tmp_path: Path):
    """空数组 + exit 0 ⇒ 通过。这是唯一允许返回空违规的正常路径。"""
    cli = _fake_cli(tmp_path, stdout="[]", exit_code=0)
    assert TOOL.check_repo(tmp_path, cli=cli, target=_src(tmp_path)) == []


def test_exit_code_2_with_empty_json_still_passes(tmp_path: Path):
    """判据以 **JSON 内容**为准而非退出码 —— 工具若改退出码语义，不会误伤。"""
    cli = _fake_cli(tmp_path, stdout="[]", exit_code=2)
    assert TOOL.check_repo(tmp_path, cli=cli, target=_src(tmp_path)) == []


# --- B：工具缺失 / 崩溃绝不能放行 -------------------------------------------


def test_missing_cli_is_a_violation(tmp_path: Path, monkeypatch):
    """核心防退化用例：找不到检测器时必须违规，否则守卫在 CI 里会变成永远绿的摆设。"""
    monkeypatch.setattr(TOOL, "resolve_cli", lambda _root: None)
    violations = TOOL.check_repo(tmp_path, target=_src(tmp_path))
    assert len(violations) == 1
    assert "找不到 impeccable 检测器" in violations[0]
    assert "npm ci" in violations[0]


def test_crashing_cli_is_a_violation(tmp_path: Path):
    cli = _fake_cli(tmp_path, stdout="", crash=True)
    violations = TOOL.check_repo(tmp_path, cli=cli, target=_src(tmp_path))
    assert len(violations) == 1
    assert "检测器执行失败" in violations[0]


def test_nonexistent_cli_command_is_a_violation(tmp_path: Path):
    """node 不在 PATH / 二进制缺失 ⇒ subprocess 抛 OSError ⇒ 必须变成违规而不是异常上抛。"""
    violations = TOOL.check_repo(
        tmp_path, cli=["definitely-not-a-real-binary-xyz"], target=_src(tmp_path)
    )
    assert len(violations) == 1
    assert "检测器执行失败" in violations[0]


# --- C：输出变形不得静默变空转 ----------------------------------------------


@pytest.mark.parametrize(
    "stdout, hint",
    [
        ("", "没有输出任何内容"),
        ("not json at all", "不是合法 JSON"),
        ('{"antipattern": "x"}', "不是 JSON 数组"),
    ],
)
def test_unparsable_output_is_a_violation(tmp_path: Path, stdout: str, hint: str):
    cli = _fake_cli(tmp_path, stdout=stdout, exit_code=0)
    violations = TOOL.check_repo(tmp_path, cli=cli, target=_src(tmp_path))
    assert len(violations) == 1
    assert "无法解析" in violations[0] and hint in violations[0]


def test_missing_scan_target_is_a_violation(tmp_path: Path):
    cli = _fake_cli(tmp_path, stdout="[]", exit_code=0)
    violations = TOOL.check_repo(tmp_path, cli=cli, target=tmp_path / "nope")
    assert len(violations) == 1
    assert "扫描目标不存在" in violations[0]


# --- 纯函数：解析与路径收敛 --------------------------------------------------


def test_parse_findings_ignores_non_dict_entries():
    findings, err = TOOL.parse_findings('["junk", {"antipattern": "x"}]')
    assert err == ""
    assert [f["antipattern"] for f in findings] == ["x"]


def test_parse_findings_surfaces_json_error():
    findings, err = TOOL.parse_findings("{oops")
    assert findings is None
    assert "不是合法 JSON" in err


def test_rel_path_normalizes_to_posix_relative(tmp_path: Path):
    """消息里必须是仓库相对路径（正斜杠）—— CI 在 Linux、本机在 Windows，绝对路径不可比。"""
    inside = tmp_path / "web" / "frontend" / "src" / "index.css"
    inside.parent.mkdir(parents=True, exist_ok=True)
    inside.write_text("x", encoding="utf-8")
    assert TOOL.rel_path(str(inside), tmp_path) == "web/frontend/src/index.css"


def test_rel_path_falls_back_to_normalized_raw_for_outside_paths(tmp_path: Path):
    assert TOOL.rel_path(r"C:\somewhere\else.css", tmp_path) == "C:/somewhere/else.css"


def test_finding_without_file_or_line_does_not_crash(tmp_path: Path):
    """畸形条目（缺字段）也要出违规，不能因为格式不全就漏报。"""
    cli = _fake_cli(tmp_path, stdout='[{"antipattern": "weird"}]', exit_code=2)
    violations = TOOL.check_repo(tmp_path, cli=cli, target=_src(tmp_path))
    assert len(violations) == 1
    assert "weird" in violations[0]


# --- 活体校验：真装了检测器时，本仓库必须干净 --------------------------------


def test_real_repository_is_clean_when_tool_present():
    """本仓库是「已审计通过」的基线 ⇒ 检测器在位时应当零发现。

    检测器不在位时 skip 而不是 fail：pytest 跑在 `lint-and-test` job，那里没有 npm ci；
    真正的门禁在 CI 的 `frontend` job（先 npm ci 再跑本守卫）。
    """
    cli = TOOL.resolve_cli(REPO_ROOT)
    if not cli:
        pytest.skip("impeccable 未安装（web/frontend/node_modules），跳过活体校验")
    violations = TOOL.check_repo(REPO_ROOT, cli=cli)
    assert violations == [], violations


def test_guard_is_executable_and_exits_nonzero_on_failure():
    """守卫必须能被 `python tools/check_ui_patterns.py` 直接调用（CI 的调用形态）。"""
    proc = subprocess.run(
        [sys.executable, str(TOOL_PATH)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=TOOL.TIMEOUT_S + 60,
    )
    # 无论有没有装检测器，都不允许以「静默成功且无输出」的形态通过
    if proc.returncode == 0:
        assert "OK" in proc.stdout
    else:
        assert proc.returncode == 1
        assert "[guard]" in proc.stderr
