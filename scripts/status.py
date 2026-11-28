#!/usr/bin/env python3
"""The state of a design in plain words, for the desktop app: status.json.

    build(project_dir, name, reports, ...) -> dict  (written by `kicad_hub.py status`)

From the reports the quick check writes (ERC, DRC with schematic parity, the per-part BOM
and the BOM of the branch it compares with) it makes:

- a headline: "No errors. Six small things left to tidy up.";
- five indicators: Schematic, Board, In sync, Parts, Datasheets;
- the list of things to tidy up, the ones that stop an order first. Each one is a short
  sentence ("The names of Q1 and TP4 are stuck together"), a detail with the numbers, the
  parts it is about and the ids KiCad needs to select them on the board.

kicad-cli writes its messages in the language of the KiCad that runs it, so the sentences
never come from them: they come from each violation's `type` (the same in every language),
from the board file (what each item is: a pad, a name, a track and its net…) and from the
references the project really has. Only the numbers are read from KiCad's message: the last
two lengths in it are always the one needed and the one found ("mínimo 0,1800 mm; real
0,1500 mm" too). A type this file does not know is shown with KiCad's own title.
"""
import csv
import hashlib
import json
import os
import re

import board_file

VERSION = 1
AREAS = ("schematic", "board", "sync", "parts", "datasheets")
LABELS = {"schematic": "Schematic", "board": "Board", "sync": "In sync", "parts": "Parts",
          "datasheets": "Datasheets"}
ORDER = ("sync", "board", "schematic", "parts", "datasheets")  # of the list, after the errors
GROUP_AT = 6  # this many violations of one kind (or more) make a single row
MAX_IDS = 500  # ids kept per row, for KiCad to select
DATASHEET_OPTIONAL = "R,C,TP,H,MH,FID,JP,NT,LOGO"
WORDS = ("No", "One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten", "Eleven", "Twelve")


def number(n, capital=True):
    word = WORDS[n] if 0 <= n < len(WORDS) else str(n)
    return word if capital or not word.isalpha() else word.lower()


def plural(n, one, many=None):
    return one if n == 1 else (many or one + "s")


def mm(x):
    """0.15 -> "0.15", 0.002 -> "0.002", 1e-05 -> "0.00001", 0 -> "0"."""
    if x == 0:
        return "0"
    for digits in (2, 3, 4, 5):
        text = f"{x:.{digits}f}"
        if float(text) != 0 and abs(float(text) - x) <= abs(x) * 0.01:
            break
    return text.rstrip("0").rstrip(".")


_LENGTH = re.compile(r"(-?\d+(?:[.,]\d+)?(?:[eE][-+]?\d+)?)\s*mm\b")


def lengths(description):
    """(needed, found) in mm from KiCad's message, or None: the last two lengths in it."""
    found = [float(x.replace(",", ".")) for x in _LENGTH.findall(description or "")]
    return (found[-2], found[-1]) if len(found) >= 2 else None


def title_of(description):
    """KiCad's own title of a violation: the message without its details in brackets."""
    return (description or "").split(" (", 1)[0].strip() or "Something KiCad flagged"


def ref_key(ref):
    m = re.match(r"([A-Za-z_#]*)(\d*)", ref)
    return (m.group(1), int(m.group(2) or 0), ref)


# --- what each item on the board is ------------------------------------------------------------

class BoardIndex:
    """uuid -> what that item is on the board: {"kind", "ref", "net", "pad", "fp"}."""

    def __init__(self, board=None):
        self.items, self.footprints = {}, {}
        if board is not None:
            self._read(board)

    @classmethod
    def load(cls, path):
        try:
            return cls(board_file.Board.load(path))
        except (OSError, ValueError):
            return cls()

    def _read(self, board):
        tree = board.tree
        nets = {}
        for n in board_file.children(tree, "net"):
            if len(n) > 2:
                nets[n[1]] = str(n[2])

        def net_of(node):
            n = board_file.child(node, "net")
            if n is None or len(n) < 2:
                return ""
            name = str(n[2]) if len(n) > 2 else (nets.get(n[1], "") if isinstance(n[1], float) else str(n[1]))
            return name

        def uid(node):
            return str(board_file.value(node, "uuid", "") or board_file.value(node, "tstamp", ""))

        for fp in board.footprints:
            ref = ""
            for prop in board_file.children(fp, "property"):
                if len(prop) > 2 and str(prop[1]) == "Reference":
                    ref = str(prop[2])
            fid = uid(fp)
            if fid:
                self.items[fid] = {"kind": "footprint", "ref": ref, "fp": fid}
                if ref:
                    self.footprints.setdefault(ref, fid)
            for node in fp[1:]:
                if not (isinstance(node, list) and node):
                    continue
                key, u = str(node[0]), uid(node)
                if not u:
                    continue
                info = {"ref": ref, "fp": fid}
                if key == "pad":
                    info.update(kind="pad", pad=str(node[1]) if len(node) > 1 else "", net=net_of(node))
                elif key == "property":
                    field = str(node[1]) if len(node) > 1 else ""
                    info["kind"] = {"Reference": "name", "Value": "value"}.get(field, "field")
                elif key == "fp_text":
                    text = str(node[2]) if len(node) > 2 else ""
                    info["kind"] = {"${REFERENCE}": "name", "${VALUE}": "value"}.get(text, "text")
                elif key.startswith("fp_"):
                    layer = str(board_file.value(node, "layer", ""))
                    info["kind"] = ("silk" if layer.endswith("SilkS") else "courtyard" if layer.endswith("CrtYd")
                                    else "edge" if layer == "Edge.Cuts" else "drawing")
                else:
                    info["kind"] = "part"
                self.items[u] = info
        for node in tree[1:]:
            if not (isinstance(node, list) and node):
                continue
            key, u = str(node[0]), uid(node)
            if not u or key == "footprint":
                continue
            if key in ("segment", "arc"):
                self.items[u] = {"kind": "track", "net": net_of(node)}
            elif key == "via":
                self.items[u] = {"kind": "via", "net": net_of(node)}
            elif key == "zone":
                self.items[u] = {"kind": "zone", "net": str(board_file.value(node, "net_name", "") or net_of(node))}
            elif key == "gr_text":
                self.items[u] = {"kind": "board text"}
            elif key.startswith("gr_"):
                edge = board_file._on_edge(node)
                self.items[u] = {"kind": "edge" if edge else "drawing",
                                 "layer": str(board_file.value(node, "layer", ""))}


