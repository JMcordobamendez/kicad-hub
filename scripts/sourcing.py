#!/usr/bin/env python3
"""Cost and stock of the fitted parts from several suppliers, per board and per batch.

Suppliers:
  LCSC, JLCPCB    by the part's LCSC field, no API key (the JSON behind their web
                  pages: unofficial, so it may change; failures are reported)
  Mouser          by MPN, needs MOUSER_API_KEY (free, mouser.com → API)
  Farnell         by MPN, needs FARNELL_API_KEY (free, partner.element14.com);
                  FARNELL_STORE picks the store (default uk.farnell.com)
  DigiKey         by MPN, needs DIGIKEY_CLIENT_ID and DIGIKEY_CLIENT_SECRET
                  (free app at developer.digikey.com, Product Information v4)
Keys come from the environment, so each user brings their own (repository secrets).
When a part has no MPN, the manufacturer part number LCSC reports is used.

Which offer is used for each part: the one named in the selection file (CSV with
columns Part, Supplier[, SKU]; Part = MPN or LCSC code), else the cheapest offer with
enough stock for each batch. Minimum orders, order multiples and price breaks count.
All prices are converted to one currency (rates from frankfurter.app, ECB data).

Writes a JSON for the report and the comparison page, a CSV with every offer, and
an HTML page to compare suppliers and download a selection file.

Usage: sourcing.py --bom BOM.csv [--variants variants.json] [--choices FILE]
                   [--boards 1,5,10,100] [--currency USD]
                   --json OUT.json --csv OUT.csv [--html OUT.html]
"""
import argparse
import csv
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

ap = argparse.ArgumentParser()
ap.add_argument("--bom", required=True, help="per-part BOM of the default build")
ap.add_argument("--variants", help="variants.json from the action (per-part BOM of each variant)")
ap.add_argument("--choices", help="selection file: Part,Supplier[,SKU]")
ap.add_argument("--boards", default="1,5,10,100", help="batch sizes, comma-separated")
ap.add_argument("--currency", default="USD")
ap.add_argument("--json", required=True)
ap.add_argument("--csv", required=True)
ap.add_argument("--html", help="supplier comparison page")
args = ap.parse_args()

UA = "Mozilla/5.0 (compatible; kicad-hub; +https://github.com/JMcordobamendez/kicad-hub)"
CUR = args.currency.upper()
batches = sorted({int(b) for b in args.boards.split(",") if b.strip().isdigit() and int(b) > 0}) or [1]
BAD_LIFECYCLE = re.compile(r"obsolete|end of life|\beol\b|not recommended|nrnd|not for new|last time|"
                           r"no_longer_manufactured|discontinued", re.I)


def blank(v):
    return (v or "").strip() in ("", "~")


def norm(s):
    return re.sub(r"[^A-Z0-9]", "", (s or "").upper())


def http(url, data=None, headers=None, form=False, timeout=20):
    h = {"User-Agent": UA, "Accept": "application/json", **(headers or {})}
    if data is not None and not form:
        data, h["Content-Type"] = json.dumps(data).encode(), "application/json"
    elif data is not None:
        data, h["Content-Type"] = urllib.parse.urlencode(data).encode(), "application/x-www-form-urlencoded"
    with urllib.request.urlopen(urllib.request.Request(url, data=data, headers=h), timeout=timeout) as r:
        return json.load(r)


class Pace:
    """Minimum time between calls to one supplier (their rate limits)."""
    def __init__(self, seconds):
        self.s, self.t, self.lock = seconds, 0.0, threading.Lock()

    def wait(self):
        with self.lock:
            delay = self.t + self.s - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self.t = time.monotonic()


def money(s):
    """'0,10 €' / '$1,234.50' / '1.234,5' -> float"""
    s = re.sub(r"[^\d,.]", "", str(s))
    if "," in s and "." in s:
        s = s.replace(",", "") if s.rfind(".") > s.rfind(",") else s.replace(".", "").replace(",", ".")
    elif "," in s:
        s = s.replace(",", ".")
    return float(s) if s else None


def offer(supplier, **kw):
    o = {"supplier": supplier, "sku": "", "mpn": "", "brand": "", "stock": 0, "moq": 1, "mult": 1,
         "lifecycle": "", "ladders": [], "currency": "USD", "url": "", "note": ""}
    o.update(kw)
    o["ladders"] = sorted((int(q), float(p)) for q, p in o["ladders"] if p is not None)
    o["moq"], o["mult"] = max(int(o["moq"] or 1), 1), max(int(o["mult"] or 1), 1)
    return o


