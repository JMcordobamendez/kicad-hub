"""Tests of scripts/kicad.py and scripts/kicad_hub.py with a fake kicad-cli (no KiCad needed)."""
import contextlib
import glob
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
out, target = a[a.index("-o") + 1], a[-1]
if a[:3] == ["sch", "export", "bom"]:
    # the "schematic" lists its parts, one per line: REF VALUE [only in these variants, split by ;]
    variant = a[a.index("--variant") + 1] if "--variant" in a else None
    if variant == os.environ.get("FAKE_FAIL_VARIANT", "-"):
        sys.exit(3)
    labels = a[a.index("--labels") + 1].split(",")
    grouped = labels[0] == "References"      # the assembly BOM: References,Qty,Value,...
    rows = [",".join(labels)]
    for line in open(target).read().split("\n"):
        p = line.split()
        if len(p) >= 2 and (len(p) == 2 or variant is None or variant in " ".join(p[2:]).split(";")):
            rows.append(",".join(([p[0], "1", p[1]] if grouped else [p[0], p[1]])
                                 + [""] * (len(labels) - (3 if grouped else 2))))
    open(out, "w").write("\n".join(rows) + "\n")
    if not grouped:
        json.dump(a, open(out + ".args", "w"))
    sys.exit(0)
if a[:2] == ["pcb", "export"]:
    kind = a[2]
    if kind == os.environ.get("FAKE_FAIL_EXPORT", "-"):
        sys.exit(3)
    if kind == "gerbers":
        open(os.path.join(out, "board-F_Cu.gbr"), "w").write("G04 fake*\n")
        open(os.path.join(out, "board-Edge_Cuts.gbr"), "w").write("G04 fake*\n")
    elif kind == "drill":
        open(os.path.join(out, "board-PTH.drl"), "w").write("M48\n")
    elif kind == "pos":
        open(out, "w").write("Ref,Val,Package,PosX,PosY,Rot,Side\nR1,10k,R,1,1,0,top\nC1,100nF,C,2,2,0,top\n"
                             "U5,nRF,QFN,3,3,90,bottom\n")
    else:
        open(out, "w").write(kind + " of " + target + "\n")
    sys.exit(0)
fmt = a[a.index("--format") + 1]
if fmt == "report":
    open(out, "w").write("report of " + target + "\n")
elif os.environ.get("FAKE_ERC") and a[:2] == ["sch", "erc"]:
    open(out, "w").write(open(os.environ["FAKE_ERC"]).read())
elif os.environ.get("FAKE_DRC") and a[:2] == ["pcb", "drc"]:
    open(out, "w").write(open(os.environ["FAKE_DRC"]).read())
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


class EngineCase(unittest.TestCase):
    """A board repository in a temporary folder (the current one) and the fake kicad-cli."""

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
        for name in ("FAKE_FAIL", "FAKE_FAIL_VARIANT", "FAKE_FAIL_EXPORT", "FAKE_ERC", "FAKE_DRC", "GITHUB_OUTPUT"):
            self.addCleanup(os.environ.pop, name, None)

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

    def engine(self, command, *extra):
        return self.engine_plain(command, "--kicad-cli", self.cli, *extra)

    def engine_plain(self, command, *extra):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = kicad_hub.main([command, *extra])
        return code, out.getvalue(), err.getvalue()


class ChecksTest(EngineCase):
    def run_checks(self, *extra):
        return self.engine("checks", *extra)

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



VARIANTS = json.dumps({"schematic": {"variants": [{"name": "lite", "description": "No debugger"},
                                                  {"name": "pro x", "description": ""}]}})
# REF VALUE [variants that fit it; none = all]
SCH = "R1 10k\nC1 100nF\nU5 nRF pro x\nFLG1 PWR_FLAG\n#PWR01 GND\n"


class BomTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.put("hw/board.kicad_pro", VARIANTS)
        self.put("hw/board.kicad_sch", SCH)

    def bom(self, *extra):
        return self.engine("bom", *extra)

    def refs(self, path):
        return [line.split(",")[0] for line in self.read(path).splitlines()[1:]]

    def git(self, *args):
        import subprocess
        subprocess.run(["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "init.defaultBranch=main",
                        "-c", "commit.gpgsign=false", *args], check=True, capture_output=True)

    def test_labels_from_required_fields(self):
        self.assertEqual(kicad_hub.bom_labels("Footprint, Datasheet,MPN|LCSC | Mouser,Tolerance"),
                         kicad_hub.BOM_LABELS + ["Mouser", "Tolerance"])
        self.assertEqual(kicad_hub.bom_labels(""), kicad_hub.BOM_LABELS)

    def test_bom_and_every_variant(self):
        code, out, _ = self.bom("--required-fields", "Footprint,MPN|Mouser")
        self.assertEqual(code, 0)
        r = ".kicad-hub/reports"
        self.assertEqual(self.refs(f"{r}/bom-parts.csv"), ["R1", "C1", "U5"])          # no PWR_FLAG, no #PWR
        self.assertEqual(self.refs(f"{r}/bom-parts-variant-lite.csv"), ["R1", "C1"])
        self.assertEqual(self.refs(f"{r}/bom-parts-variant-pro_x.csv"), ["R1", "C1", "U5"])
        self.assertEqual(self.load(f"{r}/variants.json"), [
            {"name": "lite", "description": "No debugger", "bom": f"{r}/bom-parts-variant-lite.csv", "dir": "lite"},
            {"name": "pro x", "description": "", "bom": f"{r}/bom-parts-variant-pro_x.csv", "dir": "pro_x"}])
        self.assertTrue(self.read(f"{r}/bom-parts.csv").startswith(
            "Reference,Value,Footprint,Datasheet,DNP,MPN,Manufacturer,LCSC,Voltage,Mouser\n"))
        args = self.load(f"{r}/bom-parts-variant-pro_x.csv.args")
        self.assertEqual(args[:5], ["sch", "export", "bom", "--variant", "pro x"])
        self.assertEqual(args[args.index("--fields") + 1],
                         "Reference,Value,Footprint,Datasheet,${DNP},MPN,Manufacturer,LCSC,Voltage,Mouser")
        self.assertEqual(args[args.index("--group-by") + 1], "")
        self.assertEqual(args[args.index("--ref-range-delimiter") + 1], "")
        self.assertIn("BOM: 3 fitted parts\n", out)
        self.assertIn("Variant lite: 2 fitted parts\n", out)

    def test_summary_counts_fitted_parts(self):
        # kicad-cli writes not-fitted parts too, with DNP set: the summary must not count them
        r = ".kicad-hub/reports"
        self.put(f"{r}/x.csv", "Reference,Value,DNP\nR1,1k,\nTP1,TP,DNP\nTP2,TP,DNP\nC1,1u,~\n")
        self.assertEqual(kicad_hub.fitted(f"{r}/x.csv"), (2, 4))
        self.assertEqual(kicad_hub.counted(f"{r}/x.csv"), "2 fitted parts (2 more not fitted)")

    def test_variants_none_or_a_list(self):
        self.assertEqual(self.bom("--variants", "none")[0], 0)
        self.assertEqual(self.load(".kicad-hub/reports/variants.json"), [])
        self.assertFalse(os.path.exists(".kicad-hub/reports/bom-parts-variant-lite.csv"))
        self.assertEqual(self.bom("--variants", " lite , other")[0], 0)
        self.assertEqual([(v["name"], v["description"]) for v in self.load(".kicad-hub/reports/variants.json")],
                         [("lite", "No debugger"), ("other", "")])

    def test_project_without_variants(self):
        self.put("hw/board.kicad_pro", "{}")
        self.assertEqual(self.bom()[0], 0)
        self.assertEqual(self.load(".kicad-hub/reports/variants.json"), [])
        self.put("hw/board.kicad_pro", "")                       # not even JSON
        self.assertEqual(self.bom()[0], 0)

    def test_failing_export_is_an_error(self):
        os.environ["FAKE_FAIL_VARIANT"] = "lite"
        code, _, err = self.bom()
        self.assertEqual(code, 1)
        self.assertIn("did not write the BOM of variant lite", err)
        os.environ["FAKE_FAIL"] = "1"
        code, _, err = self.bom()
        self.assertEqual(code, 1)
        self.assertIn("did not write the BOM of hw/board.kicad_sch", err)
        self.assertFalse(os.path.exists(".kicad-hub/reports/bom-parts.csv"))    # nothing old left behind

    def test_base_commit(self):
        self.git("init", "-q")
        self.put(".gitignore", ".kicad-hub/\n")
        self.put("hw/board.kicad_pro", json.dumps({"schematic": {"variants": [{"name": "lite"}]}}))
        self.put("hw/board.kicad_sch", "R1 10k\nC9 1uF lite\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "base")
        self.put("hw/board.kicad_pro", VARIANTS)                 # working tree: one more variant, other parts
        self.put("hw/board.kicad_sch", SCH)
        code, out, _ = self.bom("--base", "HEAD")
        self.assertEqual(code, 0)
        r = ".kicad-hub/reports"
        self.assertEqual(self.refs(f"{r}/bom-parts.csv"), ["R1", "C1", "U5"])           # not committed yet
        self.assertEqual(self.refs(f"{r}/bom-parts-base.csv"), ["R1", "C9"])
        self.assertEqual(self.refs(f"{r}/bom-parts-variant-lite-base.csv"), ["R1", "C9"])
        variants = {v["name"]: v for v in self.load(f"{r}/variants.json")}
        self.assertEqual(variants["lite"]["base_bom"], f"{r}/bom-parts-variant-lite-base.csv")
        self.assertNotIn("base_bom", variants["pro x"])           # that variant did not exist then
        self.assertFalse(os.path.exists(kicad_hub.BASE_DIR))      # the worktree is gone
        self.assertIn("HEAD: 2 fitted parts (to compare with)", out)

    def test_base_where_the_board_did_not_exist(self):
        self.git("init", "-q")
        self.put("README.md", "x")
        self.git("add", "README.md")
        self.git("commit", "-q", "-m", "empty")
        self.assertEqual(self.bom("--base", "HEAD")[0], 0)
        base = self.read(".kicad-hub/reports/bom-parts-base.csv")
        self.assertEqual(base, "Reference,Value,Footprint,Datasheet,DNP,MPN,Manufacturer,LCSC,Voltage\n")

    def test_base_that_cannot_be_checked_out_is_a_warning(self):
        self.git("init", "-q")
        code, _, err = self.bom("--base", "no-such-branch")
        self.assertEqual(code, 0)
        self.assertIn("::warning::Could not check out the base", err)
        self.assertFalse(os.path.exists(".kicad-hub/reports/bom-parts-base.csv"))
        self.assertTrue(os.path.isfile(".kicad-hub/reports/bom-parts.csv"))

    def test_base_fetched_from_origin(self):
        origin = os.path.join(self.tmp.name, "origin")
        os.makedirs(os.path.join(origin, "hw"))
        self.git("-C", origin, "init", "-q")
        with open(os.path.join(origin, "hw", "board.kicad_sch"), "w") as f:
            f.write("R7 1k\n")
        self.git("-C", origin, "add", "-A")
        self.git("-C", origin, "commit", "-q", "-m", "base")
        self.git("init", "-q")
        self.git("remote", "add", "origin", origin)
        code, _, _ = self.bom("--base", "main", "--fetch")
        self.assertEqual(code, 0)
        self.assertEqual(self.refs(".kicad-hub/reports/bom-parts-base.csv"), ["R7"])



BOM_CSV = ("Reference,Value,Footprint,Datasheet,DNP,MPN,Manufacturer,LCSC,Voltage\n"
           "R1,RC0603,R_0603,,,,,,\n"
           "U1,CN3722,SOP,,,CN3722,,,\n"
           "U2,LDO,SOT,docs/ldo.pdf,,,,,\n"
           "C1,1uF,C_0603,https://example.invalid/c.pdf,,,,,\n"
           "C2,1uF,C_0603,,DNP,,,,\n")


class DatasheetsAndSourcingTest(EngineCase):
    """The real datasheets.py and sourcing.py, on a BOM that needs no network."""

    def setUp(self):
        super().setUp()
        self.put(".kicad-hub/reports/bom-parts.csv", BOM_CSV)
        self.put(".kicad-hub/reports/variants.json", "[]")
        self.put("hw/datasheets/CN3722.pdf", "pdf")
        self.put("hw/docs/ldo.pdf", "pdf")

    def test_datasheets(self):
        code, out, err = self.engine_plain("datasheets", "--check-links", "false")
        self.assertEqual(code, 0, err)
        found = self.load(".kicad-hub/reports/datasheets.json")
        self.assertEqual({ref: e["status"] for ref, e in found.items()},
                         {"R1": "missing", "U1": "ok", "U2": "ok", "C1": "unverified"})   # C2 is not fitted
        self.assertTrue(os.path.isfile(".kicad-hub/datasheets/CN3722.pdf"))                # bundled
        self.assertTrue(os.path.isfile(".kicad-hub/datasheets/index.html"))
        self.assertIn("Datasheets: 3 found, 0 broken, 1 missing; 1 link not checked", out)

    def test_datasheets_extra_folder(self):
        self.put("vendor/pdf/RC0603-series.pdf", "pdf")
        self.assertEqual(self.engine_plain("datasheets", "--check-links", "false", "--dirs", "vendor/pdf")[0], 0)
        self.assertEqual(self.load(".kicad-hub/reports/datasheets.json")["R1"]["status"], "ok")

    def test_needs_the_bom_first(self):
        os.remove(".kicad-hub/reports/bom-parts.csv")
        for command in ("datasheets", "sourcing"):
            code, _, err = self.engine_plain(command)
            self.assertEqual(code, 1)
            self.assertIn("bom-parts.csv is missing: run `bom` first", err)

    def test_sourcing_writes_its_files(self):
        self.put(".kicad-hub/reports/bom-parts.csv", BOM_CSV.replace("U1,CN3722,SOP,,,CN3722", "U1,CN3722,SOP,,,"))
        code, out, err = self.engine_plain("sourcing", "--boards", "1,10", "--currency", "EUR")
        self.assertEqual(code, 0, err)
        data = self.load(".kicad-hub/reports/sourcing.json")
        self.assertEqual(data["builds"][0]["fitted"], 4)
        self.assertEqual(data["builds"][0]["priced"], 0)           # no MPN or LCSC code: nothing to look up
        self.assertTrue(os.path.isfile(".kicad-hub/reports/sourcing.csv"))
        self.assertTrue(os.path.isfile(".kicad-hub/sourcing.html"))
        self.assertIn("Suppliers: .kicad-hub/sourcing.html", out)

    def test_sourcing_failure_is_a_warning_and_leaves_nothing_old(self):
        self.put(".kicad-hub/reports/sourcing.json", "{}")
        real = kicad_hub.run_script
        kicad_hub.run_script = lambda *a, **k: 1
        self.addCleanup(setattr, kicad_hub, "run_script", real)
        code, out, err = self.engine_plain("sourcing")
        self.assertEqual(code, 0)
        self.assertIn("::warning::Cost and stock lookup failed: skipped", err)
        self.assertNotIn("Suppliers:", out)
        self.assertFalse(os.path.exists(".kicad-hub/reports/sourcing.json"))

    def test_choices_file_is_relative_to_the_project(self):
        seen = {}
        real = kicad_hub.run_script
        kicad_hub.run_script = lambda name, *a, **k: seen.update(args=a, env=k.get("env")) or 0
        self.addCleanup(setattr, kicad_hub, "run_script", real)
        self.assertEqual(self.engine_plain("sourcing", "--choices", "picks.csv")[0], 0)
        self.assertEqual(seen["args"][seen["args"].index("--choices") + 1], "hw/picks.csv")
        self.assertEqual(seen["args"][seen["args"].index("--variants") + 1], ".kicad-hub/reports/variants.json")


class SupplierKeysTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "keys.env")

    def write(self, text):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write(text)

    def test_keys_from_the_personal_file(self):
        self.write("# my keys\nMOUSER_API_KEY = abc\nFARNELL_STORE=\"es.farnell.com\"\nPATH=/evil\nnonsense\n")
        env = kicad_hub.supplier_env({"PATH": "/bin"}, self.path)
        self.assertEqual(env["MOUSER_API_KEY"], "abc")
        self.assertEqual(env["FARNELL_STORE"], "es.farnell.com")
        self.assertEqual(env["PATH"], "/bin")                       # only supplier keys are taken

    def test_the_environment_wins(self):
        self.write("MOUSER_API_KEY=from-file\nDIGIKEY_CLIENT_ID=id\n")
        env = kicad_hub.supplier_env({"MOUSER_API_KEY": "from-env", "DIGIKEY_CLIENT_ID": " "}, self.path)
        self.assertEqual(env["MOUSER_API_KEY"], "from-env")
        self.assertEqual(env["DIGIKEY_CLIENT_ID"], "id")            # blank in the environment = not set

    def test_no_file(self):
        self.assertEqual(kicad_hub.supplier_env({"A": "1"}, os.path.join(self.tmp.name, "none")), {"A": "1"})

    def test_where_the_file_lives(self):
        if os.name == "nt":
            self.assertEqual(kicad_hub.keys_file({"APPDATA": "C:\\Users\\x\\AppData\\Roaming"}),
                             "C:\\Users\\x\\AppData\\Roaming\\kicad-hub\\keys.env")
        else:
            self.assertEqual(kicad_hub.keys_file({"XDG_CONFIG_HOME": "/cfg"}), "/cfg/kicad-hub/keys.env")
            self.assertTrue(kicad_hub.keys_file({}).endswith("/.config/kicad-hub/keys.env"))