def item_info(item, index, refs):
    """What a violation's item is: the board's index, else the references named in KiCad's
    text (never its words, which are translated; what is in [brackets] is a net or a value)."""
    uid = item.get("uuid") or ""
    info = dict(index.items.get(uid) or {})
    if not info.get("ref"):
        text = re.sub(r"\[[^\]]*\]", " ", item.get("description") or "")
        named = [t for t in re.findall(r"[A-Za-z_#][A-Za-z0-9_#+-]*", text) if t in refs]
        if named:
            info["ref"] = named[-1] if re.search(r"\b(of|de|von|di|du)\b", text) else named[0]
        elif re.search(r"#(PWR|FLG)\w*", text):
            info["kind"] = "power symbol"
    if not info.get("net"):
        m = re.search(r"\[([^\]]*)\]", item.get("description") or "")
        if m and info.get("kind") in ("track", "via", "zone", "pad"):
            info["net"] = m.group(1)
    info.setdefault("kind", "part" if info.get("ref") else "item")
    info["uuid"] = uid
    return info


def noun(info, capital=False):
    """A short name for the item: "the name of U1", "pad 1 of J2", "a GND track"."""
    kind, ref, net = info.get("kind"), info.get("ref") or "", info.get("net") or ""
    net = "" if net.startswith(("Net-(", "unconnected-")) else net
    on = f"{net} " if net else ""
    text = {
        "footprint": ref or "a part",
        "part": ref or "a part",
        "pad": (f"pad {info['pad']} of {ref}" if info.get("pad") else f"a pad of {ref}") if ref else f"a {on}pad",
        "name": f"the name of {ref}" if ref else "a part's name",
        "value": f"the value of {ref}" if ref else "a part's value",
        "field": f"a text of {ref}" if ref else "a text",
        "text": f"a text of {ref}" if ref else "a text",
        "silk": f"the silkscreen of {ref}" if ref else "the silkscreen",
        "courtyard": f"the courtyard of {ref}" if ref else "a courtyard",
        "drawing": f"a drawing of {ref}" if ref else "a drawing",
        "edge": "the board edge",
        "track": f"a {on}track",
        "via": f"a {on}via",
        "zone": f"the {on}copper zone" if net else "a copper zone",
        "board text": "a text on the board",
        "power symbol": "a power symbol",
    }.get(kind) or (ref or "an item")
    return text[0].upper() + text[1:] if capital else text


def owner(info):
    """The part an item belongs to, as a noun: "J2", else the item itself."""
    return info.get("ref") or noun(info)


# --- the sentences ------------------------------------------------------------------------------

def apart(m):
    if m is None:
        return ""
    need, act = m
    if act <= 0:
        return f"They touch. They need {mm(need)} mm."
    return f"They are {mm(act)} mm apart. They need {mm(need)} mm."


def size(m, what="It"):
    if m is None:
        return ""
    need, act = m
    return f"{what} is {mm(act)} mm. " + (f"The rules ask for at least {mm(need)} mm." if act < need
                                         else f"The rules allow at most {mm(need)} mm.")


def small_or_big(m, small="too small", big="too big"):
    return big if m and m[1] > m[0] else small


def two(items):
    a = items[0]
    b = items[1] if len(items) > 1 else None
    return a, b


def _silk_pair(a, b):
    if b is not None and a.get("kind") == "name" and b.get("kind") == "name":
        return f"The names of {a.get('ref')} and {b.get('ref')} are stuck together"
    if b is None:
        return f"{noun(a, True)} overlaps another print"
    if b.get("kind") in ("silk",) and a.get("kind") != "silk":
        a, b = b, a
    return f"{noun(a, True)} sits on top of {noun(b)}"


def _not_edge(items):
    rest = [i for i in items if i.get("kind") != "edge"]
    return rest[0] if rest else items[0]


def _len_rule(what):
    return lambda items, m: (f"{noun(items[0], True)} is {'longer' if m and m[1] > m[0] else 'shorter'} than "
                             f"its {what} rule allows", size(m, "Its length") if what == "length" else "")


