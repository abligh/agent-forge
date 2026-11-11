"""Tests for agent_forge.checkout: a worktree and its main checkout
share one store, wherever each sits."""
import os
import unittest

from agent_forge import checkout
from tests.helpers import Repo


class Store(unittest.TestCase):

    def setUp(self):
        self.r = Repo(self)
        self.main = os.path.join(self.r.dir, "main")
        self.r.git(self.r.dir, "init", "-q", self.main)
        self.r.git(self.main, "-c", "user.name=t", "-c", "user.email=t@t",
                   "commit", "-q", "--allow-empty", "-m", "one")

    def test_a_worktree_shares_its_checkouts_store(self):
        wt = os.path.join(self.r.dir, "main.gen", "abc")
        self.r.git(self.main, "worktree", "add", "-q", "--detach", wt)
        self.assertEqual(checkout.store(wt), checkout.store(self.main))
        self.assertEqual(checkout.store(self.main),
                         os.path.realpath(os.path.join(self.main, ".git")))

    def test_the_store_itself_is_its_own_store(self):
        git_dir = os.path.join(self.main, ".git")
        self.assertEqual(checkout.store(git_dir), checkout.store(self.main))

    def test_another_clone_is_another_store(self):
        other = os.path.join(self.r.dir, "other")
        self.r.git(self.r.dir, "clone", "-q", self.main, other)
        self.assertNotEqual(checkout.store(other), checkout.store(self.main))

    def test_a_path_that_is_gone_is_itself(self):
        gone = os.path.join(self.r.dir, "gone")
        self.assertEqual(checkout.store(gone), os.path.realpath(gone))


if __name__ == "__main__":
    unittest.main()
