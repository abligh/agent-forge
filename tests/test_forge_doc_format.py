"""Tests for bin/forge-doc-format: trailing whitespace, the final
newline, heading order, the files it reads, and the exit codes. No
network; files live in a temporary directory."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT, example, load_tool

TOOL = ROOT / "bin" / "forge-doc-format"
fmt = load_tool("forge-doc-format")


def messages(text):
    return [message for _, message in fmt.problems(text)]


class ProblemsTest(unittest.TestCase):
    def test_a_clean_document_has_no_problems(self):
        text = "# T\n\nIntro.\n\n## A\n\n### A.1\n\n## B\n"
        self.assertEqual(fmt.problems(text), [])

    def test_trailing_whitespace_is_reported_by_line(self):
        self.assertEqual(fmt.problems("# T\n\nhard break  \n"),
                         [(3, "trailing whitespace")])

    def test_trailing_tab_and_crlf_are_trailing_whitespace(self):
        self.assertEqual(len(fmt.problems("# T\t\n")), 1)
        self.assertEqual(len(fmt.problems("# T\r\nx\r\n")), 2)

    def test_trailing_whitespace_in_code_is_still_reported(self):
        self.assertEqual(fmt.problems("# T\n\n```\nx \n```\n"),
                         [(4, "trailing whitespace")])

    def test_missing_final_newline(self):
        self.assertEqual(messages("# T\n\ntext"),
                         ["no newline at end of file"])

    def test_blank_lines_at_end(self):
        self.assertEqual(messages("# T\n\n"),
                         ["blank line(s) at end of file"])

    def test_empty_file(self):
        self.assertEqual(messages(""), ["file is empty"])

    def test_first_heading_must_be_level_one(self):
        self.assertIn("first heading is level 2", messages("## T\n")[0])

    def test_only_one_level_one_heading(self):
        self.assertIn("second level-1 heading",
                      messages("# T\n\n# U\n")[0])

    def test_skipping_a_level_is_reported_with_the_fix(self):
        problems = fmt.problems("# T\n\n## A\n\n#### B\n")
        self.assertEqual(len(problems), 1)
        self.assertEqual(problems[0][0], 5)
        self.assertIn("from level 2 to 4; use ###", problems[0][1])

    def test_going_back_up_any_number_of_levels_is_fine(self):
        self.assertEqual(fmt.problems("# T\n## A\n### B\n#### C\n## D\n"),
                         [])

    def test_hash_in_a_fence_is_not_a_heading(self):
        text = "# T\n\n```sh\n#### a comment\n# another\n```\n"
        self.assertEqual(fmt.problems(text), [])

    def test_hash_without_a_space_is_not_a_heading(self):
        self.assertEqual(fmt.problems("# T\n\n#hashtag\n"), [])


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

    def test_clean_files_pass_and_say_how_many(self):
        (self.dir / "a.md").write_text("# A\n")
        (self.dir / "sub").mkdir()
        (self.dir / "sub" / "b.md").write_text("# B\n\n## C\n")
        result = self.run_tool(self.dir)
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("clean (2 files)", result.stdout)

    def test_a_problem_fails_and_names_path_and_line(self):
        path = self.dir / "a.md"
        path.write_bytes(b"# A\n\nx \n")
        result = self.run_tool(path)
        self.assertEqual(result.returncode, 1)
        self.assertIn(f"{path}:3: trailing whitespace", result.stdout)

    def test_no_arguments_and_nothing_configured_to_read_is_an_error(self):
        result = self.run_tool()
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing was checked", result.stderr)

    def test_missing_path_is_an_error_not_a_pass(self):
        self.assertEqual(self.run_tool(self.dir / "nope.md").returncode, 2)

    def test_directory_without_markdown_is_an_error_not_a_pass(self):
        self.assertEqual(self.run_tool(self.dir).returncode, 2)

    def test_no_arguments_reads_the_default_paths(self):
        (self.dir / "README.md").write_text("# R\n")
        (self.dir / "docs").mkdir()
        (self.dir / "docs" / "a.md").write_text("# A\n\nx \n")
        (self.dir / "other").mkdir()
        (self.dir / "other" / "b.md").write_text("# B \n")
        result = self.run_tool()
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("docs/a.md:3: trailing whitespace", result.stdout)
        self.assertNotIn("other", result.stdout)
        self.assertIn("(2 files)", result.stdout)

    def test_no_arguments_reads_the_configured_paths(self):
        self.configure({"paths": ["guides", "tools/*.md", "absent"]})
        (self.dir / "README.md").write_text("# R \n")
        (self.dir / "guides").mkdir()
        (self.dir / "guides" / "a.md").write_text("# A\n")
        (self.dir / "tools").mkdir()
        (self.dir / "tools" / "t.md").write_text("# T\n")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean (2 files)", result.stdout)

    def test_named_paths_override_the_configured_ones(self):
        self.configure({"paths": ["docs"]})
        (self.dir / "a.md").write_text("# A\n")
        result = self.run_tool("a.md")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("clean (1 files)", result.stdout)

    def test_an_invalid_configuration_is_an_error(self):
        self.configure({"paths": "docs"})
        (self.dir / "a.md").write_text("# A\n")
        result = self.run_tool("a.md")
        self.assertEqual(result.returncode, 2)
        self.assertIn("docs.paths must be a list", result.stderr)


if __name__ == "__main__":
    unittest.main()
