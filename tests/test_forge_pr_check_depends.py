"""Tests for forge-pr-check's checks.depends (docs/designs/0005): the
default branch's heads run in full under the tracer and leave a record
of what each step read, and a pull request's head runs the steps that
read what it changes.

`RealRuns` uses real git and the real tracer library, since only a real
run shows that a record says what the steps read and that a pull
request is then given the right steps.
"""

import datetime
import json
import os
import pathlib
import shutil
import tempfile
import unittest
from unittest import mock

from agent_forge import config, trace
from tests.helpers import example
from tests.test_forge_pr_check import (
    REPO, SHA_A, SHA_B, SHA_C, T0, FakeRun, StateCase, done, git, heads_tsv,
    is_pulls, pc)

TREE = "e" * 40
MAIN_REF = "refs/remotes/origin/main"


def depends_on(test, steps=None, always=(), wait_minutes=30,
               tracer="preload"):
    """Turn checks.depends on for one test, with these steps."""
    for name, value in (
            ("DEPENDS", True), ("ALWAYS", frozenset(always)),
            ("DEPENDS_WAIT", datetime.timedelta(minutes=wait_minutes)),
            ("TRACER", tracer), ("TEST_AHEAD", False), ("CACHES", {}),
            ("STEPS", steps if steps is not None else pc.STEPS)):
        patch = mock.patch.object(pc, name, value)
        patch.start()
        test.addCleanup(patch.stop)


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_off_unless_configured(self):
        pc.configure(config.Project(example(), None))
        self.assertFalse(pc.DEPENDS)
        self.assertEqual(pc.ALWAYS, frozenset())

    def test_the_keys(self):
        data = example()
        data["checks"].update(depends=True, depends_minutes=5,
                              tracer="strace")
        data["checks"]["steps"][1]["always"] = True
        pc.configure(config.Project(data, None))
        self.assertTrue(pc.DEPENDS)
        self.assertEqual(pc.DEPENDS_WAIT, datetime.timedelta(minutes=5))
        self.assertEqual(pc.TRACER, "strace")
        self.assertEqual(pc.ALWAYS,
                         frozenset([data["checks"]["steps"][1]["name"]]))

    def test_always_moves_the_digest_and_nothing_else_does(self):
        steps = [("a", ["make"], None)]
        plain = pc.steps_digest(steps, always=frozenset())
        # A list without `always` digests as before this key existed.
        self.assertEqual(plain, pc.hashlib.sha256(
            json.dumps([["a", ["make"], None]]).encode()).hexdigest()[:16])
        self.assertNotEqual(pc.steps_digest(steps, always=frozenset("a")),
                            plain)


class Ordering(unittest.TestCase):

    def setUp(self):
        depends_on(self)

    def state(self, main_age, pr_age=60):
        return {"waiting": {
            SHA_A: {"pr": 7, "since": pc.iso(
                T0 - datetime.timedelta(minutes=pr_age))},
            SHA_B: {"pr": 0, "main": True, "since": pc.iso(
                T0 - datetime.timedelta(minutes=main_age))}}}

    def test_the_default_branch_waits_behind_pull_requests(self):
        self.assertEqual(pc.next_head(self.state(10), when=T0), (SHA_A, 7))

    def test_until_it_has_waited_long_enough(self):
        self.assertEqual(pc.next_head(self.state(30), when=T0), (SHA_B, 0))

    def test_even_before_the_mergers_pull_request(self):
        self.assertEqual(pc.next_head(self.state(45), first_pr=7, when=T0),
                         (SHA_B, 0))

    def test_alone_it_goes_at_once(self):
        state = self.state(1)
        del state["waiting"][SHA_A]
        self.assertEqual(pc.next_head(state, when=T0), (SHA_B, 0))

    def test_only_the_newest_goes_first(self):
        state = self.state(45)
        state["waiting"][SHA_C] = {"pr": 0, "main": True,
                                   "since": pc.iso(T0)}
        self.assertEqual(pc.next_head(state, when=T0), (SHA_A, 7))