# type -> function(items, lengths) -> (title, detail). items: item_info dicts.
DRC = {
    "clearance": lambda it, m: (f"{noun(it[0], True)} is too close to {noun(it[1]) if len(it) > 1 else 'other copper'}",
                                apart(m)),
    "copper_edge_clearance": lambda it, m: (f"{noun(_not_edge(it), True)} is too close to the board edge",
                                            apart(m) or "Copper needs a gap to the edge of the board."),
    "edge_clearance": lambda it, m: (f"{noun(_not_edge(it), True)} is too close to the board edge", apart(m)),
    "hole_clearance": lambda it, m: (f"{noun(it[0], True)} is too close to "
                                     + (f"the hole of {owner(it[1])}" if len(it) > 1 else "a hole"), apart(m)),
    "hole_to_hole": lambda it, m: ((f"The holes of {owner(it[0])} and {owner(it[1])} are too close together"
                                    if len(it) > 1 else f"A hole of {owner(it[0])} is too close to another"), apart(m)),
    "holes_co_located": lambda it, m: (f"Two holes are drilled in the same place, at {owner(it[0])}",
                                       "One of them is probably left over: remove it."),
    "drill_out_of_range": lambda it, m: (f"The hole of {noun(it[0])} is {small_or_big(m)}", size(m, "The hole")),
    "microvia_drill_out_of_range": lambda it, m: (f"The hole of {noun(it[0])} is {small_or_big(m)}",
                                                  size(m, "The hole")),
    "via_diameter": lambda it, m: (f"{noun(it[0], True)} is {small_or_big(m)}", size(m, "Its diameter")),
    "annular_width": lambda it, m: (f"The copper ring around the hole of {noun(it[0])} is "
                                    + small_or_big(m, "too thin", "too wide"), size(m, "The ring")),
    "track_width": lambda it, m: (f"{noun(it[0], True)} is {small_or_big(m, 'too thin', 'too wide')}",
                                  size(m, "Its width")),
    "connection_width": lambda it, m: (f"The copper near {owner(it[0])} narrows too much", size(m, "The narrowest part")),
    "track_angle": lambda it, m: (f"{noun(it[0], True)} turns at too sharp an angle", ""),
    "track_segment_length": lambda it, m: (f"A piece of {noun(it[0])} is {small_or_big(m, 'too short', 'too long')}",
                                           size(m, "It")),
    "courtyards_overlap": lambda it, m: ((f"{owner(it[0])} and {owner(it[1])} overlap" if len(it) > 1
                                          else f"{owner(it[0])} overlaps another part"),
                                         "The space each part needs on the board overlaps."),
    "courtyard_clearance": lambda it, m: (f"{owner(it[0])} is too close to "
                                          + (owner(it[1]) if len(it) > 1 else "another part"), apart(m)),
    "missing_courtyard": lambda it, m: (f"{owner(it[0])} has no courtyard",
                                        "Its footprint has no outline of the space it needs, so overlaps can't be checked."),
    "malformed_courtyard": lambda it, m: (f"The courtyard of {owner(it[0])} is not a closed shape", ""),
    "npth_inside_courtyard": lambda it, m: (f"A hole is inside the space of {owner(it[-1])}", ""),
    "pth_inside_courtyard": lambda it, m: (f"A plated hole is inside the space of {owner(it[-1])}", ""),
    "unconnected_items": lambda it, m: ((f"{noun(it[0], True)} and {noun(it[1])} still need a connection" if len(it) > 1
                                         else f"{noun(it[0], True)} still needs a connection"),
                                        "The schematic joins them; on the board they are not joined yet."),
    "shorting_items": lambda it, m: ((f"{noun(it[0], True)} and {noun(it[1])} touch" if len(it) > 1
                                      else f"{noun(it[0], True)} touches other copper"),
                                     "They are on different nets: that is a short circuit."),
    "tracks_crossing": lambda it, m: ((f"{noun(it[0], True)} crosses {noun(it[1])}" if len(it) > 1
                                       else f"{noun(it[0], True)} crosses another track"), "Tracks on the same layer can't cross."),
    "zones_intersect": lambda it, m: ("Two copper zones overlap", ""),
    "isolated_copper": lambda it, m: (f"A piece of {noun(it[0])} is not connected to anything", ""),
    "starved_thermal": lambda it, m: (f"{noun(it[0], True)} has too few spokes joining it to its zone", ""),
    "via_dangling": lambda it, m: (f"{noun(it[0], True)} is not connected on both sides", ""),
    "track_dangling": lambda it, m: (f"{noun(it[0], True)} ends without connecting to anything", ""),
    "track_not_centered_on_via": lambda it, m: (f"{noun(it[0], True)} does not end in the middle of its via", ""),
    "invalid_outline": lambda it, m: ("The board outline is not a closed shape",
                                      "Check the lines on Edge.Cuts: every corner must meet."),
    "item_on_disabled_layer": lambda it, m: (f"{noun(it[0], True)} is on a layer the board does not use", ""),
    "items_not_allowed": lambda it, m: (f"{noun(it[0], True)} is in an area where it is not allowed", ""),
    "silk_overlap": lambda it, m: (_silk_pair(*two(it)), apart(m)),
    "silk_clearance": lambda it, m: (_silk_pair(*two(it)), apart(m)),
    "silk_over_copper": lambda it, m: (f"{noun(it[0], True)} is printed over copper",
                                       "The fab cuts away the print where it lands on a pad."),
    "silk_over_via": lambda it, m: (f"{noun(it[0], True)} is printed over a via", ""),
    "silk_edge_clearance": lambda it, m: (f"{noun(_not_edge(it), True)} is too close to the board edge", apart(m)),
    "silk_board_edge_clearance": lambda it, m: (f"{noun(_not_edge(it), True)} is too close to the board edge", apart(m)),
    "solder_mask_bridge": lambda it, m: ((f"The solder mask between {noun(it[0])} and {noun(it[1])} is missing"
                                          if len(it) > 1 else f"The solder mask at {noun(it[0])} joins other copper"), ""),
    "solder_mask_sliver": lambda it, m: (f"A thin sliver of solder mask is left near {owner(it[0])}", size(m, "It")),
    "copper_sliver": lambda it, m: (f"A thin sliver of copper is left near {owner(it[0])}", ""),
    "text_height": lambda it, m: (f"{noun(it[0], True)} is too small to print well", size(m, "Its height")),
    "text_thickness": lambda it, m: (f"The lines of {noun(it[0])} are too thin to print well", size(m, "They")),
    "text_on_edge_cuts": lambda it, m: ("There is text on the board edge layer", "The fab would try to cut it out."),
    "mirrored_text_on_front_layer": lambda it, m: (f"{noun(it[0], True)} is mirrored on the top side", ""),
    "nonmirrored_text_on_back_layer": lambda it, m: (f"{noun(it[0], True)} on the bottom side is not mirrored", ""),
    "lib_footprint_issues": lambda it, m: (f"The footprint of {owner(it[0])} can't be checked against your libraries",
                                           "KiCad can't find its library on this computer."),
    "lib_footprint_mismatch": lambda it, m: (f"The footprint of {owner(it[0])} no longer matches your library",
                                             "Update it from the library, or keep it if you changed it on purpose."),
    "footprint_type_mismatch": lambda it, m: (f"{owner(it[0])} is marked SMD or through-hole wrongly",
                                              "Its pads say otherwise: fix its attributes."),
    "through_hole_pad_without_hole": lambda it, m: (f"{noun(it[0], True)} is through-hole but has no hole", ""),
    "padstack_invalid": lambda it, m: (f"{noun(it[0], True)} has a shape the fab can't make", ""),
    "length_out_of_range": _len_rule("length"),
    "skew_out_of_range": lambda it, m: (f"{noun(it[0], True)} is out of step with its pair or group", ""),
    "diff_pair_gap_out_of_range": lambda it, m: (f"The gap of the pair at {noun(it[0])} is out of its rule", apart(m)),
    "diff_pair_uncoupled_length_too_long": lambda it, m: (f"The pair at {noun(it[0])} runs apart for too long", ""),
    "too_many_vias": lambda it, m: (f"{noun(it[0], True)} goes through too many vias", ""),
    "unresolved_variable": lambda it, m: (f"{noun(it[0], True)} uses a ${{VARIABLE}} that has no value", ""),
    # schematic parity: the board against the schematic
    "missing_footprint": lambda it, m: (f"{owner(it[0])} is in the schematic but not on the board",
                                        "In KiCad: Tools → Update PCB from Schematic."),
    "extra_footprint": lambda it, m: (f"{owner(it[0])} is on the board but not in the schematic",
                                      "Delete it, or add it to the schematic."),
    "duplicate_footprints": lambda it, m: (f"Two parts on the board are both called {owner(it[0])}", ""),
    "net_conflict": lambda it, m: (f"{noun(it[0], True)} is joined differently on the board and in the schematic",
                                   "In KiCad: Tools → Update PCB from Schematic."),
    "footprint_symbol_mismatch": lambda it, m: (f"The footprint of {owner(it[0])} is not the one the schematic asks for",
                                                "In KiCad: Tools → Update PCB from Schematic."),
    "footprint_symbol_field_mismatch": lambda it, m: (f"A field of {owner(it[0])} differs between board and schematic",
                                                      "In KiCad: Tools → Update PCB from Schematic."),
    "footprint_filters_mismatch": lambda it, m: (f"The footprint of {owner(it[0])} does not fit its symbol", ""),
}

