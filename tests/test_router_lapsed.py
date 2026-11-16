"""Tests for the default rules' reminders that hold while what they say
stays true: rule 24, a `Defer-until:` that has passed with nobody
speaking since, and rule 25, the triage owner's daily list of open
issues nobody owns."""

import datetime
import unittest

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (LEADDEV, NOW, T0, T1, T2, T3,
                                     FakeGitHub, comment, issue_node, r,
                                     response)


def project(owners=None):
    data = example()
    if owners is not None:
        data["router"] = {"owners": owners}
    return config.Project(data, None)


def configure(test, owners=None):
    r.configure(project(owners))
    test.addCleanup(r.configure, project())


def context(issues, when=NOW, owners=None):
    _, got = r.fetch(FakeGitHub(response([], list(issues))))
    return router.Context(rules.Roster(project(owners)), when, [], got)


def owned(number=30, body="", labels=(), comments=(), created=T0,
          title=None):
    node = issue_node(number, labels=["Owner:LeadDev"] + list(labels),
                      body=body, comments=comments, created=created)
    node["title"] = title if title is not None else "item %d" % number
    return node


class DeferralPassed(unittest.TestCase):
    """Rule 24."""

    def setUp(self):
        configure(self)

    def route(self, node, when=NOW):
        ctx = context([node], when)
        return rules.route_issue(ctx.issues[0], ctx.roster, ctx)

    def test_a_passed_deferral_is_the_owners_turn(self):
        d = self.route(owned(body="Defer-until: %s" % T1))
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("LeadDev", 24, T1))
        self.assertEqual(d["line"], "#30's Defer-until (11:00Z) has passed: "
                         "act on it, or set a new one.")
        self.assertEqual(d["reminder"], rules.HOURLY)

    def test_the_same_turn_until_something_changes(self):
        # An hour on, nothing said: the same event, so a reminder.
        later = NOW + datetime.timedelta(hours=1)
        self.assertEqual(self.route(owned(body="Defer-until: %s" % T1),
                                    when=later)["event"], T1)

    def test_not_while_it_is_still_to_come(self):
        d = self.route(owned(body="Defer-until: 2026-10-09T15:00:00Z"))
        self.assertIsNone(d["who"])

    def test_a_comment_since_ends_it(self):
        d = self.route(owned(body="Defer-until: %s" % T1,
                             comments=[comment(LEADDEV, T2)]))
        self.assertIsNone(d["who"])

    def test_a_comment_before_it_does_not(self):
        d = self.route(owned(body="Defer-until: %s" % T2,
                             comments=[comment(LEADDEV, T1)]))
        self.assertEqual(d["rule"], 24)

    def test_a_new_deferral_moves_it(self):
        later = "Defer-until: 2026-10-10T09:00:00Z"
        d = self.route(owned(body="Defer-until: %s" % T1,
                             comments=[comment(LEADDEV, T2, body=later)]))
        self.assertIsNone(d["who"])

    def test_not_with_a_waiting_label(self):
        d = self.route(owned(body="Defer-until: %s" % T1,
                             labels=["Status:waiting-backlog"]))
        self.assertIsNone(d["who"])

    def test_not_without_the_runs_clock(self):
        _, got = r.fetch(FakeGitHub(response(
            [], [owned(body="Defer-until: %s" % T1)])))
        self.assertIsNone(rules.route_issue(
            got[0], rules.Roster(project()))["who"])


class UnownedList(unittest.TestCase):
    """Rule 25."""

    def setUp(self):
        configure(self, {"triage": "LeadDev"})

    def listed(self, issues, when=NOW):
        ctx = context(issues, when, owners={"triage": "LeadDev"})
        return [d for d in rules.sweep(ctx) if d.rule == 25]

    def test_a_daily_list_oldest_first_with_titles(self):
        got = self.listed([
            issue_node(41, created=T2, author="someone"),
            issue_node(40, created=T1, author="someone"),
            owned(42),
            issue_node(43, created=T0, author="someone",
                       labels=["Status:waiting-backlog"])])
        self.assertEqual(got, [router.Decision(
            None, "LeadDev", 25, "unowned 2026-10-09",
            '2 open issues with no owner, oldest first: #40; #41. '
            'Give each an Owner:<Name> label, or a waiting '
            'label.', reminder=router.ONCE)])

    def test_titles_are_named(self):
        node = issue_node(40, author="someone")
        node["title"] = "A bug"
        [got] = self.listed([node])
        self.assertIn('#40 "A bug"', got.line)

    def test_the_next_day_is_a_new_turn(self):
        tomorrow = NOW + datetime.timedelta(days=1)
        [got] = self.listed([issue_node(40, author="someone")], tomorrow)
        self.assertEqual(got.event, "unowned 2026-10-10")

    def test_nothing_when_none(self):
        self.assertEqual(self.listed([owned(42)]), [])

    def test_off_without_triage(self):
        configure(self, {})
        ctx = context([issue_node(40, author="someone")], owners={})
        self.assertEqual([d for d in rules.sweep(ctx) if d.rule == 25], [])

    def test_capped(self):
        got = self.listed([issue_node(n, author="someone")
                           for n in range(100, 140)])
        self.assertIn("40 open issues", got[0].line)
        self.assertIn("; and 10 more.", got[0].line)


if __name__ == "__main__":
    unittest.main()
