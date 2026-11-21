#!/usr/bin/env python3
"""kicad-hub's engine: the steps of the action as one program, for CI and for a PC.

    kicad_hub.py run [--project-dir DIR] [--out DIR] [--base REF|none] [--fab-profile NAME]
                     [--skip datasheets,sourcing,package,requirements,portal] (+ the options below)

`run`: every step below in a row on a PC, comparing with main (else master), then where the
report and the portal are. Or one at a time:

    kicad_hub.py checks [--project-dir DIR] [--out DIR] [--fab-profile NAME]
    kicad_hub.py bom    [--project-dir DIR] [--out DIR] [--required-fields LIST]
                        [--variants auto|none|LIST] [--base REF [--fetch]]
  both: [--kicad-cli PATH | --image IMAGE [--kicad-home DIR]]
    kicad_hub.py package [--project-dir DIR] [--out DIR] [--fab-profile NAME] [--extra-formats LIST]
                         [--rev REV]   (same KiCad options)
    kicad_hub.py requirements [--project-dir DIR] [--out DIR] [--file kicad-hub-requirements.yaml]
                              [--image IMAGE [--kicad-home DIR]]
    kicad_hub.py datasheets [--project-dir DIR] [--out DIR] [--dirs LIST] [--check-links true|false]
    kicad_hub.py sourcing   [--project-dir DIR] [--out DIR] [--boards 1,5,10,100] [--currency USD]
                            [--choices kicad-hub-sourcing.csv]
    kicad_hub.py report     [--project-dir DIR] [--out DIR] [--fab-profile NAME] [--base-ref NAME]
                            [--package FILE] [--required-fields LIST] (+ CI links: --portal-url …)
    kicad_hub.py portal     [--project-dir DIR] [--out DIR]   (CI: --site DIR and portal.py's build options)

`checks`: ERC and DRC (with schematic parity) of the project, as JSON and text reports in
<out>/reports/, plus a second DRC with a fab's rules when --fab-profile is given.

`bom`: one row per part in <out>/reports/bom-parts.csv, the same for every design variant
(listed in variants.json) and, with --base, for that commit or branch too, so the report
can say what changed.

`package`: the fab package, <out>/package/<board>-<rev>-fab.zip: Gerbers, drill files, the
IPC-D-356 netlist, BOM and pick & place of the default build and of each variant, fab notes
read from the board and checksums. It reads the BOMs that `bom` wrote.

Without --image they use the KiCad installed on this computer.

`requirements`: checks the board against the project's requirements file, using only what
the other commands wrote to <out>/reports/ (run it after them).

`datasheets` and `sourcing` read the BOMs that `bom` wrote and need no KiCad: the datasheet of
every part (links checked, local files bundled) and the price and stock at each supplier.
Supplier keys come from the environment or, on a PC, from a personal file outside the
repository (see keys_file).

`report`: the markdown report (PR comment, job summary) in <out>/reports/report.md from
whatever the other commands wrote there, plus counts.json; run it last.

`portal`: the design portal page (viewer, report, BOM, suppliers, downloads). On a PC, of the
working tree, in <out>/portal/<branch>/ with the site's front page around it; in CI only the
page, in --site, with what the action passes (publishing it is GitHub-only, in the action).

The action calls it step by step while the rest of its steps move here (roadmap, phase 7B).
Run it from the repository's folder: paths are relative to it.
"""
import argparse
import csv
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile

import bom_virtual
import detect
import kicad

HERE = os.path.dirname(os.path.abspath(__file__))
FAB_PROFILES = os.path.join(HERE, "..", "fab-profiles")
FABNOTES = os.path.join(HERE, "fabnotes.py")
FABNOTES_TIMEOUT = 300  # seconds; plain Python reading the board file, a few seconds on a large board


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


def fitted(path):
    """(fitted parts, all parts) of a per-part BOM: rows marked DNP are not fitted."""
    with open(path, newline="", encoding="utf-8-sig") as f:
        rows = [r for r in csv.DictReader(f) if r.get("Reference")]
    return sum(1 for r in rows if (r.get("DNP") or "").strip() in ("", "~")), len(rows)


