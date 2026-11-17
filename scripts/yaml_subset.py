#!/usr/bin/env python3
"""Read the small part of YAML that kicad-hub-requirements.yaml uses, without PyYAML.

KiCad's own Python has no PyYAML, and local mode installs nothing behind the user's back.
The requirements file is a list of entries made of `key: value` lines:

    # a comment
    - id: R1
      text: "Board fits in 60 x 40 mm"
      check: max_board_size
      width: 60
      fields: [MPN, Datasheet]      # or one "- item" per line below the key

optionally under a top-level `requirements:` key. Values are numbers, true/false, null,
plain or quoted text, and lists of those. Anything else YAML allows (nested mappings,
anchors, multi-line text...) is refused with the line number, never guessed.

load(text) returns what PyYAML's safe_load would for such a file.
"""
import re


class SubsetError(ValueError):
    """The file uses YAML this reader does not understand, or is malformed."""


_NUMBER = re.compile(r"[-+]?(\d+\.?\d*([eE][-+]?\d+)?|\.\d+([eE][-+]?\d+)?)$")
_KEY = re.compile(r"([A-Za-z_][A-Za-z0-9_ .-]*?)\s*:(\s|$)")
_BOOLS = {"true": True, "yes": True, "on": True, "false": False, "no": False, "off": False}
_ESCAPES = {"n": "\n", "t": "\t", '"': '"', "\\": "\\", "/": "/", "0": "\0", "r": "\r"}


def load(text):
    lines = []
    for number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[:len(raw) - len(raw.lstrip())]:
            raise SubsetError(f"line {number}: indent with spaces, not tabs")
        body = _strip_comment(raw, number).rstrip()
        if body.strip() and body.strip() != "---":
            lines.append((number, len(body) - len(body.lstrip(" ")), body.strip()))
    if not lines:
        return None
    number, indent, body = lines[0]
    if body.startswith("- ") or body == "-":
        value, rest = _entries(lines, 0, indent)
    else:
        m = _KEY.match(body)
        if not (m and m.group(1) == "requirements" and not body[m.end():].strip()):
            raise SubsetError(f"line {number}: expected a list of requirements (lines starting with '- ') "
                              "or a 'requirements:' key")
        if len(lines) == 1:
            return {"requirements": None}
        entries, rest = _entries(lines, 1, lines[1][1])
        value = {"requirements": entries}
    if rest < len(lines):
        raise SubsetError(f"line {lines[rest][0]}: unexpected indentation or content")
    return value


def _entries(lines, i, indent):
    """A list of mappings whose '- ' sits at `indent`, from lines[i]. Returns (list, next index)."""
    out = []
    while i < len(lines) and lines[i][1] == indent and (lines[i][2].startswith("- ") or lines[i][2] == "-"):
        number, _, body = lines[i]
        entry = {}
        first = body[1:].strip()
        key_indent = None
        if first:
            key_indent = indent + (len(body) - len(body[1:].lstrip()))
            i = _pair(lines, i, first, key_indent, entry)
        else:
            i += 1
        while i < len(lines) and lines[i][1] > indent:
            if key_indent is None:
                key_indent = lines[i][1]
            if lines[i][1] != key_indent:
                raise SubsetError(f"line {lines[i][0]}: keys of one entry must line up")
            i = _pair(lines, i, lines[i][2], key_indent, entry)
        if not entry:
            raise SubsetError(f"line {number}: empty entry")
        out.append(entry)
    return out, i


def _pair(lines, i, body, key_indent, entry):
    """One `key: value` (the value may be a list on the following lines). Returns the next index."""
    number = lines[i][0]
    m = _KEY.match(body)
    if not m:
        raise SubsetError(f"line {number}: expected 'key: value', found {body!r}")
    key, rest = m.group(1), body[m.end():].strip()
    if key in entry:
        raise SubsetError(f"line {number}: '{key}' is given twice")
    i += 1
    if rest:
        entry[key] = _value(rest, number)
        return i
    # nothing after the colon: a list of scalars on the next lines, or null
    items = []
    while i < len(lines) and lines[i][1] > key_indent and (lines[i][2].startswith("- ") or lines[i][2] == "-"):
        item = lines[i][2][1:].strip()
        if _KEY.match(item) and not item.startswith(("'", '"')):
            raise SubsetError(f"line {lines[i][0]}: a list inside an entry can only hold simple values")
        items.append(_scalar(item, lines[i][0]))
        i += 1
    if i < len(lines) and lines[i][1] > key_indent:
        raise SubsetError(f"line {lines[i][0]}: nested mappings are not supported")
    entry[key] = items if items else None
    return i


def _value(text, number):
    if text.startswith("["):
        if not text.endswith("]"):
            raise SubsetError(f"line {number}: a list in brackets must end on the same line")
        return [_scalar(item, number) for item in _split(text[1:-1], number)]
    if text[0] in "{&*|>!%@`":
        raise SubsetError(f"line {number}: {text[0]!r} starts YAML this reader does not understand "
                          "(use plain 'key: value' lines)")
    return _scalar(text, number)


def _split(inner, number):
    """Items of a bracket list, split on commas outside quotes."""
    items, current, quote = [], "", ""
    for ch in inner:
        if quote:
            current += ch
            if ch == quote:
                quote = ""
        elif ch in "'\"":
            quote = ch
            current += ch
        elif ch == ",":
            items.append(current.strip())
            current = ""
        elif ch in "[]{}":
            raise SubsetError(f"line {number}: lists inside lists are not supported")
        else:
            current += ch
    if quote:
        raise SubsetError(f"line {number}: a quote is not closed")
    if current.strip() or items:
        items.append(current.strip())
    return [i for i in items if i != ""] if items != [""] else []


def _scalar(text, number):
    if text == "" or text in ("~", "null", "Null", "NULL"):
        return None
    if text[0] == "'":
        if len(text) < 2 or text[-1] != "'":
            raise SubsetError(f"line {number}: a quote is not closed")
        return text[1:-1].replace("''", "'")
    if text[0] == '"':
        if len(text) < 2 or text[-1] != '"':
            raise SubsetError(f"line {number}: a quote is not closed")
        return re.sub(r"\\(.)", lambda m: _ESCAPES.get(m.group(1), m.group(0)), text[1:-1])
    if text.lower() in _BOOLS and text in (text.lower(), text.capitalize(), text.upper()):
        return _BOOLS[text.lower()]
    if _NUMBER.match(text):
        try:
            return int(text)
        except ValueError:
            return float(text)
    return text


def _strip_comment(line, number):
    """The line without its comment: a '#' at the start or after a space, outside quotes."""
    quote, i = "", 0
    while i < len(line):
        ch = line[i]
        if quote == "'" and ch == "'" and line[i + 1:i + 2] == "'":
            i += 1                                   # '' inside single quotes is a quote character
        elif quote == '"' and ch == "\\":
            i += 1                                   # an escaped character inside double quotes
        elif quote:
            if ch == quote:
                quote = ""
        elif ch in "'\"" and (i == 0 or line[i - 1] in " [,:-"):
            quote = ch
        elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
            return line[:i]
        i += 1
    return line
