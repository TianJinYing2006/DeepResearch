"""前端 UI 反模式守卫：用 impeccable 的确定性检测器拦「AI 味」回归。

为什么单写这个脚本：
`docs/web-frontend-audit.md` 自陈审计边界是「**静态源码审计，未做浏览器渲染实测**」，
而 R7 收口唯一未做项是**截图基线**（`docs/web-frontend-redesign-directions.md` 第 3 行记明
理由是「中文字体/渲染跨环境不稳定，另行提案」）。两者合起来 == 前端视觉质量**没有回归防线**：
R1~R7 把 79 项发现改完了，但改完之后**没有任何机器校验阻止新代码把它们带回来**。

本脚本补的正是这一格。它调 `impeccable detect`（`pbakaus/impeccable`，Apache-2.0）——
59 条**确定性**规则，**不需要 LLM、不需要 API key**：

- 为什么不是截图基线：确定性规则判定的是代码与渲染属性，**根本不碰像素** ⇒
  当初放弃截图基线的理由（跨环境中文字体渲染不稳定）对它不成立。
- 为什么不是再写一份 LLM 审计：那是一次性人工活动（产出是 79 项发现的报告），
  无法在每次 PR 上重跑；CI 需要的是可复现、零成本、二值判定的尺子。

判据（三类，任一即违规）：
A. 检测器报告任何**主要发现**（`severity` 非 advisory）⇒ 违规，逐条打印 `文件:行 规则 片段`；
B. 检测器**跑不起来**（没安装 / 找不到 node / 执行失败）⇒ 违规，**绝不放行**；
C. 输出**不是**合法 JSON 数组 ⇒ 违规（防止工具改版后静默变成空转）。

⚠️ 关于 B/C 的刻意选择：本脚本**不会**在工具缺失时静默通过。
`tools/check_results_whitelist.py` 的教训是「单点裁判必须经变异测试验证非空转」——
一个「工具没装就 exit 0」的守卫，会在 CI 配置漂移时变成一个永远绿的摆设。

误报治理：不在这里维护豁免名单（那会变成第二份真相源）。用检测器自带的机制 ——
    npx impeccable ignores add-value <rule> <value> --file <glob> --reason "<理由>"
写入 `.impeccable/config.json`（共享）或 `.impeccable/config.local.json`（本地）。
`--no-config` 可临时绕过全部项目配置。

用法：
    python tools/check_ui_patterns.py        # 有发现或检测器不可用 ⇒ exit 1
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]

# 扫描目标：手写源码目录（与 check_tailwind_tokens.py / check_frontend_boundary.py 同口径）。
TARGET_REL = "web/frontend/src"

# 检测器 CLI 的候选位置，按优先级。用 `node <cli.js>` 直调而不是 `.bin` 垫片：
# `.bin/impeccable` 在 Windows 上是 .cmd / .ps1，跨平台 subprocess 调用不可移植。
CLI_JS_REL = "web/frontend/node_modules/impeccable/cli/bin/cli.js"

# 子进程超时：扫描是纯本地解析，正常在数秒内；给足余量但不要挂死 CI。
TIMEOUT_S = 300


def resolve_cli(repo_root: Path) -> list[str] | None:
    """返回调用检测器的命令前缀；找不到则返回 None。"""
    cli_js = repo_root / CLI_JS_REL
    if cli_js.is_file():
        node = shutil.which("node")
        if node:
            return [node, str(cli_js)]
        # node 不在 PATH：退到 `.bin` 垫片（POSIX）或 .cmd（Windows）
    for name in ("impeccable.cmd", "impeccable"):
        shim = repo_root / "web" / "frontend" / "node_modules" / ".bin" / name
        if shim.is_file():
            return [str(shim)]
    which = shutil.which("impeccable")
    if which:
        return [which]
    return None


def run_detect(cli: list[str], target: Path, cwd: Path) -> tuple[int | None, str, str]:
    """跑一次检测。返回 (exit_code, stdout, stderr)；执行异常时 exit_code 为 None。"""
    try:
        proc = subprocess.run(
            [*cli, "detect", "--json", str(target)],
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_S,
        )
    except (OSError, subprocess.SubprocessError) as exc:  # 找不到 node / 超时 / 权限
        return None, "", f"{type(exc).__name__}: {exc}"
    return proc.returncode, proc.stdout, proc.stderr


def rel_path(raw: str, repo_root: Path) -> str:
    """把检测器给出的路径收敛成仓库相对路径（正斜杠），保证跨平台消息一致。"""
    try:
        return Path(raw).resolve().relative_to(repo_root.resolve()).as_posix()
    except (ValueError, OSError):
        return raw.replace("\\", "/")


def parse_findings(stdout: str) -> tuple[list[dict] | None, str]:
    """解析 `--json` 输出。返回 (发现列表, 错误说明)；解析失败时列表为 None。"""
    text = stdout.strip()
    if not text:
        return None, "检测器没有输出任何内容（预期是 JSON 数组）"
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, f"输出不是合法 JSON：{exc}"
    if not isinstance(data, list):
        return None, f"输出不是 JSON 数组，而是 {type(data).__name__}"
    return [d for d in data if isinstance(d, dict)], ""


def check_repo(repo_root: Path, cli: list[str] | None = None, target: Path | None = None) -> list[str]:
    """全量检查，返回违规列表（空 = 通过）。纯函数，便于注入假仓库与假 CLI 测试。"""
    scan_target = target if target is not None else repo_root / TARGET_REL
    if not scan_target.exists():
        return [f"扫描目标不存在：{rel_path(str(scan_target), repo_root)}"]

    command = cli if cli is not None else resolve_cli(repo_root)
    if not command:
        return [
            "找不到 impeccable 检测器 —— 期望 "
            f"{CLI_JS_REL} 或 PATH 上的 `impeccable`。"
            "先在 web/frontend 下跑 `npm ci`（该工具是 devDependency）。"
            "⚠️ 刻意不放行：工具缺失时静默通过会让本守卫变成永远绿的摆设。"
        ]

    code, stdout, stderr = run_detect(command, scan_target, repo_root)
    if code is None:
        return [f"检测器执行失败（无法启动或超时 {TIMEOUT_S}s）：{stderr.strip()}"]

    # 非零退出且 stdout 全空 ⇒ 是「跑失败」而不是「输出变形」，两者要分开报，
    # 否则排障时会去查 JSON 解析，而真正的原因在 stderr（实测：假 CLI 崩溃即此形态）。
    if not stdout.strip() and code != 0:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else "(stderr 为空)"
        return [f"检测器执行失败（exit={code}）：{detail}"]

    findings, err = parse_findings(stdout)
    if findings is None:
        detail = stderr.strip().splitlines()[-1] if stderr.strip() else "(stderr 为空)"
        return [f"检测器输出无法解析：{err}；exit={code}；stderr 末行：{detail}"]

    violations: list[str] = []
    for item in findings:
        rule = item.get("antipattern") or item.get("name") or "<未知规则>"
        name = item.get("name") or rule
        severity = item.get("severity") or "?"
        loc = f"{rel_path(str(item.get('file', '?')), repo_root)}:{item.get('line', '?')}"
        snippet = item.get("snippet") or ""
        violations.append(f"{loc}  [{rule}/{severity}] {name} — 命中 `{snippet}`")
    return violations


def main() -> int:
    violations = check_repo(REPO)
    if violations:
        print("[guard] 违规：前端存在 UI 反模式，或检测器不可用（impeccable detect）", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            f"[guard] 共 {len(violations)} 项，exit 1；"
            "确属误报请用 `npx impeccable ignores add-value <rule> <value> --file <glob> --reason \"<理由>\"`",
            file=sys.stderr,
        )
        return 1
    print("[guard] OK：impeccable detect 未报告任何 UI 反模式")
    return 0


if __name__ == "__main__":
    sys.exit(main())
