"""iKaoju identity sheet v2 — 一页，一据。"""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from render import render  # noqa: E402

S = 2
W, H = 1600, 1400
BG = (238, 239, 234)
STAGE = (228, 230, 223)
PAPER = (251, 250, 247)
INK = (23, 27, 25)
MUTED = (94, 104, 98)
FAINT = (138, 147, 141)
HAIR = (214, 217, 211)
BLUE = (30, 90, 216)
RED = (181, 67, 46)

FONTS = "C:/Windows/Fonts/"


def font(name, size):
    return ImageFont.truetype(FONTS + name, int(round(size * S)))


def P(v):
    return int(round(v * S))


def tracked(draw, xy, text, f, fill, tracking):
    x, y = xy
    for ch in text:
        draw.text((P(x), P(y)), ch, font=f, fill=fill, anchor="ls")
        x += f.getlength(ch) / S + tracking


def tracked_width(text, f, tracking):
    return sum(f.getlength(c) / S for c in text) + tracking * max(0, len(text) - 1)


def icon(size, supersample=3, name="01-yinqian"):
    return render(HERE / f"{name}.svg", size * S, supersample)


def paste_shadow(canvas, img, x, y, blur=20, dy=22, alpha=52):
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, INK + (alpha,))
    sh.paste(shadow, (P(x), P(y) + P(dy)), img)
    sh = sh.filter(ImageFilter.GaussianBlur(P(blur) / 2))
    canvas.alpha_composite(sh)
    canvas.alpha_composite(img, (P(x), P(y)))


canvas = Image.new("RGBA", (W * S, H * S), BG + (255,))
d = ImageDraw.Draw(canvas)

f_eyebrow = font("georgia.ttf", 11)
f_meta = font("georgia.ttf", 10.5)
f_h1 = font("msyhbd.ttc", 56)
f_sub = font("msyh.ttc", 14)
f_serif = font("simfang.ttf", 58)
f_latin = font("georgiai.ttf", 19)
f_tag = font("msyh.ttc", 12.5)
f_lead = font("simfang.ttf", 25)
f_h = font("msyhbd.ttc", 16)
f_body = font("msyh.ttc", 13)
f_small = font("msyh.ttc", 11)
f_label = font("msyhbd.ttc", 10)
f_px = font("georgia.ttf", 12)
f_cap = font("msyh.ttc", 10.5)
f_tab = font("msyh.ttc", 13)

# ---------------- header ----------------
tracked(d, (72, 62), "IKAOJU  /  IDENTITY STUDY  /  02", f_eyebrow, MUTED, 2.6)
meta = "ICON  ·  2026.10"
tracked(d, (W - 72 - tracked_width(meta, f_meta, 1.8), 62), meta, f_meta, MUTED, 1.8)
d.line([P(72), P(90), P(W - 72), P(90)], fill=HAIR, width=P(1))

d.text((P(72), P(172)), "一页，一据。", font=f_h1, fill=INK, anchor="ls")
d.text((P(72), P(210)), "页是报告，签是出处。图标不做「研究」的比喻，只做「考据」的结构。",
       font=f_sub, fill=MUTED, anchor="ls")

# ---------------- hero stage ----------------
stage = (72, 250, 652, 830)
d.rounded_rectangle([P(stage[0]), P(stage[1]), P(stage[2]), P(stage[3])], radius=P(8), fill=STAGE)
for i in range(1, 10):
    gx = stage[0] + (stage[2] - stage[0]) * i / 10
    gy = stage[1] + (stage[3] - stage[1]) * i / 10
    d.line([P(gx), P(stage[1]), P(gx), P(stage[3])], fill=(220, 222, 214), width=P(1))
    d.line([P(stage[0]), P(gy), P(stage[2]), P(gy)], fill=(220, 222, 214), width=P(1))
d.text((P(stage[0] + 16), P(stage[3] - 14)), "512 × 512  ·  4 px grid", font=f_cap, fill=FAINT, anchor="ls")

big = icon(400, 2)
paste_shadow(canvas, big, 162, 340)

# ---------------- lockup ----------------
mark = icon(84, 3)
canvas.alpha_composite(mark, (P(700), P(300)))
d.text((P(806), P(356)), "爱考据", font=f_serif, fill=INK, anchor="ls")
d.text((P(810), P(386)), "iKaoju", font=f_latin, fill=FAINT, anchor="ls")
d.text((P(810), P(412)), "可审计的研究工作台", font=f_tag, fill=MUTED, anchor="ls")

d.line([P(700), P(462), P(1528), P(462)], fill=HAIR, width=P(1))

d.text((P(700), P(508)), "不做「研究」的比喻，只做「考据」的结构。", font=f_lead, fill=INK, anchor="ls")

