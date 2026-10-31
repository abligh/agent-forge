"""Tests for agent_forge.rules_default as the engine sees it: the
interface. Most rules are tested through bin/forge-router, in
tests/test_forge_router.py; red builds first (`router.red_first`,
rules 20 and 21, designs/0039) and a green nightly's wake
(`router.nightly_wake`, rule 22, designs/0040) are tested here."""
import datetime
import json
import os
import tempfile
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
        self.assertEqual([l.name for l in rules.lookups],
                         ["blockers", "bases", "red builds",
                          "green nightly"])


# --- red builds first (designs/0039) ----------------------------------------

RED = "2026-10-09T11:00:00Z"
LEADDEV = "leaddevwidgetagent"
UIDEV = "uidevwidgetagent"
NIGHTLY_LINE = ("#40 says the nightly is red (since 11:00Z), and no pull "
                "request references it. Red builds go first: put other "
                "work down and fix it, or say on the issue who is fixing "
                "it.")


def red_project(red_first=None, idle_offer=None, quiet_labels=None,
                nightly=False, nightly_wake=None, owners=None):
    data = example()
    data["router"] = {}
    if nightly_wake is not None:
        data["router"]["nightly_wake"] = nightly_wake
    if owners is not None:
        data["router"]["owners"] = owners
    if red_first is not None:
        data["router"]["red_first"] = red_first
    if idle_offer is not None:
        data["router"]["idle_offer"] = idle_offer
    if quiet_labels is not None:
        data["router"]["quiet_labels"] = quiet_labels
    if nightly:
        data["nightly"] = {"workflow": "nightly.yaml", "job": "build"}
    return config.Project(data, None)


def note(login, at, body="noted", id=1):
    return {"databaseId": id, "author": {"login": login}, "createdAt": at,
            "body": body}


def red_issue(number=40, owner="LeadDev", labels=(), comments=(),
              event=RED, what="the nightly", created="2026-10-09T09:00:00Z"):
    item = issue(number, (["Owner:" + owner] if owner else [])
                 + list(labels), comments)
    item["createdAt"] = created
    item["title"] = "The nightly is red at 5b257c8"
    if event:
        item["redBuild"] = {"what": what, "event": event}
    return item


def plain_issue(number, owner="LeadDev", created="2026-10-01T09:00:00Z"):
    item = issue(number, ["Owner:" + owner])
    item["createdAt"] = created
    item["title"] = "item %d" % number
    return item


def draft(number, author=UIDEV, body="", closes=()):
    return {"__typename": "PullRequest", "number": number, "isDraft": True,
            "headRefOid": "0" * 40, "author": {"login": author},
            "body": body, "labels": [], "files": [], "reviews": [],
            "comments": [], "commits": {"nodes": []},
            "headCommit": {"nodes": []},
            "closingIssuesReferences": {"nodes": [{"number": n}
                                                  for n in closes]}}


def entry(name, status="idle", since="2026-10-09T13:00:00Z"):
    return {"name": name, "nick": name, "status": status,
            "idle_since": since if status == "idle" else None}


def listing(*entries):
    return {"room": "everyone@conference.example.org", "complete": True,
            "agents": list(entries)}


class RedFirstCase(unittest.TestCase):

    def use(self, project):
        rules.configure(project, merger)
        self.addCleanup(rules.configure, PROJECT, merger)
        self.roster = rules.Roster(project)

    def ctx(self, issues=(), prs=(), directory=None, only=None, when=NOW):
        return router.Context(self.roster, when, list(prs), list(issues),
                              only=only, directory=directory)

    def decided(self, item, **more):
        ctx = self.ctx(issues=[item] + list(more.pop("others", ())), **more)
        return rules.decide(item, ctx)[0]


