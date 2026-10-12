"""Tests for forge-pr-check's checks.fresh (docs/designs/0010): every
green run writes a dependency record, each step is chosen from its own
freshest record, the default branch is traced when a waiting head needs
it, and a head that runs a step only because its record is stale waits
for a fresh one.

Choosing and recording use real git, and runs the real tracer, since
only real trees show which record answers for which step.
"""

import datetime
import json
import os
import pathlib
import shutil
import unittest
from unittest import mock

from agent_forge import config, trace
from tests.helpers import example
from tests.test_forge_pr_check import (
    REPO, SHA_A, SHA_B, SHA_C, T0, FakeRun, StateCase, done, git, heads_tsv,
    is_pulls, pc)
from tests.test_forge_pr_check_depends import MAIN_REF, TREE, depends_on
from tests.test_forge_pr_check_merge import RepoCase

MINUTE = datetime.timedelta(minutes=1)


def fresh_on(test, wait_minutes=15, no_base=()):
    """Turn checks.fresh on for one test (with depends already on)."""
    for name, value in (
            ("FRESH_WAIT", datetime.timedelta(minutes=wait_minutes)),
            ("NO_BASE", frozenset(no_base)), ("REUSE_AGE", None),
            ("FAST_LANE_S", None)):
        patch = mock.patch.object(pc, name, value)
        patch.start()
        test.addCleanup(patch.stop)
    pc._DIFFS.clear()


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def data(self, **checks):
        data = example()
        data["checks"].update(checks)
        return data

    def test_off_unless_configured(self):
        pc.configure(config.Project(example(), None))
        self.assertIsNone(pc.FRESH_WAIT)

    def test_on_with_its_default_wait(self):
        pc.configure(config.Project(self.data(depends=True, fresh={}), None))
        self.assertEqual(pc.FRESH_WAIT, datetime.timedelta(minutes=15))
        pc.configure(config.Project(self.data(
            depends=True, fresh={"wait_minutes": 4}), None))
        self.assertEqual(pc.FRESH_WAIT, datetime.timedelta(minutes=4))

    def test_refused_without_depends(self):
        self.assertIn("checks.fresh needs checks.depends: it keeps the "
                      "dependency records depends writes fresh",
                      config.check(self.data(fresh={})))
        self.assertEqual(config.check(self.data(depends=True, fresh={})), [])

    def test_shapes(self):
        self.assertIn("checks.fresh.wait_minutes must be at least 1",
                      config.check(self.data(depends=True,
                                             fresh={"wait_minutes": 0})))
        self.assertIn("checks.fresh: unknown key 'minutes'",
                      config.check(self.data(depends=True,
                                             fresh={"minutes": 3})))


class Prune(StateCase):

    def test_the_newest_few_of_each_step_are_kept(self):
        for i, reads in enumerate([{"a": [], "b": []}, {"a": []}, {"a": []},
                                   {"b": []}]):
            trace.write_record(self.state_dir, "%040d" % i, "c" * 40, "d",
                               pc.iso(T0 + i * MINUTE), "preload", reads, {})
        trace.prune_records(self.state_dir, T0 + 9 * MINUTE, pc.parse,
                            per_step=1)
        self.assertEqual(sorted(os.path.basename(p) for p, _ in
                                trace.load_records(self.state_dir)),
                         ["%040d.json" % 2, "%040d.json" % 3])


