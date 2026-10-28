#!/usr/bin/env python3
"""Build the kicad-hub markdown report: ERC/DRC counts, missing BOM fields and the BOM
diff against the pull request's target branch.

Inputs are the files produced by the action's previous steps (kicad-cli JSON reports
and per-reference BOM CSVs). Prints nothing; writes the report and a small JSON with
counts that later steps can use.
"""
import argparse
import csv
import json
import os
import re

MAX_ROWS = 60  # rows per table before cutting, to keep PR comments readable

ap = argparse.ArgumentParser()
ap.add_argument("--name", required=True)
ap.add_argument("--bom", required=True, help="per-reference BOM CSV of this commit")
ap.add_argument("--base-bom", help="per-reference BOM CSV of the target branch")
ap.add_argument("--base-ref", default="base")
ap.add_argument("--erc")
ap.add_argument("--drc")
ap.add_argument("--required", default="Footprint,Datasheet,MPN|LCSC",
                help="comma-separated fields; 'A|B' means A or B is enough")
ap.add_argument("--cap-voltage", default="true")
ap.add_argument("--run-url", default="")
ap.add_argument("--out", required=True, help="markdown report")
ap.add_argument("--out-json", required=True, help="counts for later steps")
args = ap.parse_args()


def load_bom(path):
    if not path or not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["Reference"]: r for r in csv.DictReader(f) if r.get("Reference")}


def ref_key(ref):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


def blank(v):
    return (v or "").strip() in ("", "~")


def cell(v):
    return (v or "").replace("|", "\\|").strip() or "—"


def table(header, rows):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out += ["| " + " | ".join(cell(c) for c in r) + " |" for r in rows[:MAX_ROWS]]
    if len(rows) > MAX_ROWS:
        out.append(f"\n_…and {len(rows) - MAX_ROWS} more (full list in the artifact)._")
    return "\n".join(out)


md = [f"<!-- kicad-hub:{args.name} -->", f"## KiCad checks: {args.name}", ""]
counts = {}

# --- ERC / DRC -------------------------------------------------------------
def sev(items, s):
    return sum(1 for v in items if v.get("severity") == s)

erc = json.load(open(args.erc, encoding="utf-8")) if args.erc and os.path.exists(args.erc) else None
drc = json.load(open(args.drc, encoding="utf-8")) if args.drc and os.path.exists(args.drc) else None
erc_v = [v for s in erc["sheets"] for v in s["violations"]] if erc else []
drc_v = drc["violations"] if drc else []
counts["erc_errors"] = sev(erc_v, "error") if erc else None
counts["drc_errors"] = sev(drc_v, "error") if drc else None
md += [table(["Check", "Errors", "Warnings", "Unconnected"], [
    ["ERC", str(sev(erc_v, "error")), str(sev(erc_v, "warning")), ""] if erc else ["ERC", "—", "—", ""],
    ["DRC", str(sev(drc_v, "error")), str(sev(drc_v, "warning")), str(len(drc["unconnected_items"]))]
    if drc else ["DRC", "skipped", "", ""],
]), ""]
if not drc:
    md += ["_No board outline yet: DRC and PCB outputs skipped._", ""]

# --- BOM diff ----------------------------------------------------------------
head = load_bom(args.bom) or {}
base = load_bom(args.base_bom)
compare = [c for c in (next(iter(head.values())).keys() if head else []) if c != "Reference"]

md.append(f"### BOM changes vs `{args.base_ref}`")
if base is None:
    md += ["_Not a pull request (or no base schematic): nothing to compare._", ""]
else:
    added = sorted(set(head) - set(base), key=ref_key)
    removed = sorted(set(base) - set(head), key=ref_key)
    changed = []
    for ref in sorted(set(head) & set(base), key=ref_key):
        diffs = [f"{c}: {cell(base[ref].get(c))} → {cell(head[ref].get(c))}"
                 for c in compare if (base[ref].get(c) or "").strip() != (head[ref].get(c) or "").strip()]
        if diffs:
            changed.append([ref, cell(head[ref].get("Value")), "<br>".join(diffs)])
    counts.update(bom_added=len(added), bom_removed=len(removed), bom_changed=len(changed))
    if not (added or removed or changed):
        md += ["No BOM changes.", ""]
    else:
        md += [f"**＋{len(added)} added · −{len(removed)} removed · ~{len(changed)} changed**", ""]
        rows = ([["＋ " + r, cell(head[r].get("Value")), cell(head[r].get("Footprint"))] for r in added] +
                [["− " + r, cell(base[r].get("Value")), cell(base[r].get("Footprint"))] for r in removed])
        if rows:
            md += [table(["Part", "Value", "Footprint"], rows), ""]
        if changed:
            md += [table(["Part", "Value", "Change"], changed), ""]

# --- Missing fields ------------------------------------------------------------
groups = [[a.strip() for a in g.split("|") if a.strip()] for g in args.required.split(",") if g.strip()]
check_caps = args.cap_voltage.lower() == "true"
volt_re = re.compile(r"\d+(\.\d+)?\s*[kK]?V\b")
missing, per_rule = [], {}
for ref in sorted(head, key=ref_key):
    r = head[ref]
    if not blank(r.get("DNP")):
        continue  # not fitted: no MPN / datasheet needed
    miss = ["|".join(g) for g in groups if all(blank(r.get(a)) for a in g)]
    if check_caps and re.match(r"C\d", ref) and blank(r.get("Voltage")) and not volt_re.search(r.get("Value") or ""):
        miss.append("Voltage")
    for m in miss:
        per_rule[m] = per_rule.get(m, 0) + 1
    if miss:
        missing.append([ref, cell(r.get("Value")), ", ".join(miss)])
counts["missing_fields"] = len(missing)

md.append("### Missing fields")
if not missing:
    md += [f"All {len(head)} fitted parts have the required fields.", ""]
else:
    summary = ", ".join(f"{k}: {v}" for k, v in sorted(per_rule.items(), key=lambda kv: -kv[1]))
    md += [f"**{len(missing)} of {len(head)} parts** miss required fields ({summary}).", "",
           "<details><summary>Show parts</summary>", "", table(["Part", "Value", "Missing"], missing),
           "", "</details>", ""]

if args.run_url:
    md += [f"[Run details and fab outputs]({args.run_url})"]

with open(args.out, "w", encoding="utf-8", newline="\n") as f:
    f.write("\n".join(md) + "\n")
with open(args.out_json, "w", encoding="utf-8") as f:
    json.dump(counts, f)