class QueueMain(StateCase):

    def setUp(self):
        super().setUp()
        depends_on(self)

    def test_queued_once(self):
        state = self.state()
        self.assertTrue(pc.queue_main(state, (SHA_A, TREE), T0,
                                      self.state_dir))
        self.assertEqual(state["waiting"][SHA_A],
                         {"pr": 0, "since": pc.iso(T0), "main": True})
        self.assertFalse(pc.queue_main(state, (SHA_A, TREE), T0,
                                       self.state_dir))

    def test_not_when_its_tree_has_a_record_or_it_has_a_verdict(self):
        trace.write_record(self.state_dir, TREE, SHA_C, "d", pc.iso(T0),
                           "preload", {}, {})
        state = self.state()
        self.assertFalse(pc.queue_main(state, (SHA_A, TREE), T0,
                                       self.state_dir))
        state["results"][SHA_B] = {"ok": True}
        self.assertFalse(pc.queue_main(state, (SHA_B, "f" * 40), T0,
                                       self.state_dir))

    def test_a_newer_head_replaces_one_no_slot_runs(self):
        state = self.state()
        state["waiting"] = {
            SHA_A: {"pr": 0, "main": True, "since": pc.iso(T0)},
            SHA_B: {"pr": 0, "main": True, "since": pc.iso(T0)},
            "d" * 40: {"pr": 7, "since": pc.iso(T0)}}
        pc.queue_main(state, (SHA_C, TREE), T0, self.state_dir,
                      busy={SHA_B})
        self.assertEqual(sorted(state["waiting"]), [SHA_B, SHA_C, "d" * 40])

    def test_note_heads_keeps_it(self):
        state = self.state()
        state["waiting"][SHA_A] = {"pr": 0, "main": True,
                                   "since": pc.iso(T0)}
        pc.note_heads(state, [], T0, self.state_dir)
        self.assertIn(SHA_A, state["waiting"])

    def test_main_head(self):
        run = FakeRun((lambda a: "rev-parse" in a,
                       done([], 0, SHA_A + "\n" + TREE + "\n")))
        self.assertEqual(pc.main_head("/repo", run), (SHA_A, TREE))
        self.assertEqual(run.calls[0]["args"],
                         ["git", "-C", "/repo", "rev-parse", MAIN_REF,
                          MAIN_REF + "^{tree}"])
        self.assertIsNone(pc.main_head("/repo", FakeRun(
            (lambda a: True, done([], 128, "", "no")))))


class Tick(StateCase):

    def setUp(self):
        super().setUp()
        depends_on(self)

    def tick(self, heads=()):
        run = FakeRun((is_pulls, done([], 0, heads_tsv(*heads))),
                      (lambda a: "rev-parse" in a,
                       done([], 0, SHA_C + "\n" + TREE + "\n")))
        self.spawned = []
        return pc.tick(self.state_dir, "tok", run,
                       lambda *a, **k: self.spawned.append((a, k)),
                       self.lines.append, when=T0, slots=1,
                       checkout="/repo")

    def test_a_new_default_branch_head_is_queued_and_started(self):
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.spawned, [((self.state_dir, SHA_C, 0, False, 1,
                                          "/repo"), {"main": True})])
        self.assertEqual(self.lines, [
            "QUEUED main cccccccccc: a traced run of every step",
            "STARTED main cccccccccc, every step traced; 0 waiting"])

    def test_pull_requests_go_first(self):
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(self.spawned[0][0][1:3], (SHA_A, 7))

    def test_off_queues_nothing(self):
        with mock.patch.object(pc, "DEPENDS", False):
            self.tick()
        self.assertEqual(self.spawned, [])

    def test_a_default_branch_run_is_never_superseded(self):
        self.assertEqual(pc.holder_fields("#0 %s %s pid=5 main"
                                          % (SHA_C, pc.iso(T0)))[4], True)


