"""Tests for bin/forge-router's engine on what the default rules never
decide: several decisions for one item, decisions about no item, the
sweep, kinds and sections, actions, reminder policies, recheck, the
exit status, the merged pull requests' fields, the previous run's
decisions, actions once a run, a turn already woken, the grace period
before a turn is forgotten and the seed. The rules are tests/router_rules.py, told by each test
what to decide; tests/test_forge_router.py tests the default rules.
"""

import datetime
import json
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agent_forge import router
from agent_forge.router import AddLabel, Decision, Reminder
from tests import test_forge_router as base

r = base.r
TESTS = os.path.dirname(os.path.abspath(__file__))
NOW = base.NOW
E1 = "2026-10-09T11:00:00Z"
E2 = "2026-10-09T12:00:00Z"


def later(**delta):
    return NOW + datetime.timedelta(**delta)


def node(number):
    return {"number": number, "title": "item %d" % number}


def turn(item, agent, event=E1, line=None, **more):
    return Decision(item, agent, "t", event,
                    line or "#%s for %s" % (item, agent), **more)


class GitHub(base.FakeGitHub):
    """FakeGitHub that also takes writes, recording each one (and the
    order of everything, with posts, in `events`)."""

    def __init__(self, *pages, events=None, refuse=None):
        super().__init__(*pages)
        self.writes = []
        self.events = events if events is not None else []
        self.refuse = refuse

    def api(self, *args):
        self.events.append("api")
        return super().api(*args)

    def write(self, method, path, **fields):
        self.writes.append((method, path, fields))
        self.events.append("write")
        return (False, self.refuse) if self.refuse else (True, "")


class EngineCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "router")
        self.rules = router.load_rules("router_rules.py", TESTS)
        patch = mock.patch.object(r, "RULES", self.rules)
        patch.start()
        self.addCleanup(patch.stop)
        self.roster = self.rules.Roster()
        self.lines, self.posts, self.events = [], [], []
        self.refuse = {}

    def post(self, session, text):
        self.posts.append((session, text))
        self.events.append("post")
        return self.refuse.get(session)

    def decide(self, decisions=None, sweep=()):
        self.rules.DECISIONS = decisions or {}
        self.rules.SWEEP = list(sweep)

    def run_router(self, numbers=(41,), send=True, when=NOW, only=None,
                   pages=(), github=None, remind_s=7200, forget_s=0,
                   seed=False):
        self.github = github or GitHub(
            base.response([node(n) for n in numbers], []), *pages,
            events=self.events)
        self.lines.clear()
        self.posts.clear()
        return r.run(self.github, self.roster, self.dir, send, self.post,
                     only, self.lines.append, when=when, remind_s=remind_s,
                     forget_s=forget_s, seed=seed)

    def state(self):
        return json.loads(pathlib.Path(self.dir, "state.json").read_text())

    def journal(self, name="wakes.jsonl"):
        path = pathlib.Path(self.dir, name)
        if not path.exists():
            return []
        return [json.loads(l) for l in path.read_text().splitlines()]


class SeveralPerItem(EngineCase):

    def test_two_agents_one_item(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea", kind="notice")]})
        self.assertEqual(self.run_router(), 0)
        self.assertEqual([s for s, _ in self.posts], ["TeamAnn", "TeamBea"])
        self.assertEqual(self.state()["items"]["41"], [
            {"agent": "Ann", "rule": "t", "event": E1,
             "woken_at": "2026-10-09T14:00:00Z"},
            {"agent": "Bea", "rule": "t", "event": E1,
             "woken_at": "2026-10-09T14:00:00Z"}])
        self.run_router(when=later(minutes=1))
        self.assertEqual(self.posts, [])

    def test_one_agent_two_events_is_two_turns_in_one_message(self):
        self.decide({41: [turn(41, "Ann", E1, "#41 one"),
                          turn(41, "Ann", E2, "#41 two")]})
        self.run_router()
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "#41 one\n#41 two\n")])
        self.assertEqual(len(self.state()["items"]["41"]), 2)

    def test_a_repeated_key_is_one_turn(self):
        self.decide({41: [turn(41, "Ann", line="#41 a"),
                          turn(41, "Ann", line="#41 b")]})
        self.run_router()
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "#41 a\n")])

    def test_one_turn_ending_keeps_the_other(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.run_router()
        self.decide({41: [turn(41, "Bea")]})
        self.run_router(when=later(minutes=1))
        self.assertEqual(self.posts, [])
        self.assertEqual(self.state()["items"]["41"]["agent"], "Bea")
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.run_router(when=later(minutes=2))
        self.assertEqual([s for s, _ in self.posts], ["TeamAnn"])

    def test_one_agents_failure_leaves_the_items_turns_as_they_were(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.refuse = {"TeamAnn": "HTTP 503 queue-full"}
        self.assertEqual(self.run_router(), 1)
        self.assertEqual(self.state()["items"]["41"]["agent"], "Bea")
        self.refuse = {}
        self.run_router(when=later(minutes=1))
        self.assertEqual([s for s, _ in self.posts], ["TeamAnn"])


class NoItem(EngineCase):

    STALL = Decision(None, "Ann", "stall", E1, "the queue is stalled")

    def test_keyed_on_its_rule_and_woken_once(self):
        self.decide(sweep=[self.STALL])
        self.run_router(numbers=())
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "the queue is stalled\n")])
        self.assertEqual(self.state()["items"], {"-stall": {
            "agent": "Ann", "rule": "stall", "event": E1,
            "woken_at": "2026-10-09T14:00:00Z"}})
        self.assertIn("SENT to TeamAnn: -stall", self.lines)
        self.run_router(numbers=(), when=later(minutes=1))
        self.assertEqual(self.posts, [])

    def test_another_rule_is_another_turn_and_items_come_first(self):
        self.decide({41: [turn(41, "Ann", line="#41 x")]}, sweep=[
            self.STALL, Decision(None, "Ann", "red", E1, "main is red")])
        self.run_router()
        self.assertEqual(self.posts[0][1], "From: Router\nYour turn:\n"
                         "#41 x\nthe queue is stalled\nmain is red\n")
        self.assertEqual(sorted(self.state()["items"]),
                         ["-red", "-stall", "41"])

    def test_forgotten_when_no_longer_decided(self):
        self.decide(sweep=[self.STALL])
        self.run_router(numbers=())
        self.decide()
        self.run_router(numbers=(), when=later(minutes=1))
        self.assertEqual(self.state()["items"], {})

    def test_a_dry_run_says_already_woken(self):
        self.decide(sweep=[self.STALL])
        self.run_router(numbers=())
        self.run_router(numbers=(), send=False)
        self.assertIn("-stall Ann's, rule stall: already woken", self.lines)


