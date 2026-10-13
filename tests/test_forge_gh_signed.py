"""Tests for bin/forge-gh-signed. gh itself is never run: `execvpe` is
replaced by a recorder."""
import os
import tempfile
import unittest

from agent_forge import github
from tests.helpers import load_tool

tool = load_tool("forge-gh-signed")


class GhSigned(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.body = os.path.join(self.tmp.name, "b.md")
        self.write("Fine.\n\n— Reviewer\n")
        self.env = {"FORGE_AGENT": "Reviewer", "GITHUB_TOKEN": "ambient",
                    "PATH": "/usr/bin"}
        self.execs, self.err = [], []

    def write(self, text):
        with open(self.body, "w") as handle:
            handle.write(text)

    def run_tool(self, *argv, mint=lambda agent: "ghs_" + agent):
        return tool.main(list(argv), self.err.append, self.env, mint,
                         lambda f, a, e: self.execs.append((f, a, e)))

    def test_sends_as_the_agent_s_app(self):
        args = ["pr", "comment", "7", "--body-file", self.body]
        self.run_tool(*args)
        (prog, argv, env), = self.execs
        self.assertEqual((prog, argv), ("gh", ["gh"] + args))
        self.assertEqual(env["GH_TOKEN"], "ghs_Reviewer")
        self.assertNotIn("GITHUB_TOKEN", env)

    def test_the_equals_form(self):
        self.run_tool("issue", "comment", "1", "--body-file=" + self.body)
        self.assertEqual(len(self.execs), 1)

    def test_no_body_file_is_2_whatever_else_is_given(self):
        for args in (["pr", "create", "--fill"], ["issue", "comment", "1",
                                                  "--body", "x"]):
            self.assertEqual(self.run_tool(*args), 2)
        self.assertEqual(self.run_tool("x", "--body-file",
                                       self.body + ".missing"), 2)
        self.assertEqual(self.execs, [])

    def test_unsigned_or_signed_by_another_is_3(self):
        self.write("Fine.\n\n— LeadDev\n")
        self.assertEqual(self.run_tool("x", "--body-file", self.body), 3)
        self.write("Fine. — Reviewer\n")
        self.assertEqual(self.run_tool("x", "--body-file", self.body), 3)
        self.assertEqual(self.execs, [])

    def test_a_mention_is_5_and_not_sent(self):
        self.write("Thanks @LeadDev.\n\n— Reviewer\n")
        self.assertEqual(self.run_tool("pr", "comment", "7", "--body-file",
                                       self.body), 5)
        self.assertEqual(self.execs, [])
        self.assertIn("@LeadDev", self.err[0])
        # In code it is inert, and goes.
        self.write("Never write `@LeadDev`.\n\n— Reviewer\n")
        self.run_tool("pr", "comment", "7", "--body-file", self.body)
        self.assertEqual(len(self.execs), 1)

    def test_no_token_is_4_never_the_ambient_login(self):
        def mint(agent):
            raise github.MintError(3, "refused")
        self.assertEqual(self.run_tool("x", "--body-file", self.body,
                                       mint=mint), 4)
        self.assertEqual(self.execs, [])
        self.assertIn("not sending as the ambient login", self.err[0])

    def test_no_agent_is_2(self):
        del self.env["FORGE_AGENT"]
        self.assertEqual(self.run_tool("x", "--body-file", self.body), 2)


if __name__ == "__main__":
    unittest.main()
