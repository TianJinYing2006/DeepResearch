"""W9 边界守卫（D-20 三条硬边界 ①）：前端目录不得 import `research_engine`。

为什么单写这个脚本：
前端（`web/frontend/`）只负责呈现与提交配置，研究判定逻辑必须留在后端
（`web/backend/` 可 import `research_engine`）。一旦前端偷偷 import `research_engine`，
就等于把核心链路逻辑搬到了呈现层 —— 既破坏 ADR-0001 §1.1 的边界，
也让前端强耦合 Python 后端、失去「换前端后端一行不改」的免费期权（D-20 选型的核心收益）。

判据：扫描 `web/frontend/` 下所有手写源码（`.ts/.tsx/.js/.jsx/.mjs/.cjs`，
排除 `node_modules/dist/.vite`），凡出现 `research_engine` 字面量即判违规。
刻意只查字面量（不查 AST）：合法的前端代码根本不该出现这四个字符，
字面量匹配足够、且零依赖、可离线跑。

用法：
    python tools/check_frontend_boundary.py      # 违规则以 exit 1 退出
"""
from __future__ import annotations

import sys
from pathlib import Path

FRONTEND_REL = "web/frontend"
# 这些目录是构建产物或第三方依赖，不承载手写源码 ⇒ 跳过
SKIP_DIRS = {"node_modules", "dist", ".vite", ".git"}
SRC_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"}
FORBIDDEN = "research_engine"


def scan(root: Path) -> list[Path]:
    """返回所有含违禁字面量的源码文件。"""
    violations: list[Path] = []
    if not root.is_dir():
        print(f"[guard] 未找到前端目录：{root}", file=sys.stderr)
        return violations
    for path in root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if path.is_file() and path.suffix in SRC_SUFFIXES:
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            if FORBIDDEN in text:
                violations.append(path)
    return violations


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    root = repo / FRONTEND_REL
    violations = scan(root)
    if violations:
        print(f"[guard] 违规：前端目录不得 import `{FORBIDDEN}`（D-20 硬边界 ①）", file=sys.stderr)
        for v in sorted(violations):
            rel = v.relative_to(repo)
            print(f"  - {rel}", file=sys.stderr)
        print(f"[guard] 共 {len(violations)} 处违规，exit 1", file=sys.stderr)
        return 1
    print(f"[guard] OK：前端目录未引用 `{FORBIDDEN}`")
    return 0


if __name__ == "__main__":
    sys.exit(main())
