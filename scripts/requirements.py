#!/usr/bin/env python3
"""Check the board against kicad-hub-requirements.yaml.

Only uses what the action's previous steps already wrote to the reports folder: ERC and
DRC JSON, per-part BOMs, variants.json, sourcing.json and board.json (from fabnotes.py).
A requirement whose data isn't there (no prices, no board outline...) is "unknown", never
a crash; a malformed entry is "fail" with the reason.

Usage: requirements.py <requirements.yaml> <reports dir> <output JSON>
Needs PyYAML (present in the KiBot image the action runs it in).
"""
import csv
import json
import os
import re
import sys

REQ_FILE, REPORTS, OUT = sys.argv[1:4]


def path(name):
    return os.path.join(REPORTS, name)


def load_json(name):
    p = path(name)
    try:
        return json.load(open(p, encoding="utf-8")) if os.path.exists(p) else None
    except (OSError, ValueError):
        return None


def blank(v):
    return (v or "").strip() in ("", "~")


def ref_key(ref):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


class Unknown(Exception):
    """The data needed isn't available."""


class Bad(Exception):
    """The requirement itself is malformed."""


# --- Inputs ----------------------------------------------------------------------------------
erc, drc = load_json("erc.json"), load_json("drc.json")
variants = load_json("variants.json") or []
sourcing, board = load_json("sourcing.json"), load_json("board.json")


def bom(variant):
    """Fitted parts (DNP rows dropped) of the default build or a named variant."""
    if variant in (None, "", "default"):
        p = path("bom-parts.csv")
    else:
        v = next((v for v in variants if v["name"] == variant), None)
        if v is None:
            raise Unknown(f"variant '{variant}' was not built")
        p = v["bom"]
    if not os.path.exists(p):
        raise Unknown("no BOM")
    with open(p, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("Reference") and blank(r.get("DNP"))]


def number(req, key, default=None, positive=False):
    v = req.get(key, default)
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        raise Bad(f"'{key}' must be a number" + ("" if default is not None else " (required)"))
    if positive and v <= 0:
        raise Bad(f"'{key}' must be greater than 0")
    return v


def regex(req, key):
    v = req.get(key)
    if not isinstance(v, str) or not v:
        raise Bad(f"'{key}' must be a regular expression (required)")
    try:
        return re.compile(v)
    except re.error as e:
        raise Bad(f"'{key}' is not a valid regular expression ({e})")


def refs_text(refs, limit=12):
    refs = sorted(refs, key=ref_key)
    return ", ".join(refs[:limit]) + (f" and {len(refs) - limit} more" if len(refs) > limit else "")


def verdict(ok, measured, detail=""):
    return ("pass" if ok else "fail", measured, detail)


def mm(v):
    return f"{v:.3f}".rstrip("0").rstrip(".") + " mm"


# --- Checks ----------------------------------------------------------------------------------
# Each takes the requirement dict and returns (status, measured, detail) or raises
# Unknown / Bad.
def count_violations(report, req, label):
    if report is None:
        raise Unknown(f"no {label} report")
    sev = req.get("severity", "error")
    if sev not in ("error", "warning"):
        raise Bad("'severity' must be error or warning")
    items = [v for s in report["sheets"] for v in s["violations"]] if "sheets" in report else report["violations"]
    n = sum(1 for v in items if v.get("severity") == sev)
    limit = number(req, "max", 0)
    return verdict(n <= limit, f"{n} {sev}{'s' if n != 1 else ''}", f"limit {limit}")


def erc_errors_max(req):
    return count_violations(erc, req, "ERC")


def drc_errors_max(req):
    return count_violations(drc, req, "DRC")


def max_parts(req):
    limit = number(req, "max", positive=True)
    n = len(bom(req.get("variant")))
    return verdict(n <= limit, f"{n} fitted parts", f"limit {limit}")


def max_unique_parts(req):
    limit = number(req, "max", positive=True)
    # same part number, or else same value and footprint, is one unique part
    keys = {(r.get("MPN") or "").strip().upper() or (r.get("LCSC") or "").strip().upper()
            or ((r.get("Value") or "").strip(), (r.get("Footprint") or "").strip()) for r in bom(req.get("variant"))}
    return verdict(len(keys) <= limit, f"{len(keys)} unique parts", f"limit {limit}")


def build_totals(req):
    """The sourcing result of the build and the batch asked for."""
    n = number(req, "batch", positive=True)
    if not isinstance(n, int):
        raise Bad("'batch' must be a whole number")
    if sourcing is None:
        raise Unknown("no sourcing data (needs sourcing: true)")
    if n not in sourcing["batches"]:
        raise Unknown(f"batch {n} is not priced (the 'boards' input prices {', '.join(map(str, sourcing['batches']))})")
    name = req.get("variant") or "default"
    b = next((b for b in sourcing["builds"] if b["name"] == name), None)
    if b is None:
        raise Unknown(f"variant '{name}' has no sourcing data")
    return n, b


