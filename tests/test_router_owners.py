"""Tests for `router.owners` (designs/0008): one reading of whose an
issue is (the label's agent, else an agent author, else nobody), and
rule 18, an unowned issue as the triage agent's turn."""

import unittest

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (LEADDEV, NOW, OWNER, REVIEWER, T0, T1,
                                     T2, T3, FakeGitHub, comment, issue_node,
                                     r, response)


def project(owners=None):
    data = example()
    if owners is not None:
        data["router"] = {"owners": owners}
    return config.Project(data, None)


def configure(test, owners):
    r.configure(project(owners))
    test.addCleanup(r.configure, project())


def labelled(name, at):
    return {"createdAt": at, "label": {"name": name}}


def decide(node):
    _, issues = r.fetch(FakeGitHub(response([], [node])))
    return rules.route_issue(issues[0], rules.Roster(project()))


class Config(unittest.TestCase):

    def problems(self, owners):
        data = example()
        data["router"] = {"owners": owners}
        return config.check(data)

    def test_valid(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems({"triage": "LeadDev"}), [])

    def test_triage_must_be_an_agent(self):
        self.assertEqual(self.problems({"triage": "Nobody"}),
                         ["router.owners.triage: 'Nobody' is not an agent"])
        self.assertEqual(self.problems({"plan": "LeadDev"}),
                         ["router.owners: unknown key 'plan'"])

    def test_the_label_events_are_fetched_only_when_on(self):
        self.assertNotIn("ownerLabels", rules.graphql["Issue"])
        configure(self, {})
        self.assertIn("ownerLabels", rules.graphql["Issue"])


class Off(unittest.TestCase):

    def test_an_unlabelled_agent_issue_is_unchanged(self):
        # Rule 12 needs the label without `router.owners`.
        d = decide(issue_node(comments=[comment(LEADDEV, T1),
                                        comment(REVIEWER, T2)]))
        self.assertIsNone(d["who"])


class Reading(unittest.TestCase):

    def setUp(self):
        configure(self, {"triage": "Reviewer"})
        self.roster = rules.Roster(project())

    def owner(self, **kw):
        _, issues = r.fetch(FakeGitHub(response([], [issue_node(**kw)])))
        return rules.issue_owner(issues[0], self.roster)

    def test_the_label_wins(self):
        self.assertEqual(self.owner(labels=["Owner:Reviewer"]), "Reviewer")

    def test_else_an_agent_author(self):
        self.assertEqual(self.owner(), "LeadDev")

    def test_a_label_naming_nobody_is_passed_over(self):
        self.assertEqual(self.owner(labels=["Owner:Gone"]), "LeadDev")
        self.assertIsNone(self.owner(author=OWNER, labels=["Owner:Gone"]))

    def test_neither_the_owner_nor_machinery_owns_by_filing(self):
        self.assertIsNone(self.owner(author=OWNER))
        self.assertIsNone(self.owner(author="mergerwidgetagent"))


class Rules(unittest.TestCase):

    def setUp(self):
        configure(self, {"triage": "Reviewer"})

    def test_12_a_comment_on_an_agents_unlabelled_issue_wakes_it(self):
        d = decide(issue_node(comments=[comment(LEADDEV, T1),
                                        comment(REVIEWER, T2, cid=8)]))
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("LeadDev", 12, T2))

    def test_13_never_applies_the_owners_answer_is_rule_12(self):
        d = decide(issue_node(comments=[comment(LEADDEV, T1),
                                        comment(OWNER, T2)]))
        self.assertEqual((d["who"], d["rule"]), ("LeadDev", 12))

    def test_18_an_issue_the_owner_filed_goes_to_triage(self):
        d = decide(issue_node(number=30, author=OWNER, created=T1))
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("Reviewer", 18, T1))
        self.assertEqual(d["line"],
                         "#30 has no owner: label it Owner:<Name>")

    def test_18_a_label_naming_nobody_says_so(self):
        d = decide(issue_node(number=30, author=OWNER,
                              labels=["Owner:Gone"]))
        self.assertEqual(d["line"], "#30 its Owner:Gone label names "
                                    "nobody: label it Owner:<Name>")

    def test_18_a_label_taken_off_asks_again(self):
        node = issue_node(author=OWNER, created=T0)
        node["ownerLabels"] = {"nodes": [
            labelled("Owner:LeadDev", T1), labelled("bug", T3),
            labelled("Owner:LeadDev", T2)]}
        self.assertEqual(decide(node)["event"], T2)

    def test_18_not_when_waiting_on_someone(self):
        for label, rule in (("Status:waiting-external", 9),
                            ("Status:waiting-owner", 11)):
            d = decide(issue_node(author=OWNER, labels=[label]))
            self.assertEqual((d["who"], d["rule"]), (None, rule))

    def test_18_surfaced_without_triage(self):
        configure(self, {})
        d = decide(issue_node(author=OWNER))
        self.assertEqual((d["who"], d["rule"], d["kind"]),
                         (None, 18, "surface"))


class IdleOffer(unittest.TestCase):

    def test_15_offers_an_agents_unlabelled_issue(self):
        configure(self, {})
        _, issues = r.fetch(FakeGitHub(response([], [
            issue_node(30), issue_node(31, author=OWNER)])))
        ctx = router.Context(rules.Roster(project()), NOW, [], issues)
        actionable, _, _ = rules.owned_issues("LeadDev", ctx)
        self.assertEqual([i["number"] for i in actionable], [30])


if __name__ == "__main__":
    unittest.main()
