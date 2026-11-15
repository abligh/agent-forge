"""Tests for forge-pr-check's checks.quick and checks.steps[].tier
(docs/designs/0055, step 1): a quick check on every pull request's
head, posted as a context of its own in slots of its own, and the full
check where the Merger asks for it."""

import datetime
import os
import pathlib
import tempfile
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import (
    REPO, SHA_A, SHA_B, SHA_P, T0, TREE_1, FakeRun, StateCase, done, empty,
    heads_tsv, is_pulls, pc, verdict)

TIERS = {"fmt-check": "head", "test": "train"}
QUICK_CONTEXT = "vm/build-and-test/quick"


def quick_on(test, tiers=None, **quick):
    """checks.quick on for one test, with the example's steps in
    `tiers` (default TIERS; the rest "both")."""
    data = example()
    tiers = TIERS if tiers is None else tiers
    for step in data["checks"]["steps"]:
        if step["name"] in tiers:
            step["tier"] = tiers[step["name"]]
    data["checks"]["quick"] = quick
    pc.configure(config.Project(data, None))
    test.addCleanup(pc.configure, config.Project(example(), None))


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_off_unless_configured(self):
        pc.configure(config.Project(example(), None))
        self.assertEqual((pc.QUICK_CONTEXT, pc.QUICK_SLOTS, pc.TIERS,
                          pc.quick_slots()), (None, 0, {}, []))
        self.assertEqual(pc.tier_steps(True), list(pc.STEPS))

    def test_the_context_and_slots_default(self):
        quick_on(self)
        self.assertEqual((pc.QUICK_CONTEXT, pc.QUICK_SLOTS),
                         (QUICK_CONTEXT, 1))
        self.assertEqual(pc.quick_slots(), [pc.QUICK_SLOT + 1])

    def test_set(self):
        quick_on(self, context="ci/quick", slots=2)
        self.assertEqual((pc.QUICK_CONTEXT, pc.QUICK_SLOTS),
                         ("ci/quick", 2))

    def test_each_tier_runs_its_own_steps_and_both(self):
        quick_on(self)
        self.assertEqual([s[0] for s in pc.tier_steps(True)],
                         ["web dependencies", "fmt-check", "vet",
                          "doc-check"])
        self.assertEqual([s[0] for s in pc.tier_steps(False)],
                         ["web dependencies", "vet", "test", "doc-check"])

    def test_the_tiers_share_one_steps_digest(self):
        # So a quick run can take a full run's verdict.
        quick_on(self)
        whole = pc.steps_digest()
        with mock.patch.object(pc, "STEPS", pc.tier_steps(True)):
            self.assertEqual(pc.steps_digest(), whole)

    def test_an_instance_has_none(self):
        data = example()
        data["checks"]["quick"] = {}
        data["checks"]["instances"] = {"mac": {
            "context": "mac/build", "steps": [{"name": "m", "run": ["m"]}]}}
        pc.configure(config.Project(data, None), "mac")
        self.assertIsNone(pc.QUICK_CONTEXT)
        self.assertEqual(pc.quick_slots(), [])


class Slots(unittest.TestCase):

    def setUp(self):
        quick_on(self, slots=2)

    def test_a_quick_slot_has_its_own_lock_worktree_and_name(self):
        first, second = pc.quick_slots()
        self.assertEqual(pc.slot_name("wt", first), "wt-quick")
        self.assertEqual(pc.slot_name("wt", second), "wt-quick-2")
        self.assertEqual(os.path.basename(pc.lock_path("/s", first)),
                         "worker-quick.lock")
        self.assertEqual(pc.slot_label(second), "quick slot 2")
        self.assertFalse(pc.is_quick_slot(pc.LANE))
        self.assertFalse(pc.is_quick_slot(2))

    def test_quick_slots_count_in_the_cpu_share(self):
        # Like the fast lane: 2 slots and 2 quick ones share 16 CPUs.
        self.assertEqual(pc.check_cpus(online=16, slots=2), 4)

    def test_busy_quick_slots_count(self):
        with tempfile.TemporaryDirectory() as state_dir:
            held = pc.try_lock(pc.lock_path(state_dir, pc.quick_slots()[0]),
                               "#7 %s %s pid=1 quick" % (SHA_A, pc.iso(T0)))
            self.addCleanup(held.close)
            self.assertEqual(pc.busy_slots(state_dir, 2), 1)

    def test_the_worker_is_told_it_is_quick(self):
        with tempfile.TemporaryDirectory() as state_dir, \
                mock.patch.object(pc.subprocess, "Popen") as popen:
            pc.spawn_worker(state_dir, SHA_A, 7, slot=pc.quick_slots()[0],
                            checkout="/repo", quick=True)
            text = pathlib.Path(state_dir, "worker.log").read_text()
        self.assertTrue(text.rstrip().endswith(
            " worker #7 %s quick slot 1 quick ===" % SHA_A))
        argv = popen.call_args[0][0]
        self.assertIn("--quick", argv)
        self.assertEqual(argv[argv.index("--slot") + 1],
                         str(pc.QUICK_SLOT + 1))