class TodaysState(EngineCase):
    """A state file written before several turns per item still means
    what it meant: whatever it records was woken."""

    OLD = {"items": {
        "41": {"agent": "Ann", "rule": 8, "event": E1,
               "woken_at": "2026-10-09T13:00:00Z"},
        "43": {"agent": "Carl", "rule": 4, "event": E1, "withheld": True,
               "at": "2026-10-09T13:00:00Z"}}}

    def write_old(self):
        os.makedirs(self.dir)
        pathlib.Path(self.dir, "state.json").write_text(json.dumps(self.OLD))

    def test_an_old_entry_is_already_woken(self):
        self.write_old()
        self.decide({41: [turn(41, "Ann")], 43: [turn(43, "Carl")]})
        self.assertEqual(self.run_router((41, 43)), 0)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.journal(), [])
        self.assertEqual(self.state(), self.OLD)

    def test_an_old_entry_reminds_on_the_old_interval(self):
        self.write_old()
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(when=later(hours=1), remind_s=3600)
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "#41 for Ann (reminder)\n")])
        self.assertEqual(self.state()["items"]["41"]["reminders"], 1)

    def test_a_second_turn_joins_the_old_one(self):
        self.write_old()
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.run_router()
        self.assertEqual([s for s, _ in self.posts], ["TeamBea"])
        self.assertEqual(self.state()["items"]["41"][0], self.OLD["items"]["41"])

    def test_the_default_rules_read_it_as_before(self):
        # A deployed router's state, from before: #41 woken for review.
        self.write_old()
        old = dict(self.OLD["items"]["41"], agent="Reviewer", event=base.T1)
        pathlib.Path(self.dir, "state.json").write_text(json.dumps(
            {"items": {"41": old}}))
        with mock.patch.object(r, "RULES", base.rules):
            rc = r.run(base.FakeGitHub(base.response([base.pr_node(41)], [])),
                       base.ROSTER, self.dir, True, self.post, None,
                       self.lines.append, when=NOW)
        self.assertEqual((rc, self.posts), (0, []))
        self.assertEqual(self.state(), {"items": {"41": old}})


class Sweep(EngineCase):

    def test_it_runs_once_a_run_and_is_told_nothing_without_only(self):
        self.decide(sweep=[turn(43, "Bea")])
        self.run_router((41, 43))
        self.assertEqual(self.rules.swept, [None])
        self.assertEqual([s for s, _ in self.posts], ["TeamBea"])

    def test_with_only_it_is_told_the_items_and_the_rest_is_dropped(self):
        self.decide(sweep=[turn(41, "Ann"), turn(43, "Bea"),
                           Decision(None, "Ann", "stall", E1, "stalled")])
        self.run_router((41, 43), only={41})
        self.assertEqual(self.rules.swept, [frozenset({41})])
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "#41 for Ann\n")])

    def test_only_leaves_other_items_turns_alone(self):
        self.decide(sweep=[turn(43, "Bea"),
                           Decision(None, "Ann", "stall", E1, "stalled")])
        self.run_router((41, 43))
        self.decide()
        self.run_router((41, 43), only={41}, when=later(minutes=1))
        self.assertEqual(sorted(self.state()["items"]), ["-stall", "43"])


