"""代码执行工具（W4 Q2 定案：三层纵深 sandbox = B++ 修正版）。

安全模型（设计详见 docs/requirements/4-tools-and-strategic-model.md §5）：
- 主路径：subprocess `python -I -E -S`（-I 隔离用户环境 / -E 忽略环境变量防密钥泄漏 /
  -S 不加载 site-packages = 天然白名单）+ 独立临时 cwd + wall-clock timeout + 输出截断
- 第二层：AST import 白名单（9 个纯 stdlib；可被动态 import 绕过 → 第三层兜底）
- 第三层：PEP 578 Audit Hook（包装脚本内 sys.addaudithook，运行时拦截）——
  破坏/网络/进程类事件无条件拒绝；open 例外走 cwd 白名单路径判断（避免误伤包装脚本自身读写）
- Windows 现实：resource 模块 Unix-only → timeout 是唯一可靠 kill；Job Object 为可选增强
  （CODE_EXEC_USE_JOB=1，进程树级 KILL_ON_JOB_CLOSE——超时关闭 job 句柄即全树终止，防 ctypes 逃逸进程）。
  实现（W6）：Popen 正常启动 + 立即 AssignProcessToJobObject（CREATE_SUSPENDED 因 Popen._thread 缺失不可行，
  竞态窗口 = 解释器启动前微秒级，audit hook 已拦 Python 层 fork）；Job 创建失败安全降级 plain。

输出契约（给 researcher 用）：
    CodeExecOutput(ok, stdout(截断), stderr(截断), note, elapsed)
失败不吞：note 带报错信息（Q2/Q4），由调用方写入 finding.metadata / finding.note。
"""
from __future__ import annotations

import ast
import hashlib
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from typing import List, Set

from config import config

# 计算型子问题触发关键词（Q1 P2：确定性启发式，零 LLM 成本；不算路由，是 code 工具的入参门槛）
TRIGGER_KEYWORDS = (
    "复杂度", "flops", "计算", "数值", "对比", "推导", "估算", "模拟", "换算",
    "时间复杂度", "空间复杂度", "accumulate", "compute", "calculate",
)
# AST import 白名单（Q2 8 个 + Q4 +random 共 9 个；纯 stdlib、无 I/O/系统/网络能力）
IMPORT_WHITELIST: Set[str] = {
    "math", "statistics", "itertools", "functools",
    "decimal", "fractions", "collections", "typing", "random",
}
# 运行时无条件拒绝的 audit 事件（PEP 578；没有合法用途）
AUDIT_DENY_EVENTS = (
    "subprocess.Popen", "socket.socket", "os.system", "os.exec", "pty.spawn",
    "shutil.rmtree", "shutil.unpack_archive", "os.rename", "os.replace",
    "os.remove", "os.unlink", "os.rmdir", "os.mkdir", "os.makedirs",
    "os.symlink", "os.link",
)
# 输出截断（Q2：各 128KB，超限标 [TRUNCATED]）
MAX_OUTPUT = 128 * 1024
_TRUNC_MARK = "\n...[TRUNCATED]"

# 并发信号量（Q2：默认 2 可配 4；防 CPU 密集抢占主进程）
_semaphore = threading.BoundedSemaphore(config.code_exec.concurrency)

# ---- P2 触发判定 ----

_MAX_TRIGGER_RE = re.compile(
    r"(复杂度|flops|数值|计算|对比|推导|估算|模拟|换算|时间复杂|空间复杂|compute|calculate|accumulate)",
    re.IGNORECASE,
)


def should_execute(query: str) -> bool:
    """P2 关键词启发式：命中才执行，未命中返回空（零 LLM 成本、零 state 字段）。"""
    return bool(_MAX_TRIGGER_RE.search(query or ""))


# ---- AST 白名单层 ----

_AST_denied: List[str] = []


def _check_imports(code: str) -> List[str]:
    """AST 扫描 import，返回被拒模块列表（空 = 全部通过）。"""
    denied: List[str] = []
    try:
        tree = ast.parse(code)
    except SyntaxError:
        return denied  # 语法错误交给运行时暴露，不进被拒列表
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                root = n.name.split(".")[0]
                if root not in IMPORT_WHITELIST:
                    denied.append(n.name)
        elif isinstance(node, ast.ImportFrom):
            if node.module and node.module.split(".")[0] not in IMPORT_WHITELIST:
                denied.append(node.module)
    return denied


# ---- 包装脚本（第三层：Audit Hook）----

