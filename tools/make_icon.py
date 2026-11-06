"""Draws the Padthru icon (a controller with a pass-through arrow) at any size.

    python tools/make_icon.py
writes tv/icon.png (512 px), pc/assets/padthru.png (256 px) and pc/assets/padthru.ico.
"""
import os

from PIL import Image, ImageDraw

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def draw(size):
    s = size / 512.0
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    # (glyphs are drawn on their own layer below)
    # background tile with a soft vertical gradient
    top, bottom = (36, 99, 235), (22, 32, 76)
    tile = Image.new("RGBA", (size, size))
    td = ImageDraw.Draw(tile)
    for y in range(size):
        t = y / max(size - 1, 1)
        td.line([(0, y), (size, y)], fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)) + (255,))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(112 * s), fill=255)
    img.paste(tile, (0, 0), mask)

    fg = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(fg)
    white = (255, 255, 255, 255)
    accent = (36, 99, 235, 255)
    # controller body: two grips joined by a bar
    d.rounded_rectangle((96 * s, 176 * s, 416 * s, 316 * s), radius=int(70 * s), fill=white)
    d.ellipse((92 * s, 220 * s, 212 * s, 380 * s), fill=white)
    d.ellipse((300 * s, 220 * s, 420 * s, 380 * s), fill=white)
    # d-pad
    d.rounded_rectangle((136 * s, 232 * s, 196 * s, 254 * s), radius=int(6 * s), fill=accent)
    d.rounded_rectangle((155 * s, 213 * s, 177 * s, 273 * s), radius=int(6 * s), fill=accent)
    # face buttons
    for cx, cy in ((356, 216), (382, 243), (330, 243), (356, 270)):
        d.ellipse(((cx - 13) * s, (cy - 13) * s, (cx + 13) * s, (cy + 13) * s), fill=accent)
    # pass-through arrow under the controller
    d.rounded_rectangle((150 * s, 404 * s, 330 * s, 428 * s), radius=int(12 * s), fill=white)
    d.polygon([(318 * s, 380 * s), (372 * s, 416 * s), (318 * s, 452 * s)], fill=white)
    img.alpha_composite(fg, dest=(0, -int(40 * s)))
    return img


def main():
    os.makedirs(os.path.join(ROOT, "pc", "assets"), exist_ok=True)
    draw(512).save(os.path.join(ROOT, "tv", "icon.png"))
    draw(256).save(os.path.join(ROOT, "pc", "assets", "padthru.png"))
    big = draw(256)
    big.save(os.path.join(ROOT, "pc", "assets", "padthru.ico"),
             sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("icons written")


if __name__ == "__main__":
    main()
