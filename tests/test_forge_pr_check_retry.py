"""Tests for two merge.test_ahead switches in forge-pr-check:
`stop_spent` (designs/0014, amendment of 2026-11-10), which stops a
running prediction the Merger no longer predicts, and `retry_red`
(designs/0035, seventh amendment), which runs a prediction's red steps
once more before the red stands."""

import datetime
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
from tests.test_forge_pr_check_main import Writer


def configured(flake_issue=None, router=None, **ahead):
    data = example()
    data["merge"]["test_ahead"] = dict({"max_age_hours": 24}, **ahead)
    if flake_issue is not None:
        data["checks"]["flake_issue"] = flake_issue
    if router is not None:
        data["router"] = router
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


class FlakeHook(base.StateCase):
    """checks.flake_issue: the worker hands each step a retry turned
    green, with its red run's tail, to `flake`."""

    flaky = Retry.flaky

    def setUp(self):
        super().setUp()
        configured(retry_red=True)
        self.addCleanup(pc.configure, config.Project(example(), None))

    def flagged(self, run):
        got = []
        os.makedirs(os.path.join(self.state_dir, "wt", ".git"), exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        code = pc.worker("/repo", self.state_dir, SHA_P, 2, run,
                         self.lines.append, lambda: next(clock), None, None,
                         ahead=True, flake=lambda *a: got.append(a))
        return code, got

    def test_a_red_that_goes_green_is_handed_over_with_its_tail(self):
        code, got = self.flagged(self.flaky(1))
        self.assertEqual(code, 0)
        (sha, pr, failed), = got
        self.assertEqual((sha, pr), (SHA_P, 2))
        (name, tail), = failed
        self.assertEqual(name, "test")
        self.assertIn("it broke", tail)

    def test_a_red_again_is_not_a_flake(self):
        self.assertEqual(self.flagged(self.flaky(2))[1], [])

    def test_a_green_first_time_is_not_a_flake(self):
        self.assertEqual(self.flagged(self.flaky(0))[1], [])


class FailingTest(unittest.TestCase):
    """failing_test: the first test a red step's output names."""

    def test_each_harness(self):
        for text, name in (
                ("ok\n--- FAIL: TestQueue (0.01s)\n", "TestQueue"),
                ("test a::b ... FAILED\n", "a::b"),
                ("---- a::c stdout ----\n", "a::c"),
                ("thread 'a::d' (12) panicked at src/x.rs:1:2:\n", "a::d"),
                ("FAIL: test_x (pkg.Case)\n", "test_x (pkg.Case)"),
                ("\x1b[31mtest a::e ... FAILED\x1b[0m\n", "a::e")):
            self.assertEqual(pc.failing_test(text), name)

    def test_none_named(self):
        self.assertIsNone(pc.failing_test("make: *** [x] Error 1\n"))
        self.assertIsNone(pc.failing_test(
            "thread 'main' panicked at src/main.rs:1:1:\n"))


class ReportFlake(unittest.TestCase):
    """report_flake: one issue per flaky step and test, commented on
    while it is open."""

    def setUp(self):
        configured(retry_red=True, flake_issue={"labels": ["bug"],
                                                "owner": "Helper"})
        self.addCleanup(pc.configure, config.Project(example(), None))
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        pc.save(self.dir, {"results": {}, "waiting": {}})
        self.lines = []

    def report(self, write, text="test a::b ... FAILED\n", sha=SHA_P):
        pc.report_flake(self.dir, 7, sha, [("test", text)], write, [5, 7],
                        self.lines.append)

    def test_the_first_flake_files_a_labelled_issue(self):
        write = Writer()
        self.report(write)
        (method, path, body), = write.writes
        self.assertEqual((method, path),
                         ("POST", "/repos/%s/issues" % pc.REPO_SLUG))
        self.assertEqual(body["title"], "Flaky step: test, a::b")
        self.assertEqual(body["labels"], ["bug", "Owner:Helper"])
        self.assertIn("#7 at %s" % SHA_P[:10], body["body"])
        self.assertIn("The prediction held #5, #7.", body["body"])
        self.assertIn("test a::b ... FAILED", body["body"])
        self.assertEqual(pc.load(self.dir)["flake_issues"],
                         {"test\ta::b": 42})
        self.assertIn("FLAKE-FILED #42: test (a::b) flaked on #7",
                      self.lines[-1])

    def test_a_repeat_comments_on_the_open_issue(self):
        self.report(Writer())
        write = Writer()
        self.report(write, sha=SHA_Q)
        (method, path, body), = write.writes
        self.assertEqual((method, path), ("POST", "/repos/%s/issues/42/"
                                          "comments" % pc.REPO_SLUG))
        self.assertTrue(body["body"].startswith("Again: `test`"))
        self.assertIn("FLAKE-COMMENTED #42", self.lines[-1])

    def test_once_closed_the_next_files_a_new_one(self):
        self.report(Writer())
        write = Writer(issue_state="closed")
        write.next = 50
        self.report(write)
        (method, path, _), = write.writes
        self.assertEqual(path, "/repos/%s/issues" % pc.REPO_SLUG)
        self.assertEqual(pc.load(self.dir)["flake_issues"],
                         {"test\ta::b": 51})

    def test_another_test_of_the_step_is_its_own_issue(self):
        self.report(Writer())
        write = Writer()
        self.report(write, text="test a::z ... FAILED\n")
        self.assertEqual(write.writes[0][1],
                         "/repos/%s/issues" % pc.REPO_SLUG)

    def test_a_refused_write_is_logged(self):
        self.report(Writer({"POST": 403}))
        self.assertIn("REPORT-FAILED", self.lines[-1])
        self.assertNotIn("flake_issues", {
            k: v for k, v in pc.load(self.dir).items() if v})


class FlakeConfig(unittest.TestCase):
    """checks.flake_issue as configured and as checked."""

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_off_without_it(self):
        configured(retry_red=True)
        self.assertIsNone(pc.FLAKE_ISSUE)

    def test_the_owner_defaults_to_the_triager(self):
        configured(retry_red=True, flake_issue={},
                   router={"owners": {"triage": "LeadDev"}})
        self.assertEqual(pc.FLAKE_ISSUE, {"labels": ["Owner:LeadDev"]})
        configured(retry_red=True, flake_issue={"labels": ["x"]})
        self.assertEqual(pc.FLAKE_ISSUE, {"labels": ["x"]})

    def check(self, flake, **ahead):
        data = example()
        data["merge"]["test_ahead"] = dict({"max_age_hours": 24}, **ahead)
        data["checks"]["flake_issue"] = flake
        return [p for p in config.check(data) if "flake_issue" in p]

    def test_it_is_checked(self):
        self.assertEqual(self.check({"labels": ["bug"], "owner": "Helper"},
                                    retry_red=True), [])
        self.assertEqual(len(self.check({}, retry_red=False)), 1)
        self.assertEqual(len(self.check({"owner": "Nobody"},
                                        retry_red=True)), 1)
        self.assertEqual(len(self.check({"labels": [""]},
                                        retry_red=True)), 1)
        self.assertEqual(len(self.check({"colour": "red"},
                                        retry_red=True)), 1)


if __name__ == "__main__":
    unittest.main()
