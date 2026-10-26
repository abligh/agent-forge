"""Tests for designs/0029: a step's CPU share taken when it starts
(`checks.cpu_share`), and a run's steps side by side
(`checks.side_by_side`, with each step's `after`, `alone`, `light`
and `caches`)."""

import datetime
import os
import signal
import subprocess
import tempfile
import threading
import time
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import T0, done, pc
from tests.test_forge_pr_check_merge import RepoCase

STEPS = [("a", ["a"], None), ("b", ["b"], None), ("c", ["c"], None),
         ("d", ["d"], None)]


class Tracker:
    """`run` that records when each step starts and ends, how many ran
    at once, and each step's environment, holding each step until its
    `hold` event is set (or `pause` seconds)."""

    def __init__(self, pause=0.05, rc=None):
        self.lock = threading.Lock()
        self.pause, self.rc = pause, rc or {}
        self.running, self.most, self.events = set(), 0, []
        self.envs = {}

    def __call__(self, args, cwd=None, env=None, timeout=None):
        name = args[-1]
        with self.lock:
            self.running.add(name)
            self.most = max(self.most, len(self.running))
            self.events.append(("start", name, frozenset(self.running)))
            self.envs[name] = env
        time.sleep(self.pause)
        with self.lock:
            self.running.discard(name)
            self.events.append(("end", name, frozenset(self.running)))
        return done(args, self.rc.get(name, 0))

    def started(self):
        return [name for kind, name, _ in self.events if kind == "start"]

    def beside(self, name):
        """What else ran while `name` started."""
        for kind, other, running in self.events:
            if kind == "start" and other == name:
                return running - {name}


def side(**patches):
    """Patches for a side-by-side run: AFTER, ALONE, LIGHT."""
    return [mock.patch.object(pc, key, value) for key, value in
            dict({"AFTER": {}, "ALONE": frozenset(),
                  "LIGHT": frozenset()}, **patches).items()]


