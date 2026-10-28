#!/usr/bin/env python3
"""Resolve, check and bundle the datasheet of every fitted part.

Sources, in order:
  1. the symbol's Datasheet field: a URL, or a file path (relative to the project,
     or with ${KIPRJMOD})
  2. if the field is empty: a file in one of the datasheet folders whose name
     contains the part's MPN or value (e.g. CN3722.pdf for a "CN3722" part)

Local files are copied to <out>/datasheets/, URLs are checked, and an index.html
links every part to its datasheet. A JSON with the result per part is written for
the report.
"""
import argparse
import concurrent.futures
import csv
import html
import json
import os
import re
import shutil
import urllib.error
import urllib.request

DOC_EXT = (".pdf", ".zip", ".htm", ".html", ".txt", ".png", ".jpg")

ap = argparse.ArgumentParser()
ap.add_argument("--bom", required=True)
ap.add_argument("--project-dir", required=True)
ap.add_argument("--dirs", default="", help="comma-separated folders with datasheet files")
ap.add_argument("--out-dir", required=True)
ap.add_argument("--check-urls", default="true")
ap.add_argument("--json", required=True)
args = ap.parse_args()


def blank(v):
    return (v or "").strip() in ("", "~")


def norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def ref_key(ref):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


with open(args.bom, newline="", encoding="utf-8-sig") as f:
    parts = [r for r in csv.DictReader(f) if r.get("Reference") and blank(r.get("DNP"))]

proj = os.path.normpath(args.project_dir)
dirs = [d.strip() for d in args.dirs.split(",") if d.strip()]
dirs += [os.path.join(proj, d) for d in ("datasheets", "datasheet", "docs")]
dirs += [os.path.join(proj, "..", d) for d in ("datasheets", "datasheet")]
folder_files = {}
for d in dirs:
    if os.path.isdir(d):
        for name in sorted(os.listdir(d)):
            if name.lower().endswith(DOC_EXT):
                folder_files.setdefault(os.path.normpath(os.path.join(d, name)), norm(os.path.splitext(name)[0]))

ds_out = os.path.join(args.out_dir, "datasheets")
os.makedirs(ds_out, exist_ok=True)
copied = {}  # source path -> name inside datasheets/


def bundle(path):
    if path not in copied:
        name, n = os.path.basename(path), 1
        while name in copied.values():
            stem, ext = os.path.splitext(os.path.basename(path))
            name, n = f"{stem}_{n}{ext}", n + 1
        shutil.copy2(path, os.path.join(ds_out, name))
        copied[path] = name
    return copied[path]


def family_tokens(path):
    """Part-number-like pieces of a file name. One PDF often covers a family:
    'S-82A2A_B_C.pdf' or 'AP63200-AP63201-AP63203-AP63205.pdf'."""
    stem = os.path.splitext(os.path.basename(path))[0]
    toks = {norm(t) for t in re.split(r"[_\s]+", stem)} | {norm(t) for t in re.split(r"[-_\s]+", stem)}
    return {t for t in toks if len(t) >= 5}


def folder_match(r):
    keys = [norm(k) for k in (r.get("MPN"), r.get("Value")) if k and len(norm(k)) >= 4]
    for p, stem in folder_files.items():
        # whole MPN/value in the name (CN3722.pdf), or the name's family code at the
        # start of the MPN (S-82A2A… → S-82A2AAA-A8T2U)
        if any(k in stem or any(k.startswith(t) for t in family_tokens(p)) for k in keys):
            return p
    return None


