"""Tests for `router.unreviewed` (designs/0027), rule 19: the paths no
reviewer's globs cover, grouped by area, one issue for each area, each
pull request told once, a daily reminder, and the engine doing it.
"""

import datetime
import json
import os
import pathlib
import tempfile
import unittest

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (NOW, FakeGitHub, issue_node, pr_node,
                                     r, response, status)

DAY = "2026-10-09"
GAPS = {"ignore": [".agent-forge/**"], "depth2": ["crates"]}


def project(gaps=None, triage="Helper"):
    """The example project, with `router.unreviewed` set to `gaps` (None:
    off) and `router.owners.triage`."""
    data = example()
    data["router"] = {"owners": {"triage": triage}}
    if gaps is not None:
        data["router"]["unreviewed"] = gaps
    return config.Project(data, None)


def configure(test, gaps):
    r.configure(project(gaps))
    test.addCleanup(r.configure, config.Project(example(), None))


def pr(number, *paths, draft=False):
    return pr_node(number, draft=draft, files=[(p, 1) for p in paths])


def titled(number, title):
    node = issue_node(number, labels=["Owner:Helper"])
    node["title"] = title
    return node


class GitHub(FakeGitHub):
    """FakeGitHub that also takes writes and creates, numbering what it
    creates from 90."""

    def __init__(self, *pages, refuse=None):
        super().__init__(*pages)
        self.writes, self.created, self.refuse = [], [], refuse or {}

    def write(self, method, path, **fields):
        self.writes.append((method, path, fields))
        why = self.refuse.get(path)
        return (False, why) if why else (True, "")

    def create(self, path, **fields):
        self.created.append((path, fields))
        why = self.refuse.get(path)
        return (None, why) if why else (90 + len(self.created) - 1, "")


class Case(unittest.TestCase):

    def setUp(self):
        configure(self, GAPS)
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def context(self, prs=(), issues=(), when=NOW, only=None):
        got_prs, got_issues = r.fetch(FakeGitHub(response(list(prs),
                                                          list(issues))))
        return router.Context(rules.Roster(project()), when, got_prs,
                              got_issues, only=only, state_dir=self.dir)

    def sweep(self, *prs, issues=(), when=NOW, only=None):
        return rules.sweep(self.context(prs, issues, when, only))

    def state(self, value=None):
        path = pathlib.Path(self.dir, "unreviewed.json")
        if value is not None:
            path.write_text(json.dumps(value))
        return json.loads(path.read_text()) if path.exists() else None


class Config(unittest.TestCase):

    def problems(self, gaps, owners={"triage": "Helper"}):
        data = example()
        data["router"] = {"unreviewed": gaps}
        if owners is not None:
            data["router"]["owners"] = owners
        return config.check(data)

    def test_valid(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems(GAPS), [])

    def test_unknown_keys_and_wrong_types(self):
        self.assertEqual(self.problems({"skip": []}),
                         ["router.unreviewed: unknown key 'skip'"])
        self.assertEqual(self.problems({"ignore": "x/**"}),
                         ["router.unreviewed.ignore must be a list"])
        self.assertEqual(self.problems({"ignore": ["", "x"]}), [
            "router.unreviewed.ignore: each must be a non-empty string"])
        self.assertEqual(self.problems({"depth2": [3]}), [
            "router.unreviewed.depth2: each must be a non-empty string"])
        self.assertEqual(self.problems(True),
                         ["router.unreviewed must be an object"])

    def test_depth2_is_a_top_level_directory(self):
        self.assertEqual(self.problems({"depth2": ["src/lib"]}), [
            "router.unreviewed.depth2: 'src/lib' must be a top-level "
            "directory, with no /"])

    def test_refused_without_a_triage_owner(self):
        why = ("router.unreviewed needs router.owners.triage: its issues "
               "are the triage owner's")
        self.assertEqual(self.problems({}, owners=None), [why])
        self.assertEqual(self.problems({}, owners={}), [why])


