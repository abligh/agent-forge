"""The documentation keeps up with the code, so a project that knows
nothing of agent-forge can set it up from the docs alone.

* Every tool in bin/ has a page, docs/tools/<tool>.md, linked from the
  README, and every option the tool's parser takes is on its page.
* Every key of every configuration schema in agent_forge.config is
  named in docs/config.md: as `key`, `section.key` or, in a JSON
  example, "key".
* Every FORGE_ environment variable the code reads is in
  docs/config.md.
* The README links docs/getting-started.md, and the Markdown passes
  this repository's own checks: forge-doc-format, forge-doc-width and
  forge-doc-links.

A change that adds a tool, an option, a key or a variable fails here
until its documentation is written in the same pull request.
"""
import re
import subprocess
import sys
import unittest

from agent_forge import config
from tests.helpers import ROOT

DOCS = ROOT / "docs"
OPTION = re.compile(r"""add_argument\(\s*["'](--[a-z0-9][a-z0-9-]*)["']""")
VARIABLE = re.compile(r"\bFORGE_[A-Z][A-Z0-9_]*[A-Z0-9]\b")

# The Markdown the width check holds to: all but the designs, whose
# accepted text is left as it was approved.
PROSE = ["README.md", "docs/*.md", "docs/tools"]


def tools():
    return sorted(p for p in (ROOT / "bin").iterdir() if p.is_file())


def schemas():
    return {name: value for name, value in vars(config).items()
            if name.isupper() and isinstance(value, dict)
            and value and all(isinstance(k, str) for k in value)
            and all(isinstance(v, (type, tuple, dict, list))
                    for v in value.values())}


class ToolPages(unittest.TestCase):

    def test_each_tool_has_a_page_linked_from_the_readme(self):
        readme = (ROOT / "README.md").read_text()
        for path in tools():
            with self.subTest(tool=path.name):
                self.assertTrue((DOCS / "tools" / (path.name + ".md")).exists())
                self.assertIn("docs/tools/%s.md" % path.name, readme)

    def test_no_page_outlives_its_tool(self):
        names = {p.name for p in tools()}
        for page in (DOCS / "tools").glob("*.md"):
            self.assertIn(page.stem, names, page.name)

    def test_each_option_is_on_its_tools_page(self):
        for path in tools():
            page = DOCS / "tools" / (path.name + ".md")
            if not page.exists():
                continue
            text = page.read_text()
            for option in sorted(set(OPTION.findall(path.read_text()))):
                with self.subTest(tool=path.name, option=option):
                    self.assertRegex(text, r"(?<![a-z0-9-])%s(?![a-z0-9-])"
                                     % re.escape(option))


class Configuration(unittest.TestCase):

    def test_there_are_schemas(self):
        self.assertIn("TOP", schemas())
        self.assertIn("TEST_AHEAD", schemas())

    def test_every_key_is_documented(self):
        text = (DOCS / "config.md").read_text()
        for name, schema in sorted(schemas().items()):
            for key in schema:
                with self.subTest(schema=name, key=key):
                    self.assertRegex(text, r'[`."]%s[`"]' % re.escape(key))

    def test_every_variable_is_documented(self):
        text = (DOCS / "config.md").read_text()
        found = set()
        for directory in ("bin", "agent_forge"):
            for path in (ROOT / directory).iterdir():
                if path.is_file() and path.suffix in ("", ".py"):
                    found |= set(VARIABLE.findall(path.read_text()))
        self.assertTrue(found)
        for variable in sorted(found):
            with self.subTest(variable=variable):
                self.assertIn("`%s`" % variable, text)


class Markdown(unittest.TestCase):

    def test_the_readme_links_getting_started(self):
        self.assertIn("docs/getting-started.md",
                      (ROOT / "README.md").read_text())

    def test_the_docs_pass_their_own_checks(self):
        prose = [str(p.relative_to(ROOT)) for glob in PROSE
                 for p in sorted(ROOT.glob(glob))]
        for tool, paths in (("forge-doc-format", ["README.md", "docs"]),
                            ("forge-doc-width", prose),
                            ("forge-doc-links", ["README.md", "docs"])):
            with self.subTest(tool=tool):
                done = subprocess.run(
                    [sys.executable, str(ROOT / "bin" / tool)] + paths,
                    cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(done.returncode, 0,
                                 done.stdout + done.stderr)


if __name__ == "__main__":
    unittest.main()
