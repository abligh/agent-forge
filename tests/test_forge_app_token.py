"""Tests for bin/forge-app-token."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from unittest import mock

from tests.helpers import ROOT, example
from tests.test_github import FakeGitHub, fake_sign

_loader = importlib.machinery.SourceFileLoader(
    "forge_app_token", str(ROOT / "bin" / "forge-app-token"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
tool = importlib.util.module_from_spec(_spec)
_loader.exec_module(tool)


class AppToken(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = example()
        data["key_dir"] = self.tmp.name
        for name in ("LeadDev.pem", "Merger.pem"):
            with open(os.path.join(self.tmp.name, name), "w") as handle:
                handle.write("k\n")
        self.cfg = os.path.join(self.tmp.name, "config.json")
        with open(self.cfg, "w") as handle:
            json.dump(data, handle)
        self.out, self.err = [], []

    def run_tool(self, *argv, gh=None):
        with mock.patch.dict(os.environ, {"FORGE_CONFIG": self.cfg}):
            return tool.main(list(argv), self.out.append, self.err.append,
                             gh or FakeGitHub(), fake_sign)

    def test_the_token_alone_on_stdout(self):
        self.assertEqual(self.run_tool("--token", "LeadDev"), 0)
        self.assertEqual((self.out, self.err), (["ghs_x"], []))

    def test_a_refusal_says_why_without_a_token_and_exits_its_status(self):
        rc = self.run_tool("--token", "LeadDev", gh=FakeGitHub(slug="other"))
        self.assertEqual(rc, 3)
        self.assertEqual(self.out, [])
        self.assertNotIn("ghs_", self.err[0])

    def test_unknown_and_not_made(self):
        self.assertEqual(self.run_tool("--token", "Nobody"), 1)
        self.assertEqual(self.run_tool("--token", "UIReviewer"), 6)

    def test_list_marks_machinery_and_null_ids(self):
        self.assertEqual(self.run_tool("--list"), 0)
        self.assertIn("null", [l for l in self.out if "UIReviewer" in l][0])
        self.assertTrue([l for l in self.out if "Merger" in l][0]
                        .endswith("(machinery)"))

    def test_check_fails_on_any_bad_app(self):
        self.assertEqual(self.run_tool("--check"), 4)
        good = [l for l in self.out if l.split()[0] == "LeadDev"]
        self.assertIn("slug leaddevwidgetagent", good[0])

    def test_no_configuration(self):
        self.cfg = os.path.join(self.tmp.name, "missing.json")
        self.assertEqual(self.run_tool("--list"), 2)


if __name__ == "__main__":
    unittest.main()
