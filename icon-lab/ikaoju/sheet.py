"""iKaoju icon proposal sheet — 文与据."""
import sys
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE.parent))
from render import render  # noqa: E402

S = 2
W, H = 1480, 1010
BG = (231, 234, 230)
CARD = (255, 255, 255)
STAGE = (234, 238, 233)
INK = (23, 27, 25)
MUTED = (94, 104, 98)
FAINT = (138, 147, 141)
HAIR = (217, 222, 218)
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


def wrap(text, f, max_w):
    lines, cur = [], ""
    for ch in text:
        trial = cur + ch
        if f.getlength(trial) / S > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


def icon(name, size, supersample=3):
    return render(HERE / f"{name}.svg", size * S, supersample)


canvas = Image.new("RGBA", (W * S, H * S), BG + (255,))
d = ImageDraw.Draw(canvas)

f_eyebrow = font("georgia.ttf", 11.5)
f_title = font("msyhbd.ttc", 34)
f_sub = font("msyh.ttc", 13.5)
f_role = font("msyh.ttc", 9.5)
f_name = font("msyhbd.ttc", 21)
f_desc = font("msyh.ttc", 13)
f_px = font("georgia.ttf", 9.5)
f_tile = font("msyhbd.ttc", 13)
f_cap = font("msyh.ttc", 11)
f_note = font("msyh.ttc", 11)
f_lock = font("msyhbd.ttc", 26)
f_lock_en = font("georgiai.ttf", 13)

# ---------- header ----------
tracked(d, (72, 64), "IKAOJU · ICON PROPOSAL", f_eyebrow, MUTED, 2.6)
meta = "2026 · 10   |   V1"
tracked(d, (W - 72 - tracked_width(meta, f_eyebrow, 1.6), 64), meta, f_eyebrow, MUTED, 1.6)
d.line([P(72), P(92), P(W - 72), P(92)], fill=HAIR, width=P(1))

d.text((P(72), P(150)), "文与据：把「出处」画进图标", font=f_title, fill=INK, anchor="ls")
d.text((P(72), P(190)),
       "这个产品真正独有的结构，不是「研究」而是「每一句话都挂着它的出处」。图标就画这个结构，再按尺寸做减法。",
       font=f_sub, fill=MUTED, anchor="ls")

# ---------- row 1 : three executions ----------
cards = [
    dict(svg="01-margin", role="主标 · APP ICON", name="页边引文", scale="≥ 48 px",
         desc="正文三行，页边一枚角标，一条引线把这句话与它的出处连起来。档案感来自结构，不来自纸纹。"),
    dict(svg="02-inline", role="报告 / UI", name="句末角标", scale="24 – 48 px",
         desc="角标挂在最后一行句末，就是报告里论断与引用编号的关系；它同时是界面里的引用组件。"),
    dict(svg="03-mark", role="FAVICON · 极小尺寸", name="角标", scale="16 – 24 px",
         desc="只留两行与一枚角标：16px 下没有细线、没有小字，仍然分得清「文」和「据」。"),
]
CARD_W, CARD_H, GAP, CARD_Y = 430, 500, 22, 224
for i, c in enumerate(cards):
    cx = 72 + i * (CARD_W + GAP)
    d.rounded_rectangle([P(cx), P(CARD_Y), P(cx + CARD_W), P(CARD_Y + CARD_H)],
                        radius=P(20), fill=CARD, outline=HAIR, width=P(1))
    sx, sy, sw, sh_ = cx + 26, CARD_Y + 26, CARD_W - 52, 244
    d.rounded_rectangle([P(sx), P(sy), P(sx + sw), P(sy + sh_)], radius=P(16), fill=STAGE)
    big = icon(c["svg"], 200, 2)
    canvas.alpha_composite(big, (P(sx + (sw - 200) / 2), P(sy + 22)))

    base = sy + sh_ + 30
    tracked(d, (cx + 26, base), c["role"], f_role, BLUE if i == 0 else FAINT, 1.8)
    scaled = " ".join(c["scale"].split())
    tracked(d, (cx + CARD_W - 26 - tracked_width(scaled, f_role, 1.2), base), scaled, f_role, FAINT, 1.2)
    d.text((P(cx + 26), P(base + 28)), c["name"], font=f_name, fill=INK, anchor="ls")
    y = base + 56
    for line in wrap(c["desc"], f_desc, CARD_W - 52)[:2]:
        d.text((P(cx + 26), P(y)), line, font=f_desc, fill=MUTED, anchor="ls")
        y += 21
    y += 14
    d.line([P(cx + 26), P(y), P(cx + CARD_W - 26), P(y)], fill=HAIR, width=P(1))
    px = cx + 26
    bottom = y + 64
    for s in (48, 32, 16):
        small = icon(c["svg"], s, 4)
        canvas.alpha_composite(small, (P(px), P(bottom - s)))
        d.text((P(px + s / 2), P(bottom + 18)), str(s), font=f_px, fill=FAINT, anchor="ms")
        px += s + 26