# Stands in for fabnotes.py on the tiny fake boards of these tests: same arguments, same
# outputs. The real one is tested on the example board in tests/test_board_file.py.
FAKE_FABNOTES = r'''import sys
pcb, rev, readme, board_json, profile = sys.argv[1:6]
open(readme, "w").write("FAB NOTES rev " + rev + " profile " + profile + "\n")
open(board_json, "w").write("{}")
print("Via drill 0.1 mm is below the project minimum drill 0.2 mm")
'''


class PackageTest(EngineCase):
    def setUp(self):
        super().setUp()
        self.put("hw/board.kicad_pro", VARIANTS)
        self.put("hw/board.kicad_sch", SCH)
        self.put("hw/board.kicad_pcb", '(kicad_pcb (title_block (rev "B/1"))\n' + OUTLINE)
        notes = os.path.join(self.tmp.name, "fake_fabnotes.py")
        with open(notes, "w") as f:
            f.write(FAKE_FABNOTES)
        real = kicad_hub.FABNOTES
        kicad_hub.FABNOTES = notes
        self.addCleanup(setattr, kicad_hub, "FABNOTES", real)
        self.assertEqual(self.engine("bom", "--variants", "lite")[0], 0)

    def package(self, *extra):
        return self.engine("package", *extra)

    def files(self, folder):
        return sorted(os.path.relpath(os.path.join(d, f), folder).replace(os.sep, "/")
                      for d, _, fs in os.walk(folder) for f in fs)

    def test_package_contents(self):
        os.environ["GITHUB_OUTPUT"] = os.path.join(self.tmp.name, "outputs.txt")
        code, out, err = self.package("--fab-profile", "jlcpcb", "--extra-formats", "odb, ipc2581, pdf")
        self.assertEqual(code, 0, err)
        pkg = ".kicad-hub/package/board-B_1"                       # the board's revision, made a safe name
        self.assertEqual(self.files(pkg), [
            "README.txt", "SHA256SUMS.txt",
            "assembly/board-B_1-bom.csv", "assembly/board-B_1-cpl.csv",
            "assembly/jlcpcb/board-B_1-bom-jlcpcb.csv", "assembly/jlcpcb/board-B_1-cpl-jlcpcb.csv",
            "assembly/lite/board-B_1-lite-bom.csv", "assembly/lite/board-B_1-lite-cpl.csv",
            "assembly/lite/jlcpcb/board-B_1-lite-bom-jlcpcb.csv", "assembly/lite/jlcpcb/board-B_1-lite-cpl-jlcpcb.csv",
            "board-B_1-ipc2581.xml", "board-B_1-odb.zip", "board-B_1.d356",
            "drill/board-PTH.drl", "gerber/board-Edge_Cuts.gbr", "gerber/board-F_Cu.gbr"])
        cpl = lambda path: [line.split(",")[0] for line in self.read(path).splitlines()[1:]]
        self.assertEqual(cpl(f"{pkg}/assembly/board-B_1-cpl.csv"), ["R1", "C1", "U5"])
        self.assertEqual(cpl(f"{pkg}/assembly/lite/board-B_1-lite-cpl.csv"), ["R1", "C1"])   # U5 not fitted in lite
        readme = self.read(f"{pkg}/README.txt")
        self.assertTrue(readme.startswith("FAB NOTES rev B_1 profile jlcpcb\n"))
        self.assertIn("VARIANTS (same bare board, different assembly)\n  default: assembly/\n"
                      "  lite: assembly/lite/  No debugger\n", readme)
        self.assertIn("::warning::Via drill 0.1 mm is below", err)
        self.assertIn("::warning::Unknown extra format 'pdf'", err)
        self.assertIn("Fab package: .kicad-hub/package/board-B_1-fab.zip", out)
        self.assertIn("Check before ordering: Via drill 0.1 mm", out)
        self.assertEqual(self.read(os.environ["GITHUB_OUTPUT"]),
                         "rev=B_1\nboard_rev=B/1\nzip=.kicad-hub/package/board-B_1-fab.zip\nname=board-B_1-fab.zip\n")
        self.assertFalse(os.path.exists(".kicad-hub/.fabnotes.py"))

    def test_checksums_and_zip(self):
        import hashlib
        import zipfile
        self.assertEqual(self.package()[0], 0)
        pkg = ".kicad-hub/package/board-B_1"
        sums = self.read(f"{pkg}/SHA256SUMS.txt").splitlines()
        listed = [line.split("  ", 1)[1] for line in sums]
        self.assertEqual(listed, sorted(listed))
        self.assertEqual(sorted(p[2:] for p in listed), [f for f in self.files(pkg) if f != "SHA256SUMS.txt"])
        for line in sums:
            digest, rel = line.split("  ", 1)
            with open(os.path.join(pkg, rel[2:]), "rb") as f:
                self.assertEqual(hashlib.sha256(f.read()).hexdigest(), digest, rel)
        with zipfile.ZipFile(".kicad-hub/package/board-B_1-fab.zip") as z:
            self.assertEqual(sorted(z.namelist()), sorted("board-B_1/" + f for f in self.files(pkg)))

    def test_revision_from_the_tag_or_the_commit(self):
        self.assertEqual(self.package("--rev", "v1.0")[0], 0)
        self.assertTrue(os.path.isfile(".kicad-hub/package/board-v1.0-fab.zip"))
        self.assertFalse(os.path.exists(".kicad-hub/package/board-B_1"))        # the other revision is gone
        self.put("hw/board.kicad_pcb", OUTLINE)                                   # no revision in the board
        self.assertEqual(self.package()[0], 0)
        self.assertEqual(sorted(os.listdir(".kicad-hub/package")), ["board-local", "board-local-fab.zip"])
        self.assertTrue(os.path.isfile(".kicad-hub/package/board-local-fab.zip"))  # not a git repository here

    def test_a_failing_export_stops_the_package(self):
        os.environ["FAKE_FAIL_EXPORT"] = "drill"
        code, _, err = self.package()
        self.assertEqual(code, 1)
        self.assertIn("kicad-cli pcb export drill failed", err)
        self.assertEqual(glob.glob(".kicad-hub/package/*.zip"), [])

    def test_fab_notes_that_hang_are_stopped(self):
        # a KiCad message window waiting for OK left the step hanging on Windows (2026-11-18)
        hanging = os.path.join(self.tmp.name, "hanging_fabnotes.py")
        with open(hanging, "w") as f:
            f.write("import time\ntime.sleep(60)\n")
        kicad_hub.FABNOTES = hanging
        real = kicad_hub.FABNOTES_TIMEOUT
        kicad_hub.FABNOTES_TIMEOUT = 2
        self.addCleanup(setattr, kicad_hub, "FABNOTES_TIMEOUT", real)
        code, _, err = self.package()
        self.assertEqual(code, 1)
        self.assertIn("took more than 2 s and was stopped", err)
        self.assertEqual(glob.glob(".kicad-hub/package/*.zip"), [])

    def test_board_without_outline_has_no_package(self):
        self.put("hw/board.kicad_pcb", "(kicad_pcb)\n")
        code, out, _ = self.package()
        self.assertEqual(code, 0)
        self.assertIn("no fab package", out)
        self.assertFalse(os.path.exists(".kicad-hub/package"))

    def test_needs_the_bom_and_a_known_profile(self):
        code, _, err = self.package("--fab-profile", "acme")
        self.assertEqual(code, 1)
        self.assertIn("Unknown fab-profile 'acme'", err)
        os.remove(".kicad-hub/reports/bom-parts.csv")
        code, _, err = self.package()
        self.assertEqual(code, 1)
        self.assertIn("run `bom` first", err)


