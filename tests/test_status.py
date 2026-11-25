"""Tests of scripts/status.py (the plain-words status for the desktop app) and of
`kicad_hub.py status` with a fake kicad-cli."""
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts"))
import status  # noqa: E402
from test_engine import EngineCase, BomTest  # noqa: E402

# A board with three footprints: TP2 and U1 (with their names) and J2 (a pad), a GND track,
# a GND via and the edge.
BOARD = """(kicad_pcb
  (net 0 "") (net 1 "GND")
  (footprint "TestPoint" (layer "F.Cu") (uuid "fp-tp2") (at 10 10)
    (property "Reference" "TP2" (at 0 -1) (layer "F.SilkS") (uuid "name-tp2"))
    (fp_circle (center 0 0) (end 1 0) (layer "F.SilkS") (uuid "silk-tp2")))
  (footprint "QFN" (layer "F.Cu") (uuid "fp-u1") (at 20 10)
    (property "Reference" "U1" (at 0 -1) (layer "F.SilkS") (uuid "name-u1"))
    (property "Value" "nRF54L15" (at 0 1) (layer "F.Fab") (uuid "value-u1")))
  (footprint "Header" (layer "F.Cu") (uuid "fp-j2") (at 30 10)
    (property "Reference" "J2" (at 0 -1) (layer "F.SilkS") (uuid "name-j2"))
    (pad "1" thru_hole circle (at 0 0) (size 1.7 1.7) (drill 1) (net 1 "GND") (uuid "pad-j2-1")))
  (segment (start 0 0) (end 5 0) (width 0.1) (layer "F.Cu") (net 1) (uuid "track-1"))
  (via (at 5 0) (size 0.35) (drill 0.2) (layers "F.Cu" "B.Cu") (net 1) (uuid "via-1"))
  (gr_line (start 0 0) (end 40 0) (layer "Edge.Cuts") (uuid "edge-1"))
)
"""


def violation(kind, description, *items, severity="warning"):
    return {"type": kind, "severity": severity, "description": description,
            "items": [{"description": d, "uuid": u, "pos": {"x": 0, "y": 0}} for d, u in items]}