ERC = {
    "pin_not_connected": lambda it, m: (f"A pin of {owner(it[0])} is not connected",
                                        "Wire it, or mark it with a no-connect flag (X)."),
    "pin_not_driven": lambda it, m: (f"An input pin of {owner(it[0])} has nothing driving it", ""),
    "power_pin_not_driven": lambda it, m: ("A power net has no source" if it[0].get("kind") == "power symbol"
                                           else f"A power input of {owner(it[0])} has no power source",
                                           "If the power comes in through a connector, put a PWR_FLAG on that net."),
    "pin_to_pin": lambda it, m: ((f"Pins of {owner(it[0])} and {owner(it[1])} should not be joined" if len(it) > 1
                                  else f"Two pins of {owner(it[0])} should not be joined"),
                                 "Two outputs, or an output and a power pin, are on the same net."),
    "duplicate_reference": lambda it, m: (f"Two parts are both called {owner(it[0])}", "Annotate the schematic again."),
    "unannotated": lambda it, m: (f"{owner(it[0])} has no number yet", "In KiCad: Tools → Annotate Schematic."),
    "wire_dangling": lambda it, m: ("A wire ends without connecting to anything", ""),
    "unconnected_wire_endpoint": lambda it, m: ("A wire end is not connected", ""),
    "label_dangling": lambda it, m: ("A label is not on any wire", ""),
    "isolated_pin_label": lambda it, m: ("A label reaches only one pin", ""),
    "label_multiple_wires": lambda it, m: ("A label touches more than one wire", ""),
    "endpoint_off_grid": lambda it, m: ("A pin or a wire end is off the grid", "It may look connected and not be."),
    "four_way_junction": lambda it, m: ("Four wires meet in one point", "It is easy to misread: split it in two."),
    "similar_labels": lambda it, m: ("Two labels differ only in upper and lower case", ""),
    "similar_power": lambda it, m: ("Two power symbols differ only in upper and lower case", ""),
    "similar_label_and_power": lambda it, m: ("A label and a power symbol differ only in upper and lower case", ""),
    "same_local_global_label": lambda it, m: ("A local and a global label have the same name", ""),
    "single_global_label": lambda it, m: ("A global label is used only once", ""),
    "hier_label_mismatch": lambda it, m: ("A sheet pin and the label inside the sheet do not match", ""),
    "multiple_net_names": lambda it, m: ("One wire has two different net names", ""),
    "net_not_bus_member": lambda it, m: ("A net is drawn into a bus it does not belong to", ""),
    "bus_entry_needed": lambda it, m: ("A wire meets a bus without a bus entry", ""),
    "bus_to_net_conflict": lambda it, m: ("A bus is joined straight to a net", ""),
    "bus_to_bus_conflict": lambda it, m: ("Two buses with nothing in common are joined", ""),
    "bus_definition_conflict": lambda it, m: ("A bus alias is defined twice in different ways", ""),
    "no_connect_connected": lambda it, m: (f"A pin of {owner(it[0])} marked not-connected is connected", ""),
    "no_connect_dangling": lambda it, m: ("A no-connect flag is not on any pin", ""),
    "lib_symbol_issues": lambda it, m: (f"{owner(it[0])} can't be checked against your symbol libraries",
                                        "KiCad can't find its library on this computer."),
    "lib_symbol_mismatch": lambda it, m: (f"{owner(it[0])} no longer matches its library symbol",
                                          "Update it from the library, or keep it if you changed it on purpose."),
    "footprint_link_issues": lambda it, m: (f"The footprint of {owner(it[0])} is not in your libraries", ""),
    "footprint_filter": lambda it, m: (f"The footprint of {owner(it[0])} does not fit its symbol's filters", ""),
    "missing_unit": lambda it, m: (f"A unit of {owner(it[0])} is not placed", ""),
    "missing_input_pin": lambda it, m: (f"A unit of {owner(it[0])} with input pins is not placed", ""),
    "missing_power_pin": lambda it, m: (f"A unit of {owner(it[0])} with power pins is not placed",
                                        "Without it the part gets no power."),
    "missing_bidi_pin": lambda it, m: (f"A unit of {owner(it[0])} with bidirectional pins is not placed", ""),
    "different_unit_footprint": lambda it, m: (f"The units of {owner(it[0])} have different footprints", ""),
    "different_unit_net": lambda it, m: (f"The units of {owner(it[0])} join a shared pin to different nets", ""),
    "unit_value_mismatch": lambda it, m: (f"The units of {owner(it[0])} have different values", ""),
    "extra_units": lambda it, m: (f"{owner(it[0])} has more units than its symbol", ""),
    "duplicate_pins": lambda it, m: (f"{owner(it[0])} has two pins with the same number", ""),
    "stacked_pin_name": lambda it, m: (f"Stacked pins of {owner(it[0])} have different names", ""),
    "ground_pin_not_ground": lambda it, m: (f"A ground pin of {owner(it[0])} is not on a ground net", ""),
    "simulation_model_issue": lambda it, m: (f"The simulation model of {owner(it[0])} has a problem", ""),
    "undefined_netclass": lambda it, m: ("A net class is used that is not defined", ""),
    "field_name_whitespace": lambda it, m: (f"A field name of {owner(it[0])} starts or ends with a space", ""),
    "duplicate_sheet_names": lambda it, m: ("Two sheets have the same name", ""),
    "unresolved_variable": lambda it, m: ("A text uses a ${VARIABLE} that has no value", ""),
}

