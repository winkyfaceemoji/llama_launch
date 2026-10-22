"""Draw the launcher's icon (a llama head) and save it as icon.ico next to launcher.pyw.

    python dev/make_icon.py

The window, the taskbar and the tray all load icon.ico. Colours come from the dark
palette in launcher_app/theme.py, whatever the theme: a dark square reads on a light and a dark
taskbar. Each size is drawn large and scaled down, so it can be regenerated at will.
"""
import os
import sys

from PIL import Image, ImageChops, ImageDraw

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
SIZES = (16, 20, 24, 32, 40, 48, 64, 256)  # 100% to 300% scaling, plus Explorer's large view
S = 512  # drawing size; shapes are given in 0..1 units


def load_palette():
    sys.path.insert(0, ROOT)  # the launcher_app package and the agents/ it imports
    from launcher_app.theme import PALETTES
    return PALETTES["dark"]


def u(*pts):
    return [(x * S, y * S) for x, y in pts]


def draw(p):
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    inner = [S * 0.04, S * 0.04, S * 0.96 - 1, S * 0.96 - 1]
    d.rounded_rectangle([0, 0, S - 1, S - 1], radius=S * 0.22, fill=p["BORDER"])  # edge for dark taskbars
    d.rounded_rectangle(inner, radius=S * 0.19, fill=p["BG_INPUT"])

    # Llama head and neck, side view facing left. The neck runs off the bottom,
    # so the shape is clipped to the inner square.
    shape = Image.new("L", (S, S), 0)
    f = ImageDraw.Draw(shape)
    # Bold on purpose: at 16 and 24 px thinner ears ran together.
    f.rounded_rectangle(u((0.46, 0.38), (0.76, 1.10)), radius=S * 0.12, fill=255)  # neck
    f.ellipse(u((0.34, 0.24), (0.76, 0.54)), fill=255)                              # head
    f.rounded_rectangle(u((0.12, 0.33), (0.50, 0.53)), radius=S * 0.10, fill=255)  # snout
    f.rounded_rectangle(u((0.39, 0.06), (0.51, 0.34)), radius=S * 0.06, fill=255)  # ears
    f.rounded_rectangle(u((0.61, 0.06), (0.73, 0.34)), radius=S * 0.06, fill=255)
    f.ellipse(u((0.45, 0.32), (0.53, 0.40)), fill=0)                                # eye
    clip = Image.new("L", (S, S), 0)
    ImageDraw.Draw(clip).rounded_rectangle(inner, radius=S * 0.19, fill=255)
    img.paste(p["ACCENT"], (0, 0), ImageChops.multiply(shape, clip))
    return img


def main():
    big = draw(load_palette())
    frames = [big.resize((n, n), Image.LANCZOS) for n in SIZES]
    out = os.path.join(ROOT, "icon.ico")
    # append_images keeps each frame as drawn; without it Pillow rescales the largest.
    frames[-1].save(out, sizes=[(n, n) for n in SIZES], append_images=frames[:-1])
    print("wrote", os.path.normpath(out))


if __name__ == "__main__":
    main()
