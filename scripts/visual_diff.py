#!/usr/bin/env python3
"""Before/after images of the schematic sheets and PCB layers changed in a pull request.

Takes two folders of SVGs exported by kicad-cli with the same options (PR base and
this commit), rasterizes them with rsvg-convert and compares them pixel by pixel.
For every page that changed it writes:

  <kind>-<page>.png       Before | After, cropped to the changes: unchanged parts faded,
                          removed in red (before), added in green (after), each changed
                          area boxed in orange
  <kind>-<page>-full.png  the whole page as one overlay, same colours

and a JSON list of {kind, page, title, image, full} for the report. Pages that didn't
change are left out. Runs inside the KiBot image (rsvg-convert, PIL and numpy).

Usage: visual_diff.py --kind sch|pcb --base DIR --head DIR --prefix NAME --out DIR --json FILE
"""
import argparse
import glob
import json
import os
import subprocess
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

ap = argparse.ArgumentParser()
ap.add_argument("--kind", required=True, choices=("sch", "pcb"))
ap.add_argument("--base", required=True, help="SVGs of the PR base")
ap.add_argument("--head", required=True, help="SVGs of this commit")
ap.add_argument("--prefix", default="", help="file name prefix to strip for the page title (project name)")
ap.add_argument("--out", required=True)
ap.add_argument("--json", required=True, help="JSON list to append the changed pages to")
args = ap.parse_args()

DPI = {"sch": 150, "pcb": 300}[args.kind]  # PCB features are small: higher resolution
THRESHOLD = 48       # per-channel difference that counts as a change (ignores antialiasing noise)
CELL = 16            # px; changes closer than a few cells are boxed together
MARGIN = 80          # px around the changed area in the cropped image
PANEL_MAX = 900      # px; max width of each half of the before/after image
PANEL_MIN = 700      # px; narrower crops are zoomed in
MIN_CROP = {"sch": (500, 350), "pcb": (300, 210)}[args.kind]  # px around a single small change
REMOVED, ADDED, BOX = (220, 30, 30), (0, 150, 60), (255, 140, 0)


def raster(svg, tmp):
    png = os.path.join(tmp, os.path.basename(svg) + ".png")
    subprocess.run(["rsvg-convert", "-b", "white", "-d", str(DPI), "-p", str(DPI), "-o", png, svg], check=True)
    return np.asarray(Image.open(png).convert("RGB"), dtype=np.int16)


def same_size(a, b):
    h, w = max(a.shape[0], b.shape[0]), max(a.shape[1], b.shape[1])
    pad = lambda x: np.pad(x, ((0, h - x.shape[0]), (0, w - x.shape[1]), (0, 0)), constant_values=255)
    return pad(a), pad(b)


