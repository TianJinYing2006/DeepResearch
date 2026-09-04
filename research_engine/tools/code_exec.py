# -*- coding: utf-8 -*-
"""代码执行工具（W4 Q2 定案：三层纵深 sandbox = B++ 修正版）。

安全模型（设计详见 docs/requirements/4-tools-and-strategic-model.md §5）：
- 主路径：subprocess `python -I -E -S`（-I 隔离用户环境 / -E 忽略环境变量防密钥泄漏 /
  -S 不加载 site-packages = 天然白名单）+ 独立临时 cwd + wall-clock timeout + 输出截断
- 第二层：AST import 白名单（9 个纯 stdlib；可被动态 import 绕过 → 第三层兜底）
- 第三层：PEP 578 Audit Hook（包装脚本内 sys.addaudithook，运行时拦截）——
  破坏/网络/进程类事件无条件拒绝；open 例外走 cwd 白名单路径判断（避免误伤包装脚本自身读写）
- Windows 现实：resource 模块 Unix-only → timeout 是唯一可靠 kill；Job Object 为可选增强
  （CODE_EXEC_USE_JOB=1，进程树级内存/CPU 配额），**本期留桩：默认关，开源前必须验证**（DoD）。

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
    proc = None
    with _semaphore:  # Q2：并发信号量（默认 2）
        try:
            tmp_dir = tempfile.mkdtemp(prefix="dr_sandbox_")
            wrapper_path = os.path.join(tmp_dir, "main.py")
            with open(wrapper_path, "w", encoding="utf-8") as f:
                f.write(wrapper)

            # Q2 主线：subprocess -I -E -S + timeout。Job Object 增强（Windows，默认关）：
            # CODE_EXEC_USE_JOB=1 时此处应附加 AssignProcessToJobObject（ctypes，进程树级
            # 内存/CPU 配额）；**本期留桩，开源前（W6）必须实现并配 Windows 单测（DoD §7）**，
            # 此前一律走 timeout 兜底——功能不受影响，边界声明确认 AST+Audit+timeout 三层。
            proc = subprocess.run(
                [sys.executable, "-I", "-E", "-S", "main.py"],
                cwd=tmp_dir,
                input="",  # 无 stdin（用户代码通过 main.py 文件传入）
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=config.code_exec.timeout,
            )
            out = _truncate(proc.stdout)
            err = _truncate(proc.stderr)
            if proc.returncode == 0 and not err:
                ok = True
                note = ""
            else:
                ok = False
                note = (err or out)[:500] or f"exit code {proc.returncode}"
            return CodeExecOutput(
                ok=ok, stdout=out, stderr=err, note=note,
                elapsed=time.monotonic() - start, script_hash=script_hash,
                metadata={"exit_code": proc.returncode} if proc is not None else {},
            )
        except subprocess.TimeoutExpired:
            return CodeExecOutput(
                ok=False, note=f"执行超时（>{config.code_exec.timeout}s），已终止",
                elapsed=time.monotonic() - start, script_hash=script_hash,
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