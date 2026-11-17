#!/usr/bin/env python3
"""Run kicad-cli, the same way from CI and from a PC.

Two ways, one interface (the engine doesn't care which):
  - Docker: `kicad-cli` inside an image (the GitHub Action; same KiCad for everybody).
  - Local:  the `kicad-cli` of the KiCad installed on this computer (local mode, plugin).

Paths given to kicad-cli are relative to the work folder (the repository, by default),
which is the current directory in both cases.
"""
import glob
import importlib.util
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


def find_python(cli, python=None, which=shutil.which, has_pcbnew=None):
    """A Python that can `import pcbnew`, for the KiCad whose kicad-cli is `cli`; None if unknown.

    The running one when it already can (KiCad's own Python, or a Linux system Python),
    else the one KiCad ships beside kicad-cli (Windows) or in its application bundle
    (macOS), else the system `python3` (Linux packages put pcbnew there).
    """
    python = sys.executable if python is None else python
    if has_pcbnew is None:
        has_pcbnew = importlib.util.find_spec("pcbnew") is not None
    if has_pcbnew and python:
        return python
    folder = os.path.dirname(os.path.abspath(cli))
    for candidate in (os.path.join(folder, "python.exe"),
                      os.path.join(folder, "..", "Frameworks", "Python.framework", "Versions", "Current", "bin", "python3")):
        if os.path.isfile(candidate):
            return os.path.normpath(candidate)
    return which("python3")


def _version_key(path):
    """.../KiCad/10.0/bin/kicad-cli.exe -> (10, 0), so 10.0 sorts after 9.0."""
    name = os.path.basename(os.path.dirname(os.path.dirname(path)))
    return tuple(int(p) if p.isdigit() else -1 for p in name.split("."))


class KiCad:
    """kicad-cli from a Docker image (image=...) or from this computer (cli=...)."""

    def __init__(self, cli="", image="", home="", workdir=".", python=""):
        if bool(cli) == bool(image):
            raise ValueError("give either cli (local) or image (Docker)")
        self.cli, self.image, self.home, self.workdir = cli, image, home, workdir
        self._python = python  # local only: a Python with pcbnew ("" = find it when needed)

    @property
    def where(self):
        return f"Docker image {self.image}" if self.image else self.cli

    def command(self, args, workdir=None):
        """The command line that runs `kicad-cli <args>` with workdir as current folder."""
        if not self.image:
            return [self.cli, *args]
        return self._docker(workdir) + ["kicad-cli", *args]

    def python_command(self, args, workdir=None):
        """The command line that runs `python <args>` with KiCad's Python (it has pcbnew)."""
        if self.image:
            return self._docker(workdir) + ["python3", *args]
        python = self._python or find_python(self.cli)
        if not python:
            raise KiCadNotFound("No Python with KiCad's pcbnew module was found next to " + self.cli)
        return [python, *args]

    def _docker(self, workdir=None):
        work = os.path.abspath(workdir or self.workdir)
        cmd = ["docker", "run", "--rm", "-e", "LANG=C.UTF-8", "-e", "LC_ALL=C.UTF-8"]
        if hasattr(os, "getuid"):  # files written by the container belong to the caller
            cmd += ["-u", f"{os.getuid()}:{os.getgid()}"]
        if self.home:  # KiCad's configuration (the standard library tables), shared by all runs
            cmd += ["-e", "HOME=/kh", "-v", f"{os.path.abspath(self.home)}:/kh"]
        return cmd + ["-v", f"{work}:/w", "-w", "/w", self.image]

    def run(self, *args, workdir=None):
        """Run kicad-cli; its exit code (its output goes to ours). Never raises on failure."""
        return self._start(self.command(args, workdir), workdir)

    def python(self, *args, workdir=None, stdout=None):
        """Run a script with KiCad's Python; its exit code. `stdout`: an open file to write to."""
        return self._start(self.python_command(args, workdir), workdir, stdout)

    def _start(self, command, workdir=None, stdout=None):
        try:
            return subprocess.run(command, cwd=None if self.image else (workdir or self.workdir),
                                  env=None if self.image else local_env(), stdout=stdout).returncode
        except OSError as e:
            print(f"::error::Could not start {command[0]}: {e}", file=sys.stderr)
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
