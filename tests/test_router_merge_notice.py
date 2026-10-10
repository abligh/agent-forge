"""Tests for the default rules' merge notice, rule 17
(`router.merge_notice`): which merges are told, to whom, what the
notice says, once per merge, and the engine reading the merges for it.
"""

import datetime
import os
import tempfile
import unittest

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (LEADDEV, MERGER, NOW, OWNER, REVIEWER,
                                     T0, T1, FakeGitHub, issue_node, r,
                                     response)

# Ten minutes before NOW: inside the default 30-minute window.
MERGED_AT = "2026-10-09T13:50:00Z"
UIDEV = "uidevwidgetagent[bot]"


def project(notice=None):
    """The example project, with `router.merge_notice` set to `notice`
    (None: no merge notice)."""
    data = example()
    if notice is not None:
        data["router"] = {"merge_notice": notice}
    return config.Project(data, None)


def configure(test, notice):
    """Configure the engine and the default rules with `notice`, and put
    them back afterwards."""
    r.configure(project(notice))
    test.addCleanup(r.configure, project())


def merged(number, author=LEADDEV, at=MERGED_AT, title=None):
    return {"number": number, "mergedAt": at, "author": {"login": author},
            "title": title if title is not None else "change %d" % number}


def search(*nodes, more=None):
    """One page of the merged search."""
    return {"data": {"search": {
        "pageInfo": {"hasNextPage": more is not None, "endCursor": more},
        "nodes": list(nodes)}}}


def owned(number, owner="LeadDev", body="", created=T0, title=None):
    node = issue_node(number, labels=["Owner:%s" % owner], body=body,
                      created=created)
    node["title"] = title if title is not None else "item %d" % number
    return node


def context(issues=(), merges=(), only=None, when=NOW, pages=(),
            unknown=False):
    """A Context as the engine builds it: through `fetch`, with `merges`
    handed over as `ctx.merged` (None when `unknown`)."""
    prs, got_issues = r.fetch(FakeGitHub(response([], list(issues)),
                                         *pages))
    return router.Context(rules.Roster(project({})), when, prs, got_issues,
                          only=only,
                          merged=None if unknown else list(merges))


class Config(unittest.TestCase):

    def problems(self, notice):
        data = example()
        data["router"] = {"merge_notice": notice}
        return config.check(data)

    def test_a_valid_notice(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems({"window_minutes": 60}), [])

    def test_unknown_keys_and_wrong_types(self):
        self.assertEqual(self.problems({"window": 60}),
                         ["router.merge_notice: unknown key 'window'"])
        self.assertEqual(self.problems({"window_minutes": "60"}), [
            "router.merge_notice.window_minutes must be an integer"])
        self.assertEqual(config.check(dict(example(), router={
            "merge_notice": True})),
            ["router.merge_notice must be an object"])

    def test_window_at_least_one_minute(self):
        self.assertEqual(self.problems({"window_minutes": 0}), [
            "router.merge_notice.window_minutes must be at least 1"])

    def test_beside_the_idle_offer(self):
        data = example()
        data["router"] = {"idle_offer": {"idle_minutes": 0},
                          "merge_notice": {"window_minutes": 0}}
        self.assertEqual(sorted(config.check(data)), [
            "router.idle_offer.idle_minutes must be at least 1",
            "router.merge_notice.window_minutes must be at least 1"])


class Configure(unittest.TestCase):

    def test_off_without_the_key(self):
        configure(self, None)
        self.assertIsNone(rules.MERGE_NOTICE)
        self.assertEqual(rules.merged_window, 0)
        self.assertEqual(rules.sweep(context(merges=[merged(50)])), [])

    def test_defaults(self):
        configure(self, {})
        self.assertEqual(rules.MERGE_NOTICE, {"window_minutes": 30})
        self.assertEqual(rules.merged_window, 30)

    def test_a_window(self):
        configure(self, {"window_minutes": 90})
        self.assertEqual(rules.merged_window, 90)

    def test_the_engine_fills_in_a_module_without_it(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "rules.py"), "w") as handle:
                handle.write("graphql = {}\nRoster = None\n"
                             "def configure(p, m): pass\n"
                             "def decide(i, c): return []\n")
            loaded = router.load_rules("rules.py", root)
        self.assertEqual(loaded.merged_window, 0)


