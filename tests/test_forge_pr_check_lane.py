"""Tests for forge-pr-check's checks.fast_lane (docs/designs/0006): a
slot of its own for heads whose chosen steps have been cheap here.

`Estimate` uses real git, since only a real fetch into a checkout that
lacks the head shows that its steps can be worked out without a
worktree.
"""

import datetime
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_forge import config, trace
from tests.helpers import example
from tests.test_forge_pr_check import (
    REPO, SHA_A, SHA_B, SHA_C, T0, FakeRun, StateCase, done, git, heads_tsv,
    is_pulls, pc)
from tests.test_forge_pr_check_depends import MAIN_REF, depends_on

SHA_D = "d" * 40


def lane_on(test, max_seconds=60, steps=None):
    """checks.depends and checks.fast_lane on for one test."""
    depends_on(test, steps)
    patch = mock.patch.object(pc, "FAST_LANE_S", max_seconds)
    patch.start()
    test.addCleanup(patch.stop)


def history(costs, runs=pc.COST_MIN_RUNS):
    """state["results"] in which each step in `costs` ({step: seconds})
    has run `runs` times, taking those seconds each time."""
    return {"%040x" % i: {
        "ok": True, "finished": pc.iso(T0 - datetime.timedelta(hours=i)),
        "step_seconds": dict(costs)} for i in range(1, runs + 1)}


def waits(pr, minutes, steps=None, **more):
    """A waiting entry, `minutes` old, with lane_steps when given."""
    entry = {"pr": pr, "since": pc.iso(T0 - datetime.timedelta(
        minutes=minutes))}
    if steps is not None:
        entry["lane_steps"] = steps
    entry.update(more)
    return entry


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def lane(self, fast_lane):
        data = example()
        data["checks"].update(depends=True)
        if fast_lane is not None:
            data["checks"]["fast_lane"] = fast_lane
        pc.configure(config.Project(data, None))
        return pc.FAST_LANE_S

    def test_off_unless_configured(self):
        self.assertIsNone(self.lane(None))

    def test_max_seconds(self):
        self.assertEqual(self.lane({"max_seconds": 20}), 20)

    def test_sixty_seconds_by_default(self):
        self.assertEqual(self.lane({}), 60)


class Names(unittest.TestCase):

    def test_the_lane_has_its_own_lock_worktree_and_caches(self):
        self.assertEqual(pc.slot_name("wt", pc.LANE), "wt-lane")
        self.assertEqual(os.path.basename(pc.lock_path("/s", pc.LANE)),
                         "worker-lane.lock")
        with mock.patch.object(pc, "CACHES", {"CARGO_TARGET_DIR": "cargo"}):
            self.assertEqual(
                pc.step_env("/s", {}, slot=pc.LANE)["CARGO_TARGET_DIR"],
                "/s/cargo-lane")
        self.assertEqual(pc.slot_name("wt", 2), "wt-2")

    def test_the_worker_log_names_the_lane(self):
        with tempfile.TemporaryDirectory() as state_dir, \
                mock.patch.object(pc.subprocess, "Popen") as popen:
            pc.spawn_worker(state_dir, SHA_A, 7, slot=pc.LANE,
                            checkout="/repo")
            pc.spawn_worker(state_dir, SHA_B, 8, slot=2)
            text = pathlib.Path(state_dir, "worker.log").read_text()
        first, second = text.splitlines()
        self.assertTrue(first.endswith(" worker #7 %s fast lane ===" % SHA_A))
        self.assertTrue(second.endswith(" worker #8 %s slot 2 ===" % SHA_B))
        argv = popen.call_args_list[0][0][0]
        self.assertEqual(argv[argv.index("--slot") + 1], str(pc.LANE))


class StepCosts(unittest.TestCase):

    def test_the_median_of_the_last_twenty_runs(self):
        results = {}
        # 25 runs, oldest first: five of 1000s, then 1s to 20s.
        for i, took in enumerate([1000] * 5 + list(range(1, 21))):
            results["%040x" % i] = {
                "finished": pc.iso(T0 + datetime.timedelta(minutes=i)),
                "step_seconds": {"docs": took}}
        self.assertEqual(pc.step_costs({"results": results}), {"docs": 10.5})

    def test_too_few_runs_have_no_cost(self):
        state = {"results": history({"docs": 2}, runs=pc.COST_MIN_RUNS - 1)}
        self.assertEqual(pc.step_costs(state), {})
        state = {"results": history({"docs": 2})}
        self.assertEqual(pc.step_costs(state), {"docs": 2})

    def test_verdicts_without_step_times_are_skipped(self):
        results = history({"docs": 3})
        results[SHA_A] = {"finished": pc.iso(T0), "seconds": 0,
                          "matched": SHA_B}
        results[SHA_B] = {"finished": pc.iso(T0),
                          "step_seconds": {"docs": "slow", "fmt": True}}
        self.assertEqual(pc.step_costs({"results": results}), {"docs": 3})

    def test_each_step_has_its_own_runs(self):
        results = history({"docs": 2})
        results.update({"%040x" % (100 + i): {
            "finished": pc.iso(T0), "step_seconds": {"build": 300}}
            for i in range(pc.COST_MIN_RUNS)})
        self.assertEqual(pc.step_costs({"results": results}),
                         {"docs": 2, "build": 300})