# --- Suppliers -----------------------------------------------------------------------
def lcsc(code):
    d = http(f"https://wmsc.lcsc.com/ftps/wm/product/detail?productCode={code}").get("result")
    if not d:
        return None
    return offer("LCSC", sku=code, mpn=d.get("productModel") or "", brand=d.get("brandNameEn") or "",
                 stock=d.get("stockNumber") or 0, moq=d.get("minBuyNumber"), lifecycle=d.get("productCycle") or "",
                 ladders=[(p["ladder"], p["usdPrice"]) for p in d.get("productPriceList") or []],
                 currency=d.get("currencyType") or "USD", url=f"https://www.lcsc.com/product-detail/{code}.html")


def jlcpcb(code):
    d = http("https://jlcpcb.com/api/overseas-pcb-order/v1/shoppingCart/smtGood/selectSmtComponentList",
             {"keyword": code, "currentPage": 1, "pageSize": 5})
    items = ((d.get("data") or {}).get("componentPageInfo") or {}).get("list") or []
    x = next((i for i in items if (i.get("componentCode") or "").upper() == code), None)
    if not x:
        return None
    lib = {"base": "basic part", "expand": "extended part"}.get(x.get("componentLibraryType"), "")
    return offer("JLCPCB", sku=code, mpn=x.get("componentModelEn") or "", brand=x.get("componentBrandEn") or "",
                 stock=x.get("stockCount") or 0, moq=max(x.get("leastPatchNumber") or 1, x.get("minPurchaseNum") or 1),
                 ladders=[(p["startNumber"], p["productPrice"]) for p in x.get("componentPrices") or []],
                 url=f"https://jlcpcb.com/partdetail/{x.get('urlSuffix') or code}",
                 note=f"for JLCPCB assembly{', ' + lib if lib else ''}")


MOUSER_KEY = os.environ.get("MOUSER_API_KEY", "").strip()
mouser_pace = Pace(2.1)  # 30 requests per minute


def mouser(mpn):
    mouser_pace.wait()
    d = http(f"https://api.mouser.com/api/v1/search/partnumber?apiKey={MOUSER_KEY}",
             {"SearchByPartRequest": {"mouserPartNumber": mpn, "partSearchOptions": "Exact"}})
    if d.get("Errors"):
        raise RuntimeError(d["Errors"][0].get("Message", "error"))
    parts = [p for p in (d.get("SearchResults") or {}).get("Parts") or []
             if p.get("MouserPartNumber") not in (None, "", "N/A") and p.get("PriceBreaks")]
    p = next((p for p in parts if norm(p.get("ManufacturerPartNumber")) == norm(mpn)), None)
    if not p:
        return None
    stock = p.get("AvailabilityInStock") or re.sub(r"\D", "", (p.get("Availability") or "").split(" ")[0]) or 0
    breaks = p["PriceBreaks"]
    return offer("Mouser", sku=p["MouserPartNumber"], mpn=p.get("ManufacturerPartNumber") or "",
                 brand=p.get("Manufacturer") or "", stock=int(stock), moq=money(p.get("Min")) or 1,
                 mult=money(p.get("Mult")) or 1, lifecycle=p.get("LifecycleStatus") or "",
                 ladders=[(b["Quantity"], money(b["Price"])) for b in breaks],
                 currency=breaks[0].get("Currency") or "USD", url=p.get("ProductDetailUrl") or "")


FARNELL_KEY = os.environ.get("FARNELL_API_KEY", "").strip()
FARNELL_STORE = os.environ.get("FARNELL_STORE", "").strip() or "uk.farnell.com"
FARNELL_CUR = {"uk.farnell.com": "GBP", "www.newark.com": "USD", "canada.newark.com": "CAD",
               "ch.farnell.com": "CHF", "se.farnell.com": "SEK", "dk.farnell.com": "DKK",
               "no.farnell.com": "NOK", "pl.farnell.com": "PLN", "cz.farnell.com": "CZK",
               "hu.farnell.com": "HUF"}.get(FARNELL_STORE, "EUR")
farnell_pace = Pace(0.6)  # 2 requests per second


