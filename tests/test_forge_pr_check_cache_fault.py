"""Tests for `checks.cache_fault_text` in forge-pr-check (designs/0022,
amendment of 2026-10-26): a red that a stale build cache caused records
no verdict; the slot's incremental state is cleared, and the head is
run again once, at once."""

import os
import pathlib
import tempfile
import unittest

from agent_forge import config
from tests.helpers import example
from tests import test_forge_pr_check as base
from tests.test_forge_pr_check import SHA_A, FakeRun, done, pc

LINK = ["rust-lld: error:", "undefined symbol"]
STALE = ("rust-lld: error: undefined symbol: core::fmt::write\n"
         "error: could not compile `widget` (lib test)\n")


def configured(texts=(LINK,), instance=None):
    data = example()
    if texts is not None:
        data["checks"]["cache_fault_text"] = [list(t) for t in texts]
    if instance:
        data["checks"]["instances"] = {instance: {
            "context": "other/check", "steps": [{"name": "t",
                                                 "run": ["true"]}]}}
    pc.configure(config.Project(data, None), instance)


class CacheFault(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def step(self, tail, rc=1):
        return [{"name": "build", "rc": rc, "tail": tail}]

    def test_off_unless_configured(self):
        self.assertEqual(pc.CACHE_FAULT_TEXT, ())
        self.assertIsNone(pc.cache_fault(self.step(STALE)))

    def test_every_string_of_an_entry_must_be_there(self):
        configured()
        self.assertEqual(pc.cache_fault(self.step(STALE)),
                         ("build", tuple(LINK)))
        self.assertIsNone(pc.cache_fault(self.step(
            "rust-lld: error: duplicate symbol: x\n")))

    def test_only_a_failed_step_counts(self):
        configured()
        self.assertIsNone(pc.cache_fault(self.step(STALE, rc=0)))

    def test_an_instance_inherits_it(self):
        configured(instance="mac")
        self.assertEqual(pc.CACHE_FAULT_TEXT, (tuple(LINK),))


class ClearIncremental(base.StateCase):

    def setUp(self):
        super().setUp()
        configured()
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.target = os.path.join(self.state_dir, "cargo-target")

    def make(self, *parts):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pathlib.Path(path).write_text("x")
        return path

    def test_only_directories_in_incremental_go(self):
        unit = self.make(self.target, "release", "incremental", "widget-1",
                         "s-1", "query.bin")
        dep = self.make(self.target, "release", "deps", "libwidget.rlib")
        loose = self.make(self.target, "release", "incremental", "note")
        other = self.make(self.state_dir, "cargo-target-2", "release",
                          "incremental", "widget-1", "q")
        self.assertEqual(pc.clear_incremental(self.state_dir, 1), 1)
        self.assertFalse(os.path.exists(os.path.dirname(os.path.dirname(
            unit))))
        # The build's other output, a file, and another slot's cache stay.
        for path in (dep, loose, other):
            self.assertTrue(os.path.exists(path), path)

    def test_a_link_out_of_the_slots_caches_is_left_alone(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, outside)
        kept = self.make(outside, "widget-1", "q")
        inc = os.path.join(self.target, "release", "incremental")
        os.makedirs(inc)
        os.symlink(os.path.join(outside, "widget-1"),
                   os.path.join(inc, "widget-1"))
        self.assertEqual(pc.clear_incremental(self.state_dir, 1), 0)
        self.assertTrue(os.path.exists(kept))

    def test_a_cache_that_is_a_link_is_left_alone(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, outside)
        kept = self.make(outside, "release", "incremental", "widget-1", "q")
        os.symlink(outside, self.target)
        self.assertEqual(pc.clear_incremental(self.state_dir, 1), 0)
        self.assertTrue(os.path.exists(kept))


class CacheFaultWorker(base.StateCase):

    work, queue = base.Worker.work, base.Worker.queue

    def setUp(self):
        super().setUp()
        configured()
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.unit = os.path.join(self.state_dir, "cargo-target", "release",
                                 "incremental", "widget-1")
        os.makedirs(self.unit)
        pathlib.Path(self.unit, "q").write_text("x")

    def stale(self, times):
        """A run whose `test` step fails with STALE its first `times`
        runs, then passes."""
        runs = []

        def step(args):
            runs.append(1)
            if len(runs) <= times:
                return done(args, 1, "", STALE)
            return done(args, 0)
        self.runs = runs
        return FakeRun((lambda a: "FETCH_HEAD" in a,
                        done([], 0, SHA_A + "\n")),
                       (lambda a: a[0] == "make" and a[-1] == "test", step))

    def logged(self):
        return pathlib.Path(self.state_dir, "verdicts.log").read_text()

    def test_a_stale_cache_is_cleared_and_the_head_run_again(self):
        self.queue()
        posts = []
        rc = self.work(self.stale(1), post=lambda *a: posts.append(a) or True)
        self.assertEqual(rc, 0)
        self.assertEqual(len(self.runs), 2)
        self.assertFalse(os.path.exists(self.unit))
        state = self.state()
        self.assertTrue(state["results"][SHA_A]["ok"])
        self.assertNotIn(SHA_A, state["waiting"])
        self.assertNotIn(SHA_A, state.get("reruns", {}))
        self.assertEqual([p[1] for p in posts], ["pending", "success"])
        self.assertIn('CACHE-FAULT #7 aaaaaaaaaa in slot 1: test: its tail '
                      'has ["rust-lld: error:", "undefined symbol"]; no '
                      'verdict; cleared 1 incremental directories and 0 '
                      'fingerprints; kept widget\'s sessions in ', self.logged())
        self.assertIn(', and run again now', self.logged())

    def test_a_second_match_stands_red(self):
        self.queue()
        rc = self.work(self.stale(2))
        self.assertEqual(rc, 1)
        self.assertEqual(len(self.runs), 2)
        record = self.state()["results"][SHA_A]
        self.assertEqual((record["ok"], record["failed"]), (False, ["test"]))
        self.assertIn("again, after its cache was cleared; the red stands",
                      self.logged())

    def test_it_is_not_a_host_fault(self):
        self.queue()
        self.work(self.stale(1))
        self.assertNotIn("HOST-FAULT", self.logged())
        self.assertNotIn("faults", self.state()["waiting"].get(SHA_A, {}))

    def test_another_red_runs_once(self):
        configured(texts=[["something else"]])
        self.queue()
        self.assertEqual(self.work(self.stale(1)), 1)
        self.assertEqual(len(self.runs), 1)
        self.assertTrue(os.path.exists(self.unit))
        self.assertNotIn("CACHE-FAULT", self.logged())


if __name__ == "__main__":
    unittest.main()


class ClearFingerprints(base.StateCase):

    def setUp(self):
        super().setUp()
        configured()
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.target = os.path.join(self.state_dir, "cargo-target")

    def make(self, *parts):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pathlib.Path(path).write_text("x")
        return path

    def test_the_cleared_crates_fingerprints_go(self):
        self.make(self.target, "debug", "incremental", "my_widget-1a2b",
                  "s-1", "q")
        gone = [self.make(self.target, p, ".fingerprint", d, "lib")
                for p, d in (("debug", "my-widget-0f0f"),
                             ("release", "my-widget-1e1e"),
                             (os.path.join("wasm32-wasip1", "debug"),
                              "my-widget-2d2d"))]
        kept = [self.make(self.target, "debug", ".fingerprint", d, "lib")
                for d in ("serde-0f0f", "my-widget-extra-3c3c")]
        other = self.make(self.state_dir, "cargo-target-2", "debug",
                          ".fingerprint", "my-widget-0f0f", "lib")
        crates = set()
        self.assertEqual(pc.clear_incremental(self.state_dir, 1, crates), 1)
        self.assertEqual(crates, {"my_widget"})
        self.assertEqual(pc.clear_fingerprints(self.state_dir, 1, crates), 3)
        for path in gone:
            self.assertFalse(os.path.exists(path), path)
        for path in kept + [other]:
            self.assertTrue(os.path.exists(path), path)

    def test_nothing_without_crates(self):
        kept = self.make(self.target, "debug", ".fingerprint", "w-1", "lib")
        self.assertEqual(pc.clear_fingerprints(self.state_dir, 1, set()), 0)
        self.assertTrue(os.path.exists(kept))

    def test_a_link_is_left_alone(self):
        outside = tempfile.mkdtemp()
        self.addCleanup(__import__("shutil").rmtree, outside)
        kept = self.make(outside, "w-1", "lib")
        fp = os.path.join(self.target, "debug", ".fingerprint")
        os.makedirs(fp)
        os.symlink(os.path.join(outside, "w-1"), os.path.join(fp, "w-1"))
        self.assertEqual(pc.clear_fingerprints(self.state_dir, 1, {"w"}), 0)
        self.assertTrue(os.path.exists(kept))


class Quarantine(base.StateCase):

    def setUp(self):
        super().setUp()
        configured()
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.target = os.path.join(self.state_dir, "cargo-target")

    def make(self, *parts):
        path = os.path.join(*parts)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        pathlib.Path(path).write_text("x")
        return path

    def when(self, second=0):
        import datetime
        return datetime.datetime(2026, 1, 2, 3, 4, second,
                                 tzinfo=datetime.timezone.utc)

    def test_the_crates_named_in_the_tail(self):
        self.assertEqual(pc.faulting_crates(
            "error: could not compile `my-widget` (lib test) due to 1\n"
            "error: could not compile `other`\n"), {"my_widget", "other"})
        self.assertEqual(pc.faulting_crates("rust-lld: error: x\n"), set())

    def test_the_failing_crates_sessions_move(self):
        session = self.make(self.target, "debug", "incremental", "w-1",
                            "s-1", "q")
        stays = self.make(self.target, "debug", "incremental", "v-1", "s-1",
                          "q")
        dest, moved = pc.quarantine_sessions(self.state_dir, 1, {"w"},
                                             "abc-1", self.when())
        self.assertEqual(moved, 1)
        self.assertEqual(dest, os.path.join(
            self.state_dir, "cache-fault", "20260102T030400Z-abc-1"))
        self.assertFalse(os.path.exists(session))
        self.assertTrue(os.path.exists(os.path.join(
            dest, "cargo-target", "debug", "incremental", "w-1", "s-1", "q")))
        self.assertTrue(os.path.exists(stays))

    def test_nothing_named_moves_nothing(self):
        kept = self.make(self.target, "debug", "incremental", "w-1", "q")
        self.assertEqual(pc.quarantine_sessions(self.state_dir, 1, set(),
                                                "a-1", self.when()), (None, 0))
        self.assertEqual(pc.quarantine_sessions(self.state_dir, 1, {"x"},
                                                "a-1", self.when()), (None, 0))
        self.assertTrue(os.path.exists(kept))
        self.assertFalse(os.path.exists(os.path.join(
            self.state_dir, "cache-fault", "20260102T030400Z-a-1")))

    def test_only_the_newest_three_are_kept(self):
        for second in range(5):
            self.make(self.target, "debug", "incremental", "w-1", "q")
            pc.quarantine_sessions(self.state_dir, 1, {"w"}, "a-1",
                                   self.when(second))
        self.assertEqual(sorted(os.listdir(os.path.join(
            self.state_dir, "cache-fault"))),
            ["20260102T030402Z-a-1", "20260102T030403Z-a-1",
             "20260102T030404Z-a-1"])