class Patched(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wt = self._tmp.name
        self.addCleanup(self._tmp.cleanup)

    def patch(self, *patches):
        for p in patches:
            p.start()
            self.addCleanup(p.stop)


class StepShare(Patched):
    # Section 1: checks.cpu_share "step".

    def test_one_after_another_each_step_takes_the_share_then(self):
        shares = iter([8, 16, 4, 32])
        run = Tracker(pause=0)
        ok, results = pc.check(self.wt, {"CHECK_CPUS": "2"}, run,
                               steps=STEPS, share=lambda: next(shares))
        self.assertTrue(ok)
        self.assertEqual({n: e["CHECK_CPUS"] for n, e in run.envs.items()},
                         {"a": "8", "b": "16", "c": "4", "d": "32"})

    def test_without_a_share_the_runs_stands(self):
        run = Tracker(pause=0)
        pc.check(self.wt, {"CHECK_CPUS": "2"}, run, steps=STEPS)
        self.assertEqual({e["CHECK_CPUS"] for e in run.envs.values()},
                         {"2"})


class CpuShare(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_the_run_share_by_default(self):
        self.assertIsNone(pc.cpu_share("/nowhere", {"CHECK_CPUS": "6"},
                                       environ={}))

    def test_per_step_from_the_slots_busy_then(self):
        busy = iter([3, 1])
        with mock.patch.object(pc, "CPU_SHARE", "step"), \
                mock.patch.object(pc, "busy_slots",
                                  lambda state_dir: next(busy)), \
                mock.patch.object(pc, "online_cpus", lambda: 30), \
                mock.patch.object(pc, "FAST_LANE_S", None), \
                mock.patch.object(pc, "SLOTS", 6):
            share = pc.cpu_share("/nowhere", {"CHECK_CPUS": "5"}, environ={})
            self.assertEqual((share(), share()), (10, 30))

    def test_per_step_never_less_than_the_runs_share(self):
        # Seen on 2026-10-26: test-rust, taken after the build, found
        # the other slots filled and ran on 4.4 CPUs, not 7.5.
        with mock.patch.object(pc, "CPU_SHARE", "step"), \
                mock.patch.object(pc, "busy_slots", lambda state_dir: 6), \
                mock.patch.object(pc, "online_cpus", lambda: 30), \
                mock.patch.object(pc, "FAST_LANE_S", None), \
                mock.patch.object(pc, "SLOTS", 6):
            share = pc.cpu_share("/nowhere", {"CHECK_CPUS": "15"},
                                 environ={})
            self.assertEqual(share(), 15)

    def test_side_by_side_splits_the_runs_own(self):
        with mock.patch.object(pc, "SIDE_BY_SIDE", 4):
            share = pc.cpu_share("/nowhere", {"CHECK_CPUS": "12"},
                                 environ={})
        self.assertEqual(share(), 12)

    def test_the_hosts_own_wins(self):
        with mock.patch.object(pc, "CPU_SHARE", "step"), \
                mock.patch.object(pc, "SIDE_BY_SIDE", 4):
            self.assertIsNone(pc.cpu_share("/nowhere", {"CHECK_CPUS": "3"},
                                           environ={"CHECK_CPUS": "3"}))


class SideBySide(Patched):

    def test_at_most_max_steps_at_once_every_step_run(self):
        self.patch(*side())
        run = Tracker(rc={"b": 1})
        ok, results = pc.check(self.wt, {}, run, steps=STEPS, side_by_side=2)
        self.assertFalse(ok)
        self.assertEqual(run.most, 2)
        # In the steps' order, whatever order they finished in.
        self.assertEqual([(r["name"], r["rc"]) for r in results],
                         [("a", 0), ("b", 1), ("c", 0), ("d", 0)])

    def test_a_step_waits_for_its_after(self):
        self.patch(*side(AFTER={"a": ["c"], "b": ["a"]}))
        run = Tracker()
        pc.check(self.wt, {}, run, steps=STEPS, side_by_side=4)
        started = run.started()
        self.assertLess(started.index("c"), started.index("a"))
        self.assertLess(started.index("a"), started.index("b"))
        self.assertNotIn("c", run.beside("a"))
        self.assertNotIn("a", run.beside("b"))

    def test_an_after_that_isnt_run_is_ignored(self):
        # Skipped, reused or not applying: nothing to wait for.
        self.patch(*side(AFTER={"a": ["gone"]}))
        run = Tracker()
        ok, results = pc.check(self.wt, {}, run, steps=STEPS, side_by_side=4)
        self.assertTrue(ok)
        self.assertEqual(len(results), 4)
        self.assertEqual(run.most, 4)

    def test_alone_steps_run_last_one_at_a_time(self):
        self.patch(*side(ALONE=frozenset({"a", "c"})))
        run = Tracker()
        pc.check(self.wt, {}, run, steps=STEPS, side_by_side=4)
        self.assertEqual(run.started(), ["b", "d", "a", "c"])
        self.assertEqual(run.beside("a"), frozenset())
        self.assertEqual(run.beside("c"), frozenset())

    def test_the_share_split_between_the_steps_running(self):
        self.patch(*side(LIGHT=frozenset({"b"}), ALONE=frozenset({"d"})))
        run = Tracker()
        pc.check(self.wt, {"CHECK_CPUS": "12"}, run, steps=STEPS,
                 side_by_side=3, share=lambda: 12)
        cpus = {n: e["CHECK_CPUS"] for n, e in run.envs.items()}
        # a alone of the heavy ones; b light; c beside a; d alone, last.
        self.assertEqual(cpus, {"a": "12", "b": "1", "c": "6", "d": "12"})

    def test_at_least_one_cpu(self):
        self.patch(*side())
        run = Tracker()
        pc.check(self.wt, {}, run, steps=STEPS, side_by_side=4,
                 share=lambda: 2)
        self.assertEqual(sorted(e["CHECK_CPUS"] for e in run.envs.values()),
                         ["1", "1", "1", "2"])

    def test_a_steps_own_caches(self):
        self.patch(*side())
        run = Tracker(pause=0)
        pc.check(self.wt, {"CARGO_TARGET_DIR": "/s/cargo"}, run, steps=STEPS,
                 side_by_side=2, caches={"b": {"CARGO_TARGET_DIR": "/s/wasm"}})
        self.assertEqual(run.envs["a"]["CARGO_TARGET_DIR"], "/s/cargo")
        self.assertEqual(run.envs["b"]["CARGO_TARGET_DIR"], "/s/wasm")

    def test_a_tracer_names_its_files_in_order(self):
        # `wrap` is called as each step starts, from one thread.
        self.patch(*side())
        named = []

        def wrap(name, prefix, args, env):
            named.append(name)
            return prefix + args, env
        pc.check(self.wt, {}, Tracker(pause=0), steps=STEPS,
                 side_by_side=4, wrap=wrap)
        self.assertEqual(named, ["a", "b", "c", "d"])

    def test_a_steps_cpu_is_its_own(self):
        self.patch(*side())
        logs = os.path.join(self.wt, "logs")
        spin = ["sh", "-c", "i=0; while [ $i -lt 200000 ]; do "
                "i=$((i+1)); done"]
        steps = [("spin", spin, None), ("sleep", ["sleep", "0.3"], None)]
        _, results = pc.check(self.wt, dict(os.environ), steps=steps,
                              side_by_side=2, logs=logs,
                              stream=pc.sh_logged)
        cpu = {r["name"]: r["cpu"] for r in results}
        self.assertGreater(cpu["spin"], 0.05)
        self.assertLess(cpu["sleep"], 0.05)

    def test_stopped_ends_every_running_step(self):
        self.patch(*side())
        started = threading.Event()
        stops = []

        class Proc:
            pid = 4242

        def stream(args, cwd=None, env=None, timeout=None, log=None,
                   usage=None, on_start=None):
            if args == ["a"]:
                on_start(Proc())
                started.set()
                while not stops:
                    time.sleep(0.01)
                return done(args, -15)
            started.wait()
            raise pc.Stopped()

        def killpg(pid, sig):
            stops.append((pid, sig))
        with mock.patch.object(pc.os, "killpg", killpg):
            with self.assertRaises(pc.Stopped):
                pc.check(self.wt, {}, steps=STEPS[:2], side_by_side=2,
                         logs=os.path.join(self.wt, "logs"), stream=stream)
        self.assertEqual(stops[0], (4242, signal.SIGTERM))


class WaitUsage(unittest.TestCase):

    def test_the_exit_code_and_the_trees_cpu(self):
        proc = subprocess.Popen(
            ["sh", "-c", "i=0; while [ $i -lt 100000 ]; do i=$((i+1)); "
             "done; exit 3"])
        cpu = pc.wait_usage(proc, timeout=60)
        self.assertEqual(proc.returncode, 3)
        self.assertGreater(cpu, 0.0)
        self.assertEqual(proc.wait(), 3)

    def test_a_timeout(self):
        proc = subprocess.Popen(["sleep", "5"])
        self.addCleanup(proc.wait)
        self.addCleanup(proc.kill)
        with self.assertRaises(subprocess.TimeoutExpired):
            pc.wait_usage(proc, timeout=0.1)

    def test_sh_logged_says_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            usage, procs = {}, []
            got = pc.sh_logged(["sh", "-c", "echo hi"],
                               log=os.path.join(tmp, "l.gz"), usage=usage,
                               on_start=procs.append)
        self.assertEqual((got.returncode, got.stdout), (0, "hi\n"))
        self.assertIn("cpu", usage)
        self.assertEqual(len(procs), 1)


class StepAlertMode(unittest.TestCase):

    def state(self):
        when = pc.iso(T0)
        return {"results": {
            "1" * 40: {"finished": when, "step_seconds": {"a": 10}},
            "2" * 40: {"finished": when, "step_seconds": {"a": 4},
                       "side_by_side": True}}}

    def test_a_step_is_compared_only_with_runs_in_its_mode(self):
        since = T0 - datetime.timedelta(hours=1)
        got = pc.step_runs(self.state(), since)
        self.assertEqual([took for _, took, _, _ in got["a"]], [10])
        with mock.patch.object(pc, "SIDE_BY_SIDE", 4):
            got = pc.step_runs(self.state(), since)
        self.assertEqual([took for _, took, _, _ in got["a"]], [4])


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def configure(self, **checks):
        data = example()
        data["checks"].update(checks)
        pc.configure(config.Project(data, None))

    def test_off_by_default(self):
        self.assertEqual((pc.CPU_SHARE, pc.SIDE_BY_SIDE), ("run", None))
        self.assertEqual(pc.cache_folders(), list(pc.CACHES.values()))

    def test_the_keys(self):
        steps = [{"name": "a", "run": ["a"], "light": True},
                 {"name": "b", "run": ["b"], "after": ["a"],
                  "caches": {"CARGO_TARGET_DIR": "cargo-wasm"}},
                 {"name": "c", "run": ["c"], "alone": True}]
        self.configure(cpu_share="step", side_by_side={}, steps=steps)
        self.assertEqual(pc.CPU_SHARE, "step")
        self.assertEqual(pc.SIDE_BY_SIDE, pc.DEFAULT_MAX_STEPS)
        self.assertEqual(pc.AFTER, {"b": ["a"]})
        self.assertEqual((pc.ALONE, pc.LIGHT), ({"c"}, {"a"}))
        self.assertIn("cargo-wasm", pc.cache_folders())

    def test_scheduling_keys_leave_a_steps_digest_alone(self):
        plain = [{"name": "a", "run": ["a"]}]
        self.configure(steps=plain)
        before = pc.STEP_ENTRIES["a"]
        self.configure(steps=[dict(plain[0], after=[], alone=True,
                                   light=True, caches={"X": "x"})])
        self.assertEqual(pc.STEP_ENTRIES["a"], before)


class InTheWorker(RepoCase):

    def setUp(self):
        super().setUp()
        for p in [mock.patch.object(pc, "STEPS", [
                ("one", ["sleep", "0.4"], None),
                ("two", ["sleep", "0.4"], None)]),
                mock.patch.object(pc, "SIDE_BY_SIDE", 2)] + side():
            p.start()
            self.addCleanup(p.stop)

    def test_the_verdict_says_side_by_side_and_its_wall(self):
        head = self.pull_request({"A": "a2\n"})
        self.assertEqual(self.work(head), 0)
        record = self.state()["results"][head]
        self.assertTrue(record["side_by_side"])
        self.assertTrue(record["ok"])
        # Together, not one after the other.
        self.assertLess(record["seconds"], 1)
        self.assertEqual(sorted(record["step_cpu"]), ["one", "two"])


if __name__ == "__main__":
    unittest.main()
