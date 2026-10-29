#!/usr/bin/env python3
"""Assembly files in a fab's own column layout, from the generic BOM and pick & place.

The profile (fab-profiles/<name>/profile.json) maps the fab's column names to the
generic ones. A profile without an "assembly" section makes no files.

Usage: fab_assembly.py <profile.json> <generic BOM> <generic CPL> <output dir> <file prefix>
"""
import csv
import json
import os
import sys

profile_path, bom_in, cpl_in, out_dir, prefix = sys.argv[1:6]
asm = json.load(open(profile_path, encoding="utf-8")).get("assembly")
if not asm:
    sys.exit(0)
os.makedirs(out_dir, exist_ok=True)


def convert(src, spec, layer_names=None):
    with open(src, newline="", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    with open(os.path.join(out_dir, spec["file"].format(prefix=prefix)), "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, quoting=csv.QUOTE_MINIMAL)
        w.writerow(spec["columns"])
        for r in rows:
            row = [(r.get(src_col) or "").strip() for src_col in spec["columns"].values()]
            if layer_names and "Side" in spec["columns"].values():
                i = list(spec["columns"].values()).index("Side")
                row[i] = layer_names.get(row[i].lower(), row[i])
            w.writerow(row)


convert(bom_in, asm["bom"])
convert(cpl_in, asm["cpl"], asm["cpl"].get("layer_names"))