def farnell(mpn):
    farnell_pace.wait()
    q = urllib.parse.urlencode({
        "term": f"manuPartNum:{mpn}", "storeInfo.id": FARNELL_STORE, "resultsSettings.offset": 0,
        "resultsSettings.numberOfResults": 5, "resultsSettings.responseGroup": "large",
        "callInfo.responseDataFormat": "JSON", "callInfo.apiKey": FARNELL_KEY})
    d = http(f"https://api.element14.com/catalog/products?{q}")
    prods = (d.get("manufacturerPartNumberSearchReturn") or {}).get("products") or []
    p = next((p for p in prods if norm(p.get("translatedManufacturerPartNumber")) == norm(mpn) and p.get("prices")), None)
    if not p:
        return None
    return offer("Farnell", sku=p.get("sku") or "", mpn=p.get("translatedManufacturerPartNumber") or "",
                 brand=p.get("brandName") or p.get("vendorName") or "", stock=(p.get("stock") or {}).get("level") or 0,
                 moq=p.get("translatedMinimumOrderQuality") or 1, mult=p.get("orderMultiples") or 1,
                 lifecycle=p.get("productStatus") or "", ladders=[(x["from"], x["cost"]) for x in p["prices"]],
                 currency=FARNELL_CUR, url=f"https://{FARNELL_STORE}/{p.get('sku', '')}")


DK_ID = os.environ.get("DIGIKEY_CLIENT_ID", "").strip()
DK_SECRET = os.environ.get("DIGIKEY_CLIENT_SECRET", "").strip()
DK_CUR = CUR if CUR in ("USD", "EUR", "GBP", "CAD", "CHF", "JPY") else "USD"
dk_token, dk_lock = [None], threading.Lock()


def digikey(mpn):
    with dk_lock:
        if dk_token[0] is None:
            dk_token[0] = http("https://api.digikey.com/v1/oauth2/token", {
                "client_id": DK_ID, "client_secret": DK_SECRET, "grant_type": "client_credentials"},
                form=True)["access_token"]
    d = http("https://api.digikey.com/products/v4/search/keyword", {"Keywords": mpn, "Limit": 5, "Offset": 0},
             headers={"X-DIGIKEY-Client-Id": DK_ID, "Authorization": f"Bearer {dk_token[0]}",
                      "X-DIGIKEY-Locale-Site": "US", "X-DIGIKEY-Locale-Language": "en",
                      "X-DIGIKEY-Locale-Currency": DK_CUR})
    prods = (d.get("ExactMatches") or []) + (d.get("Products") or [])
    p = next((p for p in prods if norm(p.get("ManufacturerProductNumber")) == norm(mpn)), None)
    if not p:
        return None
    variations = [v for v in p.get("ProductVariations") or [] if v.get("StandardPricing")]
    if not variations:
        return None
    # cut tape when there is one (small quantities), else the smallest minimum order
    v = sorted(variations, key=lambda v: ("Cut Tape" not in ((v.get("PackageType") or {}).get("Name") or ""),
                                          v.get("MinimumOrderQuantity") or 1))[0]
    return offer("DigiKey", sku=v.get("DigiKeyProductNumber") or "", mpn=p.get("ManufacturerProductNumber") or "",
                 brand=(p.get("Manufacturer") or {}).get("Name") or "",
                 stock=v.get("QuantityAvailableforPackageType") or p.get("QuantityAvailable") or 0,
                 moq=v.get("MinimumOrderQuantity") or 1, lifecycle=(p.get("ProductStatus") or {}).get("Status") or "",
                 ladders=[(b["BreakQuantity"], b["UnitPrice"]) for b in v["StandardPricing"]],
                 currency=DK_CUR, url=p.get("ProductUrl") or "",
                 note=(v.get("PackageType") or {}).get("Name") or "")


SUPPLIERS = [  # name, lookup, looked up by, enabled
    ("LCSC", lcsc, "lcsc", True),
    ("JLCPCB", jlcpcb, "lcsc", True),
    ("Mouser", mouser, "mpn", bool(MOUSER_KEY)),
    ("Farnell", farnell, "mpn", bool(FARNELL_KEY)),
    ("DigiKey", digikey, "mpn", bool(DK_ID and DK_SECRET)),
]
status = {name: ("ok" if on else "no API key") for name, _, _, on in SUPPLIERS}
errors = {}


def lookup(name, fn, key):
    try:
        return fn(key)
    except Exception as e:  # network, auth, quota, format change: note it and go on
        errors.setdefault(name, f"{type(e).__name__}: {str(e)[:120]}")
        return None


