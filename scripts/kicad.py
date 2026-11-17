#!/usr/bin/env python3
"""Run kicad-cli, the same way from CI and from a PC.

Two ways, one interface (the engine doesn't care which):
  - Docker: `kicad-cli` inside an image (the GitHub Action; same KiCad for everybody).
  - Local:  the `kicad-cli` of the KiCad installed on this computer (local mode, plugin).

Paths given to kicad-cli are relative to the work folder (the repository, by default),
which is the current directory in both cases.
"""
import glob
import os
import shutil
import subprocess
import sys

EXE = "kicad-cli.exe" if os.name == "nt" else "kicad-cli"


class KiCadNotFound(Exception):
    pass


def find_cli(explicit="", env=None, which=shutil.which, python=None):
    """Path of the local kicad-cli, or raise KiCadNotFound saying how to give it.

    In order: the path given, $KICAD_CLI, the PATH, next to the running Python (KiCad's
    own Python lives beside kicad-cli, so a plugin or KiCad's command prompt needs no
    setup), then the usual install folders (the newest version first).
    """
    env = os.environ if env is None else env
    python = sys.executable if python is None else python
    for given, what in ((explicit, "--kicad-cli"), (env.get("KICAD_CLI", ""), "KICAD_CLI")):
        if given:
            if os.path.isfile(given):
                return given
            raise KiCadNotFound(f"{what} points to {given}, which does not exist")
    found = which("kicad-cli")
    if found:
        return found
    candidates = [os.path.join(os.path.dirname(python), EXE)] if python else []
    for base in (env.get("ProgramFiles"), env.get("ProgramW6432")):
        if base:
            candidates += sorted(glob.glob(os.path.join(base, "KiCad", "*", "bin", "kicad-cli.exe")),
                                 key=_version_key, reverse=True)
    candidates += ["/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli", "/usr/bin/kicad-cli",
                   "/usr/local/bin/kicad-cli"]
    for c in candidates:
        if os.path.isfile(c):
            return c
    raise KiCadNotFound("kicad-cli not found: install KiCad, or give its path with --kicad-cli "
                        "(or the KICAD_CLI variable), e.g. C:\\Program Files\\KiCad\\10.0\\bin\\kicad-cli.exe")


def _version_key(path):
    """.../KiCad/10.0/bin/kicad-cli.exe -> (10, 0), so 10.0 sorts after 9.0."""
    name = os.path.basename(os.path.dirname(os.path.dirname(path)))
    return tuple(int(p) if p.isdigit() else -1 for p in name.split("."))


class KiCad:
    """kicad-cli from a Docker image (image=...) or from this computer (cli=...)."""

    def __init__(self, cli="", image="", home="", workdir="."):
        if bool(cli) == bool(image):
            raise ValueError("give either cli (local) or image (Docker)")
        self.cli, self.image, self.home, self.workdir = cli, image, home, workdir

    @property
    def where(self):
        return f"Docker image {self.image}" if self.image else self.cli

    def command(self, args, workdir=None):
        """The command line that runs `kicad-cli <args>` with workdir as current folder."""
        if not self.image:
            return [self.cli, *args]
        work = os.path.abspath(workdir or self.workdir)
        cmd = ["docker", "run", "--rm", "-e", "LANG=C.UTF-8", "-e", "LC_ALL=C.UTF-8"]
        if hasattr(os, "getuid"):  # files written by the container belong to the caller
            cmd += ["-u", f"{os.getuid()}:{os.getgid()}"]
        if self.home:  # KiCad's configuration (the standard library tables), shared by all runs
            cmd += ["-e", "HOME=/kh", "-v", f"{os.path.abspath(self.home)}:/kh"]
        return cmd + ["-v", f"{work}:/w", "-w", "/w", self.image, "kicad-cli", *args]

    def run(self, *args, workdir=None):
        """Run kicad-cli; its exit code (its output goes to ours). Never raises on failure."""
        try:
            return subprocess.run(self.command(args, workdir), cwd=None if self.image else (workdir or self.workdir),
                                  env=None if self.image else local_env()).returncode
        except OSError as e:
            print(f"::error::Could not start {self.where}: {e}", file=sys.stderr)
            return 127


def local_env(env=None):
    """The environment for a local kicad-cli: a UTF-8 locale when there is none.

    With a C/POSIX locale kicad-cli mixes up names with non-ASCII characters (netclasses
    such as "Señal" were checked with another class's clearance). Windows is not affected.
    """
    env = dict(os.environ if env is None else env)
    if os.name != "nt":
        current = env.get("LC_ALL") or env.get("LC_CTYPE") or env.get("LANG") or ""
        if current in ("", "C", "POSIX"):
            env["LANG"] = env["LC_ALL"] = "C.UTF-8"
    return env
