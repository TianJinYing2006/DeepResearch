from pathlib import Path
from PIL import Image, ImageFont

HERE = Path(__file__).parent
img = Image.open(HERE / "sheet-v2.png").convert("RGB")
print("size", img.size)

PAL = {
    "B": (30, 90, 216),
    "#": (23, 27, 25),
    "X": (181, 67, 46),
    ".": (228, 230, 223),
    "_": (251, 250, 247),
    " ": (238, 239, 234),
    ",": (94, 104, 98),
    "o": (138, 147, 141),
}


def classify(p):
    return min(PAL.items(), key=lambda kv: sum((p[i] - kv[1][i]) ** 2 for i in range(3)))[0]


cols, rows = 116, 74
small = img.resize((cols, rows), Image.BOX)
for y in range(rows):
    print("".join(classify(small.getpixel((x, y))) for x in range(cols)))

px = img.load()
blue = red = dark = 0
for y in range(0, img.height, 2):
    for x in range(0, img.width, 2):
        p = px[x, y]
        if abs(p[0] - 30) < 40 and abs(p[1] - 90) < 40 and abs(p[2] - 216) < 40:
            blue += 1
        if abs(p[0] - 181) < 40 and abs(p[1] - 67) < 40 and abs(p[2] - 46) < 40:
            red += 1
        if sum(p) < 200:
            dark += 1
print("blue", blue, "red", red, "dark", dark)

F = "C:/Windows/Fonts/"
S = 2
checks = [
    ("h1", "msyhbd.ttc", 56, "一页，一据。", 1400),
    ("sub", "msyh.ttc", 14, "页是报告，签是出处。图标不做「研究」的比喻，只做「考据」的结构。", 1400),
    ("lead", "simfang.ttf", 25, "不做「研究」的比喻，只做「考据」的结构。", 828),
    ("p1", "msyh.ttc", 13, "报告是主角：纸页、页边、行文；页角留一个折痕，说明它是被翻过的。", 758),
    ("p2", "msyh.ttc", 13, "出处挂在行末、探出页边；一条细线，把这一句和它的来源连住。", 758),
    ("p3", "msyh.ttc", 13, "签的颜色就是校验状态：蓝 = 已核验，空 = 待复核，朱 = 未通过。", 758),
    ("state-note", "msyh.ttc", 13, "引签在报告里就是引用组件：一枚签，同时是图标、角标和校验状态。", 1000),
]
for name, f, size, text, limit in checks:
    w = ImageFont.truetype(F + f, size * S).getlength(text) / S
    print(name, round(w, 1), "limit", limit)

# palette row width
f_cap = ImageFont.truetype(F + "msyh.ttc", 10.5 * S)
px = 72
for label in ["墨  #171B19", "纸  #FBFAF7", "签  #1E5AD8", "朱  #B5432E"]:
    px += 26 + f_cap.getlength(label) / S + 34
print("palette end", round(px - 34, 1), "limit 1528")

# lockup width
f_serif = ImageFont.truetype(F + "simfang.ttf", 58 * S)
print("wordmark w", round(f_serif.getlength("爱考据") / S, 1))
