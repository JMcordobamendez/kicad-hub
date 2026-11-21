#!/usr/bin/env python3
"""Read a .kicad_pcb file with plain Python: no KiCad, no pcbnew.

The board file is a documented s-expression format
(https://dev-docs.kicad.org/en/file-formats/sexpr-pcb/). This reads what the fab notes
need: title block, layers, thickness, tracks, vias, pads, footprint attributes and the
outline on Edge.Cuts. It replaces loading the board with pcbnew, which needs KiCad's own
Python (KiCad 11 is expected to stop shipping one) and on Windows could open a window
that waits for OK.

    board = Board.load("board.kicad_pcb")
    board.copper_layers, board.outline_size(), board.min_track_width ...
"""
import math
import re

_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))', re.S)
_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "\\": "\\"}


class Quoted(str):
    """A string that was written in quotes, so `(layer "F.Cu")` and `(layer F.Cu)` both
    read as text and a quoted "1" is never taken for a number."""


def parse(text):
    """The s-expression as nested lists of str / Quoted / float."""
    stack, current, pos = [], [], 0
    end = len(text)
    while pos < end:
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip():
                raise ValueError(f"unexpected text at {pos}: {text[pos:pos + 20]!r}")
            break
        pos = m.end()
        opening, closing, quoted, atom = m.groups()
        if opening:
            stack.append(current)
            current = []
        elif closing:
            if not stack:
                raise ValueError(f"unbalanced ')' at {pos}")
            parent = stack.pop()
            parent.append(current)
            current = parent
        elif quoted is not None:
            current.append(Quoted(re.sub(r"\\(.)", lambda e: _ESCAPES.get(e.group(1), e.group(1)), quoted)))
        else:
            try:
                current.append(float(atom))
            except ValueError:
                current.append(atom)
    if stack:
        raise ValueError("unbalanced '(': the file ends inside a list")
    return current[0] if len(current) == 1 else current


def children(node, name):
    """The lists inside `node` whose first word is `name`."""
    return [c for c in node if isinstance(c, list) and c and c[0] == name]


def child(node, name):
    found = children(node, name)
    return found[0] if found else None


def value(node, name, default=None):
    """The first value of the child `name`: `(thickness 1.6)` -> 1.6."""
    c = child(node, name)
    return c[1] if c is not None and len(c) > 1 else default


def words(node):
    """The bare words directly inside a list: `(attr smd dnp)` -> {"smd", "dnp"}."""
    return {w for w in node[1:] if isinstance(w, str) and not isinstance(w, Quoted)}


def point(node, name):
    c = child(node, name)
    return (float(c[1]), float(c[2])) if c is not None and len(c) > 2 else None


COPPER_TYPES = {"signal", "power", "mixed", "jumper"}


class Board:
    def __init__(self, tree):
        if not (isinstance(tree, list) and tree and tree[0] == "kicad_pcb"):
            raise ValueError("not a KiCad board file")
        self.tree = tree
        self.footprints = children(tree, "footprint")

    @classmethod
    def load(cls, path):
        with open(path, encoding="utf-8") as f:
            return cls(parse(f.read()))

    # --- general ------------------------------------------------------------------------
    def title_block(self, field):
        tb = child(self.tree, "title_block")
        return str(value(tb, field, "")) if tb is not None else ""

    @property
    def thickness(self):
        general = child(self.tree, "general")
        return float(value(general, "thickness", 1.6)) if general is not None else 1.6

    @property
    def copper_layers(self):
        layers = child(self.tree, "layers") or []
        return sum(1 for layer in layers[1:] if isinstance(layer, list) and len(layer) > 2
                   and str(layer[1]).endswith(".Cu") and str(layer[2]) in COPPER_TYPES)

    # --- tracks, vias, pads -------------------------------------------------------------
    def track_widths(self):
        return [float(value(t, "width", 0)) for t in children(self.tree, "segment") + children(self.tree, "arc")]

    def vias(self):
        """[(drill, diameter)] of every via."""
        out = []
        for v in children(self.tree, "via"):
            out.append((float(value(v, "drill", 0)), float(value(v, "size", 0))))
        return out

    def pads(self):
        """[(kind, smallest drill size or None)]: kind is thru_hole, np_thru_hole, smd..."""
        out = []
        for fp in self.footprints:
            for pad in children(fp, "pad"):
                kind = str(pad[2]) if len(pad) > 2 else ""
                drill = child(pad, "drill")
                size = None
                if drill is not None:
                    numbers = [x for x in drill[1:] if isinstance(x, float)]
                    size = min(numbers[:2]) if numbers else None
                out.append((kind, size))
        return out

    def footprint_counts(self):
        """(smd, tht, on the bottom): footprints marked SMD or through-hole, as KiCad counts
        them for assembly (those with neither, e.g. logos and test points, are left out)."""
        smd = tht = bottom = 0
        for fp in self.footprints:
            attr = child(fp, "attr")
            kinds = words(attr) if attr is not None else set()
            assembled = bool(kinds & {"smd", "through_hole"})
            smd += "smd" in kinds
            tht += "through_hole" in kinds
            bottom += assembled and str(value(fp, "layer", "")).startswith("B.")
        return smd, tht, bottom

    # --- outline ------------------------------------------------------------------------
    def outline_points(self):
        """Points on every Edge.Cuts shape (arcs and circles at their extremes), in board
        coordinates, with each one's stroke width: [(x, y, width)]."""
        pts = []
        for shape in self.tree[1:]:
            if isinstance(shape, list) and shape and str(shape[0]).startswith("gr_") and _on_edge(shape):
                pts += [(x, y, _width(shape)) for x, y in _shape_points(shape)]
        for fp in self.footprints:
            at = child(fp, "at")
            ox, oy = (float(at[1]), float(at[2])) if at is not None else (0.0, 0.0)
            angle = float(at[3]) if at is not None and len(at) > 3 else 0.0
            for shape in fp[1:]:
                if isinstance(shape, list) and shape and str(shape[0]).startswith("fp_") and _on_edge(shape):
                    pts += [(*_to_board(x, y, ox, oy, angle), _width(shape)) for x, y in _shape_points(shape)]
        return pts

    def outline_size(self):
        """(width, height) of the outline, strokes included, as KiCad measures the board;
        None when nothing is drawn on Edge.Cuts."""
        pts = self.outline_points()
        if not pts:
            return None
        xs = [x - w / 2 for x, _, w in pts] + [x + w / 2 for x, _, w in pts]
        ys = [y - w / 2 for _, y, w in pts] + [y + w / 2 for _, y, w in pts]
        return max(xs) - min(xs), max(ys) - min(ys)

    # --- the file's own text ------------------------------------------------------------
    @staticmethod
    def stackup_text(text):
        start = text.find("(stackup")
        if start == -1:
            return ""
        depth = 0
        for i in range(start, len(text)):
            depth += {"(": 1, ")": -1}.get(text[i], 0)
            if depth == 0:
                return text[start:i + 1]
        return ""


