"""Tests for bin/forge-worktree-identity. Real git throughout where git's
scoping is the point; GitHub is faked."""
import os
import unittest

from agent_forge import config, github
from tests.helpers import EXAMPLE, Repo, example, load_tool

tool = load_tool("forge-worktree-identity")
LD_ID = 4242
LD_EMAIL = "4242+leaddevwidgetagent[bot]@users.noreply.github.com"
SSH = "git@github.com:example/widget.git"


def project():
    return config.Project(example(), str(EXAMPLE))


class BotUserId(unittest.TestCase):

    def lookup(self, user, minted=True):
        calls = []

        def call(url, token, scheme="Bearer", method="GET", body=None):
            calls.append((url, token))
            return 200, user
        tok = github.token
        github.token = (lambda *a, **k: "ghs_own") if minted else \
            (lambda *a, **k: (_ for _ in ()).throw(github.MintError(6, "no")))
        try:
            return github.bot_user_id(project(), "LeadDev", call), calls
        finally:
            github.token = tok

    def test_a_bot_answer_is_its_id_asked_with_the_agent_s_token(self):
        got, calls = self.lookup({"id": LD_ID, "type": "Bot"})
        self.assertEqual(got, LD_ID)
        self.assertEqual(calls, [("https://api.github.com/users/"
                                  "leaddevwidgetagent%5Bbot%5D", "ghs_own")])

    def test_a_user_that_is_not_a_bot_is_refused(self):
        self.assertIsNone(self.lookup({"id": 1, "type": "User"})[0])

    def test_without_a_token_it_asks_anonymously(self):
        got, calls = self.lookup({"id": LD_ID, "type": "Bot"}, minted=False)
        self.assertEqual((got, calls[0][1]), (LD_ID, None))

    def test_the_noreply_email(self):
        self.assertEqual(github.noreply_email(project(), "LeadDev", LD_ID),
                         LD_EMAIL)


class Worktrees(unittest.TestCase):

    def setUp(self):
        self.r = Repo(self)
        self.main_wt = os.path.join(self.r.dir, "main")
        self.wt = os.path.join(self.r.dir, "wt")
        self.r.git(self.r.dir, "init", "-q", "-b", "main", self.main_wt)
        self.r.git(self.main_wt, "remote", "add", "origin", SSH)
        self.r.git(self.main_wt, "config", "user.name", "Anonymous Agent")
        self.r.git(self.main_wt, "config", "user.email", "agent@example.org")
        self.r.git(self.main_wt, "commit", "-q", "--allow-empty", "-m", "base")
        self.r.git(self.main_wt, "worktree", "add", "-q", self.wt, "-b", "wt")
        self.helper = "!" + self.script("ours", "x-access-token", "ghs_ours")
        self.out, self.err = [], []

    def script(self, name, user, password):
        path = os.path.join(self.r.dir, name)
        with open(path, "w") as handle:
            handle.write("#!/bin/sh\n[ \"$1\" = get ] || exit 0\n"
                         "cat >/dev/null\nprintf 'username=%s\\npassword=%s\\n'\n"
                         % (user, password))
        os.chmod(path, 0o755)
        return path

    def main(self, *argv, cwd=None, lookup=lambda p, a: LD_ID):
        self.out, self.err = [], []
        return tool.main(list(argv), self.r.run(cwd or self.wt), project,
                         lookup, self.helper, self.out.append,
                         self.err.append)

    def read(self, cwd, *args):
        return self.r.git(cwd, *args, check=False).stdout

    def ident(self, cwd):
        return (self.read(cwd, "config", "--get", "user.name").strip(),
                self.read(cwd, "config", "--get", "user.email").strip())

    def fill(self, cwd):
        return self.r.git(cwd, "credential", "fill", input="protocol=https\n"
                          "host=github.com\npath=example/widget.git\n\n")


