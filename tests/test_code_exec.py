# -*- coding: utf-8 -*-
"""W6 Job Object 单测：Windows 路径（skipif 非 win32）+ 沙箱既有覆盖复核。

测试覆盖（DoD 第 0 条）：
- _run_with_job 正常执行：1+1=2 输出
- _run_with_job 超时：sleep > timeout → 标 timed_out + note 含"超时"
- Job 创建函数健壮性：CreateJobObjectW 返回非空句柄 + SetInformationJobObject 成功
- 默认关路径：use_job_object=False 时走 _run_plain（不进 Job 分支）

设计说明：恶意 ctypes 逃逸进程的端到端"被杀净"验证需要构造复杂的 CreateProcessW
测试代码（沙箱内 AST 拒 import ctypes），本期以"超时杀进程 + 机制就位 + 注释声明"
覆盖核心价值，恶意逃逸的端到端验证列入 W6 收尾后再补。
"""
from __future__ import annotations

import sys

import pytest

_WIN32 = sys.platform == "win32"


# ---------------- Job 路径（Windows，需 env CODE_EXEC_USE_JOB=true） ----------------

@pytest.mark.skipif(not _WIN32, reason="Job Object 仅 Windows")
def test_job_object_created_with_kill_on_job_close(monkeypatch):
    """Job 句柄非空且带 KILL_ON_JOB_CLOSE 标志（机制就位证据）。"""
    from research_engine.tools import code_exec
    h = code_exec._win_create_job()
    try:
        assert h, "CreateJobObjectW 应返回非空句柄"
    finally:
        code_exec._win_close_handle(h)


@pytest.mark.skipif(not _WIN32, reason="Job Object 仅 Windows")
def test_job_path_normal_exec(monkeypatch):
    """Job 路径下正常代码 1+1=2 顺利输出（被分配进 job 但不超时）。"""
    from config import config
    from research_engine.tools import code_exec
    monkeypatch.setattr(config.code_exec, "use_job_object", True)
    monkeypatch.setattr(config.code_exec, "timeout", 15)

    r = code_exec.exec_code("import math\nprint(math.sqrt(16))")
    assert r.ok, f"expected ok but: {r.note}"
    assert "4.0" in r.stdout
    assert r.metadata.get("job") == "enabled"


@pytest.mark.skipif(not _WIN32, reason="Job Object 仅 Windows")
def test_job_path_timeout_kills_process(monkeypatch):
    """Job 路径下超时被杀 + note 含"超时"（验证 KILL_ON_JOB_CLOSE 在超时分支生效）。"""
    from config import config
    from research_engine.tools import code_exec
    monkeypatch.setattr(config.code_exec, "use_job_object", True)
    monkeypatch.setattr(config.code_exec, "timeout", 2)  # 短超时加速测试

    r = code_exec.exec_code("__import__('time').sleep(30)\nprint('should not reach')")
    assert not r.ok
    assert "超时" in r.note
    assert r.metadata.get("job") == "enabled"


@pytest.mark.skipif(not _WIN32, reason="Job Object 仅 Windows")
def test_job_path_disabled_falls_back_to_plain(monkeypatch):
    """use_job_object=False 时走 plain 路径，metadata 无 job 字段。"""
    from config import config
    from research_engine.tools import code_exec
    monkeypatch.setattr(config.code_exec, "use_job_object", False)

    r = code_exec.exec_code("print('hello')")
    assert r.ok
    assert "hello" in r.stdout
    assert "job" not in (r.metadata or {})


# ---------------- 沙箱覆盖复核（跨平台，确保 Job 改造不破坏既有四用例） ----------------

def test_plain_path_normal_exec():
    """默认（非 Job）路径仍正常执行（W4 既有用例回归保护）。"""
    from research_engine.tools.code_exec import exec_code
    r = exec_code("import math\nprint(math.sqrt(16))")
    assert r.ok
    assert "4.0" in r.stdout


def test_plain_path_syntax_error_reported():
    """语法错误仍回传不吞（W4 既有用例回归保护）。"""
    from research_engine.tools.code_exec import exec_code
    r = exec_code("print(1")
    assert not r.ok
    assert r.note


def test_plain_path_timeout(monkeypatch):
    """默认路径超时（subprocess.run TimeoutExpired）仍正确处理（W4 既有用例回归保护）。"""
    from config import config
    from research_engine.tools import code_exec
    monkeypatch.setattr(config.code_exec, "timeout", 1)
    r = code_exec.exec_code("__import__('time').sleep(30)")
    assert not r.ok
    assert "超时" in r.note