class Kinds(EngineCase):

    def test_sections_after_the_turns_each_not_your_turn(self):
        self.decide({41: [turn(41, "Ann", line="#41 yours"),
                          turn(41, "Ann", E2, "#41 fyi", kind="notice")],
                     43: [turn(43, "Ann", line="#43 again", kind="relook"),
                          turn(43, "Ann", E2, "#43 fyi", kind="notice")]})
        self.run_router((41, 43))
        self.assertEqual(self.posts, [("TeamAnn",
            "From: Router\n"
            "Your turn:\n#41 yours\n"
            "\n"
            "For your information (not your turn):\n#41 fyi\n#43 fyi\n"
            "\n"
            "Worth another look (not your turn):\n#43 again\n")])

    def test_no_turns_means_no_turn_heading(self):
        self.decide({41: [turn(41, "Bea", line="#41 fyi", kind="notice")]})
        self.run_router()
        self.assertEqual(self.posts, [("TeamBea", "From: Router\n"
            "For your information (not your turn):\n#41 fyi\n")])

    def test_turns_alone_are_the_message_there_always_was(self):
        self.assertEqual(r.message(["#1 a", "#2 b"]),
                         "From: Router\nYour turn:\n#1 a\n#2 b\n")

    def test_a_surface_is_printed_every_run_journalled_once_never_sent(self):
        d = Decision(41, None, 7, None, None, kind="surface",
                     suppressed="nobody can review it")
        self.decide({41: [d]})
        for minutes in (0, 1):
            self.assertEqual(self.run_router(when=later(minutes=minutes)), 0)
            self.assertIn("#41 nobody's (rule 7: nobody can review it)",
                          self.lines)
            self.assertEqual(self.posts, [])
        self.assertEqual([(w["item"], w["kind"], w["outcome"])
                          for w in self.journal()],
                         [(41, "surface", "surfaced: nobody can review it")])
        self.assertTrue(self.state()["items"]["41"]["surfaced"])
        self.run_router(send=False)
        self.assertIn("#41 nobody's (rule 7: nobody can review it)",
                      self.lines)
        self.assertEqual([w["outcome"] for w in self.journal("dry-run.jsonl")],
                         ["surfaced: nobody can review it"])

    def test_the_default_rules_rule_7_is_a_surface(self):
        prs = [base.pr_node(9, author=base.REVIEWER)]
        for minutes in (0, 1):
            self.lines.clear()
            with mock.patch.object(r, "RULES", base.rules):
                rc = r.run(base.FakeGitHub(base.response(prs, [])),
                           base.ROSTER, self.dir, True, self.post, None,
                           self.lines.append, when=later(minutes=minutes))
            self.assertEqual(rc, 0)
            self.assertEqual(self.lines[0],
                             "#9 nobody's (rule 7: green, and Reviewer's own:"
                             " no second reviewer)")
        self.assertEqual(self.posts, [])
        self.assertEqual([(w["rule"], w["outcome"]) for w in self.journal()], [
            (7, "surfaced: green, and Reviewer's own: no second reviewer")])

    def test_what_cant_be_carried_out_is_refused(self):
        for d in (turn(41, "Ann", kind="ping"),
                  turn(41, "Ann", reminder=600),
                  turn(41, "Ann", actions=[AddLabel(41, "x")]),
                  turn(41, "Ann", actions=("label x",)),
                  Decision(41, None, 1, None, None, suppressed="no",
                           actions=(AddLabel(41, "x"),)),
                  Decision(41, "Ann", 1, E1, None),
                  Decision("41", "Ann", 1, E1, "x")):
            with self.subTest(d=d):
                self.decide({41: [d]})
                with self.assertRaises(ValueError):
                    self.run_router()


class Actions(EngineCase):

    LABEL = AddLabel(41, "Reviewer:Bea")

    def test_done_as_the_merger_before_delivery(self):
        self.decide({41: [turn(41, "Bea", actions=(self.LABEL,))]})
        self.assertEqual(self.run_router(), 0)
        self.assertEqual(self.github.writes, [
            ("POST", "repos/example/widget/issues/41/labels",
             {"labels[]": "Reviewer:Bea"})])
        self.assertEqual(self.events, ["api", "write", "post"])
        self.assertIn("DONE add label Reviewer:Bea to #41", self.lines)
        self.assertEqual(self.journal()[0]["actions"],
                         ["add label Reviewer:Bea to #41"])

    def test_only_when_the_decision_is_due(self):
        self.decide({41: [turn(41, "Bea", actions=(self.LABEL,))]})
        self.run_router()
        self.run_router(when=later(minutes=1))
        self.assertEqual(self.github.writes, [])

    def test_a_failed_action_is_logged_and_the_wake_still_goes_out(self):
        self.decide({41: [turn(41, "Bea", actions=(self.LABEL,
                                                   AddLabel(41, "y")))]})
        github = GitHub(base.response([node(41)], []), events=self.events,
                        refuse="HTTP 403")
        self.assertEqual(self.run_router(github=github), 0)
        self.assertEqual(len(github.writes), 2)
        self.assertIn("ACTION FAILED: add label Reviewer:Bea to #41: HTTP 403",
                      self.lines)
        self.assertEqual(len(self.posts), 1)
        (wake,) = self.journal()
        self.assertEqual((wake["outcome"], wake["actions"]), ("sent", [
            "failed: add label Reviewer:Bea to #41: HTTP 403",
            "failed: add label y to #41: HTTP 403"]))

    def test_an_action_that_raises_is_a_failed_one(self):
        class Boom(router.Action):
            def describe(self):
                return "explode"

            def run(self, github, repo):
                raise RuntimeError("bang")
        self.decide({41: [turn(41, "Bea", actions=(Boom(),))]})
        self.assertEqual(self.run_router(), 0)
        self.assertIn("ACTION FAILED: explode: bang", self.lines)

    def test_a_dry_run_prints_them_and_does_nothing(self):
        self.decide({41: [turn(41, "Bea", actions=(self.LABEL,))]})
        self.run_router(send=False)
        self.assertEqual(self.github.writes, [])
        self.assertIn("WOULD add label Reviewer:Bea to #41", self.lines)

    def test_a_withheld_or_surfaced_decision_acts_once(self):
        self.decide({41: [turn(41, "Carl", actions=(self.LABEL,))],
                     43: [Decision(43, None, 7, None, None, kind="surface",
                                   suppressed="x",
                                   actions=(AddLabel(43, "stuck"),))]})
        self.run_router((41, 43))
        self.assertEqual([w[1] for w in self.github.writes], [
            "repos/example/widget/issues/41/labels",
            "repos/example/widget/issues/43/labels"])
        self.run_router((41, 43), when=later(minutes=1))
        self.assertEqual(self.github.writes, [])

    def test_add_label_describes_itself(self):
        self.assertEqual(self.LABEL.describe(),
                         "add label Reviewer:Bea to #41")


