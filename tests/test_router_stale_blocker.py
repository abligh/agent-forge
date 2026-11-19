"""Tests for `router.stale_blocker` (designs/0061): rule 26 asks an
item's owner whether a `Blocked-by:` line naming an open issue is
stale, once per pull request referencing that issue that merged after
the line was written."""
import unittest

from agent_forge import config, rules_default as rules
from tests.helpers import example, load_tool
from tests.test_forge_router import (
    FakeGitHub, ROSTER, issue_node, pr_node, response, review, r)

merger = load_tool("forge-merger")
REVIEWER = "reviewerwidgetagent[bot]"
LEADDEV = "leaddevwidgetagent[bot]"
T0, T1, T2, T3 = ("2026-10-09T10:00:00Z", "2026-10-09T11:00:00Z",
                  "2026-10-09T12:00:00Z", "2026-10-09T13:00:00Z")
OPEN = {"state": "OPEN", "closedAt": None}


def project(on=True):
    data = example()
    if on is not None:
        data.setdefault("router", {})["stale_blocker"] = on
    return config.Project(data, None)


def configure(on):
    p = project(on)
    merger.configure(p)
    rules.configure(p, merger)


def opened(*refs):
    """An open blocker with these (number, mergedAt) merged references."""
    return dict(OPEN, mergedRefs=[{"number": n, "mergedAt": at}
                                  for n, at in refs])


def issue(blocked_by, body="Blocked-by: #7\n"):
    _, issues = r.fetch(FakeGitHub(response(
        [], [issue_node(number=30, labels=("Owner:LeadDev",), body=body,
                        created=T0)])))
    item = issues[0]
    item["blockedBy"] = blocked_by
    return item


def pull(blocked_by, body="Blocked-by: #7\n", **kw):
    node = pr_node(reviews=[review(REVIEWER, "APPROVED", T1)], **kw)
    node["body"], node["createdAt"], node["lastEditedAt"] = body, T0, None
    prs, _ = r.fetch(FakeGitHub(response([node], [])))
    item = prs[0]
    item["blockedBy"] = blocked_by
    return item


class Case(unittest.TestCase):
    ON = True

    def setUp(self):
        configure(self.ON)

    def tearDown(self):
        configure(None)


class Issues(Case):

    def test_a_merge_after_the_line_asks_the_owner(self):
        d = rules.route_issue(issue({7: opened((12, T2))}), ROSTER)
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("LeadDev", 26, T2))
        self.assertIn("#7", d["line"])
        self.assertIn("#12", d["line"])

    def test_the_newest_merge_is_the_event(self):
        d = rules.route_issue(issue({7: opened((12, T1), (13, T3))}), ROSTER)
        self.assertEqual((d["rule"], d["event"]), (26, T3))
        self.assertIn("#13", d["line"])

    def test_a_merge_before_the_line_was_written_says_nothing(self):
        item = issue({7: opened((12, T1))})
        item["lastEditedAt"] = T2
        self.assertNotEqual(rules.route_issue(item, ROSTER).get("rule"), 26)

    def test_no_merged_reference_says_nothing(self):
        d = rules.route_issue(issue({7: opened()}), ROSTER)
        self.assertNotEqual(d.get("rule"), 26)

    def test_a_closed_blocker_is_rule_14s(self):
        item = issue({7: {"state": "CLOSED", "closedAt": T2,
                          "mergedRefs": [{"number": 12, "mergedAt": T2}]}})
        self.assertEqual(rules.route_issue(item, ROSTER)["rule"], 14)


class PullRequests(Case):

    def test_an_approved_blocked_one_asks_its_author(self):
        d = rules.route_pr(pull({7: opened((12, T2))}), ROSTER)
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("LeadDev", 26, T2))

    def test_without_a_merge_it_stays_the_mergers(self):
        d = rules.route_pr(pull({7: opened()}), ROSTER)
        self.assertEqual((d["who"], d["rule"]), (None, 6))

    def test_itself_is_not_a_merge_that_counts(self):
        d = rules.route_pr(pull({7: opened((41, T2))}), ROSTER)
        self.assertEqual(d["rule"], 6)


class Off(Case):
    ON = None

    def test_off_by_default(self):
        d = rules.route_pr(pull({7: opened((12, T2))}), ROSTER)
        self.assertEqual(d["rule"], 6)
        d = rules.route_issue(issue({7: opened((12, T2))}), ROSTER)
        self.assertNotEqual(d.get("rule"), 26)


class Lookup(Case):

    class GitHub:
        def __init__(self, answer):
            self.answer, self.calls = answer, []

        def api(self, *args):
            self.calls.append(args)
            return {"data": {"repository": self.answer}}

    def test_merged_references_are_attached(self):
        item = issue({7: dict(OPEN)})
        github = self.GitHub({"r7": {"timelineItems": {"nodes": [
            {"source": {"number": 12, "mergedAt": T2}},
            {"source": {"number": 14, "mergedAt": None}},
            {"subject": {"number": 15, "mergedAt": T3}},
            {}]}}})
        rules.attach_stale_blockers(github, "o/n", [], [item,
                                    {"number": 7, "blockedBy": {}}])
        self.assertEqual(item["blockedBy"][7]["mergedRefs"],
                         [{"number": 12, "mergedAt": T2},
                          {"number": 15, "mergedAt": T3}])
        self.assertEqual(len(github.calls), 1)

    def test_nothing_is_asked_for_a_blocker_that_is_not_an_open_issue(self):
        item = issue({7: dict(OPEN)})
        github = self.GitHub({})
        rules.attach_stale_blockers(github, "o/n", [], [item])
        self.assertEqual(github.calls, [])

    def test_a_failure_leaves_the_items_alone(self):
        class Broken:
            def api(self, *args):
                raise RuntimeError("gh failed")
        item = issue({7: dict(OPEN)})
        rules.attach_stale_blockers(Broken(), "o/n", [],
                                    [item, {"number": 7, "blockedBy": {}}])
        self.assertNotIn("mergedRefs", item["blockedBy"][7])


class Config(unittest.TestCase):

    def problems(self, value):
        data = example()
        data.setdefault("router", {})["stale_blocker"] = value
        return [p for p in config.check(data) if "stale_blocker" in p]

    def test_a_boolean_is_accepted(self):
        self.assertEqual(self.problems(True), [])
        self.assertEqual(self.problems(False), [])

    def test_anything_else_is_refused(self):
        self.assertTrue(self.problems("yes"))


if __name__ == "__main__":
    unittest.main()
