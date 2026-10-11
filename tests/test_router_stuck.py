"""Tests for the default rules' stuck pull requests, rules 0a, 0b and
0c (`router.stuck`, designs/0004): a pull request based on a branch
nothing has as its head, one blocked by what it closes, and a draft
left idle with no wait recorded. Each is its author's turn, said once,
and off until configured.
"""

import datetime
import os
import tempfile
import unittest

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (LEADDEV, NOW, OWNER, REVIEWER, T0, T1,
                                     T2, T3, FakeGitHub, comment, pr_node, r,
                                     response, review)

# Two and a half hours before NOW, and an hour before NOW.
IDLE = "2026-10-09T11:30:00Z"
RECENT = "2026-10-09T13:00:00Z"


def project(stuck=None, **labels):
    """The example project, with `router.stuck` set to `stuck` (None:
    no key) and `labels` merged into its labels."""
    data = example()
    if stuck is not None:
        data["router"] = {"stuck": stuck}
    data["labels"].update(labels)
    return config.Project(data, None)


def configure(test, stuck, **labels):
    """Configure the engine and the default rules with `stuck`, and put
    them back afterwards."""
    r.configure(project(stuck, **labels))
    test.addCleanup(r.configure, project())


def node(number=41, base="main", head_ref=None, body="", closes=(),
         created=T0, edited=None, committed=T0, **kw):
    """A pull request node, as `pr_node` builds it, with what the stuck
    rules read: its branches, body, closing references and times."""
    pr = pr_node(number, **kw)
    pr.update({"baseRefName": base,
               "headRefName": head_ref or "topic-%d" % number,
               "body": body, "createdAt": created, "lastEditedAt": edited,
               "closingIssuesReferences": {
                   "nodes": [{"number": n} for n in closes]}})
    pr["headCommit"]["nodes"][-1]["commit"]["committedDate"] = committed
    return pr


def bases_page(*counts):
    """The answer to the bases query: b<i> -> how many open pull
    requests have that head."""
    return {"data": {"repository": {
        "b%d" % i: {"totalCount": n} for i, n in enumerate(counts)}}}


def blockers_page(**found):
    """The answer to the Blocked-by query: n<number> -> its state."""
    return {"data": {"repository": {
        k: {"state": v, "closedAt": T1} for k, v in found.items()}}}


def decide(prs, *pages, when=NOW, github=None):
    """{number: route_pr's turn} for `prs`, fetched as the engine
    fetches them, with `pages` answering the lookups."""
    github = github or FakeGitHub(response(list(prs), []), *pages)
    got, _ = r.fetch(github)
    roster = rules.Roster(project())
    decided = {pr["number"]: rules.route_pr(pr, roster, when) for pr in got}
    decided["calls"] = github.calls
    return decided


class Failing(FakeGitHub):
    """FakeGitHub, but any query but the listing fails."""

    def api(self, *args):
        if self.calls:
            raise RuntimeError("gh api graphql: HTTP 502")
        return super().api(*args)


class Config(unittest.TestCase):

    def problems(self, stuck):
        data = example()
        data["router"] = {"stuck": stuck}
        return config.check(data)

    def test_a_valid_stuck(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems({"retarget": True,
                                        "self_blocked": False,
                                        "draft_hours": 2}), [])

    def test_unknown_keys_and_wrong_types(self):
        self.assertEqual(self.problems({"retarget": "yes", "drafts": 2}), [
            "router.stuck.retarget must be true or false",
            "router.stuck: unknown key 'drafts'"])
        self.assertEqual(self.problems({"self_blocked": 1,
                                        "draft_hours": "2"}), [
            "router.stuck.self_blocked must be true or false",
            "router.stuck.draft_hours must be an integer"])
        self.assertEqual(self.problems({"draft_hours": True}), [
            "router.stuck.draft_hours must be an integer"])
        self.assertEqual(self.problems(True),
                         ["router.stuck must be an object"])

    def test_draft_hours_at_least_one(self):
        self.assertEqual(self.problems({"draft_hours": 0}), [
            "router.stuck.draft_hours must be at least 1"])