class CheapHeads(unittest.TestCase):

    def setUp(self):
        lane_on(self, 60)
        self.costs = {"docs": 3, "fmt": 1, "build": 300}

    def test_a_head_whose_steps_add_up_to_at_most_max_seconds(self):
        state = {"waiting": {SHA_A: waits(7, 5, ["docs", "fmt"]),
                             SHA_B: waits(8, 5, ["docs", "build"]),
                             SHA_C: waits(9, 5, [])}}
        self.assertEqual(pc.cheap_heads(state, self.costs),
                         {SHA_A: 4, SHA_C: 0})

    def test_exactly_max_seconds_is_cheap(self):
        state = {"waiting": {SHA_A: waits(7, 5, ["docs"])}}
        self.assertEqual(pc.cheap_heads(state, {"docs": 60}), {SHA_A: 60})
        self.assertEqual(pc.cheap_heads(state, {"docs": 61}), {})

    def test_a_step_without_a_cost_is_expensive(self):
        state = {"waiting": {SHA_A: waits(7, 5, ["docs", "lint"])}}
        self.assertEqual(pc.cheap_heads(state, self.costs), {})

    def test_a_head_whose_steps_were_not_worked_out_is_not_cheap(self):
        state = {"waiting": {SHA_A: waits(7, 5)}}
        self.assertEqual(pc.cheap_heads(state, self.costs), {})

    def test_a_default_branch_head_never_is(self):
        state = {"waiting": {SHA_A: waits(0, 5, ["docs"], main=True)}}
        self.assertEqual(pc.cheap_heads(state, self.costs), {})

    def test_costs_come_from_the_verdicts(self):
        state = {"waiting": {SHA_A: waits(7, 5, ["docs"])},
                 "results": history({"docs": 3})}
        self.assertEqual(pc.cheap_heads(state), {SHA_A: 3})

    def test_off_nothing_is(self):
        state = {"waiting": {SHA_A: waits(7, 5, [])}}
        with mock.patch.object(pc, "FAST_LANE_S", None):
            self.assertEqual(pc.cheap_heads(state, self.costs), {})


class Picking(unittest.TestCase):

    def setUp(self):
        lane_on(self)
        self.state = {"waiting": {
            SHA_A: waits(7, 30, ["build"]),
            SHA_B: waits(8, 20, ["docs"]),
            SHA_C: waits(9, 40, ["docs"])}}
        self.cheap = {SHA_B: 3, SHA_C: 3}

    def test_the_lane_takes_the_oldest_cheap_head(self):
        self.assertEqual(pc.lane_head(self.state, self.cheap, when=T0),
                         (SHA_C, 9))
        self.assertEqual(pc.lane_head(self.state, self.cheap, {SHA_C}, T0),
                         (SHA_B, 8))

    def test_the_lane_takes_no_expensive_head(self):
        self.assertIsNone(pc.lane_head(self.state, self.cheap,
                                       {SHA_B, SHA_C}, T0))

    def test_an_ordinary_slot_takes_an_expensive_head_first(self):
        self.assertEqual(pc.next_head(self.state, when=T0, cheap=self.cheap),
                         (SHA_A, 7))

    def test_and_a_cheap_one_when_no_expensive_one_waits(self):
        self.assertEqual(pc.next_head(self.state, {SHA_A}, when=T0,
                                      cheap=self.cheap), (SHA_C, 9))

    def test_a_given_up_expensive_head_does_not_hold_cheap_ones_back(self):
        self.state["waiting"][SHA_A]["errors"] = pc.MAX_ERRORS
        self.assertEqual(pc.next_head(self.state, when=T0, cheap=self.cheap),
                         (SHA_C, 9))

    def test_the_default_branch_counts_as_expensive(self):
        self.state["waiting"][SHA_A] = waits(0, 60, main=True)
        self.assertEqual(pc.next_head(self.state, when=T0, cheap=self.cheap),
                         (SHA_A, 0))

    def test_without_the_lane_nothing_changes(self):
        self.assertEqual(pc.next_head(self.state, when=T0), (SHA_C, 9))


