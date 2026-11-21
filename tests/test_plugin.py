"""Tests of the KiCad plugin (plugin/) and of its package (scripts/build_plugin.py),
with a stand-in for KiCad's API (no KiCad needed)."""
import contextlib
import io
import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import types
import unittest
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.normpath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(ROOT, "plugin"))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import build_plugin  # noqa: E402
import kicad_hub_action as action  # noqa: E402

# KiCad's own rule for plugin identifiers (common/api/api_plugin.cpp, 10.0.5)
KICAD_IDENTIFIER = re.compile(r"^[a-zA-Z]{2,}(\.([a-zA-Z0-9][a-zA-Z0-9-]*[a-zA-Z0-9]|[a-zA-Z0-9])){2,}$")


def fake_kipy():
    """`kipy.proto.common.types.DocumentType` as the plugin imports it."""
    types_mod = types.ModuleType("kipy.proto.common.types")
    types_mod.DocumentType = types.SimpleNamespace(DOCTYPE_PCB=3)
    for name in ("kipy", "kipy.proto", "kipy.proto.common"):
        sys.modules.setdefault(name, types.ModuleType(name))
    sys.modules["kipy.proto.common.types"] = types_mod


class FakeKiCad:
    def __init__(self, folder, board="board.kicad_pcb", cli="/opt/kicad-cli"):
        self.folder, self.board, self.cli = folder, board, cli

    def get_open_documents(self, doc_type):
        if self.folder is None:
            return []
        return [types.SimpleNamespace(project=types.SimpleNamespace(path=self.folder), board_filename=self.board)]

    def get_kicad_binary_path(self, name):
        return self.cli


# Stands in for kicad_hub.py run: a step title, then run.json, as the engine writes them.
FAKE_ENGINE = r'''import json, os, sys
a = sys.argv[1:]
if os.environ.get("FAKE_ENGINE_FAIL"):
    print("== ERC / DRC"); print("::error::kicad-cli stopped"); sys.exit(1)
assert a[0] == "run", a
json.dump({"args": a, "cwd": os.getcwd()}, open(os.path.join(".kicad-hub", "args.json"), "w"))
print("== ERC / DRC"); print("== Portal")
json.dump({"name": "board", "page": "main/", "summary": [], "report": ".kicad-hub/reports/report.md"},
          open(os.path.join(".kicad-hub", "run.json"), "w"))
'''


class PluginFilesTest(unittest.TestCase):
    def test_plugin_json(self):
        with open(os.path.join(ROOT, "plugin", "plugin.json"), encoding="utf-8") as f:
            p = json.load(f)
        self.assertTrue(KICAD_IDENTIFIER.match(p["identifier"]))
        self.assertEqual(p["runtime"]["type"], "python")
        (a,) = p["actions"]
        self.assertEqual((a["scopes"], a["show-button"]), (["pcb"], True))
        for path in [a["entrypoint"], *a["icons-light"], *a["icons-dark"]]:
            self.assertTrue(os.path.isfile(os.path.join(ROOT, "plugin", path)), path)

    def test_requirements_name_kicad_python(self):
        with open(os.path.join(ROOT, "plugin", "requirements.txt"), encoding="utf-8") as f:
            lines = [line.strip() for line in f if line.strip() and not line.startswith("#")]
        self.assertTrue(any(line.startswith("kicad-python") for line in lines), lines)


