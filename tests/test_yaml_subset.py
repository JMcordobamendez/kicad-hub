"""Tests of scripts/yaml_subset.py, the reader of the requirements file for a Python without PyYAML."""
import os
import re
import sys
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import yaml_subset  # noqa: E402

try:
    import yaml  # PyYAML, when this Python has it: the reference to agree with
except ImportError:
    yaml = None


def read(*parts):
    with open(os.path.join(HERE, "..", *parts), encoding="utf-8") as f:
        return f.read()


class YamlSubsetTest(unittest.TestCase):
    def test_a_requirements_file(self):
        text = '''
# a comment
- id: R1
  text: "Cost under 15 EUR, at 100 units"   # trailing comment
  check: max_cost
  batch: 100
  max: 12.5
  strict: true
- id: 2
  text: 'It''s a #1 part'
  refs: 'U\\d+'
  fields: [MPN, "Data sheet", 'x,y']
  variant: Lite
  note:
'''
        self.assertEqual(yaml_subset.load(text), [
            {"id": "R1", "text": "Cost under 15 EUR, at 100 units", "check": "max_cost", "batch": 100, "max": 12.5,
             "strict": True},
            {"id": 2, "text": "It's a #1 part", "refs": "U\\d+", "fields": ["MPN", "Data sheet", "x,y"],
             "variant": "Lite", "note": None}])

    def test_block_lists_and_the_requirements_key(self):
        text = "requirements:\n  - id: R1\n    fields:\n      - MPN\n      - Datasheet\n    max: -3\n  -\n    id: R2\n"
        self.assertEqual(yaml_subset.load(text),
                         {"requirements": [{"id": "R1", "fields": ["MPN", "Datasheet"], "max": -3}, {"id": "R2"}]})

    def test_empty_files(self):
        self.assertIsNone(yaml_subset.load(""))
        self.assertIsNone(yaml_subset.load("# only comments\n\n"))
        self.assertEqual(yaml_subset.load("requirements:\n"), {"requirements": None})

    def test_scalars(self):
        got = yaml_subset.load("- a: 1e3\n  b: .5\n  c: No\n  d: ~\n  e: 78xx\n  f: \"tab\\there\"\n  g: 0.10\n  h: []\n")
        self.assertEqual(got, [{"a": 1000.0, "b": 0.5, "c": False, "d": None, "e": "78xx", "f": "tab\there",
                                "g": 0.1, "h": []}])

    def test_what_it_does_not_read_is_refused_with_the_line(self):
        for text, line in (("- id: R1\n  limits:\n    max: 3\n", 3),          # nested mapping
                           ("- id: R1\n  text: |\n    long\n", 2),              # block text
                           ("- id: R1\n  ref: *other\n", 2),                    # alias
                           ("- id: R1\n  opts: {a: 1}\n", 2),                   # flow mapping
                           ("- id: R1\n  text: 'open\n", 2),                    # quote not closed
                           ("- id: R1\n  id: R2\n", 2),                         # key twice
                           ("- id: R1\n   text: x\n  check: y\n", 2),           # keys not lined up
                           ("- id: R1\n\ttext: x\n", 2),                        # tab
                           ("name: board\n", 1),                                # not a list
                           ("- id: R1\n  fields: [a, [b]]\n", 2)):              # list in a list
            with self.assertRaisesRegex(yaml_subset.SubsetError, f"line {line}:", msg=text):
                yaml_subset.load(text)

    def test_the_example_board_file(self):
        got = yaml_subset.load(read("examples", "royalblue54l-feather", "kicad-hub-requirements.yaml"))
        self.assertEqual(len(got), 7)
        self.assertEqual(got[4], {"id": "R5", "text": "Every IC has a footprint", "check": "required_fields",
                                  "refs": "U\\d+", "fields": ["Footprint"]})
        self.assertEqual(got[6]["text"], "Fewer than 10 fitted parts (fails on purpose, the board has far more)")

    def test_the_template_file_with_its_examples_switched_on(self):
        text = re.sub(r"(?m)^# ?( *- id:|  )", r"\1", read("template", "kicad-hub-requirements.yaml"))
        got = yaml_subset.load(text)
        self.assertGreaterEqual(len(got), 10)
        self.assertEqual(got[0]["check"], "max_cost")
        self.assertTrue(all(isinstance(e.get("id"), str) and e.get("check") for e in got))

    @unittest.skipIf(yaml is None, "PyYAML is not installed here")
    def test_same_as_pyyaml(self):
        template = re.sub(r"(?m)^# ?( *- id:|  )", r"\1", read("template", "kicad-hub-requirements.yaml"))
        for text in (read("examples", "royalblue54l-feather", "kicad-hub-requirements.yaml"), template,
                     "requirements:\n  - id: R1\n    fields:\n      - MPN\n    max: 1.50\n    flag: yes\n",
                     "- id: 007\n  text: a:b\n  pattern: '78\\d\\d'\n  when: 2026-01-01x\n  empty:\n",
                     "- id: R1\n  text: has # hash\n  url: http://x.y/#frag\n  q: \"a \\\" b\"\n"):
            self.assertEqual(yaml_subset.load(text), yaml.safe_load(text), text)


if __name__ == "__main__":
    unittest.main()