class Choose(StateCase):
    """`choose_fresh` against a real repository, records written by
    hand."""

    def setUp(self):
        super().setUp()
        self.steps = [("show", ["cat", "A"], None),
                      ("list", ["ls", "docs"], None),
                      ("fmt", ["true"], None)]
        depends_on(self, self.steps, always=["fmt"])
        fresh_on(self)
        self.wt = os.path.join(self._tmp.name, "wt")
        git("init", "-q", "-b", "main", self.wt, cwd=self._tmp.name)
        self.first = self.commit({"A": "a", "B": "b", "docs/x": "x"})
        git("update-ref", MAIN_REF, self.first, cwd=self.wt)

    def commit(self, files, at=None):
        if at:
            git("checkout", "-q", "--detach", at, cwd=self.wt)
        for name, text in files.items():
            path = pathlib.Path(self.wt, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            git("add", name, cwd=self.wt)
        git("commit", "-q", "-m", "c", cwd=self.wt)
        return git("rev-parse", "HEAD", cwd=self.wt)

    def tree(self, commit):
        return git("rev-parse", commit + "^{tree}", cwd=self.wt)

    def record(self, commit, steps=("show", "list"), at=T0, **extra):
        reads = {"show": ["A"], "list": ["docs"]}
        dirs = {"show": ["."], "list": ["docs"]}
        return trace.write_record(
            self.state_dir, self.tree(commit), commit, pc.steps_digest(),
            pc.iso(at), "preload", {s: reads[s] for s in steps},
            {s: dirs[s] for s in steps}, **extra)

    def move_main(self, files):
        tip = self.commit(files, at=self.first)
        git("update-ref", MAIN_REF, tip, cwd=self.wt)
        return tip

    def test_the_steps_that_read_the_change(self):
        path = self.record(self.first)
        head = self.commit({"docs/new": "n"}, at=self.first)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got, {
            "base": self.first, "record": self.first, "depends": path,
            "ran": {"list": "docs/new (in docs)", "fmt": "always"},
            "skipped": ["show"], "records": {"show": path, "list": path},
            "stale": []})

    def test_each_step_from_its_own_newest_record_that_answers(self):
        old = self.record(self.first)
        tip = self.move_main({"A": "a2"})
        # A pull request's run on the new tip recorded `show` only.
        new = self.record(tip, steps=("show",), at=T0 + MINUTE,
                          source="run")
        head = self.commit({"B": "b2"}, at=tip)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got["records"], {"show": new, "list": old})
        self.assertEqual(got["ran"], {"fmt": "always"})
        self.assertEqual(got["skipped"], ["show", "list"])
        self.assertEqual(got["stale"], [])
        self.assertEqual(got["record"], tip)
        # Without fresh, the base's one record is taken whole, and a
        # step it lacks runs.
        with mock.patch.object(pc, "FRESH_WAIT", None):
            self.assertEqual(pc.choose(self.wt, head, self.state_dir)["ran"],
                             {"list": "not in the record", "fmt": "always"})

    def test_a_step_runs_when_only_its_record_is_stale(self):
        path = self.record(self.first)
        tip = self.move_main({"docs/y": "y"})
        head = self.commit({"B": "b2"}, at=tip)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got["ran"], {
            "list": "docs/y (in docs) since its record", "fmt": "always"})
        self.assertEqual(got["stale"], ["list"])
        self.assertEqual(got["records"], {"show": path, "list": path})

    def test_a_step_its_own_change_runs_is_not_stale(self):
        self.record(self.first)
        tip = self.move_main({"docs/y": "y"})
        head = self.commit({"docs/z": "z"}, at=tip)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got["ran"]["list"], "docs/z (in docs)")
        self.assertEqual(got["stale"], [])

    def test_a_record_that_is_no_ancestor_answers_by_its_tree(self):
        # As a merge_default run's record: its commit is a local merge
        # no branch has, but its tree differs from the base only where
        # `list` reads nothing.
        side = self.commit({"B": "b-side"}, at=self.first)
        tip = self.move_main({"docs/y": "y"})
        self.record(self.first)
        merged = self.commit({"docs/y": "y"}, at=side)
        run = self.record(merged, steps=("list",), at=T0 + MINUTE,
                          source="run")
        head = self.commit({"A": "a2"}, at=tip)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got["records"]["list"], run)
        self.assertEqual(got["stale"], [])
        self.assertEqual(got["ran"], {"show": "A", "fmt": "always"})

    def test_a_newer_record_whose_tree_differs_in_a_read_is_passed_over(self):
        old = self.record(self.first)
        elsewhere = self.commit({"A": "a-elsewhere"}, at=self.first)
        self.record(elsewhere, at=T0 + MINUTE, source="run")
        head = self.commit({"B": "b2"}, at=self.first)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got["records"]["show"], old)
        self.assertEqual(got["skipped"], ["show", "list"])

    def test_a_step_no_record_has_runs_and_is_not_stale(self):
        self.record(self.first, steps=("show",))
        head = self.commit({"B": "b2"}, at=self.first)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got["ran"], {"list": "not in any record",
                                      "fmt": "always"})
        self.assertEqual(got["stale"], [])

    def test_a_record_whose_tree_is_gone_is_left_out(self):
        trace.write_record(self.state_dir, "0" * 40, "1" * 40,
                           pc.steps_digest(), pc.iso(T0 + MINUTE), "preload",
                           {"show": ["Z"]}, {})
        path = self.record(self.first)
        head = self.commit({"B": "b2"}, at=self.first)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual(got["records"], {"show": path, "list": path})

    def test_against_the_tip_for_an_estimate(self):
        self.record(self.first)
        head = self.commit({"B": "b2"}, at=self.first)
        tip = self.move_main({"docs/y": "y"})
        plain = pc.choose_fresh(self.wt, head, self.state_dir)
        self.assertEqual((plain["base"], plain["stale"]), (self.first, []))
        onto = pc.choose_fresh(self.wt, head, self.state_dir, onto=tip)
        self.assertEqual((onto["base"], onto["stale"]), (tip, ["list"]))

    def test_main_needs(self):
        main = lambda: (git("rev-parse", MAIN_REF, cwd=self.wt), "")
        self.assertIsNone(pc.main_needs(self.wt, main(), self.state_dir))
        self.record(self.first)
        self.assertEqual(pc.main_needs(self.wt, main(), self.state_dir), [])
        # Main's change touches nothing a step read: still exact.
        self.move_main({"B": "b2"})
        self.assertEqual(pc.main_needs(self.wt, main(), self.state_dir), [])
        self.move_main({"docs/y": "y"})
        self.assertEqual(pc.main_needs(self.wt, main(), self.state_dir),
                         ["list"])

    def test_compose(self):
        old = self.record(self.first)
        tip = self.move_main({"A": "a2"})
        new = self.record(tip, steps=("show",), at=T0 + MINUTE, source="run")
        head = self.commit({"B": "b2"}, at=tip)
        got = pc.choose_fresh(self.wt, head, self.state_dir)
        path, record = pc.compose(self.state_dir, got, 2, T0)
        self.assertEqual(os.path.basename(path), ".selection-2.json")
        self.assertEqual(record["from"], {"show": new, "list": old})
        with open(path) as handle:
            written = json.load(handle)
        self.assertEqual(written["reads"], {"show": ["A"], "list": ["docs"]})
        self.assertEqual(written["source"], "selection")
        # Not a record to choose from.
        self.assertEqual(len(trace.load_records(self.state_dir)), 2)