# --- Parts ---------------------------------------------------------------------------------
def load(path):
    with open(path, newline="", encoding="utf-8-sig") as f:
        return [r for r in csv.DictReader(f) if r.get("Reference") and blank(r.get("DNP"))]


builds = [{"name": "default", "parts": load(args.bom)}]
if args.variants and os.path.exists(args.variants):
    for v in json.load(open(args.variants, encoding="utf-8")):
        if os.path.exists(v["bom"]):
            builds.append({"name": v["name"], "parts": load(v["bom"])})

lines = {}  # one per distinct part: MPN if there is one, else LCSC code
for b in builds:
    b["unpriceable"] = []
    for r in b["parts"]:
        mpn, code = (r.get("MPN") or "").strip(), (r.get("LCSC") or "").strip().upper()
        code = code if re.fullmatch(r"C\d+", code) else ""
        key = norm(mpn) or code
        if not key:
            b["unpriceable"].append(r["Reference"])
            continue
        line = lines.setdefault(key, {"key": key, "part": mpn or code, "mpn": mpn, "lcsc": code,
                                      "value": r.get("Value") or "", "footprint": (r.get("Footprint") or "").split(":")[-1],
                                      "refs": {}, "offers": []})
        line["lcsc"] = line["lcsc"] or code
        line["refs"].setdefault(b["name"], []).append(r["Reference"])


def fetch_line(line):
    offers = []
    for name, fn, by, on in SUPPLIERS[:2]:
        if line["lcsc"]:
            o = lookup(name, fn, line["lcsc"])
            offers += [o] if o else []
    mpn = line["mpn"] or next((o["mpn"] for o in offers if o["mpn"]), "")
    for name, fn, by, on in SUPPLIERS[2:]:
        if on and mpn:
            o = lookup(name, fn, mpn)
            offers += [o] if o else []
    line["offers"] = offers
    line["mpn_used"] = mpn


with ThreadPoolExecutor(max_workers=6) as pool:
    list(pool.map(fetch_line, lines.values()))
for name, err in errors.items():
    status[name] = f"failed ({err})"

# --- Currency ---------------------------------------------------------------------------------
rates = {CUR: 1.0}
needed = {o["currency"] for l in lines.values() for o in l["offers"]} - {CUR}
if needed:
    try:
        rates.update(http(f"https://api.frankfurter.app/latest?from={CUR}")["rates"])
    except Exception as e:
        print(f"exchange rates unavailable ({e}): offers in other currencies are left out")
for l in lines.values():
    kept = []
    for o in l["offers"]:
        if o["currency"] in rates:
            o["ladders"] = [(q, round(p / rates[o["currency"]], 6)) for q, p in o["ladders"]]
            kept.append(o)
    l["offers"] = kept


