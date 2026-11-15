"""Tests for checks.instances.<name>.main_paths: a scoped instance runs
a default-branch head only when it changes one of the instance's
pull_requests.paths since the last head with a verdict there. Any other
head is given that verdict, so its status isn't left pending and a red
stays red until a head that changes those paths is run."""

import datetime
import unittest

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import (
    SHA_A, SHA_B, SHA_C, T0, FakeRun, StateCase, done, is_pulls, pc)
from tests.test_forge_pr_check_instances import SCOPE, with_mac

TREE = "7" * 40
EARLIER = pc.iso(T0 - datetime.timedelta(hours=1))


def scoped(**block):
    block.setdefault("pull_requests", SCOPE)
    block.setdefault("main", "every")
    block.setdefault("main_paths", True)
    return with_mac(**block)


def is_diff(args):
    return "diff" in args and "--name-only" in args


class Validation(unittest.TestCase):

    def test_a_scoped_block_with_every_or_newest_may_set_it(self):
        self.assertEqual(config.check(scoped()), [])
        self.assertEqual(config.check(scoped(main="newest")), [])
        self.assertEqual(config.check(scoped(main_paths=False,
                                             main="needed")), [])

    def test_it_is_a_boolean(self):
        self.assertEqual(config.check(scoped(main_paths="yes")), [
            "checks.instances.mac.main_paths must be true or false"])

    def test_it_needs_paths(self):
        self.assertEqual(config.check(scoped(
            pull_requests={"label": "Check:mac"})), [
            "checks.instances.mac.main_paths needs "
            "checks.instances.mac.pull_requests.paths: they are what a "
            "default-branch head must change to be run"])
        self.assertEqual(config.check(with_mac(main="every",
                                               main_paths=True)), [
            "checks.instances.mac.main_paths needs "
            "checks.instances.mac.pull_requests.paths: they are what a "
            "default-branch head must change to be run"])

    def test_it_needs_every_or_newest(self):
        self.assertEqual(config.check(scoped(main="needed")), [
            "checks.instances.mac.main_paths needs "
            "checks.instances.mac.main \"every\" or \"newest\": otherwise "
            "default-branch heads are run only when a record needs them"])

    def test_checks_itself_may_not(self):
        data = example()
        data["checks"]["main_paths"] = True
        self.assertEqual(config.check(data), [
            "checks.main_paths: only an instance may set it, since the "
            "Merger's context checks every default-branch head it needs"])

    def test_it_sits_with_the_red_issue(self):
        self.assertEqual(config.check(scoped(
            main_red_issue={"labels": ["bug"]})), [])


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_the_instance_reads_it(self):
        pc.configure(config.Project(scoped(), None), "mac")
        self.assertTrue(pc.MAIN_PATHS)

    def test_off_by_default(self):
        pc.configure(config.Project(scoped(main_paths=False), None), "mac")
        self.assertFalse(pc.MAIN_PATHS)
        pc.configure(config.Project(scoped(), None))
        self.assertFalse(pc.MAIN_PATHS)


class Carry(unittest.TestCase):

    def setUp(self):
        pc.configure(config.Project(scoped(), None), "mac")
        self.addCleanup(pc.configure, config.Project(example(), None))

    def state(self, ok=True):
        return {"results": {SHA_A: {"pr": 0, "main": True, "ok": ok,
                                    "failed": [] if ok else ["test-mac"],
                                    "finished": EARLIER}},
                "waiting": {}}

    def carry(self, state, changed="README.md\n", rc=0):
        self.run = FakeRun((is_diff, done([], rc, changed)))
        return pc.main_carry(state, (SHA_C, TREE), "/repo", self.run)

    def test_a_head_that_changes_none_of_its_paths_is_carried(self):
        state = self.state()
        self.assertEqual(self.carry(state), (SHA_A, state["results"][SHA_A]))
        self.assertEqual(self.run.calls[0]["args"], [
            "git", "-C", "/repo", "diff", "--name-only", SHA_A, SHA_C, "--"])

    def test_one_that_changes_one_is_run(self):
        self.assertIsNone(self.carry(self.state(),
                                     "README.md\ndesktop/mac/App.swift\n"))

    def test_git_failing_runs_it(self):
        self.assertIsNone(self.carry(self.state(), "", rc=128))

    def test_with_no_verdict_yet_it_is_run(self):
        self.assertIsNone(self.carry({"results": {}, "waiting": {}}))

    def test_a_head_already_judged_or_queued_is_left(self):
        state = self.state()
        state["waiting"][SHA_C] = {"pr": 0, "main": True, "since": EARLIER}
        self.assertIsNone(self.carry(state))

    def test_off_nothing_is_carried(self):
        pc.configure(config.Project(scoped(main_paths=False), None), "mac")
        self.assertIsNone(self.carry(self.state()))


