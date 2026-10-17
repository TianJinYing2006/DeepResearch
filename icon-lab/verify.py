from pathlib import Path
from PIL import Image

HERE = Path(__file__).parent
img = Image.open(HERE / "contact-sheet.png").convert("RGB")
W, H = img.size
print("size", img.size)

PAL = {
    "R": (194, 69, 45),
    "#": (35, 49, 58),
    ".": (243, 241, 234),
    "_": (251, 250, 247),
    " ": (236, 233, 226),
    "+": (174, 185, 199),
    "o": (138, 151, 162),
    ",": (97, 112, 123),
}


def classify(p):
    return min(PAL.items(), key=lambda kv: sum((p[i] - kv[1][i]) ** 2 for i in range(3)))[0]


cols, rows = 118, 60
small = img.resize((cols, rows), Image.BOX)
for y in range(rows):
    print("".join(classify(small.getpixel((x, y))) for x in range(cols)))

# element checks
red = sum(1 for p in img.getdata() if abs(p[0] - 194) < 40 and abs(p[1] - 69) < 40 and abs(p[2] - 45) < 40)
ink = sum(1 for p in img.getdata() if sum(p) < 260)
print("red px", red, "inkish px", ink)
