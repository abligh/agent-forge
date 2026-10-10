"""Tests for bin/forge-commit-identity."""
import os
import unittest

from agent_forge import config
from tests.helpers import EXAMPLE, Repo, done, example, load_tool

tool = load_tool("forge-commit-identity")
HOST = ("Anonymous Agent", "agent@build.example.org")
BOT = ("LeadDev", "4242+leaddevwidgetagent[bot]@users.noreply.github.com")
HUMAN = ("Ada Owner", "ada@example.org")


def project():
    return config.Project(example(), str(EXAMPLE))


def bad(name, email):
    p = project()
    return tool.host_identity(p, name, email) or \
        tool.borrowed_agent(p, name, email)


class HostIdentity(unittest.TestCase):

    def test_refused_as_author_or_committer(self):
        rows = [("a" * 40,) + HOST + BOT, ("b" * 40,) + BOT + HOST]
        self.assertEqual([(s[0], w) for s, w, *_ in
                          tool.unattributed(project(), rows)],
                         [("a", "author"), ("b", "committer")])

    def test_a_bot_and_a_human_pass(self):
        self.assertEqual(tool.unattributed(
            project(), [("c" * 40,) + BOT + HUMAN]), [])

    def test_the_rule_is_the_domain_not_the_one_address(self):
        self.assertTrue(bad("Someone", "else@build.example.org"))

    def test_the_name_alone_is_refused_any_case(self):
        self.assertTrue(bad("anonymous AGENT", "x@example.org"))
        self.assertTrue(bad("X", "AGENT@BUILD.EXAMPLE.ORG"))

    def test_a_lookalike_domain_is_not_the_reserved_one(self):
        self.assertFalse(bad("X", "x@build.example.org.evil"))

    def test_with_no_identity_section_only_borrowing_is_judged(self):
        data = example()
        del data["identity"]
        p = config.Project(data)
        self.assertFalse(tool.host_identity(p, *HOST))


class Borrowed(unittest.TestCase):

    def test_the_agents_own_stamp_passes(self):
        self.assertFalse(bad(*BOT))

    def test_a_shared_config_name_is_refused(self):
        self.assertEqual(bad("Reviewer (scratch)", "reviewer@localhost"),
                         "Reviewer")

    def test_the_leading_word_must_match_exactly(self):
        self.assertEqual(bad("LeadDev-scratch", "x@y"), "LeadDev")
        self.assertFalse(bad("LeadDevs", "x@y"))
        self.assertEqual(bad("leaddev", "x@y"), "LeadDev")

    def test_the_stamp_is_the_whole_address(self):
        self.assertTrue(bad("LeadDev", "x" + BOT[1]))
        self.assertTrue(bad("LeadDev", BOT[1] + ".evil"))

    def test_a_named_slug_is_the_stamp(self):
        self.assertFalse(bad("Helper", "7+helperbot[bot]@users.noreply."
                                       "github.com"))
        self.assertTrue(bad("Helper", "7+helperwidgetagent[bot]@users."
                                      "noreply.github.com"))

    def test_people_and_bot_logins_are_not_judged(self):
        self.assertFalse(bad(*HUMAN))
        self.assertFalse(bad("leaddevwidgetagent[bot]", "x@y"))


class Commits(unittest.TestCase):

    def test_only_the_synthetic_merge_is_skipped(self):
        log = ("1\tA\ta@x\tA\ta@x\tMerge %s into %s\n"
               "2\tB\tb@x\tB\tb@x\tMerge remote-tracking branch 'origin/main'\n"
               "3\tC\tc@x\tC\tc@x\ta subject\twith a tab\n"
               % ("f" * 40, "e" * 40))
        rows = tool.commits("x..y", lambda a: done(a, 0, log))
        self.assertEqual([r[0] for r in rows], ["2", "3"])
        self.assertEqual(rows[1], ("3", "C", "c@x", "C", "c@x"))


class Main(unittest.TestCase):

    def main(self, log=None, rc=0):
        self.out, self.err = [], []
        return tool.main(["base..head"], lambda a: done(a, rc, log or ""),
                         project, self.out.append, self.err.append)

    def test_unattributed_is_1_named_with_the_remedy(self):
        self.assertEqual(self.main("%s\t%s\t%s\t%s\t%s\tx\n"
                                   % (("a" * 40,) + HOST + HOST)), 1)
        self.assertIn("aaaaaaaaaaaa author is Anonymous Agent", self.err[0])
        self.assertIn("forge-worktree-identity", self.err[-1])
        self.assertIn("1 commit(s) examined, 1 unattributed", self.out[0])

    def test_all_attributable_is_0(self):
        self.assertEqual(self.main("%s\t%s\t%s\t%s\t%s\tx\n"
                                   % (("a" * 40,) + BOT + HUMAN)), 0)

    def test_unreadable_or_empty_ranges_are_2(self):
        self.assertEqual(self.main(rc=128), 2)
        self.assertEqual(self.main(""), 2)
        self.assertEqual(self.main("1\tA\ta@x\tA\ta@x\tMerge %s into %s\n"
                                   % ("f" * 40, "e" * 40)), 2)

    def test_no_configuration_is_2(self):
        def load():
            raise config.ConfigError(["no configuration file"])
        self.assertEqual(tool.main(["a..b"], load=load, out=list().append,
                                   err=list().append), 2)


class AgainstRealGit(unittest.TestCase):

    def test_a_real_merge_under_the_host_identity_is_refused(self):
        r = Repo(self)
        repo = os.path.join(r.dir, "repo")
        r.git(r.dir, "init", "-q", "-b", "main", repo)
        ident = ["-c", "user.name=%s" % BOT[0], "-c", "user.email=%s" % BOT[1]]
        r.git(repo, *ident, "commit", "-q", "--allow-empty", "-m", "base")
        r.git(repo, "checkout", "-q", "-b", "side")
        r.git(repo, *ident, "commit", "-q", "--allow-empty", "-m", "side")
        r.git(repo, "checkout", "-q", "main")
        r.git(repo, *ident, "commit", "-q", "--allow-empty", "-m", "main")
        r.git(repo, "checkout", "-q", "side")
        r.git(repo, "-c", "user.name=%s" % HOST[0], "-c",
              "user.email=%s" % HOST[1], "merge", "-q", "--no-edit", "main")
        out, err = [], []
        rc = tool.main(["main..side"], r.run(repo), project, out.append,
                       err.append)
        self.assertEqual(rc, 1)
        self.assertIn("2 commit(s) examined, 1 unattributed", out[0])


if __name__ == "__main__":
    unittest.main()
