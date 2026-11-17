#!/usr/bin/env python3
"""kicad-hub's engine: the steps of the action as one program, for CI and for a PC.

    kicad_hub.py checks [--project-dir DIR] [--out DIR] [--fab-profile NAME]
                        [--kicad-cli PATH | --image IMAGE [--kicad-home DIR]]

`checks`: ERC and DRC (with schematic parity) of the project, as JSON and text reports in
<out>/reports/, plus a second DRC with a fab's rules when --fab-profile is given. Without
--image it uses the KiCad installed on this computer.

The action calls it step by step while the rest of its steps move here (roadmap, phase 7B).
Run it from the repository's folder: paths are relative to it.
"""
import argparse
import json
import os
import shutil
import sys
import tempfile

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


def main(argv=None):
    ap = argparse.ArgumentParser(description="kicad-hub engine")
    sub = ap.add_subparsers(dest="command", required=True)
    c = sub.add_parser("checks", help="ERC, DRC and the optional DRC with a fab's rules")
    c.add_argument("--project-dir", default="", help="folder with the .kicad_pro (default: the only one in the repository)")
    c.add_argument("--out", default=".kicad-hub", help="output folder (reports go to <out>/reports)")
    c.add_argument("--fab-profile", default="generic", help="generic | a folder of fab-profiles/")
    c.add_argument("--kicad-cli", default="", help="path of the local kicad-cli (default: found automatically)")
    c.add_argument("--image", default="", help="run kicad-cli in this Docker image instead of the local KiCad")
    c.add_argument("--kicad-home", default="", help="with --image: folder mounted as KiCad's HOME (library tables)")
    c.add_argument("--tmp", default="", help="folder for temporary copies (default: the system's)")
    c.set_defaults(func=cmd_checks)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (StepError, detect.DetectError, kicad.KiCadNotFound) as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
