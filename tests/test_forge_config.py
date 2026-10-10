"""Tests for bin/forge-config."""
import importlib.machinery
import importlib.util
import json
import os
import shutil
import tempfile
import unittest
from unittest import mock

from tests.helpers import EXAMPLE, ROOT, example

_loader = importlib.machinery.SourceFileLoader(
    "forge_config", str(ROOT / "bin" / "forge-config"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
fc = importlib.util.module_from_spec(_spec)
_loader.exec_module(fc)


class ForgeConfig(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        os.makedirs(os.path.join(self.tmp.name, ".agent-forge"))
        self.path = os.path.join(self.tmp.name, ".agent-forge", "config.json")
        shutil.copy(EXAMPLE, self.path)
        self.out, self.err = [], []

    def run_tool(self, *argv):
        with mock.patch.dict(os.environ, {"FORGE_PROJECT_ROOT": self.tmp.name}):
            os.environ.pop("FORGE_CONFIG", None)
            return fc.main(list(argv), self.out.append, self.err.append)

    def test_check_good(self):
        self.assertEqual(self.run_tool("check"), 0)
        self.assertIn("project widget, example/widget, 5 agent(s), "
                      "2 reviewer(s)", self.out[0])

    def test_check_lists_every_problem(self):
        data = example()
        del data["owner"]
        data["hots"] = "x"
        with open(self.path, "w") as handle:
            json.dump(data, handle)
        self.assertEqual(self.run_tool("check"), 1)
        self.assertEqual(self.err[1:], ["  missing 'owner'",
                                        "  unknown key 'hots'"])

    def test_no_file_is_2(self):
        os.remove(self.path)
        self.assertEqual(self.run_tool("check"), 2)
        self.assertIn("no configuration at", self.err[0])

    def test_get_a_string_bare_and_the_rest_as_json(self):
        self.assertEqual(self.run_tool("get", "repo"), 0)
        self.assertEqual(self.run_tool("get", "agents.Reviewer.reviews"), 0)
        self.assertEqual(self.out, ["example/widget",
                                    '["docs/**", "tools/**", "src/core/**"]'])

    def test_get_a_missing_key_is_1(self):
        self.assertEqual(self.run_tool("get", "agents.Nobody"), 1)

    def test_agents_by_kind(self):
        self.assertEqual(self.run_tool("agents", "--kind", "reviewer"), 0)
        self.assertEqual(self.out, [
            "Reviewer\treviewer\treviewerwidgetagent\tWidgetReviewer",
            "UIReviewer\treviewer\tuireviewerwidgetagent\tWidgetUIReviewer"])

    def test_path(self):
        self.assertEqual(self.run_tool("path"), 0)
        self.assertEqual(self.out, [self.path])


if __name__ == "__main__":
    unittest.main()
