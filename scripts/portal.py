#!/usr/bin/env python3
"""Design portal pages for GitHub Pages.

  portal.py build  ...  one build's page: KiCanvas viewer of the schematic and PCB,
                        report, interactive BOM, BOM, supplier comparison, datasheets
                        and downloads, all copied into --site
  portal.py index  ...  the portal's front page, listing every build published in
                        the site branch (main, releases, pull requests)

Both fill scripts/portal.html (one template, two modes) with a JSON blob.
"""
import argparse
import datetime
import glob
import json
import os
import re
import shutil

HERE = os.path.dirname(os.path.abspath(__file__))

ap = argparse.ArgumentParser()
sub = ap.add_subparsers(dest="cmd", required=True)
b = sub.add_parser("build")
b.add_argument("--site", required=True, help="output folder of this build")
b.add_argument("--out", required=True, help="the action's output folder (fab)")
b.add_argument("--project-dir", required=True)
b.add_argument("--name", required=True)
b.add_argument("--rev", default="")
b.add_argument("--label", required=True, help="what this build is: main, PR #3, v1.2…")
b.add_argument("--kind", required=True, choices=("branch", "pr", "release"))
b.add_argument("--sha", required=True)
b.add_argument("--repo", required=True, help="owner/name")
b.add_argument("--server", default="https://github.com")
b.add_argument("--run-url", default="")
b.add_argument("--title", default="", help="pull request title")
i = sub.add_parser("index")
i.add_argument("--root", required=True, help="checkout of the site branch")
i.add_argument("--repo", required=True)
i.add_argument("--server", default="https://github.com")
args = ap.parse_args()


def page(data, dest):
    tpl = open(os.path.join(HERE, "portal.html"), encoding="utf-8").read()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*KICAD_HUB_DATA*/null", blob))


def first(pattern):
    found = sorted(glob.glob(pattern))
    return found[0] if found else None


def build():
    site, out = args.site, args.out
    os.makedirs(site, exist_ok=True)

    # KiCad files for the viewer, with the folder layout the sheets refer to
    design = os.path.join(site, "design")
    sheets, pcb = [], None
    root_sch = os.path.join(args.project_dir, args.name + ".kicad_sch")
    for path in sorted(glob.glob(os.path.join(args.project_dir, "**", "*.kicad_sch"), recursive=True)):
        rel = os.path.relpath(path, args.project_dir).replace(os.sep, "/")
        if rel.startswith((".", "fab/")) or "/." in rel:
            continue
        os.makedirs(os.path.dirname(os.path.join(design, rel)), exist_ok=True)
        shutil.copy2(path, os.path.join(design, rel))
        sheets.append(rel)
    if os.path.exists(root_sch):  # root sheet first, then in the order the root lists them
        order = re.findall(r'"Sheetfile" "([^"]+)"', open(root_sch, encoding="utf-8").read())
        rank = {s: n for n, s in enumerate([args.name + ".kicad_sch"] + order)}
        sheets.sort(key=lambda s: (rank.get(s, len(rank)), s))
    for ext in ("kicad_pcb", "kicad_pro"):
        src = os.path.join(args.project_dir, f"{args.name}.{ext}")
        if os.path.exists(src):
            os.makedirs(design, exist_ok=True)
            shutil.copy2(src, design)
            if ext == "kicad_pcb" and "(layer \"Edge.Cuts\")" in open(src, encoding="utf-8").read():
                pcb = f"design/{args.name}.kicad_pcb"

    # Outputs of the run
    views, downloads = {}, []

    def take(src, dest, label=None, view=None):
        if not src or not os.path.exists(src):
            return
        target = os.path.join(site, dest)
        if os.path.isdir(src):
            shutil.copytree(src, target, dirs_exist_ok=True)
        else:
            os.makedirs(os.path.dirname(target) or site, exist_ok=True)
            shutil.copy2(src, target)
        if view:
            views[view] = dest
        if label:
            downloads.append({"label": label, "file": dest, "size": os.path.getsize(src) if os.path.isfile(src) else None})

    take(first(os.path.join(out, "ibom", "*.html")), "ibom.html", view="ibom")
    take(first(os.path.join(out, "bom", "*.html")), "bom.html", view="bom")
    take(os.path.join(out, "sourcing.html"), "sourcing.html", view="sourcing")
    if os.path.exists(os.path.join(out, "datasheets", "index.html")):
        take(os.path.join(out, "datasheets"), "datasheets", view="datasheets")
    zip_ = first(os.path.join(out, "package", "*.zip"))
    take(zip_, os.path.basename(zip_) if zip_ else "", "Fab package (Gerber, drill, BOM, pick & place, notes)")
    pdf = first(os.path.join(out, "schematic", "*.pdf"))
    take(pdf, "schematic.pdf", "Schematic (PDF)", view="pdf")
    take(first(os.path.join(out, "bom", "*.csv")), "bom.csv", "BOM (CSV)")
    take(os.path.join(out, "reports", "sourcing.csv"), "sourcing.csv", "Supplier offers (CSV)")
    report = os.path.join(out, "reports", "report.md")
    md = open(report, encoding="utf-8").read() if os.path.exists(report) else ""
    md = re.sub(r"^<!-- kicad-hub:[^>]*-->\s*", "", md)
    md = re.sub(r"^🔎 .*\n\n?", "", md, flags=re.M)  # the link to this very page

    counts_path = os.path.join(out, "reports", "counts.json")
    counts = json.load(open(counts_path, encoding="utf-8")) if os.path.exists(counts_path) else {}
    cost = None
    src_path = os.path.join(out, "reports", "sourcing.json")
    if os.path.exists(src_path):
        s = json.load(open(src_path, encoding="utf-8"))
        totals = s["builds"][0]["totals"] if s.get("builds") else {}
        if totals:
            n = s["batches"][0]
            cost = {"boards": n, "total": totals.get(str(n)), "currency": s["currency"]}

    info = {"mode": "build", "name": args.name, "rev": args.rev, "label": args.label, "kind": args.kind,
            "sha": args.sha, "repo": args.repo, "server": args.server, "run_url": args.run_url,
            "title": args.title, "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sheets": [f"design/{s}" for s in sheets], "pcb": pcb, "views": views, "downloads": downloads,
            "report": md, "counts": counts, "cost": cost}
    page(info, os.path.join(site, "index.html"))
    summary = {k: info[k] for k in ("name", "rev", "label", "kind", "sha", "date", "title", "counts", "cost")}
    with open(os.path.join(site, "build.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)


def index():
    builds = []
    for path in sorted(glob.glob(os.path.join(args.root, "**", "build.json"), recursive=True)):
        rel = os.path.relpath(os.path.dirname(path), args.root).replace(os.sep, "/")
        try:
            d = json.load(open(path, encoding="utf-8"))
        except ValueError:
            continue
        d["path"] = rel + "/"
        builds.append(d)
    # the front page reads builds.json, so the cleanup workflow only has to rewrite that
    with open(os.path.join(args.root, "builds.json"), "w", encoding="utf-8") as f:
        json.dump(builds, f, indent=1)
    page({"mode": "index", "repo": args.repo, "server": args.server}, os.path.join(args.root, "index.html"))
    open(os.path.join(args.root, ".nojekyll"), "w").close()  # serve files as they are, no Jekyll


build() if args.cmd == "build" else index()
