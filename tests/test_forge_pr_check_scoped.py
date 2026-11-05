"""Tests for designs/0048 §1-§3: scoped steps (`checks.steps[].pull_requests`)
and their budget (`checks.scoped_minutes`).

The worker's tests use real git, since what matters is what a head
changes since its merge base.
"""

import datetime
import os
import pathlib
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import T0, done, git, pc
from tests.test_forge_pr_check_merge import RepoCase
from tests.test_forge_pr_check_side_by_side import side


def scoped_config(**step):
    data = example()
    entry = {"name": "slow", "run": ["make", "slow"],
             "pull_requests": {"paths": ["corpus/*"]}}
    entry.update(step)
    data["checks"]["steps"].append(entry)
    return data


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_off_unless_a_step_is_scoped(self):
        pc.configure(config.Project(example(), None))
        self.assertEqual(pc.SCOPED, {})
        self.assertEqual(pc.SCOPED_S, 3600)

    def test_a_scoped_step_and_the_budget(self):
        data = scoped_config(pull_requests={"paths": ["corpus/*"],
                                            "label": "Check:slow"})
        data["checks"]["scoped_minutes"] = 90
        self.assertEqual(config.check(data), [])
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.SCOPED, {"slow": (("corpus/*",), "Check:slow")})
        self.assertEqual(pc.SCOPED_S, 90 * 60)
        # Still one of the steps, in its place.
        self.assertEqual(pc.STEPS[-1][0], "slow")

    def test_the_scope_is_left_out_of_the_steps_digest(self):
        pc.configure(config.Project(scoped_config(), None))
        before = pc.STEP_ENTRIES["slow"]
        pc.configure(config.Project(scoped_config(pull_requests={
            "paths": ["other/*"]}), None))
        self.assertEqual(pc.STEP_ENTRIES["slow"], before)

    def test_a_scope_needs_paths_or_a_label(self):
        problems = config.check(scoped_config(pull_requests={}))
        self.assertTrue(any("checks.steps[5].pull_requests needs paths or "
                            "a label" in p for p in problems), problems)

    def test_an_empty_glob_and_a_bad_label(self):
        problems = config.check(scoped_config(pull_requests={
            "paths": [""], "label": 3}))
        self.assertTrue(any("pull_requests.paths: each must be a non-empty"
                            in p for p in problems), problems)
        self.assertTrue(any("pull_requests.label" in p for p in problems),
                        problems)

    def test_the_budget_at_least_a_minute(self):
        data = scoped_config()
        data["checks"]["scoped_minutes"] = 0
        self.assertIn("checks.scoped_minutes must be at least 1",
                      config.check(data))

    def test_refused_in_an_instances_steps(self):
        data = example()
        data["checks"]["instances"] = {"mac": {
            "context": "vm/mac", "steps": [
                {"name": "t", "run": ["make", "t"],
                 "pull_requests": {"paths": ["a/*"]}}]}}
        problems = config.check(data)
        self.assertTrue(any("checks.instances.mac.steps[0].pull_requests: "
                            "only checks' own steps" in p for p in problems),
                        problems)

    def test_no_budget_in_an_instance(self):
        data = example()
        data["checks"]["instances"] = {"mac": {
            "context": "vm/mac", "steps": [{"name": "t", "run": ["t"]}],
            "scoped_minutes": 5}}
        self.assertTrue(any("unknown key 'scoped_minutes'" in p
                            for p in config.check(data)))

    def test_an_unscoped_step_cant_wait_for_a_scoped_one(self):
        data = scoped_config()
        data["checks"]["steps"][1]["after"] = ["slow"]
        self.assertTrue(any("'slow' is scoped, so it runs after every step "
                            "that isn't" in p for p in config.check(data)))