class PythonCommandTest(unittest.TestCase):
    def test_in_the_image(self):
        d = kicad.KiCad(image="img:1").python_command(["out/.notes.py", "b.kicad_pcb"], workdir="/work")
        self.assertEqual(d[-4:], ["img:1", "python3", "out/.notes.py", "b.kicad_pcb"])

    def test_not_on_a_pc(self):
        with self.assertRaises(ValueError):
            kicad.KiCad(cli="/opt/kicad-cli").python_command(["x.py"])


REQUIREMENTS = """# what this board must meet
- id: R1
  text: "Fewer than 10 parts"
  check: max_parts
  max: 10
- id: R2
  text: Two parts at most   # fails: the BOM has four fitted parts
  check: max_parts
  max: 2
- id: R3
  text: Every IC has a footprint
  check: required_fields
  refs: 'U\\d+'
  fields: [Footprint]
"""

class RequirementsTest(EngineCase):
    """The real requirements.py, with PyYAML when this Python has it and without."""

    def setUp(self):
        super().setUp()
        self.put(".kicad-hub/reports/bom-parts.csv", BOM_CSV)
        self.put(".kicad-hub/reports/variants.json", "[]")
        self.put("hw/kicad-hub-requirements.yaml", REQUIREMENTS)

    def without_pyyaml(self):
        """Make `import yaml` fail in the scripts the engine starts, as in KiCad's own Python."""
        blocker = os.path.join(self.tmp.name, "no-pyyaml")
        os.makedirs(blocker)
        with open(os.path.join(blocker, "yaml.py"), "w") as f:
            f.write('raise ImportError("no PyYAML here")\n')
        old = os.environ.get("PYTHONPATH")
        os.environ["PYTHONPATH"] = blocker + (os.pathsep + old if old else "")
        self.addCleanup(lambda: os.environ.pop("PYTHONPATH") if old is None
                        else os.environ.__setitem__("PYTHONPATH", old))

    def check(self):
        code, out, err = self.engine_plain("requirements")
        self.assertEqual(code, 0, err)
        got = {r["id"]: r["status"] for r in self.load(".kicad-hub/reports/requirements.json")}
        self.assertEqual(got, {"R1": "pass", "R2": "fail", "R3": "pass"})

    def test_requirements(self):
        self.check()

    def test_requirements_without_pyyaml(self):
        self.without_pyyaml()
        self.check()

    def test_yaml_the_small_reader_refuses_is_a_failed_entry(self):
        self.without_pyyaml()
        self.put("hw/kicad-hub-requirements.yaml", "- id: R1\n  text: |\n    long text\n  check: max_parts\n")
        self.assertEqual(self.engine_plain("requirements")[0], 0)
        (only,) = self.load(".kicad-hub/reports/requirements.json")
        self.assertEqual(only["status"], "fail")
        self.assertIn("line 2", only["detail"])

    def test_no_file_means_nothing_checked(self):
        self.put(".kicad-hub/reports/requirements.json", "[]")     # from an earlier run
        code, out, _ = self.engine_plain("requirements", "--file", "other.yaml")
        self.assertEqual(code, 0)
        self.assertIn("No requirements file (hw/other.yaml): skipped", out)
        self.assertFalse(os.path.exists(".kicad-hub/reports/requirements.json"))
        self.assertEqual(self.engine_plain("requirements", "--file", "")[0], 0)

    def test_a_check_that_cannot_run_is_a_warning(self):
        real = kicad_hub.run_script
        kicad_hub.run_script = lambda *a, **k: 1
        self.addCleanup(setattr, kicad_hub, "run_script", real)
        code, _, err = self.engine_plain("requirements")
        self.assertEqual(code, 0)
        self.assertIn("::warning::Requirements check failed to run: skipped", err)

    def test_in_ci_it_runs_inside_the_image(self):
        calls = []

        class Image:
            image = "img:1"

            def python(self, *args):
                calls.append(args)
                with open(args[3], "w") as f:
                    f.write("[]")
                return 0

        self.assertEqual(kicad_hub.requirements("hw", ".kicad-hub", k=Image()), [])
        self.assertEqual(calls, [(".kicad-hub/.requirements.py", "hw/kicad-hub-requirements.yaml",
                                  ".kicad-hub/reports", ".kicad-hub/reports/requirements.json")])
        self.assertFalse(os.path.exists(".kicad-hub/.requirements.py"))