class Configure(Case):

    def test_off_without_the_key(self):
        configure(self, None)
        self.assertIsNone(rules.UNREVIEWED)
        self.assertEqual(self.sweep(pr(41, "desktop/a.c")), [])

    def test_defaults(self):
        configure(self, {})
        self.assertEqual(rules.UNREVIEWED, {"ignore": [], "depth2": []})


class Areas(Case):

    def test_the_first_segment_or_two_under_depth2(self):
        self.assertEqual(rules.area_of("desktop/a/b.c"), "desktop/")
        self.assertEqual(rules.area_of("crates/foo/src/lib.rs"),
                         "crates/foo/")
        self.assertEqual(rules.area_of("crates/Cargo.toml"), "crates/")
        self.assertEqual(rules.area_of("Makefile"), "/")

    def test_only_what_no_reviewer_covers_and_nothing_ignored(self):
        ctx = self.context([pr(41, "docs/a.md", "web/x.js", "desktop/a.c",
                               ".agent-forge/c.json", "Makefile",
                               "crates/foo/lib.rs", "src/ui/b.c",
                               "src/core/c.c")])
        self.assertEqual(rules.uncovered(ctx), {
            "desktop/": {41: ["desktop/a.c"]}, "/": {41: ["Makefile"]},
            "crates/foo/": {41: ["crates/foo/lib.rs"]},
            "src/": {41: ["src/ui/b.c"]}})

    def test_a_draft_is_left_out(self):
        self.assertEqual(rules.uncovered(self.context(
            [pr(41, "desktop/a.c", draft=True)])), {})

    def test_the_reviewer_choice_is_unchanged(self):
        # The uncovered lines still count towards the first reviewer.
        ctx = self.context([pr(41, "desktop/a.c", "desktop/b.c",
                               "web/x.js")])
        self.assertEqual(ctx.roster.reviewer_for(ctx.prs[0]), "Reviewer")


class Sweep(Case):

    def test_a_new_area_is_the_triage_owners_turn(self):
        d, = self.sweep(pr(41, "desktop/b.c", "desktop/a.c"),
                        pr(43, "desktop/c.c"), pr(42, "docs/x.md"))
        self.assertEqual((d.item, d.agent, d.rule, d.kind, d.event),
                         (None, "Helper", 19, "turn",
                          "desktop/ #41, #43 " + DAY))
        self.assertEqual(d.line, "No reviewer covers desktop/: #41, #43 "
                         "change it. Give it a reviewer in the "
                         "configuration; its issue is labelled "
                         "Owner:Helper.")
        self.assertIs(d.reminder, router.ONCE)
        self.assertEqual(d.actions, (rules.ReportUnreviewed(
            self.dir, "desktop/", None,
            ((41, ("desktop/a.c", "desktop/b.c")), (43, ("desktop/c.c",))),
            "Owner:Helper", DAY),))

    def test_one_decision_an_area(self):
        decisions = self.sweep(pr(41, "desktop/a.c", "Makefile"))
        self.assertEqual([d.event for d in decisions],
                         ["/ #41 " + DAY, "desktop/ #41 " + DAY])

    def test_nothing_on_an_only_run_or_without_a_state_directory(self):
        self.assertEqual(self.sweep(pr(41, "desktop/a.c"), only={41}), [])
        ctx = self.context([pr(41, "desktop/a.c")])
        ctx.state_dir = None
        self.assertEqual(rules.sweep(ctx), [])

    def test_an_open_issue_gets_the_new_pull_requests(self):
        self.state({"desktop/": {"issue": 60, "prs": [41], "said": DAY}})
        d, = self.sweep(pr(41, "desktop/a.c"), pr(44, "desktop/d.c"),
                        issues=[titled(60, "anything")])
        self.assertEqual((d.agent, d.kind, d.event),
                         (None, "surface", "desktop/ #44 " + DAY))
        self.assertEqual(d.suppressed, "#44 change desktop/, which no "
                         "reviewer covers (#60)")
        self.assertEqual(d.actions[0].issue, 60)
        self.assertEqual(d.actions[0].prs, ((44, ("desktop/d.c",)),))

    def test_found_by_title_without_a_record(self):
        d, = self.sweep(pr(41, "desktop/a.c"), issues=[
            titled(61, "No reviewer covers desktop/ (first seen on #9)"),
            titled(62, "No reviewer covers desktop/x (first seen on #9)")])
        self.assertEqual((d.kind, d.actions[0].issue), ("surface", 61))

    def test_a_closed_issue_means_a_new_one(self):
        self.state({"desktop/": {"issue": 60, "prs": [41], "said": DAY}})
        d, = self.sweep(pr(41, "desktop/a.c"))
        self.assertEqual((d.agent, d.actions[0].issue), ("Helper", None))

    def test_a_reminder_once_a_day(self):
        self.state({"desktop/": {"issue": 60, "prs": [41],
                                 "said": "2026-10-08"}})
        issues = [titled(60, "x")]
        d, = self.sweep(pr(41, "desktop/a.c"), issues=issues)
        self.assertEqual((d.agent, d.kind, d.event, d.suppressed),
                         (None, "surface", "desktop/ " + DAY,
                          "#60: #41 still change desktop/, which no "
                          "reviewer covers"))
        self.assertEqual(d.actions, (rules.RemindUnreviewed(
            self.dir, "desktop/", 60, ((41, ("desktop/a.c",)),), DAY),))
        self.state({"desktop/": {"issue": 60, "prs": [41], "said": DAY}})
        self.assertEqual(self.sweep(pr(41, "desktop/a.c"), issues=issues),
                         [])

    def test_no_reminder_once_nothing_changes_the_area(self):
        self.state({"desktop/": {"issue": 60, "prs": [41],
                                 "said": "2026-10-08"}})
        self.assertEqual(self.sweep(pr(42, "docs/a.md"),
                                    issues=[titled(60, "x")]), [])