class Notice(unittest.TestCase):

    def setUp(self):
        configure(self, {})

    def notices(self, **ctx):
        return rules.sweep(context(**ctx))

    def test_the_notice(self):
        d, = self.notices(merges=[merged(50, title="the adapter")])
        self.assertEqual((d.item, d.agent, d.rule, d.event),
                         (None, "LeadDev", 17, "#50 " + MERGED_AT))
        self.assertEqual(d.line, '#50 "the adapter" merged at 13:50Z.')
        self.assertIs(d.reminder, router.ONCE)

    def test_a_merge_on_another_day_gives_its_date(self):
        configure(self, {"window_minutes": 24 * 60})
        d, = self.notices(merges=[merged(50, at="2026-10-08T23:50:00Z")])
        self.assertEqual(d.line,
                         '#50 "change 50" merged at 2026-10-08 23:50Z.')

    def test_an_untitled_merge(self):
        d, = self.notices(merges=[merged(50, title="")])
        self.assertEqual(d.line, "#50 merged at 13:50Z.")

    def test_each_agents_own_merge(self):
        got = self.notices(merges=[merged(50), merged(51, author=UIDEV),
                                   merged(52, author=REVIEWER)])
        self.assertEqual([(d.agent, d.event.split()[0]) for d in got],
                         [("LeadDev", "#50"), ("UIDev", "#51"),
                          ("Reviewer", "#52")])

    def test_not_for_the_owner_a_human_or_the_merger(self):
        self.assertEqual(self.notices(merges=[
            merged(50, author=OWNER), merged(51, author="someone"),
            merged(52, author=MERGER), merged(53, author=None)]), [])

    def test_two_merges_by_one_author_are_two_notices(self):
        got = self.notices(merges=[merged(51, at="2026-10-09T13:55:00Z"),
                                   merged(50)])
        self.assertEqual([d.event for d in got],
                         ["#50 " + MERGED_AT, "#51 2026-10-09T13:55:00Z"])

    def test_only_inside_the_window(self):
        edge = "2026-10-09T13:30:00Z"
        got = self.notices(merges=[
            merged(50, at=edge), merged(51, at="2026-10-09T13:29:59Z"),
            merged(52, at="2026-10-09T14:00:01Z"), merged(53, at="bad"),
            merged(54, at=None)])
        self.assertEqual([d.event for d in got], ["#50 " + edge])

    def test_a_wider_window(self):
        configure(self, {"window_minutes": 120})
        got = self.notices(merges=[merged(50, at="2026-10-09T12:10:00Z")])
        self.assertEqual(len(got), 1)

    def test_unknown_merges_say_nothing(self):
        self.assertEqual(self.notices(unknown=True), [])

    def test_nothing_on_a_run_for_some_items(self):
        self.assertEqual(self.notices(merges=[merged(50)],
                                      only=frozenset({50})), [])

    def test_what_it_unblocks(self):
        issues = [owned(30, owner="UIDev", body="Blocked-by: #50"),
                  owned(31, owner="Reviewer", body="Blocked-by: #50\nBlocked-by: #60"),
                  owned(32, body="Blocked-by: #50\nBlocked-by: #61"),
                  owned(33, body="Blocked-by: #49")]
        closed = {"state": "MERGED", "closedAt": MERGED_AT}
        pages = [{"data": {"repository": {
            "n49": closed, "n50": closed, "n60": closed,
            "n61": {"state": "OPEN", "closedAt": None}}}}]
        d, = self.notices(issues=issues, merges=[merged(50)], pages=pages)
        self.assertIn(" It unblocks #30 (UIDev), #31 (Reviewer).", d.line)

    def test_an_unowned_issue_is_named_by_its_author(self):
        node = issue_node(30, author=UIDEV, body="Blocked-by: #50")
        node["title"] = "item 30"
        pages = [{"data": {"repository": {
            "n50": {"state": "MERGED", "closedAt": MERGED_AT}}}}]
        d, = self.notices(issues=[node], merges=[merged(50)], pages=pages)
        self.assertIn(" It unblocks #30 (UIDev).", d.line)

    def test_the_authors_next_item(self):
        issues = [owned(31, created=T1, title="later"),
                  owned(30, title="the next one"),
                  owned(32, owner="UIDev")]
        d, = self.notices(issues=issues, merges=[merged(50)])
        self.assertEqual(d.line, '#50 "change 50" merged at 13:50Z. Your '
                         'oldest item nothing blocks: #30 "the next one".')

    def test_no_next_item_when_all_are_blocked(self):
        issues = [owned(30, body="Blocked-by: #61")]
        pages = [{"data": {"repository": {
            "n61": {"state": "OPEN", "closedAt": None}}}}]
        d, = self.notices(issues=issues, merges=[merged(50)], pages=pages)
        self.assertEqual(d.line, '#50 "change 50" merged at 13:50Z.')

    def test_beside_the_idle_offer(self):
        r.configure(config.Project(dict(example(), router={
            "idle_offer": {}, "merge_notice": {}}), None))
        self.assertIsNotNone(rules.IDLE_OFFER)
        got = self.notices(merges=[merged(50)])
        self.assertEqual([d.rule for d in got], [17])


