#!/usr/bin/env python3
"""Drop virtual symbols from a per-part BOM CSV, in place.

kicad-cli leaves out references starting with "#" (power symbols), but a PWR_FLAG
annotated as FLG1 (no "#") ends up in the BOM like a real part, asking for a
footprint, MPN and datasheet. Those rows are removed here.

Usage: bom_virtual.py <per-part BOM CSV>...
"""
import csv
import sys

VIRTUAL_VALUES = {"PWR_FLAG"}

for path in sys.argv[1:]:
    with open(path, newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        fields = reader.fieldnames
        rows = list(reader)
    kept = [r for r in rows if (r.get("Value") or "").strip().upper() not in VIRTUAL_VALUES
            and not (r.get("Reference") or "").startswith("#")]
    if len(kept) != len(rows):
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(kept)