class QueueMain(StateCase):

    def setUp(self):
        super().setUp()
        depends_on(self)
        fresh_on(self)

    def test_queued_when_a_step_needs_tracing_whatever_the_file(self):
        trace.write_record(self.state_dir, TREE, SHA_C, "d", pc.iso(T0),
                           "preload", {}, {}, source="run")
        state = self.state()
        self.assertTrue(pc.queue_main(state, (SHA_A, TREE), T0,
                                      self.state_dir, needs=["list"]))
        self.assertFalse(pc.queue_main(state, (SHA_A, TREE), T0,
                                       self.state_dir, needs=["list"]))
        # No record at all: every step.
        self.assertTrue(pc.queue_main(state, (SHA_B, TREE), T0,
                                      self.state_dir, needs=None))
        state["results"][SHA_C] = {"ok": False, "main": True}
        self.assertFalse(pc.queue_main(state, (SHA_C, TREE), T0,
                                       self.state_dir, needs=["list"]))

    def test_not_when_every_record_holds_and_older_heads_go(self):
        state = self.state()
        state["waiting"] = {
            SHA_A: {"pr": 0, "main": True, "since": pc.iso(T0)},
            SHA_B: {"pr": 0, "main": True, "since": pc.iso(T0)}}
        self.assertFalse(pc.queue_main(state, (SHA_C, TREE), T0,
                                       self.state_dir, busy={SHA_B},
                                       needs=[]))
        self.assertEqual(sorted(state["waiting"]), [SHA_B])