class FetchMerged(unittest.TestCase):

    def setUp(self):
        configure(self, {})

    def test_the_search_and_every_page(self):
        github = FakeGitHub(search(merged(50), {}, more="c1"),
                            search(merged(51)))
        got = r.fetch_merged(github, NOW - datetime.timedelta(minutes=30))
        self.assertEqual([m["number"] for m in got], [50, 51])
        first, second = github.calls
        self.assertIn("q=repo:example/widget is:pr is:merged "
                      "merged:>=2026-10-09T13:30:00Z", first)
        self.assertTrue(any(a.startswith("query=") and "mergedAt" in a
                            for a in first))
        self.assertNotIn("cursor=c1", first)
        self.assertIn("cursor=c1", second)


class SearchFails(FakeGitHub):
    """Answers the listing, then fails the merged search as gh does."""

    def api(self, *args):
        if any(a.startswith("q=") for a in args):
            self.calls.append(args)
            raise RuntimeError("gh api graphql: HTTP 502")
        return super().api(*args)


class Engine(unittest.TestCase):
    """The notice through forge-router's run: once per merge."""

    def setUp(self):
        configure(self, {})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "router")
        self.lines, self.posts = [], []

    def post(self, session, text):
        self.posts.append((session, text))

    def run_router(self, *search_pages, when=NOW, send=True, only=None,
                   issues=(), github=FakeGitHub):
        self.posts.clear()
        self.lines.clear()
        self.github = github(response([], list(issues)), *search_pages)
        return r.run(self.github, r.RULES.Roster(project({})), self.dir,
                     send, self.post, only, self.lines.append, when=when,
                     remind_s=60)

    def test_told_once_per_merge(self):
        self.assertEqual(self.run_router(search(merged(50))), 0)
        self.assertEqual([s for s, _ in self.posts], ["WidgetLeadDev"])
        self.assertIn('#50 "change 50" merged at 13:50Z.', self.posts[0][1])
        # The next run sees the same merge: not told again, ever.
        self.run_router(search(merged(50)),
                        when=NOW + datetime.timedelta(minutes=2))
        self.run_router(search(merged(50)),
                        when=NOW + datetime.timedelta(minutes=20))
        self.assertEqual(self.posts, [])
        # A new merge is.
        self.run_router(search(merged(50), merged(51, at=pc_iso(NOW))),
                        when=NOW + datetime.timedelta(minutes=22))
        self.assertEqual(len(self.posts), 1)
        self.assertIn("#51", self.posts[0][1])

    def test_a_failed_search_tells_nothing(self):
        self.assertEqual(self.run_router(github=SearchFails), 0)
        self.assertEqual(self.posts, [])
        self.assertIn("NO-MERGED: gh api graphql: HTTP 502; no merge is "
                      "announced", self.lines)

    def test_an_only_run_does_not_search(self):
        self.run_router(only={22})
        self.assertEqual(len(self.github.calls), 1)
        self.assertEqual(self.posts, [])

    def test_a_dry_run_searches_and_sends_nothing(self):
        self.assertEqual(self.run_router(search(merged(50)), send=False), 0)
        self.assertEqual(len(self.github.calls), 2)
        self.assertEqual(self.posts, [])
        self.assertTrue(any("merged at 13:50Z" in l for l in self.lines),
                        self.lines)

    def test_not_searched_for_rules_that_dont_want_it(self):
        configure(self, None)
        self.assertEqual(self.run_router(), 0)
        self.assertEqual(len(self.github.calls), 1)


def pc_iso(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


if __name__ == "__main__":
    unittest.main()
