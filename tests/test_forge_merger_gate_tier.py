"""Tests for merge.gate_tier in forge-merger (docs/designs/0055, step
2): with "train", a head is judged by the quick check's context, and
nothing merges on its own: only a train does, on the full check's green
on its top. A red train's hand-back names the failed steps, and those
the head's quick check doesn't run."""

import os
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests import test_forge_merger as base
from tests.test_forge_merger import (
    GATE, REVIEWER, SLUG, FakeGitHub, RepoCase, ci_run, m, pr, project,
    review, status, tools_run)

QUICK = "vm/build-and-test/quick"


def train_project(quick=None, steps=None):
    """A project with merge.gate_tier "train" and what it needs."""
    data = example()
    data["checks"]["quick"] = {} if quick is None else quick
    if steps is not None:
        data["checks"]["steps"] = steps
    data["merge"] = {"gate": GATE, "own_head_green": True,
                     "gate_tier": "train",
                     "test_ahead": {"batch": 2, "trains": "live",
                                    "train_merge": True}}
    assert config.check(data) == [], config.check(data)
    return config.Project(data, None)


def table(*statuses):
    return {SLUG + "/commits/": list(statuses),
            SLUG + "/actions/runs": {"workflow_runs": [ci_run()]}}


class Configure(unittest.TestCase):

    def setUp(self):
        self.addCleanup(m.configure, project())

    def test_off_by_default(self):
        m.configure(project())
        self.assertEqual(m.GATE_TIER, "head")
        self.assertEqual(m.head_context(), m.STATUS_CONTEXT)
        self.assertEqual(m.GREENS_FILE, "greens.json")

    def test_train_reads_the_quick_context(self):
        m.configure(train_project())
        self.assertEqual(m.GATE_TIER, "train")
        self.assertEqual(m.head_context(), QUICK)
        self.assertEqual(m.STATUS_CONTEXT, "vm/build-and-test")
        # A quick green isn't a full one: their greens are kept apart.
        self.assertEqual(m.GREENS_FILE, "greens-quick.json")
        m.configure(train_project({"context": "ci/quick"}))
        self.assertEqual(m.head_context(), "ci/quick")

    def test_the_step_tiers_are_read(self):
        m.configure(train_project(steps=[
            {"name": "build", "run": ["make"], "tier": "head"},
            {"name": "test", "run": ["make", "test"], "tier": "train"},
            {"name": "lint", "run": ["make", "lint"]}]))
        self.assertEqual(m.STEP_TIERS, {"build": "head", "test": "train",
                                        "lint": "both"})


class Decide(RepoCase):

    def setUp(self):
        super().setUp()
        self.addCleanup(m.configure, project())
        m.configure(train_project())
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)

    def decide(self, *statuses):
        item = pr(5, head=self.head,
                  reviews=[review(REVIEWER, oid=self.approved)])
        return m.decide(item, m.approval_of(item), self.repo.path,
                        self.repo.main_tip(), FakeGitHub(table(*statuses)),
                        {}, tools_run())

    def test_a_quick_green_waits_for_its_train(self):
        self.assertEqual(self.decide(status(context=QUICK)), {
            "action": "WAIT", "reason": m.TRAIN_ONLY_REASON % (
                QUICK, "vm/build-and-test")})

    def test_even_a_full_green_on_the_head_does_not_merge_it(self):
        # Only a train pull request merges (designs/0055).
        got = self.decide(status(context=QUICK), status())
        self.assertEqual(got["action"], "WAIT")

    def test_the_head_is_judged_by_the_quick_context(self):
        self.assertEqual(self.decide(status()), {
            "action": "WAIT", "reason": QUICK + " is absent"})
        self.assertEqual(self.decide(status("pending", context=QUICK)), {
            "action": "WAIT", "reason": QUICK + " is pending"})

    def test_a_red_quick_check_goes_to_the_author(self):
        got = self.decide(status("failure", context=QUICK,
                                 description="build failed"))
        self.assertEqual(got, {"action": "HAND-BACK", "to": "author",
                               "reason": QUICK + " is failure: build "
                               "failed"})
        self.assertTrue(m.red_hand_back(got["reason"]))
        self.assertFalse(m.red_hand_back("vm/build-and-test is failure"))

    def test_under_head_nothing_changes(self):
        m.configure(project())
        self.assertEqual(self.decide(status()), {"action": "MERGE"})
        self.assertEqual(self.decide(status(context=QUICK)), {
            "action": "WAIT", "reason": "vm/build-and-test is absent"})


class OwnGreenWait(unittest.TestCase):

    def setUp(self):
        self.addCleanup(m.configure, project())
        m.configure(train_project())
        self.item = pr(5, head="h" * 40,
                       reviews=[review(REVIEWER, oid="a" * 40)])

    def wait(self, *statuses):
        return m.own_green_wait(self.item, m.approval_of(self.item),
                                FakeGitHub(table(*statuses)), {})

    def test_a_quick_green_makes_a_candidate(self):
        self.assertIsNone(self.wait(status(context=QUICK)))

    def test_a_full_green_alone_does_not(self):
        # A head's full green comes only from a train's top covering
        # it, and the quick context is posted then too.
        self.assertEqual(self.wait(status()), m.GREEN_WAIT_REASON % (
            QUICK, "absent", "hhhhhhhhhh"))


