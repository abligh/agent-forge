"""Tests for agent_forge.markdown: which files the documentation checks
read, and their settings with and without a configuration."""
import io
import json
import os
import pathlib
import tempfile
import unittest

from agent_forge import config, markdown
from tests.helpers import done, example


class Tree(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = pathlib.Path(tmp.name)
        cwd = os.getcwd()
        os.chdir(self.dir)
        self.addCleanup(os.chdir, cwd)

    def write(self, name, text="# T\n"):
        path = self.dir / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)

    def configure(self, docs):
        data = example()
        data["docs"] = docs
        self.write(".agent-forge/config.json", json.dumps(data))

    def environ(self):
        return {"FORGE_PROJECT_ROOT": str(self.dir)}


class Settings(Tree):

    def test_defaults_without_a_configuration(self):
        docs = markdown.settings(self.environ())
        self.assertEqual(docs.paths, ["*.md", "docs"])
        self.assertEqual(docs.width, 80)
        self.assertEqual(docs.root, str(self.dir))

    def test_defaults_outside_a_checkout(self):
        docs = markdown.settings({}, run=lambda a, cwd=None: done(a, 128))
        self.assertEqual(docs.width, 80)
        self.assertEqual(docs.root, os.getcwd())

    def test_the_configured_section(self):
        self.configure({"paths": ["guides"], "width": 72})
        docs = markdown.settings(self.environ())
        self.assertEqual((docs.paths, docs.width), (["guides"], 72))
        self.assertEqual(docs.root, str(self.dir))

    def test_an_invalid_configuration_is_an_error(self):
        self.configure({"width": "wide"})
        with self.assertRaises(config.ConfigError):
            markdown.settings(self.environ())

    def test_a_named_configuration_that_is_missing_is_an_error(self):
        with self.assertRaises(config.ConfigError):
            markdown.settings({"FORGE_CONFIG": str(self.dir / "none.json")})


class Files(Tree):

    def test_named_paths_expand_directories_in_order(self):
        self.write("docs/b.md")
        self.write("docs/a/z.md")
        self.write("docs/notes.txt")
        self.write("README.md")
        self.assertEqual(markdown.files(["README.md", "docs"], None),
                         ["README.md", "docs/b.md", "docs/a/z.md"])

    def test_a_named_path_that_is_missing_is_an_error(self):
        with self.assertRaises(FileNotFoundError):
            markdown.files(["nope.md"], None)

    def test_configured_globs_skip_what_matches_nothing(self):
        self.write("README.md")
        self.write("tools/t.md")
        self.write("tools/sub/u.md")
        docs = markdown.Settings({"paths": ["*.md", "tools/*.md",
                                            "designs"]}, str(self.dir))
        self.assertEqual(markdown.files([], docs),
                         ["README.md", "tools/t.md"])

    def test_configured_globs_are_read_from_the_checkout(self):
        self.write("README.md")
        self.write("docs/a.md")
        os.chdir(self.dir / "docs")
        docs = markdown.Settings({}, str(self.dir))
        self.assertEqual(markdown.files([], docs), ["../README.md", "./a.md"])


class Choose(Tree):

    def choose(self, paths):
        err = io.StringIO()
        return markdown.choose("forge-x", paths, err, self.environ()), \
            err.getvalue()

    def test_files_and_settings(self):
        self.write("README.md")
        (files, docs), _ = self.choose([])
        self.assertEqual(files, ["README.md"])
        self.assertEqual(docs.width, 80)

    def test_nothing_to_read_says_so(self):
        got, err = self.choose([])
        self.assertEqual(got, 2)
        self.assertIn("forge-x: no Markdown files under docs.paths", err)
        self.assertIn("nothing was checked", err)

    def test_a_missing_path_says_so(self):
        got, err = self.choose(["nope.md"])
        self.assertEqual(got, 2)
        self.assertIn("nope.md: no such file", err)

    def test_an_invalid_configuration_says_so(self):
        self.configure({"paths": "docs"})
        got, err = self.choose(["README.md"])
        self.assertEqual(got, 2)
        self.assertIn("docs.paths must be a list", err)


if __name__ == "__main__":
    unittest.main()