class Actions(Case):

    def report(self, issue=None, prs=((41, ("desktop/a.c",)),
                                       (43, ("desktop/c.c",)))):
        return rules.ReportUnreviewed(self.dir, "desktop/", issue, prs,
                                      "Owner:Helper", DAY)

    def test_opens_the_issue_and_tells_each_pull_request(self):
        github = GitHub()
        action = self.report()
        self.assertEqual(action.describe(), "open an issue for desktop/ "
                         "naming #41, #43, and say so on each")
        self.assertEqual(action.run(github, "example/widget"), (True, ""))
        (path, fields), = github.created
        self.assertEqual(path, "repos/example/widget/issues")
        self.assertEqual(fields["title"],
                         "No reviewer covers desktop/ (first seen on #41)")
        self.assertEqual(fields["labels[]"], "Owner:Helper")
        self.assertIn("- #41: `desktop/a.c`\n- #43: `desktop/c.c`\n",
                      fields["body"])
        self.assertTrue(fields["body"].endswith("\n— Merger"))
        self.assertEqual([w[1] for w in github.writes], [
            "repos/example/widget/issues/41/comments",
            "repos/example/widget/issues/43/comments"])
        body = github.writes[0][2]["body"]
        self.assertTrue(body.startswith(
            "Not-a-review: no reviewer's `reviews` globs cover `desktop/` "
            "(`desktop/a.c`), so #90 asks for one."), body)
        self.assertEqual(self.state(), {"desktop/": {
            "issue": 90, "prs": [41, 43], "said": DAY}})

    def test_adds_to_the_open_issue(self):
        self.state({"desktop/": {"issue": 60, "prs": [41], "said": "x"}})
        github = GitHub()
        action = self.report(60, ((44, ("desktop/d.c",)),))
        self.assertEqual(action.describe(),
                         "add #44 to #60, and say so on each")
        self.assertEqual(action.run(github, "example/widget"), (True, ""))
        self.assertEqual(github.created, [])
        self.assertEqual([w[1] for w in github.writes], [
            "repos/example/widget/issues/60/comments",
            "repos/example/widget/issues/44/comments"])
        self.assertEqual(github.writes[0][2]["body"],
                         "Also:\n- #44: `desktop/d.c`\n\n— Merger")
        self.assertEqual(self.state()["desktop/"],
                         {"issue": 60, "prs": [41, 44], "said": DAY})

    def test_a_refused_issue_records_nothing(self):
        github = GitHub(refuse={"repos/example/widget/issues": "HTTP 403"})
        self.assertEqual(self.report().run(github, "example/widget"),
                         (False, "HTTP 403"))
        self.assertEqual(github.writes, [])
        self.assertIsNone(self.state())

    def test_a_refused_comment_leaves_that_pull_request_untold(self):
        github = GitHub(refuse={
            "repos/example/widget/issues/43/comments": "HTTP 502"})
        self.assertEqual(self.report().run(github, "example/widget"),
                         (False, "#43: HTTP 502"))
        self.assertEqual(self.state()["desktop/"]["prs"], [41])

    def test_many_paths_are_cut_short(self):
        paths = tuple("desktop/%02d.c" % i for i in range(23))
        self.assertTrue(rules.path_list(paths).endswith(
            "`desktop/19.c` and 3 more"))

    def test_the_reminder(self):
        self.state({"desktop/": {"issue": 60, "prs": [41], "said": "x"}})
        github = GitHub()
        action = rules.RemindUnreviewed(self.dir, "desktop/", 60,
                                        ((41, ("desktop/a.c",)),), DAY)
        self.assertEqual(action.describe(),
                         "remind #60 that #41 still change desktop/")
        self.assertEqual(action.run(github, "example/widget"), (True, ""))
        (_, path, fields), = github.writes
        self.assertEqual(path, "repos/example/widget/issues/60/comments")
        self.assertIn("- #41: `desktop/a.c`\n", fields["body"])
        self.assertEqual(self.state()["desktop/"],
                         {"issue": 60, "prs": [41], "said": DAY})