class Helpers(unittest.TestCase):

    def test_scope_labels_keeps_only_the_scoped_labels(self):
        with mock.patch.object(pc, "SCOPED", {"slow": ((), "Check:slow")}):
            got = pc.scope_labels([
                (1, "a" * 40, "r", False, "x", ["Check:slow", "Other"]),
                (2, "b" * 40, "r", False, "x", ["Other"]),
                (3, "c" * 40, "r", True, "x", ["Check:slow"])])
        self.assertEqual(got, {"a" * 40: ["Check:slow"]})

    def test_last_main_is_the_newest_default_branch_verdict(self):
        state = {"results": {
            "a": {"main": True, "finished": "2026-10-09T10:00:00Z"},
            "b": {"main": True, "finished": "2026-10-09T11:00:00Z"},
            "c": {"finished": "2026-10-09T12:00:00Z"}}}
        self.assertEqual(pc.last_main(state, "z"), "b")
        self.assertEqual(pc.last_main(state, "b"), "a")
        self.assertIsNone(pc.last_main({"results": {}}, "z"))

    def test_a_match_must_have_run_what_the_head_needs(self):
        ran = {"scoped": {"slow": "ran: changes corpus/x",
                          "other": pc.OUT_OF_SCOPE}}
        accept = pc.scoped_too(None, {"slow": "changes corpus/y"})
        self.assertTrue(accept(ran))
        self.assertFalse(accept({"scoped": {"slow": pc.OUT_OF_SCOPE}}))
        # A verdict from before scoped steps says nothing of them.
        self.assertFalse(accept({}))
        self.assertTrue(accept({"scoped": {"slow": "reused: label L"}}))
        # And the other test still counts.
        self.assertFalse(pc.scoped_too(lambda r: False, {"slow": ""})(ran))
        # A head that needs none takes what `accept` takes.
        self.assertIsNone(pc.scoped_too(None, {}))

    def test_the_fast_lane_never_takes_a_scoped_head(self):
        costs = {"a": 5, "slow": 5}
        self.assertEqual(pc.lane_cost({"lane_steps": ["a"]}, costs), 5)
        self.assertIsNone(pc.lane_cost(
            {"lane_steps": ["a", "slow"], "lane_scoped": ["slow"]}, costs))


class Budget(unittest.TestCase):
    """check()'s deadline: a step stopped there, or not started."""

    def setUp(self):
        self.wt = os.getcwd()

    def test_a_step_after_the_deadline_isnt_run(self):
        calls = []
        ok, results = pc.check(self.wt, {}, lambda a, **k: calls.append(a)
                               or done(a), steps=[("slow", ["slow"], None)],
                               deadline=0)
        self.assertFalse(ok)
        self.assertEqual(calls, [])
        self.assertTrue(results[0]["over_budget"])
        self.assertIn("not started: over the scoped budget", results[0]["tail"])

    def test_a_step_stopped_at_the_deadline(self):
        seen = {}

        def run(args, cwd=None, env=None, timeout=None):
            seen["timeout"] = timeout
            return done(args, 124, "", "timed out")
        import time
        with mock.patch.object(pc, "STEP_TIMEOUT_S", 1800):
            ok, results = pc.check(self.wt, {}, run,
                                   steps=[("slow", ["slow"], None)],
                                   deadline=time.monotonic() + 100)
        self.assertFalse(ok)
        self.assertLessEqual(seen["timeout"], 100)
        self.assertTrue(results[0]["over_budget"])
        self.assertTrue(results[0]["tail"].startswith(
            "stopped: over the scoped budget"))

    def test_the_step_timeout_still_stands_when_sooner(self):
        seen = {}

        def run(args, cwd=None, env=None, timeout=None):
            seen["timeout"] = timeout
            return done(args, 124)
        import time
        with mock.patch.object(pc, "STEP_TIMEOUT_S", 60):
            ok, results = pc.check(self.wt, {}, run,
                                   steps=[("slow", ["slow"], None)],
                                   deadline=time.monotonic() + 3600)
        self.assertEqual(seen["timeout"], 60)
        self.assertNotIn("over_budget", results[0])


