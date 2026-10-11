"""Tests for forge-pr-check's checks.merge_default (docs/designs/0007): a
pull request's head is checked merged with the default branch's tip.

Real git throughout, since what matters is the tree the steps see and
the commit a later update makes.
"""

import datetime
import os
import pathlib
import unittest
from unittest import mock

from agent_forge import config, trace
from tests.helpers import example
from tests.test_forge_pr_check import T0, StateCase, git, pc
from tests.test_forge_pr_check_depends import depends_on


def merge_default_on(test, steps, test_ahead=False):
    for name, value in (("MERGE_DEFAULT", True), ("STEPS", steps),
                        ("TEST_AHEAD", test_ahead), ("CACHES", {}),
                        ("DEPENDS", False)):
        patch = mock.patch.object(pc, name, value)
        patch.start()
        test.addCleanup(patch.stop)


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_off_unless_configured(self):
        pc.configure(config.Project(example(), None))
        self.assertFalse(pc.MERGE_DEFAULT)

    def test_on(self):
        data = example()
        data["checks"]["merge_default"] = True
        pc.configure(config.Project(data, None))
        self.assertTrue(pc.MERGE_DEFAULT)

    def test_only_a_bool(self):
        data = example()
        data["checks"]["merge_default"] = "yes"
        self.assertIn("checks.merge_default must be true or false",
                      config.check(data))


class Status(unittest.TestCase):

    def record(self, **more):
        r = {"ok": True, "failed": [], "seconds": 30, "base": "b" * 40}
        r.update(more)
        return r

    def test_green_names_the_tip(self):
        self.assertEqual(pc.final_status(self.record()), (
            "success", "green on build.example.org with main at bbbbbbbbbb "
                       "in 30s"))

    def test_a_red_tip_is_said_early(self):
        r = self.record(ok=False, failed=["test"], base_red=True)
        self.assertEqual(pc.final_status(r), (
            "failure", "red on build.example.org with main at bbbbbbbbbb "
                       "(itself red here): test failed"))

    def test_conflicts_name_the_first_paths(self):
        r = self.record(ok=False, failed=["merge"],
                        conflicts=["a", "b", "c", "d", "e"])
        self.assertEqual(pc.final_status(r), (
            "failure", "red on build.example.org: conflicts with main at "
                       "bbbbbbbbbb: a, b, c and 2 more"))

    def test_a_match_says_only_where_the_tree_was_tested(self):
        r = self.record(matched="c" * 40, matched_as="#7")
        self.assertEqual(pc.final_status(r)[1],
                         "green on build.example.org: tree tested as "
                         "cccccccccc (#7)")


class RepoCase(StateCase):
    """The worker with real git: an origin with `main` and pull request
    7's head, the cron checkout cloned from it."""

    def setUp(self):
        super().setUp()
        root = self._tmp.name
        self.seen = os.path.join(root, "seen")
        self.steps = [("look", ["sh", "-c", 'cat A B > %s; echo '
                                '"${CHECK_BASE:-none}" >> %s'
                                % (self.seen, self.seen)], None)]
        merge_default_on(self, self.steps)
        self.origin = os.path.join(root, "origin.git")
        self.seed = os.path.join(root, "seed")
        self.repo = os.path.join(root, "repo")
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=root)
        git("init", "-q", "-b", "main", self.seed, cwd=root)
        self.first = self.commit({"A": "a\n", "B": "b\n"})
        git("push", "-q", self.origin, "main", cwd=self.seed)
        git("clone", "-q", self.origin, self.repo, cwd=root)
        self.posts = []

    def commit(self, files, branch=None):
        if branch:
            git("checkout", "-q", branch, cwd=self.seed)
        for name, text in files.items():
            path = pathlib.Path(self.seed, name)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
            git("add", name, cwd=self.seed)
        git("commit", "-q", "-m", "c", cwd=self.seed)
        return git("rev-parse", "HEAD", cwd=self.seed)

    def pull_request(self, files, base=None):
        git("checkout", "-q", "-B", "feature", base or self.first,
            cwd=self.seed)
        head = self.commit(files)
        self.push_head()
        return head

    def push_head(self):
        git("push", "-q", "-f", self.origin, "feature:refs/pull/7/head",
            cwd=self.seed)

    def move_main(self, files):
        tip = self.commit(files, branch="main")
        git("push", "-q", self.origin, "main", cwd=self.seed)
        git("fetch", "-q", "origin", cwd=self.repo)
        return tip

    def work(self, sha, pr=7, at=T0):
        self.write_state(dict(self.state(), waiting={sha: {
            "pr": pr, "since": pc.iso(at)}}))
        clock = iter([at, at + datetime.timedelta(minutes=1)])
        return pc.worker(self.repo, self.state_dir, sha, pr, pc.sh,
                         self.lines.append, lambda: next(clock),
                         lambda *a: self.posts.append(a) or True)

    def saw(self):
        return pathlib.Path(self.seen).read_text()

    def logged(self):
        return pathlib.Path(self.state_dir, "verdicts.log").read_text()