def boxes(mask):
    """Bounding boxes (x0, y0, x1, y1) of groups of nearby changed pixels."""
    h, w = mask.shape
    gh, gw = -(-h // CELL), -(-w // CELL)
    m = np.zeros((gh * CELL, gw * CELL), bool)
    m[:h, :w] = mask
    grid = m.reshape(gh, CELL, gw, CELL).any(axis=(1, 3))
    near = grid.copy()  # grow by 2 cells so close changes end up in one box
    for dy in (-2, -1, 0, 1, 2):
        for dx in (-2, -1, 0, 1, 2):
            near |= np.roll(np.roll(grid, dy, 0), dx, 1)
    seen = np.zeros_like(near)
    out = []
    for y, x in zip(*np.nonzero(near)):
        if seen[y, x]:
            continue
        stack, cells = [(y, x)], []
        seen[y, x] = True
        while stack:
            cy, cx = stack.pop()
            cells.append((cy, cx))
            for ny, nx in ((cy + 1, cx), (cy - 1, cx), (cy, cx + 1), (cy, cx - 1)):
                if 0 <= ny < gh and 0 <= nx < gw and near[ny, nx] and not seen[ny, nx]:
                    seen[ny, nx] = True
                    stack.append((ny, nx))
        real = [(cy, cx) for cy, cx in cells if grid[cy, cx]]
        if real:
            ys, xs = zip(*real)
            out.append((min(xs) * CELL, min(ys) * CELL, min((max(xs) + 1) * CELL, w), min((max(ys) + 1) * CELL, h)))
    return out


def fade(img, keep, amount=0.7):
    """Blend everything except `keep` towards white so the changes stand out."""
    out = img.astype(np.float32)
    out[~keep] = out[~keep] + (255 - out[~keep]) * amount
    return out.astype(np.uint8)


def draw_boxes(im, bxs, offset=(0, 0), width=3):
    d = ImageDraw.Draw(im)
    for x0, y0, x1, y1 in bxs:
        d.rectangle((x0 - offset[0] - 6, y0 - offset[1] - 6, x1 - offset[0] + 6, y1 - offset[1] + 6),
                    outline=BOX, width=width)


def font(size):
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1
        return ImageFont.load_default()


def page_title(stem):
    p = stem[len(args.prefix):].lstrip("-_") if args.prefix and stem.startswith(args.prefix) else stem
    if args.kind == "pcb":
        return p.replace("_", ".")  # F_Cu -> F.Cu
    return p or "root sheet"


def compare(stem, base_svg, head_svg, tmp):
    a = raster(base_svg, tmp) if base_svg else None
    b = raster(head_svg, tmp) if head_svg else None
    if a is None:
        a = np.full_like(b, 255)
    if b is None:
        b = np.full_like(a, 255)
    a, b = same_size(a, b)
    changed = np.abs(a - b).max(axis=2) > THRESHOLD
    if not changed.any():
        return None
    removed = changed & (a.min(axis=2) < 230)  # ink only in the base
    added = changed & (b.min(axis=2) < 230)    # ink only in this commit
    bxs = boxes(changed)
    h, w = changed.shape

    # Whole page overlay, cropped to the drawing (base or head ink)
    ink = (a.min(axis=2) < 230) | (b.min(axis=2) < 230)
    ys, xs = np.nonzero(ink)
    fx0, fy0, fx1, fy1 = max(xs.min() - 20, 0), max(ys.min() - 20, 0), min(xs.max() + 20, w), min(ys.max() + 20, h)
    full = np.full((h, w, 3), 255, np.uint8)
    full[ink] = (190, 190, 190)
    full[removed] = REMOVED
    full[added] = ADDED
    full_im = Image.fromarray(full)
    draw_boxes(full_im, bxs, width=4)
    full_im = full_im.crop((fx0, fy0, fx1, fy1))

    # Before | After, cropped to the changes
    x0, y0 = min(bx[0] for bx in bxs), min(bx[1] for bx in bxs)
    x1, y1 = max(bx[2] for bx in bxs), max(bx[3] for bx in bxs)
    cw, ch = max(x1 - x0 + 2 * MARGIN, MIN_CROP[0]), max(y1 - y0 + 2 * MARGIN, MIN_CROP[1])
    cx, cy = (x0 + x1) // 2, (y0 + y1) // 2
    cx0, cy0 = min(max(cx - cw // 2, 0), max(w - cw, 0)), min(max(cy - ch // 2, 0), max(h - ch, 0))
    crop = (cx0, cy0, min(cx0 + cw, w), min(cy0 + ch, h))

    before = fade(a, removed)
    before[removed] = REMOVED
    after = fade(b, added)
    after[added] = ADDED
    panels = []
    for arr, label in ((before, "Before"), (after, "After")):
        im = Image.fromarray(arr)
        draw_boxes(im, bxs)
        im = im.crop(crop)
        # small changes are zoomed in (up to 3x), large crops scaled down
        scale = min(PANEL_MAX / im.width, 3.0) if im.width < PANEL_MIN else min(PANEL_MAX / im.width, 1.0)
        if scale != 1.0:
            im = im.resize((round(im.width * scale), round(im.height * scale)), Image.LANCZOS)
        panels.append((im, label))
    pw, ph = panels[0][0].size
    bar, gap = 34, 12
    comp = Image.new("RGB", (2 * pw + gap, ph + bar), "white")
    d = ImageDraw.Draw(comp)
    for i, (im, label) in enumerate(panels):
        x = i * (pw + gap)
        comp.paste(im, (x, bar))
        d.rectangle((x, bar, x + pw - 1, bar + ph - 1), outline=(160, 160, 160))
        d.text((x + 4, 6), label, fill=(REMOVED if i == 0 else ADDED), font=font(22))

    name = f"{args.kind}-{stem}"
    comp.save(os.path.join(args.out, name + ".png"), optimize=True)
    full_im.save(os.path.join(args.out, name + "-full.png"), optimize=True)
    return {"kind": args.kind, "page": stem, "title": page_title(stem), "areas": len(bxs),
            "image": name + ".png", "full": name + "-full.png",
            "status": "added" if base_svg is None else "removed" if head_svg is None else "changed"}


os.makedirs(args.out, exist_ok=True)
base = {os.path.splitext(os.path.basename(p))[0]: p for p in glob.glob(os.path.join(args.base, "*.svg"))}
head = {os.path.splitext(os.path.basename(p))[0]: p for p in glob.glob(os.path.join(args.head, "*.svg"))}
results = json.load(open(args.json, encoding="utf-8")) if os.path.exists(args.json) else []
with tempfile.TemporaryDirectory() as tmp:
    for stem in sorted(set(base) | set(head)):
        r = compare(stem, base.get(stem), head.get(stem), tmp)
        if r:
            results.append(r)
            print(f"{r['kind']} {r['title']}: {r['status']}, {r['areas']} area(s)")
with open(args.json, "w", encoding="utf-8") as f:
    json.dump(results, f, indent=1)