class Engine(Case):
    """Rule 19 through forge-router's run."""

    def setUp(self):
        super().setUp()
        self.lines, self.posts = [], []

    def post(self, session, text):
        self.posts.append((session, text))

    def run_router(self, prs, issues=(), when=NOW, send=True):
        self.posts.clear()
        self.lines.clear()
        self.github = GitHub(response(list(prs), list(issues)))
        return r.run(self.github, r.RULES.Roster(project()), self.dir,
                     send, self.post, None, self.lines.append, when=when,
                     remind_s=60)

    def test_opened_once_then_added_to_then_reminded(self):
        red = [status("FAILURE")]
        first = pr_node(41, statuses=red, files=[("desktop/a.c", 1)])
        self.assertEqual(self.run_router([first]), 0)
        self.assertEqual(len(self.github.created), 1)
        self.assertIn("DONE open an issue for desktop/ naming #41, and say "
                      "so on each", self.lines)
        self.assertTrue(any(s == "Helper" and "No reviewer covers desktop/"
                            in text for s, text in self.posts), self.posts)
        issue = titled(90, "No reviewer covers desktop/ (first seen on #41)")
        # The next run, the same day: nothing more.
        later = NOW + datetime.timedelta(minutes=5)
        self.run_router([first], [issue], when=later)
        self.assertEqual((self.github.created, self.github.writes), ([], []))
        # A second pull request is added to it.
        second = pr_node(44, statuses=red, files=[("desktop/d.c", 1)])
        self.run_router([first, second], [issue], when=later)
        self.assertEqual([w[1] for w in self.github.writes], [
            "repos/example/widget/issues/90/comments",
            "repos/example/widget/issues/44/comments"])
        # The next day, one reminder, and only one.
        tomorrow = NOW + datetime.timedelta(days=1)
        self.run_router([first, second], [issue], when=tomorrow)
        self.assertIn("DONE remind #90 that #41, #44 still change desktop/",
                      self.lines)
        self.run_router([first, second], [issue],
                        when=tomorrow + datetime.timedelta(minutes=5))
        self.assertEqual(self.github.writes, [])

    def test_a_dry_run_writes_nothing(self):
        self.assertEqual(self.run_router([pr(41, "desktop/a.c")],
                                         send=False), 0)
        self.assertEqual((self.github.created, self.github.writes), ([], []))
        self.assertIn("WOULD open an issue for desktop/ naming #41, and say "
                      "so on each", self.lines)
        self.assertFalse(os.path.exists(os.path.join(self.dir,
                                                     "unreviewed.json")))


if __name__ == "__main__":
    unittest.main()
