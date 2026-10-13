"""Tests for the default rules' idle offer, rules 15 and 16
(`router.idle_offer`): who is idle by the relay's directory, what an
agent can act on, one offer per idle period, the report of a developer
with nothing to do, and the engine reading the directory for it.
"""

import datetime
import io
import json
import os
import tempfile
import unittest
import urllib.error
from unittest import mock

from agent_forge import config, router
from agent_forge import rules_default as rules
from tests.helpers import example
from tests.test_forge_router import (LEADDEV, NOW, REVIEWER, T0, T1, T2,
                                     FakeGitHub, comment, issue_node, pr_node,
                                     r, response)

UIDEV = "uidevwidgetagent[bot]"
# Idle since 13:30Z: half an hour before NOW, past the default 10 minutes.
IDLE = "2026-10-09T13:30:00Z"
LATER_IDLE = "2026-10-09T15:00:00Z"


def project(offer=None):
    """The example project, with `router.idle_offer` set to `offer`
    (None: no idle offer)."""
    data = example()
    if offer is not None:
        data["router"] = {"idle_offer": offer}
    return config.Project(data, None)


def configure(test, offer):
    """Configure the engine and the default rules with `offer`, and put
    them back afterwards."""
    r.configure(project(offer))
    test.addCleanup(r.configure, project())


def entry(name, status="idle", since=IDLE):
    return {"name": name, "nick": name, "status": status,
            "idle_since": since if status == "idle" else None}


def directory(*entries, complete=True):
    return {"room": "everyone@conference.example.org", "complete": complete,
            "agents": list(entries)}


def owned(number, owner="LeadDev", labels=(), body="", created=T0,
          title=None, comments=()):
    node = issue_node(number, labels=["Owner:%s" % owner] + list(labels),
                      body=body, created=created, comments=comments)
    node["title"] = title if title is not None else "item %d" % number
    return node


def pr(number, author=LEADDEV, body="", closes=(), **more):
    node = pr_node(number, author=author, draft=True, **more)
    node["body"] = body
    node["closingIssuesReferences"] = {"nodes": [{"number": n}
                                                 for n in closes]}
    return node


def context(prs=(), issues=(), listing=None, only=None, when=NOW, pages=()):
    """A Context as the engine builds it: through `fetch`."""
    got_prs, got_issues = r.fetch(FakeGitHub(
        response(list(prs), list(issues)), *pages))
    return router.Context(rules.Roster(project({})), when, got_prs,
                          got_issues, only=only, directory=listing)


class Config(unittest.TestCase):

    def problems(self, offer):
        data = example()
        data["router"] = {"idle_offer": offer}
        return config.check(data)

    def test_a_valid_offer(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems(
            {"idle_minutes": 10, "report_to": "LeadDev"}), [])

    def test_unknown_keys_and_wrong_types(self):
        self.assertEqual(self.problems({"idle": 10}),
                         ["router.idle_offer: unknown key 'idle'"])
        self.assertEqual(self.problems({"idle_minutes": "10"}),
                         ["router.idle_offer.idle_minutes must be an integer"])
        self.assertEqual(config.check(dict(example(), router={
            "idle_offer": True})), ["router.idle_offer must be an object"])

    def test_idle_minutes_at_least_one(self):
        self.assertEqual(self.problems({"idle_minutes": 0}),
                         ["router.idle_offer.idle_minutes must be at least 1"])

    def test_report_to_is_an_agent(self):
        self.assertEqual(self.problems({"report_to": "Merger"}),
                         ["router.idle_offer.report_to: 'Merger' is not an "
                          "agent"])


class Configure(unittest.TestCase):

    def test_off_without_the_key(self):
        configure(self, None)
        self.assertIsNone(rules.IDLE_OFFER)
        self.assertFalse(rules.wants_directory)
        self.assertEqual(rules.sweep(context(
            issues=[owned(30)], listing=directory(entry("WidgetLeadDev")))),
            [])

    def test_defaults(self):
        configure(self, {})
        self.assertEqual(rules.IDLE_OFFER,
                         {"idle_minutes": 10, "report_to": None})
        self.assertTrue(rules.wants_directory)

    def test_the_rules_load_with_it(self):
        configure(self, {"report_to": "LeadDev"})
        self.assertTrue(r.RULES.wants_directory)


