"""Tests for bin/forge-resolve: real git in a throwaway repository."""

import json
import os
import unittest

from agent_forge import config, union
from tests.helpers import Repo, example, load_tool

r = load_tool("forge-resolve")

LIST = "# Changes\n\n* base\n\n## Older\n"


class ForgeResolve(unittest.TestCase):

    def setUp(self):
        self.repo = Repo(self)
        self.repo.env.update(GIT_AUTHOR_NAME="a", GIT_AUTHOR_EMAIL="a@a",
                             GIT_COMMITTER_NAME="a", GIT_COMMITTER_EMAIL="a@a")
        self.path = os.path.join(self.repo.dir, "work")
        self.git("init", "-q", "-b", "main", self.path, cwd=self.repo.dir)
        self.run = self.repo.run(self.path)
        self.write_config(["*.md"])
        self.write("CHANGES.md", LIST)
        self.write("code.txt", "a\nb\nc\n")
        self.commit("base")
        self.git("checkout", "-q", "-b", "feature")
        self.write("CHANGES.md", LIST.replace("* base\n", "* base\n* f\n"))
        self.head = self.commit("feature")
        self.git("checkout", "-q", "main")
        self.write("CHANGES.md", LIST.replace("* base\n", "* base\n* m\n"))
        self.main = self.commit("main")
        self.git("update-ref", "refs/remotes/origin/main", self.main)
        self.git("checkout", "-q", "feature")

    def git(self, *args, cwd=None):
        return self.repo.git(cwd or self.path, *args).stdout.strip()

    def write(self, name, text):
        where = os.path.join(self.path, name)
        os.makedirs(os.path.dirname(where), exist_ok=True)
        with open(where, "w") as handle:
            handle.write(text)
        self.git("add", name)

    def write_config(self, globs):
        data = example()
        data["merge"]["resolve"] = {"union": globs}
        self.write(config.CONFIG_NAME, json.dumps(data))

    def commit(self, message):
        self.git("commit", "-q", "-m", message)
        return self.git("rev-parse", "HEAD")

    def main_(self, *argv):
        self.lines = []
        return r.main(list(argv), self.run, self.lines.append, cwd=self.path)

    def test_it_makes_the_mergers_merge(self):
        self.assertEqual(self.main_(), 0)
        new = self.git("rev-parse", "HEAD")
        self.assertEqual(self.git("rev-list", "--parents", "-n1", new).split()
                         [1:], [self.head, self.main])
        self.assertEqual(self.git("symbolic-ref", "--short", "HEAD"),
                         "feature")
        with open(os.path.join(self.path, "CHANGES.md")) as handle:
            self.assertEqual(handle.read(),
                             LIST.replace("* base\n", "* base\n* f\n* m\n"))
        self.assertEqual(self.git("rev-parse", new + "^{tree}"), union.resolve(
            self.path, self.head, self.main, ["*.md"], self.run).tree)
        self.assertEqual(self.git("log", "-1", "--format=%an", new), "a")
        self.assertIn("`CHANGES.md`: 1 line from HEAD, then 1 line from "
                      "origin/main:", self.lines)
        self.assertTrue(self.lines[-1].startswith(
            "MERGED: origin/main into feature as "))
        # Done, it is up to date.
        self.assertEqual(self.main_(), 0)
        self.assertEqual(self.lines, ["UP-TO-DATE: HEAD already holds "
                                      "origin/main"])

    def test_a_dry_run_changes_nothing(self):
        self.assertEqual(self.main_("--dry-run", "--onto", "main"), 0)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertEqual(self.lines[-1], "WOULD-MERGE: main into feature, "
                                         "keeping both sides in CHANGES.md")

    def test_another_conflict_is_left_to_the_author(self):
        self.write("code.txt", "a\nF\nc\n")
        head = self.commit("feature code")
        self.git("checkout", "-q", "main")
        self.write("code.txt", "a\nM\nc\n")
        self.commit("main code")
        self.git("checkout", "-q", "feature")
        self.assertEqual(self.main_("--onto", "main"), 1)
        self.assertEqual(self.git("rev-parse", "HEAD"), head)
        self.assertEqual(self.lines, [
            "CONFLICT: code.txt conflicts, and no glob names it; merge it "
            "by hand (git merge main)"])

    def test_the_globs_are_the_ones_at_the_ref(self):
        # This branch's own configuration names nothing; main's counts.
        self.write_config([])
        self.commit("no globs here")
        self.assertEqual(self.main_("--dry-run", "--onto", "main"), 0)
        self.git("checkout", "-q", "main")
        self.write_config(["docs/*"])
        self.commit("other globs")
        self.git("checkout", "-q", "feature")
        self.assertEqual(self.main_("--dry-run", "--onto", "main"), 1)

    def test_uncommitted_changes_stop_it(self):
        with open(os.path.join(self.path, "code.txt"), "a") as handle:
            handle.write("more\n")
        self.assertEqual(self.main_("--onto", "main"), 2)
        self.assertEqual(self.lines[-1], "DIRTY: commit or stash the changes "
                                         "to tracked files first")
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)

    def test_an_unknown_ref(self):
        self.assertEqual(self.main_("--onto", "nowhere"), 2)
        self.assertTrue(self.lines[0].startswith("NO-CONFIG: "))

    def test_outside_a_checkout(self):
        self.lines = []
        self.assertEqual(r.main([], self.repo.run(self.repo.dir),
                                self.lines.append, cwd=self.repo.dir), 2)
        self.assertEqual(self.lines, ["NO-REPO: not inside a git checkout"])


if __name__ == "__main__":
    unittest.main()