class Tested(unittest.TestCase):
    """Reuse is per tier: a quick verdict is only a quick run's."""

    def setUp(self):
        quick_on(self)

    def state(self, **results):
        return dict(empty(), results=results)

    def test_a_full_run_never_takes_a_quick_verdict(self):
        state = self.state(**{SHA_A: verdict(tier="quick")})
        self.assertIsNone(pc.tested(state, TREE_1, T0, ok=True))
        self.assertEqual(pc.tested(state, TREE_1, T0, ok=True, quick=True),
                         SHA_A)

    def test_a_quick_run_takes_a_full_verdict(self):
        for tier in ("full", None):
            record = verdict(**({"tier": tier} if tier else {}))
            state = self.state(**{SHA_A: record})
            self.assertEqual(pc.tested(state, TREE_1, T0, ok=True,
                                       quick=True), SHA_A, tier)
            self.assertEqual(pc.tested(state, TREE_1, T0, ok=True), SHA_A)

    def test_a_full_verdict_covers_the_quick_steps_it_never_runs(self):
        # A full run never runs a "head" step: its green still covers it.
        selection = {"ran": ["fmt-check", "vet"], "base": "b"}
        full = {"selection": {"ran": ["vet", "test"], "base": "b"},
                "tier": "full"}
        quick = {"selection": {"ran": ["vet"], "base": "b"},
                 "tier": "quick"}
        self.assertTrue(pc.covers(selection, tiered=True)(full))
        self.assertFalse(pc.covers(selection)(full))
        self.assertFalse(pc.covers(selection, tiered=True)(quick))


class Flakes(unittest.TestCase):

    def setUp(self):
        quick_on(self)

    def test_a_red_of_the_other_tier_is_no_flake(self):
        # They ran different steps.
        green = verdict(tier="full")
        state = dict(empty(), results={SHA_B: verdict(ok=False,
                                                      tier="quick"),
                                       SHA_P: verdict(ok=False,
                                                      tier="full")})
        self.assertEqual(pc.flakes_of(state, SHA_A, green), [SHA_P])


class Posting(unittest.TestCase):

    def setUp(self):
        quick_on(self)
        self.posts = []

    def post(self, sha, state, description, context=None):
        self.posts.append((sha, state, context))
        return True

    def test_a_quick_verdict_posts_the_quick_context(self):
        self.assertTrue(pc.post_verdict(self.post, SHA_A, verdict(
            tier="quick")))
        self.assertEqual(self.posts, [(SHA_A, "success", QUICK_CONTEXT)])

    def test_a_full_green_covers_the_quick_context(self):
        pc.post_verdict(self.post, SHA_A, verdict(tier="full"))
        self.assertEqual(self.posts, [
            (SHA_A, "success", "vm/build-and-test"),
            (SHA_A, "success", QUICK_CONTEXT)])

    def test_a_full_red_leaves_the_quick_context_alone(self):
        pc.post_verdict(self.post, SHA_A, verdict(ok=False, tier="full"))
        self.assertEqual(self.posts, [
            (SHA_A, "failure", "vm/build-and-test")])

    def test_without_quick_a_verdict_is_posted_as_ever(self):
        pc.configure(config.Project(example(), None))
        posts = []
        pc.post_verdict(lambda *a: posts.append(a) or True, SHA_A,
                        verdict())
        self.assertEqual([p[:2] for p in posts], [(SHA_A, "success")])
        self.assertEqual(len(posts[0]), 3)

    def test_a_heads_statuses_go_to_its_quick_context(self):
        with tempfile.TemporaryDirectory() as state_dir:
            pc.save(state_dir, dict(empty(), waiting={
                SHA_A: {"pr": 7}, SHA_B: {"pr": 0, "main": True}}))
            post = pc.tiered_poster(self.post, state_dir)
            post(SHA_A, "pending", "queued")
            post(SHA_B, "pending", "queued")
            post(SHA_A, "success", "x", context="vm/build-and-test")
        self.assertEqual(self.posts, [
            (SHA_A, "pending", QUICK_CONTEXT),
            (SHA_B, "pending", "vm/build-and-test"),
            (SHA_A, "success", "vm/build-and-test")])

    def test_without_quick_the_poster_is_unchanged(self):
        pc.configure(config.Project(example(), None))
        self.assertEqual(pc.tiered_poster(self.post, "/nowhere"), self.post)

    def test_the_poster_writes_the_context_it_is_given(self):
        run = FakeRun((lambda a: os.path.basename(a[0]) == "forge-app-token",
                       done([], 0, "tok\n")))
        post = pc.make_poster(run, out=lambda line: None)
        post(SHA_A, "success", "fine", context=QUICK_CONTEXT)
        post(SHA_A, "success", "fine")
        sent = [c["args"] for c in run.calls if c["args"][:2] == ["gh", "api"]]
        self.assertIn("context=" + QUICK_CONTEXT, sent[0])
        self.assertIn("context=vm/build-and-test", sent[1])