class ReportTest(EngineCase):
    """The real report.py, on the files the other steps leave in <out>/reports."""

    def setUp(self):
        super().setUp()
        self.put(".kicad-hub/reports/bom-parts.csv", BOM_CSV)
        self.put(".kicad-hub/reports/variants.json", "[]")
        self.put(".kicad-hub/reports/erc.json", json.dumps(
            {"sheets": [{"violations": [{"severity": "error"}, {"severity": "warning"}]}]}))
        self.put(".kicad-hub/reports/drc.json", json.dumps(
            {"violations": [{"severity": "error"}, {"severity": "error"}], "unconnected_items": []}))
        self.put(".kicad-hub/reports/drc-fab.json", json.dumps({"violations": [
            {"type": "annular_width", "severity": "error", "items": [],
             "description": "Ancho de anular (regla 'JLC min annular ring' mínimo 0,1800 mm; real 0,1500 mm)"}],
            "unconnected_items": []}))
        for name in ("GITHUB_OUTPUT", "GITHUB_STEP_SUMMARY"):
            self.addCleanup(os.environ.pop, name, None)

    def outputs(self, path):
        return dict(line.split("=", 1) for line in self.read(path).splitlines())

    def test_report_outputs_and_job_summary(self):
        os.environ["GITHUB_OUTPUT"] = os.path.join(self.tmp.name, "out.txt")
        os.environ["GITHUB_STEP_SUMMARY"] = os.path.join(self.tmp.name, "summary.md")
        code, out, err = self.engine_plain("report", "--fab-profile", "jlcpcb", "--base-ref", "main")
        self.assertEqual(code, 0, err)
        md = self.read(".kicad-hub/reports/report.md")
        self.assertIn("## KiCad checks: board", md)
        self.assertIn("| Ancho de anular: JLC min annular ring | 1 |", md)   # the rule names of the profile
        self.assertIn("### BOM changes", md)
        self.assertEqual(self.read(os.environ["GITHUB_STEP_SUMMARY"]), md)
        self.assertEqual(self.outputs(os.environ["GITHUB_OUTPUT"]),
                         {"erc_errors": "1", "drc_errors": "2", "missing_fields": "4", "requirements_failed": "-"})
        self.assertEqual(self.load(".kicad-hub/reports/counts.json")["drc_fab_new"], 1)
        self.assertIn("ERC errors: 1 · DRC errors: 2 · parts missing fields: 4 · requirements failed: -", out)
        self.assertIn("Report: .kicad-hub/reports/report.md", out)

    def test_files_not_written_are_left_out(self):
        for name in ("erc.json", "drc.json", "drc-fab.json", "variants.json"):
            os.remove(f".kicad-hub/reports/{name}")
        os.environ["GITHUB_OUTPUT"] = os.path.join(self.tmp.name, "out.txt")
        code, out, err = self.engine_plain("report")
        self.assertEqual(code, 0, err)
        self.assertIn("| ERC | — | — | — |", self.read(".kicad-hub/reports/report.md"))
        self.assertEqual(self.outputs(os.environ["GITHUB_OUTPUT"])["erc_errors"], "-")

    def test_the_inputs_given_to_report_py(self):
        seen = {}
        real = kicad_hub.run_script
        kicad_hub.run_script = lambda name, *a, **k: seen.update(name=name, args=a) or real(name, *a, **k)
        self.addCleanup(setattr, kicad_hub, "run_script", real)
        self.put(".kicad-hub/reports/bom-parts-base.csv", BOM_CSV)
        self.assertEqual(self.engine_plain("report", "--fab-profile", "jlcpcb")[0], 0)
        args = list(seen["args"])
        given = {args[i]: args[i + 1] for i in range(0, len(args) - 1) if args[i].startswith("--")}
        self.assertEqual(seen["name"], "report.py")
        self.assertEqual(given["--base-bom"], ".kicad-hub/reports/bom-parts-base.csv")
        self.assertEqual(os.path.normpath(given["--fab-rules"]),
                         os.path.normpath(os.path.join(SCRIPTS, "..", "fab-profiles", "jlcpcb", "rules.kicad_dru")))
        for missing in ("--datasheets", "--sourcing", "--requirements", "--visual", "--package"):
            self.assertNotIn(missing, given)

    def test_fab_package_found_or_none(self):
        self.put(".kicad-hub/package/board-A-fab.zip", "zip")
        self.put(".kicad-hub/reports/fab-warnings.txt", "Via drill too small\n")
        self.assertEqual(self.engine_plain("report")[0], 0)
        md = self.read(".kicad-hub/reports/report.md")
        self.assertIn("`board-A-fab.zip` in the artifact", md)
        self.assertIn("- ⚠️ Via drill too small", md)
        self.assertEqual(self.engine_plain("report", "--package", "")[0], 0)      # CI: no package this run
        self.assertNotIn("### Fab package", self.read(".kicad-hub/reports/report.md"))

    def test_needs_the_bom_and_a_known_profile(self):
        code, _, err = self.engine_plain("report", "--fab-profile", "nofab")
        self.assertEqual(code, 1)
        self.assertIn("Unknown fab-profile 'nofab'", err)
        os.remove(".kicad-hub/reports/bom-parts.csv")
        code, _, err = self.engine_plain("report")
        self.assertEqual(code, 1)
        self.assertIn("bom-parts.csv is missing: run `bom` first", err)

    def test_a_failing_report_is_an_error_and_leaves_nothing_old(self):
        self.put(".kicad-hub/reports/report.md", "old")
        real = kicad_hub.run_script
        kicad_hub.run_script = lambda *a, **k: 1
        self.addCleanup(setattr, kicad_hub, "run_script", real)
        code, _, err = self.engine_plain("report")
        self.assertEqual(code, 1)
        self.assertIn("report.py failed", err)
        self.assertFalse(os.path.exists(".kicad-hub/reports/report.md"))


