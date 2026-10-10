"""Tests for agent_forge.rules_default as the engine sees it: the
interface. The rules themselves are tested through bin/forge-router,
in tests/test_forge_router.py."""
import datetime
import unittest

from agent_forge import config, router, rules_default as rules
from tests.helpers import example, load_tool

merger = load_tool("forge-merger")
PROJECT = config.Project(example(), None)
merger.configure(PROJECT)
rules.configure(PROJECT, merger)
ROSTER = rules.Roster(PROJECT)
NOW = datetime.datetime(2026, 10, 9, 14, 0, tzinfo=datetime.timezone.utc)
T1 = "2026-10-09T11:00:00Z"


def issue(number, labels=(), comments=()):
    return {"__typename": "Issue", "number": number,
            "author": {"login": "leaddevwidgetagent"}, "body": "",
            "createdAt": "2026-10-09T10:00:00Z", "lastEditedAt": None,
            "labels": [{"name": l} for l in labels],
            "comments": list(comments), "blockedBy": {}}


def context(*issues):
    return router.Context(ROSTER, NOW, [], list(issues))


class Interface(unittest.TestCase):

    def test_a_turn_is_one_decision(self):
        answer = {"databaseId": 7, "author": {"login": "example-owner"},
                  "createdAt": T1, "body": "yes"}
        item = issue(22, ["Status:waiting-owner"], [answer])
        self.assertEqual(rules.decide(item, context(item)), [
            router.Decision(22, "LeadDev", 10, T1,
                            "#22 example-owner answered (comment 7)")])

    def test_nobodys_says_why_in_suppressed(self):
        item = issue(23, ["Status:waiting-external"])
        self.assertEqual(rules.decide(item, context(item)), [
            router.Decision(23, None, 9, None, None,
                            suppressed="Status:waiting-external")])

    def test_rule_7_is_a_surface(self):
        head = "5b257c8" + "0" * 33
        pr = {"__typename": "PullRequest", "number": 9, "isDraft": False,
              "headRefOid": head, "author": {"login": "reviewerwidgetagent"},
              "labels": [], "files": [], "reviews": [], "comments": [],
              "commits": {"nodes": [{"commit": {
                  "oid": head, "parents": {"totalCount": 1}}}]},
              "headCommit": {"nodes": [{"commit": {"oid": head, "status": {
                  "contexts": [{"context": merger.STATUS_CONTEXT,
                                "state": "SUCCESS", "createdAt": T1,
                                "creator": {"login": "mergerwidgetagent"}}]}}}]}}
        one = config.Project(dict(example(), agents={
            "LeadDev": {}, "Reviewer": {"kind": "reviewer"}}), None)
        ctx = router.Context(rules.Roster(one), NOW, [pr], [])
        self.assertEqual(rules.decide(pr, ctx), [router.Decision(
            9, None, 7, None, None, kind="surface",
            suppressed="green, and Reviewer's own: no second reviewer")])

    def test_no_sweep_and_recheck_decides_again(self):
        self.assertEqual(rules.sweep(context()), [])
        self.assertIs(rules.recheck, rules.decide)

    def test_the_author_is_the_configurations_name(self):
        self.assertEqual(rules.author(issue(1), context()), "LeadDev")

    def test_the_summary_counts_issues_waiting_on_the_owner(self):
        self.assertEqual(rules.summary(context(
            issue(1, ["Status:waiting-owner"]), issue(2))),
            "1 waiting on example-owner")

    def test_what_it_asks_github_for(self):
        self.assertEqual(sorted(rules.graphql), ["Issue", "PullRequest"])
        self.assertEqual([l.name for l in rules.lookups], ["blockers"])


if __name__ == "__main__":
    unittest.main()