class Project(unittest.TestCase):
    """A project folder with a board, and reports written by the test."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = self.tmp.name
        self.reports = os.path.join(self.dir, "reports")
        os.makedirs(self.reports)
        with open(os.path.join(self.dir, "b.kicad_pcb"), "w", encoding="utf-8") as f:
            f.write(BOARD)
        self.bom("Reference,Value,Footprint,Datasheet,DNP,MPN,LCSC\n"
                 "J2,Conn,H,https://x/j2.pdf,,PH1,\nTP2,TP,T,,,TP1,\nU1,nRF54L15,QFN,https://x/u1.pdf,,NRF54,\n")
        self.erc([])
        self.drc([])

    def write(self, name, data):
        with open(os.path.join(self.reports, name), "w", encoding="utf-8") as f:
            f.write(data if isinstance(data, str) else json.dumps(data))

    def bom(self, text, name="bom-parts.csv"):
        self.write(name, text)

    def erc(self, violations, path="/"):
        self.write("erc.json", {"sheets": [{"path": path, "violations": violations}]})

    def drc(self, violations, unconnected=(), parity=()):
        self.write("drc.json", {"violations": violations, "unconnected_items": list(unconnected),
                                "schematic_parity": list(parity)})

    def build(self, **kw):
        return status.build(self.dir, "b", self.reports, **kw)

    def titles(self, result):
        return [(r["ref"], r["title"], r["detail"]) for r in result["items"]]


class SentencesTest(Project):
    def test_all_clear(self):
        s = self.build()
        self.assertEqual(s["headline"], "All clear. Ready to order.")
        self.assertEqual([i["state"] for i in s["indicators"]], ["ok"] * 5)
        self.assertEqual(s["items"], [])

    def test_silkscreen_in_english_and_in_spanish(self):
        for text in ("Silkscreen overlap (board setup constraints silk clearance 0.1500 mm; actual 0.0000 mm)",
                     "Solapamiento de serigrafía (restricciones de la placa holgura de serigrafía 0,1500 mm; "
                     "real 0,0000 mm)"):
            with self.subTest(text=text):
                self.drc([violation("silk_overlap", text, ("Circle on F.Silkscreen", "silk-tp2"),
                                    ("Reference field of U1", "name-u1"))])
                row = self.build()["items"][0]
                self.assertEqual(row["title"], "The silkscreen of TP2 sits on top of the name of U1")
                self.assertEqual(row["detail"], "They touch. They need 0.15 mm.")
                self.assertEqual(row["ref"], "TP2 · U1")
                self.assertEqual(row["select"], ["silk-tp2", "name-u1"])
                self.assertEqual(row["footprints"], ["fp-tp2", "fp-u1"])
                self.assertFalse(row["blocking"])

    def test_two_names_stuck_together(self):
        self.drc([violation("silk_overlap", "Silkscreen overlap (silk clearance 0.15 mm; actual 0.002 mm)",
                            ("Reference field of U1", "name-u1"), ("Reference field of TP2", "name-tp2"))])
        row = self.build()["items"][0]
        self.assertEqual(row["title"], "The names of U1 and TP2 are stuck together")
        self.assertEqual(row["detail"], "They are 0.002 mm apart. They need 0.15 mm.")
        self.assertEqual(row["ref"], "TP2 · U1")

    def test_numbers_with_a_rule_name_that_has_a_length(self):
        # the last two lengths are what is needed and what was found, even after "JLC 0.2mm"
        self.drc([violation("clearance", "Clearance violation (rule 'JLC 0.2mm' clearance 0.2000 mm; actual "
                                         "0.1500 mm)", ("Track [GND] on F.Cu", "track-1"),
                            ("PTH pad 1 [GND] of J2", "pad-j2-1"), severity="error")])
        row = self.build()["items"][0]
        self.assertEqual(row["title"], "A GND track is too close to pad 1 of J2")
        self.assertEqual(row["detail"], "They are 0.15 mm apart. They need 0.2 mm.")
        self.assertTrue(row["blocking"])

    def test_errors_first_and_the_headline(self):
        self.drc([violation("silk_overlap", "x", ("a", "name-u1"), ("b", "name-tp2")),
                  violation("drill_out_of_range", "Hole size out of range (min hole 0.2000 mm; actual 0.1000 mm)",
                            ("Via [GND] on F.Cu - B.Cu", "via-1"), severity="error")])
        s = self.build()
        self.assertEqual(s["headline"], "One error to fix before you order. Plus one small thing.")
        self.assertEqual(s["items"][0]["title"], "The hole of a GND via is too small")
        self.assertEqual(s["items"][0]["detail"], "The hole is 0.1 mm. The rules ask for at least 0.2 mm.")
        board = s["indicators"][1]
        self.assertEqual((board["state"], board["count"], board["note"]), ("error", 2, "1 error, 1 to tidy"))

    def test_many_of_a_kind_make_one_row(self):
        self.drc([violation("drill_out_of_range", f"Hole size out of range (min hole 0.2000 mm; actual {a} mm)",
                            ("Via [GND] on F.Cu - B.Cu", "via-1"), severity="error") for a in ("1e-05",) * 7])
        rows = self.build()["items"]
        self.assertEqual(len(rows), 1)
        self.assertEqual((rows[0]["ref"], rows[0]["title"], rows[0]["count"]), ("7 vias", "7 holes are too small", 7))
        self.assertEqual(rows[0]["detail"], "The smallest is 0.00001 mm. The rules ask for at least 0.2 mm.")

    def test_unknown_kind_keeps_kicads_title(self):
        self.drc([violation("brand_new_check", "Something new (rule 'x' 1 mm; actual 2 mm)", ("Footprint J2", "fp-j2"))])
        row = self.build()["items"][0]
        self.assertEqual((row["title"], row["ref"]), ("Something new", "J2"))

    def test_parts_named_in_kicads_text_when_not_on_the_board(self):
        self.erc([violation("power_pin_not_driven", "Entrada de alimentación no alimentada",
                            ("Símbolo U1 Patilla 21 [VBUS, Power input, Line]", "sym-u1")),
                  violation("power_pin_not_driven", "x", ("Symbol #PWR06 Pin 1 [Power input, Line]", "pwr"))],
                 path="/Power/")
        rows = self.build()["items"]
        self.assertEqual(rows[0]["title"], "A power input of U1 has no power source")
        self.assertTrue(rows[0]["detail"].startswith("On the Power sheet. "))
        self.assertEqual(rows[0]["footprints"], ["fp-u1"])  # to show it on the board
        self.assertEqual(rows[1]["title"], "A power net has no source")

    def test_board_against_schematic(self):
        self.drc([], parity=[violation("missing_footprint", "Missing footprint R5", ("Symbol R5 [10k]", "s"),
                                       severity="error")])
        self.bom("Reference,Value,Footprint,Datasheet,DNP,MPN,LCSC\nR5,10k,R,,,RC0603,\n")
        s = self.build()
        self.assertEqual(s["items"][0]["title"], "R5 is in the schematic but not on the board")
        self.assertEqual(s["indicators"][2]["note"], "1 difference with the schematic")

    def test_unconnected(self):
        self.drc([], unconnected=[violation("unconnected_items", "Missing connection",
                                            ("Track [GND] on F.Cu", "track-1"), ("PTH pad 1 [GND] of J2", "pad-j2-1"))])
        row = self.build()["items"][0]
        self.assertEqual(row["title"], "A GND track and pad 1 of J2 still need a connection")
        self.assertTrue(row["blocking"])


class PartsTest(Project):
    def test_part_numbers_voltages_and_datasheets(self):
        self.bom("Reference,Value,Footprint,Datasheet,DNP,MPN,LCSC,Voltage\n"
                 "C1,100nF,C,,,CL10,,\nC2,10uF 25V,C,,,CL20,,\nD3,LED,LED,,,,C2286,\n"
                 "R1,10k,R,,,,,\nU1,TBD,QFN,,DNP,,,\n")
        s = self.build()
        self.assertEqual(self.titles(s), [
            ("R1", "R1 (10k) has no part number", "Add an MPN or an LCSC code, so it can be priced and ordered."),
            ("C1", "The capacitor C1 has no voltage rating",
             "Put the voltage in the value (100nF 50V) or in a Voltage field."),
            ("D3", "D3 (LED) has no datasheet", "The Datasheet field is empty in the schematic.")])
        notes = {i["id"]: i["note"] for i in s["indicators"]}
        self.assertEqual(notes["parts"], "4 fitted · 2 to tidy")
        self.assertEqual(notes["datasheets"], "D3 is missing one")

    def test_a_datasheet_found_in_the_folder_counts(self):
        self.bom("Reference,Value,Footprint,Datasheet,DNP,MPN,LCSC\nD3,LED,LED,,,L1,\n")
        found = {"D3": {"source": "folder", "status": "ok"}}
        self.assertEqual(self.build(datasheets=found)["items"], [])

    def test_a_link_found_broken_last_time(self):
        s = self.build(links={"https://x/u1.pdf": {"status": "broken", "why": "HTTP 404"}})
        self.assertEqual(self.titles(s), [("U1", "The datasheet of U1 can't be opened", "HTTP 404.")])

    def test_bom_against_main(self):
        self.bom("Reference,Value,Footprint,Datasheet,DNP,MPN,LCSC\n"
                 "J2,Conn,H,https://x/j2.pdf,,PH1,\nU1,nRF54L10,QFN,https://x/u1.pdf,,NRF54,\n", "bom-parts-base.csv")
        s = self.build(base_ref="main")
        self.assertEqual(s["bom"], {"fitted": 3, "added": ["TP2"], "removed": [], "changed": ["U1"]})
        self.assertEqual(s["indicators"][3]["note"], "3; 1 added, 1 changed since main")
        self.bom(open(os.path.join(self.reports, "bom-parts.csv"), encoding="utf-8").read(), "bom-parts-base.csv")
        self.assertEqual(self.build(base_ref="main")["indicators"][3]["note"], "3, same as main")


class WordsTest(unittest.TestCase):
    def test_lengths(self):
        self.assertEqual(status.mm(0.15), "0.15")
        self.assertEqual(status.mm(0.002), "0.002")
        self.assertEqual(status.mm(1e-05), "0.00001")
        self.assertEqual(status.mm(0.127), "0.127")
        self.assertEqual(status.mm(0.2032), "0.203")
        self.assertEqual(status.lengths("mínimo 0,1800 mm; real 0,1500 mm"), (0.18, 0.15))
        self.assertIsNone(status.lengths("Courtyards overlap"))

    def test_headline(self):
        self.assertEqual(status.headline(0, 6), "No errors. Six small things left to tidy up.")
        self.assertEqual(status.headline(2, 0), "Two errors to fix before you order.")
        self.assertEqual(status.headline(0, 0, has_board=False),
                         "The schematic is clean. The board has no outline yet.")
        self.assertEqual(status.headline(0, 15), "No errors. 15 small things left to tidy up.")


class StatusCommandTest(EngineCase):
    """kicad_hub.py status with the fake kicad-cli."""

    git = BomTest.git

    def setUp(self):
        super().setUp()
        self.put("hw/board.kicad_sch", "R1 10k\nU1 nRF54\n")
        self.put("hw/board.kicad_pcb", BOARD)

    def report(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f)
        return path

    def status(self, *extra):
        return self.engine("status", *extra)

    def test_writes_status_json(self):
        os.environ["FAKE_DRC"] = self.report("drc.json", {"violations": [violation(
            "silk_overlap", "x (0.15 mm; 0 mm)", ("a", "silk-tp2"), ("b", "name-u1"))], "unconnected_items": []})
        os.environ["FAKE_ERC"] = self.report("erc.json", {"sheets": [{"path": "/", "violations": []}]})
        code, out, err = self.status("--base", "none")
        self.assertEqual(code, 0, err)
        s = self.load(".kicad-hub/status.json")
        self.assertEqual(s["state"], "done")
        self.assertEqual(s["project"], "board")
        self.assertIn("The silkscreen of TP2 sits on top of the name of U1", [r["title"] for r in s["items"]])
        self.assertIn("R1 (10k) has no part number", [r["title"] for r in s["items"]])
        self.assertIsInstance(s["seconds"], float)
        self.assertRegex(s["checked_at"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d([+-]\d\d:\d\d|Z)$")  # for JavaScript's Date
        self.assertIn("No errors.", out)
        self.assertTrue(os.path.isfile(".kicad-hub/quick/erc.json"))      # apart from `run`'s reports
        self.assertFalse(os.path.exists(".kicad-hub/reports"))
        self.assertFalse(os.path.exists(".kicad-hub/datasheets"))          # the quick check copies nothing

    def test_compares_with_main_and_caches_its_bom(self):
        self.git("-c", "init.defaultBranch=main", "init", "-q")
        self.put(".gitignore", ".kicad-hub/\n")
        self.git("add", "-A")
        self.git("commit", "-q", "-m", "first")
        self.put("hw/board.kicad_sch", "R1 10k\nU1 nRF54\nC1 100nF\n")
        self.assertEqual(self.status()[0], 0)
        s = self.load(".kicad-hub/status.json")
        self.assertEqual((s["base"], s["bom"]["added"], s["branch"], s["changed"]), ("main", ["C1"], "main", True))
        cached = os.listdir(".kicad-hub/cache")
        self.assertEqual(len(cached), 1)
        # the next save: no checkout of main, the same answer
        self.assertEqual(self.status()[0], 0)
        self.assertFalse(os.path.exists(".kicad-hub-base"))
        self.assertEqual(self.load(".kicad-hub/status.json")["bom"]["added"], ["C1"])
        self.assertEqual(os.listdir(".kicad-hub/cache"), cached)

    def test_a_failure_is_written_too(self):
        os.environ["FAKE_FAIL"] = "1"
        code, _, err = self.status("--base", "none")
        self.assertEqual(code, 1)
        s = self.load(".kicad-hub/status.json")
        self.assertEqual((s["state"], s["headline"]), ("failed", "The check could not run."))
        self.assertIn("did not write", s["detail"])


class LinkCacheTest(unittest.TestCase):
    """datasheets.py --cache: links found working are not checked again for a week."""

    def run_datasheets(self, folder, cache, check="true"):
        script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts", "datasheets.py")
        out = os.path.join(folder, "ds.json")
        subprocess.run([sys.executable, script, "--bom", os.path.join(folder, "bom.csv"), "--project-dir", folder,
                        "--out-dir", folder, "--json", out, "--check-urls", check, "--cache", cache], check=True)
        with open(out, encoding="utf-8") as f:
            return json.load(f)

    def test_working_links_are_reused(self):
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "bom.csv"), "w", encoding="utf-8") as f:
                f.write("Reference,Value,Datasheet,DNP,MPN\nU1,X,https://example.invalid/u1.pdf,,X1\n")
            cache = os.path.join(d, "cache", "links.json")
            os.makedirs(os.path.dirname(cache))
            with open(cache, "w", encoding="utf-8") as f:  # checked yesterday: no network needed now
                json.dump({"https://example.invalid/u1.pdf": {"status": "ok", "why": "200",
                                                               "checked": time.time() - 86400}}, f)
            self.assertEqual(self.run_datasheets(d, cache)["U1"]["status"], "ok")
            with open(cache, "w", encoding="utf-8") as f:  # too old, and checking is off: not checked
                json.dump({"https://example.invalid/u1.pdf": {"status": "ok", "checked": time.time() - 30 * 86400}}, f)
            self.assertEqual(self.run_datasheets(d, cache, "false")["U1"]["status"], "unverified")


if __name__ == "__main__":
    unittest.main()