class Idle(unittest.TestCase):

    def setUp(self):
        configure(self, {"idle_minutes": 10})

    def idle(self, *entries, **more):
        return rules.idle_agents(context(listing=directory(*entries, **more)))

    def test_idle_long_enough(self):
        self.assertEqual(self.idle(entry("WidgetLeadDev")),
                         {"LeadDev": IDLE})

    def test_exactly_idle_minutes_counts(self):
        self.assertEqual(self.idle(entry("WidgetLeadDev",
                                         since="2026-10-09T13:50:00Z")),
                         {"LeadDev": "2026-10-09T13:50:00Z"})

    def test_idle_too_briefly(self):
        self.assertEqual(self.idle(entry("WidgetLeadDev",
                                         since="2026-10-09T13:55:00Z")), {})

    def test_busy_or_other_is_not_idle(self):
        self.assertEqual(self.idle(entry("WidgetLeadDev", "busy"),
                                   entry("WidgetUIDev", "other")), {})

    def test_idle_with_no_or_a_bad_time_is_not_idle(self):
        self.assertEqual(self.idle(entry("WidgetLeadDev", since=None),
                                   entry("WidgetUIDev", since="13:30")), {})

    def test_an_incomplete_or_unknown_directory_makes_nobody_idle(self):
        self.assertEqual(self.idle(entry("WidgetLeadDev"), complete=False), {})
        for listing in (None, {}, {"complete": True}, "idle"):
            self.assertEqual(rules.idle_agents(context(listing=listing)), {})

    def test_by_session_name_and_others_left_out(self):
        self.assertEqual(self.idle(entry("Helper"), entry("LeadDev"),
                                   entry("WidgetMerger"), entry("alice")),
                         {"Helper": IDLE})

    def test_a_twin_idle_makes_her_idle_since_the_longest_idle(self):
        data = example()
        data["agents"]["LeadDev"]["twins"] = ["WidgetLeadDev2"]
        twin = rules.Roster(config.Project(data, None))
        listing = directory(entry("WidgetLeadDev", "busy"),
                            entry("WidgetLeadDev2"))
        got = rules.idle_agents(router.Context(twin, NOW, [], [],
                                               directory=listing))
        self.assertEqual(got, {"LeadDev": IDLE})
        listing = directory(entry("WidgetLeadDev", since=T1),
                            entry("WidgetLeadDev2"))
        got = rules.idle_agents(router.Context(twin, NOW, [], [],
                                               directory=listing))
        self.assertEqual(got, {"LeadDev": min(T1, IDLE)})
        listing = directory(entry("WidgetLeadDev", "busy"),
                            entry("WidgetLeadDev2", "busy"))
        self.assertEqual(rules.idle_agents(router.Context(
            twin, NOW, [], [], directory=listing)), {})

    def test_an_agent_who_is_away_is_never_idle(self):
        data = example()
        data["agents"]["UIDev"]["away"] = "LeadDev"
        away = rules.Roster(config.Project(data, None))
        listing = directory(entry("WidgetLeadDev"), entry("WidgetUIDev"))
        self.assertEqual(rules.idle_agents(router.Context(
            away, NOW, [], [], directory=listing)), {"LeadDev": IDLE})

    def test_listed_twice_idle_only_if_both_are_since_the_later(self):
        self.assertEqual(self.idle(entry("WidgetLeadDev"),
                                   entry("WidgetLeadDev", "busy")), {})
        self.assertEqual(self.idle(entry("WidgetLeadDev", since=T1),
                                   entry("WidgetLeadDev")),
                         {"LeadDev": IDLE})


