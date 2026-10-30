#!/usr/bin/env python3
"""Does the PCB agree with the schematic, for the default build and for each variant?

The schematic is the source of truth (the per-part BOMs the action exports); the PCB only
copies it when the designer runs "Update PCB from Schematic". For every build this lists:
  - parts the schematic fits that the PCB marks DNP / excluded from the position files or
    the BOM (or the other way round),
  - references present on one side only,
  - value differences, using the PCB's own per-variant overrides when the board stores them.

The board file is read as plain text (s-expressions), so no KiCad install is needed. Per-
variant data on footprints is picked up from any `(variant (name ...) ...)` node inside a
footprint (variants are only compared when the board has some; otherwise the PCB can't hold them), with `dnp`, `exclude_from_bom`, `exclude_from_pos_files` flags and `field` /
`property` overrides; a board without any (older format, or never updated) just has none.

Usage: variant_sync.py <board.kicad_pcb> <reports dir> <output JSON> [warnings txt]
Reads bom-parts.csv and variants.json from the reports dir. The optional text file gets one
line per build whose pick & place was filtered with schematic data that disagrees with the PCB.
"""
import csv
import json
import os
import re
import sys


class Str(str):
    """A quoted string, to tell it from a bare symbol."""


def parse(text):
    """Nested lists of Str (quoted) / str (symbol) tokens."""
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[()]|[^\s()"]+', text)
    stack, cur = [], []
    for t in tokens:
        if t == "(":
            stack.append(cur)
            cur = []
        elif t == ")":
            done, cur = cur, stack.pop()
            cur.append(done)
        elif t[0] == '"':
            cur.append(Str(t[1:-1].replace('\\"', '"').replace("\\\\", "\\")))
        else:
            cur.append(t)
    return cur


def kids(node, key):
    return [c for c in node if isinstance(c, list) and c and c[0] == key]


def walk(node, key):
    for c in node:
        if isinstance(c, list):
            if c and c[0] == key:
                yield c
            yield from walk(c, key)


def flag(node, key):
    """`(key yes)`, `(key)` or a bare `key` among the node's children; None if absent."""
    for c in node[1:]:
        if c == key and not isinstance(c, Str):
            return True
        if isinstance(c, list) and c and c[0] == key:
            return not (len(c) > 1 and c[1] == "no")
    return None


def field_overrides(node):
    """`(field (name "Value") (value "x"))`, `(property "Value" "x")` or `(field "Value" "x")`."""
    out = {}
    for c in node:
        if not (isinstance(c, list) and c and c[0] in ("field", "property")):
            continue
        n, v = kids(c, "name"), kids(c, "value")
        if n and v and len(n[0]) > 1 and len(v[0]) > 1:
            out[n[0][1]] = v[0][1]
        elif len(c) >= 3 and isinstance(c[1], str) and isinstance(c[2], str):
            out[c[1]] = c[2]
    return out


def load_board(path):
    """({ref: {dnp, bom, pos, value, variants: {name: overrides}}}, any footprint has variant data)."""
    tree = parse(open(path, encoding="utf-8").read())
    root = tree[0] if tree and isinstance(tree[0], list) else tree
    parts, any_variant = {}, False
    for fp in kids(root, "footprint") + kids(root, "module"):
        props = {c[1]: c[2] for c in kids(fp, "property") if len(c) > 2}
        ref, value = props.get("Reference"), props.get("Value")
        for c in kids(fp, "fp_text"):  # boards from before KiCad 8
            if len(c) > 2 and c[1] == "reference":
                ref = ref or c[2]
            if len(c) > 2 and c[1] == "value":
                value = value or c[2]
        if not ref or ref.endswith("**"):
            continue
        attrs = kids(fp, "attr")
        attr = [a for a in (attrs[0][1:] if attrs else []) if isinstance(a, str)]
        part = {"dnp": "dnp" in attr, "bom": "exclude_from_bom" not in attr,
                "pos": "exclude_from_pos_files" not in attr, "value": value, "variants": {}}
        for v in walk(fp, "variant"):
            n = kids(v, "name")
            if not n or len(n[0]) < 2:
                continue
            any_variant = True
            o = {}
            for k, key in (("dnp", "dnp"), ("exclude_from_bom", "bom"), ("exclude_from_pos_files", "pos")):
                f = flag(v, k)
                if f is not None:
                    o[key] = f if key == "dnp" else not f
            fo = field_overrides(v)
            if "Value" in fo:
                o["value"] = fo["Value"]
            part["variants"][n[0][1]] = o
        parts[ref] = part
    return parts, any_variant


def load_bom(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["Reference"]: r for r in csv.DictReader(f) if r.get("Reference")}


def pcb_state(p):
    if p["dnp"]:
        return "DNP"
    if not p["bom"]:
        return "excluded from BOM"
    if not p["pos"]:
        return "excluded from position files"
    return "fitted"


def ref_key(ref):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


def compare(variant, bom, board):
    """Differences for one build; `variant` is None for the default build."""
    issues = []
    for ref in set(bom) | set(board):
        row, p = bom.get(ref), board.get(ref)
        if row is not None and p is None:
            issues.append((ref, "fitted" if not (row.get("DNP") or "").strip() else "DNP", "not on the PCB"))
            continue
        eff = dict(p, **p["variants"].get(variant, {})) if variant else p
        if row is None:
            # on the PCB only: fine if the PCB keeps it out of the BOM (fiducials, logos...)
            if eff["bom"] and not eff["dnp"]:
                issues.append((ref, "not in the BOM / schematic", pcb_state(eff)))
            continue
        sch_fit = not (row.get("DNP") or "").strip()
        state = pcb_state(eff)
        if sch_fit and state != "fitted":
            issues.append((ref, "fitted", state))
        elif not sch_fit and state == "fitted":
            issues.append((ref, "DNP", "fitted"))
        sv, pv = (row.get("Value") or "").strip(), (eff.get("value") or "").strip()
        if pv and sv and sv != pv:
            issues.append((ref, f"value {sv}", f"value {pv}"))
    return [{"ref": r, "schematic": s, "pcb": c} for r, s, c in sorted(issues, key=lambda i: ref_key(i[0]))]


def main():
    pcb, reports, out = sys.argv[1:4]
    warn_out = sys.argv[4] if len(sys.argv) > 4 else ""
    board, has_variant_data = load_board(pcb)
    builds = [("default", None, os.path.join(reports, "bom-parts.csv"))]
    vpath = os.path.join(reports, "variants.json")
    if os.path.exists(vpath):
        builds += [(v["name"], v["name"], v["bom"]) for v in json.load(open(vpath, encoding="utf-8"))]
    # Without per-variant data the PCB can only hold the default build: comparing a variant's
    # DNP/values with it would just repeat the variant's own differences, so it isn't checked.
    result = [{"name": name, "checked": not (variant and not has_variant_data),
               "issues": compare(variant, load_bom(p), board) if not (variant and not has_variant_data) else []}
              for name, variant, p in builds if os.path.exists(p)]
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"pcb_variant_data": has_variant_data, "builds": result}, f, indent=1)
    if warn_out:
        with open(warn_out, "w", encoding="utf-8") as f:
            for b in result:
                if b["issues"]:
                    label = "The default build" if b["name"] == "default" else f"Variant '{b['name']}'"
                    refs = ", ".join(i["ref"] for i in b["issues"][:8]) + (", ..." if len(b["issues"]) > 8 else "")
                    f.write(f"{label}: pick & place was filtered with the schematic, which disagrees with the "
                            f"PCB on {len(b['issues'])} item(s) ({refs}). Update the PCB from the schematic "
                            "and check the assembly files before ordering.\n")
    print(f"{sum(len(b['issues']) for b in result)} schematic vs PCB differences in {len(result)} build(s)")


main()
