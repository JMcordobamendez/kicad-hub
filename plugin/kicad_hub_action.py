#!/usr/bin/env python3
"""The "kicad-hub" button of the PCB editor (a KiCad IPC API plugin).

KiCad starts this script as its own process when the button is pressed, with the
plugin's Python (KiCad made it when the plugin was installed).

When the kicad-hub desktop app is installed, the button only opens it on the board's
project, or brings its window to the front: the app does the checks. Without the app it:

1. asks KiCad which board is open and where `kicad-cli` is;
2. starts, or reuses, a small web server on this computer (127.0.0.1 only) for the
   portal, and opens the browser on a progress page;
3. runs the engine, `kicad_hub.py run`, on the project (the same checks as the GitHub
   Action: ERC/DRC, BOM, datasheets, prices, fab package, requirements, report, portal),
   updating the progress page as it goes;
4. sends the browser to the design portal when it is done, or shows what went wrong.

Everything is written to `.kicad-hub/` at the top of the project's git repository (or in
the project folder when there is none). The run's log is `.kicad-hub/plugin.log`.
"""
import html
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = ".kicad-hub"
PROGRESS = "kicad-hub-progress.html"  # inside the portal folder, so the server serves it
SERVER_IDLE = 3600  # seconds without a request before the server stops by itself

# On Windows, start child processes without a console window of their own (kicad-cli is a
# console program; without this, every call would flash a window).
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
_SERVERS = []  # the background servers started here: they are meant to outlive this script


class PluginError(Exception):
    """Something the user has to fix; the message is shown in KiCad."""


# --- where things are ---------------------------------------------------------------------

def find_engine(here=HERE):
    """kicad_hub.py: bundled with the plugin, or the repository's when run from a clone."""
    for candidate in (os.path.join(here, "kicad-hub", "scripts", "kicad_hub.py"),
                      os.path.join(here, "..", "scripts", "kicad_hub.py")):
        if os.path.isfile(candidate):
            return os.path.normpath(candidate)
    raise PluginError("The kicad-hub engine is missing from the plugin: please reinstall it")


def work_folder(project_dir, git=shutil.which("git")):
    """Where to run: the top of the project's git repository (so the BOM can be compared
    with `main`), else the project folder itself."""
    if git:
        try:
            r = subprocess.run([git, "-C", project_dir, "rev-parse", "--show-toplevel"], capture_output=True,
                               text=True, timeout=30, creationflags=NO_WINDOW)
            top = r.stdout.strip()
            if r.returncode == 0 and top and os.path.isdir(top):
                return os.path.normpath(top)
        except (OSError, subprocess.SubprocessError):
            pass
    return os.path.normpath(project_dir)


def ignore_outputs(work):
    """Keep .kicad-hub/ out of git: a line in .git/info/exclude, which stays on this
    computer (it is never committed), unless .gitignore already says so."""
    info = os.path.join(work, ".git", "info")
    if not os.path.isdir(os.path.join(work, ".git")):
        return
    for name in (os.path.join(work, ".gitignore"), os.path.join(info, "exclude")):
        try:
            with open(name, encoding="utf-8") as f:
                if any(line.strip().strip("/") == OUT for line in f):
                    return
        except OSError:
            pass
    os.makedirs(info, exist_ok=True)
    with open(os.path.join(info, "exclude"), "a", encoding="utf-8") as f:
        f.write(f"\n# kicad-hub's local outputs (the KiCad plugin)\n{OUT}/\n")


# --- the desktop app ------------------------------------------------------------------------

def find_app(env=None):
    """The kicad-hub desktop app, when installed: $KICAD_HUB_APP, the installer's folder
    (per user, then for everyone) or the PATH. None when it is not installed."""
    env = os.environ if env is None else env
    candidates = [env.get("KICAD_HUB_APP", "")]
    if os.name == "nt":
        for base in (env.get("LOCALAPPDATA"), env.get("ProgramFiles"), env.get("ProgramW6432")):
            if base:
                candidates.append(os.path.join(base, "kicad-hub", "kicad-hub.exe"))
    elif sys.platform == "darwin":
        candidates.append("/Applications/kicad-hub.app/Contents/MacOS/kicad-hub")
    candidates.append(shutil.which("kicad-hub") or "")
    return next((c for c in candidates if c and os.path.isfile(c)), None)


def open_app(app, project_dir):
    """Start the app on the project; when it is already running, the running one comes to
    the front instead (it allows one window only) and switches to this project."""
    detach = ({"creationflags": getattr(subprocess, "DETACHED_PROCESS", 0)
               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt"
              else {"start_new_session": True})
    subprocess.Popen([app, "--project", project_dir], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, close_fds=True, **detach)


# --- the web server -----------------------------------------------------------------------

def server_url(out):
    """The URL of a kicad-hub server already serving this project, or None."""
    try:
        with open(os.path.join(out, "server.json"), encoding="utf-8") as f:
            info = json.load(f)
        url = f"http://127.0.0.1:{int(info['port'])}/"
        if os.path.normcase(info.get("root", "")) != os.path.normcase(os.path.abspath(os.path.join(out, "portal"))):
            return None
        with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(url, timeout=2):
            return url
    except Exception:  # no file, an old one, or the server has stopped
        return None


def start_server(engine, out, python=sys.executable, wait=10.0):
    """Start `kicad_hub.py serve` in the background; it outlives this script and stops by
    itself after an hour without requests. Returns its URL."""
    ready = os.path.join(out, "server.json")
    try:
        os.remove(ready)
    except OSError:
        pass
    detach = ({"creationflags": NO_WINDOW | getattr(subprocess, "DETACHED_PROCESS", 0)
               | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)} if os.name == "nt"
              else {"start_new_session": True})
    with open(os.path.join(out, "server.log"), "a", encoding="utf-8") as log:
        _SERVERS.append(subprocess.Popen([python, engine, "serve", "--out", out, "--port", "0", "--idle",
                                          str(SERVER_IDLE), "--ready-file", ready],
                                         stdout=log, stderr=log, stdin=subprocess.DEVNULL, **detach))
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        url = server_url(out)
        if url:
            return url
        time.sleep(0.2)
    raise PluginError(f"The local web server did not start: see {os.path.join(out, 'server.log')}")


