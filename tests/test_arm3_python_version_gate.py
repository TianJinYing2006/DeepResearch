# -*- coding: utf-8 -*-
"""W8 Arm 3：依赖环境锁定 —— 第二刀（声明）与第三刀（强制）的确定性断言。

覆盖 `docs/requirements/8-fault-transparency-and-reproducibility.md` §5.3：
- §5.3.2 声明：`pyproject.toml` 的 `requires-python` + `[tool.ruff] target-version` + README 三处口径
- §5.3.3 强制：运行时版本闸（`research_engine/__init__.py`）+ CI matrix 三档

**本文件的重点不是「测一个 if」，而是「把四处版本口径的耦合钉死」**：
§5.3 的立论是「声明不具强制力，真强制只有两处（运行时闸 + CI matrix）」，
所以这四处必须互相对得上 —— 任何一处单独被改（例如把 PY_MAX 提到 3.14 却忘了改
`requires-python`）都会在这里变红，而不是等到用户在 3.14 上撞见难懂的二进制导入错误。

全部零 LLM / 零 API / 零网络。
"""
from __future__ import annotations

import pathlib
import re
import sys
import tomllib
from collections import namedtuple

import pytest

import research_engine
from research_engine.failure_reasons import (
    ALL_REASONS,
    NON_TOOL_REASONS,
    TOOL_REASONS,
    FailureReason,
    is_tool_reason,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
CI_YML = REPO_ROOT / ".github" / "workflows" / "ci.yml"

_VI = namedtuple("_VI", "major minor micro releaselevel serial")


# ---------------- §5.3.3 运行时版本闸：纯函数边界 ----------------

@pytest.mark.parametrize(
    "version",
    [(3, 11, 0), (3, 11, 15), (3, 12, 0), (3, 12, 13), (3, 13, 0), (3, 13, 12)],
)
def test_gate_accepts_supported_versions(version):
    """区间内的版本必须放行（下界 3.11 与上界 3.13 都含端点）。"""
    research_engine.assert_supported_python(_VI(*version, "final", 0))


@pytest.mark.parametrize(
    "version,why",
    [
        ((3, 10, 9), "低于下界：langx 依赖链已在 3.10 上失去保障"),
        ((3, 9, 18), "远低于下界"),
        ((3, 14, 0), "高于上界：.deps 的 cp313 轮子教训，尚无验证"),
        ((4, 0, 0), "未来大版本"),
    ],
)
def test_gate_rejects_unsupported_versions(version, why):
    """区间外的版本必须抛 RuntimeError（不是 Warning、不是静默降级）。"""
    with pytest.raises(RuntimeError):
        research_engine.assert_supported_python(_VI(*version, "final", 0))


def test_gate_error_message_is_actionable():
    """报错必须自带三样信息：支持区间、当前版本、去哪查 —— 否则等于没拦。"""
    with pytest.raises(RuntimeError) as ei:
        research_engine.assert_supported_python(_VI(3, 10, 9, "final", 0))
    msg = str(ei.value)
    assert "3.11 ~ 3.13" in msg, "必须写明支持区间"
    assert "3.10.9" in msg, "必须写明当前实际版本"
    assert "§5.3" in msg, "必须给出文档定位"


def test_gate_default_arg_uses_real_interpreter():
    """不传参时必须读真实 `sys.version_info` —— 否则闸门形同虚设。"""
    assert research_engine.assert_supported_python() is None  # 本测试进程本身就跑在受支持版本上
    assert (sys.version_info[0], sys.version_info[1]) >= research_engine.PY_MIN


def test_gate_runs_at_import_time(monkeypatch):
    """闸门必须在 **import 的那一刻**生效，而不是等调用方记得手动调。

    做法：把 `sys.version_info` 换成 3.10，重新 exec 模块源码，必须抛。
    """
    src = (REPO_ROOT / "research_engine" / "__init__.py").read_text(encoding="utf-8")
    monkeypatch.setattr(sys, "version_info", _VI(3, 10, 9, "final", 0))
    with pytest.raises(RuntimeError, match="3.11 ~ 3.13"):
        exec(compile(src, "research_engine/__init__.py", "exec"), {"__name__": "research_engine"})


# ---------------- §5.3.2 / §5.3.3 四处口径耦合护栏 ----------------

def test_declared_range_pinned():
    """运行时闸声明区间 = (3,11)~(3,13)，且 `requires-python` 与该区间一致。

    `requires-python = ">=3.11,<3.14"` 的上界写成 3.14 是**开区间上界**，
    与运行时闸的闭区间 `PY_MAX=(3,13)` 是同一件事的两种写法 —— 这里把它钉住。
    """
    assert research_engine.PY_MIN == (3, 11)
    assert research_engine.PY_MAX == (3, 13)


def test_pyproject_declaration_matches_gate():
    """`pyproject.toml` 的声明必须与运行时闸同区间（改一处必须改另一处）。"""
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    lo, hi = research_engine.PY_MIN, research_engine.PY_MAX
    assert data["project"]["requires-python"] == f">={lo[0]}.{lo[1]},<{hi[0]}.{hi[1] + 1}"

    # 第二刀里唯一「真强制」的一处：ruff 按该版本语法集检查
    assert data["tool"]["ruff"]["target-version"] == f"py{lo[0]}{lo[1]}"


def test_readme_states_same_range():
    """README「环境要求」不得停留在旧口径（曾写 `Python 3.10+`）。"""
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    lo, hi = research_engine.PY_MIN, research_engine.PY_MAX
    assert f"Python {lo[0]}.{lo[1]} ~ {hi[0]}.{hi[1]}" in readme
    assert "Python 3.10+" not in readme, "旧口径残留会让读者以为 3.10 仍受支持"


def test_ci_matrix_covers_declared_range():
    """CI matrix 必须恰好覆盖声明区间（§5.3.3 第三刀：让「支持三档」被真实验证）。"""
    text = CI_YML.read_text(encoding="utf-8")
    lo, hi = research_engine.PY_MIN, research_engine.PY_MAX
    expected = {f"{lo[0]}.{v}" for v in range(lo[1], hi[1] + 1)}
    m = re.search(r"python-version:\s*\[([^\]]+)\]", text)
    assert m, "CI 里找不到 python-version matrix"
    actual = {s.strip().strip('"').strip("'") for s in m.group(1).split(",")}
    assert actual == expected, (
        f"matrix {sorted(actual)} 与声明区间 {lo}~{hi} 不一致（期望 {sorted(expected)}）"
    )


def test_ci_installs_from_lock():
    """§5.3.1 第一刀：CI 必须按 lock 安装，否则锁了等于没锁。"""
    text = CI_YML.read_text(encoding="utf-8")
    assert "requirements-lock.txt" in text and "requirements-dev-lock.txt" in text
    assert "pip install -r requirements-lock.txt -r requirements-dev-lock.txt" in text, (
        "CI 安装步骤必须消费 lock；若仍在读 requirements.txt，第一刀就落空了"
    )


# ---------------- `(str, Enum)` → `StrEnum`（py311 目标的直接后果） ----------------

def test_failure_reason_is_str_enum():
    """`StrEnum`（3.11+）：既能当 str 用，`str()`/f-string 又不会渲染成 `FailureReason.X`。

    这条是 ruff `target-version=py311` 翻出 `UP042` 后的行为锁定 ——
    `(str, Enum)` 下 `f"{member}"` 会给出 `"FailureReason.TIMEOUT"`（经典坑）。
    """
    r = FailureReason.TIMEOUT
    assert isinstance(r, str)
    assert r == "timeout" and r.value == "timeout"
    assert str(r) == "timeout"
    assert f"{r}" == "timeout"


def test_reason_sets_hold_plain_strings():
    """两组 frozenset 存的是**纯字符串**，`is_tool_reason` 逐字面值判断（单向派生契约的判据）。"""
    assert all(isinstance(x, str) and type(x) is str for x in ALL_REASONS)
    assert TOOL_REASONS.isdisjoint(NON_TOOL_REASONS)
    assert len(ALL_REASONS) == 9
    for reason in TOOL_REASONS:
        assert is_tool_reason(reason)
    for reason in NON_TOOL_REASONS:
        assert not is_tool_reason(reason), "非工具类原因不得被认成工具层产生"
