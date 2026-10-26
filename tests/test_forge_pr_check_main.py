"""Tests for designs/0011: `checks.main: "every"` checks every new
default-branch head, depends or not, and `checks.main_red_issue` keeps
one issue open while the default branch is red."""

import os
import subprocess
import tempfile
import unittest

from agent_forge import config
from tests.helpers import Repo, example
from unittest import mock

from tests.test_forge_pr_check import (
    SHA_A, SHA_B, SHA_C, T0, FakeRun, StateCase, done, heads_tsv, is_pulls,
    pc)

TREE = "7" * 40


def configured(**checks):
    data = example()
    data["checks"].update(checks)
    pc.configure(config.Project(data, None))


class MainConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def problems(self, **checks):
        data = example()
        data["checks"].update(checks)
        return config.check(data)

    def test_main_is_needed_or_every(self):
        self.assertEqual(self.problems(main="every"), [])
        self.assertEqual(self.problems(main="needed"), [])
        self.assertEqual(self.problems(main="newest"), [])
        self.assertEqual(self.problems(main="always"), [
            "checks.main must be one of needed, every, newest"])

    def test_a_red_issue_needs_a_default_branch_that_is_checked(self):
        self.assertEqual(self.problems(main="every",
                                       main_red_issue={"label": "bug"}), [])
        self.assertEqual(self.problems(depends=True,
                                       main_red_issue={}), [])
        self.assertEqual(len(self.problems(main_red_issue={})), 1)
        self.assertEqual(self.problems(
            main="every", main_red_issue={"labels": ["bug", "x"]}), [])
        self.assertIn("unknown key 'tags'", self.problems(
            main="every", main_red_issue={"tags": []})[0])

    def test_off_unless_configured(self):
        self.assertFalse(pc.MAIN_EVERY)
        self.assertIsNone(pc.MAIN_RED_ISSUE)
        configured(main="every", main_red_issue={"label": "bug"})
        self.assertTrue(pc.MAIN_EVERY)
        self.assertEqual(pc.MAIN_RED_ISSUE, {"label": "bug"})


class QueueEvery(unittest.TestCase):

    def setUp(self):
        configured(main="every")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_every_new_head_is_queued_whatever_the_records_say(self):
        state = {"results": {}, "waiting": {}}
        # needs=[] would have kept it out under checks.fresh.
        self.assertTrue(pc.queue_main(state, (SHA_A, TREE), T0,
                                      self.dir.name, needs=[]))
        self.assertTrue(state["waiting"][SHA_A]["main"])
        self.assertFalse(pc.queue_main(state, (SHA_A, TREE), T0,
                                       self.dir.name))
        state["results"][SHA_B] = {"ok": True}
        self.assertFalse(pc.queue_main(state, (SHA_B, TREE), T0,
                                       self.dir.name))


class TickEvery(StateCase):
    """Without depends, the default branch's head is queued and run."""

    def setUp(self):
        super().setUp()
        for name, value in (("DEPENDS", False), ("MAIN_EVERY", True),
                            ("TEST_AHEAD", False)):
            patch = mock.patch.object(pc, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def tick(self):
        run = FakeRun((is_pulls, done([], 0, heads_tsv())),
                      (lambda a: "rev-parse" in a,
                       done([], 0, SHA_C + "\n" + TREE + "\n")))
        self.spawned = []
        return pc.tick(self.state_dir, "tok", run,
                       lambda *a, **k: self.spawned.append((a, k)),
                       self.lines.append, when=T0, slots=1,
                       checkout="/repo")

    def test_the_head_is_queued_and_started_untraced(self):
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.spawned, [((self.state_dir, SHA_C, 0, False, 1,
                                          "/repo"), {"main": True})])
        self.assertEqual(self.lines, [
            "QUEUED main cccccccccc: a run of every step",
            "STARTED main cccccccccc, every step; 0 waiting"])

    def test_without_every_nothing_is_queued(self):
        with mock.patch.object(pc, "MAIN_EVERY", False):
            self.tick()
        self.assertEqual(self.spawned, [])


class MatchedMain(unittest.TestCase):

    def setUp(self):
        configured(main="every")
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_a_matched_head_is_a_green_default_branch_verdict(self):
        state = {"results": {SHA_B: {"pr": 7, "ok": True, "ahead": True,
                                     "finished": pc.iso(T0)}},
                 "waiting": {SHA_A: {"pr": 0, "main": True,
                                     "since": pc.iso(T0)}}}
        pc.save(self.dir.name, state)
        lines = []
        pc.matched(self.dir.name, SHA_A, 0, SHA_B, TREE, T0, None,
                   lines.append, main=True)
        record = pc.load(self.dir.name)["results"][SHA_A]
        self.assertTrue(record["ok"] and record["main"])
        self.assertEqual(record["matched"], SHA_B)
        self.assertIn("MAIN-GREEN", lines[0])
        self.assertIn("the Merger's prediction for #7", lines[0])


class Writer:
    """GitHub, as report_main sees it: each write recorded, answered by
    `answers` (status by method) or success."""

    def __init__(self, answers=None):
        self.writes, self.answers, self.next = [], answers or {}, 41

    def __call__(self, method, path, body=None):
        self.writes.append((method, path, body))
        status = self.answers.get(method)
        if status is not None:
            return status, {"message": "nope"}
        if method == "POST" and path.endswith("/issues"):
            self.next += 1
            return 201, {"number": self.next}
        return (201 if method == "POST" else 200), {}


