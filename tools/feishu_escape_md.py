"""生成飞书 Markdown 导入用的转义副本（W8 需求文档镜像用）。

背景（`.workbuddy/grill-me-需求梳理工作流.md` 镜像三坑 #1）：
飞书 Markdown 导入会把单个 `~`（如 `20~29pp`、`3.11~3.13`）误解析为删除线 `<del>`，
导致全文划线错乱；而**反引号代码内天然不解析、不能转义**（转义后代码内会显示 `\\~` 字面量）。

本脚本只做一件事：把「裸文本中的单个 `~`」转义为 `\\~`，
同时保护 fenced code block 与 inline code 内的 `~`，并保留有意的 `~~删除线~~`。

用法：
    python tools/feishu_escape_md.py <源 md> <输出 md> [--title "第N周需求文档"]

`--title` 会**注入一行 `<title>NAME</title>` 到输出首行**（`docs +update --command overwrite`
时飞书会用正文第一个 `#` 标题覆盖显示名，必须靠这行保住标题；`docs +create` 用 `--title` 参数即可，
但带上这行也无害）。
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

# 单个 ~ ：前一字符和后一字符都不是 ~（即不属于 ~~ 成对删除线）
SINGLE_TILDE = re.compile(r"(?<!~)~(?![~])")
# 自检用：已被 \ 转义的不算残留（`\~` 中的 ~ 前面是反斜杠）
UNESCAPED_TILDE = re.compile(r"(?<![~\\])~(?![~])")


def escape_for_feishu(text: str) -> str:
    """转义裸文本中的单个 ~，保护代码区与有意的 ~~ 删除线。"""
    protected: list[str] = []

    def _stash(match: re.Match[str]) -> str:
        protected.append(match.group(0))
        return f"\x00{len(protected) - 1}\x00"

    # 1) fenced code block（``` ... ```）整体保护
    text = re.sub(r"```.*?```", _stash, text, flags=re.S)
    # 2) inline code（`...`，不跨行）整体保护
    text = re.sub(r"`[^`\n]*`", _stash, text)

    # 3) 裸文本：单个 ~ → \~
    text = SINGLE_TILDE.sub(r"\\~", text)

    # 4) 还原保护内容
    def _restore(match: re.Match[str]) -> str:
        return protected[int(match.group(1))]

    return re.sub(r"\x00(\d+)\x00", _restore, text)


def main() -> int:
    if len(sys.argv) not in (3, 5):
        print(__doc__)
        return 2
    src, dst = Path(sys.argv[1]), Path(sys.argv[2])
    title = None
    if len(sys.argv) == 5:
        if sys.argv[3] != "--title":
            print(__doc__)
            return 2
        title = sys.argv[4]

    raw = src.read_text(encoding="utf-8")
    out = escape_for_feishu(raw)
    if title:
        # 为 overwrite 保住显示名：飞书只认正文里的第一行 <title>
        out = f"<title>{title}</title>\n\n{out}"

    # 自检：裸文本中不应再有未转义的单个 ~
    stripped = re.sub(r"```.*?```", "", out, flags=re.S)
    stripped = re.sub(r"`[^`\n]*`", "", stripped)
    leftovers = UNESCAPED_TILDE.findall(stripped)

    dst.write_text(out, encoding="utf-8")
    print(f"源文件      : {src}")
    print(f"输出        : {dst}")
    print(f"注入标题     : {title or '（无）'}")
    print(f"新增 \\~ 转义 : {out.count(chr(92) + '~')}")
    print(f"保留 ~~ 对   : {out.count('~~') // 2}")
    print(f"残留单 ~     : {len(leftovers)}  (应为 0)")
    return 1 if leftovers else 0


if __name__ == "__main__":
    raise SystemExit(main())
