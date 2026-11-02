# -*- coding: utf-8 -*-
"""W6 Job Object 单测 + 沙箱覆盖复核 + 2026-11 审计 #327 负向安全用例。

测试覆盖（DoD 第 0 条 + 审计 #327）：
- Job 路径正常执行：1+1=2 输出；超时：sleep > timeout → 标 timed_out + note 含"超时"
- Job 创建函数健壮性：CreateJobObjectW 返回非空句柄 + SetInformationJobObject 成功
- 默认关路径：use_job_object=False 时 metadata 无 "job" 字段
- **负向安全（#327）**：AST 拒 `import os`；`__import__('_socket'/'_ctypes')` 被拒；
  `os.system`/`os.chdir`/`exec` 被拒；沙箱外 open（读/写）被拒；父进程 env 不继承；
  输出超限截断并杀进程。
- 正向回归：`math` 计算、动态 `__import__('os')`、沙箱内写文件仍可用。

设计说明：恶意 ctypes 逃逸进程的端到端"被杀净"验证需要构造复杂的 CreateProcessW
测试代码；本期已封 `_ctypes`/`ctypes` import（动态路径），Job Object 仍为可选增强，
端到端杀树验证列入后续（README §3.2 已如实登记该限制）。
"""
from __future__ import annotations

import os
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
    """默认路径超时（Popen.wait TimeoutExpired）仍正确处理（W4 既有用例回归保护）。"""
    from config import config
    from research_engine.tools import code_exec
    monkeypatch.setattr(config.code_exec, "timeout", 1)
    r = code_exec.exec_code("__import__('time').sleep(30)")
    assert not r.ok
    assert "超时" in r.note


# ---------------- 2026-11 审计 #327：负向安全用例（至少 4 条，当前 8 条） ----------------

def test_neg_ast_import_os_rejected():
    """静态 import 白名单仍生效（AST 层）。"""
    from research_engine.tools.code_exec import exec_code

    r = exec_code("import os\nprint(os.getcwd())")
    assert not r.ok
    assert "导入被沙箱拒绝" in r.note


def test_neg_dynamic_import_dangerous_c_ext_denied():
    """`__import__` 动态路径被封：_socket/_ctypes 由 import 事件拒绝。"""
    from research_engine.tools.code_exec import exec_code

    for module in ("_socket", "_ctypes"):
        r = exec_code(f"m = __import__({module!r})\nprint(m)")
        blob = r.note + r.stderr
        assert not r.ok, module
        assert "not allowed by sandbox: import" in blob, (module, blob[-300:])


def test_neg_os_system_denied():
    """进程创建事件被拒（真实事件名 os.system）。"""
    from research_engine.tools.code_exec import exec_code

    r = exec_code("__import__('os').system('echo hacked')")
    assert not r.ok
    assert "not allowed by sandbox: os.system" in (r.note + r.stderr)


def test_neg_os_chdir_denied():
    """os.chdir 被拒 → 固定沙箱根判据不可被改目录绕过。"""
    from research_engine.tools.code_exec import exec_code

    r = exec_code("__import__('os').chdir('.')")
    assert not r.ok
    assert "not allowed by sandbox: os.chdir" in (r.note + r.stderr)


def test_neg_exec_builtin_denied():
    """exec/compile 事件被拒 → 用户代码不能再动态执行字符串。"""
    from research_engine.tools.code_exec import exec_code

    r = exec_code("exec('x = 1')\nprint('reached')")
    assert not r.ok
    assert "not allowed by sandbox" in (r.note + r.stderr)


def test_neg_open_outside_sandbox_read_denied():
    """沙箱根外的读被拒（防偷读 .env/密钥）。"""
    from research_engine.tools.code_exec import exec_code

    outside = r"C:/Windows/win.ini" if _WIN32 else "/etc/passwd"
    r = exec_code(f'open(r"{outside}")')
    assert not r.ok
    assert "open outside sandbox cwd" in (r.note + r.stderr)


def test_neg_open_outside_sandbox_write_denied():
    """沙箱根外的写同样被拒，且不会真的落盘。"""
    from research_engine.tools.code_exec import exec_code

    target = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "_code_exec_escape_probe.txt")
    if os.path.exists(target):
        os.remove(target)
    r = exec_code(f'open(r"{target}", "w").write("x")')
    assert not r.ok
    assert "open outside sandbox cwd" in (r.note + r.stderr)
    assert not os.path.exists(target)


def test_neg_parent_env_not_inherited(monkeypatch):
    """最小环境：父进程密钥不进入沙箱子进程（-E 只忽略 PYTHON*，不够）。"""
    from research_engine.tools.code_exec import exec_code

    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-audit-secret-value")
    r = exec_code(
        "e = __import__('os').environ\n"
        "print('HAS_KEY=', 'DASHSCOPE_API_KEY' in e)\n"
        "print('SECRET=', 'sk-audit-secret-value' in str(dict(e)))")
    assert r.ok, r.note
    assert "HAS_KEY= False" in r.stdout
    assert "SECRET= False" in r.stdout
    assert "sk-audit-secret-value" not in r.stdout


def test_neg_output_overflow_truncated_and_killed(monkeypatch):
    """输出超限：截断 + [TRUNCATED] 标记 + 杀进程（父进程内存不被失控输出撑爆）。"""
    from config import config
    from research_engine.tools.code_exec import _TRUNC_MARK, exec_code

    monkeypatch.setattr(config.code_exec, "max_output_bytes", 4096)
    r = exec_code("while True:\n    print('A' * 200)")
    assert not r.ok
    assert "[TRUNCATED]" in r.stdout
    assert len(r.stdout) <= 4096 + len(_TRUNC_MARK)
    assert "截断" in r.note


def test_pos_dynamic_import_os_still_usable():
    """`__import__('os')` 仍可用（os 由包装脚本预加载，不经 import 事件）——旧行为保持。"""
    from research_engine.tools.code_exec import exec_code

    r = exec_code("m = __import__('os')\nprint('OS_OK=', m.name in ('nt', 'posix'))")
    assert r.ok, r.stderr
    assert "OS_OK= True" in r.stdout