class PortalTest(EngineCase):
    """The real portal.py: the local site on a PC, one page in CI."""

    def setUp(self):
        super().setUp()
        self.put("hw/board.kicad_sch", "(kicad_sch)\n")
        self.put("hw/board.kicad_pcb", OUTLINE.replace("(kicad_pcb", '(kicad_pcb\n  (title_block (rev "B"))'))
        self.put(".kicad-hub/reports/bom-parts.csv", BOM_CSV)
        self.put(".kicad-hub/reports/report.md", "<!-- kicad-hub:board -->\n## KiCad checks: board\n")
        self.put(".kicad-hub/reports/counts.json", json.dumps({"erc_errors": 0, "drc_errors": 1}))

    git = BomTest.git

    def commit(self, message="first"):
        self.git("init", "-q")
        self.put(".gitignore", ".kicad-hub/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", message)

    def test_repo_and_folder_names(self):
        for url in ("https://github.com/Owner/Board.git", "git@github.com:Owner/Board.git",
                    "ssh://git@github.com/Owner/Board", "https://user@github.com/Owner/Board/"):
            self.assertEqual(kicad_hub.github_repo(url), ("https://github.com", "Owner/Board"), url)
        self.assertEqual(kicad_hub.github_repo(""), ("", ""))
        self.assertEqual(kicad_hub.site_folder("feat/new board"), "feat_new_board")
        self.assertEqual(kicad_hub.site_folder("pr-3"), "branch-pr-3")
        self.assertEqual(kicad_hub.site_folder("releases"), "branch-releases")

    def test_local_site_of_the_working_tree(self):
        self.commit("-first line")
        self.git("remote", "add", "origin", "git@github.com:Owner/Board.git")
        code, out, err = self.engine_plain("portal")
        self.assertEqual(code, 0, err)
        page = ".kicad-hub/portal/main"
        build = self.load(f"{page}/build.json")
        self.assertEqual((build["label"], build["kind"], build["branch"], build["rev"], build["name"]),
                         ("main", "branch", "main", "B", "board"))
        self.assertEqual(build["commit"]["message"], "-first line")
        self.assertEqual(build["counts"], {"erc_errors": 0, "drc_errors": 1})
        self.assertTrue(os.path.isfile(f"{page}/design/board.kicad_sch"))
        self.assertTrue(os.path.isfile(f"{page}/bom-parts.csv"))
        html = self.read(f"{page}/index.html")
        self.assertIn('"repo":"Owner/Board"', html)
        self.assertIn("## KiCad checks: board", html)                    # the report, without its marker
        self.assertNotIn("kicad-hub:board", html)
        self.assertTrue(os.path.isfile(".kicad-hub/portal/index.html"))  # the site around it
        self.assertTrue(os.path.isfile(".kicad-hub/portal/assets/kicanvas.js"))
        self.assertEqual([b["path"] for b in self.load(".kicad-hub/portal/builds.json")], ["main/"])
        self.assertIn(f"Portal page: {page}/index.html", out)
        self.assertIn("python -m http.server --directory .kicad-hub/portal 8000", out)
        self.assertIn("http://localhost:8000/main/", out)

    def test_local_changes_other_branch_and_no_git(self):
        code, _, err = self.engine_plain("portal")                       # not a repository
        self.assertEqual(code, 0, err)
        build = self.load(".kicad-hub/portal/local/build.json")
        self.assertEqual((build["label"], build["sha"]), ("local", "0" * 40))
        self.assertIn('"repo":"local/board"', self.read(".kicad-hub/portal/local/index.html"))
        self.commit()
        self.git("checkout", "-q", "-b", "feat/rev-c")
        self.put("hw/board.kicad_sch", "(kicad_sch changed)\n")
        self.assertEqual(self.engine_plain("portal")[0], 0)
        self.assertEqual(self.load(".kicad-hub/portal/feat_rev-c/build.json")["label"], "feat/rev-c + local changes")
        self.assertEqual(sorted(b["path"] for b in self.load(".kicad-hub/portal/builds.json")),
                         ["feat_rev-c/", "local/"])                      # earlier local builds stay listed

    def test_ci_page_only_with_what_the_action_passes(self):
        self.commit()
        code, _, err = self.engine_plain("portal", "--site", "site", "--label", "PR #7", "--kind", "pr", "--sha", "abc",
                                         "--repo", "o/r", "--rev", "", "--title", "Fix", "--base-ref", "main",
                                         "--light", "--commit-message=")
        self.assertEqual(code, 0, err)
        build = self.load("site/build.json")
        self.assertEqual((build["label"], build["kind"], build["sha"], build["rev"], build["title"]),
                         ("PR #7", "pr", "abc", "", "Fix"))
        self.assertNotIn("commit", build)                                 # nothing taken from git in CI
        self.assertTrue(build["light"])
        self.assertFalse(os.path.exists(".kicad-hub/portal"))             # no local site

    def test_build_options_only_with_site(self):
        code, _, err = self.engine_plain("portal", "--label", "x")
        self.assertEqual(code, 1)
        self.assertIn("--label only with --site", err)

    def test_a_failing_portal_is_an_error(self):
        real = kicad_hub.run_script
        kicad_hub.run_script = lambda *a, **k: 1
        self.addCleanup(setattr, kicad_hub, "run_script", real)
        code, _, err = self.engine_plain("portal")
        self.assertEqual(code, 1)
        self.assertIn("portal.py build failed", err)