class ScopedCase(RepoCase):
    """A fast step and a slow one scoped to corpus/*, each writing its
    name to `order` as it runs."""

    def setUp(self):
        super().setUp()
        self.order = os.path.join(self._tmp.name, "order")
        self.use([("fast", self.step("fast"), None),
                  ("slow", self.step("slow"), None)],
                 {"slow": (("corpus/*",), "Check:slow")})

    def use(self, steps, scoped, merge=False):
        for name, value in (("STEPS", steps), ("SCOPED", scoped),
                            ("MERGE_DEFAULT", merge)):
            patch = mock.patch.object(pc, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def step(self, name, rc=0, then=""):
        return ["sh", "-c", "echo %s >> %s; %s exit %d"
                % (name, self.order, then, rc)]

    def ran(self):
        path = pathlib.Path(self.order)
        return path.read_text().split() if path.exists() else []

    def work_labelled(self, sha, labels):
        state = self.state()
        state["scope_labels"] = {sha: labels}
        self.write_state(state)
        clock = iter([T0, T0 + datetime.timedelta(minutes=1)])
        state = self.state()
        state["waiting"] = {sha: {"pr": 7, "since": pc.iso(T0)}}
        self.write_state(state)
        return pc.worker(self.repo, self.state_dir, sha, 7, pc.sh,
                         self.lines.append, lambda: next(clock),
                         lambda *a: self.posts.append(a) or True)


class Scoped(ScopedCase):

    def test_out_of_scope_is_left_out(self):
        head = self.pull_request({"A": "a2\n"})
        self.assertEqual(self.work(head), 0)
        self.assertEqual(self.ran(), ["fast"])
        record = self.state()["results"][head]
        self.assertEqual(record["scoped"], {"slow": "skipped: out of scope"})
        self.assertEqual(record["scoped_seconds"], 0)
        self.assertIn("  scoped: slow out of scope", self.lines)

    def test_a_matching_path_runs_it_last(self):
        head = self.pull_request({"corpus/x": "x\n"})
        self.assertEqual(self.work(head), 0)
        self.assertEqual(self.ran(), ["fast", "slow"])
        record = self.state()["results"][head]
        self.assertEqual(record["scoped"], {"slow": "ran: changes corpus/x"})
        self.assertTrue(record["ok"])

    def test_scoped_steps_run_after_steps_listed_later(self):
        self.use([("slow", self.step("slow"), None),
                  ("fast", self.step("fast"), None)],
                 {"slow": (("corpus/*",), None)})
        head = self.pull_request({"corpus/x": "x\n"})
        self.work(head)
        self.assertEqual(self.ran(), ["fast", "slow"])

    def test_a_label_runs_it_whatever_changed(self):
        head = self.pull_request({"A": "a2\n"})
        self.assertEqual(self.work_labelled(head, ["Check:slow"]), 0)
        self.assertEqual(self.ran(), ["fast", "slow"])
        self.assertEqual(self.state()["results"][head]["scoped"],
                         {"slow": "ran: label Check:slow"})

    def test_not_run_after_an_earlier_red(self):
        self.use([("fast", self.step("fast", rc=1), None),
                  ("slow", self.step("slow"), None)],
                 {"slow": (("corpus/*",), None)})
        head = self.pull_request({"corpus/x": "x\n"})
        self.assertEqual(self.work(head), 1)
        self.assertEqual(self.ran(), ["fast"])
        record = self.state()["results"][head]
        self.assertEqual(record["failed"], ["fast"])
        self.assertEqual(record["scoped"],
                         {"slow": "skipped: an earlier step failed"})

    def test_over_the_budget_is_red(self):
        self.use([("fast", self.step("fast"), None),
                  ("slow", self.step("slow", then="sleep 5;"), None),
                  ("slower", self.step("slower"), None)],
                 {"slow": (("corpus/*",), None),
                  "slower": (("corpus/*",), None)})
        head = self.pull_request({"corpus/x": "x\n"})
        with mock.patch.object(pc, "SCOPED_S", 1):
            self.assertEqual(self.work(head), 1)
        record = self.state()["results"][head]
        self.assertEqual(record["failed"], ["slow", "slower"])
        self.assertEqual(record["scoped"], {"slow": pc.OVER_BUDGET,
                                            "slower": pc.OVER_BUDGET})
        # The second never started.
        self.assertEqual(self.ran(), ["fast", "slow"])

    def test_side_by_side_scoped_steps_still_wait_for_the_rest(self):
        for p in [mock.patch.object(pc, "SIDE_BY_SIDE", 4)] + side():
            p.start()
            self.addCleanup(p.stop)
        self.use([("fast", self.step("fast", then="sleep 0.3;"), None),
                  ("slow", self.step("slow"), None)],
                 {"slow": (("corpus/*",), None)})
        head = self.pull_request({"corpus/x": "x\n"})
        self.assertEqual(self.work(head), 0)
        self.assertEqual(self.ran(), ["fast", "slow"])

    def test_the_scope_decides_over_the_tracer(self):
        # With depends, a tracer's choice that leaves the step out, or
        # takes it in, gives way to the scope.
        selection = {"base": self.first, "record": self.first,
                     "depends": "/nowhere", "ran": {"fast": "x"},
                     "skipped": ["slow"]}
        with mock.patch.object(pc, "DEPENDS", True), \
                mock.patch.object(pc, "choose", lambda *a, **k: {
                    k: (dict(v) if isinstance(v, dict) else
                        list(v) if isinstance(v, list) else v)
                    for k, v in selection.items()}):
            head = self.pull_request({"corpus/x": "x\n"})
            self.work(head)
            record = self.state()["results"][head]
        self.assertEqual(self.ran(), ["fast", "slow"])
        self.assertEqual(record["selection"]["ran"]["slow"],
                         "in scope: changes corpus/x")
        self.assertNotIn("slow", record["selection"]["skipped"])


class Matching(ScopedCase):
    """merge.test_ahead: a head is matched to a green verdict on its tree
    only when that verdict ran every scoped step it needs."""

    def setUp(self):
        super().setUp()
        patch = mock.patch.object(pc, "TEST_AHEAD", True)
        patch.start()
        self.addCleanup(patch.stop)

    def twin(self):
        """Pull request 7's head again, the same tree, another commit."""
        git("commit", "-q", "--amend", "-m", "again", cwd=self.seed)
        self.push_head()
        return git("rev-parse", "HEAD", cwd=self.seed)

    def test_not_matched_to_a_verdict_that_left_it_out(self):
        head = self.pull_request({"corpus/x": "x\n"})
        with mock.patch.object(pc, "SCOPED", {}):
            self.work(head)
        self.assertEqual(self.ran(), ["fast", "slow"])
        again = self.twin()
        self.work(again, at=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(self.ran(), ["fast", "slow", "fast", "slow"])
        self.assertNotIn("matched", self.state()["results"][again])

    def test_matched_to_one_that_ran_it(self):
        head = self.pull_request({"corpus/x": "x\n"})
        self.work(head)
        again = self.twin()
        self.work(again, at=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(self.ran(), ["fast", "slow"])
        self.assertEqual(self.state()["results"][again]["matched"], head)


class FastLane(ScopedCase):

    def test_the_estimate_says_a_scoped_step_would_run(self):
        head = self.pull_request({"corpus/x": "x\n"})
        git("fetch", "-q", "origin", "refs/pull/7/head", cwd=self.repo)
        state = self.state()
        state["waiting"] = {head: {"pr": 7, "since": pc.iso(T0)}}
        self.write_state(state)
        with mock.patch.object(pc, "lane_steps", lambda *a, **k: {
                "base": self.first, "ran": {"fast": "x"}, "skipped": [],
                "stale": []}):
            pc.estimate(self.state_dir, self.repo, run=pc.sh)
        entry = self.state()["waiting"][head]
        self.assertEqual(entry["lane_steps"], ["fast", "slow"])
        self.assertEqual(entry["lane_scoped"], ["slow"])
        self.assertIsNone(pc.lane_cost(entry, {"fast": 1, "slow": 1}))

    def test_one_out_of_scope_stays_cheap(self):
        head = self.pull_request({"A": "a2\n"})
        git("fetch", "-q", "origin", "refs/pull/7/head", cwd=self.repo)
        state = self.state()
        state["waiting"] = {head: {"pr": 7, "since": pc.iso(T0)}}
        self.write_state(state)
        with mock.patch.object(pc, "lane_steps", lambda *a, **k: {
                "base": self.first, "ran": {"fast": "x", "slow": "y"},
                "skipped": [], "stale": []}):
            pc.estimate(self.state_dir, self.repo, run=pc.sh)
        entry = self.state()["waiting"][head]
        self.assertEqual(entry["lane_steps"], ["fast"])
        self.assertEqual(entry["lane_scoped"], [])
        self.assertEqual(pc.lane_cost(entry, {"fast": 1, "slow": 1}), 1)


class OnTheDefaultBranch(ScopedCase):

    def work_main(self, sha):
        state = self.state()
        state["waiting"] = {sha: {"pr": 0, "since": pc.iso(T0),
                                  "main": True}}
        self.write_state(state)
        clock = iter([T0, T0 + datetime.timedelta(minutes=1)])
        return pc.worker(self.repo, self.state_dir, sha, 0, pc.sh,
                         self.lines.append, lambda: next(clock), None,
                         main=True)

    def checked_main(self, sha):
        state = self.state()
        state["results"][sha] = {"main": True, "ok": True,
                                 "finished": pc.iso(T0)}
        self.write_state(state)

    def test_needed_never_runs_one(self):
        tip = self.move_main({"corpus/x": "x\n"})
        with mock.patch.object(pc, "MAIN_EVERY", False):
            self.work_main(tip)
        self.assertEqual(self.ran(), ["fast"])

    def test_every_runs_one_for_changes_since_the_last_head(self):
        self.checked_main(self.first)
        tip = self.move_main({"corpus/x": "x\n"})
        with mock.patch.object(pc, "MAIN_EVERY", True):
            self.work_main(tip)
        self.assertEqual(self.ran(), ["fast", "slow"])
        self.assertEqual(self.state()["results"][tip]["scoped"],
                         {"slow": "ran: changes corpus/x"})

    def test_every_leaves_it_out_when_nothing_matching_changed(self):
        self.checked_main(self.move_main({"corpus/x": "x\n"}))
        tip = self.move_main({"A": "a3\n"})
        with mock.patch.object(pc, "MAIN_EVERY", True):
            self.work_main(tip)
        self.assertEqual(self.ran(), ["fast"])

    def test_every_runs_each_with_no_earlier_head(self):
        tip = self.move_main({"A": "a3\n"})
        with mock.patch.object(pc, "MAIN_EVERY", True):
            self.work_main(tip)
        self.assertEqual(self.ran(), ["fast", "slow"])
        self.assertEqual(self.state()["results"][tip]["scoped"],
                         {"slow": "ran: no earlier main head checked here"})


class Predictions(ScopedCase):
    """A Merger's prediction: a batch's or a train's top (§3)."""

    def predict(self, *heads):
        git("checkout", "-q", "--detach", "main", cwd=self.seed)
        for head in heads:
            git("merge", "-q", "--no-ff", "--no-edit", head, cwd=self.seed)
        sha = git("rev-parse", "HEAD", cwd=self.seed)
        git("push", "-q", "-f", self.origin, "HEAD:refs/heads/prediction",
            cwd=self.seed)
        git("fetch", "-q", "origin", cwd=self.repo)
        return sha

    def work_ahead(self, sha, labels=None):
        state = self.state()
        state["scope_labels"] = labels or {}
        self.write_state(state)
        clock = iter([T0, T0 + datetime.timedelta(minutes=1)])
        return pc.worker(self.repo, self.state_dir, sha, 7, pc.sh,
                         self.lines.append, lambda: next(clock), None,
                         ahead=True)

    def test_any_members_change_runs_it(self):
        one = self.pull_request({"A": "a2\n"})
        git("checkout", "-q", "-B", "other", self.first, cwd=self.seed)
        two = self.commit({"corpus/y": "y\n"})
        top = self.predict(one, two)
        self.work_ahead(top)
        self.assertEqual(self.ran(), ["fast", "slow"])

    def test_any_members_label_runs_it(self):
        one = self.pull_request({"A": "a2\n"})
        git("checkout", "-q", "-B", "other", self.first, cwd=self.seed)
        two = self.commit({"B": "b2\n"})
        top = self.predict(one, two)
        self.work_ahead(top, {two: ["Check:slow"], "f" * 40: ["Check:slow"]})
        self.assertEqual(self.ran(), ["fast", "slow"])
        self.assertEqual(self.state()["results"][top]["scoped"],
                         {"slow": "ran: label Check:slow"})

    def test_no_member_in_scope(self):
        one = self.pull_request({"A": "a2\n"})
        top = self.predict(one)
        self.work_ahead(top, {"f" * 40: ["Check:slow"]})
        self.assertEqual(self.ran(), ["fast"])


if __name__ == "__main__":
    unittest.main()