# A whole group of the same kind in one row: n -> title.
MANY = {
    "drill_out_of_range": lambda n, it, m: f"{n} holes are {small_or_big(m)}",
    "microvia_drill_out_of_range": lambda n, it, m: f"{n} microvia holes are {small_or_big(m)}",
    "via_diameter": lambda n, it, m: f"{n} vias are {small_or_big(m)}",
    "annular_width": lambda n, it, m: f"{n} copper rings around holes are {small_or_big(m, 'too thin', 'too wide')}",
    "clearance": lambda n, it, m: f"{n} places where copper is too close together",
    "copper_edge_clearance": lambda n, it, m: f"{n} pieces of copper are too close to the board edge",
    "hole_clearance": lambda n, it, m: f"{n} places where copper is too close to a hole",
    "track_width": lambda n, it, m: f"{n} tracks are {small_or_big(m, 'too thin', 'too wide')}",
    "courtyards_overlap": lambda n, it, m: f"{n} pairs of parts overlap",
    "unconnected_items": lambda n, it, m: f"{n} connections are still to route",
    "silk_overlap": lambda n, it, m: f"{n} places where the silkscreen overlaps itself",
    "silk_over_copper": lambda n, it, m: f"{n} places where the silkscreen is printed over copper",
    "silk_edge_clearance": lambda n, it, m: f"{n} prints are too close to the board edge",
    "lib_footprint_issues": lambda n, it, m: f"{n} footprints can't be checked against your libraries",
    "lib_footprint_mismatch": lambda n, it, m: f"{n} footprints no longer match your library",
    "missing_footprint": lambda n, it, m: f"{n} parts of the schematic are not on the board",
    "extra_footprint": lambda n, it, m: f"{n} parts on the board are not in the schematic",
    "net_conflict": lambda n, it, m: f"{n} pads are joined differently on the board and in the schematic",
    "pin_not_connected": lambda n, it, m: f"{n} pins are not connected",
    "endpoint_off_grid": lambda n, it, m: f"{n} pins or wire ends are off the grid",
    "lib_symbol_issues": lambda n, it, m: f"{n} symbols can't be checked against your symbol libraries",
    "lib_symbol_mismatch": lambda n, it, m: f"{n} symbols no longer match their library",
    "footprint_link_issues": lambda n, it, m: f"{n} footprints are not in your libraries",
    "unannotated": lambda n, it, m: f"{n} parts have no number yet",
    "wire_dangling": lambda n, it, m: f"{n} wires end without connecting to anything",
}


def describe(table, violation, items):
    """(title, detail) of one violation in plain words."""
    m = lengths(violation.get("description"))
    make = table.get(violation.get("type"))
    if make and items:
        try:
            title, detail = make(items, m)
            return title, detail
        except (IndexError, KeyError, TypeError):
            pass
    return title_of(violation.get("description")), ""


# --- rows of the list --------------------------------------------------------------------------

def row(area, kind, blocking, title, detail="", refs=(), ids=(), fps=(), how="", n=1, sheet="", kinds=()):
    refs = sorted({r for r in refs if r}, key=ref_key)
    ids = list(dict.fromkeys(i for i in ids if i))[:MAX_IDS]
    key = hashlib.sha1(json.dumps([area, kind, title, refs, ids[:20]]).encode()).hexdigest()[:12]
    out = {"id": key, "area": area, "kind": kind, "blocking": blocking, "title": title, "detail": detail,
           "refs": refs, "ref": chip(refs, n, kinds), "count": n,
           "select": ids, "footprints": list(dict.fromkeys(f for f in fps if f))[:MAX_IDS]}
    if how:
        out["how"] = how
    if sheet:
        out["sheet"] = sheet
    return out


ITEMS = {"via": "vias", "track": "tracks", "zone": "zones", "pad": "pads", "edge": "edges"}


def chip(refs, n=1, kinds=()):
    """The short tag in front of a row: "TP2 · U1", "J2 · J4 +5", "183 vias"."""
    if not refs:
        kinds = set(kinds)
        if n > 1:
            return f"{n} {ITEMS.get(kinds.pop(), 'places')}" if len(kinds) == 1 else f"{n} places"
        return ""
    if len(refs) <= 2:
        return " · ".join(refs)
    return f"{refs[0]} · {refs[1]} +{len(refs) - 2}"


