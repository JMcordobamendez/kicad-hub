"""Tests of scripts/board_file.py and of fabnotes.py, which reads the board with it.

The expected numbers for the example board are the ones pcbnew gave (KiCad 10.0.5, the
fab package's README.txt of 2026-11-18): the reader replaces pcbnew and must agree."""
import json
import math
import os
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import board_file as bf  # noqa: E402

EXAMPLE = os.path.join(HERE, "..", "examples", "royalblue54l-feather", "RoyalBlue54L-Feather.kicad_pcb")


class ParseTest(unittest.TestCase):
    def test_lists_text_and_numbers(self):
        tree = bf.parse('(a (b 1.5 -2) (c "x \\"y\\" z" word) (d "7"))')
        self.assertEqual(tree, ["a", ["b", 1.5, -2.0], ["c", 'x "y" z', "word"], ["d", "7"]])
        self.assertIsInstance(tree[3][1], bf.Quoted)          # a quoted number stays text
        self.assertNotIsInstance(tree[2][2], bf.Quoted)

    def test_unbalanced(self):
        with self.assertRaises(ValueError):
            bf.parse("(a (b)")
        with self.assertRaises(ValueError):
            bf.parse("(a))")

    def test_not_a_board(self):
        with self.assertRaisesRegex(ValueError, "not a KiCad board"):
            bf.Board(bf.parse("(kicad_sch (version 1))"))


def board(*items):
    return bf.Board(bf.parse("(kicad_pcb " + " ".join(items) + ")"))


class OutlineTest(unittest.TestCase):
    def test_rectangle_with_stroke(self):
        b = board('(gr_rect (start 0 0) (end 50 20) (stroke (width 0.1)) (layer "Edge.Cuts"))',
                  '(gr_line (start -5 -5) (end 100 100) (stroke (width 0.1)) (layer "F.SilkS"))')
        w, h = b.outline_size()
        self.assertAlmostEqual(w, 50.1)
        self.assertAlmostEqual(h, 20.1)

    def test_arc_bulging_out(self):
        # a half circle of radius 10 from (0,0) to (20,0) through (10,-10): it reaches y = -10
        b = board('(gr_arc (start 0 0) (mid 10 -10) (end 20 0) (stroke (width 0)) (layer "Edge.Cuts"))')
        w, h = b.outline_size()
        self.assertAlmostEqual(w, 20)
        self.assertAlmostEqual(h, 10)

    def test_arc_the_other_way_round(self):
        b = board('(gr_arc (start 20 0) (mid 10 -10) (end 0 0) (stroke (width 0)) (layer "Edge.Cuts"))')
        self.assertAlmostEqual(b.outline_size()[1], 10)

    def test_circle_and_poly(self):
        b = board('(gr_circle (center 0 0) (end 5 0) (stroke (width 0)) (layer "Edge.Cuts"))',
                  '(gr_poly (pts (xy 0 0) (xy 30 0) (xy 30 2)) (stroke (width 0)) (layer "Edge.Cuts"))')
        w, h = b.outline_size()
        self.assertAlmostEqual(w, 35)
        self.assertAlmostEqual(h, 10)

    def test_footprint_outline_is_moved_and_turned(self):
        # a 10 mm line along x in the footprint, placed at (100, 50) and turned 90 degrees
        b = board('(footprint "x" (layer "F.Cu") (at 100 50 90)'
                  ' (fp_line (start 0 0) (end 10 0) (stroke (width 0)) (layer "Edge.Cuts")))')
        xs = [round(x, 6) for x, _, _ in b.outline_points()]
        ys = [round(y, 6) for _, y, _ in b.outline_points()]
        self.assertEqual(sorted(xs), [100, 100])
        self.assertEqual(sorted(ys), [40, 50])                # turned counter-clockwise, Y down

    def test_no_outline(self):
        self.assertIsNone(board('(gr_line (start 0 0) (end 1 1) (layer "F.SilkS"))').outline_size())


class ExampleBoardTest(unittest.TestCase):
    """The example board against what pcbnew measured."""

    @classmethod
    def setUpClass(cls):
        cls.b = bf.Board.load(EXAMPLE)

    def test_general(self):
        self.assertEqual(self.b.copper_layers, 8)
        self.assertEqual(self.b.thickness, 1.6)
        self.assertEqual(self.b.title_block("title"), "")

    def test_size(self):
        w, h = self.b.outline_size()
        self.assertEqual((round(w, 2), round(h, 2)), (58.52, 22.96))

    def test_tracks_vias_pads(self):
        self.assertEqual(min(w for w in self.b.track_widths() if w > 0), 0.1)
        vias = self.b.vias()
        self.assertEqual(len(vias), 183)
        self.assertEqual(min(d for d, _ in vias if d > 0), 0.00001)
        self.assertEqual(min(s for _, s in vias if s > 0), 0.35)
        pads = self.b.pads()
        self.assertEqual(sum(k == "thru_hole" for k, _ in pads), 86)
        self.assertEqual(sum(k == "np_thru_hole" for k, _ in pads), 9)
        self.assertEqual(min(d for k, d in pads if k == "thru_hole" and d), 0.2)

    def test_assembly(self):
        self.assertEqual(self.b.footprint_counts(), (51, 2, 1))


class FabNotesTest(unittest.TestCase):
    def test_example_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            readme, board_json = os.path.join(tmp, "README.txt"), os.path.join(tmp, "board.json")
            r = subprocess.run([sys.executable, os.path.join(HERE, "..", "scripts", "fabnotes.py"), EXAMPLE, "abc1234",
                                readme, board_json, "jlcpcb"], capture_output=True, text=True)
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertEqual(r.stdout, "Via drill 0.00001 mm is below the project minimum drill 0.2 mm\n")
            with open(readme, encoding="utf-8") as f:
                text = f.read()
            with open(board_json, encoding="utf-8") as f:
                data = json.load(f)
        for line in ("Board        RoyalBlue54L-Feather", "Revision     abc1234",
                     "  Size              58.52 mm x 22.96 mm", "  Copper layers     8", "  Thickness         1.6 mm",
                     "  Surface finish    ENIG", "  Castellated pads  yes",
                     "  dielectric 1   0.1000 mm  prepreg  FR4  Er 4.5",
                     "  Track width       0.1 mm", "  Via drill         0.00001 mm", "  Via diameter      0.35 mm",
                     "  Plated pad hole   0.2 mm", "  Vias / PTH / NPTH 183 / 86 / 9",
                     "  Track width       0.0762 mm", "  Clearance         0.0762 mm", "  Through drill     0.2 mm",
                     "  Hole to hole      0.25 mm", "  SMD parts         51", "  THT parts         2",
                     "  Parts on bottom   1", "FAB PROFILE: jlcpcb"):
            self.assertIn(line + "\n", text)
        self.assertEqual(data, {"size_mm": [58.52, 22.96], "copper_layers": 8, "min_track": 0.1,
                                "min_via_drill": 0.00001, "min_pad_hole": 0.2, "min_drill": 0.00001})
        self.assertTrue(math.isclose(data["min_drill"], 1e-5))


if __name__ == "__main__":
    unittest.main()
