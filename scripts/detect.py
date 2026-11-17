#!/usr/bin/env python3
"""Find the KiCad project the action works on.

    detect.py [--project-dir DIR] [--root .] [--exclude DIR ...]

With --project-dir: the .kicad_pro in that folder (as given by the workflow).
Without it: the only .kicad_pro in the repository, so a board repository needs no
option at all. Hidden folders (.git, .history...) and --exclude folders (the output
folder) are not searched. None or more than one is an error that says what to do.

Prints the step outputs, one per line: dir=, name=, has_pcb=. Messages for the log go
to stderr as workflow commands (::error::, ::warning::). Exit code 1 on error.
"""
import argparse
import os
import sys


class DetectError(Exception):
    pass


def find_projects(root, exclude=()):
    """Paths of every .kicad_pro under root, relative to it, sorted."""
    skip = {os.path.normpath(e) for e in exclude if e}
    found = []
    for base, dirs, files in os.walk(root):
        rel = os.path.normpath(os.path.relpath(base, root))
        dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                         and os.path.normpath(os.path.join(rel, d)) not in skip)
        found += [os.path.normpath(os.path.join(rel, f)) for f in files if f.endswith(".kicad_pro")]
    return sorted(p.replace(os.sep, "/") for p in found)


def detect(project_dir="", root=".", exclude=()):
    """(dir, name, has_pcb, note). dir is relative to root; note is a log line or ''."""
    note = ""
    if project_dir:
        folder = os.path.join(root, project_dir)
        pros = sorted(f for f in os.listdir(folder) if f.endswith(".kicad_pro")) if os.path.isdir(folder) else []
        if not pros:
            raise DetectError(f"No .kicad_pro found in {project_dir}")
        pro = pros[0]
    else:
        found = find_projects(root, exclude)
        if not found:
            raise DetectError("No .kicad_pro found in the repository: push the KiCad project "
                              "(.kicad_pro, .kicad_sch, .kicad_pcb) first")
        if len(found) > 1:
            raise DetectError("More than one KiCad project in the repository (" + ", ".join(found) +
                              "): set project-dir in the workflow to the folder of the one to check")
        project_dir = os.path.dirname(found[0]) or "."
        pro = os.path.basename(found[0])
        note = f"Found {found[0]}: using it (set project-dir to choose another folder)"
    name = pro[:-len(".kicad_pro")]
    project_dir = project_dir.rstrip("/") or ("/" if project_dir.startswith("/") else ".")
    return project_dir, name, has_outline(os.path.join(root, project_dir, name + ".kicad_pcb")), note


def has_outline(pcb):
    """A board without anything on Edge.Cuts is still being drawn: no PCB checks or outputs."""
    try:
        with open(pcb, encoding="utf-8", errors="replace") as f:
            return any('(layer "Edge.Cuts")' in line for line in f)
    except OSError:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-dir", default="")
    ap.add_argument("--root", default=".")
    ap.add_argument("--exclude", action="append", default=[], help="folder (relative to root) not to search")
    args = ap.parse_args()
    try:
        folder, name, has_pcb, note = detect(args.project_dir.strip(), args.root, args.exclude)
    except DetectError as e:
        print(f"::error::{e}", file=sys.stderr)
        return 1
    if note:
        print(note, file=sys.stderr)
    if not has_pcb:
        print(f"::warning::{name} has no board outline yet: skipping DRC and PCB outputs", file=sys.stderr)
    print(f"dir={folder}\nname={name}\nhas_pcb={'true' if has_pcb else 'false'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
