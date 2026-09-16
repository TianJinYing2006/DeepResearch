"""校验 Markdown 各连续表格块内每行的单元格数是否一致。

用途（对应 `.workbuddy/memory/MEMORY.md` 的「编辑纪律」）：
往 markdown 表格里插入新行时，若 `old_string` 只取「目标行开头」，会把行尾悬空、
导致该表格块列数错乱 —— 本项目曾把变更记录表改成 8 列。改完表格跑一次本脚本即可发现。

计数口径（避免误报）：
- 只把**未被反斜杠转义**的 `|` 当分隔符（`\\|` 是字面量）；
- **忽略行内代码** `` `...` `` 里的 `|`（本仓库表格里常写 `` `|` `` 说明表格行过滤）；
- **忽略围栏代码块**内部的整行。

用法：
    python tools/check_md_tables.py [<md 路径> ...]

不带参数时默认检查 `docs/requirements/` 下的全部 markdown。退出码 0 = 全部一致。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 只有「未被反斜杠转义」的 | 才是单元格分隔符
SPLIT = re.compile(r"(?<!\\)\|")
INLINE_CODE = re.compile(r"`[^`\n]*`")


def _cells(line: str) -> int:
    # 行内代码里的 | 不算分隔符（先整体抹掉，长度无关紧要）
    s = INLINE_CODE.sub("CODE", line).strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|"):
        s = s[:-1]
    return len(SPLIT.split(s))


def check_file(path: Path) -> int:
    """返回不一致的表格块数（0 = 全一致）。"""
    lines = path.read_text(encoding="utf-8").split("\n")

    blocks: list[list[tuple[int, str]]] = []
    cur: list[tuple[int, str]] = []
    in_fence = False
    for lineno, line in enumerate(lines, 1):
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            if len(cur) > 1:
                blocks.append(cur)
            cur = []
            continue
        if not in_fence and line.lstrip().startswith("|"):
            cur.append((lineno, line))
        else:
            if len(cur) > 1:  # 单行不成表
                blocks.append(cur)
            cur = []
    if len(cur) > 1:
        blocks.append(cur)

    bad = 0
    for block in blocks:
        counts = {_cells(row) for _, row in block}
        if len(counts) > 1:
            bad += 1
            print(f"  ❌ 块起始行 {block[0][0]}（{len(block)} 行）列数不一致: {sorted(counts)}")
            for lineno, row in block:
                print(f"     L{lineno}: {_cells(row)} 列  {row[:90]}")

    print(f"{path}: 表格块 {len(blocks)} 个，不一致 {bad} 个")
    return bad


def main(argv: list[str]) -> int:
    targets = [Path(a) for a in argv[1:]] or sorted(Path("docs/requirements").glob("*.md"))
    if not targets:
        print("没有找到待检查的 markdown")
        return 0

    total_bad = sum(check_file(p) for p in targets)
    print("---")
    print(f"共 {len(targets)} 个文件，不一致表格块合计 = {total_bad}")
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