principles = [
    ("一页", "墨底纸页、页角折起——说明这一页是被翻过的。"),
    ("一链", "被引用的那一行、检索路径、签，同一种颜色：它们是同一件事。"),
    ("一跳", "路径画成虚线：检索不是一步到位，是一跳一跳走出来的。"),
    ("骑缝", "签骑在页缝上，出处与正文咬在一起；页缝从签上穿过去。"),
]
py = 556
for head, body in principles:
    d.rectangle([P(700), P(py - 10), P(710), P(py)], fill=BLUE)
    d.text((P(722), P(py)), head, font=f_h, fill=INK, anchor="ls")
    d.text((P(770), P(py)), body, font=f_body, fill=MUTED, anchor="ls")
    py += 56

d.text((P(700), P(792)), "16px 起可辨  ·  单色可用  ·  不依赖渐变、细线与阴影",
       font=f_cap, fill=FAINT, anchor="ls")

# ---------------- scale row ----------------
d.text((P(72), P(880)), "尺寸 / SCALE", font=f_label, fill=FAINT, anchor="ls")
d.line([P(172), P(876), P(W - 72), P(876)], fill=HAIR, width=P(1))

sizes = [256, 96, 48, 32, 16]
xs = [72, 380, 520, 604, 668]
base = 1150
for s, x in zip(sizes, xs):
    im = icon(s, 4)
    canvas.alpha_composite(im, (P(x), P(base - s)))
    d.text((P(x + s / 2), P(base + 20)), str(s), font=f_px, fill=FAINT, anchor="ms")

# browser tab context
tab_x, tab_y = 860, 960
d.rounded_rectangle([P(tab_x), P(tab_y + 30), P(W - 72), P(tab_y + 150)], radius=P(14), fill=(230, 231, 225))
d.rounded_rectangle([P(tab_x + 22), P(tab_y), P(tab_x + 470), P(tab_y + 132)], radius=P(14), fill=PAPER)
d.rectangle([P(tab_x + 22), P(tab_y + 110), P(tab_x + 470), P(tab_y + 132)], fill=PAPER)
small = icon(16, 6)
canvas.alpha_composite(small, (P(tab_x + 46), P(tab_y + 58)))
d.text((P(tab_x + 74), P(tab_y + 72)), "爱考据 · 可审计的研究工作台", font=f_tab, fill=INK, anchor="ls")
d.text((P(tab_x + 436), P(tab_y + 72)), "×", font=f_tab, fill=FAINT, anchor="ms")
d.rounded_rectangle([P(tab_x + 494), P(tab_y + 24), P(tab_x + 650), P(tab_y + 132)], radius=P(12), fill=(224, 226, 219))
d.text((P(tab_x + 516), P(tab_y + 82)), "历史报告", font=f_small, fill=FAINT, anchor="ls")

# ---------------- states ----------------
d.text((P(72), P(1210)), "状态 / STATE", font=f_label, fill=FAINT, anchor="ls")
d.line([P(172), P(1206), P(W - 72), P(1206)], fill=HAIR, width=P(1))

states = [("01-yinqian", "已核验"), ("02-yinqian-pending", "待复核"), ("03-yinqian-failed", "未通过")]
sx = 72
for name, label in states:
    im = icon(96, 4, name)
    canvas.alpha_composite(im, (P(sx), P(1226)))
    d.text((P(sx + 48), P(1338)), label, font=f_cap, fill=MUTED, anchor="ms")
    sx += 124

d.text((P(520), P(1262)), "同一条链，三种颜色。签在报告里就是引用组件：",
       font=f_body, fill=MUTED, anchor="ls")
d.text((P(520), P(1290)), "图标、页边角标、校验状态，用的是同一枚形状。",
       font=f_body, fill=MUTED, anchor="ls")

pal = [(INK, "墨  #171B19"), (PAPER, "纸  #FBFAF7"), (BLUE, "签  #1E5AD8"), (RED, "朱  #B5432E")]
px = 1040
for color, label in pal:
    d.rounded_rectangle([P(px), P(1261), P(px + 18), P(1279)], radius=P(5), fill=color,
                        outline=HAIR, width=P(1))
    d.text((P(px + 26), P(1277)), label, font=f_cap, fill=MUTED, anchor="ls")
    px += 26 + f_cap.getlength(label) / S + 34

# ---------------- footer ----------------
d.line([P(72), P(1360), P(W - 72), P(1360)], fill=HAIR, width=P(1))
d.text((P(72), P(1388)), "爱考据 · iKaoju  /  可审计的研究工作台", font=f_cap, fill=FAINT, anchor="ls")
d.text((P(W - 72), P(1388)), "icon study 03  ·  2026", font=f_cap, fill=FAINT, anchor="rs")

out = canvas.convert("RGB").resize((W, H), Image.LANCZOS)
out.save(HERE / "sheet-v2.png")
for name in ("01-yinqian", "01-yinqian-mono", "02-yinqian-pending", "03-yinqian-failed"):
    icon(512, 3, name).save(HERE / f"{name}-512.png")
print("saved", out.size)