class Worker(StateCase):
    """A quick run runs its tier's steps and posts the quick context; a
    full run, its own; a full green covers a head's quick check."""

    def setUp(self):
        super().setUp()
        quick_on(self)
        self.posts = []

    def run_for(self):
        """`prepare`'s git and the steps faked, every tree TREE_1."""
        return FakeRun((lambda a: a[-1].endswith("^{tree}"),
                        done([], 0, TREE_1 + "\n")),
                       (lambda a: "FETCH_HEAD" in a,
                        done([], 0, SHA_A + "\n")))

    def queue(self):
        """SHA_A, #7's head, waiting, beside the verdicts already here."""
        state = self.state()
        state.setdefault("waiting", {})[SHA_A] = {
            "pr": 7, "since": pc.iso(T0 - datetime.timedelta(minutes=3))}
        self.write_state(state)

    def logged(self):
        return pathlib.Path(self.state_dir, "verdicts.log").read_text()

    def work(self, run):
        os.makedirs(os.path.join(self.state_dir, "wt", ".git"), exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        return pc.worker("/repo", self.state_dir, SHA_A, 7, run,
                         self.lines.append, lambda: next(clock))

    def ahead(self, run):
        os.makedirs(os.path.join(self.state_dir, "wt", ".git"), exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        return pc.worker("/repo", self.state_dir, SHA_P, 2, run,
                         self.lines.append, lambda: next(clock), ahead=True)

    def post(self, sha, state, description, context=None):
        self.posts.append((sha, state, context or "vm/build-and-test"))
        return True

    def quick(self, run, sha=SHA_A, pr=7):
        os.makedirs(os.path.join(self.state_dir, "wt-quick", ".git"),
                    exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        return pc.worker("/repo", self.state_dir, sha, pr, run,
                         self.lines.append, lambda: next(clock),
                         pc.tiered_poster(self.post, self.state_dir),
                         slot=pc.QUICK_SLOT + 1, quick=True)

    def steps_run(self, run):
        return [c["args"][-1] for c in run.calls if c["args"][0] == "make"]

    def test_a_quick_run_runs_head_and_both_steps(self):
        self.queue()
        run = self.run_for()
        self.assertEqual(self.quick(run), 0)
        self.assertEqual(self.steps_run(run), ["fmt-check", "vet",
                                               "doc-check"])
        record = self.state()["results"][SHA_A]
        self.assertEqual((record["tier"], record["ok"]), ("quick", True))
        self.assertEqual(self.posts, [(SHA_A, "pending", QUICK_CONTEXT),
                                      (SHA_A, "success", QUICK_CONTEXT)])
        self.assertIn("VERDICT #7 aaaaaaaaaa: green", self.logged())
        self.assertIn(" tier=quick\n", self.logged())
        # The process's steps are the whole list again.
        self.assertEqual(len(pc.STEPS), 5)

    def test_a_full_run_runs_train_and_both_steps(self):
        run = self.run_for()
        self.ahead(run)
        self.assertEqual(self.steps_run(run), ["vet", "test", "doc-check"])
        self.assertEqual(self.state()["results"][SHA_P]["tier"], "full")
        self.assertIn("VERDICT AHEAD #2 dddddddddd: green", self.logged())
        self.assertIn(" tier=full\n", self.logged())

    def test_a_quick_run_takes_a_full_green_and_covers_its_context(self):
        self.write_state(dict(empty(), results={
            SHA_P: verdict(pr=2, ahead=True, tier="full")}))
        self.queue()
        run = self.run_for()
        self.assertEqual(self.quick(run), 0)
        self.assertEqual(self.steps_run(run), [])
        record = self.state()["results"][SHA_A]
        self.assertEqual((record["matched"], record["tier"]), (SHA_P, "full"))
        self.assertEqual(self.posts, [
            (SHA_A, "success", "vm/build-and-test"),
            (SHA_A, "success", QUICK_CONTEXT)])
        self.assertIn("MATCHED #7 aaaaaaaaaa", self.logged())

    def test_a_quick_run_takes_a_quick_green_for_the_quick_context(self):
        self.write_state(dict(empty(), results={
            SHA_B: verdict(pr=9, tier="quick")}))
        self.queue()
        run = self.run_for()
        self.quick(run)
        self.assertEqual(self.steps_run(run), [])
        self.assertEqual(self.state()["results"][SHA_A]["tier"], "quick")
        self.assertEqual(self.posts, [(SHA_A, "success", QUICK_CONTEXT)])

    def test_without_quick_no_tier_is_recorded(self):
        pc.configure(config.Project(example(), None))
        self.queue()
        self.work(self.run_for())
        self.assertNotIn("tier", self.state()["results"][SHA_A])
        self.assertNotIn("tier=", self.logged())


class Tick(StateCase):
    """The quick slots take pull requests' heads; a full slot takes one
    only when no full run wants it."""

    def setUp(self):
        super().setUp()
        quick_on(self)

    def tick(self, waiting, slots=1):
        heads = [(w["pr"], sha, REPO, "false") for sha, w in waiting.items()
                 if not w.get("main")]
        self.write_state(dict(empty(), waiting=waiting))
        self.spawned = []
        return pc.tick(self.state_dir, "tok",
                       FakeRun((is_pulls, done([], 0, heads_tsv(*heads)))),
                       lambda *a, **k: self.spawned.append((a[1:5], k)),
                       self.lines.append, when=T0, slots=slots,
                       checkout="/repo")

    def waits(self, pr, minutes, **more):
        return dict({"pr": pr, "since": pc.iso(
            T0 - datetime.timedelta(minutes=minutes))}, **more)

    def test_heads_fill_the_quick_slot_then_a_free_one_quick(self):
        self.tick({SHA_A: self.waits(7, 30), SHA_B: self.waits(8, 10)})
        self.assertEqual(self.spawned, [
            ((SHA_A, 7, False, pc.QUICK_SLOT + 1), {"quick": True}),
            ((SHA_B, 8, False, 1), {"quick": True})])
        self.assertEqual(self.lines[:2], [
            "STARTED-QUICK #7 aaaaaaaaaa in quick slot 1; 1 waiting",
            "STARTED-QUICK #8 bbbbbbbbbb; 0 waiting"])

    def test_the_default_branch_never_takes_a_quick_slot(self):
        main = "c" * 40
        self.tick({main: self.waits(0, 60, main=True)})
        self.assertEqual(self.spawned, [
            ((main, 0, False, 1), {"main": True})])

    def test_a_busy_quick_slot_says_so(self):
        held = pc.try_lock(pc.lock_path(self.state_dir, pc.QUICK_SLOT + 1),
                           "#9 %s %s pid=1 quick" % (SHA_P, pc.iso(T0)))
        self.addCleanup(held.close)
        self.tick({SHA_A: self.waits(7, 30), SHA_P: self.waits(9, 40)})
        self.assertEqual(self.spawned, [
            ((SHA_A, 7, False, 1), {"quick": True})])
        self.assertTrue(any(line.startswith("RUNNING #9 dddddddddd") and
                            " in quick slot 1;" in line
                            for line in self.lines), self.lines)

    def test_without_quick_heads_start_as_ever(self):
        pc.configure(config.Project(example(), None))
        self.tick({SHA_A: self.waits(7, 30)})
        self.assertEqual(self.spawned, [((SHA_A, 7, False, 1), {})])


if __name__ == "__main__":
    unittest.main()