class Identity(Worktrees):

    def test_stamping_one_worktree_leaves_the_others(self):
        self.assertEqual(self.main("LeadDev"), 0)
        self.assertEqual(self.ident(self.wt), ("LeadDev", LD_EMAIL))
        self.assertEqual(self.ident(self.main_wt),
                         ("Anonymous Agent", "agent@example.org"))
        self.assertEqual(self.main("--check", "LeadDev", cwd=self.main_wt), 1)

    def test_check_refuses_an_inherited_identity(self):
        self.main("LeadDev")
        other = os.path.join(self.r.dir, "other")
        self.r.git(self.wt, "worktree", "add", "-q", other, "-b", "other")
        self.assertEqual(self.main("--check", "UIDev", cwd=other), 1)
        self.assertIn("commits as LeadDev", self.err[0])

    def test_check_writes_nothing(self):
        self.assertEqual(self.main("--check", "LeadDev"), 1)
        self.assertIn("run without --check", self.err[0])
        self.assertEqual(self.read(self.wt, "config", "--worktree", "--list",
                                   ), "")

    def test_unknown_agent_and_unresolvable_bot_are_2(self):
        self.assertEqual(self.main("Merger"), 2)
        self.assertEqual(self.main("LeadDev", lookup=lambda p, a: None), 2)
        self.assertIn("could not resolve leaddevwidgetagent[bot]", self.err[0])
        self.assertEqual(self.ident(self.wt),
                         ("Anonymous Agent", "agent@example.org"))


class PushApp(Worktrees):

    URL = "https://github.com/example/widget.git"

    def test_pushes_over_https_and_still_fetches_over_ssh(self):
        self.assertEqual(self.main("--push", "app", "LeadDev"), 0)
        self.assertIn("pushes as leaddevwidgetagent[bot]", self.out[-1])
        self.assertEqual(self.read(self.wt, "remote", "get-url", "--push",
                                   "origin").strip(), self.URL)
        self.assertEqual(self.read(self.wt, "remote", "get-url",
                                   "origin").strip(), SSH)

    def test_only_this_worktree_changes(self):
        self.main("--push", "app", "LeadDev")
        self.assertEqual(self.read(self.main_wt, "remote", "get-url",
                                   "--push", "origin").strip(), SSH)
        shared = self.read(self.main_wt, "config", "--local", "--list")
        self.assertNotIn("pushurl", shared)
        self.assertNotIn("credential.", shared)

    def test_running_again_changes_nothing(self):
        path = os.path.join(self.main_wt, ".git", "worktrees", "wt",
                            "config.worktree")
        self.main("--push", "app", "LeadDev")
        with open(path) as handle:
            first = handle.read()
        self.assertEqual(self.main("--push", "app", "LeadDev"), 0)
        with open(path) as handle:
            self.assertEqual(handle.read(), first)

    def test_git_asks_only_our_helper_even_with_a_global_one(self):
        other = self.script("other", "someone", "their-secret")
        for key in ("credential.helper", tool.HELPER_KEY):
            self.r.git(self.r.dir, "config", "--global", key, "!" + other)
        self.assertIn("username=someone", self.fill(self.wt).stdout)
        self.main("--push", "app", "LeadDev")
        got = self.fill(self.wt).stdout
        self.assertIn("username=x-access-token\npassword=ghs_ours", got)
        self.assertNotIn("someone", got)

    def test_check_before_and_after(self):
        self.assertEqual(self.main("--check", "--push", "app", "LeadDev"), 1)
        self.main("LeadDev")
        self.assertEqual(self.main("--check", "--push", "app", "LeadDev"), 1)
        self.assertIn("does not push as LeadDev's App", self.err[0])
        self.main("--push", "app", "LeadDev")
        self.assertEqual(self.main("--check", "--push", "app", "LeadDev"), 0)

    def test_a_stale_helper_is_replaced_not_added_to(self):
        self.main("LeadDev")
        self.r.git(self.wt, "config", "--worktree", "--add", tool.HELPER_KEY,
                   "!old")
        self.main("--push", "app", "LeadDev")
        self.assertEqual(self.read(self.wt, "config", "--worktree",
                                   "--get-all", tool.HELPER_KEY),
                         "\n" + self.helper + "\n")

    def test_a_matching_value_at_another_scope_is_not_this_worktree_s(self):
        self.main("LeadDev")
        for key, values in tool.push_settings("example/widget",
                                              self.helper).items():
            for value in values:
                self.r.git(self.main_wt, "config", "--local", "--add", key,
                           value)
        self.assertEqual(self.main("--check", "--push", "app", "LeadDev"), 1)
        self.assertEqual(self.main("--push", "app", "LeadDev"), 0)

    def test_the_default_helper_is_the_one_beside_this_tool(self):
        self.assertEqual(tool.HELPER, "!" + os.path.join(
            os.path.dirname(os.path.realpath(tool.__file__)),
            "forge-git-credential"))


if __name__ == "__main__":
    unittest.main()
