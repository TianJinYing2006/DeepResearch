"""W7 对照实验 runner 的可复用护栏单测。

覆盖 2026-09-13 补跑事故后新增的两道防线：
1. `_git_rev()` —— 记录实验代码修订（含工作树是否脏）；
2. `_revision_mismatch_warning()` —— 续跑时若修订与首轮不一致必须显式告警。

背景：W7 补跑时未校验「工作树 ≡ 首轮实验时状态」，导致 Block 0 跑在 `95adb77`+patch、
Block 1/2 跑在 `ca51886`，引用口径不可合并，直到分析阶段才发现。
"""
from __future__ import annotations

from research_engine.eval.w7_experiment import (
    REJUDGE_NOTE,
    _git_rev,
    _revision_mismatch_warning,
)


def test_git_rev_returns_non_empty_marker():
    rev = _git_rev()
    assert isinstance(rev, str) and rev
    # 正常情况下是短提交号（可能带 +dirty）；取不到 git 时回落 unknown
    assert rev == "unknown" or any(c.isalnum() for c in rev)


def test_mismatch_warning_fires_on_revision_drift():
    warn = _revision_mismatch_warning("95adb77+patch:aa4bb452", "ca518866")
    assert warn is not None
    assert "95adb77+patch:aa4bb452" in warn
    assert "ca518866" in warn


def test_mismatch_warning_silent_when_revision_matches():
    assert _revision_mismatch_warning("ca518866", "ca518866") is None
    assert _revision_mismatch_warning("ca518866+dirty", "ca518866+dirty") is None


def test_mismatch_warning_silent_when_first_round_revision_unknown():
    # 早期 manifest 没有 code_revision：不得误报
    assert _revision_mismatch_warning(None, "ca518866") is None
    assert _revision_mismatch_warning("", "ca518866") is None


def test_mismatch_warning_silent_when_current_revision_unavailable():
    # 当前修订取不到（如非 git 环境）：不得用一个不可信的值去告警
    assert _revision_mismatch_warning("95adb77+patch:aa4bb452", "unknown") is None


def test_rejudge_note_is_the_confound_warning():
    assert "validator" in REJUDGE_NOTE
    assert "w7_rejudge.py" in REJUDGE_NOTE
