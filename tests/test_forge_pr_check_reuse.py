"""Tests for forge-pr-check's checks.reuse (docs/designs/0009): a step
isn't run on inputs a green run here already passed.

The digest needs real trees, so most of these use real git, with a
dependency record written by hand: what each step read is then known
exactly, rather than found by the tracer.
"""

import datetime
import json
import os
import pathlib
import socket
import unittest
from unittest import mock

from agent_forge import config, trace
from tests.helpers import example
from tests.test_forge_pr_check import T0, git, pc
from tests.test_forge_pr_check_merge import RepoCase

DAY = datetime.timedelta(days=1)


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def data(self, **checks):
        data = example()
        data["checks"].update(checks)
        return data

    def test_off_unless_configured(self):
        pc.configure(config.Project(example(), None))
        self.assertIsNone(pc.REUSE_AGE)
        self.assertEqual(pc.VERSIONS, {})

    def test_on_with_its_default_age_and_versions(self):
        data = self.data(depends=True, reuse={})
        data["checks"]["steps"][2]["version"] = ["rustc", "-vV"]
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.REUSE_AGE, datetime.timedelta(days=7))
        name = data["checks"]["steps"][2]["name"]
        self.assertEqual(pc.VERSIONS, {name: ["rustc", "-vV"]})

    def test_refused_without_depends(self):
        self.assertIn("checks.reuse needs checks.depends: a step's inputs "
                      "are what its dependency record says it read",
                      config.check(self.data(reuse={})))

    def test_shapes(self):
        problems = config.check(self.data(depends=True,
                                          reuse={"max_age_days": 0}))
        self.assertIn("checks.reuse.max_age_days must be at least 1",
                      problems)
        data = self.data(depends=True)
        data["checks"]["steps"][0]["version"] = "rustc -vV"
        self.assertIn("checks.steps[0].version must be a non-empty list of "
                      "strings", config.check(data))


def verdict(sha, digest, ok=True, ran=True, finished=T0, host=None,
            pr=7, reused=None, failed=None):
    """A verdict for step "s" with input digest `digest`."""
    r = {"pr": pr, "ok": ok, "finished": pc.iso(finished),
         "failed": failed if failed is not None else ([] if ok else ["s"]),
         "digests": {"s": digest}, "host": host or socket.gethostname(),
         "steps": pc.steps_digest(), "step_seconds": {"s": 5} if ran else {}}
    if reused:
        r["reused"] = {"s": reused}
        r["step_seconds"] = {}
    return sha, r


class Reusable(unittest.TestCase):
    """Which earlier verdict, if any, a step's digest is reused from."""

    def setUp(self):
        patch = mock.patch.object(pc, "REUSE_AGE", 7 * DAY)
        patch.start()
        self.addCleanup(patch.stop)

    def find(self, *verdicts, when=T0 + DAY):
        return pc.reusable({"results": dict(verdicts)}, "s", "d", when)

    def test_a_green_run_with_the_same_digest(self):
        self.assertEqual(self.find(verdict("a" * 40, "d")),
                         {"reused": "a" * 40, "pr": 7,
                          "finished": pc.iso(T0)})

    def test_another_digest_is_nothing(self):
        self.assertIsNone(self.find(verdict("a" * 40, "e")))

    def test_red_an_unclean_exit_and_a_timeout_are_never_reused(self):
        # A timeout is exit 124, and any non-zero exit is a failed step.
        self.assertIsNone(self.find(verdict("a" * 40, "d", ok=False)))

    def test_the_newest_decides_so_red_since_hides_an_older_green(self):
        self.assertIsNone(self.find(
            verdict("a" * 40, "d"),
            verdict("b" * 40, "d", ok=False, finished=T0 + DAY / 2)))

    def test_a_verdict_that_skipped_the_step_says_nothing(self):
        self.assertEqual(self.find(
            verdict("a" * 40, "d"),
            verdict("b" * 40, "d", ran=False, finished=T0 + DAY / 2)
        )["reused"], "a" * 40)

    def test_too_old(self):
        self.assertIsNone(self.find(verdict("a" * 40, "d"),
                                    when=T0 + 8 * DAY))

    def test_another_host_or_step_list(self):
        self.assertIsNone(self.find(verdict("a" * 40, "d", host="other")))
        sha, r = verdict("a" * 40, "d")
        r["steps"] = "0" * 16
        self.assertIsNone(self.find((sha, r)))

    def test_a_reuse_points_at_the_run_and_ages_from_it(self):
        origin = {"reused": "a" * 40, "pr": 3, "finished": pc.iso(T0)}
        later = verdict("b" * 40, "d", finished=T0 + 6 * DAY, reused=origin)
        self.assertEqual(self.find(later, when=T0 + 6 * DAY), origin)
        self.assertIsNone(self.find(later, when=T0 + 8 * DAY))


