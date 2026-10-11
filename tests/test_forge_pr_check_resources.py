"""Tests for what forge-pr-check gives each step and keeps after it:
CHECK_CPUS, a slot's share of the host; CHECK_RESULTS, a directory a
step may leave results in; and `checks.cache_limit`, the pruning of a
slot's build caches."""

import datetime
import os
import pathlib
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import T0, StateCase, pc
from tests.test_forge_pr_check_merge import RepoCase


class CheckCpus(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_the_cpus_shared_between_the_slots(self):
        self.assertEqual(pc.check_cpus(online=20, slots=2), 10)
        self.assertEqual(pc.check_cpus(online=20, slots=3), 6)

    def test_the_fast_lane_counts_as_a_slot(self):
        with mock.patch.object(pc, "FAST_LANE_S", 60):
            self.assertEqual(pc.check_cpus(online=20, slots=2), 6)

    def test_at_least_one(self):
        self.assertEqual(pc.check_cpus(online=1, slots=4), 1)

    def test_slots_from_the_environment(self):
        with mock.patch.dict("os.environ", {"FORGE_PR_CHECK_SLOTS": "4"}), \
                mock.patch.object(pc, "online_cpus", lambda: 20):
            self.assertEqual(pc.check_cpus(), 5)

    def test_every_step_gets_it_unless_the_host_set_it(self):
        with mock.patch.object(pc, "online_cpus", lambda: 8):
            self.assertEqual(pc.step_env("/s", {})["CHECK_CPUS"], "4")
            self.assertEqual(
                pc.step_env("/s", {"CHECK_CPUS": "3"})["CHECK_CPUS"], "3")

    def test_online_cpus_is_at_least_one(self):
        self.assertGreaterEqual(pc.online_cpus(), 1)


def writes(files):
    """A `run` whose steps leave `files` ({step argv[0]: {name: text}})
    in their CHECK_RESULTS, recording each step's environment."""
    seen = []

    def run(args, cwd=None, env=None, timeout=None):
        seen.append(env)
        for name, text in files.get(args[-1], {}).items():
            pathlib.Path(env["CHECK_RESULTS"], name).write_text(text)
        return subprocess.CompletedProcess(args, 0, "", "")
    run.seen = seen
    return run


class CheckResults(unittest.TestCase):

    STEPS = [("shards", ["one"], None), ("web dependencies", ["two"], None)]

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.kept = os.path.join(self._tmp.name, "results", "abc")

    def check(self, run):
        return pc.check(self._tmp.name, {"X": "1"}, run, steps=self.STEPS,
                        kept=self.kept)

    def test_each_step_gets_an_empty_directory_of_its_own(self):
        stale = pathlib.Path(self.kept, "shards")
        stale.mkdir(parents=True)
        (stale / "old.json").write_text("from an earlier run")
        run = writes({"one": {"shards.json": "[]"}})
        ok, results = self.check(run)
        self.assertTrue(ok)
        self.assertEqual([e["CHECK_RESULTS"] for e in run.seen], [
            os.path.join(self.kept, "shards"),
            os.path.join(self.kept, "web_dependencies")])
        self.assertEqual(os.listdir(stale), ["shards.json"])
        self.assertEqual(results[0]["results_bytes"], 2)

    def test_a_step_that_leaves_nothing_has_no_directory(self):
        _, results = self.check(writes({}))
        self.assertEqual(os.listdir(self.kept), [])
        self.assertEqual(results[1]["results_bytes"], 0)

    def test_too_much_is_dropped_whole(self):
        with mock.patch.object(pc, "RESULTS_MAX", 10):
            _, results = self.check(writes({"one": {"a": "12345",
                                                    "b": "123456"}}))
        self.assertEqual((results[0]["results_bytes"],
                          results[0]["results_dropped"]), (0, 11))
        self.assertFalse(os.path.exists(os.path.join(self.kept, "shards")))

    def test_without_kept_no_step_gets_one(self):
        run = writes({})
        pc.check(self._tmp.name, {"X": "1"}, run, steps=self.STEPS)
        self.assertEqual(run.seen, [{"X": "1"}, {"X": "1"}])

    def test_a_step_name_is_made_safe(self):
        self.assertEqual(pc.results_name("web dependencies"),
                         "web_dependencies")
        self.assertEqual(pc.results_name("../x"), "_x")
        self.assertEqual(pc.results_name(".."), "_")


class PruneResults(StateCase):

    def folder(self, sha, age):
        path = os.path.join(self.state_dir, pc.RESULTS_DIR, sha)
        os.makedirs(os.path.join(path, "step"))
        when = (T0 - age).timestamp()
        os.utime(path, (when, when))
        return path

    def test_a_run_without_a_verdict_goes_once_old(self):
        kept = self.folder("a" * 40, datetime.timedelta(days=3))
        orphan = self.folder("b" * 40, datetime.timedelta(days=3))
        young = self.folder("c" * 40, datetime.timedelta(hours=2))
        pc.prune_results(self.state_dir, {"results": {"a" * 40: {}}}, T0)
        self.assertTrue(os.path.isdir(kept))
        self.assertFalse(os.path.exists(orphan))
        self.assertTrue(os.path.isdir(young))

    def test_no_results_directory_is_fine(self):
        pc.prune_results(self.state_dir, {"results": {}}, T0)


class ResultsInTheWorker(RepoCase):

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(pc, "STEPS", [
            ("shards", ["sh", "-c", 'echo "[1]" > "$CHECK_RESULTS/s.json"'],
             None),
            ("quiet", ["true"], None)])
        patch.start()
        self.addCleanup(patch.stop)

    def test_the_verdict_names_what_the_steps_left(self):
        head = self.pull_request({"A": "a2\n"})
        self.assertEqual(self.work(head), 0)
        record = self.state()["results"][head]
        self.assertEqual(record["results"], {
            "dir": os.path.join("results", head),
            "steps": {"shards": "shards"}})
        self.assertEqual(pathlib.Path(self.state_dir, "results", head,
                                      "shards", "s.json").read_text(),
                         "[1]\n")

    def test_too_much_is_logged(self):
        head = self.pull_request({"A": "a2\n"})
        with mock.patch.object(pc, "RESULTS_MAX", 2):
            self.assertEqual(self.work(head), 0)
        self.assertNotIn("results", self.state()["results"][head])
        self.assertFalse(os.path.exists(os.path.join(
            self.state_dir, "results", head)))
        self.assertIn("RESULTS-DROPPED #7 %s: shards left 4 bytes in "
                      "CHECK_RESULTS, over 2; none kept" % head[:10],
                      self.logged())


DAY = 24 * 3600


def make(path, size=1, age_days=0.0, executable=False, now=None):
    """A file of `size` bytes whose mtime is `age_days` before `now`."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    pathlib.Path(path).write_bytes(b"x" * size)
    if executable:
        os.chmod(path, 0o755)
    when = (now or NOW) - age_days * DAY
    os.utime(path, (when, when))
    return path


NOW = 1_800_000_000.0


class CacheLimitConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def problems(self, value):
        data = example()
        data["checks"]["cache_limit"] = value
        return config.check(data)

    def test_a_size(self):
        for good in ("40G", "512M", "1T", "100"):
            self.assertEqual(self.problems(good), [], good)
        for bad in ("40GB", "0", "-1G", "1.5G"):
            self.assertEqual(self.problems(bad), [
                "checks.cache_limit must be a size such as 40G (K, M, G "
                "or T)"], bad)
        self.assertEqual(self.problems(40),
                         ["checks.cache_limit must be a string"])

    def test_off_unless_configured(self):
        self.assertIsNone(pc.CACHE_LIMIT)
        data = example()
        data["checks"]["cache_limit"] = "40G"
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.CACHE_LIMIT, 40 << 30)

    def test_sizes(self):
        self.assertEqual([pc.size_bytes(t) for t in ("7", "2K", "3m", "1G")],
                         [7, 2048, 3 << 20, 1 << 30])


class PruneCaches(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = os.path.join(self._tmp.name, "cargo-target")
        self.deps = os.path.join(self.root, "debug", "deps")
        self.inc = os.path.join(self.root, "debug", "incremental")

    def path(self, *parts):
        return os.path.join(self.root, *parts)

    def test_stale_test_executables_go(self):
        old = make(self.path("debug", "deps", "t-aaa"), 10, 3, True)
        new = make(self.path("debug", "deps", "t-bbb"), 10, 0.1, True)
        lib = make(self.path("debug", "deps", "libx.rlib"), 10, 3)
        deeper = make(self.path("debug", "deps", "sub", "t-ccc"), 10, 3, True)
        gone = pc.prune_caches([self.root], None, NOW)
        self.assertFalse(os.path.exists(old))
        for kept in (new, lib, deeper):
            self.assertTrue(os.path.exists(kept), kept)
        self.assertEqual((gone["executables"], gone["freed"], gone["left"]),
                         (1, 10, 30))

    def test_what_mains_last_green_run_wrote_stays(self):
        mains = make(self.path("debug", "deps", "t-main"), 1, 3, True)
        older = make(self.path("debug", "deps", "t-old"), 1, 5, True)
        pc.prune_caches([self.root], None, NOW, main_since=NOW - 4 * DAY)
        self.assertTrue(os.path.exists(mains))
        self.assertFalse(os.path.exists(older))

    def test_untouched_incremental_directories_go(self):
        make(self.path("debug", "incremental", "a-1", "s", "f"), 5, 10)
        make(self.path("debug", "incremental", "b-2", "f"), 5, 10)
        make(self.path("debug", "incremental", "b-2", "g"), 5, 1)
        gone = pc.prune_caches([self.root], None, NOW)
        self.assertEqual(sorted(os.listdir(self.inc)), ["b-2"])
        self.assertEqual((gone["incremental"], gone["freed"]), (1, 5))

    def test_then_the_oldest_files_until_under_the_limit(self):
        first = make(self.path("a"), 40, 0.5)
        second = make(self.path("b"), 40, 0.4)
        third = make(self.path("c"), 40, 0.3)
        gone = pc.prune_caches([self.root], 90, NOW)
        self.assertEqual([os.path.exists(p) for p in (first, second, third)],
                         [False, True, True])
        self.assertEqual((gone["files"], gone["left"]), (1, 80))

    def test_the_limit_spans_the_slots_caches(self):
        other = os.path.join(self._tmp.name, "node-cache")
        make(self.path("a"), 40, 0.5)
        make(os.path.join(other, "b"), 40, 0.6)
        gone = pc.prune_caches([self.root, other], 50, NOW)
        self.assertEqual((gone["files"], gone["left"]), (1, 40))
        self.assertEqual(os.listdir(other), [])

    def test_links_are_never_followed(self):
        outside = make(os.path.join(self._tmp.name, "outside", "t-x"), 100,
                       9, True)
        os.makedirs(self.deps)
        os.symlink(outside, os.path.join(self.deps, "t-link"))
        os.symlink(os.path.dirname(outside), self.path("dir-link"))
        gone = pc.prune_caches([self.root], 1, NOW)
        self.assertTrue(os.path.exists(outside))
        self.assertTrue(os.path.islink(os.path.join(self.deps, "t-link")))
        self.assertEqual(gone["freed"], 0)

    def test_a_root_that_is_a_link_or_missing_is_left_alone(self):
        real = make(os.path.join(self._tmp.name, "real", "a"), 10, 9)
        os.symlink(os.path.dirname(real), self.root)
        self.assertEqual(pc.prune_caches(
            [self.root, self.path("nothing")], 1, NOW)["freed"], 0)
        self.assertTrue(os.path.exists(real))


class PruneSlotCaches(StateCase):

    def setUp(self):
        super().setUp()
        for name, value in (("CACHE_LIMIT", 50),
                            ("CACHES", {"CARGO_TARGET_DIR": "cargo-target"})):
            patch = mock.patch.object(pc, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_the_slots_own_caches_and_a_log_line(self):
        now = pc.time.time()
        mine = make(os.path.join(self.state_dir, "cargo-target-2", "a"), 60,
                    0.5, now=now)
        theirs = make(os.path.join(self.state_dir, "cargo-target", "a"), 60,
                      0.5, now=now)
        gone = pc.prune_slot_caches(self.state_dir, 2, self.lines.append)
        self.assertFalse(os.path.exists(mine))
        self.assertTrue(os.path.exists(theirs))
        self.assertEqual(gone["freed"], 60)
        self.assertIn("CACHE-PRUNED slot 2: freed 60 bytes (0 executables, "
                      "0 incremental directories, 1 files over the limit); "
                      "0 bytes left", self.lines[-1])

    def test_off_without_a_limit(self):
        with mock.patch.object(pc, "CACHE_LIMIT", None):
            self.assertIsNone(pc.prune_slot_caches(self.state_dir, 1))

    def test_main_since_comes_from_the_state(self):
        self.write_state({"cache_main": {"1": 123.0}})
        seen = []
        with mock.patch.object(pc, "prune_caches",
                               lambda *a: seen.append(a) or {"freed": 0}):
            pc.prune_slot_caches(self.state_dir, 1)
        self.assertEqual(seen[0][3], 123.0)

    def test_a_failure_is_logged_not_raised(self):
        def broken(*args):
            raise OSError("disk gone")
        with mock.patch.object(pc, "prune_caches", broken):
            self.assertIsNone(pc.prune_slot_caches(
                self.state_dir, 1, self.lines.append))
        self.assertIn("CACHE-PRUNE-FAILED slot 1: OSError: disk gone",
                      self.lines[-1])


class CachesInTheWorker(RepoCase):

    def setUp(self):
        super().setUp()
        for name, value in (("CACHE_LIMIT", 1 << 30),
                            ("CACHES", {"CARGO_TARGET_DIR": "cargo-target"})):
            patch = mock.patch.object(pc, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def test_a_run_prunes_its_slot(self):
        stale = make(os.path.join(self.state_dir, "cargo-target", "debug",
                                  "deps", "t-old"), 5, 3, True,
                     now=pc.time.time())
        head = self.pull_request({"A": "a2\n"})
        self.assertEqual(self.work(head), 0)
        self.assertFalse(os.path.exists(stale))
        self.assertIn("CACHE-PRUNED slot 1: freed 5 bytes", self.logged())

    def test_a_green_main_run_records_its_start(self):
        tip = self.move_main({"B": "b2\n"})
        self.write_state({"waiting": {tip: {
            "pr": 0, "main": True, "since": pc.iso(T0)}}, "results": {}})
        clock = iter([T0, T0 + datetime.timedelta(minutes=1)])
        before = pc.time.time()
        pc.worker(self.repo, self.state_dir, tip, 0, pc.sh, self.lines.append,
                  lambda: next(clock), None, main=True)
        self.assertGreaterEqual(self.state()["cache_main"]["1"], before)


if __name__ == "__main__":
    unittest.main()
