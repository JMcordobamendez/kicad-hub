#!/usr/bin/env python3
"""kicad-hub's engine: the steps of the action as one program, for CI and for a PC.

    kicad_hub.py checks [--project-dir DIR] [--out DIR] [--fab-profile NAME]
    kicad_hub.py bom    [--project-dir DIR] [--out DIR] [--required-fields LIST]
                        [--variants auto|none|LIST] [--base REF [--fetch]]
  both: [--kicad-cli PATH | --image IMAGE [--kicad-home DIR]]
    kicad_hub.py datasheets [--project-dir DIR] [--out DIR] [--dirs LIST] [--check-links true|false]
    kicad_hub.py sourcing   [--project-dir DIR] [--out DIR] [--boards 1,5,10,100] [--currency USD]
                            [--choices kicad-hub-sourcing.csv]

`checks`: ERC and DRC (with schematic parity) of the project, as JSON and text reports in
<out>/reports/, plus a second DRC with a fab's rules when --fab-profile is given.

`bom`: one row per part in <out>/reports/bom-parts.csv, the same for every design variant
(listed in variants.json) and, with --base, for that commit or branch too, so the report
can say what changed.

Without --image they use the KiCad installed on this computer.

`datasheets` and `sourcing` read the BOMs that `bom` wrote and need no KiCad: the datasheet of
every part (links checked, local files bundled) and the price and stock at each supplier.
Supplier keys come from the environment or, on a PC, from a personal file outside the
repository (see keys_file).

The action calls it step by step while the rest of its steps move here (roadmap, phase 7B).
Run it from the repository's folder: paths are relative to it.
"""
import argparse
import glob
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

import bom_virtual
import detect
import kicad

HERE = os.path.dirname(os.path.abspath(__file__))
FAB_PROFILES = os.path.join(HERE, "..", "fab-profiles")


class StepError(Exception):
    pass


REPORTS = [f"{check}.{ext}" for check in ("erc", "drc", "drc-fab") for ext in ("json", "rpt")]


def clean(reports):
    """Remove the reports of an earlier run, so a check not run now can't show old numbers."""
    for name in REPORTS:
        _remove(os.path.join(reports, name))


def erc_drc(k, project_dir, name, has_pcb, reports):
    """ERC, and DRC when the board has an outline. Returns the report files written."""
    os.makedirs(reports, exist_ok=True)
    wanted = []
    sch = f"{project_dir}/{name}.kicad_sch"
    for fmt, ext in (("json", "json"), ("report", "rpt")):
        out = f"{reports}/erc.{ext}"
        _remove(out)
        k.run("sch", "erc", "--severity-all", "--format", fmt, "-o", out, sch)
        wanted.append(out)
    if has_pcb:
        pcb = f"{project_dir}/{name}.kicad_pcb"
        for fmt, ext in (("json", "json"), ("report", "rpt")):
            out = f"{reports}/drc.{ext}"
            _remove(out)
            k.run("pcb", "drc", "--severity-all", "--schematic-parity", "--format", fmt, "-o", out, pcb)
            wanted.append(out)
    missing = [f for f in wanted if not os.path.isfile(f)]
    if missing:
        raise StepError(f"kicad-cli ({k.where}) did not write {', '.join(missing)}: see its messages above")
    return wanted


def fab_rules(profile, profiles_dir=FAB_PROFILES):
    """The rules file of a fab profile; StepError for a profile that does not exist."""
    rules = os.path.join(profiles_dir, profile, "rules.kicad_dru")
    if not os.path.isfile(rules):
        known = sorted(d for d in os.listdir(profiles_dir) if os.path.isdir(os.path.join(profiles_dir, d)))
        raise StepError(f"Unknown fab-profile '{profile}' (use generic, {', '.join(known)})")
    return rules