class Held(unittest.TestCase):

    def setUp(self):
        depends_on(self)
        fresh_on(self, wait_minutes=15)
        self.state = {"results": {}, "waiting": {
            SHA_C: {"pr": 0, "main": True, "since": pc.iso(T0)},
            SHA_A: {"pr": 7, "since": pc.iso(T0), "stale_steps": ["list"],
                    "lane_base": SHA_C, "lane_steps": ["list", "show"]},
            SHA_B: {"pr": 8, "since": pc.iso(T0), "stale_steps": [],
                    "lane_base": SHA_C, "lane_steps": ["show"]}}}

    def held(self, when=T0 + MINUTE, busy=(), **kw):
        return pc.held_heads(self.state, SHA_C, busy, when, **kw)

    def test_a_head_with_a_stale_step_waits_and_says_why(self):
        self.assertEqual(self.held(), {SHA_A: "list stale; waits for main "
                                              "cccccccccc's record, at most "
                                              "15m"})

    def test_at_most_wait_minutes(self):
        self.assertEqual(self.held(T0 + 15 * MINUTE), {})

    def test_while_the_tip_is_running(self):
        del self.state["waiting"][SHA_C]
        self.assertEqual(self.held(), {})
        self.assertEqual(list(self.held(busy={SHA_C})), [SHA_A])

    def test_not_against_another_base(self):
        self.state["waiting"][SHA_A]["lane_base"] = "d" * 40
        self.assertEqual(self.held(), {})

    def test_not_the_mergers_head_nor_a_cheap_one(self):
        self.assertEqual(self.held(first_pr=7), {})
        self.assertEqual(self.held(cheap={SHA_A: 5}), {})

    def test_off_holds_nothing(self):
        with mock.patch.object(pc, "FRESH_WAIT", None):
            self.assertEqual(self.held(), {})

    def test_urgent_puts_the_default_branch_first(self):
        self.assertEqual(pc.next_head(self.state, {SHA_A}, when=T0)[1], 8)
        self.assertEqual(pc.next_head(self.state, {SHA_A}, when=T0,
                                      urgent=True), (SHA_C, 0))

    def test_but_not_before_the_mergers_head_with_nothing_stale(self):
        self.assertEqual(pc.next_head(self.state, {SHA_A}, first_pr=8,
                                      when=T0, urgent=True), (SHA_B, 8))

    def test_still_first_when_the_mergers_head_is_stale(self):
        self.assertEqual(pc.next_head(self.state, {SHA_B}, first_pr=7,
                                      when=T0, urgent=True), (SHA_C, 0))