# --- the progress page --------------------------------------------------------------------

def progress_page(title, log_text, done_url=None, failed=False):
    """A page that reloads itself every two seconds while the run goes on, and sends the
    browser to the portal at the end."""
    steps = re.findall(r"^== (.+)$", log_text, re.M)
    current = steps[-1] if steps else "Starting"
    tail = "\n".join(log_text.splitlines()[-40:])
    if done_url:
        head = f'<meta http-equiv="refresh" content="0; url={html.escape(done_url)}">'
        status = f'Done. <a href="{html.escape(done_url)}">Open the design portal</a>.'
    elif failed:
        head = ""
        status = "<strong>The checks stopped with an error.</strong> The end of the log is below."
    else:
        head = '<meta http-equiv="refresh" content="2">'
        status = f"Working: <strong>{html.escape(current)}</strong> (step {len(steps) or 1})…"
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">{head}
<title>kicad-hub: {html.escape(title)}</title>
<style>body{{font-family:system-ui,sans-serif;margin:32px;color:#1f2328;max-width:900px}}
pre{{background:#f6f8fa;padding:12px;overflow:auto;font-size:12px;max-height:60vh}}</style></head>
<body><h1>kicad-hub · {html.escape(title)}</h1><p>{status}</p>
<details{' open' if failed else ''}><summary>Log</summary><pre>{html.escape(tail)}</pre></details></body></html>"""


def write_page(path, text):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# --- the run ------------------------------------------------------------------------------

def run_engine(engine, work, project_rel, kicad_cli, out, page, title, python=sys.executable):
    """Run the checks, keeping the progress page up to date. Returns (ok, log text)."""
    log_path = os.path.join(out, "plugin.log")
    cmd = [python, engine, "run", "--project-dir", project_rel, "--out", OUT]
    if kicad_cli:
        cmd += ["--kicad-cli", kicad_cli]
    with open(log_path, "w", encoding="utf-8") as log:
        proc = subprocess.Popen(cmd, cwd=work, stdout=log, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                creationflags=NO_WINDOW, env=engine_env())
        while proc.poll() is None:
            write_page(page, progress_page(title, read(log_path)))
            time.sleep(1)
    return proc.returncode == 0, read(log_path)


def engine_env(env=None):
    """The engine's environment: Python in UTF-8 mode, so its log is UTF-8 on Windows too
    (with the console's code page, "·" and accents came out broken in plugin.log)."""
    env = dict(os.environ if env is None else env)
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def read(path):
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def open_board(kicad):
    """(project folder, board file name) of the board open in the PCB editor."""
    from kipy.proto.common.types import DocumentType  # noqa: PLC0415 - only inside KiCad's plugin environment
    docs = kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
    if not docs:
        raise PluginError("No board is open: open the project's PCB in the PCB editor first")
    doc = docs[0]
    folder = doc.project.path
    if not folder or not os.path.isdir(folder):
        raise PluginError("Save the project first: KiCad gave no project folder for this board")
    return folder, doc.board_filename


def main(kicad=None, browser=webbrowser.open, app=None, launch=open_app):
    if kicad is None:
        from kipy import KiCad  # noqa: PLC0415 - installed by KiCad with the plugin (requirements.txt)
        kicad = KiCad()
    project_dir, board = open_board(kicad)
    app = find_app() if app is None else app
    if app:
        launch(app, project_dir)
        return 0
    try:
        kicad_cli = kicad.get_kicad_binary_path("kicad-cli")
    except Exception:  # an older KiCad: the engine finds it by itself
        kicad_cli = ""
    engine = find_engine()
    work = work_folder(project_dir)
    project_rel = os.path.relpath(project_dir, work).replace(os.sep, "/")
    out = os.path.join(work, OUT)
    portal_dir = os.path.join(out, "portal")
    os.makedirs(portal_dir, exist_ok=True)
    ignore_outputs(work)
    title = os.path.splitext(board or os.path.basename(project_dir))[0]

    page = os.path.join(portal_dir, PROGRESS)
    write_page(page, progress_page(title, ""))
    url = server_url(out) or start_server(engine, out)
    browser(url + PROGRESS)

    ok, log_text = run_engine(engine, work, project_rel, kicad_cli, out, page, title)
    done = None
    if ok:
        try:
            with open(os.path.join(out, "run.json"), encoding="utf-8") as f:
                done = json.load(f).get("page")
        except (OSError, ValueError):
            done = None
    write_page(page, progress_page(title, log_text, done_url=url + done if done else None, failed=not ok))
    if not ok:
        last = [line for line in log_text.splitlines() if line.strip()][-1:] or ["no output"]
        raise PluginError(f"The checks stopped: {last[0]} (log: {os.path.join(out, 'plugin.log')})")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except PluginError as e:
        print(f"kicad-hub: {e}", file=sys.stderr)  # KiCad shows this in its messages
        sys.exit(1)