class Configure(unittest.TestCase):

    def test_off_without_the_key(self):
        configure(self, None)
        self.assertEqual((rules.RETARGET, rules.SELF_BLOCKED,
                          rules.DRAFT_HOURS), (False, False, None))

    def test_each_key(self):
        configure(self, {"retarget": True, "self_blocked": True,
                         "draft_hours": 3})
        self.assertEqual((rules.RETARGET, rules.SELF_BLOCKED,
                          rules.DRAFT_HOURS), (True, True, 3))
        self.assertEqual(rules.WAITING_PREFIX, "Status:waiting-")

    def test_the_waiting_prefix_follows_the_status_prefix(self):
        configure(self, {}, status_prefix="state/")
        self.assertEqual(rules.WAITING_PREFIX, "state/waiting-")

    def test_it_asks_github_for_what_they_read(self):
        query = rules.graphql["PullRequest"]
        for field in ("baseRefName", "headRefName", "createdAt",
                      "lastEditedAt", "closingIssuesReferences",
                      "committedDate"):
            self.assertIn(field, query)


class Retarget(unittest.TestCase):
    """Rule 0a: based on a branch no open pull request has as its head."""

    def setUp(self):
        configure(self, {"retarget": True})

    def test_a_base_with_no_open_pull_request(self):
        d = decide([node(41, base="topic-40")], bases_page(0))
        turn = d[41]
        self.assertEqual((turn["who"], turn["rule"], turn["event"]),
                         ("LeadDev", "0a", "base topic-40"))
        self.assertEqual(turn["line"],
                         "#41 is based on topic-40, and no open pull request "
                         "has topic-40 as its head: what it was stacked on "
                         "has merged or closed, or never was. Retarget it to "
                         "main, merging main into it first if needed.")
        self.assertIs(turn["reminder"], router.ONCE)
        # The base is passed as a variable, never into the query.
        asked = d["calls"][1]
        self.assertIn("b0=topic-40", asked)
        self.assertNotIn("topic-40", asked[2])

    def test_drafts_and_waiting_ones_too(self):
        d = decide([node(41, base="topic-40", draft=True),
                    node(42, base="topic-40",
                         labels=["Status:waiting-owner"])], bases_page(0))
        self.assertEqual([d[n]["rule"] for n in (41, 42)], ["0a", "0a"])

    def test_a_base_with_an_open_pull_request_in_the_listing(self):
        d = decide([node(41, base="topic-40"), node(40)])
        self.assertNotEqual(d[41]["rule"], "0a")
        self.assertEqual(len(d["calls"]), 1)

    def test_a_base_github_says_has_an_open_pull_request(self):
        # An `--only` run lists just the item: GitHub is asked.
        d = decide([node(41, base="topic-40")], bases_page(1))
        self.assertNotEqual(d[41]["rule"], "0a")
        self.assertEqual(len(d["calls"]), 2)

    def test_the_default_branch_is_never_asked_about(self):
        d = decide([node(41)])
        self.assertNotEqual(d[41]["rule"], "0a")
        self.assertEqual(len(d["calls"]), 1)
        # Nor retargeted, whatever `baseOpen` says.
        pr = dict(node(41), baseOpen=False, __typename="PullRequest")
        self.assertIsNone(rules.stuck_pr(pr, rules.Roster(project()), NOW))

    def test_unknown_wakes_nobody(self):
        github = Failing(response([node(41, base="topic-40")], []))
        d = decide([], github=github)
        self.assertNotEqual(d[41]["rule"], "0a")

    def test_several_bases_in_one_query(self):
        d = decide([node(41, base="topic-40"), node(43, base="topic-39")],
                   bases_page(1, 0))
        # Asked in name order: topic-39 is b0, topic-40 b1.
        self.assertEqual((d[41]["rule"], d[43]["rule"]), ("0a", 8))
        self.assertEqual(len(d["calls"]), 2)

    def test_off(self):
        configure(self, {"self_blocked": True, "draft_hours": 2})
        d = decide([node(41, base="topic-40")])
        self.assertNotEqual(d[41]["rule"], "0a")
        self.assertEqual(len(d["calls"]), 1)

    def test_the_author_who_is_not_an_agent(self):
        d = decide([node(41, base="topic-40", author=OWNER)], bases_page(0))
        self.assertEqual((d[41]["who"], d[41]["rule"]), (OWNER, "0a"))