class TickTrains(RepoCase):
    """A whole tick under "train": a lone candidate goes in as a train
    of one, predicted as soon as its quick check is green, and a red
    default branch's fix leads the next train. Tick's helpers, without
    running its tests again."""

    item = base.Decide.item
    tick = base.Tick.tick
    other_pr = base.Tick.other_pr

    def setUp(self):
        super().setUp()
        self.addCleanup(m.configure, project())
        m.configure(train_project())
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        for name, value in (("RESOLVE_IDENTITY", ("Merger", "m@example")),
                            ("CI_WORKFLOW", "")):
            patch = mock.patch.object(m, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        origin = self.repo.origin

        def run(args, cwd=None, env=None):
            if "push" in args:
                args = [origin if a.startswith("https://github.com/")
                        else a for a in args]
            return tools_run()(args, cwd, env)
        self.run_git = run

    def quick_tick(self, prs, **kwargs):
        extra = dict({SLUG + "/commits/": [status(context=QUICK)]},
                     **kwargs.pop("extra", {}))
        return self.tick(prs, conclusions=(), live=True, run=self.run_git,
                         extra=extra, **kwargs)

    def entries(self):
        return (m.load_json(os.path.join(self.state, m.AHEAD_FILE), {})
                .get("entries") or [])

    def test_a_lone_candidate_merges_as_a_train_of_one(self):
        record, _ = self.quick_tick([self.item()])
        # Its quick green made it a candidate: the train of one is
        # predicted now, for a full run, and nothing merged on its own.
        [entry] = self.entries()
        self.assertEqual(entry["pr"], 5)
        self.assertEqual([(d["pr"], d["action"], d.get("reason"))
                          for d in record["decisions"]],
                         [(5, "WAIT", m.TRAIN_ONLY_REASON % (
                             QUICK, "vm/build-and-test"))])
        record, lines = self.quick_tick(
            [self.item()], minutes=1, check_state={"results": {
                entry["sha"]: {"tree": entry["tree"], "ok": True}}},
            extra={SLUG + "/pulls/5": {"merged": True}})
        self.assertEqual([(d["pr"], d["action"], d.get("train"))
                          for d in record["decisions"]],
                         [(5, "MERGE", True)])
        self.assertTrue(any(line.startswith("TRAIN-MERGE #5..#5")
                            for line in lines), lines)

    def test_without_a_quick_green_nothing_is_predicted(self):
        record, _ = self.tick([self.item()], conclusions=(), live=True,
                              run=self.run_git)
        self.assertEqual(self.entries(), [])
        self.assertNotIn("MERGE", [d["action"] for d in record["decisions"]])

    def test_a_red_default_branchs_fix_leads_the_next_train(self):
        prs = [self.item(), self.other_pr()]
        with mock.patch.object(m, "MAIN_RED_FIXES", frozenset({6})):
            record, _ = self.quick_tick(prs)
        self.assertEqual(self.entries()[0]["pr"], 6)
        self.assertEqual(record["decisions"][0]["pr"], 6)
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        self.assertNotIn("MERGE", [d["action"] for d in record["decisions"]])


class RedSteps(unittest.TestCase):

    STEPS = [{"name": "build", "run": ["make"], "tier": "head"},
             {"name": "test", "run": ["make", "test"], "tier": "train"},
             {"name": "doc", "run": ["make", "doc"], "tier": "train"}]

    def setUp(self):
        self.addCleanup(m.configure, project())
        m.configure(train_project(steps=self.STEPS))

    def note(self, failed):
        return m.red_steps_note({"results": {"p" * 40: {
            "ok": False, "failed": failed}}}, "p" * 40)

    def test_names_the_failed_steps(self):
        self.assertEqual(self.note(["build"]), " The run failed build.")

    def test_says_which_the_quick_check_does_not_run(self):
        self.assertEqual(self.note(["build", "test"]), (
            " The run failed build, test. The head's quick check (%s) "
            "doesn't run test (tier \"train\"): run it yourself before "
            "you push again." % QUICK))
        self.assertIn("doesn't run test, doc (tier \"train\"): run them",
                      self.note(["test", "doc"]))

    def test_nothing_without_a_record_or_under_head(self):
        self.assertEqual(m.red_steps_note({}, "p" * 40), "")
        self.assertEqual(self.note([]), "")
        m.configure(project())
        self.assertEqual(self.note(["test"]), "")

    def test_the_suspects_hand_back_carries_it(self):
        entry = {"pr": 5, "head": "h" * 40, "sha": "p" * 40}
        item = pr(5, head="h" * 40, reviews=[review(REVIEWER)])
        record = {"decisions": [], "at": "2026-11-15T12:00:00Z"}
        state = {"results": {"p" * 40: {"ok": False, "failed": ["test"]}}}
        m.hand_back_red_suspect(
            "/nonexistent", entry, 0, [item], {5: m.approval_of(item)}, {},
            FakeGitHub({}), {}, False, record, lambda *_: None,
            check_state=state)
        [decision] = record["decisions"]
        self.assertIn("doesn't run test (tier \"train\")",
                      decision["detail"])


if __name__ == "__main__":
    unittest.main()


class TreeVerdict(unittest.TestCase):
    """A quick run's green never stands for a train's full check, even
    when a train of one's tree is its head's (found by the Merger
    simulation, #391); a quick red stands."""

    def test_a_quick_green_is_no_verdict(self):
        state = {"results": {"h": {"tree": "t", "ok": True,
                                   "tier": "quick"}}}
        self.assertIsNone(m.tree_verdict(state, "t"))

    def test_beside_a_full_green_it_is_green(self):
        state = {"results": {"h": {"tree": "t", "ok": True,
                                   "tier": "quick"},
                             "p": {"tree": "t", "ok": True,
                                   "tier": "full"}}}
        self.assertIs(m.tree_verdict(state, "t"), True)

    def test_a_quick_red_stands(self):
        state = {"results": {"h": {"tree": "t", "ok": False,
                                   "tier": "quick"}}}
        self.assertIs(m.tree_verdict(state, "t"), False)
