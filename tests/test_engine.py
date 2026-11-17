"""Tests of scripts/kicad.py and scripts/kicad_hub.py with a fake kicad-cli (no KiCad needed)."""
import contextlib
import io
import json
import os
import stat
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import kicad  # noqa: E402
import kicad_hub  # noqa: E402

OUTLINE = '(kicad_pcb\n  (gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts"))\n)\n'

# Stands in for kicad-cli: writes the file given with -o. For a DRC it puts the project's
# .kicad_dru and the list of files beside the board in the JSON, so tests can look at them.
# Windows can't start a script by its first line, so there it gets a .cmd in front.
FAKE_CLI = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
if os.environ.get("FAKE_FAIL"):
    sys.exit(3)
out, target, fmt = a[a.index("-o") + 1], a[-1], a[a.index("--format") + 1]
if fmt == "report":
    open(out, "w").write("report of " + target + "\n")
elif a[:2] == ["sch", "erc"]:
    json.dump({"sheets": [{"violations": [{"severity": "error"}, {"severity": "warning"}, {"severity": "warning"}]}]},
              open(out, "w"))
else:
    folder = os.path.dirname(target) or "."
    dru = os.path.splitext(target)[0] + ".kicad_dru"
    json.dump({"violations": [{"severity": "error"}], "unconnected_items": [1, 2],
               "dru": open(dru).read() if os.path.exists(dru) else "",
               "files": sorted(os.listdir(folder)), "args": a}, open(out, "w"))