def max_cost(req):
    limit = number(req, "max", positive=True)
    n, b = build_totals(req)
    total = b["totals"].get(str(n))
    if total is None:
        raise Unknown("no part could be priced")
    per, cur = total / n, sourcing["currency"]
    gaps = len(b["unpriced"]) + len(b["unpriceable"])
    note = f"{b['priced']} of {b['fitted']} parts priced, components only"
    measured = f"{per:.2f} {cur} per board at {n}" + (" (partial)" if gaps else "")
    if per > limit:
        return verdict(False, measured, f"limit {limit} {cur}; {note}")
    if gaps:
        raise Unknown(f"{per:.2f} {cur} per board so far, within {limit}, but {gaps} parts have no price ({note})")
    return verdict(True, measured, f"limit {limit} {cur}; {note}")


def in_stock(req):
    n, b = build_totals(req)
    short = []
    for line in sourcing["lines"]:
        refs = line["refs"].get(b["name"])
        if not refs:
            continue
        need = len(refs) * n
        offers = line["offers"]
        if line.get("choice") is not None and line["choice"] < len(offers):
            offers = [offers[line["choice"]]]  # the supplier picked in the selection file
        best = max((o["stock"] for o in offers), default=0)
        if best < need:
            short.append((refs[0], f"{line['part']} ({best}/{need})" if offers else f"{line['part']} (not found)"))
    unlisted = len(b["unpriceable"])
    if short:
        short.sort(key=lambda s: ref_key(s[0]))
        return verdict(False, f"{len(short)} short at {n} boards", "; ".join(s[1] for s in short[:12]))
    if unlisted:
        raise Unknown(f"all looked-up parts are in stock at {n} boards, but {unlisted} have no MPN or LCSC code to look up")
    return verdict(True, f"all in stock at {n} boards")


def max_above_target(req):
    limit = number(req, "max", default=0)
    n, b = build_totals(req) if req.get("batch") else (sourcing["batches"][-1] if sourcing else None, None)
    if sourcing is None:
        raise Unknown("no sourcing data (needs sourcing: true)")
    if not sourcing.get("targets"):
        raise Unknown("no target prices (add a Target column to kicad-hub-sourcing.csv)")
    name = req.get("variant") or "default"
    over = []
    for line in sourcing["lines"]:
        v = (line.get("vs_target") or {}).get(str(n))
        if v and v["status"] == "above" and line["refs"].get(name):
            over.append(f"{line['part']} (+{v['pct']:.1f}%)")
    return verdict(len(over) <= limit, f"{len(over)} above target at {n} boards", "; ".join(over[:12]) or f"limit {limit}")


BAD_LIFECYCLE = re.compile(r"obsolete|end of life|\beol\b|not recommended|nrnd|not for new|last time|"
                           r"no_longer_manufactured|discontinued", re.I)


def no_bad_lifecycle(req):
    if sourcing is None:
        raise Unknown("no sourcing data (needs sourcing: true)")
    name = req.get("variant") or "default"
    if not any(b["name"] == name for b in sourcing["builds"]):
        raise Unknown(f"variant '{name}' has no sourcing data")
    bad = []
    for line in sourcing["lines"]:
        if not line["refs"].get(name):
            continue
        known = [o for o in line["offers"] if o["lifecycle"]]
        # a part only counts when no supplier offers it as active
        if known and all(BAD_LIFECYCLE.search(o["lifecycle"]) for o in known):
            bad.append(f"{line['part']} ({known[0]['lifecycle']})")
    if bad:
        return verdict(False, f"{len(bad)} not active", "; ".join(bad[:12]))
    return verdict(True, "all active or unknown")


def board_value(key, what):
    if board is None or board.get(key) is None:
        raise Unknown(f"no {what} data (needs a board outline)")
    return board[key]


def max_layers(req):
    limit = number(req, "max", positive=True)
    n = board_value("copper_layers", "board")
    return verdict(n <= limit, f"{n} copper layers", f"limit {limit}")


def max_board_size(req):
    w, h = number(req, "width", positive=True), number(req, "height", positive=True)
    bw, bh = board_value("size_mm", "board")
    ok = all(a <= b + 1e-6 for a, b in zip(sorted((bw, bh)), sorted((w, h))))
    return verdict(ok, f"{bw:.1f} x {bh:.1f} mm", f"limit {w:g} x {h:g} mm (either orientation)")


def smallest(key, what, req):
    limit = number(req, "min", positive=True)
    v = board_value(key, what)
    return verdict(v >= limit - 1e-9, mm(v), f"minimum {limit:g} mm")


def min_track(req):
    return smallest("min_track", "track", req)


def min_drill(req):
    return smallest("min_drill", "drill", req)


