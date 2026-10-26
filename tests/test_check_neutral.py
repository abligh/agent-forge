"""Tests for tools/check-neutral. The words here are invented; the real
list lives outside the repository."""
import contextlib
import importlib.machinery
import io
import importlib.util
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
_loader = importlib.machinery.SourceFileLoader(
    "check_neutral", str(HERE.parent / "tools" / "check-neutral"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
cn = importlib.util.module_from_spec(_spec)
_loader.exec_module(cn)


def git(root, *args):
    subprocess.run(["git", "-C", root, *args], check=True, capture_output=True)


class Hits(unittest.TestCase):

    def test_any_case_anywhere_in_a_line(self):
        self.assertEqual(cn.hits("see Zorblax-cron and ~/zorblax\nclean\n",
                                 ["zorblax"]),
                         [(1, "zorblax"), (1, "zorblax")])

    def test_not_inside_a_longer_word(self):
        # "among" held a forbidden word, and a design couldn't say it.
        text = "zorbing\nZORB2 zorb_cron zorbEngineer x.zorb.y Zorb\nazorb\n"
        self.assertEqual(cn.hits(text, ["zorb"]),
                         [(2, "zorb")] * 5)

    def test_no_words_no_hits(self):
        self.assertEqual(cn.hits("anything", []), [])


class Repository(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "repo")
        os.mkdir(self.root)
        git(self.root, "init", "-q")
        self.words = os.path.join(self.tmp.name, "words")

    def write(self, name, text):
        path = os.path.join(self.root, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        git(self.root, "add", name)

    def run_check(self, *args, words="zorblax\n# a comment\n"):
        if words is not None:
            with open(self.words, "w") as handle:
                handle.write(words)
        with mock.patch.dict(os.environ, {"FORGE_FORBIDDEN_WORDS": self.words}), \
                contextlib.redirect_stdout(io.StringIO()), \
                contextlib.redirect_stderr(io.StringIO()):
            return cn.main(["--root", self.root, *args])

    def test_clean(self):
        self.write("a.md", "nothing to see\n")
        self.assertEqual(self.run_check(), 0)

    def test_a_hit_in_content(self):
        self.write("a.md", "built for Zorblax\n")
        self.assertEqual(self.run_check(), 1)

    def test_a_hit_in_a_path(self):
        self.write("tools/zorblax-merger", "x\n")
        self.assertEqual(self.run_check(), 1)

    def test_staged_checks_the_index_not_the_working_tree(self):
        self.write("a.md", "clean\n")
        with open(os.path.join(self.root, "a.md"), "w") as handle:
            handle.write("zorblax, unstaged\n")
        self.assertEqual(self.run_check("--staged"), 0)

    def test_no_list_checks_nothing_and_says_so(self):
        self.write("a.md", "zorblax\n")
        self.assertEqual(self.run_check(words=None), 0)

    def test_an_empty_list_refuses(self):
        self.write("a.md", "fine\n")
        self.assertEqual(self.run_check(words="# only a comment\n\n"), 2)


class ThisRepository(unittest.TestCase):
    """The real check, when this host has the list."""

    def test_this_repository_is_neutral(self):
        path = os.path.expanduser(os.environ.get(
            "FORGE_FORBIDDEN_WORDS", cn.DEFAULT_LIST))
        if cn.load_words(path) is None:
            self.skipTest("no forbidden-words list on this host")
        out = io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            rc = cn.main(["--root", str(HERE.parent)])
        self.assertEqual(rc, 0, out.getvalue())


if __name__ == "__main__":
    unittest.main()
