"""Fabrication notes (README.txt) for the fab package, read from the board itself.

Reads the .kicad_pcb and .kicad_pro files with plain Python (scripts/board_file.py): no
KiCad and no pcbnew are needed, so it runs with any Python 3 (KiCad's own, the runner's,
a system one). It used to load the board with pcbnew, which needs KiCad's bundled Python
(KiCad 11 is expected to drop it) and on Windows could open a "font not found" window
that waited for OK. The numbers are the same: they were checked against pcbnew on the
example board.

Usage: fabnotes.py <board.kicad_pcb> <revision> <output README.txt> [board.json] [fab profile]
The optional JSON has the same measurements for later steps (requirements.py);
pass "" to skip it. The fab profile (default generic) is described in README.txt.
"""
import datetime
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from board_file import Board  # noqa: E402

# KiCad's defaults (BOARD_DESIGN_SETTINGS) for a rule the project file doesn't set
DEFAULT_RULES = {"min_track_width": 0.0, "min_clearance": 0.0, "min_through_hole_diameter": 0.3,
                 "min_hole_to_hole": 0.25}


def project_rules(pcb_path):
    """The board's minimum design rules, from the .kicad_pro next to the board (mm)."""
    rules = dict(DEFAULT_RULES)
    try:
        with open(os.path.splitext(pcb_path)[0] + ".kicad_pro", encoding="utf-8") as f:
            saved = json.load(f).get("board", {}).get("design_settings", {}).get("rules", {})
    except (OSError, ValueError, AttributeError):
        return rules
    rules.update({k: float(v) for k, v in saved.items() if k in rules and isinstance(v, (int, float))})
    return rules


pcb_path, rev, out = sys.argv[1:4]
profile = sys.argv[5] if len(sys.argv) > 5 and sys.argv[5] else "generic"
with open(pcb_path, encoding="utf-8") as f:
    text = f.read()
b = Board.load(pcb_path)
rules = project_rules(pcb_path)
size = b.outline_size() or (0.0, 0.0)
vias = b.vias()
pads = b.pads()
pth = [d for kind, d in pads if kind == "thru_hole"]
npth = [d for kind, d in pads if kind == "np_thru_hole"]
pad_holes = [d for d in pth if d]


def fmt(v, unit="mm"):
    return "n/a" if v is None else f"{v:.5f}".rstrip("0").rstrip(".") + f" {unit}"


def smallest(values):
    values = [v for v in values if v > 0]
    return min(values) if values else None


# The stackup, read from the file's own text
stack = Board.stackup_text(text)


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

smd, tht, bottom = b.footprint_counts()

min_track = smallest(b.track_widths())
min_via_drill = smallest(d for d, _ in vias)
min_hole = smallest(pad_holes)
warnings = []
if min_track is not None and min_track < rules["min_track_width"]:
    warnings.append(f"Track width {fmt(min_track)} is below the project minimum {fmt(rules['min_track_width'])}")
for what, v in (("Via drill", min_via_drill), ("Plated pad hole", min_hole)):
    if v is not None and v < rules["min_through_hole_diameter"]:
        warnings.append(f"{what} {fmt(v)} is below the project minimum drill {fmt(rules['min_through_hole_diameter'])}")
warn_txt = ("\n!! CHECK BEFORE ORDERING\n" + "\n".join("  - " + w for w in warnings) + "\n") if warnings else ""

lines = f"""FABRICATION NOTES
=================

Board        {b.title_block('title') or os.path.splitext(os.path.basename(pcb_path))[0]}
Revision     {rev}
Company      {b.title_block('company') or '-'}
Generated    {datetime.date.today().isoformat()} by kicad-hub from the KiCad project
{warn_txt}
BOARD
  Size              {fmt(size[0])} x {fmt(size[1])}
  Copper layers     {b.copper_layers}
  Thickness         {fmt(b.thickness)}
  Surface finish    {opt('copper_finish', 'not specified')}
  Impedance control {opt('dielectric_constraints', 'no')}
  Edge plating      {opt('edge_plating', 'no')}
  Castellated pads  {opt('castellated_pads', 'no')}

STACKUP
{chr(10).join(layers) if layers else '  not defined in the project'}

SMALLEST FEATURES USED ON THE BOARD
  Track width       {fmt(min_track)}
  Via drill         {fmt(min_via_drill)}
  Via diameter      {fmt(smallest(s for _, s in vias))}
  Plated pad hole   {fmt(min_hole)}
  Vias / PTH / NPTH {len(vias)} / {len(pth)} / {len(npth)}

DESIGN RULES (project minimums)
  Track width       {fmt(rules['min_track_width'])}
  Clearance         {fmt(rules['min_clearance'])}
  Through drill     {fmt(rules['min_through_hole_diameter'])}
  Hole to hole      {fmt(rules['min_hole_to_hole'])}

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
if profile != "generic":
    lines += f"""
FAB PROFILE: {profile}
  Checked with a second DRC that adds {profile}'s published capabilities
  (kicad-hub fab-profiles/{profile}/rules.kicad_dru); see the DRC report.
  assembly/{profile}/  BOM and pick & place in {profile}'s column layout, when it has one
"""
with open(out, "w", encoding="utf-8", newline="\n") as f:
    f.write(lines)
if len(sys.argv) > 4 and sys.argv[4]:
    drills = [d for d in (min_via_drill, min_hole) if d is not None]
    with open(sys.argv[4], "w", encoding="utf-8") as f:
        json.dump({"size_mm": [round(size[0], 3), round(size[1], 3)],
                   "copper_layers": b.copper_layers, "min_track": min_track,
                   "min_via_drill": min_via_drill, "min_pad_hole": min_hole,
                   "min_drill": min(drills) if drills else None}, f)
for w in warnings:
    print(w)  # the action turns these into CI warnings