class Text(unittest.TestCase):

    def test_the_description_names_the_first_reuse(self):
        r = {"ok": True, "failed": [], "seconds": 3,
             "selection": {"ran": {"test": "x", "doc": "y", "vet": "z"},
                           "skipped": ["fmt"]},
             "reused": {"test": {"reused": "3193656" + "0" * 33, "pr": 458},
                        "vet": {"reused": "1" * 40, "pr": 0}}}
        self.assertEqual(pc.final_status(r), (
            "success", "green on build.example.org: test as #458 at 3193656, "
                       "same inputs (and 1 more); 1 of 4 steps ran"))
        self.assertEqual(pc.reuse_text("vet", r["reused"]["vet"]),
                         "vet as main at 1111111")


class DigestCase(RepoCase):
    """A real repository and hand-written dependency records."""

    def setUp(self):
        super().setUp()
        self.log = os.path.join(self._tmp.name, "ran")
        # Each step notes that it ran; "c" also reads what "b" wrote.
        self.steps = [
            ("a", ["sh", "-c", "cat A >/dev/null; echo a >> %s" % self.log],
             None),
            ("b", ["sh", "-c", "mkdir -p gen; cat B > gen/out; echo b >> %s"
                   % self.log], None),
            ("c", ["sh", "-c", "cat gen/out >/dev/null; echo c >> %s"
                   % self.log], None),
        ]
        self.reads = {"a": ["A"], "b": ["B"], "c": ["gen/out", "lib"]}
        self.dirs = {"a": [], "b": [], "c": ["lib"]}
        for name, value in (
                ("STEPS", self.steps), ("DEPENDS", True),
                ("ALWAYS", frozenset()), ("REUSE_AGE", 7 * DAY),
                ("VERSIONS", {}), ("STEP_ENTRIES", {}),
                ("MERGE_DEFAULT", False)):
            patch = mock.patch.object(pc, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.wt = os.path.join(self._tmp.name, "probe")

    def write_record(self, commit=None):
        commit = commit or self.first
        tree = git("rev-parse", commit + "^{tree}", cwd=self.seed)
        trace.write_record(self.state_dir, tree, commit, pc.steps_digest(),
                           pc.iso(T0), "preload", self.reads, self.dirs)

    def record(self):
        return trace.load_records(self.state_dir)[0][1]

    def digests(self, commit, env=None, names=None):
        if not os.path.exists(self.wt):
            git("clone", "-q", self.seed, self.wt, cwd=self._tmp.name)
        git("fetch", "-q", self.seed, "+refs/heads/*:refs/remotes/s/*",
            cwd=self.wt)
        git("checkout", "-q", "--detach", "--force", commit, cwd=self.wt)
        return pc.step_digests(self.wt, commit, self.record(), env or {},
                               names=names or ["a", "b", "c"])

    def pr_head(self, number, files, base=None):
        git("checkout", "-q", "-B", "pr%d" % number, base or self.first,
            cwd=self.seed)
        head = self.commit(files)
        git("push", "-q", "-f", self.origin,
            "pr%d:refs/pull/%d/head" % (number, number), cwd=self.seed)
        return head

    def ran(self):
        """The steps that ran since last asked."""
        if not os.path.exists(self.log):
            return []
        steps = pathlib.Path(self.log).read_text().split()
        os.unlink(self.log)
        return steps


class Digests(DigestCase):

    def setUp(self):
        super().setUp()
        self.write_record()
        self.base = self.digests(self.first)

    def changed(self, files, **kw):
        return self.digests(self.commit(files), **kw)

    def test_every_step_with_a_read_set_has_one(self):
        self.assertEqual(sorted(self.base), ["a", "b", "c"])

    def test_a_read_path_changing_changes_only_its_steps(self):
        got = self.changed({"A": "a2\n"})
        self.assertNotEqual(got["a"], self.base["a"])
        self.assertEqual(got["b"], self.base["b"])

    def test_a_path_added_to_a_listed_directory(self):
        got = self.changed({"lib/new": "x\n"})
        self.assertNotEqual(got["c"], self.base["c"])
        self.assertEqual(got["a"], self.base["a"])

    def test_a_path_looked_for_appearing(self):
        self.reads["a"] = ["A", "maybe"]
        self.write_record()
        before = self.digests(self.first)
        self.assertNotEqual(self.changed({"maybe": "now\n"})["a"],
                            before["a"])

    def test_a_step_reading_what_an_earlier_step_wrote(self):
        # gen/out isn't in the tree: "c" counts "a"'s and "b"'s digests.
        got = self.changed({"B": "b2\n"})
        self.assertNotEqual(got["c"], self.base["c"])
        self.assertEqual(got["a"], self.base["a"])

    def test_an_earlier_step_without_a_digest_leaves_none(self):
        del self.reads["b"]
        self.write_record()
        got = self.digests(self.first)
        self.assertEqual(sorted(got), ["a"])

    def test_check_base_counts(self):
        tip = self.commit({"Z": "z\n"})
        got = self.digests(self.first, env={"CHECK_BASE": tip})
        self.assertNotEqual(got["a"], self.base["a"])

    def test_a_step_that_never_reads_the_base_leaves_it_out(self):
        tip = self.commit({"Z": "z\n"})
        with mock.patch.object(pc, "NO_BASE", frozenset({"a"})):
            got = self.digests(self.first, env={"CHECK_BASE": tip})
            self.assertEqual(got["a"], self.digests(self.first)["a"])

    def test_the_configuration_entry_counts(self):
        with mock.patch.object(pc, "STEP_ENTRIES", {"a": '{"name": "a"}'}):
            self.assertNotEqual(self.digests(self.first)["a"],
                                self.base["a"])

    def test_the_version_commands_output_counts_and_failing_runs_it(self):
        with mock.patch.object(pc, "VERSIONS",
                               {"a": ["echo", "tool 1.0"]}):
            one = self.digests(self.first)
        with mock.patch.object(pc, "VERSIONS",
                               {"a": ["echo", "tool 1.1"]}):
            two = self.digests(self.first)
        self.assertNotEqual(one["a"], two["a"])
        self.assertNotEqual(one["a"], self.base["a"])
        with mock.patch.object(pc, "VERSIONS", {"a": ["false"]}):
            self.assertNotIn("a", self.digests(self.first))


class Worker(DigestCase):
    """Reuse through the worker: two pull requests with the same change."""

    def setUp(self):
        super().setUp()
        self.write_record()

    def work_pr(self, sha, pr, at=T0):
        self.write_state(dict(self.state(), waiting={sha: {
            "pr": pr, "since": pc.iso(at)}}))
        clock = iter([at, at + datetime.timedelta(minutes=1)])
        return pc.worker(self.repo, self.state_dir, sha, pr, pc.sh,
                         self.lines.append, lambda: next(clock),
                         lambda *a: self.posts.append(a) or True)

    def test_the_same_change_again_reuses_its_steps(self):
        one = self.pr_head(7, {"A": "a2\n"})
        self.assertEqual(self.work_pr(one, 7), 0)
        self.assertEqual(self.ran(), ["a"])
        first = self.state()["results"][one]
        self.assertIn("a", first["digests"])
        two = self.pr_head(8, {"A": "a2\n", "Z": "z\n"})
        self.assertEqual(self.work_pr(two, 8, at=T0 + DAY), 0)
        self.assertEqual(self.ran(), [])
        record = self.state()["results"][two]
        self.assertEqual(record["reused"], {"a": {
            "reused": one, "pr": 7, "finished": first["finished"]}})
        self.assertNotIn("a", record["step_seconds"])
        # Where the run's own time went, by phase (agent-forge #13).
        for phase in ("lock", "prepare", "choose", "steps", "verdict",
                      "post", "between"):
            self.assertIn(phase, first["overhead"])
        # Each step's time unrounded too, beside the whole seconds.
        self.assertEqual(set(first["step_wall"]), {"a"})
        self.assertIsInstance(first["step_wall"]["a"], float)
        self.assertLessEqual(first["step_seconds"]["a"],
                             first["step_wall"]["a"])
        self.assertTrue(any(line.startswith("  overhead: lock ")
                            for line in self.lines))
        self.assertEqual(self.posts[-1], (
            two, "success", "green on build.example.org: a as #7 at %s, "
                            "same inputs; 0 of 3 steps ran" % one[:7]))
        self.assertIn("  steps: a reused (a as #7 at %s); b skipped; "
                      "c skipped" % one[:7], self.lines)

    def test_a_red_step_runs_again(self):
        broken = [("a", ["sh", "-c", "echo a >> %s; false" % self.log],
                   None)] + self.steps[1:]
        with mock.patch.object(pc, "STEPS", broken):
            self.write_record()
            one = self.pr_head(7, {"A": "a2\n"})
            self.assertEqual(self.work_pr(one, 7), 1)
            two = self.pr_head(8, {"A": "a2\n", "Z": "z\n"})
            self.assertEqual(self.work_pr(two, 8, at=T0 + DAY), 1)
        self.assertEqual(self.ran(), ["a", "a"])

    def test_an_always_step_runs_again(self):
        with mock.patch.object(pc, "ALWAYS", frozenset({"a"})):
            self.write_record()
            one = self.pr_head(7, {"A": "a2\n"})
            self.work_pr(one, 7)
            two = self.pr_head(8, {"A": "a2\n", "Z": "z\n"})
            self.work_pr(two, 8, at=T0 + DAY)
        self.assertEqual(self.ran().count("a"), 2)

    def test_off_nothing_is_digested_or_reused(self):
        with mock.patch.object(pc, "REUSE_AGE", None):
            one = self.pr_head(7, {"A": "a2\n"})
            self.work_pr(one, 7)
            two = self.pr_head(8, {"A": "a2\n", "Z": "z\n"})
            self.work_pr(two, 8, at=T0 + DAY)
        self.assertEqual(self.ran(), ["a", "a"])
        record = self.state()["results"][two]
        self.assertNotIn("digests", record)
        self.assertNotIn("host", record)
        self.assertFalse(any(l.startswith("  steps:") for l in self.lines))

    def test_the_merged_tree_is_digested_with_merge_default(self):
        head = self.pr_head(7, {"B": "b from the head\n"})
        tip = self.move_main({"A": "a from main\n"})
        with mock.patch.object(pc, "MERGE_DEFAULT", True):
            self.work_pr(head, 7)
        record = self.state()["results"][head]
        self.assertIn("merged", record)
        at_merge = pc.step_digests(
            self.repo, record["merged"], self.record(),
            {"CHECK_BASE": tip}, names=["a", "b", "c"])
        self.assertEqual(record["digests"]["a"], at_merge["a"])
        at_head = pc.step_digests(self.repo, head, self.record(),
                                  {"CHECK_BASE": tip}, names=["a", "b", "c"])
        self.assertNotEqual(record["digests"]["a"], at_head["a"])

    def test_the_default_branch_never_reuses(self):
        one = self.pr_head(7, {"A": "a2\n"})
        self.work_pr(one, 7)
        self.ran()
        git("checkout", "-q", "main", cwd=self.seed)
        git("merge", "-q", "--ff-only", one, cwd=self.seed)
        git("push", "-q", self.origin, "main", cwd=self.seed)
        git("fetch", "-q", "origin", cwd=self.repo)
        self.write_state(dict(self.state(), waiting={one: {
            "pr": 0, "main": True, "since": pc.iso(T0 + DAY)}}))
        clock = iter([T0 + DAY, T0 + DAY + datetime.timedelta(minutes=1)])
        with mock.patch.object(pc, "trace_wrapper",
                               lambda *a: None), \
                mock.patch.object(pc, "tracer_for", lambda *a: None):
            pc.worker(self.repo, self.state_dir, one, 0, pc.sh,
                      self.lines.append, lambda: next(clock), None,
                      main=True)
        self.assertEqual(self.ran(), ["a", "b", "c"])
        self.assertNotIn("reused", self.state()["results"][one])


if __name__ == "__main__":
    unittest.main()


class Overhead(unittest.TestCase):
    """agent-forge #13: where a run's time goes outside its steps."""

    def test_each_mark_charges_the_time_since_the_last(self):
        ticks = iter([10.0, 10.5, 12.0, 12.25, 13.0])
        phases = pc.Phases(lambda: next(ticks))
        for name in ("lock", "prepare", "lock", "steps"):
            phases.mark(name)
        self.assertEqual(phases.spent, {"lock": 0.75, "prepare": 1.5,
                                        "steps": 0.75})
        self.assertEqual(phases.text(), "lock 0.8s, prepare 1.5s, steps 0.8s")
