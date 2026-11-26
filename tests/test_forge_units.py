"""Tests for bin/forge-units (designs/0065).

Real git repositories in a temporary directory: what `select` answers
is a git question (what changed since the merge base, what the head's
tree holds), and a fake git would be testing the fake.
"""

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from agent_forge import config, proc
from tests.helpers import example, load_tool

tool = load_tool("forge-units")
ENV = dict(os.environ, GIT_CONFIG_NOSYSTEM="1",
           GIT_CONFIG_GLOBAL=os.devnull)

TABLE = """\
# what each test reads
everything tools/harness.py
placed tools
placed docs
unit tools/test_a.py tools/a.py docs/a/
unit tools/test_b.py tools/b*.py
unit tools/test_c.py -
"""


def run(args, cwd=None, **_):
    return proc.sh(args, cwd=cwd, env=ENV)


class Fixture(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.repo = Path(self._tmp.name)
        self.git("init", "-q", "-b", "main")
        self.git("config", "user.email", "t@t")
        self.git("config", "user.name", "t")
        self.write({"tools/units.txt": TABLE, "tools/harness.py": "",
                    "tools/a.py": "", "tools/b1.py": "", "tools/test_a.py": "",
                    "tools/test_b.py": "", "tools/test_c.py": "",
                    "docs/a/one.md": "", "src/main.c": ""})
        self.commit("base")
        self.git("branch", "base")
        self.units = {"table": "tools/units.txt", "glob": "tools/test_*.py"}

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.repo, env=ENV,
                              capture_output=True, text=True)

    def write(self, files):
        for name, text in files.items():
            target = self.repo / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text)

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)

    def change(self, files=None, remove=()):
        self.write(files or {})
        for name in remove:
            self.git("rm", "-q", name)
        self.commit("change")

    def load(self):
        data = example()
        data["units"] = {"tools": self.units}
        return config.Project(data, str(self.repo / config.CONFIG_NAME))

    def call(self, *argv):
        out, err = [], []
        code = tool.main(list(argv), run=run, load=self.load,
                         out=out.append, err=err.append)
        return code, out, err

    def select(self, *extra):
        return self.call("select", "--units", "tools", "--base", "base",
                         *extra)


class Select(Fixture):

    def test_a_changed_input_selects_its_unit_alone(self):
        self.change({"tools/a.py": "x"})
        self.assertEqual(self.select(), (0, ["tools/test_a.py"], []))

    def test_a_prefix_input_holds_the_files_below_it(self):
        self.change({"docs/a/two.md": "x"})
        self.assertEqual(self.select()[1], ["tools/test_a.py"])

    def test_a_glob_input_matches(self):
        self.change({"tools/b2.py": "x"})
        self.assertEqual(self.select()[1], ["tools/test_b.py"])

    def test_a_units_own_file_selects_it(self):
        self.change({"tools/test_c.py": "x"})
        self.assertEqual(self.select()[1], ["tools/test_c.py"])

    def test_an_everything_path_selects_all(self):
        self.change({"tools/harness.py": "x"})
        code, out, _ = self.select()
        self.assertEqual(code, 0)
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("ALL tools/harness.py changed"))

    def test_a_path_in_no_placed_directory_selects_all(self):
        self.change({"src/main.c": "x"})
        self.assertEqual(self.select()[1],
                         ["ALL src/main.c is in no placed directory"])

    def test_a_placed_path_no_input_holds_selects_nothing(self):
        self.change({"docs/other.md": "x"})
        self.assertEqual(self.select(), (0, [], []))

    def test_no_changes_select_nothing(self):
        self.assertEqual(self.select(), (0, [], []))

    def test_a_unit_with_no_entry_is_always_selected(self):
        self.change({"tools/test_d.py": "", "docs/other.md": "x"})
        self.assertEqual(self.select()[1], ["tools/test_d.py"])

    def test_a_rename_counts_by_its_old_name_too(self):
        self.git("mv", "tools/a.py", "docs/moved.py")
        self.commit("move")
        self.assertEqual(self.select()[1], ["tools/test_a.py"])

    def test_the_heads_table_is_read(self):
        self.change({"tools/units.txt": TABLE.replace(
            "tools/test_c.py -", "tools/test_c.py docs/other.md"),
            "docs/other.md": "x"})
        # The table is in tools, a placed directory, read by no entry.
        self.assertEqual(self.select()[1], ["tools/test_c.py"])

    def test_a_table_that_doesnt_parse_selects_all(self):
        self.change({"tools/units.txt": TABLE + "bogus line\n"})
        out = self.select()[1]
        self.assertEqual(len(out), 1)
        self.assertTrue(out[0].startswith("ALL the table doesn't parse"))

    def test_no_merge_base_selects_all(self):
        self.git("checkout", "-q", "--orphan", "other")
        self.commit("unrelated")
        code, out, _ = self.select()
        self.assertEqual(code, 0)
        self.assertTrue(out[0].startswith("ALL no merge base"))

    def test_an_unknown_table_says_all_and_exits_2(self):
        code, out, err = self.call("select", "--units", "nope",
                                   "--base", "base")
        self.assertEqual(code, 2)
        self.assertEqual(out, ["ALL no table to read"])
        self.assertIn("no units.nope", err[0])

    def test_why_says_what_selected_each_on_stderr(self):
        self.change({"tools/a.py": "x", "tools/b1.py": "x"})
        code, out, err = self.select("--why")
        self.assertEqual(out, ["tools/test_a.py", "tools/test_b.py"])
        self.assertEqual(err, ["tools/test_a.py: tools/a.py (input tools/a.py)",
                               "tools/test_b.py: tools/b1.py "
                               "(input tools/b*.py)",
                               "selected 2 of 3 (66%)"])

    def test_why_for_everything(self):
        self.change({"src/main.c": "x"})
        err = self.select("--why")[2]
        self.assertEqual(err, ["selected all: src/main.c is in no placed "
                               "directory"])

    def test_the_head_may_be_named(self):
        self.change({"tools/a.py": "x"})
        self.git("branch", "later")
        self.git("checkout", "-q", "base")
        self.assertEqual(self.select("--head", "later")[1],
                         ["tools/test_a.py"])