class PackageTest(unittest.TestCase):
    def test_layout_and_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            with contextlib.redirect_stdout(io.StringIO()):
                path = build_plugin.build("1.6.0", tmp)
            self.assertEqual(os.path.basename(path), "kicad-hub-plugin-1.6.0.zip")
            with zipfile.ZipFile(path) as z:
                names = set(z.namelist())
                meta = json.loads(z.read("metadata.json"))
                z.extractall(os.path.join(tmp, "x"))
            for name in ("plugins/plugin.json", "plugins/requirements.txt", "plugins/kicad_hub_action.py",
                         "plugins/icon.png", "resources/icon.png", "plugins/kicad-hub/LICENSE",
                         "plugins/kicad-hub/scripts/kicad_hub.py", "plugins/kicad-hub/scripts/portal.html",
                         "plugins/kicad-hub/scripts/vendor/kicanvas.js",
                         "plugins/kicad-hub/fab-profiles/jlcpcb/rules.kicad_dru"):
                self.assertIn(name, names)
            self.assertFalse([n for n in names if "__pycache__" in n or n.endswith(".pyc") or "build_plugin" in n])
            self.assertEqual(meta["identifier"], build_plugin.IDENTIFIER)
            self.assertTrue(KICAD_IDENTIFIER.match(meta["identifier"]))
            self.assertEqual(meta["versions"], [{"version": "1.6.0", "status": "stable", "kicad_version": "10.0",
                                                 "runtime": "ipc"}])
            for key in ("name", "description", "description_full", "type", "author", "license"):
                self.assertTrue(meta[key], key)
            # the bundled engine is where the plugin looks for it, and it starts
            plugins = os.path.join(tmp, "x", "plugins")
            engine = action.find_engine(plugins)
            self.assertEqual(engine, os.path.join(plugins, "kicad-hub", "scripts", "kicad_hub.py"))
            r = subprocess.run([sys.executable, engine, "--help"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("serve", r.stdout)

    def test_bad_version(self):
        with self.assertRaises(SystemExit):
            build_plugin.build("v1", tempfile.gettempdir())


class HelpersTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def git(self, *args, cwd=None):
        subprocess.run(["git", "-c", "init.defaultBranch=main", *args], cwd=cwd or self.tmp.name, check=True,
                       capture_output=True)

    def test_engine_from_a_clone(self):
        self.assertEqual(action.find_engine(os.path.join(ROOT, "plugin")), os.path.join(ROOT, "scripts", "kicad_hub.py"))
        with self.assertRaisesRegex(action.PluginError, "reinstall"):
            action.find_engine(self.tmp.name)

    def test_work_folder_is_the_top_of_the_repository(self):
        project = os.path.join(self.tmp.name, "hardware", "board")
        os.makedirs(project)
        self.assertEqual(action.work_folder(project), os.path.normpath(project))      # no repository yet
        self.git("init", "-q")
        self.assertEqual(os.path.realpath(action.work_folder(project)), os.path.realpath(self.tmp.name))
        self.assertEqual(action.work_folder(project, git=None), os.path.normpath(project))

    def test_outputs_kept_out_of_git_once(self):
        self.git("init", "-q")
        action.ignore_outputs(self.tmp.name)
        action.ignore_outputs(self.tmp.name)
        with open(os.path.join(self.tmp.name, ".git", "info", "exclude"), encoding="utf-8") as f:
            self.assertEqual(f.read().count(".kicad-hub/"), 1)
        other = os.path.join(self.tmp.name, "other")
        os.makedirs(os.path.join(other, ".git"))
        with open(os.path.join(other, ".gitignore"), "w") as f:
            f.write("/.kicad-hub/\n")
        action.ignore_outputs(other)                                   # .gitignore already says so
        self.assertFalse(os.path.exists(os.path.join(other, ".git", "info", "exclude")))
        action.ignore_outputs(os.path.join(self.tmp.name, "no-repo"))  # not a repository: nothing

    def test_progress_page(self):
        running = action.progress_page("board", "Found x\n== ERC / DRC\n== BOM (and main)\nline <b>\n")
        self.assertIn('http-equiv="refresh" content="2"', running)
        self.assertIn("<strong>BOM (and main)</strong> (step 2)", running)
        self.assertIn("line &lt;b&gt;", running)                        # the log is escaped
        done = action.progress_page("board", "== Portal\n", done_url="http://127.0.0.1:9/main/")
        self.assertIn('content="0; url=http://127.0.0.1:9/main/"', done)
        failed = action.progress_page("board", "== ERC / DRC\nboom\n", failed=True)
        self.assertNotIn("refresh", failed)
        self.assertIn("<details open>", failed)


class ServerTest(unittest.TestCase):
    """The real `kicad_hub.py serve`, started in the background as the plugin does."""

    def test_start_then_reuse(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, ".kicad-hub")
            os.makedirs(os.path.join(out, "portal"))
            with open(os.path.join(out, "portal", "x.html"), "w") as f:
                f.write("x")
            self.assertIsNone(action.server_url(out))
            url = action.start_server(os.path.join(ROOT, "scripts", "kicad_hub.py"), out)
            try:
                self.assertRegex(url, r"^http://127\.0\.0\.1:\d+/$")
                self.assertEqual(action.server_url(out), url)                 # the next press reuses it
                other = os.path.join(tmp, "other", ".kicad-hub")
                os.makedirs(other)
                with open(os.path.join(out, "server.json"), encoding="utf-8") as f:
                    info = json.load(f)
                with open(os.path.join(other, "server.json"), "w", encoding="utf-8") as f:
                    json.dump(info, f)
                self.assertIsNone(action.server_url(other))                   # another project's server
            finally:
                with open(os.path.join(out, "server.json"), encoding="utf-8") as f:
                    pid = json.load(f)["pid"]
                os.kill(pid, signal.SIGTERM)
                for proc in action._SERVERS:
                    proc.wait(timeout=10)
                action._SERVERS.clear()
                self.assertIsNone(action.server_url(out))


class MainTest(unittest.TestCase):
    def setUp(self):
        fake_kipy()
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        self.project = os.path.join(self.repo, "hardware", "board")
        os.makedirs(self.project)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        engine = os.path.join(self.tmp.name, "engine.py")
        with open(engine, "w") as f:
            f.write(FAKE_ENGINE)
        self.opened = []
        for name, value in (("find_engine", lambda: engine), ("server_url", lambda out: None),
                            ("start_server", lambda engine, out: "http://127.0.0.1:1/")):
            self.addCleanup(setattr, action, name, getattr(action, name))
            setattr(action, name, value)
        self.addCleanup(os.environ.pop, "FAKE_ENGINE_FAIL", None)

    def test_runs_the_engine_and_ends_on_the_portal(self):
        self.assertEqual(action.main(FakeKiCad(self.project), browser=self.opened.append), 0)
        out = os.path.join(self.repo, ".kicad-hub")
        self.assertEqual(self.opened, ["http://127.0.0.1:1/" + action.PROGRESS])
        with open(os.path.join(out, "args.json"), encoding="utf-8") as f:
            ran = json.load(f)
        self.assertEqual(ran["args"], ["run", "--project-dir", "hardware/board", "--out", ".kicad-hub",
                                       "--kicad-cli", "/opt/kicad-cli"])
        self.assertEqual(os.path.realpath(ran["cwd"]), os.path.realpath(self.repo))
        with open(os.path.join(out, "portal", action.PROGRESS), encoding="utf-8") as f:
            self.assertIn('url=http://127.0.0.1:1/main/"', f.read())
        with open(os.path.join(out, "plugin.log"), encoding="utf-8") as f:
            self.assertIn("== Portal", f.read())

    def test_a_failed_run_says_why(self):
        os.environ["FAKE_ENGINE_FAIL"] = "1"
        with self.assertRaisesRegex(action.PluginError, "kicad-cli stopped"):
            action.main(FakeKiCad(self.project), browser=self.opened.append)
        with open(os.path.join(self.repo, ".kicad-hub", "portal", action.PROGRESS), encoding="utf-8") as f:
            self.assertIn("stopped with an error", f.read())

    def test_no_board_open(self):
        with self.assertRaisesRegex(action.PluginError, "No board is open"):
            action.main(FakeKiCad(None), browser=self.opened.append)
        self.assertEqual(self.opened, [])


if __name__ == "__main__":
    unittest.main()
