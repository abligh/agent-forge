"""Tests for bin/forge-new-body."""
import io
import os
import tempfile
import unittest

from tests.helpers import load_tool

tool = load_tool("forge-new-body")
S = "— Reviewer"


class NewBody(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"FORGE_AGENT": "Reviewer", "TMPDIR": self.tmp.name}

    def run_tool(self, name, body, env=None):
        self.out, self.err = [], []
        return tool.main([name], io.StringIO(body), self.out.append,
                         self.err.append, self.env if env is None else env)

    def test_writes_signed_per_agent_outside_the_tree(self):
        self.assertEqual(self.run_tool("c12", "Looks good.\n"), 0)
        want = os.path.join(self.tmp.name, "agent-forge-bodies", "Reviewer",
                            "c12.md")
        self.assertEqual(self.out, [want])
        with open(want) as handle:
            self.assertEqual(handle.read(), "Looks good.\n\n%s\n" % S)

    def test_a_name_ending_md_is_not_doubled(self):
        self.run_tool("c12.md", "x")
        self.assertTrue(self.out[0].endswith("/c12.md"))

    def test_body_dir_overrides(self):
        env = dict(self.env, FORGE_BODY_DIR=os.path.join(self.tmp.name, "b"))
        self.run_tool("x", "y", env)
        self.assertEqual(self.out, [os.path.join(self.tmp.name, "b", "x.md")])

    def test_two_agents_do_not_collide(self):
        self.run_tool("body", "mine")
        mine = self.out[0]
        self.run_tool("body", "theirs", dict(self.env, FORGE_AGENT="LeadDev"))
        self.assertNotEqual(mine, self.out[0])

    def test_a_mention_is_4(self):
        self.assertEqual(self.run_tool("x", "thanks @LeadDev\n"), 4)
        self.assertIn("1: @LeadDev", self.err[-1])
        self.assertEqual(self.out, [])

    def test_a_backticked_handle_warns_and_writes(self):
        self.assertEqual(self.run_tool("x", "never write `@LeadDev`\n"), 0)
        self.assertIn("backticked handles: `@LeadDev`", self.err[0])

    def test_no_agent_or_bad_usage_is_2(self):
        self.assertEqual(self.run_tool("x", "y", {"TMPDIR": "/t"}), 2)
        self.assertEqual(self.run_tool("../x", "y"), 2)
        self.assertEqual(tool.main([], io.StringIO(""), list().append,
                                   list().append, self.env), 2)

    def test_an_unwritable_directory_is_1(self):
        env = dict(self.env, FORGE_BODY_DIR="/proc/no/such")
        self.assertEqual(self.run_tool("x", "y", env), 1)


if __name__ == "__main__":
    unittest.main()
