"""引签 · 引用链组件：无头截图 + 拼状态板。

为什么要有这个脚本：组件是 HTML，肉眼验收靠截图；但**图必须来自真渲染**，
不能是脚本「画一个像组件的图」。所以这里只做两件事：

1. 用本机 Chrome 无头跑 `chain.html`（沙箱内 Chrome 起不来，需在无沙箱的 shell 里跑）；
2. 把截图拼成一张状态板，附上「产品字段 → 链上的形」对照。

用法：python icon-lab/ikaoju-v2/chain/shot.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageFont

HERE = Path(__file__).parent
V2 = HERE.parent
CHROME = Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe")
FONTS = "C:/Windows/Fonts/"
#: Chrome 的 profile 不放工作区（几百个文件），放系统临时目录
PROFILE = Path(tempfile.gettempdir()) / "dsh-ikaoju-chain-profile"

S = 2                      # 拼板超采样
W, H = 1680, 0             # H 动态
BG = (238, 239, 234)
PANEL = (251, 250, 247)
INK = (23, 27, 25)
MUTED = (94, 104, 98)
FAINT = (138, 147, 141)
HAIR = (214, 217, 211)
BLUE = (30, 90, 216)
RED = (181, 67, 46)
PALE = (110, 143, 217)
AMBER = (154, 107, 31)


def font(name: str, size: float):
    return ImageFont.truetype(FONTS + name, int(round(size * S)))


# ---------------------------------------------------------------- 截图
def chrome_shot(query: str, width: int, height: int, out: Path, scale: int = 2) -> Path:
    """按给定视口跑一次无头截图。返回 PNG 路径。"""
    url = (HERE / "chain.html").as_uri() + query
    profile = PROFILE
    args = [
        str(CHROME), "--headless=new", "--no-sandbox", "--disable-gpu",
        "--hide-scrollbars", "--disable-lcd-text",
        f"--force-device-scale-factor={scale}",
        "--virtual-time-budget=6000",
        f"--user-data-dir={profile}",
        f"--window-size={width},{height}",
        f"--screenshot={out}",
        url,
    ]
    subprocess.run(args, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if not out.exists():
        raise SystemExit(f"截图失败：{out}")
    return out


def trim(img: Image.Image, bg=(244, 246, 243), pad: int = 0) -> Image.Image:
    """裁掉底部/右侧的纯背景留白（视口比内容高时必然出现）。"""
    solid = Image.new("RGB", img.size, bg)
    diff = ImageChops.difference(img.convert("RGB"), solid).convert("L").point(lambda v: 255 if v > 6 else 0)
    box = diff.getbbox()
    if not box:
        return img
    left, top, right, bottom = box
    return img.crop((max(0, left - pad), max(0, top - pad),
                     min(img.width, right + pad), min(img.height, bottom + pad)))


def fit(img: Image.Image, width: int) -> Image.Image:
    if img.width == width:
        return img
    height = int(round(img.height * width / img.width))
    return img.resize((width, height), Image.LANCZOS)


# ---------------------------------------------------------------- 拼板
canvas = Image.new("RGBA", (W * S, 3000 * S), BG + (255,))
d = ImageDraw.Draw(canvas)
y = 0


def text(x, yy, s, f, fill, anchor="ls"):
    d.text((int(x * S), int(yy * S)), s, font=f, fill=fill, anchor=anchor)


def tracked(x, yy, s, f, fill, tracking):
    for ch in s:
        d.text((int(x * S), int(yy * S)), ch, font=f, fill=fill, anchor="ls")
        x += f.getlength(ch) / S + tracking


def rule(yy, x0=64, x1=W - 64):
    d.line([int(x0 * S), int(yy * S), int(x1 * S), int(yy * S)], fill=HAIR, width=int(S))


def panel(img: Image.Image, x, yy) -> int:
    """把截图按纸面卡片贴上去，返回底边 y。"""
    w = img.width
    d.rectangle([int((x - 10) * S), int((yy - 10) * S), int((x + w + 10) * S), int((yy + img.height + 10) * S)],
                fill=PANEL, outline=HAIR, width=int(S))
    canvas.alpha_composite(img.convert("RGBA"), (int(x * S), int(yy * S)))
    return yy + img.height + 10


f_eyebrow = font("georgia.ttf", 11)
f_meta = font("georgia.ttf", 10.5)
f_h1 = font("msyhbd.ttc", 46)
f_sub = font("msyh.ttc", 14)
f_h = font("msyhbd.ttc", 15)
f_body = font("msyh.ttc", 12.5)
f_small = font("msyh.ttc", 11.5)
f_label = font("msyhbd.ttc", 10)
f_mono = font("consola.ttf", 11.5)
f_serif = font("simfang.ttf", 40)

# ---------------- header ----------------
tracked(64, 58, "IKAOJU  /  COMPONENT STUDY  /  YIN CHAIN", f_eyebrow, MUTED, 2.6)
meta = "CHAIN  ·  2026.10"
text(W - 64 - sum(f_meta.getlength(c) / S for c in meta) - 1.8 * (len(meta) - 1), 58, meta, f_meta, MUTED)
rule(84)
text(64, 168, "图标落地成组件：报告里的引用链。", f_h1, INK)
text(64, 204, "同一枚形状 —— 被引用的那一行、一跳一跳的虚线、骑在页缝上的签 —— 在报告正文里就是引用标记本身。",
     f_sub, MUTED)
y = 250

# ---------------- 1. 报告页（静置态） ----------------
def grab(query: str, width: int, height: int, name: str, scale: int = 2) -> Image.Image:
    """截图 → 裁到「页 + 投影」的包围盒。"""
    raw = Image.open(chrome_shot(f"{query}&shot=1", width, height, HERE / f"_shot-{name}.png", scale))
    out = trim(raw)
    print(f"  {name}: 视口 {raw.size} → 裁切 {out.size}")
    return out


print("截图：")
shot_report = fit(grab("?motion=0", 1440, 1400, "report"), 980)
shot_card = fit(grab("?motion=0&open=2", 1440, 1400, "card"), 980)
shot_states = fit(grab("?view=states&motion=0", 1280, 1500, "states"), 620)
shot_mobile = fit(grab("?motion=0", 430, 2400, "mobile", scale=3), 300)

text(64, y, "① 报告页（静置）：每一处 [来源: N] 都是一条链", f_h, INK)
text(64, y + 22, "claim 下划线、三段虚线、页边的签，同一种颜色；页缝上的三道针脚与页角折痕都保留。", f_small, MUTED)
y += 46
bottom = panel(shot_report, 64, y)

# 右侧：字段 → 形 的对照
bx = 64 + shot_report.width + 46
text(bx, y + 6, "产品字段 → 链上的形", f_h, INK)
rows = [
    ("Claim.claim", "被引用的那一行 → 同色下划线", INK),
    ("finding_id", "第 1 段虚线（论断 → 工作证据）", INK),
    ("evidence_id / content_hash", "第 2 段虚线（工作证据 → 原文证据）", INK),
    ("snapshot_id / quote / match_level", "第 3 段虚线（原文 → 来源快照）", INK),
    ("verified", "已核验 · 青蓝实心", BLUE),
    ("verified_relaxed", "待复核 · 浅蓝实心", PALE),
    ("verification_failed", "待复核 · 浅蓝空心（没结论）", PALE),
    ("!verified 且已查实", "未通过 · 朱", RED),
    ("independent_source_count", "叠签枚数（多源印证）", INK),
    ("[来源: 信息不足]", "琥珀空心虚线边（无据）", AMBER),
]
ry = y + 42
for key, val, color in rows:
    d.rectangle([int(bx * S), int((ry - 9) * S), int((bx + 3) * S), int(ry * S)], fill=color)
    text(bx + 12, ry, key, f_mono, INK)
    text(bx + 12, ry + 19, val, f_small, MUTED)
    ry += 44

text(bx, ry + 10, "口径不发明：色与形全部来自产品既有令牌", f_small, MUTED)
text(bx, ry + 30, "与 state.Citation / lib/quality.ts 的字段一一对应。", f_small, MUTED)
y = max(bottom, ry + 48) + 56

# ---------------- 2. 状态对照 ----------------
rule(y - 26)
text(64, y, "② 同一条链，五种签面（?view=states）", f_h, INK)
text(64 + 420, y, "四态映射成三色 + 形；签面说品牌口径，卡里说产品精确口径。", f_small, MUTED)
y += 40
bottom = panel(shot_states, 64, y)

legend = [
    ("已核验", BLUE, "verified：存在且忠实"),
    ("待复核", PALE, "relaxed：宽松口径通过"),
    ("待复核", PALE, "unknown：校验未完成（空心）"),
    ("未通过", RED, "refuted：确证失败"),
    ("无据", AMBER, "写作层无可用编号（虚线边）"),
]
lx = 64 + shot_states.width + 60
for i, (name, color, note) in enumerate(legend):
    ly = y + 30 + i * 62
    d.rounded_rectangle([int(lx * S), int((ly - 20) * S), int((lx + 34) * S), int((ly + 14) * S)],
                        radius=int(11 * S), fill=color if "空心" not in note and "虚线" not in note else None,
                        outline=color, width=int(3 * S))
    text(lx + 52, ly - 6, name, f_body, INK)
    text(lx + 52, ly + 14, note, f_small, MUTED)
y = bottom + 56

# ---------------- 3. 展开态 + 窄屏 ----------------
rule(y - 26)
text(64, y, "③ 点签展开：出处从页缝上长出来（三段 = 三跳）", f_h, INK)
text(64, y + 22, "卡里的每一跳可回指对应的那一段虚线；多源印证时是叠签（independent_source_count 枚）。", f_small, MUTED)
y += 46
bottom = panel(shot_card, 64, y)

mx = 64 + shot_card.width + 60
text(mx, y + 6, "④ 窄屏（≤860px）", f_h, INK)
text(mx, y + 28, "页缝收掉，签落回行内；", f_small, MUTED)
text(mx, y + 46, "卡变成底部抽屉。", f_small, MUTED)
panel(shot_mobile, mx, y + 70)
y = max(bottom, y + 70 + shot_mobile.height + 10) + 56

# ---------------- 4. 与图标同一枚形状 ----------------
rule(y - 26)
text(64, y, "⑤ 同一枚形状：组件 ↔ 图标", f_h, INK)
text(64, y + 22, "组件的三段路径在 16px 上退化成三段并排的短线；签、页缝、针脚、折角同步衰减，主形不变。", f_small, MUTED)
y += 56
icons = [("01-yinqian", "已核验", 128), ("02-yinqian-pending", "待复核", 128),
         ("03-yinqian-failed", "未通过", 128), ("01-yinqian-mono", "单色", 128)]
ix = 64
for name, label, size in icons:
    path = V2 / f"{name}-512.png"
    if path.exists():
        im = Image.open(path).convert("RGBA").resize((size * S, size * S), Image.LANCZOS)
        canvas.alpha_composite(im, (int(ix * S), int(y * S)))
        text(ix, y + size + 22, label, f_small, MUTED, anchor="ls")
    ix += size + 44
# 尺寸衰减
sx = ix + 30
for s in (64, 32, 16):
    im = Image.open(V2 / "01-yinqian-512.png").convert("RGBA").resize((s * S, s * S), Image.LANCZOS)
    canvas.alpha_composite(im, (int(sx * S), int((y + 128 - s) * S)))
    text(sx, y + 150, f"{s}px", f_small, FAINT, anchor="ls")
    sx += s + 34
y += 200

# ---------------- footer ----------------
rule(y)
text(64, y + 26, "爱考据 · iKaoju  /  引签 · 报告内引用链（组件原型）", f_small, FAINT)
text(W - 64, y + 26, "chain.html · shot.py  ·  2026", f_small, FAINT, anchor="rs")
y += 56

out = canvas.crop((0, 0, W * S, int(y * S))).convert("RGB").resize((W, int(y)), Image.LANCZOS)
out.save(HERE / "chain-sheet.png")
print("saved", out.size)
