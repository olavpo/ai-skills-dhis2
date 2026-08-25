#!/usr/bin/env python3
"""Trim empty background from a DHIS2 app screenshot and upscale it.

App screenshots are mostly empty canvas around a small table, which reads badly
as a Jira attachment. This crops to the content and doubles the size so the
figures stay legible after Jira's own scaling.

Examples:
    python crop.py raw.png out.png
    python crop.py raw.png out.png --left 232 --top 64 --right 1010
    python crop.py raw.png out.png --scale 1
"""
import argparse

from PIL import Image


def main():
    p = argparse.ArgumentParser()
    p.add_argument("src")
    p.add_argument("dst")
    p.add_argument("--left", type=int, default=232,
                   help="cut the app's left navigation panel, 0 to keep it")
    p.add_argument("--top", type=int, default=64, help="cut the blue header bar")
    p.add_argument("--right", type=int, default=0,
                   help="hard right edge; 0 means full width. Set this to drop the "
                        "full-width layout bar and keep the crop narrow")
    p.add_argument("--bottom", type=int, default=0, help="hard bottom edge, 0 means full height")
    p.add_argument("--scale", type=int, default=2)
    p.add_argument("--pad", type=int, default=10)
    a = p.parse_args()

    im = Image.open(a.src).convert("RGB")
    right = a.right or im.width
    bottom = a.bottom or im.height
    bg = im.getpixel((im.width - 40, im.height - 40))

    reg = im.crop((a.left, a.top, right, bottom))
    px = reg.load()
    xr = range(0, reg.width, 3)
    yr = range(0, reg.height, 2)

    # A column that is non-background for almost its whole height is app chrome
    # (panel edge, scrollbar, divider), not content. Counting it would stretch the
    # crop to the full window height, so drop those columns first.
    col_hits = {x: sum(1 for y in yr if px[x, y] != bg) for x in xr}
    content_cols = [x for x, n in col_hits.items() if 0 < n < 0.9 * len(yr)]
    if not content_cols:
        raise SystemExit("no content found; check --left/--top/--right")

    hits = [(x, y) for y in yr for x in content_cols if px[x, y] != bg]
    xs = [h[0] for h in hits]
    ys = [h[1] for h in hits]
    x0 = max(min(xs) - a.pad, 0)
    x1 = min(max(xs) + a.pad + 3, reg.width)
    y0 = max(min(ys) - a.pad, 0)
    y1 = min(max(ys) + a.pad + 2, reg.height)

    out = reg.crop((x0, y0, x1, y1))
    if a.scale != 1:
        out = out.resize((out.width * a.scale, out.height * a.scale), Image.LANCZOS)
    out.save(a.dst)
    print(a.dst, out.size)


if __name__ == "__main__":
    main()
