#!/usr/bin/env python3
"""Cost and stock of the fitted parts, per board and per batch, from LCSC.

Parts are looked up by their LCSC part number (the `LCSC` field). No API key is
needed: it uses the JSON behind LCSC's product pages, so it may change without
notice; any failure is reported, never fatal. Prices are for the components only
(no PCB, no assembly), in the currency LCSC answers with (USD).

For each build (default + variants) and each batch size it buys every line at
max(needed, minimum order) and takes the price break for that quantity.

Writes a JSON for the report and a CSV with one line per LCSC part.

Usage: sourcing.py --bom BOM.csv [--variants variants.json] --boards 1,10,100
                   --json OUT.json --csv OUT.csv
"""
import argparse
import csv
import json
import math
import re
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("--bom", required=True, help="per-part BOM of the default build")
ap.add_argument("--variants", help="variants.json from the action (per-part BOM of each variant)")
ap.add_argument("--boards", default="1,10,100", help="batch sizes, comma-separated")
ap.add_argument("--json", required=True)
ap.add_argument("--csv", required=True)
args = ap.parse_args()

URL = "https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={}"
UA = "Mozilla/5.0 (compatible; kicad-hub; +https://github.com/JMcordobamendez/kicad-hub)"
batches = sorted({int(b) for b in args.boards.split(",") if b.strip().isdigit() and int(b) > 0}) or [1]


def blank(v):
    return (v or "").strip() in ("", "~")


def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("Reference") and blank(r.get("DNP"))]


def lines(parts):
    """LCSC code -> {"refs": [...], "mpn": ...}, plus the fitted parts without a code."""
    by_code, no_code = {}, []
    for r in parts:
        code = (r.get("LCSC") or "").strip().upper()
        if not re.fullmatch(r"C\d+", code):
            no_code.append(r["Reference"])
            continue
        line = by_code.setdefault(code, {"refs": [], "mpn": (r.get("MPN") or "").strip()})
        line["refs"].append(r["Reference"])
    return by_code, no_code


def fetch(code):
    try:
        req = urllib.request.Request(URL.format(code), headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=20) as resp:
            d = json.load(resp).get("result")
    except Exception as e:  # network, HTTP, bad JSON: report and go on
        return code, {"error": f"lookup failed ({type(e).__name__})"}
    if not d:
        return code, {"error": "not found at LCSC"}
    ladders = sorted((int(p["ladder"]), float(p["usdPrice"])) for p in d.get("productPriceList") or [])
    return code, {
        "model": d.get("productModel") or "",
        "brand": d.get("brandNameEn") or "",
        "stock": int(d.get("stockNumber") or 0),
        "min_order": max(int(d.get("minBuyNumber") or 1), 1),
        "lifecycle": d.get("productCycle") or "",
        "ladders": ladders,
        "currency": d.get("currencyType") or "USD",
    }


def price_at(info, qty):
    p = None
    for ladder, price in info["ladders"]:
        if qty >= ladder:
            p = price
    return p if p is not None else (info["ladders"][0][1] if info["ladders"] else None)


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", s.upper())


builds = [{"name": "default", "parts": load(args.bom)}]
if args.variants:
    try:
        for v in json.load(open(args.variants, encoding="utf-8")):
            builds.append({"name": v["name"], "parts": load(v["bom"])})
    except FileNotFoundError:
        pass

for b in builds:
    b["lines"], b["no_code"] = lines(b["parts"])
codes = sorted({c for b in builds for c in b["lines"]}, key=lambda c: int(c[1:]))
with ThreadPoolExecutor(max_workers=4) as pool:
    info = dict(pool.map(fetch, codes))

out = {"source": "LCSC", "currency": next((i["currency"] for i in info.values() if "currency" in i), "USD"),
       "batches": batches, "builds": [], "warnings": []}
warned = set()


def warn(code, msg):
    if (code, msg) not in warned:
        warned.add((code, msg))
        out["warnings"].append({"lcsc": code, "message": msg})


for b in builds:
    res = {"name": b["name"], "fitted": len(b["parts"]), "no_code": b["no_code"],
           "priced": 0, "unpriced": [], "totals": {}}
    for code, line in b["lines"].items():
        i = info.get(code, {})
        refs = ", ".join(line["refs"])
        if "error" in i or not i.get("ladders"):
            res["unpriced"] += line["refs"]
            warn(code, f"{refs}: {i.get('error', 'no price at LCSC')}")
            continue
        res["priced"] += len(line["refs"])
        per_board = len(line["refs"])
        if per_board * batches[-1] > i["stock"]:
            warn(code, f"{refs}: {i['stock']} in stock, {per_board * batches[-1]} needed for {batches[-1]} boards")
        if i["lifecycle"] and i["lifecycle"].lower() != "normal":
            warn(code, f"{refs}: lifecycle '{i['lifecycle']}' at LCSC")
        if line["mpn"] and i["model"] and norm(line["mpn"]) not in norm(i["model"]) \
                and norm(i["model"]) not in norm(line["mpn"]):
            warn(code, f"{refs}: MPN {line['mpn']} but {code} is {i['model']} ({i['brand']})")
        for n in batches:
            qty = max(per_board * n, i["min_order"])
            res["totals"][n] = res["totals"].get(n, 0.0) + qty * price_at(i, qty)
    res["totals"] = {str(n): round(t, 2) for n, t in res["totals"].items()}
    out["builds"].append(res)

with open(args.json, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)

default = builds[0]
with open(args.csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["LCSC", "References", "Qty per board", "MPN (schematic)", "LCSC model", "Brand", "Stock",
                "Lifecycle", "Min order"] + [f"Unit price @{n} boards" for n in batches]
               + [f"Line total @{n} boards" for n in batches])
    for code, line in sorted(default["lines"].items(), key=lambda kv: int(kv[0][1:])):
        i = info.get(code, {})
        n_ref = len(line["refs"])
        if "error" in i or not i.get("ladders"):
            w.writerow([code, " ".join(line["refs"]), n_ref, line["mpn"], i.get("error", "no price")])
            continue
        qtys = [max(n_ref * n, i["min_order"]) for n in batches]
        unit = [price_at(i, q) for q in qtys]
        w.writerow([code, " ".join(line["refs"]), n_ref, line["mpn"], i["model"], i["brand"], i["stock"],
                    i["lifecycle"], i["min_order"]] + unit + [round(q * u, 4) for q, u in zip(qtys, unit)])

d = out["builds"][0]
print(f"priced {d['priced']} of {d['fitted']} fitted parts; totals {d['totals']}; {len(out['warnings'])} warning(s)")