def violation_rows(area, table, violations, index, refs, blocking_errors=True, sheet_of=None):
    """Rows for a list of KiCad violations: one per violation, or one per kind when a kind
    comes GROUP_AT times or more."""
    by_type = {}
    for v in violations:
        if not isinstance(v, dict) or v.get("severity") not in ("error", "warning"):
            continue
        by_type.setdefault(v.get("type") or title_of(v.get("description")), []).append(v)
    rows = []
    for kind, group in by_type.items():
        error = any(v.get("severity") == "error" for v in group) and blocking_errors
        infos = [[item_info(i, index, refs) for i in v.get("items", [])] for v in group]
        if len(group) >= GROUP_AT:
            all_items = [i for its in infos for i in its]
            m = lengths(group[0].get("description"))
            many = MANY.get(kind)
            title = many(len(group), infos[0], m) if many else None
            if not title:
                first, _ = describe(table, group[0], infos[0])
                title = f"{first}, and {len(group) - 1} more like it"
            detail = group_detail(group) or (_plural_detail(describe(table, group[0], infos[0])[1])
                                             if kind in table else "")
            first = [_not_edge(its).get("kind") for its in infos if its]
            named = all(any(i.get("ref") for i in its) for its in infos)
            rows.append(row(area, kind, error, title, detail,
                            [i.get("ref") for i in all_items] if named else [], [i["uuid"] for i in all_items],
                            [i.get("fp") or index.footprints.get(i.get("ref", "")) for i in all_items],
                            n=len(group), kinds=first))
            continue
        for v, its in zip(group, infos):
            title, detail = describe(table, v, its)
            sheet = (sheet_of or {}).get(id(v), "").strip("/")
            if sheet:
                detail = f"On the {sheet.split('/')[-1]} sheet. {detail}".strip()
            rows.append(row(area, kind, v.get("severity") == "error" and blocking_errors, title, detail,
                            [i.get("ref") for i in its], [i["uuid"] for i in its],
                            [i.get("fp") or index.footprints.get(i.get("ref", "")) for i in its],
                            sheet=(sheet_of or {}).get(id(v), "")))
    return rows


GAPS = {"clearance", "copper_edge_clearance", "edge_clearance", "hole_clearance", "hole_to_hole",
        "courtyard_clearance", "silk_clearance", "silk_overlap", "silk_edge_clearance", "silk_board_edge_clearance",
        "diff_pair_gap_out_of_range"}


def group_detail(group):
    """For a group: the worst value found and the one needed, when they say it."""
    found = [m for m in (lengths(v.get("description")) for v in group) if m]
    if not found or len({need for need, _ in found}) != 1:
        return ""
    need = found[0][0]
    acts = [act for _, act in found]
    if group[0].get("type") in GAPS and all(act < need for act in acts):
        closest = min(acts)
        return (f"Some touch. They need {mm(need)} mm." if closest <= 0
                else f"The closest are {mm(closest)} mm away. They need {mm(need)} mm.")
    if all(act < need for act in acts):
        return f"The smallest is {mm(min(acts))} mm. The rules ask for at least {mm(need)} mm."
    if all(act > need for act in acts):
        return f"The largest is {mm(max(acts))} mm. The rules allow at most {mm(need)} mm."
    return ""


def _plural_detail(detail):
    """The detail of the first one of a group, when it reads right for all of them."""
    return "" if re.search(r"\d", detail) else detail


# --- parts and datasheets ----------------------------------------------------------------------

def blank(v):
    return (v or "").strip() in ("", "~")


def load_bom(path):
    try:
        with open(path, newline="", encoding="utf-8-sig") as f:
            return {r["Reference"]: r for r in csv.DictReader(f) if r.get("Reference")}
    except (OSError, KeyError):
        return None


def bom_changes(base, head):
    """(added, removed, changed) references between two per-part BOMs."""
    fields = [c for c in (next(iter(head.values())).keys() if head else []) if c not in ("Reference", "Datasheet")]
    added = sorted(set(head) - set(base), key=ref_key)
    removed = sorted(set(base) - set(head), key=ref_key)
    changed = sorted((r for r in set(head) & set(base)
                      if any((head[r].get(c) or "").strip() != (base[r].get(c) or "").strip() for c in fields)),
                     key=ref_key)
    return added, removed, changed


def part_rows(head, required, index, check_caps=True):
    """Parts that miss what an order needs (Datasheet apart: it has its own indicator)."""
    groups = [[a.strip() for a in g.split("|") if a.strip()] for g in required.split(",") if g.strip()]
    groups = [g for g in groups if g != ["Datasheet"]]
    no_number, others = [], []
    for ref in sorted(head, key=ref_key):
        r = head[ref]
        if not blank(r.get("DNP")):
            continue
        for g in groups:
            if any(not blank(r.get(f)) for f in g):
                continue
            if set(g) & {"MPN", "LCSC"}:
                no_number.append(ref)
            else:
                others.append((ref, " or ".join(g)))
        if check_caps and re.match(r"C\d", ref) and blank(r.get("Voltage")) \
                and not re.search(r"\d+(\.\d+)?\s*[kK]?V\b", r.get("Value") or ""):
            others.append((ref, "Voltage"))
        if re.fullmatch(r"(?i)\s*(tbd|tbc|\?+|x+|n/?a)\s*", r.get("Value") or ""):
            others.append((ref, "value"))
    rows = []
    fp = index.footprints.get
    if len(no_number) >= GROUP_AT:
        rows.append(row("parts", "no_part_number", False, f"{len(no_number)} parts have no part number",
                        "Add an MPN or an LCSC code to each, so they can be priced and ordered.",
                        no_number, fps=[fp(r) for r in no_number], n=len(no_number)))
    else:
        for ref in no_number:
            rows.append(row("parts", "no_part_number", False, f"{ref} ({_value(head[ref])}) has no part number",
                            "Add an MPN or an LCSC code, so it can be priced and ordered.", [ref], fps=[fp(ref)]))
    for ref, what in others:
        if what == "Voltage":
            title, detail = f"The capacitor {ref} has no voltage rating", "Put the voltage in the value (100nF 50V) or in a Voltage field."
        elif what == "value":
            title, detail = f"The value of {ref} is still to decide", f"It says {_value(head[ref])}."
        else:
            title, detail = f"{ref} has no {what}", f"The {what} field is empty in the schematic."
        rows.append(row("parts", f"missing_{what.lower()}", False, title, detail, [ref], fps=[fp(ref)]))
    return grouped(rows)


GROUPED = {"missing_voltage": ("{n} capacitors have no voltage rating",
                               "Put the voltage in the value (100nF 50V) or in a Voltage field."),
           "missing_value": ("{n} values are still to decide", "")}


def grouped(rows):
    """Rows of the same kind (GROUP_AT or more) as one."""
    out, kinds = [], {}
    for r in rows:
        kinds.setdefault(r["kind"], []).append(r)
    for kind, same in kinds.items():
        if len(same) < GROUP_AT:
            out += same
            continue
        refs = [ref for r in same for ref in r["refs"]]
        title, detail = GROUPED.get(kind, ("{n} parts: " + same[0]["title"], ""))
        out.append(row(same[0]["area"], kind, False, title.format(n=len(same)), detail or same[0]["detail"],
                       refs, fps=[f for r in same for f in r["footprints"]], n=len(same)))
    return out


