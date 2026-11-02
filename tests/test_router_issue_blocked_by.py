"""Tests for `router.issue_blocked_by`: where an issue's `Blocked-by:`
lines may be. "body", the default, reads the body alone, as the Merger
does; "agents" also reads comments by the project's owner or by any
agent in the configuration, the newest such text deciding, so whoever
owns the issue now can correct a line another wrote."""
import unittest

from agent_forge import config, rules_default as rules
from tests.helpers import example, load_tool

merger = load_tool("forge-merger")
LEADDEV = "leaddevwidgetagent"
REVIEWER = "reviewerwidgetagent"
OWNER = "example-owner"
T1, T2, T3, T4 = ("2026-10-09T11:00:00Z", "2026-10-09T12:00:00Z",
                  "2026-10-09T13:00:00Z", "2026-10-09T13:30:00Z")


def project(issue_blocked_by=None):
    data = example()
    if issue_blocked_by is not None:
        data.setdefault("router", {})["issue_blocked_by"] = issue_blocked_by
    return config.Project(data, None)


def note(login, at, body, id=1):
    return {"databaseId": id, "author": {"login": login}, "createdAt": at,
            "body": body}


def issue(comments=(), body="", blocked_by=None, owner="LeadDev"):
    return {"__typename": "Issue", "number": 30,
            "author": {"login": OWNER}, "body": body,
            "createdAt": "2026-10-09T10:00:00Z", "lastEditedAt": None,
            "labels": [{"name": "Owner:" + owner}],
            "comments": list(comments), "blockedBy": blocked_by or {}}


OPEN = {"state": "OPEN", "closedAt": None}


def closed(at):
    return {"state": "CLOSED", "closedAt": at}


class Case(unittest.TestCase):
    SETTING = "agents"

    def setUp(self):
        self.project = project(self.SETTING)
        merger.configure(self.project)
        rules.configure(self.project, merger)
        self.roster = rules.Roster(self.project)

    def tearDown(self):
        plain = project()
        merger.configure(plain)
        rules.configure(plain, merger)


class BodyOnly(Case):
    SETTING = None

    def test_the_default_reads_the_body_alone(self):
        item = issue([note(LEADDEV, T1, "Blocked-by: #7")], blocked_by={7: OPEN})
        self.assertEqual(rules.blockers_of(item), set())
        self.assertFalse(rules.blocked(item))

    def test_the_body_still_counts(self):
        item = issue(body="Blocked-by: #7\n", blocked_by={7: OPEN})
        self.assertEqual(rules.blockers_of(item), {7})
        self.assertTrue(rules.blocked(item))


class FromComments(Case):

    def test_an_agents_comment_blocks(self):
        item = issue([note(LEADDEV, T1, "Waiting on these.\n\nBlocked-by: #7\n"
                           "Blocked-by: #8")], blocked_by={7: OPEN, 8: OPEN})
        self.assertEqual(rules.issue_blocker_state(item), ({7, 8}, T1))
        self.assertTrue(rules.blocked(item))

    def test_the_owners_comment_blocks(self):
        item = issue([note(OWNER, T1, "Blocked-by: #7")], blocked_by={7: OPEN})
        self.assertEqual(rules.blockers_of(item), {7})

    def test_a_stranger_or_machinery_is_not_read(self):
        item = issue([note("someone-else", T1, "Blocked-by: #7"),
                      note("mergerwidgetagent[bot]", T2, "Blocked-by: #8")],
                     body="Blocked-by: #9\n")
        self.assertEqual(rules.blockers_of(item), {9})

    def test_a_retired_owners_line_is_cleared_by_anyone_on_the_project(self):
        # The issue changed hands: Reviewer clears what LeadDev wrote.
        item = issue([note(LEADDEV, T1, "Blocked-by: #7", id=1),
                      note(REVIEWER, T2, "Blocked-by: none", id=2)],
                     blocked_by={7: OPEN}, owner="Reviewer")
        self.assertEqual(rules.issue_blocker_state(item), (set(), T2))
        self.assertFalse(rules.blocked(item))

    def test_the_newest_text_replaces_earlier_numbers(self):
        item = issue([note(LEADDEV, T2, "Blocked-by: #8", id=2),
                      note(OWNER, T1, "Blocked-by: #7", id=1)],
                     body="Blocked-by: #6\n")
        self.assertEqual(rules.issue_blocker_state(item), ({8}, T2))

    def test_a_comment_without_a_line_changes_nothing(self):
        item = issue([note(LEADDEV, T1, "Blocked-by: #7", id=1),
                      note(OWNER, T2, "Any news?", id=2)])
        self.assertEqual(rules.blockers_of(item), {7})

    def test_a_line_in_code_does_not_count(self):
        item = issue([note(LEADDEV, T1, "```\nBlocked-by: #7\n```\n")])
        self.assertEqual(rules.blockers_of(item), set())

    def test_a_pull_request_reads_its_body_only(self):
        pr = {"__typename": "PullRequest", "number": 31, "headRefOid": "a1",
              "body": "", "comments": [note(LEADDEV, T1, "Blocked-by: #7")],
              "reviews": []}
        self.assertEqual(rules.blockers_of(pr), set())


class Unblocked(Case):
    """Rule 14 with the lines in a comment: the line's own time is when
    it was written."""

    def route(self, item):
        return rules.route_issue(item, self.roster)

    def test_the_last_close_wakes_the_owner(self):
        item = issue([note(LEADDEV, T1, "Blocked-by: #7")],
                     blocked_by={7: closed(T2)})
        d = self.route(item)
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("LeadDev", 14, T2))

    def test_a_line_written_after_the_close_does_not_wake(self):
        item = issue([note(LEADDEV, T3, "Blocked-by: #7")],
                     blocked_by={7: closed(T2)})
        d = self.route(item)
        self.assertNotEqual(d.get("rule"), 14)


class Config(unittest.TestCase):

    def problems(self, value):
        data = example()
        data.setdefault("router", {})["issue_blocked_by"] = value
        return [p for p in config.check(data) if "issue_blocked_by" in p]

    def test_body_and_agents_are_accepted(self):
        self.assertEqual(self.problems("body"), [])
        self.assertEqual(self.problems("agents"), [])

    def test_anything_else_is_refused(self):
        self.assertTrue(self.problems("everywhere"))
        self.assertTrue(self.problems(True))


if __name__ == "__main__":
    unittest.main()
