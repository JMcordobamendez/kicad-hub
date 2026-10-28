#!/usr/bin/env python3
"""Pick & place for one build (default or a variant): the parts of the full CPL that
are in this build's per-part BOM and not marked DNP there. Parts excluded from the
BOM aren't assembled, so they are left out too.

DNP is taken from the schematic (per-variant BOM) instead of `kicad-cli pcb export
pos --exclude-dnp --variant`, which reads it from the PCB and so is only right after
the PCB has been updated from the schematic.

Usage: variant_cpl.py <per-part BOM of the build> <full CPL (no DNP filter)> <CPL out>
"""
import csv
import sys

bom, cpl_in, cpl_out = sys.argv[1:4]

with open(bom, newline="", encoding="utf-8-sig") as f:
    fitted = {r["Reference"] for r in csv.DictReader(f) if not (r.get("DNP") or "").strip()}
with open(cpl_in, newline="", encoding="utf-8-sig") as f:
    reader = csv.DictReader(f)
    rows = [r for r in reader if r["Ref"] in fitted]
    fields = reader.fieldnames
with open(cpl_out, "w", newline="", encoding="utf-8") as f:
    w = csv.DictWriter(f, fieldnames=fields)
    w.writeheader()
    w.writerows(rows)
