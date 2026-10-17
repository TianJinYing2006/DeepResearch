from pathlib import Path
from PIL import Image, ImageFont

HERE = Path(__file__).parent
img = Image.open(HERE / "sheet.png").convert("RGB")
print("size", img.size)

PAL = {
    "B": (30, 90, 216),
    "#": (23, 27, 25),
    "X": (181, 67, 46),
    ".": (234, 238, 233),
    "_": (255, 255, 255),
    " ": (231, 234, 230),
    ",": (94, 104, 98),
    "o": (138, 147, 141),
}


def classify(p):
    return min(PAL.items(), key=lambda kv: sum((p[i] - kv[1][i]) ** 2 for i in range(3)))[0]


cols, rows = 118, 68
small = img.resize((cols, rows), Image.BOX)
for y in range(rows):
    print("".join(classify(small.getpixel((x, y))) for x in range(cols)))

px = list(img.getdata())
blue = sum(1 for p in px if abs(p[0] - 30) < 40 and abs(p[1] - 90) < 40 and abs(p[2] - 216) < 40)
red = sum(1 for p in px if abs(p[0] - 181) < 40 and abs(p[1] - 67) < 40 and abs(p[2] - 46) < 40)
dark = sum(1 for p in px if sum(p) < 200)
print("blue", blue, "red", red, "dark", dark)

# text width checks
F = "C:/Windows/Fonts/"
S = 2
sub = ImageFont.truetype(F + "msyh.ttc", 13 * S)
title = ImageFont.truetype(F + "msyhbd.ttc", 34 * S)
desc = ImageFont.truetype(F + "msyh.ttc", 13 * S)
foot = ImageFont.truetype(F + "msyh.ttc", 11 * S)
print("title w", round(title.getlength("文与据：把「出处」画进图标") / S, 1), "limit 1336")
print("sub w", round(sub.getlength("这个产品真正独有的结构，不是「研究」而是「每一句话都挂着它的出处」。图标就画这个结构，再按尺寸做减法。") / S, 1), "limit 1336")
texts = [
    "正文三行，页边一枚角标，一条引线把这句话与它的出处连起来。档案感来自结构，不来自纸纹。",
    "角标挂在最后一行句末，就是报告里论断与引用编号的关系；它同时是界面里的引用组件。",
    "只留两行与一枚角标：16px 下没有细线、没有小字，仍然分得清「文」和「据」。",
]
for t in texts:
    lines, cur = [], ""
    for ch in t:
        if desc.getlength(cur + ch) / S > 378 and cur:
            lines.append(cur); cur = ch
        else:
            cur += ch
    if cur: lines.append(cur)
    print(len(lines), [round(desc.getlength(l) / S, 1) for l in lines])
print("footer left w", round(foot.getlength("被否掉的隐喻：放大镜（那是搜索）· 火花（AI 默认脸）· 对勾文档与印章（暗示背书，和「不承诺」的口径冲突）") / S, 1))
print("footer right w", round(foot.getlength("16px 可辨 · 单色可用 · 不依赖渐变、细线与阴影") / S, 1))
