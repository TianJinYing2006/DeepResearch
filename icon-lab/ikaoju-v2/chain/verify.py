"""引签 · 引用链组件的验收脚本。

两件事，都可重复执行：

1. **无头断言**：四种配置（默认 / 展开态 / 状态页 / 窄屏）各跑一次 `?assert=1`，
   把组件自己的自检结果读回来（几何、ARIA、对比度、动效终态、多源拆解）；
2. **像素复核**：截图里必须真的出现四种链色与墨色页缝 —— 断言跑在 DOM 上，
   这一层确认「确实画出来了」，并打一张粗预览，便于人眼扫一眼布局有没有塌。

用法：python icon-lab/ikaoju-v2/chain/verify.py
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path

from PIL import Image

HERE = Path(__file__).parent
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
#: 与 shot.py 一致：Chrome profile 放系统临时目录，不污染工作区
PROFILE = Path(tempfile.gettempdir()) / "dsh-ikaoju-chain-profile"

CASES = [
    ("默认 1440", "?assert=1", 1440, 1100),
    ("展开态 1440", "?assert=1&open=2", 1440, 1100),
    ("状态页 1280", "?assert=1&view=states", 1280, 1200),
    ("窄屏 430", "?assert=1", 430, 1600),
]

CHAIN_COLORS = {
    "已核验 蓝": (30, 90, 216),
    "待复核 浅蓝": (110, 143, 217),
    "未通过 朱": (181, 67, 46),
    "无据 琥珀": (154, 107, 31),
    "墨底": (23, 27, 25),
    "纸白": (255, 255, 255),
}

PREVIEW = {
    " ": (244, 246, 243),
    ".": (255, 255, 255),
    "#": (23, 27, 25),
    "B": (30, 90, 216),
    "p": (110, 143, 217),
    "X": (181, 67, 46),
    "A": (154, 107, 31),
    "-": (219, 222, 218),
    ",": (94, 104, 98),
}


def dump_dom(query: str, width: int, height: int) -> str:
    """跑一次无头 Chrome 取回 DOM。用管道收 stdout：写临时文件会被 Chrome 的子进程占住句柄。"""
    proc = subprocess.run(
        [str(CHROME), "--headless=new", "--no-sandbox", "--disable-gpu", "--hide-scrollbars",
         "--virtual-time-budget=6000", f"--window-size={width},{height}",
         f"--user-data-dir={PROFILE}", "--dump-dom",
         (HERE / "chain.html").as_uri() + query],
        check=True, capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    return proc.stdout


def check_asserts() -> int:
    failures = 0
    for name, query, width, height in CASES:
        dom = dump_dom(query, width, height)
        title = re.search(r"<title>(.*?)</title>", dom)
        summary = title.group(1) if title else "(无 title)"
        block = re.search(r'(?s)<pre id="assert"[^>]*>(.*?)</pre>', dom)
        bad = []
        if block:
            body = block.group(1).replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
            bad = [line for line in body.splitlines() if line.startswith("FAIL")]
        ok = summary.startswith("ASSERT OK") and not bad
        failures += 0 if ok else 1
        print(f"[{'PASS' if ok else 'FAIL'}] {name:12s} {summary}")
        for line in bad:
            print("        " + line)
    return failures


def classify(pixel) -> str:
    return min(PREVIEW.items(), key=lambda kv: sum((pixel[i] - kv[1][i]) ** 2 for i in range(3)))[0]


def preview(img: Image.Image, cols: int = 112):
    rows = max(1, int(round(cols * img.height / img.width * 0.46)))
    small = img.convert("RGB").resize((cols, rows), Image.BOX)
    for y in range(rows):
        print("".join(classify(small.getpixel((x, y))) for x in range(cols)))


def check_pixels() -> int:
    shot = HERE / "_shot-report.png"
    if not shot.exists():
        print("[SKIP] 还没截图（先跑 shot.py）")
        return 0
    img = Image.open(shot).convert("RGB")
    print(f"\n报告页截图 {img.size[0]}×{img.size[1]}（device scale 2）")
    failures = 0
    counts = {}
    px = img.load()
    step = 2
    for y in range(0, img.height, step):
        for x in range(0, img.width, step):
            p = px[x, y]
            for name, target in CHAIN_COLORS.items():
                if all(abs(p[i] - target[i]) <= 26 for i in range(3)):
                    counts[name] = counts.get(name, 0) + 1
    for name in CHAIN_COLORS:
        n = counts.get(name, 0)
        need = 40 if name not in ("墨底", "纸白") else 400
        ok = n >= need
        failures += 0 if ok else 1
        print(f"  [{'PASS' if ok else 'FAIL'}] {name:10s} 命中像素 {n}（阈值 {need}）")

    # 页缝必须是右侧一条墨色竖带（宽度 = --rail-w 76px）；多取几行取最大值 ——
    # 签骑在页缝上会盖住其中一段，只测一行会低估宽度。
    runs, start = [], None
    widths = []
    for y in range(120, img.height, 137):
        runs, start = [], None
        for x in range(img.width):
            dark = all(abs(px[x, y][i] - CHAIN_COLORS["墨底"][i]) <= 30 for i in range(3))
            if dark and start is None:
                start = x
            elif not dark and start is not None:
                runs.append((start, x - start))
                start = None
        if start is not None:
            runs.append((start, img.width - start))
        best = max([r[1] for r in runs] or [0])
        if best:
            widths.append(best)
    widest = max(widths or [0])
    ok = abs(widest / 2 - 76) <= 3
    print(f"  [{'PASS' if ok else 'FAIL'}] 页缝竖带最宽 {widest}px @2x ≈ {widest / 2:.0f}px（期望 76px）")
    failures += 0 if ok else 1

    print("\n粗预览（B=已核验 p=待复核 X=未通过 A=无据 #=墨底 .=纸面 空格=桌面）")
    preview(img)
    return failures


if __name__ == "__main__":
    import sys

    sys.exit(1 if (check_asserts() + check_pixels()) else 0)
