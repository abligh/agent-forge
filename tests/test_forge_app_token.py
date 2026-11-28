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

    def installed_on(self, *logins):
        gh = FakeGitHub()
        inner = gh.__call__

        def call(url, token, scheme="Bearer", method="GET", body=None):
            if "/app/installations?" in url:
                gh.calls.append((method, url, scheme, body))
                page = int(url.rsplit("page=", 1)[1])
                chunk = list(logins)[(page - 1) * 100:page * 100]
                return 200, [{"account": {"login": l}} for l in chunk]
            return inner(url, token, scheme, method, body)
        return call

    def test_installations_on_the_one_org_only(self):
        rc = self.run_tool("--installations", "LeadDev", "--org", "Example",
                           gh=self.installed_on("example", "EXAMPLE"))
        self.assertEqual(rc, 0)
        self.assertEqual(self.out, ["LeadDev: 2 installations, all on "
                                    "Example: yes"])

    def test_another_account_says_no_without_naming_it(self):
        rc = self.run_tool("--installations", "LeadDev", "--org", "example",
                           gh=self.installed_on("example", "elsewhere"))
        self.assertEqual(rc, 7)
        self.assertEqual(self.out, ["LeadDev: 2 installations, all on "
                                    "example: no"])
        self.assertNotIn("elsewhere", " ".join(self.out + self.err))

    def test_installations_read_every_page(self):
        logins = ["example"] * 150 + ["elsewhere"]
        rc = self.run_tool("--installations", "LeadDev", "--org", "example",
                           gh=self.installed_on(*logins))
        self.assertEqual(rc, 7)
        self.assertIn("151 installations", self.out[0])

    def test_installations_refused_and_unknown(self):
        self.assertEqual(self.run_tool("--installations", "LeadDev", "--org",
                                       "example",
                                       gh=FakeGitHub(slug="other")), 3)
        self.assertEqual(self.run_tool("--installations", "Nobody", "--org",
                                       "example"), 1)
        self.assertEqual(self.out, [])

    def test_installations_and_org_go_together(self):
        with self.assertRaises(SystemExit):
            self.run_tool("--installations", "LeadDev")
        with self.assertRaises(SystemExit):
            self.run_tool("--list", "--org", "example")

    def test_no_configuration(self):
        self.cfg = os.path.join(self.tmp.name, "missing.json")
        self.assertEqual(self.run_tool("--list"), 2)


if __name__ == "__main__":
    unittest.main()