class Record(StateCase):

    def setUp(self):
        super().setUp()
        pc.configure(config.Project(scoped(), None), "mac")
        self.addCleanup(pc.configure, config.Project(example(), None))

    def test_the_verdict_is_recorded_as_carried(self):
        state = {"results": {SHA_A: {"pr": 0, "main": True, "ok": False,
                                     "failed": ["test-mac"],
                                     "finished": EARLIER}},
                 "waiting": {SHA_B: {"pr": 0, "main": True,
                                     "since": EARLIER}}}
        self.assertTrue(pc.carry_main(state, SHA_C, SHA_A,
                                      state["results"][SHA_A], T0,
                                      self.state_dir, out=self.lines.append))
        record = state["results"][SHA_C]
        self.assertEqual(record["carried"], SHA_A)
        self.assertFalse(record["ok"])
        self.assertEqual(record["failed"], ["test-mac"])
        self.assertNotIn("tree", record)
        # An older tip still waiting is dropped, as queue_main drops it.
        self.assertEqual(state["waiting"], {})
        self.assertEqual(self.lines, [
            "2026-10-09T12:00:00Z CARRIED mac main cccccccccc: "
            "red(test-mac) as at aaaaaaaaaa, which changes none of this "
            "check's paths"])
        # labels.rerun runs a carried red again like any red.
        self.assertEqual(pc.rerun_kind(state, SHA_C), "red")

    def test_its_status_names_the_head_it_was_carried_from(self):
        self.assertEqual(pc.final_status(
            {"ok": True, "failed": [], "carried": SHA_A}),
            ("success", "green as at aaaaaaaaaa: no change in this "
             "check's paths since"))
        self.assertEqual(pc.final_status(
            {"ok": False, "failed": ["test-mac"], "carried": SHA_A}),
            ("failure", "red(test-mac) as at aaaaaaaaaa: no change in "
             "this check's paths since"))


class Tick(StateCase):

    def setUp(self):
        super().setUp()
        pc.configure(config.Project(scoped(), None), "mac")
        self.addCleanup(pc.configure, config.Project(example(), None))

    def tick(self, changed, ok=True, posted=True):
        pc.save(self.state_dir, {"results": {SHA_A: {
            "pr": 0, "main": True, "ok": ok,
            "failed": [] if ok else ["test-mac"], "finished": EARLIER}},
            "waiting": {}})
        self.run = FakeRun(
            (is_pulls, done([], 0, "")),
            (lambda a: "rev-parse" in a, done([], 0, SHA_C + "\n" + TREE)),
            (is_diff, done([], 0, changed)))
        self.posts, self.spawned = [], []

        def post(sha, state, text, context=None):
            self.posts.append((sha, state, text))
            return posted
        return pc.tick(self.state_dir, "tok", self.run,
                       lambda *a, **k: self.spawned.append(a),
                       self.lines.append, when=T0, post=post, slots=1,
                       checkout="/repo")

    def test_an_unchanged_tip_is_posted_and_not_run(self):
        self.assertEqual(self.tick("README.md\n"), 0)
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.posts, [(SHA_C, "success",
                                       "green as at aaaaaaaaaa: no change "
                                       "in this check's paths since")])
        record = pc.load(self.state_dir)["results"][SHA_C]
        self.assertTrue(record["posted"])
        self.assertIn("CARRIED", self.lines[0])

    def test_a_red_is_carried_as_red(self):
        self.tick("README.md\n", ok=False)
        self.assertEqual(self.posts[0][1], "failure")

    def test_a_changed_tip_is_queued_and_run(self):
        self.tick("desktop/mac/App.swift\n")
        self.assertEqual(self.posts, [])
        self.assertEqual([a[1] for a in self.spawned], [SHA_C])

    def test_a_refused_post_is_tried_again(self):
        self.tick("README.md\n", posted=False)
        self.assertFalse(pc.load(self.state_dir)["results"][SHA_C]["posted"])
        self.posts = []

        def post(sha, state, text, context=None):
            self.posts.append((sha, state, text))
            return True
        pc.tick(self.state_dir, "tok", self.run, lambda *a, **k: None,
                self.lines.append, when=T0, post=post, slots=1,
                checkout="/repo")
        self.assertEqual([p[0] for p in self.posts], [SHA_C])
        self.assertTrue(pc.load(self.state_dir)["results"][SHA_C]["posted"])


if __name__ == "__main__":
    unittest.main()
