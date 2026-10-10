"""Tests for bin/forge-approval-scope.

Real git repositories in a temporary directory, because every predicate
here is a git question (was this file approved AT THIS COMMIT, what did
this commit add, what does its message say), and a fixture that faked
git would be testing the fake.

The case that decides the design is
`test_a_draft_before_approval_is_not_flagged`: a document approved later
must not make its own earlier drafting retrospectively suspect, or every
design would fail on the commits that wrote it.
"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_forge import config, proc
from tests.helpers import example, load_tool

tool = load_tool("forge-approval-scope")
ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
           GIT_CONFIG_GLOBAL=os.devnull)


def setUpModule():
    tool.RUN[0] = lambda args, cwd=None, **kw: proc.sh(args, cwd=cwd,
                                                       env=ENV)
    tool.DESIGNS[0] = "designs"


APPROVED = "* Status: approved (A. Owner, 2026-09-09)\n"
IMPLEMENTED = "* Status: implemented. Approved by A. Owner, 2026-09-09\n"


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.repo = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        (self.repo / "designs").mkdir()

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=ENV,
                              capture_output=True, text=True)

    def commit(self, text, message, path="designs/0044-x.md"):
        target = self.repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD").stdout.strip()

    def survey(self, rng):
        return tool.survey(rng, cwd=str(self.repo))


class Scope(Fixture):
    def test_unmarked_text_after_approval_is_flagged(self):
        base = self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# doc\n" + APPROVED + "\nnew paragraph\n", "add detail")
        rows = self.survey("%s..HEAD" % base)
        self.assertEqual([r[3] for r in rows], [False])

    def test_an_emphasized_status_is_still_approved(self):
        """`Status: **approved**` must not read as a draft, or unmarked
        text added to it goes unchecked."""
        for status in ("* Status: **approved** — Ada, 2026-09-01\n",
                       "* Status: _implemented_, 2026-09-10\n"):
            with self.subTest(status=status):
                base = self.commit("# doc\n" + status, "approve")
                self.commit("# doc\n" + status + "\nnew paragraph\n",
                            "add detail")
                rows = self.survey("%s..HEAD" % base)
                self.assertEqual([r[3] for r in rows], [False])

    def test_a_bold_status_key_is_still_approved(self):
        """`* **Status**: approved` must not read as a draft either."""
        for status in ("* **Status**: approved (A. Owner, 2026-10-02,\n",
                       "* __Status__: implemented. Approved by A\n"):
            with self.subTest(status=status):
                base = self.commit("# doc\n" + status, "approve")
                self.commit("# doc\n" + status + "\nnew paragraph\n",
                            "add detail")
                rows = self.survey("%s..HEAD" % base)
                self.assertEqual([r[3] for r in rows], [False])

    def test_approving_a_bold_status_key_is_not_an_amendment(self):
        """A commit that only records the approval under a bold key is
        not unmarked text."""
        base = self.commit("# doc\n* **Status**: proposed\n* **Author**: x\n"
                           "\nbody\n", "draft")
        self.commit("# doc\n* **Status**: approved (A. Owner, 2026-10-02,\n"
                    "  [#3](https://example.invalid/3))\n"
                    "* **Author**: x\n\nbody\n",
                    "designs/0095: approved")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_a_bold_other_status_is_not_approved(self):
        base = self.commit("# doc\n* **Status**: proposed\n", "draft")
        self.commit("# doc\n* **Status**: proposed\n\nmore\n", "more")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_an_emphasized_other_status_is_not_approved(self):
        """`Status: **parked — ...**` stays a non-approval: the emphasis
        is skipped, not the word."""
        base = self.commit("# doc\n* Status: **parked — gathering.**\n",
                           "park")
        self.commit("# doc\n* Status: **parked — gathering.**\n\nmore\n",
                    "more")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_the_commit_message_can_say_it(self):
        base = self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# doc\n" + APPROVED + "\nnew paragraph\n",
                    "designs/0044: amend after approval")
        self.assertEqual([r[3] for r in self.survey("%s..HEAD" % base)],
                         [True])

    def test_the_added_text_can_say_it(self):
        """"Amended 2026-09-10, after approval" in the text is enough."""
        base = self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# doc\n" + APPROVED + "\n**Amended 2026-09-10.** x\n",
                    "designs/0044: correct the thing")
        self.assertEqual([r[3] for r in self.survey("%s..HEAD" % base)],
                         [True])

    def test_a_draft_before_approval_is_not_flagged(self):
        """The case that decides the design: a document approved later must
        not make its own drafting retrospectively suspect."""
        base = self.commit("# doc\n* Status: proposed\n", "draft")
        self.commit("# doc\n* Status: proposed\n\nmore drafting\n", "more")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_the_approval_itself_is_not_an_amendment(self):
        """A proposed design, then one commit that only changes its status
        to approved, wrapped over several lines."""
        base = self.commit("# doc\n* Status: proposed\n\nbody\n", "draft")
        self.commit("# doc\n* Status: approved (A. Owner, 2026-09-10) — on\n"
                    "  [#4](https://example.invalid/4),\n"
                    "  *\"designs/0050 approved\"*\n\nbody\n",
                    "designs/0050: record the approval")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_text_added_with_the_approval_still_needs_marking(self):
        """Only the Status block is the approval, so a paragraph riding in
        with it is still judged."""
        base = self.commit("# doc\n* Status: proposed\n\nbody\n", "draft")
        self.commit("# doc\n* Status: approved (A. Owner, 2026-09-10)\n\n"
                    "body\n\nnew paragraph\n",
                    "designs/0050: record the approval")
        rows = self.survey("%s..HEAD" % base)
        self.assertEqual([(r[2], r[3]) for r in rows], [(1, False)])

    def test_a_status_edit_after_approval_is_still_judged(self):
        """The exemption needs the design to be unapproved at the parent.
        Editing an approved design's Status block later -- a supersession
        note, say -- is text added to an approved design, and must be
        named as an amendment."""
        base = self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# doc\n* Status: approved (A. Owner, 2026-09-09); "
                    "superseded by 0099\n", "note the successor")
        rows = self.survey("%s..HEAD" % base)
        self.assertEqual([(r[2], r[3]) for r in rows], [(1, False)])

    def test_the_commit_after_the_approval_is_still_judged(self):
        """The exemption is for the approving commit only."""
        base = self.commit("# doc\n* Status: proposed\n", "draft")
        self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# doc\n" + APPROVED + "\nnew paragraph\n", "add detail")
        rows = self.survey("%s..HEAD" % base)
        self.assertEqual([r[3] for r in rows], [False])

    def test_unmarked_text_in_an_implemented_design_is_flagged(self):
        """`implemented. Approved by ...` counts as approved."""
        base = self.commit("# doc\n" + IMPLEMENTED, "ship")
        self.commit("# doc\n" + IMPLEMENTED + "\nnew paragraph\n", "add detail")
        rows = self.survey("%s..HEAD" % base)
        self.assertEqual([(r[2], r[3]) for r in rows], [(1, False)])

    def test_marking_a_design_implemented_is_not_an_amendment(self):
        """§7: recording what shipped -- the status change and its
        `Implemented:` field, wrapped lines included -- is permitted."""
        base = self.commit("# doc\n" + APPROVED + "* Author: x\n\nbody\n",
                           "approve")
        self.commit("# doc\n" + IMPLEMENTED
                    + "* Implemented: 2026-09-11. Steps 1 to 3 in #1;\n"
                    "  step 4 deviated, see below.\n* Author: x\n\nbody\n",
                    "designs/0044: record what shipped")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_text_carried_in_by_the_implemented_commit_is_judged(self):
        """Only the implementing commit's record of what shipped is
        exempt, not everything it adds."""
        base = self.commit("# doc\n" + APPROVED + "\nbody\n", "approve")
        self.commit("# doc\n" + IMPLEMENTED + "* Implemented: 2026-09-11\n"
                    "\nbody\n\nnew paragraph\n",
                    "designs/0044: record what shipped")
        rows = self.survey("%s..HEAD" % base)
        self.assertEqual([(r[2], r[3]) for r in rows], [(1, False)])

    def test_updating_the_implemented_field_later_is_not_an_amendment(self):
        base = self.commit("# doc\n" + IMPLEMENTED
                           + "* Implemented: 2026-09-11.\n", "ship")
        self.commit("# doc\n" + IMPLEMENTED + "* Implemented: 2026-09-11. "
                    "Live from 18:59Z\n  (#5).\n", "record the roll")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_a_status_edit_to_an_implemented_design_is_still_judged(self):
        """The Status exemption needs the status to change at this commit,
        as for an approved design."""
        base = self.commit("# doc\n" + IMPLEMENTED, "ship")
        self.commit("# doc\n* Status: implemented. Approved by A. Owner, "
                    "2026-09-09; superseded in part by 0099\n", "note it")
        rows = self.survey("%s..HEAD" % base)
        self.assertEqual([(r[2], r[3]) for r in rows], [(1, False)])

    def test_whitespace_only_changes_are_not_text(self):
        base = self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# doc\n" + APPROVED + "\n\n\n", "reflow")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_files_outside_designs_are_not_its_business(self):
        base = self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# guide\n", "guide", path="guides/x.md")
        self.assertEqual(self.survey("%s..HEAD" % base), [])

    def test_an_unreadable_range_is_none_not_empty(self):
        """"Cannot read" must not report as "nothing to report"."""
        self.assertIsNone(self.survey("no-such-ref..HEAD"))

    def test_the_population_is_reported_separately(self):
        """A branch with commits and no design must not read as an empty
        range: both would print "0 touch an approved design"."""
        base = self.commit("# doc\n" + APPROVED, "approve")
        self.commit("# guide\n", "guide", path="guides/x.md")
        rng = "%s..HEAD" % base
        self.assertEqual(tool.examined(rng, cwd=str(self.repo)), 1)
        self.assertEqual(self.survey(rng), [])

    def test_an_empty_range_examines_nothing(self):
        base = self.commit("# doc\n" + APPROVED, "approve")
        rng = "%s..%s" % (base, base)
        self.assertEqual(tool.examined(rng, cwd=str(self.repo)), 0)

    def test_an_uncountable_range_is_none(self):
        self.assertIsNone(
            tool.examined("no-such-ref..HEAD", cwd=str(self.repo)))


class History(Fixture):
    """--history names the approving commit and every later one."""

    def history(self):
        return tool.history("designs/0044-x.md", cwd=str(self.repo))

    def test_a_bold_status_key_is_found(self):
        """A literal search for `Status: approved` would miss
        `* **Status**: approved`."""
        self.commit("# doc\n* **Status**: proposed\n", "draft")
        approval = self.commit("# doc\n* **Status**: approved (A, 2026-10-02)\n",
                               "approve")
        later = self.commit("# doc\n* **Status**: approved (A, 2026-10-02)\n"
                            "\nmore\n", "amend it")
        got, commits = self.history()
        self.assertEqual(got, approval)
        self.assertEqual([c.split()[0] for c in commits], [later])

    def test_a_plain_status_key_is_still_found(self):
        self.commit("# doc\n* Status: proposed\n", "draft")
        approval = self.commit("# doc\n" + APPROVED, "approve")
        got, commits = self.history()
        self.assertEqual((got, commits), (approval, []))

    def test_a_design_never_approved_has_no_history(self):
        self.commit("# doc\n* **Status**: proposed\n", "draft")
        self.assertIsNone(self.history())


class ShallowTest(unittest.TestCase):
    """A shallow clone makes this tool fabricate, not under-report.

    Against a grafted history every commit below the graft reads as
    added on the branch. A long list of findings reads as the tool
    working, which is why refusing beats reporting.
    """

    def setUp(self):
        self.mod = tool
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        # Everything lives under one TemporaryDirectory, so concurrent
        # `make test` runs never race for the same paths.
        root = Path(self.tmp.name)
        self.dir = root / "repo"
        self.scratch = root / "scratch"
        self.dir.mkdir()
        self.scratch.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.dir)],
                       check=True, env=ENV)
        for key, value in (("user.email", "t@example.com"),
                           ("user.name", "T")):
            subprocess.run(["git", "-C", str(self.dir), "config", key, value],
                           check=True, env=ENV)
        (self.dir / "f").write_text("x\n")
        subprocess.run(["git", "-C", str(self.dir), "add", "-A"], check=True,
                       env=ENV)
        subprocess.run(["git", "-C", str(self.dir), "commit", "-qm", "c"],
                       check=True, env=ENV)

    def test_a_normal_clone_is_not_shallow(self):
        self.assertIs(self.mod.shallow(cwd=str(self.dir)), False)

    def test_a_shallow_clone_is_detected(self):
        dest = self.scratch / "shallow"
        subprocess.run(["git", "clone", "-q", "--depth", "1",
                        "file://%s" % self.dir, str(dest)], check=True,
                       env=ENV)
        self.assertIs(self.mod.shallow(cwd=str(dest)), True)

    def test_not_a_repository_is_none_not_false(self):
        """Unknown must not read as "definitely fine"."""
        notrepo = self.scratch / "notrepo"
        notrepo.mkdir()
        self.assertIsNone(self.mod.shallow(cwd=str(notrepo)))


class Main(unittest.TestCase):
    """The command line, with the designs directory from configuration."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = self.tmp.name

        def git(*args):
            return subprocess.run(["git", *args], cwd=self.repo, env=ENV,
                                  capture_output=True, text=True, check=True)
        self.git = git
        git("init", "-q", "-b", "trunk")
        git("config", "user.email", "t@t")
        git("config", "user.name", "t")
        os.makedirs(os.path.join(self.repo, "rfcs"))
        self.write("rfcs/1.md", "# x\n" + APPROVED)
        git("add", "-A")
        git("commit", "-qm", "approve")
        git("branch", "base")
        self.write("rfcs/1.md", "# x\n" + APPROVED + "\nsneaked in\n")
        git("commit", "-qam", "more")

    def write(self, path, text):
        with open(os.path.join(self.repo, path), "w") as handle:
            handle.write(text)

    def main(self, *argv, designs="rfcs"):
        def load():
            p = config.Project(dict(example(), default_branch="trunk"))
            p.data["docs"] = {"designs_dir": designs}
            return p
        out, err = [], []
        run = lambda args, cwd=None, **kw: proc.sh(args, cwd=cwd or self.repo,
                                                   env=ENV)
        rc = tool.main(list(argv), run, load, out.append, err.append)
        self.addCleanup(lambda: tool.DESIGNS.__setitem__(0, "designs"))
        self.addCleanup(lambda: tool.RUN.__setitem__(
            0, lambda args, cwd=None, **kw: proc.sh(args, cwd=cwd, env=ENV)))
        return rc, out, err

    def test_the_designs_directory_comes_from_configuration(self):
        rc, out, err = self.main("base..HEAD")
        self.assertEqual(rc, 1)
        self.assertIn("rfcs/1.md", err[0])
        rc, out, _ = self.main("base..HEAD", designs="designs")
        self.assertEqual(rc, 0)
        self.assertIn("1 commit(s) examined, 0 touch", out[0])

    def test_the_default_range_is_the_default_branch(self):
        rc, _, err = self.main()
        self.assertEqual(rc, 2)
        self.assertIn("origin/trunk..HEAD", err[0])


if __name__ == "__main__":
    unittest.main()