class Reminders(EngineCase):

    def times(self, policy, until_s, step_s=60):
        """The seconds after the first wake at which each reminder went
        out, running every `step_s`."""
        self.decide({41: [turn(41, "Ann", reminder=policy)]})
        self.run_router()
        sent = []
        for s in range(step_s, until_s + 1, step_s):
            self.run_router(when=later(seconds=s))
            if self.posts:
                sent.append(s)
        return sent

    def test_first_then_doubling_to_the_ceiling(self):
        self.assertEqual(self.times(Reminder(600, 2400, jitter=0), 9000),
                         [600, 1800, 4200, 6600, 9000])
        self.assertEqual(self.state()["items"]["41"]["reminders"], 5)

    def test_jitter_only_delays(self):
        policy = Reminder(600, 2400, jitter=0.5)
        sent = self.times(policy, 9000)
        gaps = [b - a for a, b in zip([0] + sent, sent)]
        for gap, interval in zip(gaps, (600, 1200, 2400, 2400)):
            self.assertGreaterEqual(gap, interval)
            self.assertLessEqual(gap, interval * 1.5 + 60)
        self.assertNotEqual(sent[:3], [600, 1800, 4200])

    def test_the_reminder_line_says_so(self):
        self.times(Reminder(600, 600, jitter=0), 600)
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "#41 for Ann (reminder)\n")])

    def test_none_is_the_fixed_interval_and_counts_too(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(remind_s=600)
        sent = []
        for s in range(300, 1801, 300):
            self.run_router(when=later(seconds=s), remind_s=600)
            if self.posts:
                sent.append(s)
        self.assertEqual(sent, [600, 1200, 1800])
        self.assertEqual(self.state()["items"]["41"]["reminders"], 3)


class Recheck(EngineCase):

    def recheck_with(self, again):
        """Give the rules a recheck answering `again(item)`."""
        self.rechecked = []

        def recheck(item, ctx):
            self.rechecked.append((item["number"], item["title"],
                                   sorted(ctx.items)))
            return again(item)
        self.rules.recheck = recheck

    def fresh(self, **items):
        """The recheck query's answer: n<number> -> (typename, state)."""
        return {"data": {"repository": {
            k: (dict(node(int(k[1:]), ), title="fresh", __typename=v[0],
                     state=v[1]) if v else None)
            for k, v in items.items()}}}

    def test_a_decision_that_holds_is_sent(self):
        self.decide({41: [turn(41, "Ann")], 43: [turn(43, "Ann")]})
        self.recheck_with(lambda item: [turn(item["number"], "Ann",
                                             line="changed")])
        rc = self.run_router((41, 43, 45), pages=[self.fresh(
            n41=("PullRequest", "OPEN"), n43=("Issue", "OPEN"))])
        self.assertEqual(rc, 0)
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "#41 for Ann\n#43 for Ann\n")])
        query = self.github.calls[1][2]
        self.assertIn("n41: issueOrPullRequest(number:41)", query)
        self.assertIn("n43: issueOrPullRequest(number:43)", query)
        self.assertNotIn("n45", query)
        self.assertEqual(self.rechecked, [(41, "fresh", [41, 43, 45]),
                                          (43, "fresh", [41, 43, 45])])

    def test_one_that_no_longer_holds_is_withdrawn(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.recheck_with(lambda item: [turn(41, "Bea", E2)])
        rc = self.run_router(pages=[self.fresh(n41=("PullRequest", "OPEN"))])
        self.assertEqual((rc, self.posts), (0, []))
        self.assertIn("#41 Ann's, rule t: withdrawn on recheck "
                      "(no longer decided)", self.lines)
        self.assertEqual([w["outcome"] for w in self.journal()],
                         ["withdrawn on recheck: no longer decided"] * 2)
        self.assertEqual(self.state()["items"], {})
        self.assertIn("; 2 withdrawn on recheck", self.lines[-1])

    def test_a_closed_item_is_withdrawn(self):
        self.decide({41: [turn(41, "Ann")]})
        self.recheck_with(lambda item: [turn(41, "Ann")])
        for answer in (("PullRequest", "MERGED"), None):
            with self.subTest(answer=answer):
                self.run_router(pages=[self.fresh(n41=answer)])
                self.assertEqual(self.posts, [])
                self.assertEqual(self.rechecked, [])
                self.assertEqual(self.journal()[-1]["outcome"],
                                 "withdrawn on recheck: closed")

    def test_a_failed_recheck_is_not_sent_and_stays_due(self):
        self.decide({41: [turn(41, "Ann")]}, sweep=[
            Decision(None, "Ann", "stall", E1, "stalled")])
        self.recheck_with(lambda item: [turn(41, "Ann")])
        self.assertEqual(self.run_router(), 1)    # no page for the recheck
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:\n"
                                       "stalled\n")])
        self.assertEqual(self.journal()[0]["outcome"][:32],
                         "not sent: recheck failed: no pag")
        self.assertEqual(sorted(self.state()["items"]), ["-stall"])
        self.decide({41: [turn(41, "Ann")]})
        self.assertEqual(self.run_router(when=later(minutes=1)), 2)
        self.assertEqual(self.run_router(when=later(minutes=2), pages=[
            self.fresh(n41=("PullRequest", "OPEN"))]), 0)
        self.assertEqual(len(self.posts), 1)

    def test_without_a_recheck_of_their_own_nothing_is_fetched_again(self):
        self.decide({41: [turn(41, "Ann")]})
        self.assertIs(self.rules.recheck, self.rules.decide)
        self.run_router()
        self.assertEqual(len(self.github.calls), 1)

    def test_a_dry_run_does_not_recheck(self):
        self.decide({41: [turn(41, "Ann")]})
        self.recheck_with(lambda item: [])
        self.run_router(send=False)
        self.assertEqual(len(self.github.calls), 1)

    def test_the_lookups_run_over_the_fresh_items(self):
        seen = []
        self.rules.lookups = (router.Lookup("x", lambda g, repo, prs, issues:
                                            seen.append((len(prs),
                                                         len(issues)))),)
        self.decide({41: [turn(41, "Ann")]})
        self.recheck_with(lambda item: [turn(41, "Ann")])
        self.run_router((41, 43), pages=[self.fresh(n41=("Issue", "OPEN"))])
        self.assertEqual(seen, [(2, 0), (0, 1)])