def _on_edge(shape):
    layer = child(shape, "layer")
    if layer is not None and len(layer) > 1 and str(layer[1]) == "Edge.Cuts":
        return True
    layers = child(shape, "layers")
    return layers is not None and "Edge.Cuts" in [str(x) for x in layers[1:]]


def _width(shape):
    stroke = child(shape, "stroke")
    w = value(stroke, "width") if stroke is not None else None
    if w is None:
        w = value(shape, "width", 0)
    return float(w or 0)


def _to_board(x, y, ox, oy, angle_deg):
    """A footprint's local point in board coordinates (KiCad: Y points down, angles turn
    counter-clockwise on screen)."""
    a = math.radians(angle_deg)
    return ox + x * math.cos(a) + y * math.sin(a), oy - x * math.sin(a) + y * math.cos(a)


def _shape_points(shape):
    kind = str(shape[0])[3:]
    if kind in ("line", "rect"):
        return [p for p in (point(shape, "start"), point(shape, "end")) if p]
    if kind == "circle":
        c, e = point(shape, "center"), point(shape, "end")
        if not (c and e):
            return []
        r = math.hypot(e[0] - c[0], e[1] - c[1])
        return [(c[0] - r, c[1]), (c[0] + r, c[1]), (c[0], c[1] - r), (c[0], c[1] + r)]
    if kind == "arc":
        s, m, e = point(shape, "start"), point(shape, "mid"), point(shape, "end")
        return _arc_points(s, m, e) if s and m and e else [p for p in (s, e) if p]
    if kind in ("poly", "curve"):  # a Bézier stays inside its control points: close enough
        pts = child(shape, "pts")
        return [(float(xy[1]), float(xy[2])) for xy in (children(pts, "xy") if pts else [])]
    return []


def _arc_points(s, m, e):
    """The ends of an arc through s, m, e and the extremes (left, right, top, bottom) it
    passes through."""
    (x1, y1), (x2, y2), (x3, y3) = s, m, e
    d = 2 * (x1 * (y2 - y3) + x2 * (y3 - y1) + x3 * (y1 - y2))
    if abs(d) < 1e-12:  # the three points are in a line
        return [s, m, e]
    ux = ((x1 ** 2 + y1 ** 2) * (y2 - y3) + (x2 ** 2 + y2 ** 2) * (y3 - y1) + (x3 ** 2 + y3 ** 2) * (y1 - y2)) / d
    uy = ((x1 ** 2 + y1 ** 2) * (x3 - x2) + (x2 ** 2 + y2 ** 2) * (x1 - x3) + (x3 ** 2 + y3 ** 2) * (x2 - x1)) / d
    r = math.hypot(x1 - ux, y1 - uy)
    ang = lambda p: math.atan2(p[1] - uy, p[0] - ux) % (2 * math.pi)  # noqa: E731
    a1, a2, a3 = ang(s), ang(m), ang(e)
    ccw = (a2 - a1) % (2 * math.pi) < (a3 - a1) % (2 * math.pi)  # going s -> m -> e
    span = (a3 - a1) % (2 * math.pi) if ccw else (a1 - a3) % (2 * math.pi)
    out = [s, e]
    for k in range(4):
        t = k * math.pi / 2
        off = (t - a1) % (2 * math.pi) if ccw else (a1 - t) % (2 * math.pi)
        if off <= span:
            out.append((ux + r * math.cos(t), uy + r * math.sin(t)))
    return out