def counted(path):
    """'48 fitted parts (11 more not fitted)' for the terminal."""
    n, total = fitted(path)
    return f"{n} fitted parts" + (f" ({total - n} more not fitted)" if total > n else "")


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


# --- fab package ----------------------------------------------------------------------------

ASSEMBLY_BOM = ["--fields", "Reference,${QUANTITY},Value,Footprint,MPN,Manufacturer,LCSC",
                "--labels", "References,Qty,Value,Footprint,MPN,Manufacturer,LCSC",
                "--group-by", "Value,Footprint,MPN,Manufacturer,LCSC"]
EXTRA_FORMATS = {"odb": "-odb.zip", "ipc2581": "-ipc2581.xml"}


def safe_name(text):
    return re.sub(r"[^A-Za-z0-9._-]", "_", text)


def board_revision(pcb):
    """The revision in the board's title block, or "" when it has none."""
    try:
        with open(pcb, encoding="utf-8", errors="replace") as f:
            m = re.search(r'\(rev "([^"]+)', f.read())
    except OSError:
        return ""
    return m.group(1) if m else ""


def commit_id():
    """The short id of the commit being built; "local" outside a git repository."""
    try:
        r = subprocess.run(["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True)
    except OSError:
        return "local"
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else "local"


def must(k, *args):
    """Run kicad-cli; a failure stops the package (half a fab package is worse than none)."""
    if k.run(*args) != 0:
        raise StepError(f"kicad-cli {' '.join(args[:3])} failed ({k.where}): see its messages above")


def need_script(name, *args):
    if run_script(name, *args) != 0:
        raise StepError(f"{name} failed: see its messages above")


def package(k, project_dir, name, out, profile="generic", extra="", rev="", fabnotes=None):
    """Build <out>/package/<name>-<rev>/ and its zip. Returns what later steps need:
    {"rev", "board_rev", "zip", "name", "warnings"}.

    Revision: `rev` when given (the tag, on a release), else the board's title block, else
    the commit. Gerbers use the plot settings saved in the board.
    """
    reports = f"{out}/reports"
    pcb, sch = f"{project_dir}/{name}.kicad_pcb", f"{project_dir}/{name}.kicad_sch"
    need_bom(out)
    board_rev = rev or board_revision(pcb)
    rev = safe_name(board_rev or commit_id())
    base = f"{name}-{rev}"
    shutil.rmtree(f"{out}/package", ignore_errors=True)  # no files of another revision in the zip
    pkg = f"{out}/package/{base}"
    for sub in ("gerber", "drill", "assembly"):
        os.makedirs(f"{pkg}/{sub}")

    # Standard formats any fab accepts
    must(k, "pcb", "export", "gerbers", "--board-plot-params", "-o", f"{pkg}/gerber/", pcb)
    must(k, "pcb", "export", "drill", "--excellon-separate-th", "--generate-map", "--map-format", "pdf",
         "--excellon-units", "mm", "-o", f"{pkg}/drill/", pcb)
    must(k, "pcb", "export", "ipcd356", "-o", f"{pkg}/{base}.d356", pcb)
    # Assembly files for the default build and each variant. DNP comes from the schematic
    # (per-variant BOM), so the pick & place is right even if the PCB wasn't updated.
    cpl_all = f"{reports}/cpl-all.csv"
    must(k, "pcb", "export", "pos", "--format", "csv", "--units", "mm", "--side", "both", "-o", cpl_all, pcb)

    def assembly(folder, prefix, per_part_bom, variant=None):
        os.makedirs(folder, exist_ok=True)
        bom, cpl = f"{folder}/{prefix}-bom.csv", f"{folder}/{prefix}-cpl.csv"
        must(k, "sch", "export", "bom", "--exclude-dnp", *(["--variant", variant] if variant else []),
             *ASSEMBLY_BOM, "-o", bom, sch)
        bom_virtual.drop_virtual(bom)
        need_script("variant_cpl.py", per_part_bom, cpl_all, cpl)
        if profile != "generic":  # the fab's own column layout, next to the generic files
            need_script("fab_assembly.py", os.path.join(FAB_PROFILES, profile, "profile.json"),
                        bom, cpl, f"{folder}/{profile}", prefix)

    assembly(f"{pkg}/assembly", base, f"{reports}/bom-parts.csv")
    variant_lines = []
    try:
        with open(f"{reports}/variants.json", encoding="utf-8") as f:
            variants = json.load(f)
    except (OSError, ValueError):
        variants = []
    for v in variants:
        assembly(f"{pkg}/assembly/{v['dir']}", f"{base}-{v['dir']}", v["bom"], v["name"])
        variant_lines.append(f"  {v['name']}: assembly/{v['dir']}/  {v.get('description') or ''}\n")
    for fmt in (f.strip() for f in extra.split(",")):
        if fmt in EXTRA_FORMATS:
            must(k, "pcb", "export", fmt, "-o", f"{pkg}/{base}{EXTRA_FORMATS[fmt]}", pcb)
        elif fmt:
            print(f"::warning::Unknown extra format '{fmt}' (use {', '.join(EXTRA_FORMATS)})", file=sys.stderr)

    # Fab notes read from the board and project files, with plain Python (no KiCad needed)
    readme, warnings_txt = f"{pkg}/README.txt", f"{reports}/fab-warnings.txt"
    with open(warnings_txt, "w", encoding="utf-8") as f:
        try:
            code = subprocess.run([sys.executable, fabnotes or FABNOTES, pcb, rev, readme, f"{reports}/board.json",
                                   profile], stdout=f, timeout=FABNOTES_TIMEOUT).returncode
        except subprocess.TimeoutExpired:  # subprocess.run has already stopped it
            raise StepError(f"Reading the fab notes from the board took more than {FABNOTES_TIMEOUT} s and was "
                            "stopped: please report it") from None
    if code != 0 or not os.path.isfile(readme):
        raise StepError("The fab notes could not be read from the board: see the messages above")
    with open(readme, "a", encoding="utf-8") as f:
        if variant_lines:
            f.write("\nVARIANTS (same bare board, different assembly)\n  default: assembly/\n" + "".join(variant_lines))
        # Schematic vs PCB (DNP, attributes, values): the pick & place above follows the schematic
        sync_json, sync_txt = f"{reports}/variant-sync.json", f"{reports}/variant-sync.txt"
        _remove(sync_txt)
        if run_script("variant_sync.py", pcb, reports, sync_json, sync_txt) != 0:
            _remove(sync_json)
        if os.path.isfile(sync_txt) and os.path.getsize(sync_txt):
            with open(sync_txt, encoding="utf-8") as t:
                f.write("\nSCHEMATIC vs PCB MISMATCH\n" + "".join("  " + line for line in t))
    with open(warnings_txt, encoding="utf-8", errors="replace") as f:
        warnings = [line.rstrip("\r\n") for line in f if line.strip()]
    for w in warnings:
        print(f"::warning::{w}", file=sys.stderr)

    checksums(pkg)
    zip_path = f"{out}/package/{base}-fab.zip"
    zip_folder(pkg, zip_path, base)
    return {"rev": rev, "board_rev": board_rev, "zip": zip_path, "name": f"{base}-fab.zip", "warnings": warnings}


def package_files(pkg):
    """Files of the package as ./relative/paths, in the order `sort` gives in the C locale."""
    found = []
    for folder, _, files in os.walk(pkg):
        rel = os.path.relpath(folder, pkg).replace(os.sep, "/")
        found += ["./" + (f if rel == "." else f"{rel}/{f}") for f in files]
    return sorted(found, key=lambda p: p.encode("utf-8"))


def checksums(pkg):
    """SHA256SUMS.txt, in the format `sha256sum` writes and `sha256sum -c` checks."""
    lines = []
    for rel in package_files(pkg):
        if rel == "./SHA256SUMS.txt":
            continue
        with open(os.path.join(pkg, rel[2:]), "rb") as f:
            lines.append(f"{hashlib.sha256(f.read()).hexdigest()}  {rel}\n")
    with open(os.path.join(pkg, "SHA256SUMS.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(lines))


def zip_folder(pkg, zip_path, top):
    """The package as a zip with everything under one folder (`top`)."""
    _remove(zip_path)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as z:
        for rel in package_files(pkg):
            z.write(os.path.join(pkg, rel[2:]), f"{top}/{rel[2:]}")


def github_output(values):
    """Step outputs for the action; nothing to do outside GitHub Actions."""
    path = os.environ.get("GITHUB_OUTPUT")
    if path:
        with open(path, "a", encoding="utf-8") as f:
            f.write("".join(f"{key}={value}\n" for key, value in values.items()))


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


def datasheet_line(found):
    """The terminal line for the datasheet step, counted as the report counts them: a link
    that could not be checked is still a datasheet."""
    have = found["ok"] + found["unverified"]
    return (f"Datasheets: {have} found" + (f" ({found['unverified']} link not checked)" if found["unverified"] else "")
            + f", {found['broken']} broken, {found['missing']} missing")


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


def requirements(project_dir, out, req_file="kicad-hub-requirements.yaml", k=None):
    """Check the requirements file of the project: <out>/reports/requirements.json.

    Returns its entries, or None when there is no file or the check could not run (a
    warning: requirements never stop the other outputs). With a Docker image the check
    runs inside it, as it always did; otherwise with this Python.
    """
    reports = f"{out}/reports"
    result = f"{reports}/requirements.json"
    _remove(result)
    file = f"{project_dir}/{req_file}"
    if not req_file or not os.path.isfile(file):
        print(f"No requirements file ({file}): skipped")
        return None
    if k is not None and k.image:
        copy = f"{out}/.requirements.py"  # where the image can see it
        shutil.copy(os.path.join(HERE, "requirements.py"), copy)
        try:
            code = k.python(copy, file, reports, result)
        finally:
            _remove(copy)
    else:
        code = run_script("requirements.py", file, reports, result)
    if code != 0 or not os.path.isfile(result):
        print("::warning::Requirements check failed to run: skipped", file=sys.stderr)
        return None
    with open(result, encoding="utf-8") as f:
        return json.load(f)


# --- report ---------------------------------------------------------------------------------

# report.py option -> the file in <out>/reports an earlier step writes; the ones not there
# (a step not run, or not run yet on this PC) are left out and report.py skips that section
REPORT_INPUTS = (("--base-bom", "bom-parts-base.csv"), ("--erc", "erc.json"), ("--drc", "drc.json"),
                 ("--drc-fab", "drc-fab.json"), ("--datasheets", "datasheets.json"),
                 ("--package-warnings", "fab-warnings.txt"), ("--variants", "variants.json"),
                 ("--variant-sync", "variant-sync.json"), ("--requirements", "requirements.json"),
                 ("--visual", "visual.json"), ("--sourcing", "sourcing.json"))
# counts the action exposes as step outputs ("-" when that check did not run)
REPORT_OUTPUTS = ("erc_errors", "drc_errors", "missing_fields", "requirements_failed")


def report(out, name, profile="generic", package=None, options=()):
    """<out>/reports/report.md and counts.json from the files the other steps wrote.

    `package`: the fab zip's file name; None finds it in <out>/package/, "" means none.
    `options`: more report.py options (base ref, required fields, portal and run links).
    Returns the counts.
    """
    reports = f"{out}/reports"
    args = ["--name", name, "--bom", need_bom(out), "--fab-profile", profile]
    if profile != "generic":
        args += ["--fab-rules", fab_rules(profile)]
    for option, file in REPORT_INPUTS:
        if os.path.isfile(f"{reports}/{file}"):
            args += [option, f"{reports}/{file}"]
    if package is None:
        zips = sorted(glob.glob(f"{out}/package/*-fab.zip"))
        package = os.path.basename(zips[-1]) if zips else ""
    if package:
        args += ["--package", package]
    md, counts = f"{reports}/report.md", f"{reports}/counts.json"
    _remove(md)
    _remove(counts)
    if run_script("report.py", *args, *options, "--out", md, "--out-json", counts) != 0 or not os.path.isfile(counts):
        raise StepError("report.py failed: see its messages above")
    with open(counts, encoding="utf-8") as f:
        result = json.load(f)
    github_output({key: "-" if result.get(key) is None else result[key] for key in REPORT_OUTPUTS})
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(md, encoding="utf-8") as f, open(summary, "a", encoding="utf-8") as s:
            s.write(f.read())
    return result


# --- design portal ----------------------------------------------------------------------------

PORTAL_DIR = "portal"  # <out>/portal/: the local site, one folder per branch built on this PC


def _git_out(*args):
    """Output of a git command, stripped; "" when git or the repository is not there."""
    try:
        r = subprocess.run(["git", *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def github_repo(url):
    """("https://github.com", "owner/name") from a remote URL (https or ssh), else ("", "")."""
    m = re.match(r"(?:https?://(?:[^@/]+@)?|ssh://git@|git@)([^/:]+)[/:]([^/]+)/([^/]+?)(?:\.git)?/?$", url or "")
    return (f"https://{m.group(1)}", f"{m.group(2)}/{m.group(3)}") if m else ("", "")


def site_folder(label):
    """A build's folder in the site, by the action's rules (`pr-*` and `releases` are taken)."""
    dest = re.sub(r"[^A-Za-z0-9._-]", "_", label) or "local"
    return f"branch-{dest}" if dest.startswith("pr-") or dest == "releases" else dest


def working_tree_build(project_dir, name):
    """What a build of the working tree on this PC is, from git and the board."""
    head = _git_out("rev-parse", "HEAD")  # "" outside a repository or before its first commit
    sha = head or "0" * 40
    branch = _git_out("rev-parse", "--abbrev-ref", "HEAD") if head else ""
    if branch == "HEAD":  # detached
        branch = ""
    label = branch or (head[:7] if head else "local")
    if _git_out("status", "--porcelain", "--", project_dir):
        label += " + local changes"
    server, repo = github_repo(_git_out("remote", "get-url", "origin"))
    info = {"sha": sha, "label": label, "kind": "branch", "branch": branch, "repo": repo or f"local/{name}",
            "server": server or "https://github.com", "rev": board_revision(f"{project_dir}/{name}.kicad_pcb"),
            "commit-date": _git_out("log", "-1", "--format=%cI") if head else "",
            "commit-author": _git_out("log", "-1", "--format=%an") if head else "",
            "commit-message": _git_out("log", "-1", "--format=%s") if head else "", "folder": site_folder(label.split(" ")[0])}
    return info


PORTAL_BUILD = ("rev", "label", "kind", "sha", "repo", "server", "run-url", "title", "base-ref", "branch",
                "commit-date", "commit-author", "commit-message", "renders", "pr")


def portal(project_dir, name, out, site=None, build=None, light=False, activity=False):
    """The design portal page of this build.

    With `site` (CI): only the page, in that folder; `build` holds every portal.py build
    option, as the action knows them. Without it (a PC): the page of the working tree in
    <out>/portal/<branch>/, and the site's front page, builds list and viewer scripts in
    <out>/portal/, so it opens with any local web server. Returns the page's folder.
    """
    local = site is None
    build = dict(working_tree_build(project_dir, name) if local else {}, **(build or {}))
    if local:
        root = f"{out}/{PORTAL_DIR}"
        site = f"{root}/{build['folder']}"
        shutil.rmtree(site, ignore_errors=True)  # no files of an earlier build of this branch
    args = ["build", "--site", site, "--out", out, "--project-dir", project_dir, "--name", name]
    for key in PORTAL_BUILD:
        if build.get(key) not in (None, ""):
            args.append(f"--{key}={build[key]}")  # =: a commit message starting with "-" is still a value
    if light:
        args.append("--light")
    if activity:
        args.append("--activity")
    if run_script("portal.py", *args) != 0:
        raise StepError("portal.py build failed: see its messages above")
    if local and run_script("portal.py", "index", "--root", root, "--repo", build["repo"],
                            "--server", build["server"]) != 0:
        raise StepError("portal.py index failed: see its messages above")
    return site


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


def write_variants(reports, listed):
    """variants.json: the variants and their BOMs (and base BOMs), for the report and the portal."""
    with open(f"{reports}/variants.json", "w", encoding="utf-8") as f:
        json.dump([{key: v[key] for key in ("name", "description", "bom", "dir", "base_bom") if key in v}
                   for v in listed], f, indent=2)
        f.write("\n")


def default_base():
    """The branch a PC run compares with: main, else master, else none ("")."""
    for ref in ("main", "master"):
        if _git_out("rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}"):
            return ref
    return ""


RUN_STEPS = ("datasheets", "sourcing", "package", "requirements", "portal")  # the ones --skip can leave out


def cmd_run(args):
    """Every step, in the action's order, with this computer's KiCad: what a PC needs."""
    skip = {x.strip() for x in args.skip.split(",") if x.strip()}
    unknown = skip - set(RUN_STEPS)
    if unknown:
        raise StepError(f"--skip: unknown step {', '.join(sorted(unknown))} (use {', '.join(RUN_STEPS)})")
    folder, name, has_pcb, note = detect.detect(args.project_dir.strip(), ".", [args.out])
    if note:
        print(note, flush=True)
    if args.fab_profile != "generic":
        fab_rules(args.fab_profile)  # a wrong profile name stops here, not after the checks
    base = default_base() if args.base is None else args.base.strip()
    if base.lower() == "none":
        base = ""
    k = make_kicad(args)
    reports = f"{args.out}/reports"
    lines = []

    def step(title):
        sys.stdout.flush()
        print(f"\n== {title}", flush=True)

    step(f"ERC / DRC ({k.where})")
    clean(reports)
    erc_drc(k, folder, name, has_pcb, reports)
    if args.fab_profile != "generic" and has_pcb:
        fab_drc(k, folder, name, reports, args.fab_profile, args.out, tmp_root=args.tmp or None)
    lines += summary(reports, args.fab_profile)

    step("BOM" + (f" (and {base}, to compare with)" if base else ""))
    labels = bom_labels(args.required_fields)
    parts, listed = boms(k, folder, name, reports, labels, wanted_variants(args.variants, f"{folder}/{name}.kicad_pro"))
    base_parts = base_boms(k, folder, name, reports, labels, listed, base) if base else None
    write_variants(reports, listed)
    fitted_in = lambda path: fitted(path)[0]  # noqa: E731 - kicad-cli lists not-fitted parts too
    lines.append(f"BOM: {fitted_in(f'{reports}/bom-parts.csv')} fitted parts"
                 + "".join(f", {v['name']} {fitted_in(v['bom'])}" for v in listed)
                 + (f"; {base}: {fitted_in(f'{reports}/bom-parts-base.csv') if base_parts else 0}"
                    if base_parts is not None else ""))

    if "datasheets" not in skip:
        step("Datasheets")
        try:
            found = datasheets(folder, args.out, args.dirs, args.check_links.strip().lower() == "true")
            lines.append(datasheet_line(found))
        except StepError as e:  # an extra: the rest still runs
            print(f"::warning::{e}: skipped", file=sys.stderr)
    if "sourcing" not in skip:
        step("Cost and stock")
        sourcing(folder, args.out, args.boards, args.currency, args.choices)
    zip_name = ""
    if "package" not in skip and has_pcb:
        step("Fab package")
        made = package(k, folder, name, args.out, args.fab_profile, args.extra_formats)
        zip_name = made["name"]
        lines.append(f"Fab package: {made['zip']}")
    if "requirements" not in skip:
        step("Requirements")
        requirements(folder, args.out, args.requirements_file.strip(), k)

    step("Report")
    counts = report(args.out, name, args.fab_profile, zip_name,
                    ["--base-ref", base or "base", "--required", args.required_fields])
    shown = {key: "-" if counts.get(key) is None else counts[key] for key in REPORT_OUTPUTS}
    lines.append(f"Parts missing fields: {shown['missing_fields']} · requirements failed: "
                 f"{shown['requirements_failed']}")
    site = None
    if "portal" not in skip:
        step("Portal")
        site = portal(folder, name, args.out)

    sys.stdout.flush()
    print(f"\n{name} ({k.where})")
    for line in lines:
        print("  " + line)
    print(f"Report: {reports}/report.md")
    if site:
        print(f"Portal: python -m http.server --directory {args.out}/{PORTAL_DIR} 8000, "
              f"then http://localhost:8000/{os.path.basename(site)}/")
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
    write_variants(reports, listed)
    sys.stdout.flush()
    print(f"\n{name} ({k.where})")
    print(f"  BOM: {counted(f'{reports}/bom-parts.csv')}")
    for v in listed:
        print(f"  Variant {v['name']}: {counted(v['bom'])}")
    if base_parts is not None:
        base = f"{reports}/bom-parts-base.csv"
        print(f"  {args.base}: {counted(base) if base_parts else '0 parts (the board is new)'} (to compare with)")
    print(f"BOMs: {reports}/bom-parts*.csv")
    return 0


def cmd_package(args):
    folder, name, has_pcb, note = detect.detect(args.project_dir.strip(), ".", [args.out])
    if note:
        print(note, flush=True)
    if not has_pcb:
        print(f"{name} has no board outline yet: no fab package")
        return 0
    if args.fab_profile != "generic":
        fab_rules(args.fab_profile)
    k = make_kicad(args)
    made = package(k, folder, name, args.out, args.fab_profile, args.extra_formats, args.rev.strip())
    github_output({key: made[key] for key in ("rev", "board_rev", "zip", "name")})
    sys.stdout.flush()
    print(f"\n{name} ({k.where})")
    print(f"  Fab package: {made['zip']}")
    for w in made["warnings"]:
        print(f"  Check before ordering: {w}")
    return 0


def cmd_requirements(args):
    folder, _, _, _ = detect.detect(args.project_dir.strip(), ".", [args.out])
    k = kicad.KiCad(image=args.image, home=args.kicad_home) if args.image else None
    requirements(folder, args.out, args.file.strip(), k)
    return 0


def cmd_datasheets(args):
    folder, name, _, _ = detect.detect(args.project_dir.strip(), ".", [args.out])
    found = datasheets(folder, args.out, args.dirs, args.check_links.strip().lower() == "true")
    print(f"\n{name}\n  {datasheet_line(found)}")
    print(f"Datasheets: {args.out}/datasheets/index.html")
    return 0


def cmd_sourcing(args):
    folder, _, _, _ = detect.detect(args.project_dir.strip(), ".", [args.out])
    if sourcing(folder, args.out, args.boards, args.currency, args.choices):
        print(f"Suppliers: {args.out}/sourcing.html")
    return 0


def cmd_report(args):
    folder, name, _, _ = detect.detect(args.project_dir.strip(), ".", [args.out])
    if args.fab_profile != "generic":
        fab_rules(args.fab_profile)
    options = ["--base-ref", args.base_ref or "base", "--required", args.required_fields,
               "--cap-voltage", args.check_capacitor_voltage, "--datasheet-optional", args.datasheet_optional_refs,
               "--portal-url", args.portal_url, "--portal-state", args.portal_state,
               "--pages-branch", args.pages_branch, "--pages-settings-url", args.pages_settings_url,
               "--action-ref", args.action_ref, "--run-url", args.run_url]
    counts = report(args.out, name, args.fab_profile, args.package, options)

    def shown(key):
        return "-" if counts.get(key) is None else str(counts[key])

    print(f"\n{name}\n  ERC errors: {shown('erc_errors')} · DRC errors: {shown('drc_errors')} · "
          f"parts missing fields: {shown('missing_fields')} · requirements failed: {shown('requirements_failed')}")
    print(f"Report: {args.out}/reports/report.md")
    return 0


def cmd_portal(args):
    folder, name, _, _ = detect.detect(args.project_dir.strip(), ".", [args.out])
    build = {key: getattr(args, key.replace("-", "_")) for key in PORTAL_BUILD}
    if not args.site:
        given = [f"--{k}" for k, v in build.items() if v is not None]
        if given:
            raise StepError(f"{', '.join(given)} only with --site (CI); on a PC they come from git and the board")
    site = portal(folder, name, args.out, args.site or None,
                  {k: v for k, v in build.items() if v is not None}, args.light, args.activity)
    if not args.site:
        root = f"{args.out}/{PORTAL_DIR}"
        print(f"\n{name}\n  Portal page: {site}/index.html")
        print(f"Open it through a local web server (a browser can't load its files from disk):\n"
              f"  python -m http.server --directory {root} 8000\n"
              f"  then http://localhost:8000/{os.path.basename(site)}/")
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
    f = sub.add_parser("package", help="fab package: Gerbers, drill, netlist, BOM and pick & place, fab notes")
    add_common(f)
    f.add_argument("--fab-profile", default="generic", help="generic | a folder of fab-profiles/")
    f.add_argument("--extra-formats", default="", help="odb, ipc2581 (comma-separated)")
    f.add_argument("--rev", default="", help="revision (the tag, on a release); default: the board's, else the commit")
    f.set_defaults(func=cmd_package)
    q = sub.add_parser("requirements", help="check the board against the project's requirements file")
    add_project(q)
    q.add_argument("--file", default="kicad-hub-requirements.yaml", help="requirements file, relative to the project folder")
    q.add_argument("--image", default="", help="run the check in this Docker image (CI) instead of this Python")
    q.add_argument("--kicad-home", default="", help="with --image: folder mounted as KiCad's HOME")
    q.set_defaults(func=cmd_requirements)
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
    r = sub.add_parser("report", help="the markdown report of everything the other steps wrote")
    add_project(r)
    r.add_argument("--fab-profile", default="generic", help="generic | a folder of fab-profiles/")
    r.add_argument("--base-ref", default="base", help="name of what the BOM diff compares with (the bom --base)")
    r.add_argument("--package", default=None, help="fab zip file name (default: the one in <out>/package/)")
    r.add_argument("--required-fields", default="Footprint,Datasheet,MPN|LCSC", help="fields every part needs")
    r.add_argument("--check-capacitor-voltage", default="true", help="true | false")
    r.add_argument("--datasheet-optional-refs", default="R,C,TP,H,MH,FID,JP,NT,LOGO",
                   help="reference prefixes that need no datasheet")
    r.add_argument("--portal-url", default="", help="this build's page in the design portal (CI)")
    r.add_argument("--portal-state", default="serving", choices=["serving", "disabled", "unknown"],
                   help="whether GitHub Pages serves the portal (CI)")
    r.add_argument("--pages-branch", default="kicad-hub-site", help="branch the portal is published to (CI)")
    r.add_argument("--pages-settings-url", default="", help="the repository's Settings → Pages page (CI)")
    r.add_argument("--action-ref", default="", help="ref the workflow uses for the action (CI)")
    r.add_argument("--run-url", default="", help="link to the CI run")
    r.set_defaults(func=cmd_report)
    w = sub.add_parser("portal", help="the design portal page: viewer, report, BOM, suppliers, downloads")
    add_project(w)
    w.add_argument("--site", default="", help="CI: folder for this build's page only (default: the local "
                                              "site in <out>/portal/, page and front page)")
    for key in PORTAL_BUILD:
        w.add_argument(f"--{key}", default=None, type=int if key == "pr" else str,
                       help=argparse.SUPPRESS)  # CI only, with --site: as portal.py build takes them
    w.add_argument("--light", action="store_true", help="CI: light snapshot for the timeline")
    w.add_argument("--activity", action="store_true", help="CI: the site has an activity page")
    w.set_defaults(func=cmd_portal)
    n = sub.add_parser("run", help="every step on this computer: checks, BOMs, datasheets, prices, fab "
                                   "package, requirements, report and portal")
    add_common(n)
    n.add_argument("--base", default=None, help="branch or commit to compare with (default: main, else master; "
                                                "none: no comparison)")
    n.add_argument("--fab-profile", default="generic", help="generic | a folder of fab-profiles/")
    n.add_argument("--skip", default="", help="steps to leave out, comma-separated: " + ", ".join(RUN_STEPS))
    n.add_argument("--required-fields", default="Footprint,Datasheet,MPN|LCSC", help="fields every part needs")
    n.add_argument("--variants", default="auto", help="auto (all in the project) | none | a comma-separated list")
    n.add_argument("--dirs", default="", help="extra folders with datasheet files, comma-separated")
    n.add_argument("--check-links", default="true", help="true | false: check that the datasheet links answer")
    n.add_argument("--boards", default="1,5,10,100", help="batch sizes to price")
    n.add_argument("--currency", default="USD")
    n.add_argument("--choices", default="kicad-hub-sourcing.csv", help="supplier picked per part (project folder)")
    n.add_argument("--extra-formats", default="", help="odb, ipc2581 (comma-separated)")
    n.add_argument("--requirements-file", default="kicad-hub-requirements.yaml",
                   help="requirements file, relative to the project folder")
    n.add_argument("--tmp", default="", help="folder for temporary copies (default: the system's)")
    n.set_defaults(func=cmd_run)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (StepError, detect.DetectError, kicad.KiCadNotFound) as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