class Tick(StateCase):
    """The tick with fresh: what the tip needs and each head's estimate
    are mocked; the queue is real."""

    def setUp(self):
        super().setUp()
        depends_on(self)
        fresh_on(self)
        self.needs = ["list"]
        self.estimates = {SHA_A: {"lane_steps": ["list", "show"],
                                  "stale_steps": ["list"],
                                  "lane_base": SHA_C}}

    def estimate(self, state_dir, checkout, busy, run, tip):
        self.assertEqual(tip, SHA_C)
        with pc.state_lock(state_dir):
            state = pc.load(state_dir)
            for sha, got in self.estimates.items():
                if sha in state["waiting"]:
                    state["waiting"][sha].update(got)
            pc.save(state_dir, state)

    def tick(self, heads=(), when=T0, slots=2, running=None,
             merger_dir=None):
        run = FakeRun((is_pulls, done([], 0, heads_tsv(*heads))),
                      (lambda a: "rev-parse" in a,
                       done([], 0, SHA_C + "\n" + TREE + "\n")))
        self.spawned = []
        with mock.patch.object(pc, "main_needs",
                               lambda *a: self.needs), \
                mock.patch.object(pc, "estimate", self.estimate), \
                mock.patch.object(pc, "running", lambda d, slot=1: (
                    running if slot == 1 else None)):
            return pc.tick(self.state_dir, "tok", run,
                           lambda *a, **k: self.spawned.append((a, k)),
                           self.lines.append, when=when, slots=slots,
                           checkout="/repo", merger_dir=merger_dir)

    def test_the_tip_goes_first_and_the_stale_head_waits(self):
        self.assertEqual(self.tick([(7, SHA_A, REPO, "false")]), 0)
        self.assertEqual([a[1:3] for a, _ in self.spawned], [(SHA_C, 0)])
        self.assertEqual(self.lines, [
            "QUEUED main cccccccccc: a traced run of every step; "
            "stale: list",
            "HELD #7 aaaaaaaaaa: list stale; waits for main cccccccccc's "
            "record, at most 15m",
            "STARTED main cccccccccc, every step traced; 1 waiting"])
        self.assertEqual(self.state()["waiting"][SHA_A]["held"],
                         "list stale; waits for main cccccccccc's record, "
                         "at most 15m")

    def test_the_tip_goes_before_the_mergers_predictions_too(self):
        # A held head waits on the tip's record; a prediction run first
        # would only make it wait out wait_minutes for nothing.
        prediction = {"pr": 2, "sha": "d" * 40, "tree": "e" * 40}
        with mock.patch.object(pc, "TEST_AHEAD", True), \
                mock.patch.object(pc, "merger_first", lambda *a: None), \
                mock.patch.object(pc, "merger_ahead",
                                  lambda *a: [prediction]):
            self.tick([(7, SHA_A, REPO, "false")], merger_dir="/merger")
        self.assertEqual([a[1:3] for a, _ in self.spawned], [(SHA_C, 0)])

    def test_a_head_that_needs_no_fresh_record_runs_beside_it(self):
        self.estimates[SHA_A]["stale_steps"] = []
        self.tick([(7, SHA_A, REPO, "false")])
        # Pull requests first, as without fresh.
        self.assertEqual([a[1:3] for a, _ in self.spawned], [(SHA_A, 7)])

    def test_after_wait_minutes_it_runs_as_now(self):
        self.tick([(7, SHA_A, REPO, "false")])
        # The tip's traced run is in slot 1.
        holder = "#0 %s %s pid=5 main" % (SHA_C, pc.iso(T0))
        self.tick([(7, SHA_A, REPO, "false")], when=T0 + 14 * MINUTE,
                  running=holder)
        self.assertEqual(self.spawned, [])
        self.assertIn("held", self.state()["waiting"][SHA_A])
        self.tick([(7, SHA_A, REPO, "false")], when=T0 + 15 * MINUTE,
                  running=holder)
        self.assertEqual([a[1:3] for a, _ in self.spawned], [(SHA_A, 7)])
        self.assertNotIn("held", self.state()["waiting"][SHA_A])

    def test_a_tip_whose_records_hold_is_not_queued(self):
        self.needs = []
        self.estimates[SHA_A]["stale_steps"] = []
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertNotIn(SHA_C, self.state()["waiting"])
        self.assertEqual([a[1:3] for a, _ in self.spawned], [(SHA_A, 7)])

    def test_off_nothing_is_held(self):
        with mock.patch.object(pc, "FRESH_WAIT", None):
            self.tick([(7, SHA_A, REPO, "false")])
        self.assertNotIn("held", self.state()["waiting"][SHA_A])
        self.assertEqual(self.lines[0],
                         "QUEUED main cccccccccc: a traced run of every step")