class Status(unittest.TestCase):

    def test_what_ran_and_why(self):
        r = {"ok": True, "seconds": 9, "failed": [], "selection": {
            "ran": {"docs": "designs/0042.md", "rust": "designs/0042.md"},
            "skipped": ["web", "fmt", "vet", "desktop"]}}
        self.assertEqual(pc.final_status(r), (
            "success", "green on build.example.org: 2 of 6 steps (docs: "
            "designs/0042.md; rust: designs/0042.md)"))

    def test_nothing_ran(self):
        r = {"ok": True, "seconds": 0, "failed": [],
             "selection": {"ran": {}, "skipped": ["a", "b"]}}
        self.assertEqual(pc.final_status(r)[1], "green on build.example.org: "
                         "0 of 2 steps (nothing reads what changed)")

    def test_every_step_and_why(self):
        r = {"ok": True, "seconds": 9, "failed": [],
             "selection": {"every": "no dependency record for these steps"}}
        self.assertEqual(pc.final_status(r)[1], "green on build.example.org "
                         "in 9s (every step: no dependency record for these "
                         "steps)")

    def test_red_says_how_many_ran(self):
        r = {"ok": False, "seconds": 9, "failed": ["rust"], "selection": {
            "ran": {"rust": "src/a.rs"}, "skipped": ["web"]}}
        self.assertEqual(pc.final_status(r), (
            "failure", "red on build.example.org: rust failed (1 of 2 "
            "steps)"))


class Covers(unittest.TestCase):

    BASE = "f" * 40

    def narrow(self, *ran, base=BASE):
        return {"selection": {"base": base, "ran": dict.fromkeys(ran, "x"),
                              "skipped": []}}

    def test_a_full_run_covers_anything(self):
        for selection in (None, {"every": "why"},
                          self.narrow("a")["selection"]):
            self.assertTrue(pc.covers(selection)({}))
            self.assertTrue(pc.covers(selection)(
                {"selection": {"every": "why"}}))

    def test_a_narrow_run_covers_only_a_narrower_head_on_its_base(self):
        head = self.narrow("a")["selection"]
        self.assertTrue(pc.covers(head)(self.narrow("a", "b")))
        self.assertFalse(pc.covers(head)(self.narrow("b")))
        self.assertFalse(pc.covers(head)(self.narrow("a", base="0" * 40)))

    def test_nor_a_head_that_runs_everything(self):
        self.assertFalse(pc.covers({"every": "why"})(self.narrow("a")))
        self.assertFalse(pc.covers(None)(self.narrow("a")))


