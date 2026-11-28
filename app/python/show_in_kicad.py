#!/usr/bin/env python3
"""Show in KiCad: select a row's items on the board open in KiCad and zoom to them.

The desktop app runs it with its own Python, which has kicad-python (kipy) installed (the
installer bundles it). It reads one JSON object on stdin:

    {"ids": [...], "footprints": [...], "project_dir": "...", "board": "riego.kicad_pcb"}

`ids` are the items KiCad flagged (a pad, a name, a track), `footprints` the footprints of
the parts the row is about: when none of the ids is on the board any more (or the row
comes from the schematic), the footprints are selected instead. It writes one JSON object
on stdout: {"ok": bool, "selected": n, "zoomed": bool, "message": "..."}, the message in
plain words for the window.

KiCad has to be running with the board open in the PCB editor, and Preferences → Plugins
→ "Enable KiCad API" ticked (off by default).
"""
import json
import os
import sys

ZOOM = "common.Control.zoomFitSelection"
CLIENT = "kicad-hub"
ENABLE = "In KiCad, tick Preferences → Plugins → Enable KiCad API, then try again."


class Problem(Exception):
    """Something the user can fix; the message is shown in the window."""


def same_folder(a, b):
    try:
        return os.path.normcase(os.path.realpath(a)) == os.path.normcase(os.path.realpath(b))
    except (OSError, TypeError, ValueError):
        return False


def connect(kipy):
    try:
        kicad = kipy.KiCad(client_name=CLIENT, timeout_ms=3000)
        kicad.ping()
        return kicad
    except Exception as e:  # pynng/kipy connection errors: KiCad is closed or its API is off
        raise Problem(f"KiCad doesn't answer. Is it open? {ENABLE}") from e


def board_of(kicad, project_dir, board_name):
    from kipy.proto.common.types import DocumentType  # noqa: PLC0415
    try:
        docs = kicad.get_open_documents(DocumentType.DOCTYPE_PCB)
    except Exception as e:
        raise Problem(f"KiCad doesn't answer. {ENABLE}") from e
    if not docs:
        raise Problem("Open the board in KiCad's PCB editor first.")
    for doc in docs:
        if not project_dir or same_folder(doc.project.path, project_dir) \
                or (board_name and doc.board_filename == board_name):
            from kipy.board import Board  # noqa: PLC0415
            return Board(kicad._client, doc)
    raise Problem(f"KiCad has another board open ({docs[0].board_filename}). "
                  f"Open {board_name or 'this project'}'s board in the PCB editor.")


def show(kicad, board, ids, footprints):
    from kipy.proto.common.types import KIID  # noqa: PLC0415

    def kiids(values):
        return [KIID(value=v) for v in values if v]

    found = []
    for group in (ids, footprints):
        if group and not found:
            try:
                found = list(board.get_items_by_id(kiids(group)))
            except Exception:  # one id KiCad can't take: try the others one by one
                found = []
                for k in kiids(group):
                    try:
                        found += list(board.get_items_by_id([k]))
                    except Exception:
                        pass
    if not found:
        raise Problem("These are no longer on the board. Save in KiCad: the list catches up after the check.")
    board.clear_selection()
    board.add_to_selection(found)
    zoomed = False
    try:
        reply = kicad.run_action(ZOOM)
        zoomed = getattr(reply, "status", 0) == 1  # RAS_OK
    except Exception:
        zoomed = False
    n = len(found)
    what = f"{n} {'item' if n == 1 else 'items'}"
    return {"ok": True, "selected": n, "zoomed": zoomed,
            "message": f"Selected {what} in KiCad." if zoomed else f"Selected {what} in KiCad (zoom in to see them)."}


def main(stdin=sys.stdin, stdout=sys.stdout, kipy=None):
    try:
        request = json.loads(stdin.read() or "{}")
        if kipy is None:
            try:
                import kipy  # noqa: PLC0415 - bundled with the app
            except ImportError as e:
                raise Problem("Show in KiCad needs kicad-python, which comes with the app's installer "
                              "(from the repository: pip install kicad-python).") from e
        kicad = connect(kipy)
        board = board_of(kicad, request.get("project_dir", ""), request.get("board", ""))
        result = show(kicad, board, request.get("ids") or [], request.get("footprints") or [])
    except Problem as e:
        result = {"ok": False, "selected": 0, "zoomed": False, "message": str(e)}
    except Exception as e:  # anything else: say it, don't crash the window's request
        result = {"ok": False, "selected": 0, "zoomed": False, "message": f"KiCad said: {e}"}
    stdout.write(json.dumps(result) + "\n")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
