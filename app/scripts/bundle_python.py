#!/usr/bin/env python3
"""Put a Python for Windows inside the app: the official embeddable package, with
kicad-python (Show in KiCad) installed in it. The installer ships it, so the app needs
nothing installed on the PC but KiCad.

    python app/scripts/bundle_python.py [--out app/src-tauri/python]

Runs on Windows or Linux (it downloads Windows wheels either way, with the running
Python's pip). The engine itself needs only the standard library.

What it does:
1. downloads python-<VERSION>-embed-amd64.zip from python.org and checks its SHA-256;
2. unpacks it into --out;
3. adds Lib\\site-packages to the interpreter's path file (python313._pth);
4. installs kicad-python and what it needs there, as Windows wheels for CPython 3.13.
"""
import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import urllib.request
import zipfile

VERSION = "3.13.17"
SHA256 = "9e35319bffedab8b36ad44d8f377cb4f1733bcec257a0b6d04d94f01486ddb38"
URL = f"https://www.python.org/ftp/python/{VERSION}/python-{VERSION}-embed-amd64.zip"
PACKAGES = ["kicad-python==0.9.2"]  # Show in KiCad; the engine uses only the standard library
HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_OUT = os.path.normpath(os.path.join(HERE, "..", "src-tauri", "python"))


def download(url, path, sha256):
    if not (os.path.isfile(path) and digest(path) == sha256):
        print(f"Downloading {url}", flush=True)
        with urllib.request.urlopen(url, timeout=120) as r, open(path + ".part", "wb") as f:
            shutil.copyfileobj(r, f)
        os.replace(path + ".part", path)
    got = digest(path)
    if got != sha256:
        raise SystemExit(f"{path}: SHA-256 {got}, expected {sha256}: not the file python.org published")


def digest(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def path_file(out):
    """The ._pth file: with one, Python ignores PYTHONPATH and site-packages unless listed."""
    names = [n for n in os.listdir(out) if n.endswith("._pth")]
    if len(names) != 1:
        raise SystemExit(f"expected one ._pth file in {out}, found {names}")
    return os.path.join(out, names[0])


def enable_site_packages(out):
    pth = path_file(out)
    with open(pth, encoding="utf-8") as f:
        lines = [line.rstrip("\r\n") for line in f]
    if "Lib\\site-packages" not in lines:
        lines.insert(lines.index(".") + 1 if "." in lines else len(lines), "Lib\\site-packages")
    with open(pth, "w", encoding="utf-8", newline="\r\n") as f:
        f.write("\n".join(lines) + "\n")


def install_packages(out, packages):
    target = os.path.join(out, "Lib", "site-packages")
    os.makedirs(target, exist_ok=True)
    subprocess.run([sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check",
                    "--no-compile", "--target", target, "--platform", "win_amd64", "--python-version", "3.13",
                    "--implementation", "cp", "--only-binary=:all:", *packages], check=True)
    shutil.rmtree(os.path.join(target, "bin"), ignore_errors=True)  # console scripts: not used
    for folder, dirs, _ in os.walk(target):
        for d in [d for d in dirs if d == "__pycache__"]:
            shutil.rmtree(os.path.join(folder, d), ignore_errors=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="Bundle Windows' embeddable Python with kicad-python")
    ap.add_argument("--out", default=DEFAULT_OUT, help="where to put it (default: app/src-tauri/python)")
    ap.add_argument("--cache", default="", help="folder to keep the download in (default: next to --out)")
    args = ap.parse_args(argv)
    out = os.path.abspath(args.out)
    cache = os.path.abspath(args.cache or os.path.dirname(out))
    os.makedirs(cache, exist_ok=True)
    archive = os.path.join(cache, os.path.basename(URL))
    download(URL, archive, SHA256)
    shutil.rmtree(out, ignore_errors=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(out)
    enable_site_packages(out)
    install_packages(out, PACKAGES)
    size = sum(os.path.getsize(os.path.join(d, f)) for d, _, fs in os.walk(out) for f in fs)
    print(f"Python {VERSION} for Windows with {', '.join(PACKAGES)} in {out} ({size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