class RedIssue(unittest.TestCase):

    def setUp(self):
        configured(main="every", main_red_issue={
            "label": "main-red", "title": "Red at {sha}"})
        self.r = Repo(self)
        self.repo = os.path.join(self.r.dir, "repo")
        self.r.git(self.r.dir, "init", "-q", "-b", "main", self.repo)
        self.green = self.commit("green")
        self.c1 = self.commit("one")
        self.red = self.commit("two")
        self.state_dir = os.path.join(self.r.dir, "state")
        os.makedirs(self.state_dir)
        pc.save(self.state_dir, {"results": {}, "waiting": {},
                                 "main_green": self.green})
        self.lines = []

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def commit(self, message):
        self.r.git(self.repo, "-c", "user.name=t", "-c", "user.email=t@t",
                   "commit", "-q", "--allow-empty", "-m", message)
        return self.r.git(self.repo, "rev-parse", "HEAD").stdout.strip()

    def run_git(self, args, **_):
        return subprocess.run(args, capture_output=True, text=True,
                              env=self.r.env)

    def report(self, sha, ok, write, failed=(("test", "boom\n"),),
               same=None):
        pc.report_main(self.state_dir, self.repo, sha, ok,
                       [] if ok else list(failed), same, write,
                       self.lines.append, self.run_git)

    def test_the_first_red_head_opens_one_labelled_issue(self):
        write = Writer()
        self.report(self.red, False, write)
        (method, path, body), = write.writes
        self.assertEqual((method, path), ("POST", "/repos/%s/issues"
                                          % pc.REPO_SLUG))
        self.assertEqual(body["title"], "Red at %s" % self.red[:10])
        self.assertEqual(body["labels"], ["main-red"])
        self.assertIn("test", body["body"])
        self.assertIn("boom", body["body"])
        self.assertIn("one", body["body"])
        self.assertIn("two", body["body"])
        self.assertNotIn("* %s green" % self.green[:7], body["body"])
        self.assertEqual(pc.load(self.state_dir)["main_red"]["issue"], 42)
        self.assertIn("FILED #42", self.lines[0])
        # Signed as the poster, on the last line, where a project's tools
        # read the owner from.
        self.assertEqual(body["body"].splitlines()[-1],
                         "— %s" % pc.POSTER)

    def test_labels_as_a_list_and_every_write_signed(self):
        configured(main="every", main_red_issue={
            "labels": ["bug", "Notify:Reviewer"]})
        write = Writer()
        self.report(self.red, False, write)
        self.report(self.commit("three"), False, write)
        self.report(self.commit("fix"), True, write)
        self.assertEqual(write.writes[0][2]["labels"],
                         ["bug", "Notify:Reviewer"])
        self.assertEqual(write.writes[0][2]["title"],
                         "The default branch is red at %s" % self.red[:10])
        for method, _, body in write.writes:
            if "body" in body:
                self.assertTrue(body["body"].endswith(
                    "build check.\n\n— %s" % pc.POSTER), body["body"])

    def test_each_further_red_head_comments_once(self):
        write = Writer()
        self.report(self.red, False, write)
        later = self.commit("three")
        self.report(later, False, write)
        self.report(later, False, write)
        self.assertEqual([w[0] for w in write.writes], ["POST", "POST"])
        self.assertTrue(write.writes[1][1].endswith("/issues/42/comments"))
        self.assertIn("Still red at %s" % later[:10],
                      write.writes[1][2]["body"])

    def test_the_first_green_head_closes_it_and_names_a_match(self):
        write = Writer()
        self.report(self.red, False, write)
        fixed = self.commit("fix")
        self.report(fixed, True, write, same=SHA_C)
        comment, close = write.writes[1:]
        self.assertIn(SHA_C[:10], comment[2]["body"])
        self.assertEqual(close[:2], ("PATCH", "/repos/%s/issues/42"
                                     % pc.REPO_SLUG))
        self.assertEqual(close[2], {"state": "closed"})
        state = pc.load(self.state_dir)
        self.assertNotIn("main_red", state)
        self.assertEqual(state["main_green"], fixed)

    def test_green_with_nothing_open_writes_nothing(self):
        write = Writer()
        self.report(self.c1, True, write)
        self.assertEqual(write.writes, [])
        self.assertEqual(pc.load(self.state_dir)["main_green"], self.c1)

    def test_a_refused_issue_is_tried_again_on_the_next_red(self):
        self.report(self.red, False, Writer({"POST": 403}))
        self.assertIn("REPORT-FAILED", self.lines[0])
        self.assertNotIn("main_red", pc.load(self.state_dir))
        write = Writer()
        self.report(self.commit("three"), False, write)
        self.assertTrue(write.writes[0][1].endswith("/issues"))

    def test_a_refused_close_is_tried_again_on_the_next_green(self):
        self.report(self.red, False, Writer())
        self.report(self.commit("fix"), True, Writer({"PATCH": 500}))
        self.assertEqual(pc.load(self.state_dir)["main_red"]["issue"], 42)
        write = Writer()
        self.report(self.commit("more"), True, write)
        self.assertEqual(write.writes[-1][0], "PATCH")
        self.assertNotIn("main_red", pc.load(self.state_dir))


if __name__ == "__main__":
    unittest.main()
