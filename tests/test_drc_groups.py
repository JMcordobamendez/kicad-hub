"""Tests of scripts/drc_groups.py and of the report's fab DRC table, in English and Spanish.

The Spanish descriptions follow KiCad 10's own translation (translation/pofiles/es.po:
"rule '%s'" -> "regla '%s'", "(%s min %s; actual %s)" -> "(%s mínimo %s; real %s)",
"Annular width" -> "Ancho de anular", "Clearance violation" -> "Infracción de margen",
"netclass '%s'" -> "clase de nodo '%s'"), with a decimal comma as on a Spanish PC.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..", "scripts")
sys.path.insert(0, SCRIPTS)
import drc_groups  # noqa: E402

JLC = os.path.join(HERE, "..", "fab-profiles", "jlcpcb", "rules.kicad_dru")

ENGLISH = [
    ("annular_width", "Annular width (rule 'JLC min annular ring' min 0.1800 mm; actual 0.1500 mm)"),
    ("annular_width", "Annular width (rule 'JLC min annular ring' min 0.1800 mm; actual 0.1000 mm)"),
    ("clearance", "Clearance violation (rule 'JLC min clearance' clearance 0.1000 mm; actual 0.0900 mm)"),
    ("clearance", "Clearance violation (netclass 'Default' clearance 0.2000 mm; actual 0.1500 mm)"),
]
SPANISH = [
    ("annular_width", "Ancho de anular (regla 'JLC min annular ring' mínimo 0,1800 mm; real 0,1500 mm)"),
    ("annular_width", "Ancho de anular (regla 'JLC min annular ring' mínimo 0,1800 mm; real 0,1000 mm)"),
    ("clearance", "Infracción de margen (regla 'JLC min clearance' margen 0,1000 mm; real 0,0900 mm)"),
    ("clearance", "Infracción de margen (clase de nodo 'Default' margen 0,2000 mm; real 0,1500 mm)"),
]


def violations(rows):
    return [{"type": t, "description": d, "severity": "error",
             "items": [{"description": f"item {i}", "pos": {"x": i, "y": 0}}]} for i, (t, d) in enumerate(rows)]


def jlc_names():
    with open(JLC, encoding="utf-8") as f:
        return drc_groups.rule_names(f.read())


class RuleNamesTest(unittest.TestCase):
    def test_quoted_unquoted_and_comments(self):
        text = '(version 1)\n# (rule "commented out")\n(rule "A b (c)"\n  (constraint x))\n(rule plain\n)\n' \
               '(rule "with \\" quote")'
        self.assertEqual(drc_groups.rule_names(text), ["A b (c)", "plain", 'with " quote'])

    def test_every_shipped_profile_has_names(self):
        root = os.path.join(HERE, "..", "fab-profiles")
        for profile in sorted(os.listdir(root)):
            with open(os.path.join(root, profile, "rules.kicad_dru"), encoding="utf-8") as f:
                text = f.read()
            self.assertEqual(len(drc_groups.rule_names(text)), text.count("(rule "), profile)


class GroupTest(unittest.TestCase):
    def test_english(self):
        self.assertEqual(drc_groups.group(violations(ENGLISH), jlc_names()), [
            ("Annular width: JLC min annular ring", 2),
            ("Clearance violation", 1),
            ("Clearance violation: JLC min clearance", 1),
        ])

    def test_spanish_groups_the_same(self):
        self.assertEqual(drc_groups.group(violations(SPANISH), jlc_names()), [
            ("Ancho de anular: JLC min annular ring", 2),
            ("Infracción de margen", 1),
            ("Infracción de margen: JLC min clearance", 1),
        ])

    def test_longest_rule_name_wins(self):
        names = ["JLC min drill", "JLC min drill (via and plated hole)"]
        text = "Drilled hole too small (rule 'JLC min drill (via and plated hole)' min 0.1500 mm; actual 0.1 mm)"
        self.assertEqual(drc_groups.rule_of(text, names), "JLC min drill (via and plated hole)")

    def test_name_must_be_quoted(self):
        self.assertIsNone(drc_groups.rule_of("Clearance violation (netclass 'Default' …)", ["Clearance"]))

    def test_other_quotes(self):
        self.assertEqual(drc_groups.rule_of("Largeur (règle «JLC min track width» min 0,1 mm)",
                                            ["JLC min track width"]), "JLC min track width")

    def test_without_rule_names_or_details(self):
        self.assertEqual(drc_groups.group([{"type": "silk_overlap", "description": "Silkscreen overlap"},
                                           {"type": "x", "description": ""}]),
                         [("Silkscreen overlap", 1), ("x", 1)])


class ReportTableTest(unittest.TestCase):
    """report.py end to end: the fab DRC table reads the same in both languages."""

    def report(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            def put(name, data):
                path = os.path.join(tmp, name)
                with open(path, "w", encoding="utf-8") as f:
                    json.dump(data, f)
                return path

            bom = os.path.join(tmp, "bom.csv")
            with open(bom, "w", encoding="utf-8") as f:
                f.write("Reference,Value\n")
            drc = put("drc.json", {"violations": [], "unconnected_items": []})
            fab = put("drc-fab.json", {"violations": violations(rows), "unconnected_items": []})
            out, counts = os.path.join(tmp, "report.md"), os.path.join(tmp, "counts.json")
            subprocess.run([sys.executable, os.path.join(SCRIPTS, "report.py"), "--name", "b", "--bom", bom,
                            "--drc", drc, "--drc-fab", fab, "--fab-profile", "jlcpcb",
                            "--out", out, "--out-json", counts], check=True)
            with open(out, encoding="utf-8") as f:
                md = f.read()
            with open(counts, encoding="utf-8") as f:
                return md, json.load(f)

    def test_english_and_spanish(self):
        for rows, annular, margin in ((ENGLISH, "Annular width", "Clearance violation"),
                                      (SPANISH, "Ancho de anular", "Infracción de margen")):
            md, counts = self.report(rows)
            self.assertIn(f"| {annular}: JLC min annular ring | 2 |", md)
            self.assertIn(f"| {margin}: JLC min clearance | 1 |", md)
            self.assertIn(f"| {margin} | 1 |", md)
            self.assertEqual((counts["drc_fab_violations"], counts["drc_fab_new"]), (4, 4))


if __name__ == "__main__":
    unittest.main()
