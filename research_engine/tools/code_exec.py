"""代码执行工具（W4 Q2 定案：三层纵深 sandbox = B++ 修正版）。

安全模型（设计详见 docs/requirements/4-tools-and-strategic-model.md §5；2026-11 审计 #327 加固）：

- 主路径：subprocess `python -I -E -S`（-I 隔离用户环境 / -S 不加载 site-packages = 天然依赖白名单）
  + **最小环境变量白名单**（只传解释器启动必需项，父进程密钥/业务变量不再继承）
  + 独立临时 cwd + wall-clock timeout + **有界输出读取**（单流超限即截断并 kill，父进程内存有界）；
- 第二层：AST import 白名单（9 个纯 stdlib）；动态 `__import__` 由第三层 import 事件拦截；
- 第三层：PEP 578 Audit Hook（包装脚本内 sys.addaudithook，运行时拦截）——
  网络 / 进程 / 破坏类事件按**真实事件名 + 前缀**拒绝；`import` 拒绝危险 C 扩展；
  `exec`/`compile` 拒绝；`open` 按**构建期固定的沙箱根**判包含（读也拒）+ `os.chdir` 拒绝；
- Windows 现实：resource 模块 Unix-only → timeout 是唯一可靠 kill；Job Object 为可选增强
  （CODE_EXEC_USE_JOB=1，进程树级 KILL_ON_JOB_CLOSE——超时关闭 job 句柄即全树终止，防 ctypes 逃逸进程）。
  实现（W6）：Popen 正常启动 + 立即 AssignProcessToJobObject（CREATE_SUSPENDED 因 Popen._thread 缺失不可行，
  竞态窗口 = 解释器启动前微秒级，audit hook 已拦 Python 层 fork）；Job 创建失败安全降级 plain。
  仍存在的限制（预加载 C 模块 / 非 OS 级隔离 / 纯 Python 模块不可用）见 README §3.2。

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
# 运行时无条件拒绝的 audit 事件（PEP 578 **真实事件名**；2026-11 审计 #327：
# 旧列表里的 "socket.socket" 不是真实事件名（真实为 socket.__new__/socket.connect 等），
# 网络拒绝实际未生效 —— 现改为前缀匹配 + 真实事件名，前缀族见 AUDIT_DENY_PREFIXES）
AUDIT_DENY_EVENTS = (
    "os.system", "os.chdir", "os.startfile", "os.kill", "os.fork",
    "shutil.rmtree", "shutil.unpack_archive",
    "os.rename", "os.replace", "os.remove", "os.unlink", "os.rmdir",
    "os.mkdir", "os.makedirs", "os.symlink", "os.link",
)
# 前缀匹配（PEP 578 事件按族命名，逐条枚举易漏）：网络 / 进程创建 / 执行族。
AUDIT_DENY_PREFIXES = (
    "socket.",          # socket.__new__ / socket.connect / socket.getaddrinfo / socket.bind ...
    "subprocess.",      # subprocess.Popen / subprocess.run ...
    "os.exec",          # os.exec / os.execv / os.execve ...
    "os.spawn",         # os.spawn / os.spawnv / os.spawnve ...
    "os.posix_spawn",   # Unix：os.posix_spawn
    "pty.",             # pty.spawn / pty.openpty ...
)
# 动态 import 拦截（`__import__` 绕过 AST 白名单）：顶层模块名黑名单。
# AST 静态 import 由 _check_imports 拦截；这里封 `__import__`/`importlib` 路径。
# 只列"能直接触达进程/网络/系统调用"的模块；纯 Python 包的 .py 读取已被 open 判据挡在沙箱外。
DENY_IMPORT_ROOTS: Set[str] = {
    "_socket", "_ssl", "_ctypes", "_winapi", "_subprocess", "_posixsubprocess",
    "_multiprocessing", "_overlapped",
    "ctypes", "multiprocessing", "socket", "ssl", "subprocess", "asyncio",
}
# 子进程环境变量白名单（最小集）：解释器启动/临时目录所需；父进程密钥与业务变量一律不传。
# 2026-11 审计 #327：`-E` 只忽略 PYTHON*，旧实现子进程继承父进程完整 env（DASHSCOPE_API_KEY 可读）。
_SANDBOX_ENV_KEYS = (
    "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "COMSPEC", "PATHEXT",
    "NUMBER_OF_PROCESSORS", "PROCESSOR_ARCHITECTURE",
)
# 输出上限（各流 128KB；超限即截断并 kill，防父进程内存被失控输出撑爆）
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

def _build_wrapper(user_code: str, sandbox_root: str) -> str:
    """生成包装脚本：PEP 578 Audit Hook（第三层）+ 用户代码。

    2026-11 审计 #327 修复：
    - deny 改为**真实事件名 + 前缀匹配**（旧列表的 ``socket.socket`` 不存在）；
    - ``import`` 事件拒绝 ``_socket``/``_ctypes`` 等危险 C 扩展（`__import__` 动态路径）；
    - 拒绝 ``exec``/``compile``（用户代码不能再动态执行字符串/代码对象）；
    - ``open`` 判据用**构建期固定的沙箱根**（不再用可变的 ``os.getcwd()``），并拒绝 ``os.chdir``；
    - hook 闭包捕获判据所需常量，用户代码无法通过重绑定模块全局名绕过。
    """
    deny_events = ", ".join(repr(e) for e in AUDIT_DENY_EVENTS)
    deny_prefixes = ", ".join(repr(e) for e in AUDIT_DENY_PREFIXES)
    deny_imports = ", ".join(repr(e) for e in sorted(DENY_IMPORT_ROOTS))
    return f'''# -*- coding: utf-8 -*-
"""Sandbox wrapper: PEP 578 audit hook (layer 3) + user code."""
import os as _dr_os
import sys as _dr_sys

_DR_SANDBOX_ROOT = {sandbox_root!r}
_DR_DENY_EVENTS = ({deny_events},)
_DR_DENY_PREFIXES = ({deny_prefixes},)
_DR_DENY_IMPORTS = frozenset([{deny_imports}])


def _dr_install_audit_hook():
    # 闭包捕获判据（用户代码与包装脚本共享模块 globals，全局名可被重绑定）
    _os = _dr_os
    _root = _DR_SANDBOX_ROOT
    _deny_events = _DR_DENY_EVENTS
    _deny_prefixes = _DR_DENY_PREFIXES
    _deny_imports = _DR_DENY_IMPORTS

    def _deny(reason):
        raise PermissionError("operation not allowed by sandbox: " + reason)

    def _audit(event, args):
        if event in _deny_events:
            _deny(event)
        for prefix in _deny_prefixes:
            if event.startswith(prefix):
                _deny(event)
        if event == "import":
            name = str(args[0]) if args else ""
            if name.split(".")[0] in _deny_imports:
                _deny("import " + name)
            return
        if event in ("exec", "compile"):
            _deny(event)
        if event == "open":
            # open：任何沙箱根外的 open 都拒绝（读也拒：防偷读 .env/密钥）。
            # builtin open / io.open / os.open / io.FileIO 都会触发该事件。
            if not args:
                _deny("open without path")
            path = args[0]
            if isinstance(path, int):
                _deny("open by file descriptor")  # 绕过路径判据的 fd 形态
            if isinstance(path, bytes):
                path = path.decode("utf-8", errors="surrogateescape")
            try:
                abs_path = _os.path.abspath(_os.fspath(path))
            except TypeError:
                _deny("open path type not allowed")
            try:
                inside = _os.path.commonpath([abs_path, _root]) == _root
            except ValueError:  # 跨盘符（Windows 不同 drive）commonpath 抛异常
                inside = False
            if not inside:
                raise PermissionError("open outside sandbox cwd: " + abs_path)

    _dr_sys.addaudithook(_audit)


_dr_install_audit_hook()
del _dr_install_audit_hook

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
    truncated: bool = False
    job_enabled: bool = False


def _sandbox_env(sandbox_dir: str) -> dict:
    """子进程**最小环境**：只保留解释器启动必需项 + 临时目录。

    2026-11 审计 #327：`-E` 只忽略 `PYTHON*`，旧实现让子进程继承父进程完整 env
    ⇒ 沙箱内可读出 `DASHSCOPE_API_KEY` 等密钥。这里改为白名单构造，
    `TEMP/TMP/TMPDIR` 指向沙箱目录（沙箱外路径的 open 本就会被拒绝）。
    """
    env = {key: os.environ[key] for key in _SANDBOX_ENV_KEYS if os.environ.get(key)}
    env["TEMP"] = sandbox_dir
    env["TMP"] = sandbox_dir
    env["TMPDIR"] = sandbox_dir
    return env


def _pump(stream, buf: bytearray, cap: int, on_overflow) -> None:
    """逐块读取子进程管道：累计超过 cap 即回调（杀进程），父进程内存 ≤ cap。"""
    try:
        while True:
            try:
                chunk = stream.read1(8192)  # BufferedReader：单次系统调用，不阻塞等满
            except AttributeError:  # pragma: no cover - 非 BufferedReader 兜底
                chunk = stream.read(8192)
            if not chunk:
                return
            remain = cap - len(buf)
            if remain > 0:
                buf.extend(chunk[:remain])
            if len(chunk) > remain:
                on_overflow()
    except Exception:  # noqa: BLE001 - 进程被杀后管道读取可能抛错，属预期
        return
    finally:
        try:
            stream.close()
        except Exception:  # noqa: BLE001
            pass


def _run_process(cwd: str, *, timeout: int, use_job: bool) -> _RunResult:
    """执行包装脚本：最小环境 + 有界输出读取（超限即杀）+ wall-clock timeout（+ 可选 Job）。

    W8 Arm 2：命令行加 `-X utf8`（父进程按 utf-8 解码管道，而子进程在 Windows 上默认按
    cp936 写管道 ⇒ 非 ASCII 输出必然乱码；`-E` 忽略环境变量，故只能走 `-X` 命令行开关）。

    2026-11 审计 #327：输出由 `subprocess.run(capture_output=True)` 整段读入父进程内存
    改为 `Popen` + 逐块读取；单流超过 `config.code_exec.max_output_bytes` 即截断并 kill，
    防失控代码在 15s 内撑爆 API 进程内存。
    """
    cap = max(1, int(getattr(config.code_exec, "max_output_bytes", MAX_OUTPUT) or MAX_OUTPUT))
    env = _sandbox_env(cwd)
    proc = subprocess.Popen(
        [sys.executable, "-I", "-E", "-S", "-X", "utf8", "main.py"],
        cwd=cwd, stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=env,
    )

    h_job = None
    job_enabled = False
    if use_job:
        h_job = _win_create_job()
        if h_job is not None:
            h_proc = _win_open_process(
                proc.pid,
                _PROCESS_SET_QUOTA | _PROCESS_TERMINATE | _PROCESS_QUERY_INFORMATION,
            )
            if h_proc:
                try:
                    job_enabled = bool(_win_assign_job(h_job, h_proc))
                finally:
                    _win_close_handle(h_proc)
            if not job_enabled:
                _win_close_handle(h_job)
                h_job = None

    out_buf: bytearray = bytearray()
    err_buf: bytearray = bytearray()
    truncated = {"stdout": False, "stderr": False}
    killed = threading.Event()

    def _kill() -> None:
        if killed.is_set():
            return
        killed.set()
        try:
            proc.kill()
        except Exception:  # noqa: BLE001
            pass

    def _overflow_cb(key: str):
        def _cb() -> None:
            truncated[key] = True
            _kill()
        return _cb

    t_out = threading.Thread(
        target=_pump, args=(proc.stdout, out_buf, cap, _overflow_cb("stdout")), daemon=True)
    t_err = threading.Thread(
        target=_pump, args=(proc.stderr, err_buf, cap, _overflow_cb("stderr")), daemon=True)
    t_out.start()
    t_err.start()

    timed_out = False
    try:
        try:
            proc.wait(timeout=max(1, int(timeout)))
        except subprocess.TimeoutExpired:
            timed_out = True
            _kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:  # pragma: no cover - 极端兜底
                pass
    finally:
        t_out.join(timeout=5)
        t_err.join(timeout=5)
        # KILL_ON_JOB_CLOSE：关闭句柄即终止 job 内全部进程（防逃逸孙进程）
        _win_close_handle(h_job)
        if proc.poll() is None:  # pragma: no cover - 兜底
            _kill()
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                pass

    stdout = bytes(out_buf).decode("utf-8", errors="replace")
    stderr = bytes(err_buf).decode("utf-8", errors="replace")
    if truncated["stdout"]:
        stdout += _TRUNC_MARK
    if truncated["stderr"]:
        stderr += _TRUNC_MARK
    return _RunResult(
        stdout=stdout, stderr=stderr,
        returncode=proc.returncode if proc.returncode is not None else -1,
        timed_out=timed_out,
        truncated=bool(truncated["stdout"] or truncated["stderr"]),
        job_enabled=job_enabled,
    )

def exec_code(code: str, query: str = "", params: str = "") -> CodeExecOutput:
    """在受限子进程中执行用户代码，返回截断输出 + 失败 note（不吞，Q4）。

    参数：
        code   — 用户 Python 代码（由 LLM/上层生成）
        query  — 触发查询（进 hash 与审计元数据）
        params — 可选执行参数（进 hash；"脚本模板+参数注入"模式下防止同脚本不同参数误去重）

    W8 Arm 2（§5.2.2）：`query` 现同时写入 `metadata["query"]`。
    这是**审计侧的关联通道** —— 调用方拿到的产出带着「是哪次查询触发的」，
    从而不必再把 query 文本拼进被执行的脚本源码（见 `researcher._default_code_script`）。
    `query` 进 hash 的既有行为**保持不变**：`dedupe()` / `compress()` 按 `f.source` 归并，
    去掉它会让同题多条 code 证据的 hash 塌缩成常量（§5.2 预检第 3 条，实测占基线 24.3%）。
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

    tmp_dir = ""
    with _semaphore:  # Q2：并发信号量（默认 2）
        try:
            tmp_dir = tempfile.mkdtemp(prefix="dr_sandbox_")
            # 沙箱根在构建期固定（realpath）；hook 不再用可变的 os.getcwd() 判据。
            sandbox_root = os.path.realpath(tmp_dir)
            wrapper_path = os.path.join(tmp_dir, "main.py")
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(_build_wrapper(code, sandbox_root))

            # Q2 主线 + W6 Job 增强：默认走 plain（subprocess -I -E -S + timeout + 最小 env）；
            # Windows 且 config.code_exec.use_job_object 时额外 AssignProcessToJobObject
            # （KILL_ON_JOB_CLOSE，防 ctypes 逃逸进程）。
            use_job = (sys.platform == "win32" and config.code_exec.use_job_object)
            r = _run_process(tmp_dir, timeout=config.code_exec.timeout, use_job=use_job)
            out = r.stdout
            err = r.stderr
            if r.timed_out:
                ok = False
                note = f"执行超时（>{config.code_exec.timeout}s），已终止"
            elif r.returncode == 0 and not err:
                ok = True
                note = ""
            else:
                ok = False
                note = (err or out)[:500] or f"exit code {r.returncode}"
            if r.truncated:
                note = f"{note}（输出超过上限已截断并终止）" if note else "输出超过上限已截断并终止"
            meta = {"exit_code": r.returncode, "query": query}
            if r.job_enabled:
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