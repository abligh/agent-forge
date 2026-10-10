"""Tests for bin/forge-doc-links: relative targets, anchors, GitHub slug
rules, what is skipped, and the exit codes. No network; files live in a
temporary directory."""

import json
import os
import pathlib
import subprocess
import sys
import tempfile
import unittest

from tests.helpers import ROOT, example, load_tool

TOOL = ROOT / "bin" / "forge-doc-links"
links = load_tool("forge-doc-links")


class SlugTest(unittest.TestCase):
    def test_github_slug_rules(self):
        cases = {
            "Before committing": "before-committing",
            "What `make test` runs": "what-make-test-runs",
            "Step 3: build it!": "step-3-build-it",
            "snake_case and dash-ed": "snake_case-and-dash-ed",
            "See [the guide](g.md)": "see-the-guide",
            "Two  spaces": "two--spaces",
        }
        for heading, want in cases.items():
            self.assertEqual(links.slug(heading), want, heading)

    def test_repeated_headings_are_numbered(self):
        text = "# T\n## Notes\n## Notes\n## Notes\n"
        self.assertEqual(links.anchors(text),
                         {"t", "notes", "notes-1", "notes-2"})

    def test_explicit_html_anchors_count(self):
        self.assertIn("here", links.anchors('<a id="here"></a>\n'))
        self.assertIn("old", links.anchors("<a name='old'></a>\n"))

    def test_headings_in_fences_are_not_anchors(self):
        self.assertEqual(links.anchors("```\n# not\n```\n"), set())


class LinksTest(unittest.TestCase):
    def test_finds_inline_image_and_definition_links(self):
        text = ("[a](a.md) ![i](img.png \"title\")\n"
                "[ref]: docs/b.md#x\n")
        self.assertEqual(list(links.links(text)),
                         [(1, "a.md"), (1, "img.png"), (2, "docs/b.md#x")])

    def test_skips_code_spans_and_fences(self):
        text = "`[x](missing.md)`\n```\n[y](missing.md)\n```\n"
        self.assertEqual(list(links.links(text)), [])


class TreeTest(unittest.TestCase):
    """Runs the real script in a temporary tree, which is the project's
    checkout as far as the configuration is concerned."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = pathlib.Path(self._tmp.name)
        (self.dir / "docs").mkdir()
        (self.dir / "docs" / "guide.md").write_text(
            "# Guide\n\n## Before committing\n\nSee [top](#guide).\n")
        (self.dir / "Makefile").write_text("all:\n")

    def write(self, name, text):
        (self.dir / name).write_text(text)

    def configure(self, docs):
        data = example()
        data["docs"] = docs
        (self.dir / ".agent-forge").mkdir(exist_ok=True)
        (self.dir / ".agent-forge" / "config.json").write_text(
            json.dumps(data))

    def run_tool(self, *args, cwd=None):
        env = {k: v for k, v in os.environ.items()
               if not k.startswith("FORGE_")}
        env["FORGE_PROJECT_ROOT"] = str(self.dir)
        return subprocess.run([sys.executable, str(TOOL), *args],
                              cwd=cwd or self.dir, env=env,
                              capture_output=True, text=True)

    def test_good_links_pass_and_say_what_was_read(self):
        self.write("README.md",
                   "# R\n\n[g](docs/guide.md) "
                   "[c](docs/guide.md#before-committing) "
                   "[m](Makefile) [d](docs/) [root](/docs/guide.md) "
                   "[web](https://example.com/x.md#nope) [me](#r)\n")
        result = self.run_tool("README.md", "docs")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("clean (8 links, 2 files)", result.stdout)

    def test_missing_file_is_reported(self):
        self.write("README.md", "# R\n\nline\n[g](docs/gone.md)\n")
        result = self.run_tool("README.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md:4: links to docs/gone.md, which does not "
                      "exist", result.stdout)

    def test_links_resolve_from_the_linking_files_directory(self):
        (self.dir / "docs" / "other.md").write_text(
            "# O\n\n[up](../README.md) [wrong](docs/guide.md)\n")
        self.write("README.md", "# R\n")
        result = self.run_tool(os.path.join("docs", "other.md"))
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("does not exist"), 1)
        self.assertIn("docs/guide.md", result.stdout)

    def test_a_leading_slash_is_the_top_of_the_checkout(self):
        (self.dir / "docs" / "other.md").write_text(
            "# O\n\n[g](/docs/guide.md#before-committing) [x](/other.md)\n")
        result = self.run_tool("other.md", cwd=self.dir / "docs")
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stdout.count("does not exist"), 1)
        self.assertIn("links to /other.md", result.stdout)

    def test_missing_anchor_is_reported(self):
        self.write("README.md", "# R\n\n[c](docs/guide.md#after)\n")
        result = self.run_tool("README.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("docs/guide.md#after, which matches no heading",
                      result.stdout)

    def test_missing_same_file_anchor_is_reported(self):
        self.write("README.md", "# R\n\n[c](#nowhere)\n")
        result = self.run_tool("README.md")
        self.assertEqual(result.returncode, 1)
        self.assertIn("README.md#nowhere", result.stdout)

    def test_anchor_into_a_non_markdown_file_is_not_checked(self):
        self.write("README.md", "# R\n\n[m](Makefile#L1)\n")
        self.assertEqual(self.run_tool("README.md").returncode, 0)

    def test_percent_encoded_target(self):
        (self.dir / "docs" / "a b.md").write_text("# A\n")
        self.write("README.md", "# R\n\n[s](docs/a%20b.md)\n")
        self.assertEqual(self.run_tool("README.md").returncode, 0)

    def test_no_arguments_reads_the_default_paths(self):
        self.write("README.md", "# R\n\n[g](docs/guide.md)\n")
        result = self.run_tool()
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("clean (2 links, 2 files)", result.stdout)

    def test_no_arguments_reads_the_configured_paths(self):
        self.configure({"paths": ["README.md"]})
        self.write("README.md", "# R\n\n[g](docs/gone.md)\n")
        result = self.run_tool()
        self.assertEqual(result.returncode, 1)
        self.assertIn("(1 links, 1 files)", result.stdout)

    def test_no_arguments_and_nothing_configured_to_read_is_an_error(self):
        self.configure({"paths": ["absent"]})
        result = self.run_tool()
        self.assertEqual(result.returncode, 2)
        self.assertIn("nothing was checked", result.stderr)

    def test_missing_path_is_an_error_not_a_pass(self):
        self.assertEqual(self.run_tool("nope.md").returncode, 2)


if __name__ == "__main__":
    unittest.main()