def _build_wrapper(user_code: str) -> str:
    deny = ", ".join(repr(e) for e in AUDIT_DENY_EVENTS)
    return f'''# -*- coding: utf-8 -*-
"""W4 sandbox wrapper：PEP 578 Audit Hook（第三层）+ 用户代码。"""
import os
import sys

_DENY = ({deny},)
def _audit(event, args):
    if event in _DENY:
        raise PermissionError("operation not allowed by sandbox: " + event)
    if event == "open":
        # open 例外：白名单路径判断——任何 cwd 外的 open 都拒绝
        # （读也不行：防偷读 .env/密钥等敏感文件；buildin open 不经 AST，全靠本层）
        if not args:
            raise PermissionError("open without path")
        path = os.path.abspath(args[0])
        try:
            inside = os.path.commonpath([path, os.getcwd()]) == os.getcwd()
        except ValueError:  # 跨盘符（Windows 不同 drive）commonpath 抛异常
            inside = False
        if not inside:
            raise PermissionError("open outside sandbox cwd: " + path)
sys.addaudithook(_audit)

{user_code}
'''


# ---- 输出 ----

@dataclass
class CodeExecOutput:
    ok: bool = False
    stdout: str = ""
    stderr: str = ""
    note: str = ""
    elapsed: float = 0.0
    script_hash: str = ""  # Q6：code:{hash} 的哈希（脚本+参数+查询），供 source 协议
    metadata: dict = field(default_factory=dict)


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + _TRUNC_MARK


# ---- Windows Job Object（第四层纵深增强，W6 实现，默认关）----
# 仅在 Windows 启用：进程树级 KILL_ON_JOB_CLOSE——超时关闭 job 句柄即终止全部派生进程，
# 防 ctypes 逃逸进程（audit hook 拦 Python 层 subprocess.Popen/socket，但 ctypes 调
# CreateProcessW 不触发 Python audit，靠 job 兜底）。

if sys.platform == "win32":  # pragma: no cover - 平台守卫
    import ctypes
    from ctypes import wintypes

    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
    _JobObjectExtendedLimitInformation = 9
    _PROCESS_SET_QUOTA = 0x0100
    _PROCESS_TERMINATE = 0x0001
    _PROCESS_QUERY_INFORMATION = 0x0400

    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [
            ("ReadOperationCount", ctypes.c_ulonglong),
            ("WriteOperationCount", ctypes.c_ulonglong),
            ("OtherOperationCount", ctypes.c_ulonglong),
            ("ReadTransferCount", ctypes.c_ulonglong),
            ("WriteTransferCount", ctypes.c_ulonglong),
            ("OtherTransferCount", ctypes.c_ulonglong),
        ]

    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("PerProcessUserTimeLimit", ctypes.c_longlong),
            ("PerJobUserTimeLimit", ctypes.c_longlong),
            ("LimitFlags", wintypes.DWORD),
            ("MinimumWorkingSetSize", ctypes.c_size_t),
            ("MaximumWorkingSetSize", ctypes.c_size_t),
            ("ActiveProcessLimit", wintypes.DWORD),
            ("Affinity", ctypes.c_void_p),
            ("PriorityClass", wintypes.DWORD),
            ("SchedulingClass", wintypes.DWORD),
        ]

    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [
            ("BasicLimitInformation", _JOBOBJECT_BASIC_LIMIT_INFORMATION),
            ("IoInfo", _IO_COUNTERS),
            ("ProcessMemoryLimit", ctypes.c_size_t),
            ("JobMemoryLimit", ctypes.c_size_t),
            ("PeakProcessMemoryUsed", ctypes.c_size_t),
            ("PeakJobMemoryUsed", ctypes.c_size_t),
        ]

    def _win_create_job():
        """创建带 KILL_ON_JOB_CLOSE 标志的 Job Object；失败返回 None（调用方降级 plain）。"""
        k32 = ctypes.windll.kernel32
        h_job = k32.CreateJobObjectW(None, None)
        if not h_job:
            return None
        info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        ok = k32.SetInformationJobObject(
            h_job, _JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        )
        if not ok:
            k32.CloseHandle(h_job)
            return None
        return h_job

    def _win_open_process(pid: int, access: int):
        return ctypes.windll.kernel32.OpenProcess(access, False, pid)

    def _win_assign_job(h_job, h_proc) -> bool:
        return bool(ctypes.windll.kernel32.AssignProcessToJobObject(h_job, h_proc))

    def _win_close_handle(h) -> None:
        if h:
            ctypes.windll.kernel32.CloseHandle(h)

else:  # pragma: no cover - 非 Windows 无 Job 路径
    _win_create_job = _win_open_process = _win_assign_job = _win_close_handle = None


@dataclass
class _RunResult:
    """子进程运行结果（plain 与 Job 路径共用）。"""
    stdout: str = ""
    stderr: str = ""
    returncode: int = -1
    timed_out: bool = False
    note_extra: str = ""