class Actionable(unittest.TestCase):
    """What an agent can act on, through the offer it gets."""

    def setUp(self):
        configure(self, {})
        self.listing = directory(entry("WidgetLeadDev"))

    def offer(self, issues, prs=(), pages=()):
        decisions = rules.sweep(context(prs, issues, self.listing,
                                        pages=pages))
        self.assertTrue(all(d.agent == "LeadDev" and d.rule == 15
                            for d in decisions), decisions)
        return [d.line.split()[0] for d in decisions]

    def test_its_own_unblocked_issue(self):
        self.assertEqual(self.offer([owned(30)]), ["#30"])

    def test_the_oldest_by_creation(self):
        self.assertEqual(self.offer([owned(30, created=T2),
                                     owned(31, created=T1),
                                     owned(29, created=T2)]), ["#31"])

    def test_not_anothers_or_unowned(self):
        self.assertEqual(self.offer([owned(30, owner="UIDev"),
                                     issue_node(31)]), [])

    def test_not_waiting_on_the_owner_or_outside(self):
        self.assertEqual(self.offer([
            owned(30, labels=["Status:waiting-owner"]),
            owned(31, labels=["Status:waiting-external"])]), [])

    def test_not_while_a_blocker_is_open(self):
        self.assertEqual(self.offer([owned(30, body="Blocked-by: #31\n"),
                                     owned(31, owner="UIDev")]), [])

    def test_once_every_blocker_has_closed(self):
        # Written after the closes, so rule 14 has no turn for it.
        issue = owned(30, body="Blocked-by: #1\nBlocked-by: #2\n")
        issue["lastEditedAt"] = T2
        self.assertEqual(self.offer(
            [issue],
            pages=[{"data": {"repository": {
                "n1": {"state": "MERGED", "closedAt": T1},
                "n2": {"state": "CLOSED", "closedAt": T1}}}}]), ["#30"])

    def test_a_number_naming_nothing_does_not_block(self):
        self.assertEqual(self.offer(
            [owned(30, body="Blocked-by: #1\n")],
            pages=[{"data": {"repository": {"n1": None}}}]), ["#30"])

    def test_rule_14_wakes_it_first(self):
        self.assertEqual(rules.sweep(context(
            [], [owned(30, body="Blocked-by: #1\n")], self.listing,
            pages=[{"data": {"repository": {
                "n1": {"state": "MERGED", "closedAt": T1}}}}])), [])

    def test_unknown_blockers_block(self):
        self.assertTrue(rules.blocked({"body": "Blocked-by: #1\n"}))

    def test_not_when_its_own_pull_request_references_it(self):
        for kind in ({"closes": [30]}, {"body": "Closes #30"},
                     {"body": "Part of #30."},
                     {"body": "https://github.com/example/widget/issues/30"}):
            self.assertEqual(self.offer([owned(30), owned(31, created=T1)],
                                        prs=[pr(41, **kind)]), ["#31"], kind)

    def test_a_reference_that_isnt_one(self):
        self.assertEqual(self.offer([owned(30)], prs=[pr(41, body=(
            "x#30, &#30; and pull/30 are not references"))]), ["#30"])

    def test_someone_elses_pull_request_does_not_count(self):
        self.assertEqual(self.offer([owned(30)], prs=[
            pr(41, author=REVIEWER, body="Closes #30")]), ["#30"])


class Offer(unittest.TestCase):

    def setUp(self):
        configure(self, {"report_to": "LeadDev"})

    def test_the_offer(self):
        ctx = context(issues=[owned(30, title="Draw the cat")],
                      listing=directory(entry("WidgetLeadDev")))
        self.assertEqual(rules.sweep(ctx), [router.Decision(
            None, "LeadDev", 15, IDLE,
            '#30 "Draw the cat" is yours and nothing blocks it: start it, or '
            "say on the issue why not (a Status:waiting-owner or "
            "Status:waiting-external label, or a Blocked-by: line). You have "
            "been idle since 13:30Z.", reminder=router.ONCE)])

    def test_an_idle_period_from_another_day_gives_its_date(self):
        ctx = context(issues=[owned(30, title="")], when=NOW + datetime.
                      timedelta(days=1),
                      listing=directory(entry("WidgetLeadDev")))
        self.assertIn("#30 is yours", rules.sweep(ctx)[0].line)
        self.assertIn("since 2026-10-09 13:30Z.", rules.sweep(ctx)[0].line)

    def test_no_offer_while_the_agent_has_a_turn(self):
        # Rule 12: someone else commented on its other issue.
        ctx = context(issues=[owned(30), owned(31, comments=[
            comment(REVIEWER, T2)])], listing=directory(entry("WidgetLeadDev")))
        self.assertEqual(rules.sweep(ctx), [])

    def test_another_agents_turn_does_not_stop_it(self):
        ctx = context(issues=[owned(30), owned(31, owner="UIDev", comments=[
            comment(REVIEWER, T2)])], listing=directory(entry("WidgetLeadDev")))
        self.assertEqual([d.rule for d in rules.sweep(ctx)], [15])

    def test_a_reviewer_is_offered_its_own_issues_too(self):
        ctx = context(issues=[owned(30, owner="Reviewer")],
                      listing=directory(entry("WidgetReviewer")))
        self.assertEqual([(d.agent, d.rule) for d in rules.sweep(ctx)],
                         [("Reviewer", 15)])

    def test_nothing_on_a_run_for_some_items(self):
        ctx = context(issues=[owned(30)], only=frozenset({30}),
                      listing=directory(entry("WidgetLeadDev")))
        self.assertEqual(rules.sweep(ctx), [])


