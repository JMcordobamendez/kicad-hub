"""Tests of scripts/detect.py: python3 -m unittest discover -s tests"""
import os
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "scripts")
sys.path.insert(0, SCRIPTS)
import detect  # noqa: E402

OUTLINE = '(kicad_pcb\n  (gr_line (start 0 0) (end 10 0) (layer "Edge.Cuts"))\n)\n'


class DetectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = self.tmp.name

    def tearDown(self):
        self.tmp.cleanup()

    def put(self, path, text=""):
        full = os.path.join(self.root, path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "w", encoding="utf-8") as f:
            f.write(text)

    def test_explicit_dir(self):
        self.put("hw/board/board.kicad_pro")
        self.put("hw/board/board.kicad_pcb", OUTLINE)
        self.put("other/other.kicad_pro")
        self.assertEqual(detect.detect("hw/board", self.root), ("hw/board", "board", True, ""))

    def test_explicit_dir_trailing_slash(self):
        self.put("hw/board.kicad_pro")
        self.assertEqual(detect.detect("hw/", self.root)[0], "hw")

    def test_explicit_dir_without_project(self):
        self.put("hw/readme.txt")
        with self.assertRaisesRegex(detect.DetectError, "No .kicad_pro found in hw"):
            detect.detect("hw", self.root)
        with self.assertRaisesRegex(detect.DetectError, "No .kicad_pro found in missing"):
            detect.detect("missing", self.root)

    def test_auto_at_the_root(self):
        self.put("board.kicad_pro")
        self.put("board.kicad_pcb", OUTLINE)
        folder, name, has_pcb, note = detect.detect("", self.root)
        self.assertEqual((folder, name, has_pcb), (".", "board", True))
        self.assertIn("board.kicad_pro", note)

    def test_auto_in_a_subfolder(self):
        self.put("hardware/my board/My Board.kicad_pro")
        self.assertEqual(detect.detect("", self.root)[:3], ("hardware/my board", "My Board", False))

    def test_auto_skips_hidden_and_excluded_folders(self):
        self.put("hw/board.kicad_pro")
        self.put(".history/old/board.kicad_pro")
        self.put(".git/x/board.kicad_pro")
        self.put("fab/copy/board.kicad_pro")
        self.assertEqual(detect.detect("", self.root, exclude=["fab"])[0], "hw")

    def test_auto_none(self):
        self.put("readme.txt")
        with self.assertRaisesRegex(detect.DetectError, "No .kicad_pro found in the repository"):
            detect.detect("", self.root)

    def test_auto_more_than_one(self):
        self.put("a/a.kicad_pro")
        self.put("b/b.kicad_pro")
        with self.assertRaisesRegex(detect.DetectError, r"a/a\.kicad_pro, b/b\.kicad_pro.*set project-dir"):
            detect.detect("", self.root)

    def test_no_outline_means_no_pcb(self):
        self.put("board.kicad_pro")
        self.put("board.kicad_pcb", "(kicad_pcb)\n")
        self.assertFalse(detect.detect("", self.root)[2])

    def test_command_line(self):
        self.put("hw/board.kicad_pro")
        self.put("hw/board.kicad_pcb", OUTLINE)
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "detect.py"), "--root", self.root],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0)
        self.assertEqual(r.stdout, "dir=hw\nname=board\nhas_pcb=true\n")
        r = subprocess.run([sys.executable, os.path.join(SCRIPTS, "detect.py"), "--root", self.root,
                            "--project-dir", "nope"], capture_output=True, text=True)
        self.assertEqual(r.returncode, 1)
        self.assertEqual(r.stdout, "")
        self.assertIn("::error::No .kicad_pro found in nope", r.stderr)


if __name__ == "__main__":
    unittest.main()