class RealMerges(RepoCase):

    def test_a_head_behind_the_tip_is_checked_merged_with_it(self):
        head = self.pull_request({"A": "a from the head\n"})
        tip = self.move_main({"B": "b from main\n"})
        self.assertEqual(self.work(head), 0)
        # The steps saw both sides.
        self.assertEqual(self.saw(),
                         "a from the head\nb from main\nnone\n")
        record = self.state()["results"][head]
        self.assertEqual(record["base"], tip)
        merged = record["merged"]
        self.assertEqual(git("rev-list", "--parents", "-n1", merged,
                             cwd=self.repo).split()[1:], [tip, head])
        self.assertEqual(self.posts[-1], (
            head, "success", "green on build.example.org with main at %s "
                             "in 0s" % tip[:10]))

    def test_the_same_inputs_make_the_same_commit(self):
        head = self.pull_request({"A": "a2\n"})
        self.move_main({"B": "b2\n"})
        self.work(head)
        first = self.state()["results"][head]["merged"]
        self.work(head)
        self.assertEqual(self.state()["results"][head]["merged"], first)

    def test_a_head_that_contains_the_tip_is_checked_as_itself(self):
        head = self.pull_request({"A": "a2\n"})
        self.assertEqual(self.work(head), 0)
        record = self.state()["results"][head]
        self.assertEqual(record["base"], self.first)
        self.assertNotIn("merged", record)

    def test_a_conflict_runs_nothing_and_names_the_paths(self):
        head = self.pull_request({"A": "theirs\n"})
        tip = self.move_main({"A": "ours\n"})
        self.assertEqual(self.work(head), 1)
        self.assertFalse(os.path.exists(self.seen))
        record = self.state()["results"][head]
        self.assertEqual((record["failed"], record["conflicts"]),
                         (["merge"], ["A"]))
        self.assertEqual(self.state()["waiting"], {})
        self.assertEqual(self.posts, [(
            head, "failure", "red on build.example.org: conflicts with main "
                             "at %s: A" % tip[:10])])
        self.assertIn("VERDICT #7 %s: red(merge) conflicts with main at %s: "
                      "A" % (head[:10], tip[:10]), self.logged())

    def test_a_red_tip_is_named(self):
        head = self.pull_request({"A": "a2\n"})
        tip = self.move_main({"B": "b2\n"})
        self.write_state({"waiting": {}, "results": {tip: {
            "main": True, "ok": False, "failed": ["look"],
            "finished": pc.iso(T0)}}})
        with mock.patch.object(pc, "STEPS", [("broken", ["false"], None)]):
            self.assertEqual(self.work(head), 1)
        self.assertIn("(itself red here)", self.posts[-1][2])

    def test_the_update_before_a_merge_is_matched_unrun(self):
        merge_default_on(self, self.steps, test_ahead=True)
        head = self.pull_request({"A": "a2\n"})
        tip = self.move_main({"B": "b2\n"})
        self.assertEqual(self.work(head), 0)
        # What GitHub's update-branch makes: main merged into the head.
        git("checkout", "-q", "feature", cwd=self.seed)
        git("merge", "-q", "--no-edit", tip, cwd=self.seed)
        update = git("rev-parse", "HEAD", cwd=self.seed)
        self.push_head()
        os.unlink(self.seen)
        self.assertEqual(self.work(
            update, at=T0 + datetime.timedelta(minutes=5)), 0)
        self.assertFalse(os.path.exists(self.seen))
        self.assertEqual(self.state()["results"][update]["matched"], head)
        self.assertIn("MATCHED", self.logged())

    def test_predictions_and_off_are_unchanged(self):
        head = self.pull_request({"A": "a2\n"})
        self.move_main({"B": "b2\n"})
        with mock.patch.object(pc, "MERGE_DEFAULT", False):
            self.assertEqual(self.work(head), 0)
        self.assertEqual(self.saw(), "a2\nb\nnone\n")
        self.assertNotIn("base", self.state()["results"][head])


class WithDepends(RepoCase):
    """With checks.depends, the steps are chosen against the tip."""

    def test_the_tips_record_applies_and_is_check_base(self):
        steps = [("show", ["cat", "A"], None),
                 ("other", ["cat", "B"], None),
                 ("base", ["sh", "-c", 'echo "${CHECK_BASE:-none}" > %s'
                           % self.seen], None)]
        depends_on(self, steps, always=["base"])
        merge_default_on(self, steps)
        patch = mock.patch.object(pc, "DEPENDS", True)
        patch.start()
        self.addCleanup(patch.stop)
        head = self.pull_request({"A": "a2\n"})
        tip = self.move_main({"C": "c\n"})
        self.write_state({"waiting": {tip: {
            "pr": 0, "main": True, "since": pc.iso(T0)}}, "results": {}})
        clock = iter([T0, T0 + datetime.timedelta(minutes=1)])
        pc.worker(self.repo, self.state_dir, tip, 0, pc.sh, self.lines.append,
                  lambda: next(clock), None, main=True)
        self.assertTrue(trace.load_records(self.state_dir))
        self.assertEqual(self.work(head), 0)
        selection = self.state()["results"][head]["selection"]
        self.assertEqual(selection["base"], tip)
        self.assertEqual(selection["record"], tip)
        self.assertEqual(sorted(selection["ran"]), ["base", "show"])
        self.assertEqual(selection["skipped"], ["other"])
        self.assertEqual(self.saw().strip(), tip)


if __name__ == "__main__":
    unittest.main()