def _value(r):
    return (r.get("Value") or "").strip() or "no value"


def datasheet_rows(head, found, index, optional=DATASHEET_OPTIONAL, links=None):
    """Parts that need a datasheet and have none (`found`: datasheets.py's result, without
    the links checked), and links found broken the last time they were checked."""
    skip = {p.strip().upper() for p in optional.split(",") if p.strip()}
    links = links or {}
    missing, broken = [], []
    for ref in sorted(head, key=ref_key):
        r = head[ref]
        if not blank(r.get("DNP")) or ref_key(ref)[0].upper() in skip:
            continue
        e = (found or {}).get(ref)
        source = e.get("source") if e else ("none" if blank(r.get("Datasheet")) else "url")
        if source == "none":
            missing.append(ref)
        elif e and e.get("status") == "broken" and e.get("source") == "file":
            broken.append((ref, e.get("why") or "file not found"))
        elif source == "url":
            link = links.get((r.get("Datasheet") or "").strip())
            if link and link.get("status") == "broken":
                broken.append((ref, link.get("why") or "broken link"))
    fp = index.footprints.get
    rows = []
    if len(missing) >= GROUP_AT:
        rows.append(row("datasheets", "no_datasheet", False, f"{len(missing)} parts have no datasheet",
                        "Their Datasheet field is empty in the schematic.", missing, fps=[fp(r) for r in missing],
                        n=len(missing)))
    else:
        for ref in missing:
            rows.append(row("datasheets", "no_datasheet", False, f"{ref} ({_value(head[ref])}) has no datasheet",
                            "The Datasheet field is empty in the schematic.", [ref], fps=[fp(ref)]))
    for ref, why in broken:
        rows.append(row("datasheets", "broken_datasheet", False, f"The datasheet of {ref} can't be opened",
                        f"{why[0].upper() + why[1:]}.", [ref], fps=[fp(ref)]))
    return rows


# --- the whole status ----------------------------------------------------------------------------

def load_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError, TypeError):
        return None


def headline(errors, others, has_board=True):
    if errors:
        text = f"{number(errors)} {plural(errors, 'error')} to fix before you order."
        if others:
            text += f" Plus {number(others, False)} small {plural(others, 'thing')}."
        return text
    if others:
        return f"No errors. {number(others)} small {plural(others, 'thing')} left to tidy up."
    if not has_board:
        return "The schematic is clean. The board has no outline yet."
    return "All clear. Ready to order."


def build(project_dir, name, reports, base_ref="", required="Footprint,Datasheet,MPN|LCSC",
          datasheets=None, links=None, fab=None, optional=DATASHEET_OPTIONAL, check_caps=True):
    """The status of the project from what the quick check wrote to `reports`.

    datasheets: datasheets.py's result without the links checked (or None);
    links: url -> {"status", "why"} of links checked earlier (the cache);
    fab: (profile, drc-fab report) when the board was also checked with a fab's rules."""
    pcb = os.path.join(project_dir, f"{name}.kicad_pcb")
    index = BoardIndex.load(pcb) if os.path.isfile(pcb) else BoardIndex()
    head = load_bom(os.path.join(reports, "bom-parts.csv")) or {}
    refs = set(head) | set(index.footprints)
    erc = load_json(os.path.join(reports, "erc.json"))
    drc = load_json(os.path.join(reports, "drc.json"))
    rows = []
    checked = {"schematic": erc is not None, "board": drc is not None, "sync": drc is not None,
               "parts": bool(head), "datasheets": bool(head)}
    if erc is not None:
        sheet_of, violations = {}, []
        for s in erc.get("sheets", []):
            for v in s.get("violations", []):
                violations.append(v)
                sheet_of[id(v)] = s.get("path", "")
        rows += violation_rows("schematic", ERC, violations, index, refs, sheet_of=sheet_of)
    if drc is not None:
        rows += violation_rows("board", DRC, drc.get("violations", []), index, refs)
        unconnected = [dict(v, severity="error", type="unconnected_items") for v in drc.get("unconnected_items", [])
                       if isinstance(v, dict)]
        rows += violation_rows("board", DRC, unconnected, index, refs)
        rows += violation_rows("sync", DRC, [dict(v, severity=v.get("severity") or "error")
                                             for v in drc.get("schematic_parity", []) if isinstance(v, dict)],
                                 index, refs)
    if fab:
        profile, fab_report = fab
        known = {(v.get("type"), tuple(i.get("uuid") for i in v.get("items", [])))
                 for v in (drc or {}).get("violations", [])}
        new = [dict(v, severity="error") for v in (fab_report or {}).get("violations", [])
               if v.get("severity") in ("error", "warning")
               and (v.get("type"), tuple(i.get("uuid") for i in v.get("items", []))) not in known]
        for r in violation_rows("board", DRC, new, index, refs):
            r["detail"] = (r["detail"] + " " if r["detail"] else "") + f"(The {profile} rules.)"
            r["fab"] = profile
            rows.append(r)
    rows += part_rows(head, required, index, check_caps)
    if "Datasheet" in required:
        rows += datasheet_rows(head, datasheets, index, optional, links)
    rows.sort(key=lambda r: (not r["blocking"], ORDER.index(r["area"])))

    fitted = sum(1 for r in head.values() if blank(r.get("DNP")))
    base = load_bom(os.path.join(reports, "bom-parts-base.csv")) if base_ref else None
    changes = bom_changes(base, head) if base is not None else None
    notes = {
        "schematic": "no issues",
        "board": "no issues" if drc is not None else "no outline yet",
        "sync": "board matches schematic",
        "parts": f"{fitted} fitted",
        "datasheets": "every part that needs one",
    }
    if changes is not None:
        a, r_, c = changes
        notes["parts"] = (f"{fitted}, same as {base_ref}" if not (a or r_ or c) else
                          f"{fitted}; " + ", ".join(x for x in (f"{len(a)} added" if a else "",
                                                                 f"{len(r_)} removed" if r_ else "",
                                                                 f"{len(c)} changed" if c else "") if x)
                          + f" since {base_ref}")
    indicators = []
    for area in AREAS:
        mine = [r for r in rows if r["area"] == area]
        state = ("off" if not checked[area] else "error" if any(r["blocking"] for r in mine)
                 else "todo" if mine else "ok")
        if area == "parts":
            note = notes["parts"] + (f" · {_note(area, mine)}" if mine else "")
        else:
            note = _note(area, mine) if mine else notes[area] if checked[area] else "not checked"
        indicators.append({"id": area, "label": LABELS[area], "state": state, "count": len(mine), "note": note})
    errors = sum(1 for r in rows if r["blocking"])
    return {
        "version": VERSION,
        "state": "done",
        "project": name,
        "project_dir": project_dir,
        "headline": headline(errors, len(rows) - errors, drc is not None),
        "errors": errors,
        "to_tidy": len(rows) - errors,
        "indicators": indicators,
        "items": rows,
        "base": base_ref or None,
        "bom": {"fitted": fitted, "added": changes[0], "removed": changes[1], "changed": changes[2]}
        if changes is not None else {"fitted": fitted},
    }