class Estimate(RepoCase):
    """A held head is chosen again once a fresh record is written, and
    goes to the fast lane when its steps are then cheap."""

    def setUp(self):
        super().setUp()
        self.steps = [("show", ["cat", "A"], None),
                      ("list", ["ls", "docs"], None)]
        depends_on(self, self.steps)
        fresh_on(self, no_base=("show", "list"))
        mock.patch.object(pc, "MERGE_DEFAULT", True).start()
        self.addCleanup(mock.patch.stopall)

    def record(self, commit, at):
        tree = git("rev-parse", commit + "^{tree}", cwd=self.repo)
        trace.write_record(self.state_dir, tree, commit, pc.steps_digest(),
                           pc.iso(at), "preload",
                           {"show": ["A"], "list": ["docs"]},
                           {"show": ["."], "list": ["docs"]})

    def test_estimated_again_when_a_record_arrives(self):
        self.record(self.first, T0)
        head = self.pull_request({"B": "b2\n"})
        tip = self.move_main({"docs/y": "y\n"})
        self.write_state({"results": {}, "waiting": {head: {
            "pr": 7, "since": pc.iso(T0)}}})
        pc.estimate(self.state_dir, self.repo, (), pc.sh, tip)
        entry = self.state()["waiting"][head]
        self.assertEqual((entry["lane_steps"], entry["stale_steps"],
                          entry["lane_base"]), (["list"], ["list"], tip))
        # The tip's traced run records; the head is chosen again.
        self.record(tip, T0 + MINUTE)
        pc.estimate(self.state_dir, self.repo, (), pc.sh, tip)
        entry = self.state()["waiting"][head]
        self.assertEqual((entry["lane_steps"], entry["stale_steps"]),
                         ([], []))
        with mock.patch.object(pc, "FAST_LANE_S", 60):
            self.assertEqual(pc.cheap_heads(self.state(), {}), {head: 0})


