"""Minimal SVG renderer for the icon-contact-sheet subset (rect/circle/path, strokes, translate)."""
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from PIL import Image, ImageDraw

NS = "{http://www.w3.org/2000/svg}"
COLORS = {"#FBFAF7": (251, 250, 247), "#23313A": (35, 49, 58),
          "#C2452D": (194, 69, 45), "#AEB9C7": (174, 185, 199)}


def parse_color(value):
    if value is None or value == "none":
        return None
    value = value.strip()
    if value.startswith("#"):
        h = value[1:]
        if len(h) == 3:
            h = "".join(c * 2 for c in h)
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    return None


def parse_path(d, scale, ox, oy):
    tokens = re.findall(r"[MLHVQZ]|-?\d*\.?\d+(?:e[-+]?\d+)?", d)
    pts, i = [], 0
    cur = (0.0, 0.0)
    start = (0.0, 0.0)
    cmd = None
    while i < len(tokens):
        t = tokens[i]
        if t.isalpha():
            cmd = t
            i += 1
            if cmd == "Z":
                cur = start
                pts.append(start)
            continue
        if cmd in ("M", "L", "Q"):
            n = 2 if cmd in ("M", "L") else 4
            vals = [float(x) for x in tokens[i:i + n]]
            i += n
            if cmd == "M":
                cur = (vals[0], vals[1])
                start = cur
                pts.append(cur)
                cmd = "L"
            elif cmd == "L":
                cur = (vals[0], vals[1])
                pts.append(cur)
            else:
                p0 = cur
                p1 = (vals[0], vals[1])
                p2 = (vals[2], vals[3])
                for k in range(1, 17):
                    u = k / 16
                    x = (1 - u) ** 2 * p0[0] + 2 * (1 - u) * u * p1[0] + u ** 2 * p2[0]
                    y = (1 - u) ** 2 * p0[1] + 2 * (1 - u) * u * p1[1] + u ** 2 * p2[1]
                    pts.append((x, y))
                cur = p2
        elif cmd == "H":
            v = float(tokens[i]); i += 1
            cur = (v, cur[1]); pts.append(cur)
        elif cmd == "V":
            v = float(tokens[i]); i += 1
            cur = (cur[0], v); pts.append(cur)
        else:
            i += 1
    return [((x + ox) * scale, (y + oy) * scale) for x, y in pts]


def render(svg_path, size=512, supersample=4):
    root = ET.parse(svg_path).getroot()
    scale = size * supersample / 512
    img = Image.new("RGBA", (size * supersample, size * supersample), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    def draw_node(node, ox=0.0, oy=0.0, inherit=None):
        inherit = inherit or {}
        tag = node.tag.replace(NS, "")
        transform = node.get("transform")
        if transform:
            m = re.search(r"translate\(\s*(-?[\d.]+)[ ,]+(-?[\d.]+)\s*\)", transform)
            if m:
                ox += float(m.group(1)); oy += float(m.group(2))
        fill = parse_color(node.get("fill")) if node.get("fill") is not None else inherit.get("fill")
        stroke = parse_color(node.get("stroke")) if node.get("stroke") is not None else inherit.get("stroke")
        sw = float(node.get("stroke-width", inherit.get("sw", 0))) * scale
        cap = node.get("stroke-linecap", inherit.get("cap", "butt"))
        child_inherit = {"fill": fill, "stroke": stroke, "sw": sw / scale, "cap": cap}
        if tag in ("g", "svg"):
            for child in node:
                draw_node(child, ox, oy, child_inherit)
            return
        if tag == "rect":
            x = (float(node.get("x", 0)) + ox) * scale
            y = (float(node.get("y", 0)) + oy) * scale
            w = float(node.get("width")) * scale
            h = float(node.get("height")) * scale
            r = float(node.get("rx", 0)) * scale
            if fill:
                draw.rounded_rectangle([x, y, x + w, y + h], radius=r, fill=fill)
            if stroke:
                draw.rounded_rectangle([x, y, x + w, y + h], radius=r, outline=stroke, width=int(round(sw)))
            return
        if tag == "circle":
            cx = (float(node.get("cx")) + ox) * scale
            cy = (float(node.get("cy")) + oy) * scale
            r = float(node.get("r")) * scale
            if fill:
                draw.ellipse([cx - r, cy - r, cx + r, cy + r], fill=fill)
            if stroke:
                ro = r + sw / 2
                draw.ellipse([cx - ro, cy - ro, cx + ro, cy + ro], outline=stroke, width=int(round(sw)))
            return
        if tag == "path":
            pts = parse_path(node.get("d"), scale, ox, oy)
            if fill and len(pts) > 2:
                draw.polygon(pts, fill=fill)
            if stroke and len(pts) > 1:
                draw.line(pts, fill=stroke, width=int(round(sw)), joint="curve")
                if cap == "round":
                    for (x, y) in (pts[0], pts[-1]):
                        draw.ellipse([x - sw / 2, y - sw / 2, x + sw / 2, y + sw / 2], fill=stroke)
            return

    draw_node(root)
    img = img.resize((size, size), Image.LANCZOS)
    return img


PALETTE = [((251, 250, 247), " "), ((35, 49, 58), "#"), ((194, 69, 45), "R"), ((174, 185, 199), "+"),
           ((30, 90, 216), "B"), ((181, 67, 46), "X"), ((244, 246, 243), " ")]


def ascii_map(img, n=32, bg=(236, 233, 226)):
    flat = Image.new("RGB", img.size, bg)
    flat.paste(img, (0, 0), img)
    img = flat.resize((n, n), Image.LANCZOS)
    rows = []
    for y in range(n):
        row = ""
        for x in range(n):
            p = img.getpixel((x, y))
            best = min(PALETTE, key=lambda c: sum((p[i] - c[0][i]) ** 2 for i in range(3)))
            row += best[1]
        rows.append(row)
    return "\n".join(rows)


if __name__ == "__main__":
    for name in ["a-lens", "b-confluence", "c-citation"]:
        svg = Path(__file__).parent / "concepts" / f"{name}.svg"
        img = render(svg, 512, 4)
        img.save(Path(__file__).parent / f"_{name}.png")
        print("\n===", name, "32px ===")
        print(ascii_map(img, 32))
        print("\n--- 16px ---")
        print(ascii_map(img, 16))
