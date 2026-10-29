#!/usr/bin/env python3
"""Design portal pages for GitHub Pages.

  portal.py build  ...  one build's page: KiCanvas viewer of the schematic and PCB,
                        report, interactive BOM, BOM, supplier comparison, datasheets
                        and downloads, all copied into --site
  portal.py index  ...  the portal's front page and compare page, listing every build
                        published in the site branch (main, history, releases, pull
                        requests); prunes the history of main to --keep-history

`build --kind history` is a light snapshot of a commit on main for the timeline: design
files (if small), the per-page renders and the parts BOM, no fab package, offers or PDFs.

portal.html (one template, two modes) and compare.html are filled with a JSON blob.
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
b.add_argument("--kind", required=True, choices=("branch", "pr", "release", "history"))
b.add_argument("--sha", required=True)
b.add_argument("--repo", required=True, help="owner/name")
b.add_argument("--server", default="https://github.com")
b.add_argument("--run-url", default="")
b.add_argument("--title", default="", help="pull request title")
b.add_argument("--base-ref", default="", help="pull request target branch")
b.add_argument("--branch", default="", help="branch of a branch build or a history snapshot")
b.add_argument("--commit-date", default="", help="commit date, ISO 8601")
b.add_argument("--commit-author", default="")
b.add_argument("--commit-message", default="", help="first line of the commit message")
b.add_argument("--renders", default="", help="folder with the PNG renders and renders.json (renders.py)")
b.add_argument("--light", action="store_true", help="snapshot for the timeline: no fab package, offers, PDFs")
i = sub.add_parser("index")
i.add_argument("--root", required=True, help="checkout of the site branch")
i.add_argument("--repo", required=True)
i.add_argument("--server", default="https://github.com")
i.add_argument("--keep-history", type=int, default=-1, help="keep the newest N snapshots in history/ (-1 = all)")
args = ap.parse_args()


def page(data, dest, template="portal.html"):
    tpl = open(os.path.join(HERE, template), encoding="utf-8").read()
    blob = json.dumps(data, separators=(",", ":")).replace("</", "<\\/")
    with open(dest, "w", encoding="utf-8") as f:
        f.write(tpl.replace("/*KICAD_HUB_DATA*/null", blob))


def first(pattern):
    found = sorted(glob.glob(pattern))
    return found[0] if found else None


LIGHT_DESIGN_MAX = 6_000_000  # bytes of KiCad files a light snapshot keeps for the viewer


def part_info(out, site, light=False):
    """Reference -> what the side panel shows when the part is clicked in the viewer."""
    import csv
    parts = {}
    bom = os.path.join(out, "reports", "bom-parts.csv")
    if not os.path.exists(bom):
        return parts
    with open(bom, newline="", encoding="utf-8-sig") as f:
        for r in csv.DictReader(f):
            if not r.get("Reference"):
                continue
            parts[r["Reference"]] = {k: v for k, v in {
                "value": r.get("Value"), "footprint": (r.get("Footprint") or "").split(":")[-1],
                "mpn": r.get("MPN"), "manufacturer": r.get("Manufacturer"), "lcsc": r.get("LCSC"),
                "voltage": r.get("Voltage"), "dnp": bool((r.get("DNP") or "").strip())}.items() if v not in ("", None, "~")}
    if light:  # snapshots carry no datasheets or supplier offers
        return parts
    ds_path = os.path.join(out, "reports", "datasheets.json")
    if os.path.exists(ds_path):
        for ref, d in json.load(open(ds_path, encoding="utf-8")).items():
            if ref not in parts:
                continue
            local = d.get("local") or (d.get("target") if d.get("source") in ("file", "folder") else "")
            if local and os.path.exists(os.path.join(site, "datasheets", os.path.basename(local))):
                parts[ref]["datasheet"] = "datasheets/" + os.path.basename(local)
            elif d.get("source") == "url":
                parts[ref]["datasheet"] = d.get("target")
    src_path = os.path.join(out, "reports", "sourcing.json")
    if os.path.exists(src_path):
        s = json.load(open(src_path, encoding="utf-8"))
        for line in s.get("lines", []):
            refs = line.get("refs", {}).get("default") or []
            offers = []
            for o in line.get("offers", []):
                if not o.get("ladders"):
                    continue
                price = next((p for q, p in reversed(o["ladders"]) if len(refs) >= q), o["ladders"][0][1])
                offers.append({"supplier": o["supplier"], "sku": o["sku"], "stock": o["stock"],
                               "moq": o["moq"], "price": price, "url": o.get("url", "")})
            offers.sort(key=lambda o: (o["stock"] < len(refs), o["price"]))
            for ref in refs:
                if ref in parts:
                    parts[ref]["offers"] = offers[:4]
                    parts[ref]["currency"] = s["currency"]
    return parts


def ref_key(ref):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


def load_bom(path):
    import csv
    if not os.path.exists(path):
        return None
    with open(path, newline="", encoding="utf-8-sig") as f:
        return {r["Reference"]: r for r in csv.DictReader(f) if r.get("Reference")}


def changes(out, site):
    """What this pull request changes: visual diff pages and BOM lines against the base branch."""
    visual = []
    vpath = os.path.join(out, "reports", "visual.json")
    if os.path.exists(vpath):
        for p in json.load(open(vpath, encoding="utf-8")):
            img, full = (os.path.join(out, "visual", p.get(k) or "") for k in ("image", "full"))
            if not (os.path.isfile(img) and os.path.isfile(full)):
                continue
            os.makedirs(os.path.join(site, "visual"), exist_ok=True)
            for f in (img, full):
                shutil.copy2(f, os.path.join(site, "visual", os.path.basename(f)))
            entry = {"kind": p["kind"], "title": p["title"], "status": p.get("status"), "areas": p.get("areas"),
                     "image": "visual/" + os.path.basename(img), "full": "visual/" + os.path.basename(full)}
            # both versions of the page, for the interactive compare (swipe / fade)
            pair = [os.path.join(out, "visual", p.get(k) or "") for k in ("before", "after")]
            if all(p.get(k) for k in ("before", "after")) and all(os.path.isfile(f) for f in pair):
                for f, k in zip(pair, ("before", "after")):
                    shutil.copy2(f, os.path.join(site, "visual", os.path.basename(f)))
                    entry[k] = "visual/" + os.path.basename(f)
            visual.append(entry)
    head = load_bom(os.path.join(out, "reports", "bom-parts.csv")) or {}
    base = load_bom(os.path.join(out, "reports", "bom-parts-base.csv"))
    bom = None
    if base is not None:
        compare = [c for c in (next(iter(head.values())).keys() if head else []) if c != "Reference"]

        def line(r, src):
            return {"ref": r, "value": src[r].get("Value") or "", "footprint": (src[r].get("Footprint") or "").split(":")[-1]}
        bom = {"added": [line(r, head) for r in sorted(set(head) - set(base), key=ref_key)],
               "removed": [line(r, base) for r in sorted(set(base) - set(head), key=ref_key)], "changed": []}
        for ref in sorted(set(head) & set(base), key=ref_key):
            diffs = [f"{c}: {(base[ref].get(c) or '').strip() or '—'} → {(head[ref].get(c) or '').strip() or '—'}"
                     for c in compare if (base[ref].get(c) or "").strip() != (head[ref].get(c) or "").strip()]
            if diffs:
                bom["changed"].append({"ref": ref, "value": head[ref].get("Value") or "", "diffs": diffs})
    return {"base": args.base_ref, "visual": visual, "bom": bom}


def light_report(counts, commit, viewer):
    """Report tab of a history snapshot: commit and check counts only."""
    lines = [f"**{commit['message']}**" if commit.get("message") else "**Snapshot of main**", ""]
    who = " · ".join(x for x in (commit.get("author"), commit.get("date", "")[:16].replace("T", " ")) if x)
    if who:
        lines += [who, ""]
    rows = [(label, counts.get(k)) for label, k in (("ERC errors", "erc_errors"), ("DRC errors", "drc_errors"),
                                                       ("Parts missing fields", "missing_fields")) if counts.get(k) is not None]
    if rows:
        lines += ["| Check | |", "|---|---|"] + [f"| {a} | {b} |" for a, b in rows] + [""]
    lines.append("_Light snapshot kept for the timeline: no fab package, supplier offers or PDFs" +
                 ("" if viewer else "; the design is too big to embed, use the compare page for the sheet and layer renders") +
                 ". The full build of the latest commit is under `main/`; releases keep their full pages._")
    return "\n".join(lines)


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

    if args.light and os.path.isdir(design):
        total = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(design) for f in fs)
        if total > LIGHT_DESIGN_MAX:  # too big to keep ten of them: renders and BOM only
            shutil.rmtree(design)
            sheets, pcb = [], None

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

    if not args.light:
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
    # What the compare page reads from every build: the parts BOM and the page renders
    bom_csv = os.path.join(out, "reports", "bom-parts.csv")
    has_bom = os.path.isfile(bom_csv)
    if has_bom:
        shutil.copy2(bom_csv, os.path.join(site, "bom-parts.csv"))
    has_renders = bool(args.renders) and os.path.isfile(os.path.join(args.renders, "renders.json"))
    if has_renders:
        shutil.copytree(args.renders, os.path.join(site, "renders"), dirs_exist_ok=True)
    report = os.path.join(out, "reports", "report.md")
    md = open(report, encoding="utf-8").read() if os.path.exists(report) and not args.light else ""
    md = re.sub(r"^<!-- kicad-hub:[^>]*-->\s*", "", md)
    md = re.sub(r"^🔎 .*\n\n?", "", md, flags=re.M)  # the link to this very page

    counts_path = os.path.join(out, "reports", "counts.json")
    counts = json.load(open(counts_path, encoding="utf-8")) if os.path.exists(counts_path) else {}
    cost = None
    src_path = os.path.join(out, "reports", "sourcing.json")
    if os.path.exists(src_path) and not args.light:
        s = json.load(open(src_path, encoding="utf-8"))
        totals = s["builds"][0]["totals"] if s.get("builds") else {}
        if totals:
            n = s["batches"][0]
            cost = {"boards": n, "total": totals.get(str(n)), "currency": s["currency"]}

    commit = {k: v for k, v in (("date", args.commit_date), ("author", args.commit_author),
                                ("message", args.commit_message)) if v}
    if args.light:
        md = light_report(counts, commit, bool(sheets or pcb))
    parts = part_info(out, site, args.light)
    info = {"mode": "build", "name": args.name, "rev": args.rev, "label": args.label, "kind": args.kind,
            "sha": args.sha, "repo": args.repo, "server": args.server, "run_url": args.run_url,
            "title": args.title, "date": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
            "sheets": [f"design/{s}" for s in sheets], "pcb": pcb, "views": views, "downloads": downloads,
            "report": md, "counts": counts, "cost": cost, "parts": parts,
            "branch": args.branch, "commit": commit, "light": args.light,
            "changes": changes(out, site) if args.kind == "pr" else None}
    page(info, os.path.join(site, "index.html"))
    summary = {k: info[k] for k in ("name", "rev", "label", "kind", "sha", "date", "title", "counts", "cost")}
    # builds.json is these summaries plus their folder, so the cleanup workflow can rebuild
    # it from the build.json files alone
    summary.update({k: v for k, v in (("branch", args.branch), ("commit", commit)) if v})
    if has_bom:
        summary["bom"] = "bom-parts.csv"
    if has_renders:
        summary["renders"] = "renders/renders.json"
    if args.light:
        summary["light"] = True
    with open(os.path.join(site, "build.json"), "w", encoding="utf-8") as f:
        json.dump(summary, f, indent=1)


def stamp(d):
    """Commit time (else publish time) of a build.json, as a sortable number."""
    for value, fmt in (((d.get("commit") or {}).get("date"), None), (d.get("date"), "%Y-%m-%d %H:%M UTC")):
        if not value:
            continue
        try:
            if fmt:
                t = datetime.datetime.strptime(value, fmt).replace(tzinfo=datetime.timezone.utc)
            else:
                t = datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))
            return t.timestamp()
        except ValueError:
            continue
    return 0


def prune_history(keep):
    """Drop the oldest history/<sha>/ folders beyond the newest `keep`."""
    found = []
    for path in glob.glob(os.path.join(args.root, "history", "*", "build.json")):
        try:
            found.append((stamp(json.load(open(path, encoding="utf-8"))), os.path.dirname(path)))
        except ValueError:
            found.append((0, os.path.dirname(path)))
    for _, folder in sorted(found, reverse=True)[keep:]:
        shutil.rmtree(folder, ignore_errors=True)


def index():
    if args.keep_history >= 0:
        prune_history(args.keep_history)
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
    assets = os.path.join(args.root, "assets")  # viewer libraries shared by every page
    os.makedirs(assets, exist_ok=True)
    for js in glob.glob(os.path.join(HERE, "vendor", "*.js")):
        shutil.copy2(js, assets)
    shutil.copy2(os.path.join(HERE, "compare.js"), assets)  # compare widget, shared by both pages
    data = {"repo": args.repo, "server": args.server}
    page({"mode": "index", **data}, os.path.join(args.root, "index.html"))
    page(data, os.path.join(args.root, "compare.html"), "compare.html")
    open(os.path.join(args.root, ".nojekyll"), "w").close()  # serve files as they are, no Jekyll


build() if args.cmd == "build" else index()
