"""Tests for agent_forge/union.py: real git, in throwaway repositories,
since only git can say what its merge of two commits is."""

import os
import unittest

from agent_forge import union
from tests.helpers import Repo


class Repository:
    """One repository with `main` and a `feature` branch off it."""

    def __init__(self, test, files):
        self.repo = Repo(test)
        self.repo.env.update(GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                             GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
        self.path = os.path.join(self.repo.dir, "work")
        self.git(self.repo.dir, "init", "-q", "-b", "main", self.path)
        self.run = self.repo.run(self.path)
        self.commit(files, "base")
        self.git(self.path, "branch", "feature")

    def git(self, cwd, *args):
        return self.repo.git(cwd, *args).stdout.strip()

    def commit(self, files, message, branch=None):
        """Commit `files` ({path: text, or None to delete}) on `branch`
        (default the current one), and return the commit."""
        if branch:
            self.git(self.path, "checkout", "-q", branch)
        for name, text in files.items():
            where = os.path.join(self.path, name)
            if text is None:
                self.git(self.path, "rm", "-q", name)
                continue
            os.makedirs(os.path.dirname(where), exist_ok=True)
            with open(where, "w", newline="") as handle:
                handle.write(text)
            self.git(self.path, "add", name)
        self.git(self.path, "commit", "-q", "-m", message)
        return self.git(self.path, "rev-parse", "HEAD")

    def sides(self, feature, main):
        """Commit `feature` on the feature branch and `main` on main, and
        return the two commits."""
        head = self.commit(feature, "feature", "feature")
        tip = self.commit(main, "main", "main")
        return head, tip

    def show(self, tree, path):
        return self.git(self.path, "cat-file", "blob", "%s:%s" % (tree, path))

    def resolve(self, head, tip, globs):
        return union.resolve(self.path, head, tip, globs, self.run)


LIST = "# List\n\n* one\n* two\n\n## After\n"


class Resolve(unittest.TestCase):

    def setUp(self):
        self.r = Repository(self, {"LIST.md": LIST, "code.txt": "a\nb\nc\n"})

    def test_a_pure_addition_conflict_keeps_both_sides_head_first(self):
        head, tip = self.r.sides(
            {"LIST.md": LIST.replace("* two\n", "* two\n* feature\n")},
            {"LIST.md": LIST.replace("* two\n", "* two\n* main 1\n* main 2\n")})
        got = self.r.resolve(head, tip, ["*.md"])
        self.assertTrue(got)
        self.assertFalse(got.clean)
        self.assertEqual(self.r.show(got.tree, "LIST.md"),
                         "# List\n\n* one\n* two\n* feature\n* main 1\n"
                         "* main 2\n\n## After")
        self.assertEqual(got.files, [("LIST.md", [(["* feature"],
                                                   ["* main 1", "* main 2"])])])
        # The rest of the tree is git's own merge.
        self.assertEqual(self.r.show(got.tree, "code.txt"), "a\nb\nc")

    def test_two_hunks_in_one_file(self):
        head, tip = self.r.sides(
            {"LIST.md": "# List\n\nF1\n* one\n* two\nF2\n\n## After\n"},
            {"LIST.md": "# List\n\nM1\n* one\n* two\nM2\n\n## After\n"})
        got = self.r.resolve(head, tip, ["LIST.md"])
        self.assertEqual(self.r.show(got.tree, "LIST.md"),
                         "# List\n\nF1\nM1\n* one\n* two\nF2\nM2\n\n## After")
        self.assertEqual(got.files, [("LIST.md", [(["F1"], ["M1"]),
                                                  (["F2"], ["M2"])])])

    def test_a_file_both_sides_added_is_resolved(self):
        head, tip = self.r.sides({"new.md": "feature\nshared\n"},
                                 {"new.md": "main\nshared\n"})
        got = self.r.resolve(head, tip, ["*.md"])
        self.assertEqual(self.r.show(got.tree, "new.md"),
                         "feature\nmain\nshared")

    def test_nothing_in_the_repository_changes(self):
        head, tip = self.r.sides({"LIST.md": LIST + "F\n"},
                                 {"LIST.md": LIST + "M\n"})
        before = self.r.git(self.r.path, "status", "--porcelain=2",
                            "--branch")
        self.assertTrue(self.r.resolve(head, tip, ["LIST.md"]))
        self.assertEqual(self.r.git(self.r.path, "status", "--porcelain=2",
                                    "--branch"), before)
        self.assertEqual(self.r.git(self.r.path, "rev-parse", "HEAD"), tip)

    def test_the_same_inputs_give_the_same_tree(self):
        head, tip = self.r.sides({"LIST.md": LIST + "F\n"},
                                 {"LIST.md": LIST + "M\n"})
        self.assertEqual(self.r.resolve(head, tip, ["LIST.md"]).tree,
                         self.r.resolve(head, tip, ["LIST.md"]).tree)

    def test_a_clean_merge_is_git_own(self):
        head, tip = self.r.sides({"a.txt": "a\n"}, {"b.txt": "b\n"})
        got = self.r.resolve(head, tip, [])
        self.assertTrue(got.clean)
        self.assertEqual(got.tree, self.r.git(
            self.r.path, "merge-tree", "--write-tree", head, tip))

    def test_a_changed_line_in_a_matching_file_is_not_resolved(self):
        head, tip = self.r.sides(
            {"LIST.md": LIST.replace("* two\n", "* TWO\n")},
            {"LIST.md": LIST.replace("* two\n", "* two\n* main\n")})
        got = self.r.resolve(head, tip, ["*.md"])
        self.assertIsNone(got.tree)
        self.assertIn("changed or removed", got.reason)

    def test_a_removed_line_in_a_matching_file_is_not_resolved(self):
        head, tip = self.r.sides(
            {"LIST.md": LIST.replace("* two\n", "")},
            {"LIST.md": LIST.replace("* two\n", "* two\n* main\n")})
        self.assertIsNone(self.r.resolve(head, tip, ["*.md"]).tree)

    def test_one_real_conflict_anywhere_stops_it(self):
        head, tip = self.r.sides(
            {"LIST.md": LIST + "F\n", "code.txt": "a\nB\nc\n"},
            {"LIST.md": LIST + "M\n", "code.txt": "a\nb!\nc\n"})
        got = self.r.resolve(head, tip, ["*.md"])
        self.assertIsNone(got.tree)
        self.assertEqual(got.reason, "code.txt conflicts, and no glob names it")

    def test_a_file_no_glob_names_is_not_resolved(self):
        head, tip = self.r.sides({"LIST.md": LIST + "F\n"},
                                 {"LIST.md": LIST + "M\n"})
        self.assertIsNone(self.r.resolve(head, tip, ["docs/*"]).tree)

    def test_without_globs_a_conflict_is_not_resolved(self):
        head, tip = self.r.sides({"LIST.md": LIST + "F\n"},
                                 {"LIST.md": LIST + "M\n"})
        got = self.r.resolve(head, tip, [])
        self.assertIsNone(got.tree)
        self.assertEqual(got.reason, "no globs are configured")

    def test_a_modify_delete_conflict_is_not_resolved(self):
        head, tip = self.r.sides({"LIST.md": LIST + "F\n"},
                                 {"LIST.md": None})
        got = self.r.resolve(head, tip, ["*"])
        self.assertIsNone(got.tree)
        self.assertIn("modify/delete", got.reason)

    def test_carriage_returns_are_not_resolved(self):
        r = Repository(self, {"crlf.md": "a\r\nb\r\n"})
        head, tip = r.sides({"crlf.md": "a\r\nb\r\nF\r\n"},
                            {"crlf.md": "a\r\nb\r\nM\r\n"})
        got = r.resolve(head, tip, ["*.md"])
        self.assertIsNone(got.tree)
        self.assertIn("carriage returns", got.reason)


class UnionText(unittest.TestCase):

    def text(self, *lines):
        return "".join(line + "\n" for line in lines)

    def test_an_empty_base_is_both_sides(self):
        got = union.union_text(self.text(
            "x", "<<<<<<< H", "h", "||||||| B", "=======", "m", ">>>>>>> M",
            "y"), "H", "M")
        self.assertEqual(got, ("x\nh\nm\ny\n", [(["h"], ["m"])]))

    def test_a_longer_marker(self):
        got = union.union_text(self.text(
            "<<<<<<<<<< H", "h", "|||||||||| B", "==========", "m",
            ">>>>>>>>>> M"), "H", "M")
        self.assertEqual(got[0], "h\nm\n")

    def test_lines_like_markers_with_other_labels_are_text(self):
        got = union.union_text(self.text(
            "<<<<<<< other", "=======", ">>>>>>> other",
            "<<<<<<< H", "h", "||||||| B", "=======", "m", ">>>>>>> M"),
            "H", "M")
        self.assertEqual(got, (self.text(
            "<<<<<<< other", "=======", ">>>>>>> other", "h", "m"),
            [(["h"], ["m"])]))

    def test_a_base_with_lines_is_refused(self):
        self.assertEqual(union.union_text(self.text(
            "<<<<<<< H", "h", "||||||| B", "b", "=======", "m", ">>>>>>> M"),
            "H", "M"), (None, "a conflict where a line was changed or "
                              "removed"))

    def test_no_base_section_is_refused(self):
        self.assertEqual(union.union_text(self.text(
            "<<<<<<< H", "h", "=======", "m", ">>>>>>> M"), "H", "M")[0],
            None)

    def test_a_conflict_that_never_ends_is_refused(self):
        self.assertEqual(union.union_text(self.text(
            "<<<<<<< H", "h", "||||||| B", "=======", "m"), "H", "M"),
            (None, "a conflict that never ends"))

    def test_no_markers_is_refused(self):
        self.assertEqual(union.union_text("x\n", "H", "M"),
                         (None, "no conflict markers"))


class Globs(unittest.TestCase):

    def test_read_from_the_merge_section(self):
        self.assertEqual(union.globs({"resolve": {"union": ["a", "b/*"]}}),
                         ("a", "b/*"))
        self.assertEqual(union.globs({"resolve": [{"paths": ["x"]}]}), ())
        self.assertEqual(union.globs({}), ())

    def test_a_star_matches_across_directories(self):
        self.assertTrue(union.matches("docs/a/b.md", ["docs/*.md"]))
        self.assertFalse(union.matches("README.md", ["docs/*.md"]))


class Summary(unittest.TestCase):

    def test_each_file_and_side(self):
        text = union.summary([("a.md", [(["x"], ["y", "z"])])], "this branch",
                             "`main`")
        self.assertEqual(text, "`a.md`: 1 line from this branch, then 2 lines "
                               "from `main`:\n\n```diff\n+ x\n+ y\n+ z\n```")

    def test_long_sides_are_cut(self):
        text = union.summary([("a.md", [(["x"] * 3, ["y"])])], "h", "m",
                             limit=2)
        self.assertIn("+ x\n+ x\n  ... and 1 more\n+ y", text)

    def test_the_fence_outlasts_the_lines(self):
        text = union.summary([("a.md", [(["````"], ["y"])])], "h", "m")
        self.assertIn("`````diff\n", text)
        self.assertTrue(text.endswith("\n`````"))


class ParseMergeTree(unittest.TestCase):

    def test_stages_and_messages(self):
        out = ("tree\x00100644 b 1\tf\x00100644 o 2\tf\x00100644 t 3\tf\x00\x00"
               "1\x00f\x00Auto-merging\x00Auto-merging f\n\x00"
               "1\x00f\x00CONFLICT (contents)\x00CONFLICT (content): in f\n\x00")
        self.assertEqual(union.parse_merge_tree(out), (
            "tree", {"f": {1: ("100644", "b"), 2: ("100644", "o"),
                           3: ("100644", "t")}},
            [("Auto-merging", ["f"]), ("CONFLICT (contents)", ["f"])]))


if __name__ == "__main__":
    unittest.main()
