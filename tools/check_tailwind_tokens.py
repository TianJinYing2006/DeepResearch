"""Tailwind 色板 token 守卫：禁止用字符串覆盖内置色板名，禁止使用未定义档位。

为什么单写这个脚本：
`theme.extend.colors` 里写 `cyan: '#66e3ff'` 这种**字符串**，会把 Tailwind 内置 `cyan`
的 `{50..950}` 色阶对象**整体替换**（是替换而非合并），于是 `text-cyan-200` /
`bg-cyan-300` / `border-cyan-300` / `from-cyan-400` / `to-cyan-300` 全部生成不出 CSS 规则。

这是**静默失效**，四道防线全漏：
- `tsc -b` 不报错 —— 类名只是字符串字面量，类型系统看不见；
- `vite build` 不报错 —— 无法解析的类直接不生成；
- Playwright E2E 不报错 —— E2E 只认 testid / 中文文案 / `article.report-prose`，不认色值；
- 人眼几乎看不出 —— 深色底配低透明度（`bg-cyan-300/[0.05]`）。

2026-09-29 实测：7 处代码 / 15 个类名实例长期失效，只在人工比对 dist CSS 产物时才发现。
本脚本把这类错误变成 CI 红灯（需求 11 的防复发机制）。

判据（两类）：
A. `theme.extend.colors` 中，键名为 Tailwind 内置色板名且值为字符串 ⇒ 违规；
B. 源码使用了自定义色阶的档位（如 `brand-500`），而该档位未在 config 中定义 ⇒ 违规。

刻意只做正则解析（不引入 JS 解析器）：config 结构固定、零依赖、可离线跑。

用法：
    python tools/check_tailwind_tokens.py      # 违规则以 exit 1 退出
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
CONFIG_REL = "web/frontend/tailwind.config.js"
FRONTEND_SRC_REL = "web/frontend/src"

# Tailwind v3 内置色板名。用字符串覆盖其中任何一个都会整条色阶失效。
BUILTIN_PALETTES = {
    "slate", "gray", "zinc", "neutral", "stone", "red", "orange", "amber",
    "yellow", "lime", "green", "emerald", "teal", "cyan", "sky", "blue",
    "indigo", "violet", "purple", "fuchsia", "pink", "rose",
}

SKIP_DIRS = {"node_modules", "dist", ".vite", ".git"}
SRC_SUFFIXES = {".ts", ".tsx", ".js", ".jsx", ".css"}


def _extract_block(text: str, start: int) -> str:
    """从 start 处的 '{' 起按括号配对取整个块（含外层花括号）；不配对则返回空串。"""
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
    return ""


def parse_colors(config_text: str) -> dict[str, set[str] | None]:
    """解析 `colors: { ... }` 块。

    返回 `{颜色名: 档位集合}`；值为 `None` 表示该颜色被定义成**字符串**（单值，非色阶）。
    """
    hit = re.search(r"colors:\s*\{", config_text)
    if not hit:
        return {}
    block = _extract_block(config_text, hit.end() - 1)
    if not block:
        return {}

    colors: dict[str, set[str] | None] = {}
    # 先收对象形式：brand: { 200: '#a5f3fc', 300: '#66e3ff' }
    for m in re.finditer(r"([A-Za-z][\w-]*)\s*:\s*\{([^{}]*)\}", block):
        colors[m.group(1)] = set(re.findall(r"(\d+)\s*:", m.group(2)))
    # 再收字符串形式：cyan: '#66e3ff'（setdefault 不覆盖已识别的对象形式）
    for m in re.finditer(r"([A-Za-z][\w-]*)\s*:\s*'[^']*'", block):
        colors.setdefault(m.group(1), None)
    return colors


def check_builtin_override(colors: dict[str, set[str] | None]) -> list[str]:
    """判据 A：内置色板被字符串覆盖。"""
    return sorted(n for n, v in colors.items() if v is None and n in BUILTIN_PALETTES)


def check_undefined_shades(
    colors: dict[str, set[str] | None], src_root: Path
) -> list[tuple[Path, str]]:
    """判据 B：源码用到了自定义色阶中未定义的档位。"""
    custom = {n: s for n, s in colors.items() if s}
    if not custom or not src_root.is_dir():
        return []

    bad: list[tuple[Path, str]] = []
    for path in src_root.rglob("*"):
        if any(part in SKIP_DIRS for part in path.parts):
            continue
        if not (path.is_file() and path.suffix in SRC_SUFFIXES):
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for name, shades in custom.items():
            for m in re.finditer(rf"\b{re.escape(name)}-(\d+)\b", text):
                if m.group(1) not in shades:
                    bad.append((path, f"{name}-{m.group(1)}"))
    return bad


def main() -> int:
    config_path = REPO / CONFIG_REL
    if not config_path.is_file():
        print(f"[guard] 未找到配置文件：{CONFIG_REL}", file=sys.stderr)
        return 1

    colors = parse_colors(config_path.read_text(encoding="utf-8"))
    if not colors:
        print(f"[guard] 未能从 {CONFIG_REL} 解析出 colors 块，判据失效", file=sys.stderr)
        return 1

    failed = False

    overridden = check_builtin_override(colors)
    if overridden:
        failed = True
        print(
            "[guard] 违规：不得用字符串覆盖 Tailwind 内置色板名 "
            "（会整体替换 {50..950} 色阶，导致带档位的类静默失效）",
            file=sys.stderr,
        )
        for name in overridden:
            print(f"  - {CONFIG_REL}: `{name}` 应为色阶对象，如 `{name}: {{ 300: '#xxxxxx' }}`", file=sys.stderr)

    undefined = check_undefined_shades(colors, REPO / FRONTEND_SRC_REL)
    if undefined:
        failed = True
        print("[guard] 违规：使用了自定义色阶中未定义的档位", file=sys.stderr)
        for path, token in sorted(undefined, key=lambda x: (str(x[0]), x[1])):
            rel = path.relative_to(REPO)
            print(f"  - {rel}: `{token}` 未在 {CONFIG_REL} 中定义", file=sys.stderr)

    if failed:
        print(f"[guard] 共 {len(overridden) + len(undefined)} 处违规，exit 1", file=sys.stderr)
        return 1

    shades = {n: sorted(s) for n, s in colors.items() if s}
    print(f"[guard] OK：无内置色板覆盖；自定义色阶档位齐全 {shades}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
