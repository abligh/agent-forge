"""Tests for bin/forge-units (designs/0065).

Real git repositories in a temporary directory: what `select` answers
is a git question (what changed since the merge base, what the head's
tree holds), and a fake git would be testing the fake.
"""

import os
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


if __name__ == "__main__":
    unittest.main()