def fab_drc(k, project_dir, name, reports, profile, out_dir, profiles_dir=FAB_PROFILES, tmp_root=None):
    """DRC of a copy of the project with the fab's rules appended to its own .kicad_dru."""
    rules = fab_rules(profile, profiles_dir)
    os.makedirs(reports, exist_ok=True)
    tmp = tempfile.mkdtemp(prefix="fab-drc-", dir=tmp_root)
    try:
        work = os.path.join(tmp, "p")
        skip = os.path.abspath(out_dir)  # the outputs, when they live inside the project folder

        def ignore(folder, names):
            return [n for n in names if n == ".git" or os.path.abspath(os.path.join(folder, n)) == skip]

        shutil.copytree(project_dir, work, symlinks=True, ignore=ignore)
        dru = os.path.join(work, f"{name}.kicad_dru")
        own = "(version 1)\n"
        if os.path.isfile(dru):
            with open(dru, encoding="utf-8") as f:
                own = f.read().rstrip("\n") + "\n\n"
        with open(rules, encoding="utf-8") as f:
            fab = "".join(line for line in f if not line.startswith("(version"))
        with open(dru, "w", encoding="utf-8", newline="\n") as f:
            f.write(own + fab)
        written = []
        for fmt, ext in (("json", "json"), ("report", "rpt")):
            k.run("pcb", "drc", "--severity-all", "--schematic-parity", "--format", fmt,
                  "-o", f"drc-fab.{ext}", f"{name}.kicad_pcb", workdir=work)
            src = os.path.join(work, f"drc-fab.{ext}")
            if not os.path.isfile(src):
                raise StepError(f"kicad-cli ({k.where}) did not write drc-fab.{ext}: see its messages above")
            shutil.copy(src, f"{reports}/drc-fab.{ext}")
            written.append(f"{reports}/drc-fab.{ext}")
        return written
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def summary(reports, profile="generic"):
    """Lines for the terminal: errors and warnings of each check, from the JSON reports."""
    def load(name):
        path = os.path.join(reports, name)
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return None

    def count(items):
        sev = [v.get("severity") for v in items]
        return f"{sev.count('error')} errors, {sev.count('warning')} warnings"

    lines = []
    erc = load("erc.json")
    if erc is not None:
        lines.append("ERC: " + count([v for s in erc.get("sheets", []) for v in s.get("violations", [])]))
    drc = load("drc.json")
    if drc is not None:
        lines.append(f"DRC: {count(drc.get('violations', []))}, {len(drc.get('unconnected_items', []))} unconnected")
    else:
        lines.append("DRC: skipped (no board outline yet)")
    fab = load("drc-fab.json") if profile != "generic" else None
    if fab is not None:
        lines.append(f"DRC with {profile} rules: {count(fab.get('violations', []))}")
    return lines


# --- per-part BOM ---------------------------------------------------------------------

BOM_LABELS = ["Reference", "Value", "Footprint", "Datasheet", "DNP", "MPN", "Manufacturer", "LCSC", "Voltage"]
BASE_DIR = ".kicad-hub-base"  # worktree of the commit to compare with; hidden, so never taken for a project


def bom_labels(required=""):
    """Columns of the per-part BOM: the fixed ones plus every field named in required-fields
    ("Footprint,Datasheet,MPN|LCSC": comma = all of them, | = either)."""
    labels = list(BOM_LABELS)
    for field in re.split(r"[,|]", required or ""):
        field = field.strip()
        if field and field not in labels:
            labels.append(field)
    return labels


def export_bom(k, sch, out, labels, variant=None):
    """`out`: one row per part of the schematic `sch`, without virtual symbols. Parts left."""
    fields = ",".join("${DNP}" if label == "DNP" else label for label in labels)
    _remove(out)
    k.run("sch", "export", "bom", *(["--variant", variant] if variant else []),
          "--fields", fields, "--labels", ",".join(labels), "--group-by", "", "--ref-range-delimiter", "",
          "-o", out, sch)
    if not os.path.isfile(out):
        what = f"variant {variant} of {sch}" if variant else sch
        raise StepError(f"kicad-cli ({k.where}) did not write the BOM of {what}: see its messages above")
    return bom_virtual.drop_virtual(out)


def project_variants(pro):
    """[(name, description)] of the design variants stored in the project file (KiCad 10)."""
    try:
        with open(pro, encoding="utf-8") as f:
            variants = (json.load(f).get("schematic") or {}).get("variants") or []
    except (OSError, ValueError):
        return []
    return [(v["name"], v.get("description") or "") for v in variants if isinstance(v, dict) and v.get("name")]