class Report(unittest.TestCase):

    def setUp(self):
        configure(self, {"report_to": "LeadDev"})

    def sweep(self, issues=(), prs=(), *entries):
        return rules.sweep(context(prs, issues, directory(*entries)))

    def test_an_idle_developer_with_nothing_is_reported(self):
        got = self.sweep(
            [owned(30, owner="UIDev", body="Blocked-by: #31\n"),
             owned(31, owner="Reviewer"),
             owned(32, owner="UIDev", labels=["Status:waiting-owner"]),
             owned(33, owner="UIDev")],
            [pr(41, author=UIDEV, body="Closes #33"), pr(42, author=UIDEV)],
            entry("WidgetUIDev"))
        self.assertEqual(got, [router.Decision(
            None, "LeadDev", 16, "UIDev " + IDLE,
            "UIDev has been idle since 13:30Z with nothing it can act on (2 "
            "open pull requests, 1 issue blocked, 1 issue waiting).",
            reminder=router.ONCE)])

    def test_owning_nothing_at_all(self):
        got = self.sweep([], [], entry("WidgetUIDev"))
        self.assertEqual(got[0].line, "UIDev has been idle since 13:30Z with "
                         "nothing it can act on (no open issues of its own).")

    def test_one_report_per_developer(self):
        got = self.sweep([], [], entry("WidgetUIDev"), entry("Helper"))
        self.assertEqual([d.event for d in got],
                         ["Helper " + IDLE, "UIDev " + IDLE])

    def test_not_a_reviewer_or_report_to_itself(self):
        self.assertEqual(self.sweep([], [], entry("WidgetReviewer"),
                                    entry("WidgetLeadDev")), [])

    def test_not_while_the_developer_has_a_turn(self):
        self.assertEqual(self.sweep([owned(30, owner="UIDev", comments=[
            comment(REVIEWER, T2)], body="Blocked-by: #31\n"),
            owned(31, owner="Reviewer")], [], entry("WidgetUIDev")), [])

    def test_nobody_without_report_to(self):
        configure(self, {})
        self.assertEqual(self.sweep([], [], entry("WidgetUIDev")), [])