class Choose(StateCase):
    """`choose` against a real repository."""

    def setUp(self):
        super().setUp()
        self.steps = [("docs", ["cat", "README"], None),
                      ("code", ["cat", "src/a"], None),
                      ("fmt", ["true"], None)]
        depends_on(self, self.steps, always=["fmt"])
        self.wt = os.path.join(self._tmp.name, "wt")
        git("init", "-q", "-b", "main", self.wt, cwd=self._tmp.name)
        self.commit({"README": "r", "src/a": "a"})
        self.base = git("rev-parse", "HEAD", cwd=self.wt)
        git("update-ref", MAIN_REF, self.base, cwd=self.wt)
        self.tree = git("rev-parse", "HEAD^{tree}", cwd=self.wt)

    def commit(self, files):
        for name, text in files.items():
            path = pathlib.Path(self.wt, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            git("add", name, cwd=self.wt)
        git("commit", "-q", "-m", "c", cwd=self.wt)
        return git("rev-parse", "HEAD", cwd=self.wt)

    def record(self, tree=None, commit=None, steps=None):
        return trace.write_record(
            self.state_dir, tree or self.tree, commit or self.base,
            steps or pc.steps_digest(), pc.iso(T0), "preload",
            {"docs": ["README"], "code": ["src/a"]},
            {"docs": ["."], "code": ["src"]})

    def test_the_steps_that_read_the_change(self):
        path = self.record()
        head = self.commit({"src/a": "b"})
        self.assertEqual(pc.choose(self.wt, head, self.state_dir), {
            "base": self.base, "record": self.base, "depends": path,
            "ran": {"code": "src/a", "fmt": "always"}, "skipped": ["docs"]})

    def test_from_an_ancestors_record_with_what_changed_since(self):
        self.record()
        newer = self.commit({"README": "changed on main"})
        git("update-ref", MAIN_REF, newer, cwd=self.wt)
        head = self.commit({"src/b": "new"})
        got = pc.choose(self.wt, head, self.state_dir)
        self.assertEqual(got["base"], newer)
        self.assertEqual(got["record"], self.base)
        self.assertEqual(got["ran"], {"docs": "README",
                                      "code": "src/b (in src)",
                                      "fmt": "always"})

    def test_every_step_without_a_record(self):
        head = self.commit({"src/a": "b"})
        self.assertEqual(pc.choose(self.wt, head, self.state_dir),
                         {"every": "no dependency record for these steps"})

    def test_every_step_when_the_steps_changed(self):
        self.record(steps="another digest")
        head = self.commit({"src/a": "b"})
        self.assertIn("every", pc.choose(self.wt, head, self.state_dir))

    def test_every_step_when_no_record_is_of_an_ancestor(self):
        self.record(tree="0" * 40, commit="1" * 40)
        head = self.commit({"src/a": "b"})
        self.assertEqual(pc.choose(self.wt, head, self.state_dir)["every"],
                         "no dependency record for %s or before it"
                         % self.base[:10])

    def test_every_step_without_a_merge_base(self):
        self.record()
        git("update-ref", "-d", MAIN_REF, cwd=self.wt)
        self.assertEqual(pc.choose(self.wt, self.base, self.state_dir),
                         {"every": "no merge base with main"})

    def test_every_step_when_the_diff_fails(self):
        self.record()
        head = self.commit({"src/a": "b"})
        real = pc.sh

        def run(args, **kw):
            if "diff" in args:
                return done(args, 128, "", "fatal")
            return real(args, **kw)
        self.assertEqual(pc.choose(self.wt, head, self.state_dir, run),
                         {"every": "could not list what changed since %s"
                          % self.base[:10]})


@unittest.skipUnless(shutil.which("cc"), "no C compiler")
class RealRuns(StateCase):
    """The worker with real git, real steps and the real tracer: a
    default-branch run records, then a pull request runs what it needs."""

    def setUp(self):
        super().setUp()
        root = self._tmp.name
        self.bases = os.path.join(root, "bases")
        self.steps = [
            ("show", ["cat", "A"], None),
            ("list", ["ls", "docs"], None),
            ("base", ["sh", "-c", 'echo "${CHECK_BASE:-none}" >> %s'
                      % self.bases], None)]
        depends_on(self, self.steps, always=["base"])
        self.origin = os.path.join(root, "origin.git")
        self.seed = os.path.join(root, "seed")
        self.repo = os.path.join(root, "repo")
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=root)
        git("init", "-q", "-b", "main", self.seed, cwd=root)
        self.main = self.commit({"A": "a", "B": "b", "docs/x": "x"})
        git("push", "-q", self.origin, "main", cwd=self.seed)
        git("clone", "-q", self.origin, self.repo, cwd=root)
        self.posts = []

    def commit(self, files):
        for name, text in files.items():
            path = pathlib.Path(self.seed, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            git("add", name, cwd=self.seed)
        git("commit", "-q", "-m", "c", cwd=self.seed)
        return git("rev-parse", "HEAD", cwd=self.seed)

    def pull_request(self, files):
        git("checkout", "-q", "-B", "feature", self.main, cwd=self.seed)
        head = self.commit(files)
        git("push", "-q", "-f", self.origin, "feature:refs/pull/7/head",
            cwd=self.seed)
        return head

    def work(self, sha, pr, main=False):
        clock = iter([T0, T0 + datetime.timedelta(minutes=1)])
        return pc.worker(self.repo, self.state_dir, sha, pr, pc.sh,
                         self.lines.append, lambda: next(clock),
                         lambda *a: self.posts.append(a) or True,
                         main=main)

    def run_main(self):
        self.write_state({"waiting": {self.main: {
            "pr": 0, "main": True, "since": pc.iso(T0)}}})
        return self.work(self.main, 0, main=True)

    def logged(self):
        return pathlib.Path(self.state_dir, "verdicts.log").read_text()

    def test_a_default_branch_run_records_what_each_step_read(self):
        self.assertEqual(self.run_main(), 0)
        tree = git("rev-parse", self.main + "^{tree}", cwd=self.seed)
        (path, record), = trace.load_records(self.state_dir)
        self.assertEqual(os.path.basename(path), tree + ".json")
        self.assertEqual(record["commit"], self.main)
        self.assertIn("A", record["reads"]["show"])
        self.assertNotIn("B", record["reads"]["show"])
        self.assertIn("docs", record["reads"]["list"])
        self.assertIn("docs", record["dirs"]["list"])
        self.assertIn("MAIN-GREEN main %s" % self.main[:10], self.logged())
        self.assertEqual([p[:2] for p in self.posts],
                         [(self.main, "pending"), (self.main, "success")])
        state = self.state()
        self.assertTrue(state["results"][self.main]["main"])
        self.assertEqual(state["waiting"], {})
        # No trace file is left behind.
        self.assertEqual([n for n in os.listdir(trace.records_dir(
            self.state_dir)) if n.startswith(".")], [])

    def test_a_pull_request_then_runs_what_reads_its_change(self):
        self.run_main()
        head = self.pull_request({"docs/new": "n"})
        self.write_state(dict(self.state(), waiting={head: {
            "pr": 7, "since": pc.iso(T0)}}))
        self.assertEqual(self.work(head, 7), 0)
        selection = self.state()["results"][head]["selection"]
        self.assertEqual(selection["ran"], {"list": "docs/new (in docs)",
                                            "base": "always"})
        self.assertEqual(selection["skipped"], ["show"])
        self.assertEqual(pathlib.Path(self.bases).read_text().split(),
                         ["none", self.main])
        self.assertEqual(self.posts[-1][1:], (
            "success", "green on build.example.org: 2 of 3 steps (list: "
            "docs/new (in docs); base: always)"))

    def test_a_red_default_branch_run_records_nothing(self):
        with mock.patch.object(pc, "STEPS", self.steps
                               + [("broken", ["false"], None)]):
            self.assertEqual(self.run_main(), 1)
        self.assertEqual(trace.load_records(self.state_dir), [])
        self.assertIn("MAIN-RED main", self.logged())

    def test_no_tracer_runs_every_step_and_records_nothing(self):
        with mock.patch.object(pc.trace, "build", lambda *a, **k: None):
            self.assertEqual(self.run_main(), 0)
        self.assertEqual(trace.load_records(self.state_dir), [])
        self.assertIn("NO-TRACE main %s: the preload tracer can't be used"
                      % self.main[:10], self.logged())

    def test_a_step_that_recorded_nothing_makes_the_record_unusable(self):
        with mock.patch.object(pc.trace, "read_preload",
                               lambda path: []):
            self.assertEqual(self.run_main(), 0)
        self.assertEqual(trace.load_records(self.state_dir), [])
        self.assertIn("DEPENDS-UNUSABLE main", self.logged())

    def test_a_narrow_verdict_is_not_matched_for_a_head_needing_more(self):
        self.run_main()
        head = self.pull_request({"docs/new": "n"})
        self.write_state(dict(self.state(), waiting={head: {
            "pr": 7, "since": pc.iso(T0)}}))
        with mock.patch.object(pc, "TEST_AHEAD", True):
            self.work(head, 7)
            tree = git("rev-parse", head + "^{tree}", cwd=self.seed)
            state = self.state()
            narrow = state["results"][head]
            # Another head with this tree, but needing `show` as well.
            wider = {"base": narrow["selection"]["base"],
                     "ran": {"show": "A", "list": "x", "base": "always"}}
            later = T0 + datetime.timedelta(minutes=5)
            self.assertIsNone(pc.tested(state, tree, later, ok=True,
                                        accept=pc.covers(wider)))
            self.assertEqual(pc.tested(state, tree, later, ok=True,
                                       accept=pc.covers(
                                           narrow["selection"])), head)


if __name__ == "__main__":
    unittest.main()