class RunTest(EngineCase):
    """kicad_hub.py run: every step in a row, as on a PC (fake kicad-cli and fabnotes.py)."""

    git = BomTest.git

    def setUp(self):
        super().setUp()
        self.put("hw/board.kicad_pro", VARIANTS)
        self.put("hw/board.kicad_sch", "R1 10k\n")
        self.put("hw/board.kicad_pcb", '(kicad_pcb (title_block (rev "B"))\n' + OUTLINE)
        notes = os.path.join(self.tmp.name, "fake_fabnotes.py")
        with open(notes, "w") as f:
            f.write(FAKE_FABNOTES)
        real = kicad_hub.FABNOTES
        kicad_hub.FABNOTES = notes
        self.addCleanup(setattr, kicad_hub, "FABNOTES", real)

    def commit(self, branch="main"):
        self.git("-c", f"init.defaultBranch={branch}", "init", "-q")
        self.put(".gitignore", ".kicad-hub/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")

    def run_all(self, *extra):
        return self.engine("run", "--check-links", "false", *extra)

    def test_every_step_against_main(self):
        self.commit()
        self.put("hw/board.kicad_sch", SCH)                               # edited since the commit
        code, out, err = self.run_all("--skip", "sourcing", "--fab-profile", "jlcpcb")
        self.assertEqual(code, 0, err)
        r = ".kicad-hub/reports"
        for path in ("erc.json", "drc.json", "drc-fab.json", "bom-parts.csv", "bom-parts-base.csv",
                     "variants.json", "datasheets.json", "report.md", "counts.json"):
            self.assertTrue(os.path.isfile(f"{r}/{path}"), path)
        self.assertFalse(os.path.exists(f"{r}/sourcing.json"))            # skipped
        self.assertEqual(len(glob.glob(".kicad-hub/package/board-B-fab.zip")), 1)
        md = self.read(f"{r}/report.md")
        self.assertIn("### BOM changes vs `main`", md)
        self.assertIn("`board-B-fab.zip` in the artifact", md)
        self.assertIn("**DRC vs jlcpcb rules", md)
        self.assertEqual(self.load(".kicad-hub/portal/main/build.json")["label"],
                         "main + local changes")
        for title in ("== ERC / DRC", "== BOM (and main, to compare with)", "== Datasheets", "== Fab package",
                      "== Requirements", "== Report", "== Portal"):
            self.assertIn(title, out)
        self.assertNotIn("== Cost and stock", out)
        self.assertIn("BOM: 3 fitted parts, lite 2, pro x 3; main: 1", out)
        self.assertIn("Report: .kicad-hub/reports/report.md", out)
        self.assertIn("Portal: python scripts/kicad_hub.py serve (or python -m http.server --directory "
                      ".kicad-hub/portal 8000), then http://localhost:8000/main/", out)
        done = self.load(".kicad-hub/run.json")          # what the KiCad plugin reads when it ends
        self.assertEqual((done["name"], done["page"], done["report"]), ("board", "main/", ".kicad-hub/reports/report.md"))
        self.assertIn("BOM: 3 fitted parts, lite 2, pro x 3; main: 1", done["summary"])

    def test_no_base_and_most_steps_skipped(self):
        self.commit()
        code, out, err = self.run_all("--base", "none", "--skip", "datasheets,sourcing,package,requirements,portal")
        self.assertEqual(code, 0, err)
        self.assertFalse(os.path.exists(".kicad-hub/reports/bom-parts-base.csv"))
        self.assertFalse(os.path.exists(".kicad-hub/package"))
        self.assertFalse(os.path.exists(".kicad-hub/portal"))
        self.assertIn("### BOM changes\n", self.read(".kicad-hub/reports/report.md"))
        self.assertNotIn("Portal:", out)

    def test_default_base(self):
        self.assertEqual(kicad_hub.default_base(), "")                    # not a repository
        self.commit("master")
        self.assertEqual(kicad_hub.default_base(), "master")
        self.git("branch", "main")
        self.assertEqual(kicad_hub.default_base(), "main")

    def test_board_without_outline_has_no_package(self):
        self.put("hw/board.kicad_pcb", "(kicad_pcb)\n")
        code, out, err = self.run_all("--base", "none", "--skip", "sourcing,portal")
        self.assertEqual(code, 0, err)
        self.assertNotIn("== Fab package", out)
        self.assertIn("DRC: skipped (no board outline yet)", out)

    def test_wrong_options_stop_before_any_check(self):
        for extra, message in ((("--skip", "bom"), "--skip: unknown step bom"),
                               (("--fab-profile", "nofab"), "Unknown fab-profile 'nofab'")):
            code, _, err = self.run_all(*extra)
            self.assertEqual(code, 1)
            self.assertIn(message, err)
            self.assertFalse(os.path.exists(".kicad-hub/reports/erc.json"))