def wanted_variants(setting, pro):
    """The variants to build: auto = all in the project, none, or a comma-separated list."""
    known = dict(project_variants(pro))
    setting = (setting or "auto").strip()
    if setting == "none":
        return []
    if setting == "auto":
        return list(known.items())
    return [(n, known.get(n, "")) for n in (p.strip() for p in setting.split(",")) if n]


def boms(k, project_dir, name, reports, labels, variants):
    """bom-parts.csv, one bom-parts-variant-<name>.csv per variant, and variants.json."""
    os.makedirs(reports, exist_ok=True)
    for old in glob.glob(os.path.join(reports, "bom-parts*.csv")):
        _remove(old)
    sch = f"{project_dir}/{name}.kicad_sch"
    parts = export_bom(k, sch, f"{reports}/bom-parts.csv", labels)
    listed = []
    for vname, description in variants:
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", vname)
        vbom = f"{reports}/bom-parts-variant-{safe}.csv"
        listed.append({"name": vname, "description": description, "bom": vbom, "dir": safe,
                       "parts": export_bom(k, sch, vbom, labels, vname)})
    return parts, listed


def base_boms(k, project_dir, name, reports, labels, listed, ref, fetch=False, git=None):
    """The same BOMs from another commit (a PR's target, or `main` on a PC), for the diff.

    Adds "base_bom" to the variants that exist there. Returns None when that commit
    could not be checked out (a warning, not an error), else its number of parts.
    """
    git = git or _git
    if os.path.exists(BASE_DIR):
        git("worktree", "remove", "--force", BASE_DIR)
    if fetch:
        ok = git("fetch", "--no-tags", "--depth=1", "origin", ref) == 0
        ref = "FETCH_HEAD"
    else:
        ok = True
    if not (ok and git("worktree", "add", "--detach", BASE_DIR, ref) == 0):
        print("::warning::Could not check out the base to compare with: BOM diff skipped", file=sys.stderr)
        return None
    try:
        sch = f"{BASE_DIR}/{project_dir}/{name}.kicad_sch"
        base = f"{reports}/bom-parts-base.csv"
        if not os.path.isfile(sch):
            # the board is new since then: every part counts as added
            with open(f"{reports}/bom-parts.csv", encoding="utf-8-sig") as f:
                header = f.readline()
            with open(base, "w", encoding="utf-8", newline="") as f:
                f.write(header)
            return 0
        parts = export_bom(k, sch, base, labels)
        there = dict(project_variants(f"{BASE_DIR}/{project_dir}/{name}.kicad_pro"))
        for v in listed:  # each variant as it was there, when it already existed
            if v["name"] not in there:
                continue
            vbase = f"{reports}/bom-parts-variant-{v['dir']}-base.csv"
            try:
                export_bom(k, sch, vbase, labels, v["name"])
            except StepError:
                continue
            v["base_bom"] = vbase
        return parts
    finally:
        git("worktree", "remove", "--force", BASE_DIR)


# --- datasheets, cost and stock (no KiCad needed) -------------------------------------------

SUPPLIER_KEYS = ("MOUSER_API_KEY", "FARNELL_API_KEY", "FARNELL_STORE", "DIGIKEY_CLIENT_ID", "DIGIKEY_CLIENT_SECRET")


def need_bom(out):
    bom = f"{out}/reports/bom-parts.csv"
    if not os.path.isfile(bom):
        raise StepError(f"{bom} is missing: run `bom` first")
    return bom


def run_script(name, *args, env=None):
    """Run one of the scripts beside this file with the same Python; its exit code."""
    return subprocess.run([sys.executable, os.path.join(HERE, name), *args], env=env).returncode