class Check(Fixture):

    def test_a_good_table_passes(self):
        code, out, err = self.call("check", "--units", "tools")
        self.assertEqual((code, err), (0, []))
        self.assertEqual(out, ["forge-units: 3 unit(s), 3 entries, "
                               "0 fault(s)"])

    def test_a_unit_with_no_entry_is_a_fault(self):
        self.write({"tools/test_d.py": ""})
        code, _, err = self.call("check", "--units", "tools")
        self.assertEqual(code, 1)
        self.assertEqual(err, ["tools/units.txt: tools/test_d.py has no "
                               "entry"])

    def test_an_entry_naming_no_unit_is_a_fault(self):
        (self.repo / "tools/test_c.py").unlink()
        code, _, err = self.call("check", "--units", "tools")
        self.assertEqual(code, 1)
        self.assertIn("tools/test_c.py's entry names no unit", err[0])

    def test_bad_lines_are_each_reported(self):
        self.write({"tools/units.txt": TABLE + "unit x\nplaced\nwhat y\n"})
        code, _, err = self.call("check", "--units", "tools")
        self.assertEqual(code, 1)
        self.assertEqual(len(err), 3)


class Parse(unittest.TestCase):

    def test_comments_and_blank_lines_are_skipped(self):
        table = tool.parse("# c\n\nunit t a # trailing\n")
        self.assertEqual(table.units, {"t": ["a"]})

    def test_a_dash_must_stand_alone(self):
        with self.assertRaises(tool.TableError):
            tool.parse("unit t - a\n")

    def test_a_second_entry_for_a_unit_is_an_error(self):
        with self.assertRaises(tool.TableError):
            tool.parse("unit t a\nunit t b\n")

    def test_covers(self):
        self.assertTrue(tool.covers("docs", "docs/a.md"))
        self.assertFalse(tool.covers("docs", "docs2/a.md"))
        self.assertTrue(tool.covers("a/*.md", "a/b/c.md"))
        self.assertTrue(tool.covers("a/b*", "a/bc/d.py"))