class ServeTest(unittest.TestCase):
    """kicad_hub.py serve: the local web server for the portal."""

    def test_serves_the_portal_and_stops_when_idle(self):
        import threading
        import time
        import urllib.request
        with tempfile.TemporaryDirectory() as tmp:
            root = os.path.join(tmp, "portal")
            os.makedirs(os.path.join(root, "main"))
            with open(os.path.join(root, "main", "index.html"), "w") as f:
                f.write("<title>page</title>")
            ready = os.path.join(tmp, "server.json")
            done = []
            out = io.StringIO()

            def run():
                with contextlib.redirect_stdout(out):
                    done.append(kicad_hub.serve(root, port=0, idle=1.5, ready_file=ready))

            t = threading.Thread(target=run, daemon=True)
            t.start()
            for _ in range(100):
                if os.path.exists(ready):
                    break
                time.sleep(0.05)
            with open(ready, encoding="utf-8") as f:
                info = json.load(f)
            self.assertEqual(info["pid"], os.getpid())
            self.assertEqual(info["root"], os.path.abspath(root))
            with urllib.request.urlopen(f"http://127.0.0.1:{info['port']}/main/") as r:
                self.assertEqual(r.status, 200)
                self.assertEqual(r.headers["Cache-Control"], "no-store")
                self.assertIn(b"<title>page</title>", r.read())
            t.join(timeout=15)
            self.assertFalse(t.is_alive(), "the server did not stop after being idle")
            self.assertEqual(done, [0])


if __name__ == "__main__":
    unittest.main()