class RedFirstConfig(unittest.TestCase):

    def problems(self, red):
        return config.check(dict(example(), router={"red_first": red}))

    def test_valid(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems({"fallback_hours": 1.5}), [])

    def test_every_key_is_checked(self):
        self.assertEqual(self.problems({"fallback": 2}),
                         ["router.red_first: unknown key 'fallback'"])
        self.assertEqual(self.problems(True),
                         ["router.red_first must be an object"])
        for bad in (0, -1, "2", True, None, float("nan")):
            with self.subTest(bad=bad):
                self.assertEqual(self.problems({"fallback_hours": bad}), [
                    "router.red_first.fallback_hours must be a number "
                    "above 0"])

    def test_configure(self):
        rules.configure(red_project(), merger)
        self.addCleanup(rules.configure, PROJECT, merger)
        self.assertIsNone(rules.RED_FIRST)
        rules.configure(red_project({}), merger)
        self.assertEqual(rules.RED_FIRST, {"fallback_hours": 2})
        rules.configure(red_project({"fallback_hours": 0.5}), merger)
        self.assertEqual(rules.RED_FIRST, {"fallback_hours": 0.5})


class Rule20(RedFirstCase):

    def setUp(self):
        self.use(red_project({}))

    def test_off_without_the_key(self):
        self.use(red_project())
        got = self.decided(red_issue())
        self.assertIsNone(got.agent)
        self.assertEqual(got.suppressed, "no rule matched")
        # Nor does the lookup read anything.
        item = red_issue(event=None)
        rules.attach_red_builds(None, "example/widget", [], [item])
        self.assertNotIn("redBuild", item)

    def test_the_owners_turn_at_once_busy_or_not(self):
        busy = listing(entry("WidgetLeadDev", status="busy"))
        self.assertEqual(self.decided(red_issue(), directory=busy),
                         router.Decision(40, "LeadDev", 20, RED,
                                         NIGHTLY_LINE))

    def test_the_default_branch(self):
        got = self.decided(red_issue(what="the default branch"))
        self.assertTrue(got.line.startswith(
            "#40 says the default branch is red (since 11:00Z)"))

    def test_any_open_pull_request_referencing_it_ends_it(self):
        for pr in (draft(50, body="Fixes the nightly, #40."),
                   draft(50, author=LEADDEV, closes=(40,)),
                   draft(50, body="See issue 40.")):
            with self.subTest(pr=pr["body"]):
                got = self.decided(red_issue(), prs=[pr])
                self.assertNotEqual(got.rule, 20)
        # Another issue's number doesn't.
        self.assertEqual(self.decided(red_issue(), prs=[draft(
            50, body="#41")]).rule, 20)

    def test_the_owners_comment_after_the_red_event_ends_it(self):
        said = note(LEADDEV, "2026-10-09T11:30:00Z", "On it.")
        got = self.decided(red_issue(comments=[said]))
        self.assertIsNone(got.agent)
        self.assertEqual(got.suppressed, "no rule matched")
        # One before it doesn't.
        early = note(LEADDEV, "2026-10-09T10:30:00Z")
        self.assertEqual(self.decided(red_issue(comments=[early])).rule, 20)

    def test_a_new_red_event_wakes_her_again(self):
        said = note(LEADDEV, "2026-10-09T11:30:00Z", "On it.")
        again = "2026-10-09T13:10:00Z"
        got = self.decided(red_issue(comments=[said], event=again))
        self.assertEqual((got.agent, got.rule, got.event),
                         ("LeadDev", 20, again))

    def test_parked_issues_are_left_alone(self):
        self.assertEqual(self.decided(red_issue(
            labels=["Status:waiting-external"])).rule, 9)
        self.assertEqual(self.decided(red_issue(
            labels=["Status:waiting-owner"])).rule, 11)
        got = self.decided(red_issue(labels=["Status:waiting-backlog"]))
        self.assertIsNone(got.agent)
        self.use(red_project({}, quiet_labels=["parked"]))
        self.assertEqual(self.decided(red_issue(labels=["parked"])).suppressed,
                         "quiet: parked")

    def test_no_owner_no_turn(self):
        got = self.decided(red_issue(owner=None))
        self.assertNotEqual(got.rule, 20)

    def test_without_the_runs_context_it_never_applies(self):
        self.assertNotEqual(rules.route_issue(red_issue(), self.roster)
                            ["rule"], 20)