class Configuration(unittest.TestCase):

    def problems(self, units):
        data = example()
        data["units"] = units
        return config.check(data)

    def test_a_good_section_passes(self):
        self.assertEqual(self.problems({"t": {
            "table": "t.txt", "glob": "test_*.py",
            "trace_ignore": ["target/"]}}), [])

    def test_table_and_glob_are_required(self):
        got = self.problems({"t": {}})
        self.assertIn("units.t: missing 'table'", got)
        self.assertIn("units.t: missing 'glob'", got)

    def test_an_unknown_key_is_refused(self):
        self.assertIn("units.t: unknown key 'tabel'", self.problems(
            {"t": {"table": "a", "glob": "b", "tabel": "c"}}))

    def test_trace_ignore_holds_globs(self):
        self.assertIn("units.t.trace_ignore must be a list of globs",
                      self.problems({"t": {"table": "a", "glob": "b",
                                           "trace_ignore": [""]}}))


TRACED = {
    "tools/test_listed.py": "open('tools/a.py').read()\n",
    "tools/test_unlisted.py": "open('docs/b/two.md').read()\n",
    "tools/test_lists.py": "import os\nos.listdir('docs')\n",
    "tools/test_absent.py":
        "assert 'secret' not in open('docs/a/one.md').read()\n",
    "tools/test_skips.py":
        "import os, sys\n"
        "if not os.path.exists('tools/fixture.bin'):\n"
        "    sys.exit(0)\n",
    "tools/test_git_spec.py":
        "import subprocess\n"
        "subprocess.run(['git', 'ls-files', '--', 'docs/a'], check=True,\n"
        "               capture_output=True)\n",
    "tools/test_git_bare.py":
        "import subprocess\n"
        "subprocess.run(['git', 'ls-files'], check=True,\n"
        "               capture_output=True)\n",
    "tools/test_fails.py": "open('tools/a.py').read()\nraise SystemExit(3)\n",
}
TRACED_TABLE = """\
placed tools
placed docs
unit tools/test_listed.py tools/a.py
unit tools/test_unlisted.py tools/a.py
unit tools/test_lists.py docs/a/
unit tools/test_absent.py tools/a.py
unit tools/test_skips.py -
unit tools/test_git_spec.py docs/a/
unit tools/test_git_bare.py docs/a/
unit tools/test_fails.py tools/a.py
"""


@unittest.skipUnless(shutil.which("strace"), "needs strace")
class Trace(Fixture):

    def setUp(self):
        super().setUp()
        self.write(dict(TRACED, **{"tools/units.txt": TRACED_TABLE,
                                   "docs/b/two.md": ""}))
        for name in ("tools/test_a.py", "tools/test_b.py", "tools/test_c.py"):
            (self.repo / name).unlink()
        self.commit("traced")

    def trace(self):
        return self.call("trace", "--units", "tools", "--", "python3",
                         "{unit}")

    def lines_for(self, out, unit):
        return [l for l in out if l.startswith(unit + " ")]

    def test_a_listed_read_is_no_finding(self):
        out = self.trace()[1]
        self.assertEqual(self.lines_for(out, "tools/test_listed.py"), [])

    def test_an_unlisted_read_is_a_finding_with_its_fix(self):
        code, out, _ = self.trace()
        self.assertEqual(code, 1)
        self.assertIn("tools/test_unlisted.py reads docs/b/two.md "
                      "(not in its entry)", out)
        self.assertIn("  unit tools/test_unlisted.py tools/a.py "
                      "docs/b/two.md", out)

    def test_a_directory_listing_is_a_read(self):
        out = self.trace()[1]
        self.assertEqual(self.lines_for(out, "tools/test_lists.py"), [])
        self.write({"tools/units.txt": TRACED_TABLE.replace(
            "test_lists.py docs/a/", "test_lists.py tools/a.py")})
        out = self.trace()[1]
        self.assertIn("tools/test_lists.py lists docs (not in its entry)",
                      out)

    def test_a_test_that_asserts_text_absent_still_reads_it(self):
        out = self.trace()[1]
        self.assertIn("tools/test_absent.py reads docs/a/one.md "
                      "(not in its entry)", out)

    def test_a_test_that_skips_on_a_missing_input_asks_for_it(self):
        out = self.trace()[1]
        self.assertIn("tools/test_skips.py reads tools/fixture.bin "
                      "(not in its entry)", out)

    def test_git_with_a_covered_pathspec_is_no_finding(self):
        out = self.trace()[1]
        self.assertEqual(self.lines_for(out, "tools/test_git_spec.py"), [])

    def test_git_with_no_pathspec_needs_a_star(self):
        out = self.trace()[1]
        self.assertIn("tools/test_git_bare.py runs git ls-files with no "
                      "pathspec (not in its entry)", out)

    def test_a_failing_unit_is_reported_not_counted(self):
        code, out, err = self.trace()
        self.assertIn("tools/test_fails.py: its own command failed (exit 3);"
                      " its reads are judged all the same", err)
        self.assertEqual(self.lines_for(out, "tools/test_fails.py"), [])

    def test_a_command_without_unit_is_refused(self):
        code, _, err = self.call("trace", "--units", "tools", "--",
                                 "python3")
        self.assertEqual(code, 2)
        self.assertIn("{unit}", err[0])


