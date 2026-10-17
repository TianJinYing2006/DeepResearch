"""Build the icon contact sheet PNG (no browser needed) from the concept SVGs."""
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont, ImageFilter

from render import render

S = 2
W, H = 1480, 878
BG = (236, 233, 226)
PAPER = (251, 250, 247)
STAGE = (243, 241, 234)
INK = (35, 49, 58)
RED = (194, 69, 45)
MUTED = (97, 112, 123)
FAINT = (138, 151, 162)
SLATE = (174, 185, 199)
HAIR = tuple(int(round(BG[i] + (INK[i] - BG[i]) * 0.14)) for i in range(3))

FONTS = "C:/Windows/Fonts/"
HERE = Path(__file__).parent


def font(name, size):
    return ImageFont.truetype(FONTS + name, int(round(size * S)))


def P(v):
    return int(round(v * S))


def tracked(draw, xy, text, f, fill, tracking):
    x, y = xy
    for ch in text:
        draw.text((P(x), P(y)), ch, font=f, fill=fill, anchor="ls")
        x += f.getlength(ch) / S + tracking
    return x


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
    img = render(HERE / "concepts" / f"{name}.svg", size * S, supersample)
    return img


def paste_with_shadow(canvas, img, x, y, blur=13, dy=13, alpha=44):
    sh = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    shadow = Image.new("RGBA", img.size, INK + (alpha,))
    sh.paste(shadow, (P(x), P(y) + P(dy)), img)
    sh = sh.filter(ImageFilter.GaussianBlur(P(blur) / 2))
    canvas.alpha_composite(sh)
    canvas.alpha_composite(img, (P(x), P(y)))


canvas = Image.new("RGBA", (W * S, H * S), BG + (255,))
d = ImageDraw.Draw(canvas)

f_eyebrow = font("georgia.ttf", 11.5)
f_meta = font("georgia.ttf", 11.5)
f_title_lat = font("georgiai.ttf", 38)
f_title_cjk = font("msyhbd.ttc", 32)
f_sub = font("msyh.ttc", 13.5)
f_idx = font("georgia.ttf", 14)
f_name = font("msyhbd.ttc", 20)
f_en = font("georgia.ttf", 10)
f_desc = font("msyh.ttc", 13)
f_px = font("georgia.ttf", 9.5)
f_swatch = font("msyh.ttc", 10.5)
f_note = font("msyh.ttc", 11)

# ---- header ----
tracked(d, (72, 66), "ICON STUDIES / 01  —  IKAOJU REBRAND", f_eyebrow, MUTED, 2.4)
tracked(d, (W - 72 - tracked_width("2026 · 10   |   CONCEPT SHEET", f_meta, 1.4), 66),
        "2026 · 10   |   ", f_meta, MUTED, 1.4)
tracked(d, (W - 72 - tracked_width("CONCEPT SHEET", f_meta, 1.4), 66), "CONCEPT SHEET", f_meta, RED, 1.4)
d.line([P(72), P(96), P(W - 72), P(96)], fill=HAIR, width=P(1))

x = 72
d.text((P(x), P(154)), "iKaoju", font=f_title_lat, fill=RED, anchor="ls")
x += f_title_lat.getlength("iKaoju") / S + 10
d.text((P(x), P(152)), "的三种图标方向", font=f_title_cjk, fill=INK, anchor="ls")
d.text((P(72), P(192)),
       "同一套纸、墨、朱的语气，三种不同的侧重：检索、收束、引证。手工矢量路径，未借用图标库；每枚都在 16px 下校过轮廓。",
       font=f_sub, fill=MUTED, anchor="ls")

# ---- cards ----
cards = [
    dict(idx="A", name="深读", en="LENS", svg="a-lens",
         desc="镜片里三行资料逐行收短，最后一行以墨色落定。检索与读完，被放进同一个符号里。"),
    dict(idx="B", name="汇流", en="CONFLUENCE", svg="b-confluence",
         desc="三条线索从左侧汇入一枚朱色结论块。考据不是堆叠资料，而是收束成一个判断。"),
    dict(idx="C", name="引证", en="CITATION", svg="c-citation",
         desc="一对方括号框住一枚朱色落点：被引用的事实，与由此得到的结论。最安静的一版。"),
]

CARD_W, CARD_H, GAP = 430, 506, 22
CARD_Y = 232
for i, c in enumerate(cards):
    cx = 72 + i * (CARD_W + GAP)
    d.rounded_rectangle([P(cx), P(CARD_Y), P(cx + CARD_W), P(CARD_Y + CARD_H)],
                        radius=P(22), fill=PAPER, outline=HAIR, width=P(1))
    sx, sy, sw, sh_ = cx + 26, CARD_Y + 26, CARD_W - 52, 250
    d.rounded_rectangle([P(sx), P(sy), P(sx + sw), P(sy + sh_)], radius=P(18), fill=STAGE)

    big = icon(c["svg"], 200, supersample=2)
    paste_with_shadow(canvas, big, sx + (sw - 200) / 2, sy + 25)

    base = sy + sh_ + 36
    d.text((P(cx + 26), P(base)), c["idx"], font=f_idx, fill=RED, anchor="ls")
    d.text((P(cx + 26 + 26), P(base)), c["name"], font=f_name, fill=INK, anchor="ls")
    en_x = cx + CARD_W - 26 - tracked_width(c["en"], f_en, 1.9)
    tracked(d, (en_x, base), c["en"], f_en, MUTED, 1.9)

    y = base + 30
    for line in wrap(c["desc"], f_desc, CARD_W - 52)[:3]:
        d.text((P(cx + 26), P(y)), line, font=f_desc, fill=MUTED, anchor="ls")
        y += 21
    y += 12
    d.line([P(cx + 26), P(y), P(cx + CARD_W - 26), P(y)], fill=HAIR, width=P(1))

    sizes = [48, 32, 16]
    bottom = y + 74
    px = cx + 26
    for s in sizes:
        img = icon(c["svg"], s, supersample=4)
        canvas.alpha_composite(img, (P(px), P(bottom - s)))
        d.text((P(px + s / 2), P(bottom + 18)), str(s), font=f_px, fill=FAINT, anchor="ms")
        px += s + 26

# ---- footer ----
d.line([P(72), P(792), P(W - 72), P(792)], fill=HAIR, width=P(1))
sw_x = 72
for label, col in [("纸 #FBFAF7", PAPER), ("墨 #23313A", INK), ("朱 #C2452D", RED), ("灰 #AEB9C7", SLATE)]:
    d.rounded_rectangle([P(sw_x), P(812), P(sw_x + 14), P(826)], radius=P(4), fill=col,
                        outline=HAIR, width=P(1))
    d.text((P(sw_x + 22), P(825)), label, font=f_swatch, fill=MUTED, anchor="ls")
    sw_x += 22 + 14 + f_swatch.getlength(label) / S + 26
d.text((P(W - 72), P(825)), "512 × 512 canvas · 可选 4px 网格重绘 · 透明底与深色底版本可另出",
       font=f_note, fill=FAINT, anchor="rs")

out = canvas.convert("RGB").resize((W, H), Image.LANCZOS)
out.save(HERE / "contact-sheet.png")
for c in cards:
    icon(c["svg"], 512, 3).save(HERE / f"{c['svg']}-512.png")
print("saved", out.size)