# ---------- row 2 : variants ----------
TILE_Y, TILE_H = 746, 210
tiles = [(72, "单色与反白"), (72 + CARD_W + GAP, "角标三态 · 图标即状态语言"),
         (72 + 2 * (CARD_W + GAP), "与字标的组合")]
for tx, title in tiles:
    d.rounded_rectangle([P(tx), P(TILE_Y), P(tx + CARD_W), P(TILE_Y + TILE_H)],
                        radius=P(20), fill=CARD, outline=HAIR, width=P(1))
    d.text((P(tx + 26), P(TILE_Y + 34)), title, font=f_tile, fill=INK, anchor="ls")

tx = tiles[0][0]
for img_name, ox, cap in (("01-margin-mono", 26, "单色"), ("01-margin-dark", 132, "深色底")):
    im = icon(img_name, 72, 3)
    canvas.alpha_composite(im, (P(tx + ox), P(TILE_Y + 68)))
    d.text((P(tx + ox + 36), P(TILE_Y + 172)), cap, font=f_cap, fill=MUTED, anchor="ms")

tx = tiles[1][0]
for img_name, ox, cap in (("03-mark", 26, "已核验"), ("s-pending", 124, "待复核"), ("s-failed", 222, "未通过")):
    im = icon(img_name, 64, 3)
    canvas.alpha_composite(im, (P(tx + ox), P(TILE_Y + 72)))
    d.text((P(tx + ox + 32), P(TILE_Y + 172)), cap, font=f_cap, fill=MUTED, anchor="ms")

tx = tiles[2][0]
im = icon("03-mark", 52, 3)
canvas.alpha_composite(im, (P(tx + 26), P(TILE_Y + 76)))
d.text((P(tx + 96), P(TILE_Y + 112)), "爱考据", font=f_lock, fill=INK, anchor="ls")
d.text((P(tx + 100), P(TILE_Y + 136)), "iKaoju", font=f_lock_en, fill=FAINT, anchor="ls")
d.text((P(tx + 26), P(TILE_Y + 172)), "把复杂问题变成可追溯的研究结论", font=f_cap, fill=MUTED, anchor="ls")

# ---------- footer ----------
d.line([P(72), P(976), P(W - 72), P(976)], fill=HAIR, width=P(1))
d.text((P(72), P(996)), "被否掉的隐喻：放大镜（那是搜索）· 火花（AI 默认脸）· 对勾文档与印章（暗示背书，和「不承诺」的口径冲突）",
       font=f_note, fill=FAINT, anchor="ls")
d.text((P(W - 72), P(996)), "16px 可辨 · 单色可用 · 不依赖渐变、细线与阴影",
       font=f_note, fill=FAINT, anchor="rs")

out = canvas.convert("RGB").resize((W, H), Image.LANCZOS)
out.save(HERE / "sheet.png")
for name in ("01-margin", "02-inline", "03-mark"):
    icon(name, 512, 3).save(HERE / f"{name}-512.png")
print("saved", out.size)