class TraceParsing(unittest.TestCase):

    def test_an_unfinished_call_is_joined_to_its_resumption(self):
        lines = ['1 openat(AT_FDCWD</r>, "a", O_RDONLY <unfinished ...>\n',
                 '2 exit_group(0) = ?\n',
                 '1 <... openat resumed>) = 3</r/a>\n']
        got = list(tool.calls(lines))
        self.assertEqual(got[-1][:2], (1, "openat"))
        self.assertIn('"a"', got[-1][2])

    def test_reads_resolve_against_the_dirfd_and_forks_inherit_cwd(self):
        lines = ['1 chdir("/r/sub") = 0\n',
                 '1 vfork() = 2\n',
                 '2 open("x", O_RDONLY) = 3\n',
                 '2 openat(AT_FDCWD</r>, "y", O_RDONLY) = 3\n',
                 '2 newfstatat(AT_FDCWD</r>, "d", {st_mode=S_IFDIR|0755}, 0)'
                 ' = 0\n',
                 '2 openat(AT_FDCWD</r>, "z", O_RDONLY|O_DIRECTORY) = 3\n',
                 '2 openat(AT_FDCWD</r>, "/etc/passwd", O_RDONLY) = 3\n']
        paths, listings, gits = tool.reads(lines, "/r")
        self.assertEqual(paths, {"sub/x", "y"})
        self.assertEqual(listings, {"z"})
        self.assertEqual(gits, [])

    def test_a_vfork_child_that_moved_before_the_parent_says_so(self):
        # The order strace -f gave on a real trace: the child's chdir,
        # its execve unfinished, the parent's vfork resumed, then the
        # execve resumed. The child's own directory stands.
        lines = ['1 chdir("/r") = 0\n',
                 '1 vfork( <unfinished ...>\n',
                 '2 chdir("/tmp/x") = 0\n',
                 '2 execve("/usr/bin/git", ["git", "ls-files"], 0x0 '
                 '/* 3 vars */ <unfinished ...>\n',
                 '1 <... vfork resumed>) = 2\n',
                 '2 <... execve resumed>) = 0\n']
        paths, _, gits = tool.reads(lines, "/r")
        self.assertEqual((paths, gits), (set(), []))

    def test_a_vfork_child_whose_execve_ends_before_the_parent_says_so(self):
        # A nightly's order: the child's execve finished before the
        # parent's vfork named the child, so at the execve the child had
        # no directory; it started in the parent's, outside the root.
        lines = ['1 chdir("/tmp/repo") = 0\n',
                 '1 vfork( <unfinished ...>\n',
                 '2 execve("/usr/bin/git", ["git", "cat-file", "-e", '
                 '"abc^{commit}"], 0x0 /* 3 vars */) = 0\n',
                 '1 <... vfork resumed>) = 2\n',
                 '2 openat(AT_FDCWD</tmp/repo>, "/etc/ld.so.cache", '
                 'O_RDONLY) = 3\n']
        paths, _, gits = tool.reads(lines, "/r")
        self.assertEqual((paths, gits), (set(), []))

    def test_a_vfork_child_that_exits_before_the_parent_says_so(self):
        # A short git can exit, too, before its parent's vfork returns.
        lines = ['1 chdir("/tmp/repo") = 0\n',
                 '1 vfork( <unfinished ...>\n',
                 '2 execve("/usr/bin/git", ["git", "archive", "HEAD"], '
                 '0x0 /* 3 vars */) = 0\n',
                 '2 +++ exited with 0 +++\n',
                 '1 <... vfork resumed>) = 2\n']
        _, _, gits = tool.reads(lines, "/r")
        self.assertEqual(gits, [])

    def test_a_git_never_named_is_judged_from_the_root(self):
        lines = ['2 execve("/usr/bin/git", ["git", "archive", "HEAD"], '
                 '0x0 /* 3 vars */) = 0\n',
                 '2 +++ exited with 0 +++\n']
        _, _, gits = tool.reads(lines, "/r")
        self.assertEqual(gits, [("archive", [])])

    def test_a_git_with_no_directory_shown_takes_its_parents(self):
        lines = ['1 chdir("/r") = 0\n',
                 '1 vfork( <unfinished ...>\n',
                 '2 execve("/usr/bin/git", ["git", "archive", "HEAD"], '
                 '0x0 /* 3 vars */) = 0\n',
                 '1 <... vfork resumed>) = 2\n',
                 '2 exit_group(0) = ?\n']
        _, _, gits = tool.reads(lines, "/r")
        self.assertEqual(gits, [("archive", [])])

    def test_a_pid_that_comes_back_inherits_afresh(self):
        lines = ['1 chdir("/r/a") = 0\n',
                 '1 vfork() = 2\n',
                 '2 chdir("/tmp") = 0\n',
                 '2 exit_group(0) = ?\n',
                 '2 +++ exited with 0 +++\n',
                 '1 vfork() = 2\n',
                 '2 open("x", O_RDONLY) = 3\n']
        paths, _, _ = tool.reads(lines, "/r")
        self.assertEqual(paths, {"a/x"})

    def test_a_git_process_is_judged_by_its_command_line(self):
        lines = ['5 execve("/usr/bin/git", ["git", "-C", "sub", "grep", '
                 '"-n", "word", "--", "docs"], 0x0 /* 3 vars */) = 0\n',
                 '5 openat(AT_FDCWD</r/sub>, ".git/HEAD", O_RDONLY) = 3\n']
        paths, _, gits = tool.reads(lines, "/r")
        self.assertEqual(paths, set())
        self.assertEqual(gits, [("grep", ["sub/docs"])])

    def test_git_show_takes_rev_colon_path_from_the_top(self):
        self.assertEqual(tool.git_pathspecs(
            ["git", "show", "HEAD:docs/a.md"], "/r/sub", "/r"),
            ("show", ["docs/a.md"]))

    def test_a_git_in_another_repository_reads_nothing_here(self):
        # A test's scratch repository outside the root: by its cwd, by
        # -C, and by --work-tree, with and without pathspecs.
        lines = ['5 chdir("/tmp/repo") = 0\n',
                 '5 execve("/usr/bin/git", ["git", "ls-files"], 0x0 '
                 '/* 3 vars */) = 0\n',
                 '6 execve("/usr/bin/git", ["git", "-C", "/tmp/repo", '
                 '"show", "HEAD:a.txt"], 0x0 /* 3 vars */) = 0\n',
                 '7 execve("/usr/bin/git", ["git", "-C", "/tmp/repo", '
                 '"ls-files", "a.txt"], 0x0 /* 3 vars */) = 0\n',
                 '8 execve("/usr/bin/git", ["git", "--work-tree=/tmp/repo",'
                 ' "--git-dir=/tmp/repo/.git", "grep", "x"], 0x0 '
                 '/* 3 vars */) = 0\n']
        paths, _, gits = tool.reads(lines, "/r")
        self.assertEqual((paths, gits), (set(), []))
        self.assertEqual(tool.findings("u", [], set(), set(), gits, []), [])

    def test_a_git_moved_into_the_tree_is_still_judged(self):
        self.assertEqual(tool.git_pathspecs(
            ["git", "-C", "/r/sub", "ls-files"], "/tmp", "/r"),
            ("ls-files", []))
        self.assertEqual(tool.git_pathspecs(
            ["git", "--work-tree", "/r", "show", "HEAD:a.md"], "/tmp", "/r"),
            ("show", ["a.md"]))

    def test_git_that_reads_no_content_is_not_a_reader(self):
        self.assertEqual(tool.git_pathspecs(["git", "rev-parse", "HEAD"],
                                            "/r", "/r"), (None, None))


if __name__ == "__main__":
    unittest.main()
