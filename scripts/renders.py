#!/usr/bin/env python3
"""Full-page renders of every schematic sheet and PCB layer, for the portal's compare page.

Takes a folder of SVGs exported by kicad-cli (same options as the visual diff: PCB in
black and white on the board's page, no drawing sheet) and rasterizes each one with
rsvg-convert. Unlike visual_diff.py nothing here depends on the content: no crop, one
fixed resolution per kind, so the renders of any two builds are pixel-comparable and the
browser can diff them (compare.html). The page size is the sheet's / board's page size,
so it only changes when the design's page setting changes.

Writes <kind>-<page>.png for each page and appends them to renders.json:
  {"version": 1, "pages": [{kind, key, title, file, w, h, hash}]}
`key` (kind + title) is what the compare page uses to match a page across builds; `hash`
is the PNG's SHA-1, equal for pages that didn't change. Runs inside the KiBot image
(rsvg-convert, PIL and numpy).

Usage: renders.py --kind sch|pcb --svg DIR --prefix NAME --out DIR
"""
import argparse
import glob
import hashlib
import json
import os
import re
import subprocess
import tempfile

from PIL import Image

ap = argparse.ArgumentParser()
ap.add_argument("--kind", required=True, choices=("sch", "pcb"))
ap.add_argument("--svg", required=True, help="SVGs exported by kicad-cli")
ap.add_argument("--prefix", default="", help="project name, stripped from the file names to get the page title")
ap.add_argument("--out", required=True, help="folder for the PNGs and renders.json")
args = ap.parse_args()

# Same resolution as visual_diff.py for the raster, then one fixed reduction so a page
# stays a few hundred KB. Both are constants per kind: never derived from the content.
RENDER_DPI = {"sch": 150, "pcb": 300}[args.kind]
OUT_DPI = {"sch": 150, "pcb": 200}[args.kind]
COLORS = 256         # palette size of schematic renders (PCB renders are grey)


def page_title(stem):
    """Same titles as visual_diff.py, so a page has the same name in both."""
    p = stem[len(args.prefix):].lstrip("-_") if args.prefix and stem.startswith(args.prefix) else stem
    if args.kind == "pcb":
        return p.replace("_", ".")  # F_Cu -> F.Cu
    return p or "root sheet"


def rank(kind, title):
    """Order in the page list: root sheet first / copper F.Cu … B.Cu, silkscreen, outline."""
    if kind == "sch":
        return (title != "root sheet", title.lower())
    m = re.match(r"In(\d+)\.Cu$", title)
    n = 0 if title == "F.Cu" else int(m.group(1)) if m else 1000 if title == "B.Cu" else None
    if n is None:
        n = {"F.Silkscreen": 2000, "B.Silkscreen": 2001, "Edge.Cuts": 3000}.get(title, 4000)
    return (n, title.lower())


def render(svg, tmp):
    png = os.path.join(tmp, "page.png")
    subprocess.run(["rsvg-convert", "-b", "white", "-d", str(RENDER_DPI), "-p", str(RENDER_DPI), "-o", png, svg], check=True)
    im = Image.open(png).convert("RGB")
    if OUT_DPI != RENDER_DPI:
        k = OUT_DPI / RENDER_DPI
        im = im.resize((max(1, round(im.width * k)), max(1, round(im.height * k))), Image.LANCZOS)
    if args.kind == "pcb":
        return im.convert("L")  # black and white export: 8-bit grey is all there is
    return im.quantize(colors=COLORS, method=Image.MEDIANCUT, dither=Image.NONE)


os.makedirs(args.out, exist_ok=True)
index_path = os.path.join(args.out, "renders.json")
index = json.load(open(index_path, encoding="utf-8")) if os.path.exists(index_path) else {"version": 1, "pages": []}
index["dpi"] = {**index.get("dpi", {}), args.kind: OUT_DPI}
pages = []
with tempfile.TemporaryDirectory() as tmp:
    for svg in glob.glob(os.path.join(args.svg, "*.svg")):
        stem = os.path.splitext(os.path.basename(svg))[0]
        title = page_title(stem)
        file = f"{args.kind}-{re.sub(r'[^A-Za-z0-9._-]', '_', title)}.png"
        try:
            im = render(svg, tmp)
        except (subprocess.CalledProcessError, OSError) as e:
            print(f"{args.kind} {title}: render failed ({e})")
            continue
        path = os.path.join(args.out, file)
        im.save(path, optimize=True)
        data = open(path, "rb").read()
        pages.append({"kind": args.kind, "key": f"{args.kind}:{title}", "title": title, "file": file,
                      "w": im.width, "h": im.height, "hash": hashlib.sha1(data).hexdigest()})
        print(f"{args.kind} {title}: {im.width}x{im.height}, {len(data) // 1024} KB")
index["pages"] = [p for p in index["pages"] if p["kind"] != args.kind] + pages
index["pages"].sort(key=lambda p: (p["kind"] != "sch",) + rank(p["kind"], p["title"]))
with open(index_path, "w", encoding="utf-8") as f:
    json.dump(index, f, separators=(",", ":"))
