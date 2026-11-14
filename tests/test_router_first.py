"""designs/0054: what the router puts first. Urgent items (an issue the
owner labelled `labels.urgent`, a pull request carrying it or closing
such an issue), then a red nightly's fixes, then bugs (`labels.bug`),
then the rest, oldest first within each.
"""
import unittest

from agent_forge import config
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (
    NOW, OWNER, ROSTER, StateCase, issue_node, pr_node, r)


def urgent_project():
    data = example()
    data["labels"] = dict(data["labels"], urgent="Urgent", bug="bug")
    return config.Project(data, None)


def labelled(node, *events):
    """`node` with `urgentLabels`: (label, actor) LabeledEvents, oldest
    first, as the rules' query returns them."""
    node["urgentLabels"] = {"nodes": [
        {"label": {"name": name}, "actor": {"login": who}}
        for name, who in events]}
    return node


def closing(node, *numbers):
    node["closingIssuesReferences"] = {"nodes": [{"number": n}
                                                 for n in numbers]}
    return node


class Ctx:
    def __init__(self, prs=(), issues=()):
        self.prs = [r.prepare(p, "PullRequest") for p in prs]
        self.issues = [r.prepare(i, "Issue") for i in issues]


class Configured(unittest.TestCase):

    def setUp(self):
        r.configure(urgent_project())
        self.addCleanup(r.configure, config.Project(example(), None))


class Rank(Configured):

    def test_the_owners_urgent_label_counts(self):
        issue = labelled(issue_node(22, labels=["Urgent"]),
                         ("Urgent", OWNER))
        self.assertTrue(rules.owner_marked_urgent(r.prepare(issue, "Issue")))

    def test_an_urgent_label_put_on_by_anyone_else_does_not(self):
        issue = labelled(issue_node(22, labels=["Urgent"]),
                         ("Urgent", OWNER),
                         ("Urgent", "leaddevwidgetagent[bot]"))
        self.assertFalse(rules.owner_marked_urgent(
            r.prepare(issue, "Issue")))

    def test_an_urgent_label_with_no_event_does_not(self):
        self.assertFalse(rules.owner_marked_urgent(
            r.prepare(issue_node(22, labels=["Urgent"]), "Issue")))

    def test_the_tiers(self):
        urgent = labelled(issue_node(20, labels=["Urgent"]),
                          ("Urgent", OWNER))
        bug = issue_node(21, labels=["bug"])
        plain = issue_node(22)
        ctx = Ctx(prs=[closing(pr_node(41), 20), closing(pr_node(42), 21),
                       pr_node(43, labels=["Urgent"]), pr_node(44),
                       pr_node(45)],
                  issues=[urgent, bug, plain])
        self.addCleanup(setattr, r.merger, "NIGHTLY_FIXES", frozenset())
        r.merger.NIGHTLY_FIXES = frozenset({45})
        ranks = {n["number"]: rules.rank(n, ctx)
                 for n in ctx.prs + ctx.issues}
        self.assertEqual(ranks, {20: 0, 21: 2, 22: 3, 41: 0, 42: 2, 43: 0,
                                 44: 3, 45: 1})

    def test_without_the_urgent_label_configured_nothing_is_urgent(self):
        r.configure(config.Project(example(), None))
        issue = labelled(issue_node(22, labels=["Urgent"]),
                         ("Urgent", OWNER))
        ctx = Ctx(issues=[issue])
        self.assertEqual(rules.rank(ctx.issues[0], ctx), 3)

    def test_without_the_bug_label_configured_a_bug_is_not_first(self):
        r.configure(config.Project(example(), None))
        ctx = Ctx(issues=[issue_node(21, labels=["bug"])])
        self.assertEqual(rules.rank(ctx.issues[0], ctx), 3)

    def test_the_query_asks_who_put_labels_on_only_when_configured(self):
        self.assertIn("urgentLabels", rules.graphql["Issue"])
        r.configure(config.Project(example(), None))
        self.assertNotIn("urgentLabels", rules.graphql["Issue"])


class Offer(Configured):

    def test_an_idle_agent_is_offered_urgent_then_bugs_then_the_rest(self):
        mine = ["Owner:LeadDev"]
        issues = [issue_node(20, labels=mine, created="2026-10-01T00:00:00Z"),
                  issue_node(21, labels=mine + ["bug"],
                             created="2026-10-02T00:00:00Z"),
                  labelled(issue_node(22, labels=mine + ["Urgent"],
                                      created="2026-10-03T00:00:00Z"),
                           ("Urgent", OWNER)),
                  issue_node(23, labels=mine + ["bug"],
                             created="2026-10-01T12:00:00Z")]
        ctx = Ctx(issues=issues)
        ctx.roster = ROSTER
        ctx.when = NOW
        actionable, _, _ = rules.owned_issues("LeadDev", ctx)
        self.assertEqual([i["number"] for i in actionable], [22, 23, 21, 20])


class Carry(Configured):

    def test_a_fix_of_an_urgent_issue_is_labelled_urgent(self):
        urgent = labelled(issue_node(20, labels=["Urgent"]),
                          ("Urgent", OWNER))
        ctx = Ctx(prs=[closing(pr_node(41), 20),
                       closing(pr_node(42, labels=["Urgent"]), 20),
                       closing(pr_node(43), 21), pr_node(44)],
                  issues=[urgent, issue_node(21)])
        self.assertEqual([(a.item, a.label) for a in rules.each_run(ctx)],
                         [(41, "Urgent")])

    def test_nothing_is_carried_from_a_label_the_owner_did_not_put_on(self):
        other = labelled(issue_node(20, labels=["Urgent"]),
                         ("Urgent", "leaddevwidgetagent[bot]"))
        ctx = Ctx(prs=[closing(pr_node(41), 20)], issues=[other])
        self.assertEqual(list(rules.each_run(ctx)), [])


class Wakes(StateCase):

    def setUp(self):
        super().setUp()
        r.configure(urgent_project())
        self.addCleanup(r.configure, config.Project(example(), None))
        self.addCleanup(setattr, r.merger, "NIGHTLY_FIXES", frozenset())

    def test_urgent_then_the_nightlys_fixes_then_bugs_then_the_rest(self):
        r.merger.NIGHTLY_FIXES = frozenset({44})
        prs = [pr_node(41), pr_node(42, labels=["bug"]), pr_node(43),
               pr_node(44), pr_node(45, labels=["Urgent"])]
        self.assertEqual(self.run_router(prs), 0)
        self.assertEqual(self.posts, [
            ("WidgetReviewer", "From: Router\nYour turn:\n"
             "#45 needs your review (green at 5b257c8) (urgent: first)\n"
             "#44 needs your review (green at 5b257c8) (fixes the red "
             "nightly: first)\n"
             "#42 needs your review (green at 5b257c8)\n"
             "#41 needs your review (green at 5b257c8)\n"
             "#43 needs your review (green at 5b257c8)\n")])


if __name__ == "__main__":
    unittest.main()
