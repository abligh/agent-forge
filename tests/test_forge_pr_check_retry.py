"""Tests for two merge.test_ahead switches in forge-pr-check:
`stop_spent` (designs/0014, amendment of 2026-11-10), which stops a
running prediction the Merger no longer predicts, and `retry_red`
(designs/0035, seventh amendment), which runs a prediction's red steps
once more before the red stands."""

import os
import pathlib
import tempfile
import unittest

from agent_forge import config
from tests.helpers import example
from tests import test_forge_pr_check as base
from tests.test_forge_pr_check import (SHA_A, SHA_C, SHA_P, SHA_Q, T0,
                                       TREE_1, TREE_2, TREE_3, FakeRun, done,
                                       entry, pc, write_ahead)


def configured(**ahead):
    data = example()
    data["merge"]["test_ahead"] = dict({"max_age_hours": 24}, **ahead)
    pc.configure(config.Project(data, None))


class Spent(unittest.TestCase):
    """merger_spent: the running predictions no entry has any more."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.trees = {SHA_P: TREE_1, SHA_Q: TREE_3, SHA_C: TREE_2}
        self.run = FakeRun((lambda a: a[-1].endswith("^{tree}"),
                            lambda a: done(a, 0, self.trees.get(
                                a[-1][:40], "") + "\n")))

    def spent(self, running):
        return pc.merger_spent(self.dir, T0, dict.fromkeys(running, "/repo"),
                               run=self.run)

    def test_a_prediction_no_entry_has_is_spent(self):
        write_ahead(self.dir, [entry(7, SHA_P, TREE_1)])
        self.assertEqual(self.spent([SHA_P, SHA_Q]), {SHA_Q})

    def test_one_whose_tree_an_entry_still_has_is_kept(self):
        # Another commit with the same tree: a head can still match it.
        write_ahead(self.dir, [entry(7, SHA_C, TREE_3)])
        self.assertEqual(self.spent([SHA_Q]), set())

    def test_later_batches_entries_count(self):
        write_ahead(self.dir, [entry(7, SHA_P, TREE_1),
                               entry(8, SHA_Q, TREE_3),
                               entry(9, SHA_C, TREE_2)], batch=1, later=(2,))
        self.assertEqual(self.spent([SHA_P, SHA_Q, SHA_C]), set())

    def test_nothing_is_spent_without_a_fresh_list(self):
        write_ahead(self.dir, [])
        self.assertEqual(self.spent([SHA_Q]), set())
        write_ahead(self.dir, [entry(7, SHA_P, TREE_1)],
                    when=T0 - 2 * pc.MERGER_FRESH)
        self.assertEqual(self.spent([SHA_Q]), set())
        self.assertEqual(pc.merger_spent(os.path.join(self.dir, "no"), T0,
                                         {SHA_Q: "/repo"}, run=self.run),
                         set())

    def test_one_whose_tree_git_cannot_name_is_kept(self):
        write_ahead(self.dir, [entry(7, SHA_P, TREE_1)])
        self.trees.pop(SHA_Q)
        self.assertEqual(self.spent([SHA_Q]), set())

    def test_the_switch_is_read(self):
        self.addCleanup(pc.configure, config.Project(example(), None))
        configured(stop_spent=True, retry_red=True)
        self.assertTrue(pc.STOP_SPENT)
        self.assertTrue(pc.RETRY_RED)
        configured()
        self.assertFalse(pc.STOP_SPENT)
        self.assertFalse(pc.RETRY_RED)


class Retry(base.StateCase):
    """retry_red: a prediction's red steps run once more."""

    ahead, logged = base.WorkerAhead.ahead, base.WorkerAhead.logged
    work, queue = base.Worker.work, base.Worker.queue

    def setUp(self):
        super().setUp()
        configured(retry_red=True)
        self.addCleanup(pc.configure, config.Project(example(), None))

    def flaky(self, times):
        """A run whose `test` step is red its first `times` runs."""
        runs = []

        def step(args):
            runs.append(1)
            return done(args, 1, "", "it broke\n") if len(runs) <= times \
                else done(args, 0)
        self.runs = runs
        return FakeRun((lambda a: a[-1].endswith("^{tree}"),
                        done([], 0, TREE_1 + "\n")),
                       (lambda a: "FETCH_HEAD" in a,
                        done([], 0, SHA_A + "\n")),
                       (lambda a: a[0] == "make" and a[-1] == "test", step))

    def made(self, run):
        return [c["args"][-1] for c in run.calls if c["args"][0] == "make"]

    def test_a_red_that_goes_green_the_second_time_is_green(self):
        run = self.flaky(1)
        self.assertEqual(self.ahead(run), 0)
        # Only the red step runs again.
        self.assertEqual(self.made(run).count("test"), 2)
        self.assertEqual(self.made(run).count("vet"), 1)
        r = self.state()["results"][SHA_P]
        self.assertEqual((r["ok"], r["failed"], r["retried"]),
                         (True, [], {"steps": ["test"], "ok": True}))
        self.assertIn("RETRIED AHEAD #2 dddddddddd: test red; run once more "
                      "on the same tree: green", self.logged())
        self.assertIn("VERDICT AHEAD #2 dddddddddd: green", self.logged())

    def test_a_red_again_stands(self):
        run = self.flaky(2)
        self.assertEqual(self.ahead(run), 1)
        self.assertEqual(len(self.runs), 2)
        r = self.state()["results"][SHA_P]
        self.assertEqual((r["ok"], r["failed"], r["retried"]),
                         (False, ["test"], {"steps": ["test"], "ok": False}))
        self.assertIn("red again; the red stands", self.logged())

    def test_without_the_switch_a_red_runs_once(self):
        configured()
        run = self.flaky(1)
        self.assertEqual(self.ahead(run), 1)
        self.assertEqual(len(self.runs), 1)
        self.assertNotIn("retried", self.state()["results"][SHA_P])
        self.assertNotIn("RETRIED", self.logged())

    def test_a_pull_requests_own_head_is_not_run_again(self):
        self.queue()
        run = self.flaky(1)
        self.assertEqual(self.work(run), 1)
        self.assertEqual(len(self.runs), 1)
        self.assertNotIn("RETRIED", pathlib.Path(
            self.state_dir, "verdicts.log").read_text())


if __name__ == "__main__":
    unittest.main()