class SelfBlocked(unittest.TestCase):
    """Rule 0b: a `Blocked-by:` line naming what it closes."""

    BODY = "Fixes the widget.\n\nBlocked-by: #30\n"

    def setUp(self):
        configure(self, {"self_blocked": True})

    def test_blocked_by_what_it_closes(self):
        d = decide([node(41, body=self.BODY, closes=[30], edited=T2)])
        turn = d[41]
        self.assertEqual((turn["who"], turn["rule"], turn["event"]),
                         ("LeadDev", "0b", T2))
        self.assertEqual(turn["line"],
                         "#41 says Blocked-by: #30, which it closes: #30 "
                         "stays open until it merges, so the Merger can never "
                         "take it. Replace the line with `Closes #30`.")
        self.assertIs(turn["reminder"], router.ONCE)

    def test_the_event_is_creation_until_the_body_is_edited(self):
        d = decide([node(41, body=self.BODY, closes=[30], draft=True)])
        self.assertEqual((d[41]["rule"], d[41]["event"]), ("0b", T0))

    def test_in_the_merger_notation(self):
        d = decide([node(41, body="**Blocked-by:** #30\n", closes=[30])])
        self.assertEqual(d[41]["rule"], "0b")

    def test_blocked_by_something_it_does_not_close(self):
        d = decide([node(41, body=self.BODY, closes=[31])])
        self.assertNotEqual(d[41]["rule"], "0b")

    def test_a_line_in_code_does_not_count(self):
        d = decide([node(41, body="```\nBlocked-by: #30\n```\n",
                         closes=[30])])
        self.assertNotEqual(d[41]["rule"], "0b")

    def test_off(self):
        configure(self, {"retarget": True})
        d = decide([node(41, body=self.BODY, closes=[30])])
        self.assertNotEqual(d[41]["rule"], "0b")


class IdleDraft(unittest.TestCase):
    """Rule 0c: a draft with nothing new for `draft_hours` and no wait
    recorded."""

    def setUp(self):
        configure(self, {"draft_hours": 2})

    def draft(self, **kw):
        kw.setdefault("committed", IDLE)
        return node(41, draft=True, **kw)

    def test_an_idle_draft(self):
        d = decide([self.draft()])
        turn = d[41]
        self.assertEqual((turn["who"], turn["rule"], turn["event"]),
                         ("LeadDev", "0c", IDLE))
        self.assertEqual(turn["line"],
                         "#41 has been a draft with nothing new for 2 hours "
                         "(since 11:30Z), and records no wait. Un-draft it, "
                         "or record what it waits for: a Blocked-by: line, a "
                         "Defer-until: line or a Status:waiting-* label.")
        self.assertIs(turn["reminder"], router.ONCE)

    def test_exactly_draft_hours_counts(self):
        d = decide([self.draft()],
                   when=NOW - datetime.timedelta(minutes=30))
        self.assertEqual((d[41]["rule"], d[41]["event"]), ("0c", IDLE))

    def test_younger_than_draft_hours(self):
        d = decide([self.draft()],
                   when=NOW - datetime.timedelta(minutes=31))
        self.assertEqual(d[41]["rule"], 1)

    def test_a_new_commit_a_body_edit_or_the_author_speaking(self):
        for kw in ({"committed": RECENT}, {"edited": RECENT},
                   {"comments": [comment(LEADDEV, RECENT)]},
                   {"reviews": [review(LEADDEV, "COMMENTED", RECENT)]}):
            self.assertEqual(decide([self.draft(**kw)])[41]["rule"], 1, kw)

    def test_someone_else_speaking_is_not_the_authors_activity(self):
        d = decide([self.draft(comments=[comment(REVIEWER, RECENT)])])
        self.assertEqual((d[41]["rule"], d[41]["event"]), ("0c", IDLE))

    def test_the_event_is_the_last_activity(self):
        d = decide([self.draft(committed=T0, edited=T1,
                               comments=[comment(LEADDEV, IDLE)])])
        self.assertEqual(d[41]["event"], IDLE)

    def test_a_waiting_label(self):
        for label in ("Status:waiting-owner", "Status:waiting-review"):
            d = decide([self.draft(labels=[label])])
            self.assertEqual(d[41]["rule"], 1, label)

    def test_a_waiting_label_under_another_prefix(self):
        configure(self, {"draft_hours": 2}, status_prefix="state/")
        self.assertEqual(decide([self.draft(labels=["state/waiting-ci"])])
                         [41]["rule"], 1)
        self.assertEqual(decide([self.draft(labels=["Status:waiting-x"])])
                         [41]["rule"], "0c")

    def test_blocked_by_an_open_item(self):
        body = "Blocked-by: #40\n"
        d = decide([self.draft(body=body), node(40)])
        self.assertEqual(d[41]["rule"], 1)
        # Not in the listing: asked for, and open.
        d = decide([self.draft(body=body)], blockers_page(n40="OPEN"))
        self.assertEqual(d[41]["rule"], 1)

    def test_blocked_by_a_closed_item_is_no_wait(self):
        d = decide([self.draft(body="Blocked-by: #40\n")],
                   blockers_page(n40="MERGED"))
        self.assertEqual(d[41]["rule"], "0c")

    def test_a_defer_until_still_to_come(self):
        d = decide([self.draft(body="Defer-until: 2026-10-10\n")])
        self.assertEqual(d[41]["rule"], 1)
        d = decide([self.draft(comments=[comment(
            REVIEWER, T1, "Defer-until: 2026-10-09T15:00Z")])])
        self.assertEqual(d[41]["rule"], 1)

    def test_a_defer_until_past_or_not_a_time_is_no_wait(self):
        for body in ("Defer-until: 2026-10-09T12:00Z\n",
                     "Defer-until: when it is ready\n"):
            d = decide([self.draft(body=body)])
            self.assertEqual(d[41]["rule"], "0c", body)

    def test_the_newest_defer_until_decides(self):
        d = decide([self.draft(body="Defer-until: 2026-10-10\n",
                               comments=[comment(REVIEWER, T1,
                                                 "Defer-until: 2026-10-01")])])
        self.assertEqual(d[41]["rule"], "0c")

    def test_not_a_draft(self):
        d = decide([node(41, committed=IDLE)])
        self.assertNotEqual(d[41]["rule"], "0c")

    def test_off(self):
        configure(self, {"retarget": True})
        self.assertEqual(decide([self.draft()])[41]["rule"], 1)