'''


class FindCliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def touch(self, *parts):
        path = os.path.join(self.tmp.name, *parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        open(path, "w").close()
        return path

    def find(self, explicit="", env=None, which=None, python=""):
        return kicad.find_cli(explicit, env or {}, lambda name: which, python)

    def test_explicit_path_wins(self):
        cli = self.touch("a", "kicad-cli")
        self.assertEqual(self.find(cli, {"KICAD_CLI": "/other"}, "/usr/bin/kicad-cli"), cli)

    def test_explicit_path_that_does_not_exist(self):
        with self.assertRaisesRegex(kicad.KiCadNotFound, "--kicad-cli points to /nope"):
            self.find("/nope")
        with self.assertRaisesRegex(kicad.KiCadNotFound, "KICAD_CLI points to /nope"):
            self.find(env={"KICAD_CLI": "/nope"})

    def test_environment_then_path(self):
        cli = self.touch("b", "kicad-cli")
        self.assertEqual(self.find(env={"KICAD_CLI": cli}, which="/usr/bin/kicad-cli"), cli)
        self.assertEqual(self.find(which="/somewhere/kicad-cli"), "/somewhere/kicad-cli")

    def test_next_to_the_running_python(self):
        cli = self.touch("Kicad10", "bin", kicad.EXE)
        python = os.path.join(self.tmp.name, "Kicad10", "bin", "python.exe")
        self.assertEqual(self.find(python=python), cli)

    def test_newest_version_in_program_files(self):
        for v in ("7.0", "9.0", "10.0"):
            self.touch("PF", "KiCad", v, "bin", "kicad-cli.exe")
        found = self.find(env={"ProgramFiles": os.path.join(self.tmp.name, "PF")})
        self.assertEqual(found, os.path.join(self.tmp.name, "PF", "KiCad", "10.0", "bin", "kicad-cli.exe"))

    def test_not_found_says_what_to_do(self):
        if os.path.isfile("/usr/bin/kicad-cli") or os.path.isfile("/usr/local/bin/kicad-cli"):
            self.skipTest("KiCad is installed here")
        with self.assertRaisesRegex(kicad.KiCadNotFound, "--kicad-cli"):
            self.find()


class RunnerTest(unittest.TestCase):
    def test_local_command(self):
        k = kicad.KiCad(cli="/opt/kicad-cli")
        self.assertEqual(k.command(["sch", "erc", "x.kicad_sch"]), ["/opt/kicad-cli", "sch", "erc", "x.kicad_sch"])
        self.assertEqual(k.where, "/opt/kicad-cli")

    def test_docker_command(self):
        k = kicad.KiCad(image="img:1", home="/tmp/kh")
        cmd = k.command(["pcb", "drc", "b.kicad_pcb"], workdir="/work")
        self.assertEqual(cmd[:2], ["docker", "run"])
        self.assertEqual(cmd[-5:], ["img:1", "kicad-cli", "pcb", "drc", "b.kicad_pcb"])
        joined = " ".join(cmd)
        for part in ("-e LANG=C.UTF-8", "-e LC_ALL=C.UTF-8", "-e HOME=/kh", f"-v {os.path.abspath('/tmp/kh')}:/kh",
                     f"-v {os.path.abspath('/work')}:/w", "-w /w"):
            self.assertIn(part, joined)
        if hasattr(os, "getuid"):  # not on Windows
            self.assertIn(f"-u {os.getuid()}:{os.getgid()}", joined)

    def test_one_of_cli_or_image(self):
        with self.assertRaises(ValueError):
            kicad.KiCad()
        with self.assertRaises(ValueError):
            kicad.KiCad(cli="x", image="y")

    def test_local_env_gets_utf8_only_without_a_locale(self):
        if os.name == "nt":
            self.skipTest("not on Windows")
        self.assertEqual(kicad.local_env({})["LC_ALL"], "C.UTF-8")
        self.assertEqual(kicad.local_env({"LANG": "C"})["LC_ALL"], "C.UTF-8")
        self.assertNotIn("LC_ALL", kicad.local_env({"LANG": "es_ES.UTF-8"}))


class ChecksTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = os.path.join(self.tmp.name, "repo")
        self.cli = os.path.join(self.tmp.name, "fake-kicad-cli")
        with open(self.cli, "w") as f:
            f.write(FAKE_CLI)
        os.chmod(self.cli, os.stat(self.cli).st_mode | stat.S_IEXEC)
        if os.name == "nt":
            script, self.cli = self.cli, self.cli + ".cmd"
            with open(self.cli, "w") as f:
                f.write(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\nexit /b %errorlevel%\r\n')
        self.put("hw/board.kicad_pro")
        self.put("hw/board.kicad_sch")
        self.put("hw/board.kicad_pcb", OUTLINE)
        self.old = os.getcwd()
        os.chdir(self.repo)
        self.addCleanup(os.chdir, self.old)
        self.addCleanup(os.environ.pop, "FAKE_FAIL", None)

    def put(self, path, text=""):
        full = os.path.join(self.repo, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    @staticmethod
    def read(path):
        with open(path, encoding="utf-8") as f:
            return f.read()

    def load(self, path):
        return json.loads(self.read(path))

    def run_checks(self, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = kicad_hub.main(["checks", "--kicad-cli", self.cli, *extra])
        return code, out.getvalue(), err.getvalue()

    def test_erc_and_drc_reports_and_summary(self):
        code, out, _ = self.run_checks()
        self.assertEqual(code, 0)
        for name in ("erc.json", "erc.rpt", "drc.json", "drc.rpt"):
            self.assertTrue(os.path.isfile(f".kicad-hub/reports/{name}"), name)
        self.assertFalse(os.path.exists(".kicad-hub/reports/drc-fab.json"))
        self.assertIn("Found hw/board.kicad_pro", out)
        self.assertIn("ERC: 1 errors, 2 warnings", out)
        self.assertIn("DRC: 1 errors, 0 warnings, 2 unconnected", out)
        args = self.load(".kicad-hub/reports/drc.json")["args"]
        self.assertEqual(args[:4], ["pcb", "drc", "--severity-all", "--schematic-parity"])
        self.assertEqual(args[-1], "hw/board.kicad_pcb")

    def test_board_without_outline_skips_drc(self):
        self.put("hw/board.kicad_pcb", "(kicad_pcb)\n")
        code, out, _ = self.run_checks("--fab-profile", "jlcpcb")
        self.assertEqual(code, 0)
        self.assertTrue(os.path.isfile(".kicad-hub/reports/erc.json"))
        self.assertFalse(os.path.exists(".kicad-hub/reports/drc.json"))
        self.assertFalse(os.path.exists(".kicad-hub/reports/drc-fab.json"))
        self.assertIn("DRC: skipped", out)

    def test_fab_drc_appends_the_fab_rules_to_a_copy(self):
        self.put("hw/board.kicad_dru", '(version 1)\n(rule "mine")\n')
        self.put("hw/.git/config")
        code, out, _ = self.run_checks("--project-dir", "hw", "--out", "hw/fab", "--fab-profile", "jlcpcb")
        self.assertEqual(code, 0)
        fab = self.load("hw/fab/reports/drc-fab.json")
        self.assertIn('(rule "mine")', fab["dru"])
        self.assertIn("JLC", fab["dru"])                       # the fab's rules, appended
        self.assertEqual(fab["dru"].count("(version"), 1)      # with a single version line
        self.assertNotIn(".git", fab["files"])                 # the copy leaves these out
        self.assertNotIn("fab", fab["files"])
        self.assertEqual(self.read("hw/board.kicad_dru"), '(version 1)\n(rule "mine")\n')  # project untouched
        self.assertTrue(os.path.isfile("hw/fab/reports/drc-fab.rpt"))
        self.assertIn("DRC with jlcpcb rules: 1 errors", out)

    def test_a_check_not_run_now_shows_no_old_numbers(self):
        self.assertEqual(self.run_checks("--fab-profile", "jlcpcb")[0], 0)
        code, out, _ = self.run_checks()                       # same folder, no fab profile this time
        self.assertEqual(code, 0)
        self.assertNotIn("rules:", out)
        self.assertFalse(os.path.exists(".kicad-hub/reports/drc-fab.json"))
        self.assertFalse(os.path.exists(".kicad-hub/reports/drc-fab.rpt"))
        self.put("hw/board.kicad_pcb", "(kicad_pcb)\n")        # and now the board loses its outline
        code, out, _ = self.run_checks()
        self.assertIn("DRC: skipped", out)
        self.assertFalse(os.path.exists(".kicad-hub/reports/drc.json"))

    def test_fab_drc_without_project_rules(self):
        code, _, _ = self.run_checks("--fab-profile", "pcbway")
        self.assertEqual(code, 0)
        self.assertTrue(self.load(".kicad-hub/reports/drc-fab.json")["dru"].startswith("(version 1)\n"))

    def test_unknown_fab_profile_stops_before_any_check(self):
        code, _, err = self.run_checks("--fab-profile", "acme")
        self.assertEqual(code, 1)
        self.assertIn("::error::Unknown fab-profile 'acme'", err)
        self.assertIn("jlcpcb", err)
        self.assertFalse(os.path.exists(".kicad-hub/reports/erc.json"))

    def test_kicad_cli_that_fails_is_an_error(self):
        os.environ["FAKE_FAIL"] = "1"
        code, _, err = self.run_checks()
        self.assertEqual(code, 1)
        self.assertIn("did not write .kicad-hub/reports/erc.json", err)

    def test_old_reports_do_not_hide_a_failure(self):
        self.assertEqual(self.run_checks()[0], 0)
        os.environ["FAKE_FAIL"] = "1"
        self.assertEqual(self.run_checks()[0], 1)

    def test_no_project(self):
        os.remove("hw/board.kicad_pro")
        code, _, err = self.run_checks()
        self.assertEqual(code, 1)
        self.assertIn("No .kicad_pro found", err)

    def test_kicad_not_found(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = kicad_hub.main(["checks", "--kicad-cli", "/nope/kicad-cli"])
        self.assertEqual(code, 1)
        self.assertIn("--kicad-cli points to /nope/kicad-cli", err.getvalue())


if __name__ == "__main__":
    unittest.main()