def required_fields(req):
    pat = regex(req, "refs")
    fields = req.get("fields")
    if isinstance(fields, str):
        fields = [fields]
    if not isinstance(fields, list) or not fields or not all(isinstance(f, str) and f for f in fields):
        raise Bad("'fields' must be a list of field names (required)")
    parts = [r for r in bom(req.get("variant")) if pat.fullmatch(r["Reference"])]
    if not parts:
        raise Unknown(f"no fitted part has a reference matching '{pat.pattern}'")
    cols = parts[0].keys()
    for f in fields:
        if f not in cols:
            raise Unknown(f"field '{f}' is not in the exported BOM (add it to required-fields)")
    miss = {r["Reference"]: [f for f in fields if blank(r.get(f))] for r in parts}
    miss = {k: v for k, v in miss.items() if v}
    if miss:
        return verdict(False, f"{len(miss)} of {len(parts)} incomplete",
                       "; ".join(f"{k}: {', '.join(miss[k])}" for k in sorted(miss, key=ref_key)[:12]))
    return verdict(True, f"all {len(parts)} complete")


def forbidden_parts(req):
    pat = regex(req, "pattern")
    pat = re.compile(pat.pattern, re.I)
    hits = [r["Reference"] for r in bom(req.get("variant"))
            if pat.search(r.get("MPN") or "") or pat.search(r.get("Value") or "")]
    if hits:
        return verdict(False, f"{len(hits)} found", refs_text(hits))
    return verdict(True, "none found")


# check type -> (function, parameters it accepts besides id, text and check)
CHECKS = {
    "erc_errors_max": (erc_errors_max, {"max", "severity"}),
    "drc_errors_max": (drc_errors_max, {"max", "severity"}),
    "max_parts": (max_parts, {"max", "variant"}),
    "max_unique_parts": (max_unique_parts, {"max", "variant"}),
    "max_cost": (max_cost, {"max", "batch", "variant"}),
    "in_stock": (in_stock, {"batch", "variant"}),
    "max_above_target": (max_above_target, {"max", "batch", "variant"}),
    "no_bad_lifecycle": (no_bad_lifecycle, {"variant"}),
    "max_layers": (max_layers, {"max"}),
    "max_board_size": (max_board_size, {"width", "height"}),
    "min_track": (min_track, {"min"}),
    "min_drill": (min_drill, {"min"}),
    "required_fields": (required_fields, {"refs", "fields", "variant"}),
    "forbidden_parts": (forbidden_parts, {"pattern", "variant"}),
}


def run(req):
    check = req["check"]
    fn, params = CHECKS[check]
    extra = sorted(set(req) - params - {"id", "text", "check"})
    if extra:
        raise Bad(f"unknown parameter {', '.join(repr(k) for k in extra)} for {check} "
                  f"(accepts: {', '.join(sorted(params)) or 'none'})")
    return fn(req)


def write(results):
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump(results, f, indent=1)
    n = {s: sum(1 for r in results if r["status"] == s) for s in ("pass", "fail", "unknown")}
    print(f"{len(results)} requirements: {n['pass']} pass, {n['fail']} fail, {n['unknown']} unknown")
    for r in results:
        if r["status"] != "pass":
            print(f"  {r['id']}: {r['status']} - {r['measured']} {r['detail']}".rstrip())


def entry(rid, text, check, status, measured="", detail=""):
    return {"id": str(rid), "text": text, "check": check, "status": status, "measured": measured, "detail": detail}


try:
    import yaml
except ImportError:
    write([entry("-", "Read the requirements file", "", "fail", "",
                 "PyYAML is not available in this environment")])
    sys.exit(0)

try:
    data = yaml.safe_load(open(REQ_FILE, encoding="utf-8"))
except (OSError, yaml.YAMLError) as e:
    write([entry("-", "Read the requirements file", "", "fail", "", f"{os.path.basename(REQ_FILE)} is not valid YAML: "
                 + " ".join(str(e).split())[:200])])
    sys.exit(0)
if isinstance(data, dict) and "requirements" in data:
    data = data["requirements"]
if data is None:
    data = []
if not isinstance(data, list):
    write([entry("-", "Read the requirements file", "", "fail", "",
                 "the file must be a list of requirements (or a 'requirements:' list)")])
    sys.exit(0)

results, seen = [], set()
for i, req in enumerate(data, 1):
    if not isinstance(req, dict):
        results.append(entry(f"#{i}", "", "", "fail", "", "entry is not a mapping (id, text, check...)"))
        continue
    rid, text, check = req.get("id"), str(req.get("text") or ""), str(req.get("check") or "")
    rid = str(rid) if rid not in (None, "") else f"#{i}"
    try:
        if req.get("id") in (None, ""):
            raise Bad("missing 'id'")
        if rid in seen:
            raise Bad("duplicate 'id'")
        if not text:
            raise Bad("missing 'text'")
        if check not in CHECKS:
            raise Bad(f"unknown check '{check}'" if check else "missing 'check'")
        if "variant" in req and not isinstance(req["variant"], str):
            raise Bad("'variant' must be a name")
        results.append(entry(rid, text, check, *run(req)))
    except Bad as e:
        results.append(entry(rid, text, check, "fail", "invalid requirement", str(e)))
    except Unknown as e:
        results.append(entry(rid, text, check, "unknown", "no data", str(e)))
    except Exception as e:  # a data format we didn't expect: report it, don't stop the build
        results.append(entry(rid, text, check, "unknown", "error", f"{type(e).__name__}: {e}"))
    seen.add(rid)
write(results)
