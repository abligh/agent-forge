"""Tests for examples/labels.json and examples/CODEOWNERS: the label set
and code owners docs/getting-started.md tells a new project to copy."""

import json
import re
import unittest
from pathlib import Path

from agent_forge import config
from tests.helpers import example

ROOT = Path(__file__).resolve().parent.parent
LABELS = json.loads((ROOT / "examples" / "labels.json").read_text())


class TestLabels(unittest.TestCase):
    def test_each_label_is_one_github_takes(self):
        for label in LABELS:
            self.assertLessEqual(set(label), {"name", "color", "description", "key"})
            self.assertTrue(label["name"])
            self.assertLessEqual(len(label["name"]), 50, label["name"])
            self.assertRegex(label["color"], r"^[0-9a-f]{6}$")
            self.assertLessEqual(len(label["description"]), 100, label["name"])
            self.assertNotIn("\t", label["description"])

    def test_names_are_unique(self):
        names = [label["name"] for label in LABELS]
        self.assertEqual(len(names), len(set(names)))

    def test_each_key_names_a_labels_entry_the_configuration_takes(self):
        data = example()
        data["labels"].update(
            {label["key"]: label["name"] for label in LABELS if "key" in label})
        self.assertEqual(config.check(data), [])
        for label in LABELS:
            if "key" in label:
                self.assertIn(label["key"], config.LABELS, label["name"])

    def test_the_defaults_are_in_the_set(self):
        names = {label["name"] for label in LABELS}
        self.assertIn("Status:waiting-owner", names)
        self.assertIn("Status:waiting-external", names)


class TestCodeOwners(unittest.TestCase):
    def test_guards_the_configuration_and_itself(self):
        lines = [l.split() for l in (ROOT / "examples" / "CODEOWNERS").read_text().splitlines()
                 if l.strip() and not l.startswith("#")]
        paths = {l[0] for l in lines}
        self.assertTrue({"/.agent-forge/", "/.github/CODEOWNERS"} <= paths)
        for l in lines:
            self.assertTrue(all(re.match(r"^@[\w-]+$", o) for o in l[1:]), l)


if __name__ == "__main__":
    unittest.main()
