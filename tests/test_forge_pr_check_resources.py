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


if __name__ == "__main__":
    unittest.main()