def _note(area, rows):
    n = len(rows)
    if area == "datasheets":
        missing = [r for r in rows if r["kind"] == "no_datasheet"]
        refs = [ref for r in missing for ref in r["refs"]]
        if len(refs) == 1 and len(rows) == 1:
            return f"{refs[0]} is missing one"
        return f"{len(refs)} missing" + (f", {n - len(missing)} broken" if n > len(missing) else "")
    if area == "sync":
        return f"{n} {plural(n, 'difference')} with the schematic"
    errors = sum(1 for r in rows if r["blocking"])
    if errors:
        return f"{errors} {plural(errors, 'error')}" + (f", {n - errors} to tidy" if n > errors else "")
    return f"{n} to tidy"


def failed(name, message, project_dir=""):
    """The status when the check could not run at all."""
    return {"version": VERSION, "state": "failed", "project": name, "project_dir": project_dir,
            "headline": "The check could not run.", "detail": message, "errors": 0, "to_tidy": 0,
            "indicators": [{"id": a, "label": LABELS[a], "state": "off", "count": 0, "note": "not checked"}
                           for a in AREAS], "items": []}


# --- the order (after `run`: datasheet links, prices, the fab package) ---------------------------

SYMBOLS = {"USD": "$", "EUR": "€", "GBP": "£"}


def order(out, package=None, seconds=None, checked_at=""):
    """What "Prepare the order" found, for the app: the cost per board for each batch, the
    parts short on stock for the largest one, the fab package and the datasheet links.
    Reads what `run` wrote in <out>/reports/. package: `package()`'s result, or None."""
    reports = os.path.join(out, "reports")
    src = load_json(os.path.join(reports, "sourcing.json"))
    result = {"version": VERSION, "state": "done", "checked_at": checked_at, "seconds": seconds}
    if src and src.get("builds"):
        build = src["builds"][0]
        cur = src.get("currency", "USD")
        totals = build.get("totals") or {}
        batches = [{"boards": int(n), "total": t, "each": round(t / int(n), 2)}
                   for n, t in sorted(totals.items(), key=lambda kv: int(kv[0])) if int(n) > 0]
        used = [s["name"] for s in src.get("suppliers", []) if s.get("status") == "ok"]
        no_key = [s["name"] for s in src.get("suppliers", []) if s.get("status") == "no API key"]
        failed_ = [s["name"] for s in src.get("suppliers", []) if str(s.get("status", "")).startswith("failed")]
        short = [{"refs": ", ".join(sorted(x.get("refs", []), key=ref_key)), "mpn": x.get("mpn", ""),
                  "text": stock_text(x), "boards": x.get("boards")} for x in src.get("short", [])]
        unpriced = sorted(set(build.get("unpriceable", [])) | set(build.get("unpriced", [])), key=ref_key)
        result["cost"] = {
            "currency": cur, "symbol": SYMBOLS.get(cur, cur + " "), "batches": batches,
            "priced": build.get("priced"), "fitted": build.get("fitted"), "unpriced": unpriced,
            "suppliers": used, "no_key": no_key, "failed": failed_,
            "note": cost_note(used, no_key, failed_, unpriced),
        }
        result["short"] = short
        result["short_for"] = short[0]["boards"] if short else (batches[-1]["boards"] if batches else None)
    ds = load_json(os.path.join(reports, "datasheets.json"))
    if ds is not None:
        broken = sorted((r for r, e in ds.items() if e.get("status") == "broken"), key=ref_key)
        unchecked = {e.get("target") for e in ds.values() if e.get("status") == "unverified"}
        result["datasheets"] = {"broken": broken, "not_checked": len(unchecked)}
    if package:
        result["package"] = {"zip": package.get("zip"), "name": package.get("name"),
                             "warnings": list(package.get("warnings") or [])}
    return result


def stock_text(x):
    """"JLCPCB has 1 of 200", "LCSC has none"."""
    if not x.get("stock"):
        return f"{x.get('supplier')} has none"
    return f"{x.get('supplier')} has {x['stock']} of {x.get('need')}"


def cost_note(used, no_key, failed_, unpriced):
    parts = []
    if used:
        parts.append("Parts only, with prices from " + " and ".join(used[:2] if len(used) <= 2 else
                                                                   [", ".join(used[:-1]), used[-1]]) + ".")
    else:
        parts.append("No supplier answered: no prices this time.")
    if failed_:
        parts.append(" and ".join(failed_) + " did not answer.")
    if no_key:
        names = ", ".join(no_key[:-1]) + " or " + no_key[-1] if len(no_key) > 1 else no_key[0]
        parts.append(f"Comparing with {names} needs {'their' if len(no_key) > 1 else 'its'} API key.")
    if unpriced:
        shown = ", ".join(unpriced[:6]) + (f" and {len(unpriced) - 6} more" if len(unpriced) > 6 else "")
        parts.append(f"Not priced: {shown}.")
    return " ".join(parts)
