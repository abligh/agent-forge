"""Tests for `router.busy_backlog` (agent-forge #580), rule 28: an
agent is reminded of her own issues she hasn't touched for a while,
whatever her presence, at most `per_day` lists a day and never the same
list twice.
"""

import datetime
import json
import pathlib
import tempfile
import unittest

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (LEADDEV, NOW, FakeGitHub, comment,
                                     issue_node, pr_node, r, response)

OLD = "2026-10-09T01:00:00Z"
RECENT = "2026-10-09T12:00:00Z"
BUSY = {"untouched_hours": 6, "per_day": 2}


def project(busy=None):
    data = example()
    if busy is not None:
        data["router"] = {"busy_backlog": busy}
    return config.Project(data, None)


def owned(number, owner="LeadDev", labels=(), body="", created=OLD,
          comments=()):
    node = issue_node(number, labels=["Owner:%s" % owner] + list(labels),
                      body=body, created=created, comments=comments)
    node["title"] = "item %d" % number
    return node


class Case(unittest.TestCase):

    def setUp(self):
        r.configure(project(BUSY))
        self.addCleanup(r.configure, project())
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def sweep(self, issues, prs=(), when=NOW, only=None, state_dir=True):
        got_prs, got_issues = r.fetch(FakeGitHub(response(list(prs),
                                                          list(issues))))
        return rules.sweep(router.Context(
            rules.Roster(project(BUSY)), when, got_prs, got_issues,
            only=only, state_dir=self.dir if state_dir else None))

    def tell(self, decisions):
        """Do the decisions' actions, as the engine does on a wake."""
        for d in decisions:
            for action in d.actions:
                self.assertEqual(action.run(None, "o/n"), (True, ""))

    def state(self):
        path = pathlib.Path(self.dir, "busy_backlog.json")
        return json.loads(path.read_text()) if path.exists() else None


class Lists(Case):

    def test_untouched_issues_are_listed_oldest_first(self):
        got = self.sweep([owned(31), owned(30)])
        self.assertEqual(len(got), 1)
        d = got[0]
        self.assertEqual((d.item, d.agent, d.rule, d.reminder),
                         (None, "LeadDev", 28, router.ONCE))
        self.assertTrue(d.event.startswith("busy "))
        self.assertEqual(
            d.line, '2 open issues are yours and untouched by you for 6 '
            'hours or more, oldest first: #30 "item 30"; #31 "item 31". '
            'Start one, or say on it why not (a waiting label, a '
            'Blocked-by: or a Defer-until: line).')

    def test_whatever_her_presence(self):
        # No directory at all: the idle offer couldn't run, this does.
        self.assertEqual(len(self.sweep([owned(30)])), 1)

    def test_a_recent_comment_of_hers_is_a_touch(self):
        got = self.sweep([owned(30, comments=[comment(LEADDEV, RECENT)])])
        self.assertEqual(got, [])

    def test_someone_elses_comment_is_not(self):
        got = self.sweep([owned(30, comments=[
            comment("someone-else", RECENT)])])
        self.assertEqual(len(got), 1)

    def test_a_new_issue_is_not_untouched(self):
        self.assertEqual(self.sweep([owned(30, created=RECENT)]), [])

    def test_held_issues_are_left_out(self):
        self.assertEqual(self.sweep([
            owned(30, labels=["Status:waiting-owner"]),
            owned(31, labels=["stats-watch"]),
            owned(32, body="Defer-until: 2026-10-10T07:00Z\n"),
            owned(33, body="Blocked-by: #34\n"), owned(34, owner="UIDev",
                                                       created=RECENT)]), [])

    def test_in_hand_is_left_out(self):
        node = pr_node(41, author=LEADDEV, draft=True)
        node["body"] = "Part of #30"
        self.assertEqual(self.sweep([owned(30)], prs=[node]), [])

    def test_each_agent_her_own(self):
        got = self.sweep([owned(30), owned(31, owner="UIDev")])
        self.assertEqual(sorted(d.agent for d in got), ["LeadDev", "UIDev"])


class Limits(Case):

    def test_never_the_same_list_twice(self):
        got = self.sweep([owned(30)])
        self.tell(got)
        self.assertEqual(self.sweep([owned(30)]), [])
        later = NOW + datetime.timedelta(days=2)
        self.assertEqual(self.sweep([owned(30)], when=later), [])

    def test_a_new_list_is_told(self):
        self.tell(self.sweep([owned(30)]))
        got = self.sweep([owned(30), owned(31)])
        self.assertEqual(len(got), 1)
        self.assertIn("#31", got[0].line)

    def test_at_most_per_day(self):
        self.tell(self.sweep([owned(30)]))
        self.tell(self.sweep([owned(30), owned(31)]))
        self.assertEqual(self.state()["LeadDev"]["count"], 2)
        self.assertEqual(self.sweep([owned(30), owned(31), owned(32)]), [])
        tomorrow = NOW + datetime.timedelta(days=1)
        self.assertEqual(len(self.sweep(
            [owned(30), owned(31), owned(32)], when=tomorrow)), 1)

    def test_nothing_on_an_only_run_or_without_a_state_directory(self):
        self.assertEqual(self.sweep([owned(30)], only=[30]), [])
        self.assertEqual(self.sweep([owned(30)], state_dir=False), [])


class Off(unittest.TestCase):

    def test_off_by_default(self):
        r.configure(project())
        got_prs, got_issues = r.fetch(FakeGitHub(response([], [owned(30)])))
        with tempfile.TemporaryDirectory() as d:
            self.assertEqual(rules.sweep(router.Context(
                rules.Roster(project()), NOW, got_prs, got_issues,
                state_dir=d)), [])


class Config(unittest.TestCase):

    def problems(self, busy):
        data = example()
        data["router"] = {"busy_backlog": busy}
        return [p for p in config.check(data) if "busy_backlog" in p]

    def test_valid(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems(BUSY), [])

    def test_refused(self):
        self.assertTrue(self.problems({"hours": 6}))
        self.assertTrue(self.problems({"per_day": 0}))
        self.assertTrue(self.problems({"untouched_hours": "6"}))
        self.assertTrue(self.problems([]))


if __name__ == "__main__":
    unittest.main()