class ExitStatus(EngineCase):

    def test_nothing_due_is_zero(self):
        self.decide({41: [Decision(41, None, 1, None, None, suppressed="no"),
                          turn(41, "Carl")]})
        self.assertEqual(self.run_router(), 0)

    def test_everything_delivered_is_zero(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.assertEqual(self.run_router(), 0)

    def test_some_delivered_is_one(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.refuse = {"TeamBea": "HTTP 503 queue-full"}
        self.assertEqual(self.run_router(), 1)

    def test_none_delivered_is_two(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.refuse = {"TeamAnn": "HTTP 503", "TeamBea": "HTTP 503"}
        self.assertEqual(self.run_router(), 2)

    def test_a_dry_run_is_zero(self):
        self.decide({41: [turn(41, "Ann")]})
        self.refuse = {"TeamAnn": "HTTP 503"}
        self.assertEqual(self.run_router(send=False), 0)


def merged_search(*numbers):
    """One page of the merged search, each pull request labelled."""
    return {"data": {"search": {
        "pageInfo": {"hasNextPage": False, "endCursor": None},
        "nodes": [{"number": n, "title": "x", "mergedAt": E1,
                   "author": {"login": "ann"},
                   "labels": {"nodes": [{"name": "Owner:Bea"}]}}
                  for n in numbers]}}}


class Merged(EngineCase):
    """`graphql["Merged"]`: the module's fields on `ctx.merged`."""

    def test_the_fields_are_asked_for_and_flattened(self):
        self.rules.merged_window = 30
        self.rules.graphql = dict(self.rules.graphql,
                                  Merged="labels(first:20){nodes{name}}")
        self.rules.flatten = ("labels",)
        self.decide()
        self.run_router(pages=[merged_search(50)])
        query = next(a for a in self.github.calls[1]
                     if a.startswith("query="))
        self.assertIn("author { login }\n", query)
        self.assertIn("labels(first:20){nodes{name}}", query)
        (ctx,) = self.rules.contexts
        self.assertEqual(ctx.merged[0]["labels"], [{"name": "Owner:Bea"}])
        self.assertNotIn("__typename", ctx.merged[0])

    def test_without_the_entry_the_search_asks_for_what_it_did(self):
        self.rules.merged_window = 30
        self.decide()
        self.run_router(pages=[merged_search(50)])
        query = next(a for a in self.github.calls[1]
                     if a.startswith("query="))
        self.assertNotIn("labels", query)
        self.assertIn("number title mergedAt author { login }", query)


class Previous(EngineCase):
    """`previous.json` and `ctx.previous`: the last sending run's
    decisions."""

    def setUp(self):
        super().setUp()
        self.rules.wants_previous = True

    def previous(self):
        return json.loads(pathlib.Path(self.dir,
                                       "previous.json").read_text())

    def test_none_before_the_first_run(self):
        self.decide()
        self.run_router()
        self.assertIsNone(self.rules.contexts[0].previous)

    def test_a_sending_run_records_every_decision(self):
        self.decide({41: [turn(41, "Ann"),
                          turn(41, "Bea", kind="notice"),
                          Decision(41, None, 1, None, None, suppressed="no")]},
                    sweep=[Decision(None, "Ann", "red", E1, "main is red")])
        self.run_router()
        want = {"at": "2026-10-09T14:00:00Z", "items": {
            "41": [{"agent": "Ann", "rule": "t", "event": E1,
                    "kind": "turn", "suppressed": None},
                   {"agent": "Bea", "rule": "t", "event": E1,
                    "kind": "notice", "suppressed": None},
                   {"agent": None, "rule": 1, "event": None,
                    "kind": "turn", "suppressed": "no"}],
            "-red": [{"agent": "Ann", "rule": "red", "event": E1,
                      "kind": "turn", "suppressed": None}]}}
        self.assertEqual(self.previous(), want)
        self.decide()
        self.run_router(when=later(minutes=1))
        self.assertEqual(self.rules.contexts[1].previous, want)
        self.assertEqual(self.previous(), {"at": "2026-10-09T14:01:00Z",
                                           "items": {}})

    def test_it_is_written_whether_or_not_the_rules_read_it(self):
        self.rules.wants_previous = False
        self.decide({41: [turn(41, "Ann")]})
        self.run_router()
        self.run_router(when=later(minutes=1))
        self.assertEqual(list(self.previous()["items"]), ["41"])
        self.assertIsNone(self.rules.contexts[1].previous)

    def test_an_only_run_replaces_only_its_items(self):
        self.decide({41: [turn(41, "Ann")], 43: [turn(43, "Bea")]},
                    sweep=[Decision(None, "Ann", "red", E1, "main is red")])
        self.run_router((41, 43))
        self.decide({43: [turn(43, "Ann", E2)]})
        self.run_router((41, 43), only={41, 43}, when=later(minutes=1))
        got = self.previous()
        self.assertEqual(sorted(got["items"]), ["-red", "43"])
        self.assertEqual(got["items"]["43"][0]["event"], E2)

    def test_a_dry_run_writes_nothing(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(send=False)
        self.assertFalse(pathlib.Path(self.dir, "previous.json").exists())

    def test_a_run_that_cant_read_the_queue_writes_nothing(self):
        self.decide({41: [turn(41, "Ann")]})
        with self.assertRaises(AssertionError):
            self.run_router(github=GitHub())
        self.assertFalse(pathlib.Path(self.dir, "previous.json").exists())

    def test_unreadable_is_none(self):
        os.makedirs(self.dir)
        for text in ("not json", "[]", '{"at": "x"}', '{"items": []}'):
            with self.subTest(text=text):
                pathlib.Path(self.dir, "previous.json").write_text(text)
                self.rules.contexts.clear()
                self.decide()
                self.run_router(send=False)
                self.assertIsNone(self.rules.contexts[0].previous)

    def test_the_recheck_is_handed_it_too(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router()
        seen = []

        def recheck(item, ctx):
            seen.append(ctx.previous)
            return [turn(41, "Ann", E2)]
        self.rules.recheck = recheck
        self.decide({41: [turn(41, "Ann", E2)]})
        self.run_router(when=later(minutes=1), pages=[{"data": {
            "repository": {"n41": dict(node(41), __typename="Issue",
                                       state="OPEN")}}}])
        self.assertEqual(list(seen[0]["items"]), ["41"])

    def test_a_module_without_it_reads_nothing(self):
        with tempfile.TemporaryDirectory() as root:
            with open(os.path.join(root, "rules.py"), "w") as handle:
                handle.write("graphql = {}\nRoster = None\n"
                             "def configure(p, m): pass\n"
                             "def decide(i, c): return []\n")
            self.assertIs(router.load_rules("rules.py", root).wants_previous,
                          False)


class Heartbeat(router.Action):
    """An `each_run` action: records what it saw, and may fail."""

    def __init__(self, test, name="beat", fail=None):
        self.test, self.name, self.fail = test, name, fail

    def describe(self):
        return "rewrite the %s" % self.name

    def run(self, github, repo):
        state = pathlib.Path(self.test.dir, "state.json")
        self.test.events.append("beat")
        self.test.beats.append((repo, state.exists()))
        if isinstance(self.fail, Exception):
            raise self.fail
        return (False, self.fail) if self.fail else (True, "")


class EachRun(EngineCase):
    """`each_run(ctx)`: actions once a sending run, after delivery."""

    def setUp(self):
        super().setUp()
        self.beats, self.given = [], []

    def each(self, *actions):
        def each_run(ctx):
            self.given.append(ctx)
            return list(actions)
        self.rules.each_run = each_run

    def test_once_after_delivery_and_recording(self):
        self.each(Heartbeat(self))
        self.decide({41: [turn(41, "Ann")]})
        self.assertEqual(self.run_router(), 0)
        self.assertEqual(self.events, ["api", "post", "beat"])
        self.assertEqual(self.beats, [("example/widget", True)])
        self.assertIn("DONE rewrite the beat", self.lines)
        self.assertEqual(self.given[0].when, NOW)
        self.assertEqual([w["outcome"] for w in self.journal()], ["sent"])

    def test_on_every_run_due_or_not(self):
        self.each(Heartbeat(self))
        self.decide()
        self.run_router()
        self.run_router(when=later(minutes=1))
        self.assertEqual(len(self.beats), 2)

    def test_not_on_an_only_run(self):
        self.each(Heartbeat(self))
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(only={41})
        self.run_router(only={41}, send=False)
        self.assertEqual((self.beats, self.given), ([], []))

    def test_a_dry_run_prints_them_and_does_nothing(self):
        self.each(Heartbeat(self), Heartbeat(self, "status"))
        self.decide()
        self.assertEqual(self.run_router(send=False), 0)
        self.assertEqual(self.beats, [])
        self.assertEqual(self.lines[-2:], ["WOULD rewrite the beat",
                                           "WOULD rewrite the status"])
        self.assertEqual(self.journal(), [])

    def test_a_failed_one_is_journalled_and_the_rest_go_on(self):
        self.each(Heartbeat(self, fail="HTTP 403"),
                  Heartbeat(self, "status", fail=RuntimeError("bang")),
                  Heartbeat(self, "last"))
        self.decide({41: [turn(41, "Ann")]})
        self.assertEqual(self.run_router(), 0)
        self.assertEqual(len(self.beats), 3)
        self.assertIn("ACTION FAILED: rewrite the beat: HTTP 403", self.lines)
        self.assertIn("ACTION FAILED: rewrite the status: bang", self.lines)
        self.assertEqual(self.journal()[1:], [
            {"kind": "run", "outcome": "failed: rewrite the beat: HTTP 403",
             "at": "2026-10-09T14:00:00Z"},
            {"kind": "run", "outcome": "failed: rewrite the status: bang",
             "at": "2026-10-09T14:00:00Z"}])

    def test_the_exit_status_is_the_wakes(self):
        self.each(Heartbeat(self, fail="HTTP 500"))
        self.decide({41: [turn(41, "Ann")]})
        self.refuse = {"TeamAnn": "HTTP 503"}
        self.assertEqual(self.run_router(), 2)
        self.assertEqual(len(self.beats), 1)

    def test_rules_that_raise_or_give_something_else(self):
        def boom(ctx):
            raise RuntimeError("no state")
        for each_run, why in ((boom, "no state"),
                              (lambda ctx: ["x"], "'x' is not an Action")):
            with self.subTest(why=why):
                self.rules.each_run = each_run
                self.decide()
                self.assertEqual(self.run_router(), 0)
                self.assertIn("ACTION FAILED: each_run: " + why, self.lines)
                self.assertEqual(self.journal()[-1]["outcome"],
                                 "failed: each_run: " + why)

    def test_none_without_it(self):
        self.decide({41: [turn(41, "Ann")]})
        self.assertEqual(self.rules.each_run(None), ())
        self.run_router()
        self.assertEqual(self.events, ["api", "post"])


class Woken(EngineCase):
    """`Decision.woken`: a turn someone woke some other way."""

    WOKEN = "Woke: comment 700"

    def test_recorded_as_woken_and_not_sent(self):
        self.decide({41: [turn(41, "Ann", woken=self.WOKEN,
                               actions=(AddLabel(41, "x"),))]})
        self.assertEqual(self.run_router(), 0)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.state()["items"]["41"], {
            "agent": "Ann", "rule": "t", "event": E1,
            "woken_at": "2026-10-09T14:00:00Z"})
        self.assertIn("#41 Ann's, rule t: marked woken: Woke: comment 700",
                      self.lines)
        (rec,) = self.journal()
        self.assertEqual((rec["outcome"], rec["actions"]),
                         ("marked woken: Woke: comment 700",
                          ["add label x to #41"]))
        self.assertEqual(len(self.github.writes), 1)
        self.assertIn("1 marked woken", self.lines[-1])

    def test_then_an_ordinary_turn(self):
        self.decide({41: [turn(41, "Ann", woken=self.WOKEN)]})
        self.run_router(remind_s=600)
        self.run_router(when=later(seconds=599), remind_s=600)
        self.assertEqual(self.posts, [])
        self.run_router(when=later(seconds=600), remind_s=600)
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:"
                                       "\n#41 for Ann (reminder)\n")])
        self.assertEqual(self.state()["items"]["41"]["reminders"], 1)
        self.decide({41: [turn(41, "Ann", E2)]})
        self.run_router(when=later(seconds=601), remind_s=600)
        self.assertEqual(len(self.posts), 1)

    def test_no_effect_on_a_turn_already_recorded(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(remind_s=600)
        self.decide({41: [turn(41, "Ann", woken=self.WOKEN)]})
        self.run_router(when=later(seconds=600), remind_s=600)
        self.assertEqual(len(self.posts), 1)

    def test_a_dry_run_says_so(self):
        self.decide({41: [turn(41, "Ann", woken=self.WOKEN)]})
        self.run_router(send=False)
        self.assertIn("#41 Ann's, rule t: marked woken: Woke: comment 700",
                      self.lines)
        self.assertEqual([w["outcome"] for w in self.journal("dry-run.jsonl")],
                         ["would be marked woken: Woke: comment 700"])
        self.assertFalse(pathlib.Path(self.dir, "state.json").exists())

    def test_not_an_agent_is_withheld_as_ever(self):
        self.decide({41: [turn(41, "Carl", woken=self.WOKEN)]})
        self.run_router()
        self.assertTrue(self.state()["items"]["41"]["withheld"])

    def test_it_must_say_why(self):
        self.decide({41: [turn(41, "Ann", woken=True)]})
        with self.assertRaises(ValueError):
            self.run_router()


class Forget(EngineCase):
    """`router.forget_minutes`: a turn no run decides is kept a while."""

    HOUR = 3600

    def test_without_it_an_undecided_turn_is_forgotten_at_once(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router()
        self.assertNotIn("decided_at", self.state()["items"]["41"])
        self.decide()
        self.run_router(when=later(minutes=1))
        self.assertEqual(self.state()["items"], {})

    def test_a_flap_within_it_is_one_turn(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(forget_s=self.HOUR, remind_s=7200)
        self.decide()
        self.run_router(when=later(minutes=30), forget_s=self.HOUR)
        self.assertEqual(self.state()["items"]["41"]["decided_at"],
                         "2026-10-09T14:00:00Z")
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(when=later(minutes=50), forget_s=self.HOUR)
        self.assertEqual(self.posts, [])
        self.assertEqual(self.state()["items"]["41"]["decided_at"],
                         "2026-10-09T14:50:00Z")
        # Its reminder counts from the wake, as if it never flapped.
        self.run_router(when=later(hours=2), forget_s=self.HOUR)
        self.assertEqual(self.posts, [("TeamAnn", "From: Router\nYour turn:"
                                       "\n#41 for Ann (reminder)\n")])

    def test_forgotten_once_it_has_passed(self):
        self.decide({41: [turn(41, "Ann")], 43: [turn(43, "Bea")]})
        self.run_router((41, 43), forget_s=self.HOUR)
        self.decide({43: [turn(43, "Bea")]})
        self.run_router((41, 43), when=later(minutes=60), forget_s=self.HOUR)
        self.assertEqual(sorted(self.state()["items"]), ["41", "43"])
        self.run_router((41, 43), when=later(minutes=61), forget_s=self.HOUR)
        self.assertEqual(sorted(self.state()["items"]), ["43"])
        self.decide({41: [turn(41, "Ann")]})
        self.run_router((41, 43), when=later(minutes=62), forget_s=self.HOUR)
        self.assertEqual([s for s, _ in self.posts], ["TeamAnn"])

    def test_not_reminded_while_undecided(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router(forget_s=10 * self.HOUR, remind_s=600)
        self.decide()
        self.run_router(when=later(hours=1), forget_s=10 * self.HOUR,
                        remind_s=600)
        self.assertEqual(self.posts, [])

    def test_a_record_from_before_it_counts_from_its_wake(self):
        os.makedirs(self.dir)
        pathlib.Path(self.dir, "state.json").write_text(json.dumps(
            {"items": {"41": {"agent": "Ann", "rule": "t", "event": E1,
                              "woken_at": "2026-10-09T13:30:00Z"}}}))
        self.decide()
        self.run_router(forget_s=self.HOUR)
        self.assertIn("41", self.state()["items"])
        self.run_router(when=later(minutes=31), forget_s=self.HOUR)
        self.assertEqual(self.state()["items"], {})

    def test_an_only_run_forgets_nothing(self):
        self.decide({41: [turn(41, "Ann"), turn(41, "Bea")]})
        self.run_router(forget_s=self.HOUR)
        self.decide({41: [turn(41, "Bea")]})
        self.run_router(only={41}, when=later(hours=2), forget_s=self.HOUR)
        self.assertEqual(len(self.state()["items"]["41"]), 2)
        self.run_router(when=later(hours=2), forget_s=self.HOUR)
        self.assertEqual(self.state()["items"]["41"]["agent"], "Bea")

    def test_from_the_configuration(self):
        project = base.config.Project(dict(base.example(), router={
            "forget_minutes": 360}), None)
        r.configure(project)
        self.addCleanup(r.configure, base.config.Project(base.example(),
                                                         None))
        self.assertEqual(r.FORGET_MINUTES, 360)
        r.RULES = self.rules
        self.decide({41: [turn(41, "Ann")]})
        r.run(GitHub(base.response([node(41)], [])), self.roster, self.dir,
              True, self.post, None, self.lines.append, when=NOW)
        self.decide()
        r.run(GitHub(base.response([node(41)], [])), self.roster, self.dir,
              True, self.post, None, self.lines.append, when=later(hours=5))
        self.assertIn("41", self.state()["items"])


class Seed(EngineCase):
    """`--seed`: a first run that records turns and wakes nobody."""

    def test_every_due_turn_is_recorded_as_woken_and_nothing_sent(self):
        beats = []
        self.rules.each_run = lambda ctx: beats.append(ctx) or []
        self.decide({41: [turn(41, "Ann", actions=(AddLabel(41, "x"),)),
                          turn(41, "Bea", kind="notice")]},
                    sweep=[Decision(None, "Ann", "red", E1, "main is red")])
        self.assertEqual(self.run_router(seed=True), 0)
        self.assertEqual((self.posts, self.github.writes, beats), ([], [], []))
        self.assertEqual(sorted(self.state()["items"]), ["-red", "41"])
        self.assertEqual(self.state()["items"]["-red"]["woken_at"],
                         "2026-10-09T14:00:00Z")
        self.assertEqual([(w["agent"], w["outcome"]) for w in self.journal()],
                         [("Ann", "seeded"), ("Bea", "seeded"),
                          ("Ann", "seeded")])
        self.assertTrue(pathlib.Path(self.dir, "previous.json").exists())
        self.assertEqual(self.lines[-1],
                         "SEEDED 1 open, 3 turn(s) recorded as woken")
        # The next run wakes only what began since.
        self.decide({41: [turn(41, "Ann", actions=(AddLabel(41, "x"),)),
                          turn(41, "Bea", E2, kind="notice")]},
                    sweep=[Decision(None, "Ann", "red", E1, "main is red")])
        self.run_router(when=later(minutes=1))
        self.assertEqual([s for s, _ in self.posts], ["TeamBea"])

    def test_refused_on_a_state_that_records_a_turn(self):
        self.decide({41: [turn(41, "Ann")]})
        self.run_router()
        self.assertEqual(self.run_router(seed=True, when=later(minutes=1)), 2)
        self.assertEqual(self.github.calls, [])
        self.assertTrue(self.lines[0].startswith("SEED REFUSED: "))

    def test_an_empty_state_may_be_seeded(self):
        os.makedirs(self.dir)
        pathlib.Path(self.dir, "state.json").write_text('{"items": {}}')
        self.decide({41: [turn(41, "Ann")]})
        self.assertEqual(self.run_router(seed=True), 0)
        self.assertEqual(self.posts, [])


if __name__ == "__main__":
    unittest.main()
