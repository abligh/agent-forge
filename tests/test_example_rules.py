"""Tests for examples/rules.py: a project's own rules, loaded as the
engine loads them."""
import os
import unittest

from agent_forge import config, router
from tests import test_forge_router as tr
from tests.helpers import example

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
rules = router.load_rules("examples/rules.py", ROOT)
rules.configure(config.Project(example(), None), tr.r.merger)


def decide(node, mergeable):
    prs, _ = tr.r.fetch(tr.FakeGitHub(tr.response([node], [])))
    prs[0]["mergeable"] = mergeable
    ctx = router.Context(tr.ROSTER, tr.NOW, prs, [])
    return rules.decide(prs[0], ctx)


class ExampleRules(unittest.TestCase):

    def test_it_loads_with_every_part(self):
        for part in ("graphql", "Roster", "configure", "decide", "flatten",
                     "lookups", "sweep", "recheck", "summary"):
            self.assertTrue(hasattr(rules, part), part)
        self.assertIn("mergeable", rules.graphql["PullRequest"])
        # Its recheck is its decide, so the engine re-fetches nothing.
        self.assertIs(rules.recheck, rules.decide)

    def test_a_conflict_is_the_authors_turn(self):
        (d,) = decide(tr.pr_node(41), "CONFLICTING")
        self.assertEqual((d.agent, d.rule, d.event, d.line),
                         ("LeadDev", "conflict", tr.HEAD,
                          "#41 conflicts with the default branch"))

    def test_otherwise_the_default_rules_decide(self):
        (d,) = decide(tr.pr_node(41), "MERGEABLE")
        self.assertEqual((d.agent, d.rule), ("Reviewer", 8))

    def test_drafts_and_waiting_labels_come_first(self):
        for node in (tr.pr_node(41, draft=True),
                     tr.pr_node(41, labels=["Status:waiting-owner"])):
            (d,) = decide(node, "CONFLICTING")
            self.assertEqual((d.agent, d.rule), (None, 1))


if __name__ == "__main__":
    unittest.main()