class Rule15RedFirst(RedFirstCase):

    def setUp(self):
        self.use(red_project({}, idle_offer={}))

    def offer(self):
        # She has said she is on the red one, so rule 20 is over.
        said = note(LEADDEV, "2026-10-09T11:30:00Z", "On it.")
        ctx = self.ctx(issues=[plain_issue(30), red_issue(comments=[said])],
                       directory=listing(entry(
                           "WidgetLeadDev", since="2026-10-09T11:40:00Z")),
                       when=datetime.datetime(2026, 10, 9, 12, 0,
                                              tzinfo=datetime.timezone.utc))
        return [d for d in rules.sweep(ctx) if d.rule == 15]

    def test_red_issues_come_first(self):
        [got] = self.offer()
        self.assertEqual(got.agent, "LeadDev")
        self.assertTrue(got.line.startswith(
            '#40 "The nightly is red at 5b257c8" is yours and says the '
            'nightly is red. Red builds go first: start it'), got.line)

    def test_without_red_first_the_oldest(self):
        self.use(red_project(idle_offer={}))
        [got] = self.offer()
        self.assertTrue(got.line.startswith('#30 "item 30" is yours and '
                                            'nothing blocks it'), got.line)


class Rule21(RedFirstCase):

    def setUp(self):
        self.use(red_project({}, idle_offer={}))

    def sweep(self, issues=None, prs=(), directory=None, only=None):
        issues = [red_issue()] if issues is None else issues
        directory = directory or listing(
            entry("WidgetLeadDev", status="busy"),
            entry("WidgetUIDev", since="2026-10-09T13:00:00Z"),
            entry("Helper", since="2026-10-09T13:30:00Z"),
            entry("WidgetReviewer", since="2026-10-09T12:00:00Z"))
        return rules.sweep(self.ctx(issues=issues, prs=prs,
                                    directory=directory, only=only))

    def fallbacks(self, **more):
        return [d for d in self.sweep(**more) if d.rule == 21]

    def test_the_longest_idle_developer_is_offered_it_once(self):
        # UIDev has an issue of her own that rule 15 would offer.
        got = self.sweep(issues=[red_issue(), plain_issue(31, "UIDev")])
        self.assertEqual([d for d in got if d.rule == 21], [router.Decision(
            None, "UIDev", 21, "#40 " + RED,
            "#40 says the nightly is red since 11:00Z, and for 2 h no pull "
            "request has referenced it and its owner, LeadDev, hasn't "
            "commented. You are idle: take it (move its owner label to "
            "Owner:UIDev and say so on the issue) and fix it first, unless "
            "a pull request is about to reference it.",
            reminder=router.ONCE)])
        # Nothing else is offered to her that run.
        self.assertEqual([(d.agent, d.rule) for d in got], [("UIDev", 21)])

    def test_the_name_breaks_a_tie(self):
        got = self.fallbacks(directory=listing(
            entry("WidgetUIDev"), entry("Helper")))
        self.assertEqual([d.agent for d in got], ["Helper"])

    def test_not_before_fallback_hours(self):
        late = red_issue(event="2026-10-09T12:30:00Z")
        self.assertEqual(self.fallbacks(issues=[late]), [])
        self.use(red_project({"fallback_hours": 1}, idle_offer={}))
        self.assertEqual([d.agent for d in self.fallbacks(issues=[late])],
                         ["UIDev"])

    def test_not_to_the_owner(self):
        # She commented long ago, so it isn't her turn, and she is idle.
        said = note(LEADDEV, "2026-10-09T11:10:00Z")
        got = self.fallbacks(issues=[red_issue(comments=[said])],
                             directory=listing(entry("WidgetLeadDev")))
        self.assertEqual(got, [])

    def test_not_when_a_pull_request_references_it(self):
        self.assertEqual(self.fallbacks(prs=[draft(50, author=LEADDEV,
                                                   body="Fixes #40")]), [])

    def test_not_when_the_owner_commented_lately(self):
        said = note(LEADDEV, "2026-10-09T12:30:00Z", "Bisecting.")
        self.assertEqual(self.fallbacks(issues=[red_issue(comments=[said])]),
                         [])
        old = note(LEADDEV, "2026-10-09T11:10:00Z", "Bisecting.")
        self.assertEqual([d.agent for d in self.fallbacks(
            issues=[red_issue(comments=[old])])], ["UIDev"])

    def test_not_when_parked(self):
        self.assertEqual(self.fallbacks(issues=[red_issue(
            labels=["Status:waiting-backlog"])]), [])

    def test_not_to_a_developer_with_a_turn(self):
        busy = plain_issue(31, owner="UIDev")
        busy["comments"] = [note(LEADDEV, "2026-10-09T13:40:00Z")]
        self.assertEqual([d.agent for d in self.fallbacks(
            issues=[red_issue(), busy])], ["Helper"])

    def test_nobody_idle_nothing_said(self):
        self.assertEqual(self.fallbacks(directory=listing(
            entry("WidgetUIDev", status="busy"))), [])

    def test_not_without_the_idle_offer(self):
        self.use(red_project({}))
        self.assertEqual(self.sweep(), [])

    def test_not_on_an_only_run(self):
        self.assertEqual(self.sweep(only=frozenset({40})), [])