def datasheets(project_dir, out, dirs="", check_links=True):
    """Datasheet of every fitted part: <out>/reports/datasheets.json and <out>/datasheets/."""
    bom = need_bom(out)
    result = f"{out}/reports/datasheets.json"
    _remove(result)
    code = run_script("datasheets.py", "--bom", bom, "--project-dir", project_dir, "--dirs", dirs,
                      "--check-urls", "true" if check_links else "false", "--out-dir", out, "--json", result)
    if code or not os.path.isfile(result):
        raise StepError("The datasheet step failed: see its messages above")
    with open(result, encoding="utf-8") as f:
        status = [e.get("status") for e in json.load(f).values()]
    return {name: status.count(name) for name in ("ok", "broken", "unverified", "missing")}


def keys_file(env=None):
    """Where a PC keeps the supplier keys: never in the repository.

    %APPDATA%\\kicad-hub\\keys.env on Windows, ~/.config/kicad-hub/keys.env elsewhere
    ($XDG_CONFIG_HOME respected). Lines NAME=value, # for comments; names as in SUPPLIER_KEYS.
    """
    env = os.environ if env is None else env
    base = env.get("APPDATA") if os.name == "nt" else env.get("XDG_CONFIG_HOME")
    return os.path.join(base or os.path.join(os.path.expanduser("~"), ".config"), "kicad-hub", "keys.env")


def supplier_env(env=None, path=None):
    """The environment for sourcing.py: the caller's, plus the keys of the personal file
    for those not already set (repository secrets in CI, the file on a PC)."""
    env = dict(os.environ if env is None else env)
    try:
        with open(path or keys_file(env), encoding="utf-8-sig") as f:
            lines = f.read().splitlines()
    except OSError:
        return env
    for line in lines:
        name, sep, value = line.strip().partition("=")
        name, value = name.strip(), value.strip().strip('"').strip("'")
        if sep and name in SUPPLIER_KEYS and value and not env.get(name, "").strip():
            env[name] = value
    return env


def sourcing(project_dir, out, boards="1,5,10,100", currency="USD", choices="kicad-hub-sourcing.csv", env=None):
    """Price and stock of every part per supplier: sourcing.json, sourcing.csv, sourcing.html.
    Suppliers are other people's services: a failure is a warning (False), not an error."""
    bom = need_bom(out)
    reports = f"{out}/reports"
    written = [f"{reports}/sourcing.json", f"{reports}/sourcing.csv", f"{out}/sourcing.html"]
    for old in written:
        _remove(old)
    code = run_script("sourcing.py", "--bom", bom, "--variants", f"{reports}/variants.json",
                      "--choices", f"{project_dir}/{choices}", "--boards", boards, "--currency", currency,
                      "--json", written[0], "--csv", written[1], "--html", written[2],
                      env=supplier_env(env))
    if code:
        print("::warning::Cost and stock lookup failed: skipped", file=sys.stderr)
    return code == 0


def _git(*args):
    try:
        return subprocess.run(["git", *args]).returncode
    except OSError as e:
        print(f"::warning::Could not run git: {e}", file=sys.stderr)
        return 127


def _remove(path):
    try:
        os.remove(path)
    except OSError:
        pass


def make_kicad(args):
    if args.image:
        return kicad.KiCad(image=args.image, home=args.kicad_home)
    return kicad.KiCad(cli=kicad.find_cli(args.kicad_cli))


def cmd_checks(args):
    folder, name, has_pcb, note = detect.detect(args.project_dir.strip(), ".", [args.out])
    if note:
        print(note, flush=True)  # before kicad-cli's own output, which does not go through Python
    fab = args.fab_profile != "generic" and has_pcb
    if fab:
        fab_rules(args.fab_profile)  # a wrong profile name stops here, not after a minute of checks
    k = make_kicad(args)
    reports = f"{args.out}/reports"
    clean(reports)
    erc_drc(k, folder, name, has_pcb, reports)
    if fab:
        fab_drc(k, folder, name, reports, args.fab_profile, args.out, tmp_root=args.tmp or None)
    sys.stdout.flush()
    print(f"\n{name} ({k.where})")
    for line in summary(reports, args.fab_profile):
        print("  " + line)
    print(f"Reports: {reports}/")
    return 0