class Tick(StateCase):

    def setUp(self):
        super().setUp()
        lane_on(self)

    def tick(self, waiting, heads=None, slots=1, checkout="/repo"):
        heads = heads if heads is not None else [
            (w["pr"], sha, REPO, "false") for sha, w in waiting.items()]
        self.write_state({"waiting": waiting,
                          "results": history({"docs": 3, "build": 300})})
        self.run = FakeRun((is_pulls, done([], 0, heads_tsv(*heads))))
        self.spawned = []
        return pc.tick(self.state_dir, "tok", self.run,
                       lambda *a, **k: self.spawned.append(a),
                       self.lines.append, when=T0, slots=slots,
                       checkout=checkout)

    def hold(self, slot, holder=None):
        held = pc.try_lock(pc.lock_path(self.state_dir, slot), holder)
        self.addCleanup(held.close)
        return held

    def test_the_lane_starts_a_cheap_head_beside_an_expensive_one(self):
        self.tick({SHA_A: waits(7, 30, ["build"]),
                   SHA_B: waits(8, 10, ["docs"])})
        self.assertEqual(self.spawned, [
            (self.state_dir, SHA_B, 8, False, pc.LANE, "/repo"),
            (self.state_dir, SHA_A, 7, False, 1, "/repo")])
        self.assertEqual(self.lines, [
            "STARTED #8 bbbbbbbbbb in the fast lane, its steps ~3s here; "
            "1 waiting",
            "STARTED #7 aaaaaaaaaa; 0 waiting"])
        waiting = self.state()["waiting"]
        self.assertEqual(waiting[SHA_B]["started"], pc.iso(T0))

    def test_the_lane_takes_the_oldest_cheap_head(self):
        self.tick({SHA_B: waits(8, 10, ["docs"]),
                   SHA_C: waits(9, 20, ["docs"])}, slots=1)
        self.assertEqual([a[1:5] for a in self.spawned],
                         [(SHA_C, 9, False, pc.LANE), (SHA_B, 8, False, 1)])

    def test_a_busy_lane_leaves_cheap_heads_waiting_behind_expensive(self):
        self.hold(pc.LANE, "#9 %s %s pid=1" % (SHA_C, pc.iso(T0)))
        self.tick({SHA_A: waits(7, 5, ["build"]),
                   SHA_B: waits(8, 30, ["docs"]),
                   SHA_C: waits(9, 40, ["docs"])})
        self.assertEqual([a[1:5] for a in self.spawned],
                         [(SHA_A, 7, False, 1)])
        self.assertIn("RUNNING #9 cccccccccc since %s in the fast lane; "
                      "2 waiting" % pc.iso(T0), self.lines)

    def test_a_busy_lane_with_no_expensive_head_lets_a_slot_take_it(self):
        self.hold(pc.LANE, "#9 %s %s pid=1" % (SHA_C, pc.iso(T0)))
        self.tick({SHA_B: waits(8, 30, ["docs"]),
                   SHA_C: waits(9, 40, ["docs"])})
        self.assertEqual([a[1:5] for a in self.spawned],
                         [(SHA_B, 8, False, 1)])

    def test_a_head_not_worked_out_waits_in_the_ordinary_queue(self):
        self.tick({SHA_A: waits(7, 30)})
        self.assertEqual([a[1:5] for a in self.spawned],
                         [(SHA_A, 7, False, 1)])

    def test_ordinary_slots_all_busy_the_lane_still_starts(self):
        self.hold(1, "#7 %s %s pid=1" % (SHA_A, pc.iso(T0)))
        self.tick({SHA_A: waits(7, 30, ["build"]),
                   SHA_B: waits(8, 10, ["docs"])})
        self.assertEqual([a[1:5] for a in self.spawned],
                         [(SHA_B, 8, False, pc.LANE)])

    def test_nothing_cheap_the_lane_stays_free(self):
        self.tick({SHA_A: waits(7, 30, ["build"])}, slots=1)
        self.assertEqual([a[4] for a in self.spawned], [1])
        self.assertIsNone(pc.running(self.state_dir, pc.LANE))

    def test_without_a_checkout_there_is_no_lane(self):
        self.tick({SHA_B: waits(8, 10, ["docs"])}, checkout=None)
        self.assertEqual([a[1:5] for a in self.spawned],
                         [(SHA_B, 8, False, 1)])

    def test_off_there_is_no_lane(self):
        with mock.patch.object(pc, "FAST_LANE_S", None):
            self.tick({SHA_A: waits(7, 30, ["build"]),
                       SHA_B: waits(8, 10, ["docs"])})
        self.assertEqual([a[1:5] for a in self.spawned],
                         [(SHA_A, 7, False, 1)])

    def test_a_superseded_lane_run_is_stopped(self):
        self.hold(pc.LANE, "#9 %s %s pid=4242" % (SHA_C, pc.iso(T0)))
        stopped = []
        self.write_state({"waiting": {}})
        pc.tick(self.state_dir, "tok",
                FakeRun((is_pulls, done([], 0, ""))),
                lambda *a, **k: None, self.lines.append, when=T0, slots=1,
                checkout="/repo", stop=lambda pid: stopped.append(pid) or True)
        self.assertEqual(stopped, [4242])
        self.assertIn("in the fast lane (pid 4242)", self.lines[0])

    def test_a_head_without_an_estimate_is_worked_out(self):
        calls = []

        def steps(checkout, sha, pr, state_dir, run):
            calls.append((checkout, sha, pr))
            return {"ran": {"docs": "x"}, "base": SHA_D}
        with mock.patch.object(pc, "lane_steps", steps):
            self.tick({SHA_A: waits(7, 30, ["build"]),
                       SHA_B: waits(8, 10)})
        self.assertEqual(calls, [("/repo", SHA_B, 8)])
        self.assertEqual(self.state()["waiting"][SHA_B]["lane_steps"],
                         ["docs"])
        self.assertEqual(self.spawned[0][1:5], (SHA_B, 8, False, pc.LANE))


