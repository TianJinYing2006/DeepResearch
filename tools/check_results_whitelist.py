"""Arm 7「轨道 1」白名单裁判：**白名单集合 ≡ git 跟踪集合，且每条目都带引用出处**。

为什么单写这个脚本：
`.gitignore` 对【已跟踪】文件无效 —— 就算规则写得再对，历史误入库的内容也不会自动消失，
两者的偏离会静默积累（2026-09-14 实测偏了 94 项）。所以规则写了不算数，
必须有一个「唯一的裁判」持续比对：偏离即失败。

判据（Arm 7 Grill Q1 拍板）：**引用即入库**
  ① 每条例外都必须在【紧邻上方】的注释行或 `# keep:` 行尾写明「被谁引用 / 谁读写」；
  ② 证明的形式是：引用到某个 `docs/**.md`，或写明代码读写方（形如「读写方：xxx.py::func()」）；
  ③ 既没被引用、又不是流水线常驻文件的 —— 不准留在跟踪里（`git rm --cached`，磁盘文件保留）。

⚠️ 附带锁死一个坑：`.gitignore` 只认【行首】的 `#`。`!path/  # 注释` 会把注释吞进 pattern，
`!` 规则整条失效（2026-09-18 实测）⇒ 本脚本对任何含 `#` 的 `!` 规则直接判失败。

用法：
    python tools/check_results_whitelist.py          # 违规则以 exit 1 退出
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

RESULTS_REL = "research_engine/eval/results"

# `# keep: <相对路径> —— <证明>`
KEEP_RE = re.compile(r"^#\s*keep:\s*(?P<path>\S+)\s*——\s*(?P<proof>.+?)\s*$")
# 证明的形式约束：引用到 docs 下的正式文档，或写明代码读写方。
# 用 `\w` 而非 `[A-Za-z0-9_]`，否则中文文件名的文档引用匹配不到（会被误判成「没写出处」）。
DOC_REF_RE = re.compile(r"docs/[\w./\-]+\.md")
CODE_REF_MARK = "读写方："

# ⚠️ 自动枚举文档：由流水线/脚本自动写出，会把【所有】run 无差别地列进表格。
# 实测：`docs/eval-report.md` 含 74 个互不相同的 run id，而手写结论文档只提了 4 个 ——
# 出现在它们里面不构成「有人挑选过这个 run」的证据。若允许它们当证明，判据自我作废。
# （该文件自己的头部也写明：自动生成、每次运行会被覆盖、「不要依赖本文件」）
AUTO_ENUMERATED_DOCS = {"docs/eval-report.md"}
def parse_gitignore(text: str) -> tuple[list[dict], list[str]]:
    """从 .gitignore 文本里解析白名单条目。返回 (条目列表, 静态违规列表)。"""
    entries: list[dict] = []
    violations: list[str] = []
    pending_comments: list[str] = []

    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        stripped = line.strip()

        if not stripped:
            pending_comments = []  # 空行打断注释归属，避免跨段误挂
            continue

        if stripped.startswith("#"):
            m = KEEP_RE.match(stripped)
            if m:
                entries.append(
                    {
                        "path": m.group("path"),
                        "proof": m.group("proof"),
                        "line": lineno,
                        "kind": "keep",
                    }
                )
                pending_comments = []
            else:
                pending_comments.append(stripped.lstrip("#").strip())
            continue

        if stripped.startswith("!"):
            path = stripped[1:].strip()
            if "#" in path:
                violations.append(
                    f"行 {lineno}：`!` 规则行内含 `#`（{path!r}）—— "
                    ".gitignore 只认行首注释，行尾文字会被当成 pattern 的一部分导致规则失效；"
                    "出处必须独立成注释行写在它的上方"
                )
            if not path.startswith(RESULTS_REL):
                violations.append(f"行 {lineno}：白名单路径不在 {RESULTS_REL}/ 下（{path!r}）")
            entries.append(
                {
                    "path": path,
                    "proof": " ".join(pending_comments).strip(),
                    "line": lineno,
                    "kind": "bang",
                }
            )
            pending_comments = []
            continue

        pending_comments = []  # 普通忽略规则：重置归属

    return entries, violations


def top_level(rel_path: str) -> str:
    """把 results 下的相对路径收敛到顶层条目名（目录名或文件名）。"""
    tail = rel_path[len(RESULTS_REL) :].strip("/") if rel_path.startswith(RESULTS_REL) else rel_path
    return tail.split("/", 1)[0]


def tracked_top_levels(repo_root: Path) -> list[str]:
    """取 Git 实际跟踪的顶层条目集合（= 真实入库真相）。"""
    out = subprocess.run(
        ["git", "ls-files", RESULTS_REL],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return sorted({top_level(p.strip()) for p in out.splitlines() if p.strip()})


def check_repo(repo_root: Path) -> list[str]:
    """全量检查，返回违规列表（空 = 通过）。"""
    gitignore = repo_root / ".gitignore"
    if not gitignore.exists():
        return [f"缺少 {gitignore}"]

    entries, violations = parse_gitignore(gitignore.read_text(encoding="utf-8"))

    for e in entries:
        if not e["proof"]:
            violations.append(f"行 {e['line']}：白名单 {e['path']} 没有任何出处说明")
            continue
        if not DOC_REF_RE.search(e["proof"]) and CODE_REF_MARK not in e["proof"]:
            violations.append(
                f"行 {e['line']}：{e['path']} 的出处不满足「引用即入库」—— "
                f"需写出被引用的 `docs/**.md`，或写明「{CODE_REF_MARK}xxx.py::func()」"
            )
        cited = set(DOC_REF_RE.findall(e["proof"]))
        manual = cited - AUTO_ENUMERATED_DOCS
        if cited and not manual and CODE_REF_MARK not in e["proof"]:
            violations.append(
                f"行 {e['line']}：{e['path']} 只出现在自动枚举文档 {sorted(cited)} 里 —— "
                "那张表会把所有 run 都列一遍，不构成「有人挑选过它」的证据，需补手写文档的引用"
            )
        for ref in cited:
            if not (repo_root / ref).exists():
                violations.append(f"行 {e['line']}：{e['path']} 引用的文档不存在（{ref}）")
        if not (repo_root / e["path"].rstrip("/")).exists():
            violations.append(f"行 {e['line']}：白名单路径在磁盘上不存在（{e['path']}）")

    expected = sorted({top_level(e["path"]) for e in entries})
    actual = tracked_top_levels(repo_root)

    for name in actual:
        if name not in expected:
            violations.append(
                f"跟踪了但不在白名单：{RESULTS_REL}/{name} —— "
                "补齐外链出处或执行 `git rm --cached -r`（磁盘文件保留）"
            )
    for name in expected:
        if name not in actual:
            violations.append(
                f"白名单了但没跟踪：{RESULTS_REL}/{name} —— 规则与实际不一致，删规则或补入库"
            )

    return violations


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    violations = check_repo(repo_root)

    entries, _ = parse_gitignore((repo_root / ".gitignore").read_text(encoding="utf-8"))
    actual = tracked_top_levels(repo_root)

    print(f"白名单条目 {len(entries)} 条 / git 跟踪顶层条目 {len(actual)} 个：")
    for name in actual:
        kind = next((e["kind"] for e in entries if top_level(e["path"]) == name), "?")
        print(f"  - {name:32s} [{kind}]")

    if violations:
        print(f"\n[FAIL] 白名单纪律违规 {len(violations)} 处：", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1

    print("\n[PASS] 白名单集合 ≡ git 跟踪集合，且每条都写明了引用出处")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