class RedBuilds(unittest.TestCase):
    """Which issues are red-build issues: the build tools' state."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = {"FORGE_STATE_DIR": tmp.name, "HOME": tmp.name}
        self.project = red_project({}, nightly=True)
        self.now = NOW

    def write(self, tool, name, data):
        path = os.path.join(self.env["FORGE_STATE_DIR"], "widget", tool)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, name), "w") as handle:
            json.dump(data, handle)

    def found(self):
        return rules.red_builds(self.project, self.env, self.now)

    def test_nothing_without_the_files(self):
        self.assertEqual(self.found(), {})

    def test_the_nightlys_open_issue_while_fresh(self):
        self.write("nightly", "nightly.json", {
            "at": "2026-10-09T13:55:00Z", "issue": 40, "issue_open": True,
            "opened": "2026-10-09T05:00:00Z",
            "red": {"ended": "2026-10-09T05:40:00Z"}})
        self.assertEqual(self.found(),
                         {40: ("the nightly", "2026-10-09T05:40:00Z")})
        self.now = NOW + datetime.timedelta(hours=1)
        self.assertEqual(self.found(), {})

    def test_the_nightly_opened_without_a_red_run(self):
        self.write("nightly", "nightly.json", {
            "at": "2026-10-09T13:55:00Z", "issue": 40, "issue_open": True,
            "opened": "2026-10-09T05:00:00Z"})
        self.assertEqual(self.found(),
                         {40: ("the nightly", "2026-10-09T05:00:00Z")})

    def test_not_once_closed_or_without_the_section(self):
        self.write("nightly", "nightly.json", {
            "at": "2026-10-09T13:55:00Z", "issue": 40, "issue_open": False,
            "opened": "2026-10-09T05:00:00Z"})
        self.assertEqual(self.found(), {})
        self.write("nightly", "nightly.json", {
            "at": "2026-10-09T13:55:00Z", "issue": 40, "issue_open": True,
            "opened": "2026-10-09T05:00:00Z"})
        self.project = red_project({})
        self.assertEqual(self.found(), {})

    def test_the_default_branchs_issue(self):
        self.write("pr-check", "state.json", {"main_red": {
            "issue": 41, "since": "5b257c8", "told": []}})
        self.assertEqual(self.found(), {41: ("the default branch", None)})
        self.write("pr-check", "state.json", {"main_red": {"issue": True}})
        self.assertEqual(self.found(), {})

    def test_the_lookup(self):
        # The lookup reads the file as of now.
        written = datetime.datetime.now(datetime.timezone.utc)
        self.write("nightly", "nightly.json", {
            "at": written.strftime("%Y-%m-%dT%H:%M:%SZ"), "issue": 40, "issue_open": True,
            "opened": "2026-10-09T05:00:00Z",
            "red": {"ended": "2026-10-09T05:40:00Z"}})
        self.write("pr-check", "state.json", {"main_red": {"issue": 41}})
        rules.configure(self.project, merger)
        self.addCleanup(rules.configure, PROJECT, merger)
        old = rules.ENVIRON
        rules.ENVIRON = self.env
        self.addCleanup(setattr, rules, "ENVIRON", old)
        nightly_issue, main, again, other = (
            issue(40), issue(41), issue(41), issue(42))
        main["comments"] = [
            note("mergerwidgetagent", "2026-10-09T08:00:00Z", "Still red"),
            note(LEADDEV, "2026-10-09T09:00:00Z")]
        rules.attach_red_builds(None, "example/widget", [],
                                [nightly_issue, main, other])
        rules.attach_red_builds(None, "example/widget", [], [again])
        self.assertEqual(nightly_issue["redBuild"], {
            "what": "the nightly", "event": "2026-10-09T05:40:00Z"})
        # The poster's newest comment, else the issue's creation.
        self.assertEqual(main["redBuild"], {
            "what": "the default branch", "event": "2026-10-09T08:00:00Z"})
        self.assertEqual(again["redBuild"]["event"], "2026-10-09T10:00:00Z")
        self.assertNotIn("redBuild", other)


# --- a green nightly wakes what waits for it (designs/0040) -----------------

WAITS = "awaiting-nightly"
GREEN = {"run": 77, "sha": "c0ffee1" + "0" * 33,
         "started": "2026-10-09T12:00:00Z", "ended": "2026-10-09T13:30:00Z"}
GREEN_LINE = ("#60 waits for a nightly check, and nightly run 77 (c0ffee1, "
              "green at 13:30Z) includes its fixes: check it on that build "
              "now.")


def waiting_issue(number=60, owner="LeadDev", labels=(),
                  labelled="2026-10-09T11:00:00Z", green=GREEN, body="",
                  events=()):
    item = issue(number, (["Owner:" + owner] if owner else [])
                 + [WAITS] + list(labels))
    item["title"] = "item %d" % number
    item["body"] = body
    nodes = list(events)
    if labelled:
        nodes.append({"createdAt": labelled, "label": {"name": WAITS}})
    item["ownerLabels"] = {"nodes": nodes}
    if green is not None:
        item["greenNightly"] = dict(green)
    return item


def wake_project(**more):
    more.setdefault("nightly", True)
    more.setdefault("nightly_wake", {"label": WAITS})
    return red_project(**more)


class NightlyWakeConfig(unittest.TestCase):

    def problems(self, wake, nightly=True):
        # forge-nightly needs someone for steps nobody owns.
        data = dict(example(), router={"nightly_wake": wake,
                                       "owners": {"triage": "LeadDev"}})
        if nightly:
            data["nightly"] = {"workflow": "nightly.yaml", "job": "build"}
        return config.check(data)

    def test_valid(self):
        self.assertEqual(self.problems({"label": WAITS}), [])

    def test_every_key_is_checked(self):
        self.assertEqual(self.problems({"label": WAITS, "hours": 1}),
                         ["router.nightly_wake: unknown key 'hours'"])
        self.assertEqual(self.problems({}),
                         ["router.nightly_wake: missing 'label'"])
        self.assertEqual(self.problems({"label": " "}),
                         ["router.nightly_wake.label must not be empty"])
        self.assertEqual(self.problems({"label": 3}),
                         ["router.nightly_wake.label must be a string"])
        self.assertEqual(self.problems(True),
                         ["router.nightly_wake must be an object"])

    def test_it_needs_the_nightly_section(self):
        self.assertEqual(self.problems({"label": WAITS}, nightly=False),
                         ["router.nightly_wake needs the nightly section"])

    def test_configure(self):
        rules.configure(red_project(nightly=True), merger)
        self.addCleanup(rules.configure, PROJECT, merger)
        self.assertIsNone(rules.NIGHTLY_WAKE)
        self.assertNotIn("ownerLabels", rules.graphql["Issue"])
        rules.configure(wake_project(), merger)
        self.assertEqual(rules.NIGHTLY_WAKE, {"label": WAITS})
        # Label events are fetched for it, without `router.owners` too.
        self.assertIn("ownerLabels", rules.graphql["Issue"])


class Rule22(RedFirstCase):

    def setUp(self):
        self.use(wake_project())

    def wakes(self, item, **more):
        ctx = self.ctx(issues=[item] + list(more.pop("others", ())), **more)
        return [d for d in rules.decide(item, ctx) if d.rule == 22]

    def test_off_without_the_key(self):
        self.use(red_project(nightly=True))
        self.assertEqual(self.wakes(waiting_issue()), [])
        # Nor does the lookup read anything.
        item = waiting_issue(green=None)
        rules.attach_green_nightly(None, "example/widget", [], [item])
        self.assertNotIn("greenNightly", item)

    def test_labelled_before_the_run_started(self):
        self.assertEqual(self.wakes(waiting_issue()), [router.Decision(
            60, "LeadDev", 22, GREEN["ended"], GREEN_LINE,
            reminder=router.ONCE)])

    def test_beside_the_issues_own_decision(self):
        # A comment by someone else is still rule 12's, and rule 22 is
        # given beside it, neither hiding the other.
        item = waiting_issue()
        item["comments"] = [note(UIDEV, "2026-10-09T13:00:00Z")]
        got = rules.decide(item, self.ctx(issues=[item]))
        self.assertEqual([(d.agent, d.rule) for d in got],
                         [("LeadDev", 12), ("LeadDev", 22)])

    def test_not_when_labelled_after_it_started(self):
        self.assertEqual(self.wakes(waiting_issue(
            labelled="2026-10-09T12:30:00Z")), [])
        self.assertEqual(self.wakes(waiting_issue(
            labelled=GREEN["started"])), [])

    def test_the_newest_time_the_label_went_on(self):
        older = {"createdAt": "2026-10-08T09:00:00Z",
                 "label": {"name": WAITS}}
        other = {"createdAt": "2026-10-09T13:00:00Z",
                 "label": {"name": "bug"}}
        self.assertEqual(len(self.wakes(waiting_issue(
            events=[older, other]))), 1)
        self.assertEqual(self.wakes(waiting_issue(
            labelled="2026-10-09T12:30:00Z", events=[older])), [])
        # No event for it among those fetched: unknown wakes nobody.
        self.assertEqual(self.wakes(waiting_issue(labelled=None,
                                                  events=[other])), [])

    def test_not_when_parked(self):
        for label in ("Status:waiting-owner", "Status:waiting-external",
                      "Status:waiting-backlog"):
            with self.subTest(label=label):
                self.assertEqual(self.wakes(waiting_issue(
                    labels=[label])), [])
        self.use(wake_project(quiet_labels=["parked"]))
        self.assertEqual(self.wakes(waiting_issue(labels=["parked"])), [])

    def test_its_own_label_doesnt_park_it(self):
        self.use(wake_project(nightly_wake={
            "label": "Status:waiting-nightly"}))
        item = waiting_issue()
        item["labels"] = [{"name": "Owner:LeadDev"},
                          {"name": "Status:waiting-nightly"}]
        item["ownerLabels"]["nodes"][0]["label"]["name"] = \
            "Status:waiting-nightly"
        self.assertEqual(len(self.wakes(item)), 1)

    def test_not_without_an_owner(self):
        self.assertEqual(self.wakes(waiting_issue(owner=None)), [])
        # With `router.owners`, a label naming no agent is no owner.
        self.use(wake_project(owners={"triage": "LeadDev"}))
        item = waiting_issue(owner="Nobody")
        item["author"] = {"login": "someone"}
        self.assertEqual(self.wakes(item), [])

    def test_a_future_defer_until_doesnt_hold_it(self):
        item = waiting_issue(body="Defer-until: 2026-10-10T03:17:00Z")
        self.assertIsNotNone(rules.deferred_until(item))
        self.assertGreater(rules.deferred_until(item), NOW)
        self.assertEqual(len(self.wakes(item)), 1)

    def test_no_green_run_wakes_nobody(self):
        self.assertEqual(self.wakes(waiting_issue(green=None)), [])
        self.assertEqual(self.wakes(waiting_issue(
            green=dict(GREEN, started=None))), [])

    def test_a_new_green_run_is_a_new_event(self):
        later = dict(GREEN, run=78, started="2026-10-10T03:17:00Z",
                     ended="2026-10-10T04:20:00Z")
        [got] = self.wakes(waiting_issue(green=later),
                           when=datetime.datetime(
                               2026, 10, 10, 5, 0,
                               tzinfo=datetime.timezone.utc))
        self.assertEqual((got.event, got.line), (
            "2026-10-10T04:20:00Z",
            "#60 waits for a nightly check, and nightly run 78 (c0ffee1, "
            "green at 04:20Z) includes its fixes: check it on that build "
            "now."))

    def test_without_the_runs_context_it_never_applies(self):
        self.assertIsNone(rules.nightly_turn(waiting_issue(), self.roster,
                                             None))


class NightlyWaitIsNotActionable(RedFirstCase):
    """An issue carrying the label waits: rule 15 offers her real work."""

    def setUp(self):
        self.use(wake_project(idle_offer={}))

    def offer(self):
        waiting = waiting_issue(green=None)
        waiting["createdAt"] = "2026-09-01T09:00:00Z"
        ctx = self.ctx(issues=[waiting, plain_issue(30)],
                       directory=listing(entry("WidgetLeadDev")))
        return [d for d in rules.sweep(ctx) if d.rule == 15]

    def test_the_label_waits_with_or_without_defer_until(self):
        [got] = self.offer()
        self.assertTrue(got.line.startswith('#30 "item 30" is yours'),
                        got.line)
        actionable, _, waiting = rules.owned_issues(
            "LeadDev", self.ctx(issues=[waiting_issue(), plain_issue(30)]))
        self.assertEqual(([i["number"] for i in actionable], waiting),
                         ([30], 1))

    def test_without_the_key_it_is_actionable(self):
        self.use(red_project(idle_offer={}, nightly=True))
        [got] = self.offer()
        self.assertTrue(got.line.startswith('#60 "item 60" is yours'),
                        got.line)


class GreenNightly(unittest.TestCase):
    """Which run is the last green one: forge-nightly's state."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.env = {"FORGE_STATE_DIR": tmp.name, "HOME": tmp.name}
        self.project = wake_project()
        self.now = NOW

    def write(self, data):
        path = os.path.join(self.env["FORGE_STATE_DIR"], "widget", "nightly")
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "nightly.json"), "w") as handle:
            json.dump(data, handle)

    def found(self):
        return rules.green_nightly(self.project, self.env, self.now)

    def test_the_green_run_while_fresh(self):
        self.write({"at": "2026-10-09T13:55:00Z", "green": dict(
            GREEN, started="2026-10-09T12:00:00+00:00")})
        self.assertEqual(self.found(), GREEN)
        self.now = NOW + datetime.timedelta(hours=1)
        self.assertIsNone(self.found())

    def test_nothing_without_the_file_or_a_whole_record(self):
        self.assertIsNone(self.found())
        for missing in ("run", "sha", "started", "ended"):
            with self.subTest(missing=missing):
                green = dict(GREEN)
                del green[missing]
                self.write({"at": "2026-10-09T13:55:00Z", "green": green})
                self.assertIsNone(self.found())
        self.write({"at": "2026-10-09T13:55:00Z"})
        self.assertIsNone(self.found())

    def test_the_lookup(self):
        written = datetime.datetime.now(datetime.timezone.utc)
        self.write({"at": written.strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "green": GREEN})
        rules.configure(self.project, merger)
        self.addCleanup(rules.configure, PROJECT, merger)
        old = rules.ENVIRON
        rules.ENVIRON = self.env
        self.addCleanup(setattr, rules, "ENVIRON", old)
        waiting, other = waiting_issue(green=None), issue(61)
        rules.attach_green_nightly(None, "example/widget", [],
                                   [waiting, other])
        self.assertEqual(waiting["greenNightly"], GREEN)
        self.assertNotIn("greenNightly", other)


if __name__ == "__main__":
    unittest.main()
