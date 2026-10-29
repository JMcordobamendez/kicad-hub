#!/usr/bin/env python3
"""Fabrication notes (README.txt) for the fab package, read from the board itself.

Runs with KiCad's Python (pcbnew), so the numbers don't depend on the UI language.
Usage: fabnotes.py <board.kicad_pcb> <revision> <output README.txt> [board.json]
The optional JSON has the same measurements for later steps (requirements.py).
"""
import datetime
import json
import os
import re
import sys

import pcbnew

pcb_path, rev, out = sys.argv[1:4]
b = pcbnew.LoadBoard(pcb_path)
mm = pcbnew.ToMM
ds = b.GetDesignSettings()
tb = b.GetTitleBlock()

bbox = b.GetBoardEdgesBoundingBox()
tracks = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_TRACE_T]
vias = [t for t in b.GetTracks() if t.Type() == pcbnew.PCB_VIA_T]
pads = [p for f in b.GetFootprints() for p in f.Pads()]
pth = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_PTH]
npth = [p for p in pads if p.GetAttribute() == pcbnew.PAD_ATTRIB_NPTH]
pad_holes = [mm(min(p.GetDrillSize().x, p.GetDrillSize().y)) for p in pth]


def fmt(v, unit="mm"):
    return "n/a" if v is None else f"{v:.5f}".rstrip("0").rstrip(".") + f" {unit}"


def smallest(values):
    values = [v for v in values if v > 0]
    return min(values) if values else None


# The stackup isn't exposed to Python: read it from the (stable) s-expression file
text = open(pcb_path, encoding="utf-8").read()
start = text.find("(stackup")
stack = ""
if start != -1:
    depth = 0
    for i in range(start, len(text)):
        depth += {"(": 1, ")": -1}.get(text[i], 0)
        if depth == 0:
            stack = text[start:i + 1]
            break


def opt(key, default=None):
    m = re.search(rf'\({key} "?([^")]+)"?\)', stack)
    return m.group(1) if m else default


layers = []
for m in re.finditer(r'\(layer "([^"]+)"\s*\(type "(copper|core|prepreg)"\)(.*?)(?=\(layer "|\(copper_finish|\Z)', stack, re.S):
    name, kind, rest = m.groups()
    th = re.search(r"\(thickness ([\d.]+)", rest)
    mat = re.search(r'\(material "([^"]+)"', rest)
    er = re.search(r"\(epsilon_r ([\d.]+)", rest)
    extra = "" if kind == "copper" else f"  {kind}  {mat.group(1) if mat else ''}  Er {er.group(1) if er else '?'}"
    layers.append(f"  {name:<14} {float(th.group(1)) if th else 0:.4f} mm{extra}")

smd = sum(1 for f in b.GetFootprints() if f.GetAttributes() & pcbnew.FP_SMD)
tht = sum(1 for f in b.GetFootprints() if f.GetAttributes() & pcbnew.FP_THROUGH_HOLE)
bottom = sum(1 for f in b.GetFootprints() if f.IsFlipped() and f.GetAttributes() & (pcbnew.FP_SMD | pcbnew.FP_THROUGH_HOLE))

min_track = smallest(mm(t.GetWidth()) for t in tracks)
min_via_drill = smallest(mm(v.GetDrillValue()) for v in vias)
min_hole = smallest(pad_holes)
warnings = []
if min_track is not None and min_track < mm(ds.m_TrackMinWidth):
    warnings.append(f"Track width {fmt(min_track)} is below the project minimum {fmt(mm(ds.m_TrackMinWidth))}")
for what, v in (("Via drill", min_via_drill), ("Plated pad hole", min_hole)):
    if v is not None and v < mm(ds.m_MinThroughDrill):
        warnings.append(f"{what} {fmt(v)} is below the project minimum drill {fmt(mm(ds.m_MinThroughDrill))}")
warn_txt = ("\n!! CHECK BEFORE ORDERING\n" + "\n".join("  - " + w for w in warnings) + "\n") if warnings else ""

lines = f"""FABRICATION NOTES
=================

Board        {tb.GetTitle() or os.path.splitext(os.path.basename(pcb_path))[0]}
Revision     {rev}
Company      {tb.GetCompany() or '-'}
Generated    {datetime.date.today().isoformat()} by kicad-hub from the KiCad project
{warn_txt}
BOARD
  Size              {fmt(mm(bbox.GetWidth()))} x {fmt(mm(bbox.GetHeight()))}
  Copper layers     {b.GetCopperLayerCount()}
  Thickness         {fmt(mm(ds.GetBoardThickness()))}
  Surface finish    {opt('copper_finish', 'not specified')}
  Impedance control {opt('dielectric_constraints', 'no')}
  Edge plating      {opt('edge_plating', 'no')}
  Castellated pads  {opt('castellated_pads', 'no')}

STACKUP
{chr(10).join(layers) if layers else '  not defined in the project'}

SMALLEST FEATURES USED ON THE BOARD
  Track width       {fmt(min_track)}
  Via drill         {fmt(min_via_drill)}
  Via diameter      {fmt(smallest(mm(v.GetWidth(pcbnew.F_Cu)) for v in vias))}
  Plated pad hole   {fmt(min_hole)}
  Vias / PTH / NPTH {len(vias)} / {len(pth)} / {len(npth)}

DESIGN RULES (project minimums)
  Track width       {fmt(mm(ds.m_TrackMinWidth))}
  Clearance         {fmt(mm(ds.m_MinClearance))}
  Through drill     {fmt(mm(ds.m_MinThroughDrill))}
  Hole to hole      {fmt(mm(ds.m_HoleToHoleMin))}

ASSEMBLY
  SMD parts         {smd}
  THT parts         {tht}
  Parts on bottom   {bottom}

FILES
  gerber/      Gerber X2 + job file (.gbrjob): layer stack and attributes
  drill/       Excellon, plated and non-plated holes in separate files, drill map (PDF)
  *.d356       IPC-D-356 netlist for electrical test
  assembly/    BOM (CSV, grouped, DNP excluded) and pick & place (CSV, mm, both sides)
  SHA256SUMS.txt  checksums of every file in this package
"""
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write(lines)
if len(sys.argv) > 4:
    drills = [d for d in (min_via_drill, min_hole) if d is not None]
    with open(sys.argv[4], "w", encoding="utf-8") as f:
        json.dump({"size_mm": [round(mm(bbox.GetWidth()), 3), round(mm(bbox.GetHeight()), 3)],
                   "copper_layers": b.GetCopperLayerCount(), "min_track": min_track,
                   "min_via_drill": min_via_drill, "min_pad_hole": min_hole,
                   "min_drill": min(drills) if drills else None}, f)
for w in warnings:
    print(w)  # the action turns these into CI warnings
