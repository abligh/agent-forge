"""Tests for bin/forge-doc-width: which lines are too long, which are
exempt, the configured width, and the exit codes. No network; files
live in a temporary directory."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT, example, load_tool

TOOL = ROOT / "bin" / "forge-doc-width"
width = load_tool("forge-doc-width")
LONG = " ".join(["word"] * 20)  # 99 columns


class LongLinesTest(unittest.TestCase):
    def test_prose_at_the_limit_passes(self):
        line = ("x " * 40)[:80]
        self.assertEqual(len(line), 80)
        self.assertEqual(width.long_lines(line + "\n"), [])

    def test_prose_past_the_limit_is_reported_by_line(self):
        self.assertEqual(width.long_lines("# T\n\nshort\n" + LONG + "\n"),
                         [4])

    def test_the_limit_is_a_parameter(self):
        line = ("x " * 40)[:72]
        self.assertEqual(width.long_lines(line + "\n", 72), [])
        self.assertEqual(width.long_lines(line + "x\n", 72), [1])

    def test_fenced_code_is_exempt(self):
        for fence in ("```", "~~~"):
            text = f"{fence}sh\n{LONG}\n{fence}\n"
            self.assertEqual(width.long_lines(text), [], fence)

    def test_prose_after_a_fence_closes_is_checked(self):
        self.assertEqual(width.long_lines(f"```\ncode\n```\n{LONG}\n"), [4])

    def test_a_different_fence_marker_does_not_close(self):
        text = f"```\n~~~\n{LONG}\n```\n"
        self.assertEqual(width.long_lines(text), [])

    def test_indented_code_block_is_exempt(self):
        self.assertEqual(width.long_lines(f"para\n\n    {LONG}\n"), [])

    def test_indented_list_continuation_is_prose(self):
        text = f"* item\n    {LONG}\n"
        self.assertEqual(width.long_lines(text), [2])

    def test_table_rows_are_exempt(self):
        self.assertEqual(width.long_lines(f"| {LONG} |\n"), [])

    def test_lines_with_links_are_exempt(self):
        for line in (f"{LONG} [x](docs/a.md)",
                     f"{LONG} https://example.com/",
                     f"[ref]: https://example.com/{'a' * 90}"):
            self.assertEqual(width.long_lines(line + "\n"), [], line)

    def test_a_single_unbreakable_word_is_exempt(self):
        self.assertEqual(width.long_lines("x" * 100 + "\n"), [])


class MainTest(unittest.TestCase):
    """Runs the real script in a temporary tree, which is the project's
    checkout as far as the configuration is concerned."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)

    def configure(self, docs):
        data = example()
        data["docs"] = docs
        (self.dir / ".agent-forge").mkdir(exist_ok=True)
        (self.dir / ".agent-forge" / "config.json").write_text(
            json.dumps(data))

    def run_tool(self, *args):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("FORGE_")}
        env["FORGE_PROJECT_ROOT"] = str(self.dir)
        return subprocess.run([sys.executable, str(TOOL), *map(str, args)],
                              cwd=self.dir, env=env, capture_output=True,
                              text=True)

    def test_clean_tree_says_how_many_files_it_read(self):
        (self.dir / "docs").mkdir()
        (self.dir / "docs" / "a.md").write_text("# A\n\nshort\n")
        (self.dir / "README.md").write_text("# R\n")
        (self.dir / "docs" / "notes.txt").write_text(LONG + "\n")
        result = self.run_tool(self.dir / "README.md", self.dir / "docs")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean (2 files)", result.stdout)

    def test_long_line_fails_and_names_path_and_line(self):
        path = self.dir / "a.md"
        path.write_text("# A\n\n" + LONG + "\n")
        result = self.run_tool(path)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{path}:3: longer than 80 columns", result.stdout)

    def test_no_arguments_and_nothing_configured_to_read_is_an_error(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing was checked", result.stderr)

    def test_missing_path_is_an_error_not_a_pass(self):
        result = self.run_tool(self.dir / "nope.md")
        self.assertEqual(result.returncode, 2)
        self.assertIn("no such file", result.stderr)

    def test_directory_without_markdown_is_an_error_not_a_pass(self):
        result = self.run_tool(self.dir)
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing was checked", result.stderr)

    def test_the_configured_width(self):
        self.configure({"width": 100})
        (self.dir / "a.md").write_text("# A\n\n" + LONG + "\n")
        self.assertEqual(self.run_tool("a.md").returncode, 0)
        self.configure({"width": 72})
        (self.dir / "a.md").write_text("# A\n\n" + ("x " * 37) + "\n")
        result = self.run_tool("a.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("a.md:3: longer than 72 columns", result.stdout)

    def test_no_arguments_reads_the_configured_paths(self):
        self.configure({"paths": ["notes/**/*.md"]})
        (self.dir / "README.md").write_text("# R\n\n" + LONG + "\n")
        (self.dir / "notes" / "deep").mkdir(parents=True)
        (self.dir / "notes" / "deep" / "n.md").write_text("# N\n")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("clean (1 files)", result.stdout)


if __name__ == "__main__":
    unittest.main()
