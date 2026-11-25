#!/usr/bin/env python3
"""Build the KiCad plugin package: a ZIP for KiCad's Plugin and Content Manager.

    build_plugin.py --version 1.6.0 [--out dist]

Layout (https://dev-docs.kicad.org/en/addons/):
    metadata.json            the package's description, "runtime": "ipc"
    plugins/                 plugin.json, requirements.txt, kicad_hub_action.py, icon.png
    plugins/kicad-hub/       the engine: scripts/ and fab-profiles/ from this repository
    resources/icon.png       the 64x64 icon shown in the Plugin and Content Manager

Install it in KiCad with Plugin and Content Manager → Install from File, then Apply
Pending Changes. The engine is the same code the GitHub Action runs.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import zipfile

ROOT = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
IDENTIFIER = "com.github.jmcordobamendez.kicad-hub"
REPO = "https://github.com/JMcordobamendez/kicad-hub"
PLUGIN_FILES = ("plugin.json", "requirements.txt", "kicad_hub_action.py", "icon.png")
ENGINE_DIRS = ("scripts", "fab-profiles")


def metadata(version):
    return {
        "$schema": "https://go.kicad.org/pcm/schemas/v1",
        "name": "kicad-hub",
        "description": "Design review of the open board: checks, BOM, prices, fab package, portal",
        "description_full": (
            "One button in the PCB editor runs on the open project the same checks as the kicad-hub "
            "GitHub Action: ERC and DRC (also against a fab's rules), per-part BOM and variants compared "
            "with main, datasheets, price and stock (LCSC and JLCPCB with no key; Mouser, Farnell and "
            "DigiKey with your own keys), the fab package (Gerber X2, Excellon, IPC-D-356, BOM and pick & "
            "place, fab notes), requirements and a report. The results open in the browser as a design "
            "portal served from this computer only.\n\n"
            "Needs Preferences > Plugins > Enable KiCad API. On install KiCad sets up the plugin's Python "
            "environment and downloads kicad-python (needs network once). Price lookups contact the "
            "suppliers' public services; nothing else leaves the computer."),
        "identifier": IDENTIFIER,
        "type": "plugin",
        "author": {"name": "José Miguel Córdoba Méndez", "contact": {"web": REPO}},
        "maintainer": {"name": "José Miguel Córdoba Méndez", "contact": {"web": REPO}},
        "license": "MIT",
        "resources": {"homepage": REPO, "Issues": f"{REPO}/issues"},
        "versions": [{"version": version, "status": "stable", "kicad_version": "10.0", "runtime": "ipc"}],
    }


def engine_files():
    """(path in the repository, path in the package) of every engine file."""
    for top in ENGINE_DIRS:
        for folder, dirs, files in os.walk(os.path.join(ROOT, top)):
            dirs[:] = sorted(d for d in dirs if d != "__pycache__")
            for name in sorted(files):
                if name.endswith((".pyc", ".pyo")) or name == "build_plugin.py":
                    continue
                src = os.path.join(folder, name)
                rel = os.path.relpath(src, ROOT).replace(os.sep, "/")
                yield src, f"plugins/kicad-hub/{rel}"


def build(version, out_dir):
    if not re.fullmatch(r"\d+\.\d+(\.\d+)?", version):
        raise SystemExit(f"version must look like 1.6.0, not {version!r}")
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, f"kicad-hub-plugin-{version}.zip")
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("metadata.json", json.dumps(metadata(version), indent=2, ensure_ascii=False) + "\n")
        for name in PLUGIN_FILES:
            z.write(os.path.join(ROOT, "plugin", name), f"plugins/{name}")
        z.write(os.path.join(ROOT, "plugin", "icon-64.png"), "resources/icon.png")
        z.write(os.path.join(ROOT, "LICENSE"), "plugins/kicad-hub/LICENSE")
        for src, dst in engine_files():
            z.write(src, dst)
    with open(path, "rb") as f:
        digest = hashlib.sha256(f.read()).hexdigest()
    print(f"{path}\nsha256 {digest}\nsize {os.path.getsize(path)}")
    return path


def main(argv=None):
    ap = argparse.ArgumentParser(description="Build the kicad-hub KiCad plugin package")
    ap.add_argument("--version", required=True, help="plugin version, e.g. 1.6.0 (no leading v)")
    ap.add_argument("--out", default="dist", help="folder for the ZIP")
    args = ap.parse_args(argv)
    build(args.version.lstrip("v"), args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