class Estimate(StateCase):
    """lane_steps and estimate with real git: the head is only in the
    origin, as a pull request's ref, until the checkout fetches it."""

    def setUp(self):
        super().setUp()
        self.steps = [("docs", ["cat", "README"], None),
                      ("code", ["cat", "src/a"], None),
                      ("web", ["npm", "test"], "web/package.json")]
        lane_on(self, steps=self.steps)
        root = self._tmp.name
        self.origin = os.path.join(root, "origin.git")
        self.seed = os.path.join(root, "seed")
        self.checkout = os.path.join(root, "checkout")
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=root)
        git("init", "-q", "-b", "main", self.seed, cwd=root)
        self.main = self.commit({"README": "r", "src/a": "a"})
        git("push", "-q", self.origin, "main", cwd=self.seed)
        # Not a local clone, which would share every object.
        git("clone", "-q", "--no-local", self.origin, self.checkout,
            cwd=root)
        trace.write_record(
            self.state_dir, git("rev-parse", "HEAD^{tree}", cwd=self.seed),
            self.main, pc.steps_digest(), pc.iso(T0), "preload",
            {"docs": ["README"], "code": ["src/a"], "web": ["web"]},
            {"docs": ["."], "code": ["src"], "web": ["web"]})

    def commit(self, files):
        for name, text in files.items():
            path = pathlib.Path(self.seed, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            git("add", name, cwd=self.seed)
        git("commit", "-q", "-m", "c", cwd=self.seed)
        return git("rev-parse", "HEAD", cwd=self.seed)

    def pull_request(self, files, pr=7):
        git("checkout", "-q", "-B", "feature", self.main, cwd=self.seed)
        head = self.commit(files)
        git("push", "-q", "-f", self.origin,
            "feature:refs/pull/%d/head" % pr, cwd=self.seed)
        return head

    def has(self, sha):
        return subprocess.run(
            ["git", "-C", self.checkout, "cat-file", "-e", sha + "^{commit}"],
            capture_output=True).returncode == 0

    def test_the_head_is_fetched_and_its_steps_worked_out(self):
        head = self.pull_request({"README": "changed"})
        self.assertFalse(self.has(head))
        got = pc.lane_steps(self.checkout, head, 7, self.state_dir)
        self.assertTrue(self.has(head))
        self.assertEqual((got["base"], got["ran"], got["skipped"]),
                         (self.main, {"docs": "README"}, ["code"]))
        # Other users of the checkout may read FETCH_HEAD.
        self.assertFalse(os.path.exists(
            os.path.join(self.checkout, ".git", "FETCH_HEAD")))

    def test_a_step_applies_as_its_only_if_says_in_the_commit(self):
        head = self.pull_request({"web/package.json": "{}"})
        got = pc.lane_steps(self.checkout, head, 7, self.state_dir)
        self.assertEqual(got["ran"], {"web": "web/package.json (in web)"})
        self.assertEqual(
            [name for name, _, _ in pc.applicable_at(self.checkout, self.main)],
            ["docs", "code"])

    def test_a_head_the_checkout_has_is_not_fetched(self):
        head = self.pull_request({"README": "changed"})
        pc.lane_steps(self.checkout, head, 7, self.state_dir)
        run = FakeRun()
        real = pc.sh

        def spy(args, **kw):
            run(args)
            return real(args, **kw)
        pc.lane_steps(self.checkout, head, 7, self.state_dir, spy)
        self.assertFalse([c for c in run.calls if "fetch" in c["args"]])

    def test_a_fetch_that_fails_works_nothing_out(self):
        head = self.pull_request({"README": "changed"})
        got = pc.lane_steps(self.checkout, head, 99, self.state_dir)
        self.assertTrue(got["every"].startswith(
            "could not fetch pull/99/head"))

    def test_a_head_that_moved_works_nothing_out(self):
        self.pull_request({"README": "changed"})
        self.assertEqual(
            pc.lane_steps(self.checkout, SHA_D, 7, self.state_dir),
            {"every": "pull/7/head is no longer dddddddddd"})

    def test_estimate_keeps_only_answers(self):
        head = self.pull_request({"src/a": "b"})
        self.write_state({"waiting": {
            head: waits(7, 5), SHA_D: waits(8, 5),
            SHA_A: waits(0, 5, main=True), SHA_B: waits(9, 5, ["x"])}})
        pc.estimate(self.state_dir, self.checkout)
        waiting = self.state()["waiting"]
        self.assertEqual(waiting[head]["lane_steps"], ["code"])
        # Its fetch fails: tried again next tick.
        self.assertNotIn("lane_steps", waiting[SHA_D])
        self.assertNotIn("lane_steps", waiting[SHA_A])
        self.assertEqual(waiting[SHA_B]["lane_steps"], ["x"])

    def test_without_a_record_nothing_is_kept(self):
        for path, _ in trace.load_records(self.state_dir):
            os.unlink(path)
        head = self.pull_request({"README": "changed"})
        self.write_state({"waiting": {head: waits(7, 5)}})
        pc.estimate(self.state_dir, self.checkout)
        self.assertNotIn("lane_steps", self.state()["waiting"][head])

    def test_busy_and_given_up_heads_are_not_worked_out(self):
        self.write_state({"waiting": {
            SHA_A: waits(7, 5), SHA_B: waits(8, 5, errors=pc.MAX_ERRORS)}})
        run = FakeRun()
        pc.estimate(self.state_dir, self.checkout, busy={SHA_A}, run=run)
        self.assertEqual(run.calls, [])


class Worker(StateCase):
    """The worker records each step's time, and logs a slow lane run."""

    def setUp(self):
        super().setUp()
        lane_on(self, 10)
        self.write_state({"waiting": {SHA_A: waits(7, 3)}})

    def work(self, seconds, slot=pc.LANE):
        os.makedirs(os.path.join(self.state_dir, pc.slot_name("wt", slot),
                                 ".git"), exist_ok=True)
        results = [{"name": name, "rc": 0, "seconds": took, "tail": ""}
                   for name, took in seconds.items()]
        run = FakeRun((lambda a: "FETCH_HEAD" in a, done([], 0, SHA_A + "\n")))
        clock = iter([T0, T0 + datetime.timedelta(minutes=1)])
        with mock.patch.object(pc, "check", lambda *a, **k: (True, results)):
            return pc.worker("/repo", self.state_dir, SHA_A, 7, run,
                             self.lines.append, lambda: next(clock),
                             slot=slot)

    def logged(self):
        return pathlib.Path(self.state_dir, "verdicts.log").read_text()

    def test_each_steps_seconds_are_recorded(self):
        self.assertEqual(self.work({"docs": 4, "fmt": 1}), 0)
        record = self.state()["results"][SHA_A]
        self.assertEqual(record["step_seconds"], {"docs": 4, "fmt": 1})
        self.assertEqual(record["seconds"], 5)

    def test_a_lane_run_over_three_times_max_seconds_is_slow(self):
        self.work({"docs": 25, "fmt": 6})
        self.assertIn("SLOW-LANE #7 aaaaaaaaaa: ran 31s in the fast lane, "
                      "over 3 times its 10s; docs 25s, fmt 6s", self.logged())
        # Not stopped: the verdict stands.
        self.assertTrue(self.state()["results"][SHA_A]["ok"])

    def test_three_times_is_not(self):
        self.work({"docs": 30})
        self.assertNotIn("SLOW-LANE", self.logged())

    def test_nor_is_a_long_run_in_an_ordinary_slot(self):
        self.work({"docs": 300}, slot=1)
        self.assertNotIn("SLOW-LANE", self.logged())


if __name__ == "__main__":
    unittest.main()