result = {}
for r in sorted(parts, key=lambda r: ref_key(r["Reference"])):
    ref, ds = r["Reference"], (r.get("Datasheet") or "").strip()
    e = {"value": r.get("Value", ""), "mpn": r.get("MPN", "") or r.get("LCSC", "")}
    if not blank(ds) and re.match(r"https?://", ds, re.I):
        e.update(source="url", target=ds, status="pending")
        hit = folder_match(r)
        if hit:  # offline copy too, so the package doesn't depend on the vendor's URL
            e["local"] = "datasheets/" + bundle(hit)
    elif not blank(ds):
        path = ds.replace("${KIPRJMOD}", proj).replace("$(KIPRJMOD)", proj)
        if re.match(r"[A-Za-z]:[\\/]", path) or path.startswith("/"):
            e.update(source="file", target=ds, status="broken", why="absolute path outside the repo")
        else:
            path = os.path.normpath(os.path.join(proj, path) if not path.startswith(proj) else path)
            if os.path.isfile(path):
                e.update(source="file", target="datasheets/" + bundle(path), status="ok")
            else:
                e.update(source="file", target=ds, status="broken", why="file not found")
    else:
        hit = folder_match(r)
        if hit:
            e.update(source="folder", target="datasheets/" + bundle(hit), status="ok", file=os.path.relpath(hit))
        else:
            e.update(source="none", target="", status="missing")
    result[ref] = e


def check(url):
    """ok / broken (404, 410, DNS) / unverified (site blocks bots, timeouts, auth)."""
    headers = {"User-Agent": "Mozilla/5.0 (kicad-hub datasheet check)"}
    for method in ("HEAD", "GET"):
        try:
            req = urllib.request.Request(url, method=method, headers=headers)
            with urllib.request.urlopen(req, timeout=15) as resp:
                return "ok", str(resp.status)
        except urllib.error.HTTPError as err:
            if err.code in (404, 410):
                return "broken", f"HTTP {err.code}"
            if method == "GET":
                return "unverified", f"HTTP {err.code}"
        except urllib.error.URLError as err:
            if method == "GET":
                reason = str(err.reason)
                dns = "Name or service not known" in reason or "getaddrinfo" in reason
                return ("broken" if dns else "unverified"), reason[:80]
        except Exception as err:  # timeouts, SSL…
            if method == "GET":
                return "unverified", type(err).__name__
    return "unverified", ""


urls = sorted({e["target"] for e in result.values() if e["status"] == "pending"})
if args.check_urls.lower() == "true" and urls:
    with concurrent.futures.ThreadPoolExecutor(8) as pool:
        checked = dict(zip(urls, pool.map(check, urls)))
else:
    checked = {u: ("unverified", "not checked") for u in urls}
for e in result.values():
    if e["status"] == "pending":
        e["status"], e["why"] = checked[e["target"]]

# --- index.html ----------------------------------------------------------------
badge = {"ok": "#1a7f37", "broken": "#cf222e", "unverified": "#9a6700", "missing": "#59636e"}
rows = []
for ref, e in result.items():
    t = html.escape(e["target"])
    link = f'<a href="{t}">{html.escape(os.path.basename(e["target"]) if e["source"] != "url" else e["target"])}</a>' if t else "—"
    if e.get("local"):
        link += f' · <a href="{html.escape(os.path.basename(e["local"]))}">local copy</a>'
    rows.append(f'<tr><td>{html.escape(ref)}</td><td>{html.escape(e["value"])}</td><td>{html.escape(e["mpn"])}</td>'
                f'<td>{link}</td><td style="color:{badge[e["status"]]};font-weight:600">{e["status"]}</td>'
                f'<td>{html.escape(e.get("why", "") if e["status"] != "ok" else "")}</td></tr>')
page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><title>Datasheets</title>
<style>body{{font-family:system-ui,sans-serif;margin:24px;color:#1f2328}}table{{border-collapse:collapse}}
td,th{{border-bottom:1px solid #d1d9e0;padding:6px 10px;text-align:left;font-size:14px}}a{{color:#0b5cad}}</style>
</head><body><h1>Datasheets</h1><table><tr><th>Part</th><th>Value</th><th>MPN</th><th>Datasheet</th><th>Status</th><th></th></tr>
{''.join(rows)}</table></body></html>"""
with open(os.path.join(ds_out, "index.html"), "w", encoding="utf-8") as f:
    f.write(page)

with open(args.json, "w", encoding="utf-8") as f:
    json.dump(result, f, indent=1)
