"""Group DRC violations by what they break, in whatever language kicad-cli writes.

kicad-cli writes each violation's `description` in the language of the KiCad that runs
it: English in CI, the user's language on a PC (Spanish: "Ancho de anular (regla 'JLC min
annular ring' mínimo 0,1800 mm; real 0,1500 mm)"). So the text is only shown, never
parsed. A violation is grouped by its `type` (`annular_width`, the same in every
language) and by the fab rule it breaks, found by the rule's name: rule names are
written by us in the profile's `.kicad_dru` and KiCad never translates them.
"""
import re

# (rule "name" ...) or (rule name ...), the two forms KiCad reads
RULE = re.compile(r'\(rule\s+(?:"((?:[^"\\]|\\.)*)"|([^\s()"]+))')
# quotes a translation may put around the rule name: 'x' in English and Spanish
QUOTES = (("'", "'"), ('"', '"'), ("‘", "’"), ("“", "”"), ("«", "»"))


def rule_names(dru_text):
    """Names of the rules in the text of a `.kicad_dru` file, in file order."""
    text = "\n".join(line for line in dru_text.splitlines() if not line.lstrip().startswith("#"))
    return [re.sub(r"\\(.)", r"\1", m.group(1)) if m.group(1) is not None else m.group(2)
            for m in RULE.finditer(text)]


def rule_of(description, names):
    """The rule of `names` quoted in `description` (the longest, if several match), else None."""
    for name in sorted({n for n in names if n}, key=len, reverse=True):
        if any(f"{a}{name}{b}" in description for a, b in QUOTES):
            return name
    return None


def group(violations, names=()):
    """[(label, count)], most frequent first. Label: the violation's title as kicad-cli
    wrote it (the description up to its details in brackets), plus the rule's name."""
    groups = {}
    for v in violations:
        text = (v.get("description") or "").strip()
        title = text.split(" (", 1)[0].strip() or v.get("type") or "?"
        rule = rule_of(text, names)
        key = (v.get("type") or title, rule)
        if key not in groups:
            groups[key] = [f"{title}: {rule}" if rule else title, 0]
        groups[key][1] += 1
    counts = {}
    for label, n in groups.values():  # two kinds with the same title count as one row
        counts[label] = counts.get(label, 0) + n
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))