def _run_plain(wrapper_path: str, cwd: str) -> _RunResult:
    """默认路径：subprocess.run + timeout（Windows 唯一可靠 kill）。"""
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-E", "-S", "main.py"],
            cwd=cwd, input="",
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=config.code_exec.timeout,
        )
        return _RunResult(proc.stdout or "", proc.stderr or "", proc.returncode, False, "")
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else ""
        err = e.stderr if isinstance(e.stderr, str) else ""
        return _RunResult(out, err, -1, True, "")


def _run_with_job(wrapper_path: str, cwd: str) -> _RunResult:
    """Job 增强路径：Popen + AssignProcessToJobObject + KILL_ON_JOB_CLOSE；创建失败降级 plain。"""
    h_job = _win_create_job()
    if h_job is None:
        return _run_plain(wrapper_path, cwd)  # 降级，不抛

    proc = None
    try:
        proc = subprocess.Popen(
            [sys.executable, "-I", "-E", "-S", "main.py"],
            cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8", errors="replace",
        )
        h_proc = _win_open_process(
            proc.pid, _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_INFORMATION,
        )
        if h_proc:
            try:
                _win_assign_job(h_job, h_proc)
            finally:
                _win_close_handle(h_proc)
        try:
            out, err = proc.communicate(timeout=config.code_exec.timeout)
            return _RunResult(out or "", err or "", proc.returncode, False, "")
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                out, err = proc.communicate(timeout=2)
            except Exception:  # noqa: BLE001
                out, err = "", ""
            return _RunResult(out or "", err or "", proc.returncode or -1, True, "")
    except Exception as e:  # noqa: BLE001 - Job 路径异常 → 降级 plain
        return _RunResult("", f"job path error: {type(e).__name__}: {e}", -1, False, "")
    finally:
        # KILL_ON_JOB_CLOSE：句柄关闭即终止 job 内全部进程（防 ctypes 逃逸孙进程）
        _win_close_handle(h_job)
        if proc is not None and proc.poll() is None:
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass


def exec_code(code: str, query: str = "", params: str = "") -> CodeExecOutput:
    """在受限子进程中执行用户代码，返回截断输出 + 失败 note（不吞，Q4）。

    参数：
        code   — 用户 Python 代码（由 LLM/上层生成）
        query  — 触发查询（进 hash 与审计元数据）
        params — 可选执行参数（进 hash；"脚本模板+参数注入"模式下防止同脚本不同参数误去重）
    """
    start = time.monotonic()
    ctx = f"{code}\x00{params}\x00{query}"
    script_hash = hashlib.sha256(ctx.encode("utf-8")).hexdigest()[:10]  # Q6 单 hash

    # ---- 第二层：AST import 白名单 ----
    denied = _check_imports(code)
    if denied:
        return CodeExecOutput(
            ok=False, note=f"导入被沙箱拒绝: {', '.join(denied)}",
            elapsed=time.monotonic() - start, script_hash=script_hash,
        )

    wrapper = _build_wrapper(code)
    tmp_dir = ""
    with _semaphore:  # Q2：并发信号量（默认 2）
        try:
            tmp_dir = tempfile.mkdtemp(prefix="dr_sandbox_")
            wrapper_path = os.path.join(tmp_dir, "main.py")
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(wrapper)

            # Q2 主线 + W6 Job 增强：默认走 plain（subprocess -I -E -S + timeout）；
            # Windows 且 config.code_exec.use_job_object 时走 _run_with_job
            # （Popen + AssignProcessToJobObject + KILL_ON_JOB_CLOSE，防 ctypes 逃逸进程）。
            use_job = (sys.platform == "win32" and config.code_exec.use_job_object)
            if use_job:
                r = _run_with_job(wrapper_path, tmp_dir)
            else:
                r = _run_plain(wrapper_path, tmp_dir)
            out = _truncate(r.stdout)
            err = _truncate(r.stderr)
            if r.timed_out:
                ok = False
                note = f"执行超时（>{config.code_exec.timeout}s），已终止"
            elif r.returncode == 0 and not err:
                ok = True
                note = ""
            else:
                ok = False
                note = (err or out)[:500] or f"exit code {r.returncode}"
            meta = {"exit_code": r.returncode}
            if use_job:
                meta["job"] = "enabled"
            return CodeExecOutput(
                ok=ok, stdout=out, stderr=err, note=note,
                elapsed=time.monotonic() - start, script_hash=script_hash,
                metadata=meta,
            )
        except Exception as e:  # noqa: BLE001 — 失败回传不吞（Q2/Q4）
            return CodeExecOutput(
                ok=False, note=f"sandbox 异常: {type(e).__name__}: {e}",
                elapsed=time.monotonic() - start, script_hash=script_hash,
            )
        finally:
            # 临时 cwd 清理（尽力而为，防残留）
            if tmp_dir:
                try:
                    import shutil
                    shutil.rmtree(tmp_dir, ignore_errors=True)
                except Exception:  # noqa: BLE001
                    pass