@unittest.skipUnless(shutil.which("cc"), "no C compiler")
class Records(RepoCase):
    """Real runs, traced: the default branch's, then a pull request's."""

    def setUp(self):
        super().setUp()
        root = self._tmp.name
        self.ran = os.path.join(root, "ran")
        self.steps = [
            ("show", ["sh", "-c", "cat A >/dev/null; echo show >> %s"
                      % self.ran], None),
            ("list", ["sh", "-c", "ls docs >/dev/null; echo list >> %s"
                      % self.ran], None),
            ("base", ["sh", "-c", "cat B >/dev/null; echo base >> %s"
                      % self.ran], None)]
        depends_on(self, self.steps)
        fresh_on(self, no_base=("show", "list"))
        mock.patch.object(pc, "MERGE_DEFAULT", True).start()
        self.addCleanup(mock.patch.stopall)
        self.first = self.move_main({"docs/x": "x\n"})

    def run_main(self, sha):
        self.write_state(dict(self.state(), waiting={sha: {
            "pr": 0, "main": True, "since": pc.iso(T0)}}))
        clock = iter([T0, T0 + MINUTE])
        return pc.worker(self.repo, self.state_dir, sha, 0, pc.sh,
                         self.lines.append, lambda: next(clock),
                         lambda *a: True, main=True)

    def steps_ran(self):
        try:
            text = pathlib.Path(self.ran).read_text().split()
        except OSError:
            return []
        os.unlink(self.ran)
        return text

    def record_of(self, commit):
        tree = git("rev-parse", commit + "^{tree}", cwd=self.repo)
        with open(os.path.join(trace.records_dir(self.state_dir),
                               tree + ".json")) as handle:
            return json.load(handle)

    def test_a_green_run_records_ran_steps_and_carries_skipped_ones(self):
        self.assertEqual(self.run_main(self.first), 0)
        self.steps_ran()
        (main_path, _), = trace.load_records(self.state_dir)
        head = self.pull_request({"docs/new": "n\n"}, base=self.first)
        # Main changes what only `base` reads.
        tip = self.move_main({"B": "b3\n"})
        self.assertEqual(self.work(head, at=T0 + MINUTE), 0)
        self.assertEqual(self.steps_ran(), ["list", "base"])
        merged = self.state()["results"][head]["merged"]
        record = self.record_of(merged)
        self.assertEqual(record["source"], "run")
        self.assertEqual(record["commit"], merged)
        self.assertEqual(sorted(record["reads"]), ["list", "show"])
        self.assertIn("docs", record["dirs"]["list"])
        self.assertEqual(record["reads"]["show"], self.record_of(
            self.first)["reads"]["show"])
        self.assertEqual(record["from"], {"list": "run", "show": main_path})
        # `base` is given CHECK_BASE, so a narrowed run says too little.
        self.assertNotIn("base", record["reads"])
        selection = self.state()["results"][head]["selection"]
        self.assertEqual(selection["stale"], ["base"])
        self.assertEqual(selection["base"], tip)
        # The chosen records, as one, for the steps.
        self.assertTrue(os.path.exists(os.path.join(
            trace.records_dir(self.state_dir), ".selection-1.json")))
        self.assertEqual([n for n in os.listdir(trace.records_dir(
            self.state_dir)) if n.startswith(".trace")], [])

    def test_the_next_head_on_that_tree_is_chosen_from_it(self):
        self.run_main(self.first)
        head = self.pull_request({"A": "a2\n"}, base=self.first)
        tip = self.move_main({"docs/y": "y\n"})
        self.work(head, at=T0 + MINUTE)
        # `list` was stale (main added docs/y) and ran: now it's fresh.
        selection = self.state()["results"][head]["selection"]
        self.assertEqual(selection["stale"], ["list"])
        self.assertIn("list", self.record_of(
            self.state()["results"][head]["merged"])["reads"])
        self.steps_ran()
        # The default branch merges the head: its tree is the record's.
        git("checkout", "-q", "main", cwd=self.seed)
        git("merge", "-q", "--no-edit", "feature", cwd=self.seed)
        git("push", "-q", self.origin, "main", cwd=self.seed)
        git("fetch", "-q", "origin", cwd=self.repo)
        tip = git("rev-parse", MAIN_REF, cwd=self.repo)
        self.assertEqual(pc.main_needs(self.repo, (tip, ""), self.state_dir),
                         [])
        later = self.pull_request({"B": "b2\n"}, base=tip)
        self.assertEqual(self.work(later, at=T0 + 2 * MINUTE), 0)
        selection = self.state()["results"][later]["selection"]
        self.assertEqual(selection["stale"], [])
        self.assertEqual(selection["skipped"], ["show", "list"])
        self.assertEqual(self.steps_ran(), ["base"])

    def test_a_red_run_records_nothing(self):
        self.run_main(self.first)
        broken = self.steps + [("broken", ["sh", "-c", "cat A; false"],
                                None)]
        with mock.patch.object(pc, "STEPS", broken), \
                mock.patch.object(pc, "NO_BASE",
                                  frozenset({"show", "list", "broken"})):
            self.run_main(self.first)
            before = trace.load_records(self.state_dir)
            head = self.pull_request({"docs/new": "n\n"}, base=self.first)
            self.assertEqual(self.work(head, at=T0 + MINUTE), 1)
        self.assertEqual(trace.load_records(self.state_dir), before)
        self.assertEqual([n for n in os.listdir(trace.records_dir(
            self.state_dir)) if n.startswith(".trace")], [])

    def test_the_default_branchs_own_record_is_never_replaced(self):
        self.run_main(self.first)
        path, before = trace.load_records(self.state_dir)[0]
        # A head that is the tip itself checks the tip's tree.
        git("push", "-q", "-f", self.origin, "%s:refs/pull/7/head"
            % self.first, cwd=self.seed)
        self.assertEqual(self.work(self.first, at=T0 + MINUTE), 0)
        with open(path) as handle:
            self.assertEqual(json.load(handle), before)

    def test_off_a_pull_requests_run_records_nothing(self):
        self.run_main(self.first)
        before = trace.load_records(self.state_dir)
        head = self.pull_request({"docs/new": "n\n"}, base=self.first)
        with mock.patch.object(pc, "FRESH_WAIT", None):
            self.assertEqual(self.work(head, at=T0 + MINUTE), 0)
        self.assertEqual(trace.load_records(self.state_dir), before)
        selection = self.state()["results"][head]["selection"]
        self.assertNotIn("records", selection)
        self.assertNotIn("stale", selection)


if __name__ == "__main__":
    unittest.main()