def cmd_bom(args):
    folder, name, _, note = detect.detect(args.project_dir.strip(), ".", [args.out])
    if note:
        print(note, flush=True)
    k = make_kicad(args)
    reports = f"{args.out}/reports"
    labels = bom_labels(args.required_fields)
    parts, listed = boms(k, folder, name, reports, labels, wanted_variants(args.variants, f"{folder}/{name}.kicad_pro"))
    base_parts = base_boms(k, folder, name, reports, labels, listed, args.base, args.fetch) if args.base else None
    with open(f"{reports}/variants.json", "w", encoding="utf-8") as f:
        json.dump([{key: v[key] for key in ("name", "description", "bom", "dir", "base_bom") if key in v}
                   for v in listed], f, indent=2)
        f.write("\n")
    sys.stdout.flush()
    print(f"\n{name} ({k.where})")
    print(f"  BOM: {parts} parts")
    for v in listed:
        print(f"  Variant {v['name']}: {v['parts']} parts")
    if base_parts is not None:
        print(f"  {args.base}: {base_parts} parts (to compare with)")
    print(f"BOMs: {reports}/bom-parts*.csv")
    return 0


def cmd_datasheets(args):
    folder, name, _, _ = detect.detect(args.project_dir.strip(), ".", [args.out])
    found = datasheets(folder, args.out, args.dirs, args.check_links.strip().lower() == "true")
    print(f"\n{name}\n  Datasheets: {found['ok']} found, {found['broken']} broken, "
          f"{found['unverified']} not checked, {found['missing']} missing")
    print(f"Datasheets: {args.out}/datasheets/index.html")
    return 0


def cmd_sourcing(args):
    folder, _, _, _ = detect.detect(args.project_dir.strip(), ".", [args.out])
    if sourcing(folder, args.out, args.boards, args.currency, args.choices):
        print(f"Suppliers: {args.out}/sourcing.html")
    return 0


def add_project(c):
    c.add_argument("--project-dir", default="", help="folder with the .kicad_pro (default: the only one in the repository)")
    c.add_argument("--out", default=".kicad-hub", help="output folder (reports go to <out>/reports)")


def add_common(c):
    add_project(c)
    c.add_argument("--kicad-cli", default="", help="path of the local kicad-cli (default: found automatically)")
    c.add_argument("--image", default="", help="run kicad-cli in this Docker image instead of the local KiCad")
    c.add_argument("--kicad-home", default="", help="with --image: folder mounted as KiCad's HOME (library tables)")


def main(argv=None):
    ap = argparse.ArgumentParser(description="kicad-hub engine")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("checks", help="ERC, DRC and the optional DRC with a fab's rules")
    add_common(c)
    c.add_argument("--fab-profile", default="generic", help="generic | a folder of fab-profiles/")
    c.add_argument("--tmp", default="", help="folder for temporary copies (default: the system's)")
    c.set_defaults(func=cmd_checks)
    b = sub.add_parser("bom", help="per-part BOM of the project, of each variant and of a base commit")
    add_common(b)
    b.add_argument("--required-fields", default="Footprint,Datasheet,MPN|LCSC",
                   help="fields every part needs; they become columns of the BOM")
    b.add_argument("--variants", default="auto", help="auto (all in the project) | none | a comma-separated list")
    b.add_argument("--base", default="", help="commit or branch to compare with (e.g. main)")
    b.add_argument("--fetch", action="store_true", help="with --base: fetch it from origin first (CI)")
    b.set_defaults(func=cmd_bom)
    d = sub.add_parser("datasheets", help="datasheet of every part: links checked, local files bundled")
    add_project(d)
    d.add_argument("--dirs", default="", help="extra folders with datasheet files, comma-separated")
    d.add_argument("--check-links", default="true", help="true | false: check that the links answer")
    d.set_defaults(func=cmd_datasheets)
    p = sub.add_parser("sourcing", help="price and stock of every part at each supplier")
    add_project(p)
    p.add_argument("--boards", default="1,5,10,100", help="batch sizes to price")
    p.add_argument("--currency", default="USD")
    p.add_argument("--choices", default="kicad-hub-sourcing.csv",
                   help="supplier picked per part, a file relative to the project folder")
    p.set_defaults(func=cmd_sourcing)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (StepError, detect.DetectError, kicad.KiCadNotFound) as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