class Order(unittest.TestCase):

    def test_the_first_that_matches_decides(self):
        configure(self, {"retarget": True, "self_blocked": True,
                         "draft_hours": 2})
        pr = node(41, base="topic-40", draft=True, committed=IDLE,
                  body="Blocked-by: #30\n", closes=[30])
        # The draft's Blocked-by: is looked up first, then its base.
        self.assertEqual(decide([pr], blockers_page(n30="OPEN"),
                                bases_page(0))[41]["rule"], "0a")
        pr["baseRefName"] = "main"
        self.assertEqual(decide([pr], blockers_page(n30="OPEN"))[41]["rule"],
                         "0b")
        pr["body"] = "Closes #30\n"
        self.assertEqual(decide([pr])[41]["rule"], "0c")


class Engine(unittest.TestCase):
    """Through forge-router's run: each said once for its event, and a
    new event is a new turn."""

    def setUp(self):
        configure(self, {"retarget": True, "self_blocked": True,
                         "draft_hours": 2})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "router")
        self.posts = []

    def post(self, session, text):
        self.posts.append((session, text))

    def run_router(self, prs, *pages, hours=0):
        self.posts.clear()
        github = FakeGitHub(response(list(prs), []), *pages)
        return r.run(github, r.RULES.Roster(project()), self.dir, True,
                     self.post, None, lambda line: None,
                     when=NOW + datetime.timedelta(hours=hours),
                     remind_s=60)

    def test_retarget_said_once_per_base(self):
        self.run_router([node(41, base="topic-40")], bases_page(0))
        self.assertEqual([s for s, _ in self.posts], ["WidgetLeadDev"])
        self.assertIn("#41 is based on topic-40", self.posts[0][1])
        self.run_router([node(41, base="topic-40")], bases_page(0), hours=5)
        self.assertEqual(self.posts, [])
        self.run_router([node(41, base="topic-39")], bases_page(0), hours=6)
        self.assertIn("#41 is based on topic-39", self.posts[0][1])

    def test_self_blocked_said_once_per_edit(self):
        body = "Blocked-by: #30\n"
        self.run_router([node(41, body=body, closes=[30])])
        self.assertIn("#41 says Blocked-by: #30", self.posts[0][1])
        self.run_router([node(41, body=body, closes=[30])], hours=5)
        self.assertEqual(self.posts, [])
        self.run_router([node(41, body=body, closes=[30], edited=T3)],
                        hours=6)
        self.assertEqual(len(self.posts), 1)

    def test_an_idle_draft_asked_once_per_idle_stretch(self):
        self.run_router([node(41, draft=True, committed=IDLE)])
        self.assertIn("#41 has been a draft", self.posts[0][1])
        self.run_router([node(41, draft=True, committed=IDLE)], hours=5)
        self.assertEqual(self.posts, [])
        # A push ends the stretch; the next one, once idle, is asked.
        self.run_router([node(41, draft=True, committed=RECENT)], hours=5)
        self.assertEqual(len(self.posts), 1)
        self.assertIn("for 6 hours (since 13:00Z)", self.posts[0][1])


if __name__ == "__main__":
    unittest.main()