class Engine(unittest.TestCase):
    """The offer through forge-router's run: once per idle period."""

    def setUp(self):
        configure(self, {"report_to": "LeadDev"})
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "router")
        self.lines, self.posts = [], []
        self.listing = (directory(entry("WidgetLeadDev")), None)

    def post(self, session, text):
        self.posts.append((session, text))

    def run_router(self, issues, when=NOW, send=True):
        self.posts.clear()
        self.lines.clear()
        return r.run(FakeGitHub(response([], issues)), r.RULES.Roster(
            project({"report_to": "LeadDev"})), self.dir, send, self.post,
            None, self.lines.append, when=when, remind_s=60,
            directory=lambda: self.listing)

    def test_offered_once_per_idle_period(self):
        self.assertEqual(self.run_router([owned(30)]), 0)
        self.assertEqual([s for s, _ in self.posts], ["WidgetLeadDev"])
        self.assertIn("Your turn:\n#30 \"item 30\" is yours",
                      self.posts[0][1])
        # Still idle hours later: no reminder.
        self.run_router([owned(30)], when=NOW + datetime.timedelta(hours=5))
        self.assertEqual(self.posts, [])
        # Busy, then idle again: a new idle period, a new offer.
        self.listing = (directory(entry("WidgetLeadDev", "busy")), None)
        self.run_router([owned(30)], when=NOW + datetime.timedelta(hours=6))
        self.assertEqual(self.posts, [])
        self.listing = (directory(entry("WidgetLeadDev", since=LATER_IDLE)),
                        None)
        self.run_router([owned(30)], when=NOW + datetime.timedelta(hours=7))
        self.assertEqual([s for s, _ in self.posts], ["WidgetLeadDev"])

    def test_another_item_in_the_same_idle_period_is_not_offered(self):
        self.run_router([owned(30), owned(31, created=T1)])
        self.run_router([owned(31, created=T1)],
                        when=NOW + datetime.timedelta(minutes=2))
        self.assertEqual(self.posts, [])

    def test_an_unreadable_directory_offers_nothing(self):
        self.listing = (None, "HTTP 503, not a complete directory")
        self.assertEqual(self.run_router([owned(30)]), 0)
        self.assertEqual(self.posts, [])
        self.assertIn("NO-DIRECTORY: HTTP 503, not a complete directory; "
                      "nobody is taken to be idle", self.lines)

    def presence(self):
        path = os.path.join(self.dir, "presence.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle]

    def test_each_change_of_status_is_recorded_once(self):
        self.run_router([])
        self.run_router([], when=NOW + datetime.timedelta(minutes=1))
        self.listing = (directory(entry("WidgetLeadDev", "busy"),
                                  entry("WidgetUIDev")), None)
        self.run_router([], when=NOW + datetime.timedelta(minutes=2))
        self.listing = (directory(entry("WidgetUIDev")), None)
        self.run_router([], when=NOW + datetime.timedelta(minutes=3))
        self.run_router([], when=NOW + datetime.timedelta(minutes=4))
        got = [(p["name"], p["status"]) for p in self.presence()]
        self.assertEqual(got, [("WidgetLeadDev", "idle"),
                               ("WidgetLeadDev", "busy"),
                               ("WidgetUIDev", "idle"),
                               ("WidgetLeadDev", "gone")])
        self.assertEqual(self.presence()[0]["since"], IDLE)

    def test_a_dry_run_or_an_unreadable_directory_records_nothing(self):
        self.run_router([], send=False)
        self.listing = (None, "HTTP 503, not a complete directory")
        self.run_router([])
        self.assertEqual(self.presence(), [])

    def test_a_dry_run_reads_it_and_sends_nothing(self):
        self.assertEqual(self.run_router([owned(30)], send=False), 0)
        self.assertEqual(self.posts, [])
        self.assertTrue(any(l.startswith("WOULD SEND to WidgetLeadDev:")
                            for l in self.lines))

    def test_not_read_for_rules_that_dont_want_it(self):
        configure(self, None)

        def unread():
            raise AssertionError("read the directory")
        self.assertEqual(r.run(FakeGitHub(response([], [owned(30)])),
                               r.RULES.Roster(project()), self.dir, True,
                               self.post, None, self.lines.append, when=NOW,
                               directory=unread), 0)


class Reader(unittest.TestCase):
    """make_directory_reader: what counts as the relay's directory."""

    class Response:
        def __init__(self, code, answer):
            self.status, self.answer = code, answer

        def read(self, limit=-1):
            return json.dumps(self.answer).encode()

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def read(self, answer, token="s3cret"):
        seen = []

        def opener(req, timeout):
            seen.append(req)
            if isinstance(answer, Exception):
                raise answer
            return self.Response(*answer)
        got = r.make_directory_reader("http://relay:8788/", token, opener)()
        self.seen = seen
        return got

    def test_a_complete_directory(self):
        listing = directory(entry("WidgetLeadDev"))
        self.assertEqual(self.read((200, listing)), (listing, None))
        req = self.seen[0]
        self.assertEqual((req.full_url, req.get_method()),
                         ("http://relay:8788/directory", "GET"))
        self.assertEqual(req.get_header("X-webhook-token"), "s3cret")

    def test_incomplete_or_not_a_directory(self):
        for answer in (directory(complete=False), {"agents": []},
                       {"complete": True, "agents": {}}, ["x"]):
            got, why = self.read((200, answer))
            self.assertIsNone(got)
            self.assertEqual(why, "HTTP 200, not a complete directory")

    def test_http_errors_and_no_answer(self):
        err = urllib.error.HTTPError("http://relay:8788/directory", 503,
                                     "x", {}, io.BytesIO(b"{}"))
        self.assertEqual(self.read(err), (None, "HTTP 503"))
        got, why = self.read(urllib.error.URLError("refused"))
        self.assertIsNone(got)
        self.assertTrue(why.startswith(r.UNREACHABLE), why)

    def test_no_token_asks_nothing(self):
        got, why = self.read((200, directory()), token=None)
        self.assertIsNone(got)
        self.assertIn("no relay token", why)
        self.assertEqual(self.seen, [])


class Main(unittest.TestCase):
    """main builds a reader only for rules that want the directory."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.checkout = os.path.join(tmp.name, "cron")
        os.makedirs(os.path.join(self.checkout, ".agent-forge"))
        self.addCleanup(r.configure, project())

    def readers(self, data):
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(data, f)
        got = []

        def fake_run(*args, directory=None, **more):
            got.append(directory)
            return 0
        with mock.patch.object(r, "run", fake_run):
            r.main(["--state-dir", os.path.join(self.tmp, "router")], None,
                   lambda line: None, roster=object(),
                   environ={"HOME": self.tmp,
                            "FORGE_CRON_CHECKOUT": self.checkout},
                   mint=lambda project, agent: "token")
        return got

    def test_only_with_the_idle_offer(self):
        self.assertEqual(self.readers(example()), [None])
        got = self.readers(dict(example(), router={"idle_offer": {}}))
        self.assertTrue(callable(got[0]))


if __name__ == "__main__":
    unittest.main()