# --- Selection and totals ----------------------------------------------------------------------
def order(o, need):
    qty = max(need, o["moq"])
    qty = -(-qty // o["mult"]) * o["mult"]
    price = next((p for q, p in reversed(o["ladders"]) if qty >= q), o["ladders"][0][1] if o["ladders"] else None)
    return qty, price, (qty * price if price is not None else None)


choices = {}
if args.choices and os.path.exists(args.choices):
    with open(args.choices, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if norm(r.get("Part")) and (r.get("Supplier") or "").strip():
                choices[norm(r["Part"])] = ((r.get("Supplier") or "").strip().lower(), (r.get("SKU") or "").strip())

out = {"currency": CUR, "batches": batches, "suppliers": [{"name": n, "status": status[n]} for n, *_ in SUPPLIERS],
       "choices_file": bool(choices), "lines": [], "builds": [], "warnings": []}
warned = set()


def warn(part, msg):
    if (part, msg) not in warned:
        warned.add((part, msg))
        out["warnings"].append({"part": part, "message": msg})


for key in sorted(lines, key=lambda k: (min((r for refs in lines[k]["refs"].values() for r in refs)), k)):
    l = lines[key]
    c = choices.get(norm(l["mpn"])) or choices.get(norm(l["lcsc"]))
    l["choice"] = None
    if c:
        idx = next((i for i, o in enumerate(l["offers"]) if o["supplier"].lower() == c[0]
                    and (not c[1] or norm(o["sku"]) == norm(c[1]))), None)
        if idx is None:
            warn(l["part"], f"selection file asks for {c[0]} {c[1]}".rstrip() + ", not offered now: cheapest used")
        l["choice"] = idx
    if l["lcsc"] and l["mpn"]:
        m = next((o["mpn"] for o in l["offers"] if o["supplier"] == "LCSC"), "")
        if m and norm(m) not in norm(l["mpn"]) and norm(l["mpn"]) not in norm(m):
            warn(l["part"], f"LCSC code {l['lcsc']} is {m}, not the MPN {l['mpn']}")
    for o in l["offers"]:
        if o["lifecycle"] and BAD_LIFECYCLE.search(o["lifecycle"]):
            warn(l["part"], f"{o['supplier']}: lifecycle '{o['lifecycle']}'")
    if not l["offers"]:
        refs = ", ".join(l["refs"].get("default") or next(iter(l["refs"].values())))
        warn(l["part"], f"{refs}: not found at any supplier")
    out["lines"].append(l)


def pick(l, need):
    """Offer index for this quantity: the chosen one, else the cheapest with enough stock."""
    if l["choice"] is not None:
        return l["choice"]
    costed = [(order(o, need)[2], i) for i, o in enumerate(l["offers"]) if o["ladders"]]
    if not costed:
        return None
    in_stock = [(c, i) for c, i in costed if l["offers"][i]["stock"] >= need]
    return min(in_stock or costed)[1]


for b in builds:
    res = {"name": b["name"], "fitted": len(b["parts"]), "unpriceable": b["unpriceable"], "unpriced": [],
           "totals": {}, "by_supplier": {}}
    for l in out["lines"]:
        refs = l["refs"].get(b["name"])
        if not refs:
            continue
        if not any(o["ladders"] for o in l["offers"]):
            res["unpriced"] += refs
            continue
        for n in batches:
            need = len(refs) * n
            o = l["offers"][pick(l, need)]
            res["totals"][str(n)] = res["totals"].get(str(n), 0.0) + order(o, need)[2]
            res["by_supplier"].setdefault(str(n), {})
            res["by_supplier"][str(n)][o["supplier"]] = res["by_supplier"][str(n)].get(o["supplier"], 0) + 1
            if b["name"] == "default" and n == batches[-1] and o["stock"] < need:
                warn(l["part"], f"{', '.join(refs)}: {o['supplier']} has {o['stock']} in stock, "
                                f"{need} needed for {n} boards")
    res["totals"] = {k: round(v, 2) for k, v in res["totals"].items()}
    res["priced"] = res["fitted"] - len(res["unpriceable"]) - len(res["unpriced"])
    out["builds"].append(res)

with open(args.json, "w", encoding="utf-8") as f:
    json.dump(out, f, indent=1)

# Every offer, one row each; "Used @n" marks the offer the totals use for that batch
with open(args.csv, "w", newline="", encoding="utf-8") as f:
    w = csv.writer(f)
    w.writerow(["Part", "References", "Qty per board", "Supplier", "SKU", "Manufacturer PN", "Brand", "Stock",
                "Min order", "Multiple", "Lifecycle", "Note"] + [f"Unit price @{n} ({CUR})" for n in batches]
               + [f"Line total @{n} ({CUR})" for n in batches] + [f"Used @{n}" for n in batches] + ["URL"])
    for l in out["lines"]:
        refs = l["refs"].get("default")
        if not refs:
            continue
        if not l["offers"]:
            w.writerow([l["part"], " ".join(refs), len(refs), "not found"])
        for i, o in enumerate(l["offers"]):
            orders = [order(o, len(refs) * n) for n in batches]
            used = ["yes" if pick(l, len(refs) * n) == i else "" for n in batches]
            w.writerow([l["part"], " ".join(refs), len(refs), o["supplier"], o["sku"], o["mpn"], o["brand"], o["stock"],
                        o["moq"], o["mult"], o["lifecycle"], o["note"]] + [x[1] for x in orders]
                       + [round(x[2], 4) if x[2] is not None else "" for x in orders] + used + [o["url"]])

if args.html:
    tpl = open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "sourcing.html"), encoding="utf-8").read()
    data = json.dumps(out, separators=(",", ":")).replace("</", "<\\/")
    with open(args.html, "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*KICAD_HUB_DATA*/null", data))

d = out["builds"][0]
print(f"{d['priced']} of {d['fitted']} fitted parts priced; totals {d['totals']}; "
      f"suppliers: {', '.join(s['name'] + ' ' + s['status'] for s in out['suppliers'])}")
