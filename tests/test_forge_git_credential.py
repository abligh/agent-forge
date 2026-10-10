"""Tests for bin/forge-git-credential."""
import io
import os
import unittest

from tests.helpers import Repo, done, load_tool

tool = load_tool("forge-git-credential")
GET = "protocol=https\nhost=github.com\n\n"


def stamped(name="Reviewer", ext="true"):
    def run(args, **_):
        if args[-1] == "extensions.worktreeConfig":
            return done(args, 0, ext + "\n")
        return done(args, 0 if name else 1, (name or "") + "\n")
    return run


class Minter:
    def __init__(self, token="ghs_x", why=None):
        self.token, self.why, self.calls = token, why, []

    def __call__(self, agent):
        self.calls.append(agent)
        return (self.token, None) if self.token else (None, self.why)


def call(action="get", request=GET, run=None, minter=None):
    out, err = io.StringIO(), io.StringIO()
    import contextlib
    with contextlib.redirect_stderr(err):
        rc = tool.main([action], io.StringIO(request), out,
                       run or stamped(), minter or Minter())
    return rc, out.getvalue(), err.getvalue()


class Get(unittest.TestCase):

    def test_a_stamped_worktree_gets_the_minted_token(self):
        minter = Minter()
        self.assertEqual(call(minter=minter)[:2],
                         (0, "username=x-access-token\npassword=ghs_x\n"))
        self.assertEqual(minter.calls, ["Reviewer"])

    def test_extra_attributes_and_no_blank_line_are_fine(self):
        rc, out, _ = call(request="protocol=https\nhost=github.com\n"
                                  "path=o/r.git\nwwwauth[]=Basic")
        self.assertEqual(rc, 0)
        self.assertIn("password=ghs_x", out)


class NoCredential(unittest.TestCase):

    def test_no_worktree_name_is_refused_without_minting(self):
        minter = Minter()
        rc, out, err = call(run=stamped(None), minter=minter)
        self.assertEqual((rc, out, minter.calls), (1, "", []))
        self.assertIn("no user.name at worktree scope", err)

    def test_worktree_config_off_is_refused(self):
        rc, _, err = call(run=stamped(ext="false"))
        self.assertEqual(rc, 1)
        self.assertIn("extensions.worktreeConfig is off", err)

    def test_a_failed_mint_says_why_and_answers_nothing(self):
        rc, out, err = call(minter=Minter(None, "the key belongs to x"))
        self.assertEqual((rc, out), (1, ""))
        self.assertIn("no push credential for Reviewer: the key belongs to x",
                      err)


class Ignored(unittest.TestCase):

    def test_store_and_erase_do_nothing(self):
        for action in ("store", "erase"):
            self.assertEqual(call(action, minter=Minter(None))[:2], (0, ""))

    def test_another_host_or_protocol_gets_nothing(self):
        for request in ("protocol=https\nhost=gitlab.com\n",
                        "protocol=http\nhost=github.com\n"):
            self.assertEqual(call(request=request)[:2], (0, ""))

    def test_usage(self):
        self.assertEqual(call("fetch")[0], 2)


class Mint(unittest.TestCase):

    def test_a_configuration_problem_is_a_reason_not_a_crash(self):
        from agent_forge import config

        def load():
            raise config.ConfigError(["no configuration file"], "/x")
        self.assertEqual(tool.mint("A", load=load),
                         (None, "/x: no configuration file"))


class AgainstRealGit(unittest.TestCase):
    """The stamp is read the way git scopes it, not the way a fake does."""

    def setUp(self):
        self.r = Repo(self)
        self.main = os.path.join(self.r.dir, "main")
        self.wt = os.path.join(self.r.dir, "wt")
        self.r.git(self.r.dir, "init", "-q", "-b", "main", self.main)
        self.r.git(self.main, "-c", "user.name=x", "-c", "user.email=x@x",
                   "commit", "-q", "--allow-empty", "-m", "root")
        self.r.git(self.main, "worktree", "add", "-q", self.wt)

    def agent_in(self, cwd):
        return tool.stamped_agent(self.r.run(cwd))

    def test_a_worktree_stamp_is_read(self):
        self.r.git(self.wt, "config", "extensions.worktreeConfig", "true")
        self.r.git(self.wt, "config", "--worktree", "user.name", "Reviewer")
        self.assertEqual(self.agent_in(self.wt), ("Reviewer", None))

    def test_a_repository_scope_name_is_not_a_stamp(self):
        self.r.git(self.main, "config", "extensions.worktreeConfig", "true")
        self.r.git(self.main, "config", "user.name", "Reviewer")
        agent, why = self.agent_in(self.wt)
        self.assertIsNone(agent)
        self.assertIn("no user.name at worktree scope", why)

    def test_without_the_extension_nothing_is_a_stamp(self):
        self.r.git(self.wt, "config", "user.name", "Reviewer")
        self.assertIn("extensions.worktreeConfig is off",
                      self.agent_in(self.wt)[1])

    def test_another_worktree_s_stamp_is_not_this_one_s(self):
        self.r.git(self.main, "config", "extensions.worktreeConfig", "true")
        self.r.git(self.main, "config", "--worktree", "user.name", "Reviewer")
        self.assertEqual(self.agent_in(self.main), ("Reviewer", None))
        self.assertIsNone(self.agent_in(self.wt)[0])


if __name__ == "__main__":
    unittest.main()
