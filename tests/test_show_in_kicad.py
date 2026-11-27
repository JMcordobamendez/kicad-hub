"""Tests of app/python/show_in_kicad.py with a stand-in for kicad-python (no KiCad)."""
import io
import json
import os
import sys
import types
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "app", "python"))
import show_in_kicad  # noqa: E402


class FakeKIID:
    def __init__(self, value=""):
        self.value = value


class Doc:
    def __init__(self, folder, board):
        self.project = types.SimpleNamespace(path=folder)
        self.board_filename = board


class FakeBoard:
    on_board = {"pad-1", "name-u1", "fp-u1", "fp-j2"}

    def __init__(self, client, doc):
        self.doc, self.selected, self.cleared = doc, [], False

    def get_items_by_id(self, ids):
        if any(k.value == "bad" for k in ids) and len(ids) > 1:
            raise RuntimeError("KiCad can't take one of these")
        return [types.SimpleNamespace(id=k) for k in ids if k.value in self.on_board]

    def clear_selection(self):
        self.cleared = True

    def add_to_selection(self, items):
        self.selected += [i.id.value for i in items]


class FakeKiCad:
    docs = []
    zoom_status = 1
    fail = False
    boards = []

    def __init__(self, client_name=None, timeout_ms=0):
        if FakeKiCad.fail:
            raise ConnectionError("Failed to connect to KiCad")
        self._client = object()
        self.actions = []

    def ping(self):
        pass

    def get_open_documents(self, kind):
        return FakeKiCad.docs

    def run_action(self, action):
        self.actions.append(action)
        return types.SimpleNamespace(status=FakeKiCad.zoom_status)


def install_fakes():
    """kipy, kipy.board and kipy.proto.common.types as the helper imports them."""
    def board(client, doc):
        b = FakeBoard(client, doc)
        FakeKiCad.boards.append(b)
        return b
    mods = {
        "kipy": types.SimpleNamespace(KiCad=FakeKiCad),
        "kipy.board": types.SimpleNamespace(Board=board),
        "kipy.proto": types.SimpleNamespace(),
        "kipy.proto.common": types.SimpleNamespace(),
        "kipy.proto.common.types": types.SimpleNamespace(DocumentType=types.SimpleNamespace(DOCTYPE_PCB=3),
                                                         KIID=FakeKIID),
    }
    saved = {k: sys.modules.get(k) for k in mods}
    sys.modules.update(mods)
    return saved


class ShowTest(unittest.TestCase):
    def setUp(self):
        saved = install_fakes()
        self.addCleanup(lambda: [sys.modules.pop(k) if v is None else sys.modules.__setitem__(k, v)
                                 for k, v in saved.items()])
        here = os.path.dirname(os.path.abspath(__file__))
        self.project = here
        FakeKiCad.docs = [Doc(here, "riego.kicad_pcb")]
        FakeKiCad.zoom_status, FakeKiCad.fail, FakeKiCad.boards = 1, False, []

    def ask(self, **request):
        request.setdefault("project_dir", self.project)
        request.setdefault("board", "riego.kicad_pcb")
        out = io.StringIO()
        code = show_in_kicad.main(io.StringIO(json.dumps(request)), out, kipy=sys.modules["kipy"])
        return code, json.loads(out.getvalue())

    def test_selects_and_zooms(self):
        code, r = self.ask(ids=["pad-1", "name-u1"], footprints=["fp-u1"])
        self.assertEqual((code, r["ok"], r["selected"], r["zoomed"]), (0, True, 2, True))
        self.assertEqual(r["message"], "Selected 2 items in KiCad.")
        self.assertEqual(FakeKiCad.boards[0].selected, ["pad-1", "name-u1"])
        self.assertTrue(FakeKiCad.boards[0].cleared)

    def test_only_selects_when_the_zoom_fails(self):
        FakeKiCad.zoom_status = 2  # RAS_INVALID: the action's name changed in this KiCad
        _, r = self.ask(ids=["pad-1"])
        self.assertEqual((r["ok"], r["zoomed"]), (True, False))
        self.assertEqual(r["message"], "Selected 1 item in KiCad (zoom in to see them).")

    def test_footprints_when_the_items_are_gone(self):
        _, r = self.ask(ids=["old-1"], footprints=["fp-j2"])
        self.assertTrue(r["ok"])
        self.assertEqual(FakeKiCad.boards[0].selected, ["fp-j2"])

    def test_one_id_kicad_cannot_take(self):
        _, r = self.ask(ids=["bad", "pad-1"])
        self.assertEqual(FakeKiCad.boards[0].selected, ["pad-1"])
        self.assertTrue(r["ok"])

    def test_nothing_left(self):
        code, r = self.ask(ids=["old-1"], footprints=["old-2"])
        self.assertEqual((code, r["ok"]), (1, False))
        self.assertIn("no longer on the board", r["message"])

    def test_kicad_closed_or_api_off(self):
        FakeKiCad.fail = True
        _, r = self.ask(ids=["pad-1"])
        self.assertIn("Enable KiCad API", r["message"])

    def test_no_board_open(self):
        FakeKiCad.docs = []
        _, r = self.ask(ids=["pad-1"])
        self.assertEqual(r["message"], "Open the board in KiCad's PCB editor first.")

    def test_another_board_open(self):
        FakeKiCad.docs = [Doc("/elsewhere", "other.kicad_pcb")]
        _, r = self.ask(ids=["pad-1"])
        self.assertIn("another board open (other.kicad_pcb)", r["message"])

    def test_without_kicad_python(self):
        out = io.StringIO()
        saved = sys.modules.pop("kipy")
        sys.modules["kipy"] = None  # import kipy -> ImportError
        try:
            show_in_kicad.main(io.StringIO("{}"), out)
        finally:
            sys.modules["kipy"] = saved
        self.assertIn("needs kicad-python", json.loads(out.getvalue())["message"])


if __name__ == "__main__":
    unittest.main()
