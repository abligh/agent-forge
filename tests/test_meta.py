"""Rules every tool and module here keeps, checked over all of them, so
a new one can't forget one.

* A tool in bin/ is executable, a Python script with a docstring that
  opens with one line saying what it does, and has its own test file,
  tests/test_<name>.py (hyphens as underscores).
* A module in agent_forge/ has its own test file, tests/test_<module>.py.
* Nothing imports beyond the standard library and agent_forge: a host
  installs agent-forge by cloning it, with nothing to pip install.
* Every Python file compiles without a SyntaxWarning, which an invalid
  escape in a docstring about a regex is the usual cause of.
"""
import ast
import os
import py_compile
import sys
import tempfile
import unittest
import warnings

from tests.helpers import ROOT


def tools():
    return sorted(p for p in (ROOT / "bin").iterdir() if p.is_file())


def python_files():
    found = set(ROOT.glob("agent_forge/*.py")) | set(ROOT.glob("tests/*.py"))
    for directory in ("bin", "tools"):
        for path in (ROOT / directory).iterdir():
            if path.is_file() and path.read_bytes().startswith(b"#!") \
                    and b"python" in path.read_bytes().splitlines()[0]:
                found.add(path)
    return sorted(found)


class Tools(unittest.TestCase):

    def test_there_are_tools(self):
        self.assertTrue(tools())

    def test_each_is_an_executable_python_script_with_a_docstring(self):
        for path in tools():
            with self.subTest(tool=path.name):
                self.assertTrue(os.access(path, os.X_OK))
                text = path.read_text()
                self.assertEqual(text.splitlines()[0], "#!/usr/bin/env python3")
                doc = ast.get_docstring(ast.parse(text))
                self.assertTrue(doc and doc.splitlines()[0].strip())

    def test_each_is_named_forge_something(self):
        for path in tools():
            self.assertTrue(path.name.startswith("forge-"), path.name)

    def test_each_has_its_own_tests(self):
        for path in tools():
            name = "test_%s.py" % path.name.replace("-", "_")
            self.assertTrue((ROOT / "tests" / name).exists(), name)


class Modules(unittest.TestCase):

    def test_each_has_its_own_tests(self):
        for path in sorted((ROOT / "agent_forge").glob("*.py")):
            if path.name == "__init__.py":
                continue
            name = "test_%s" % path.name
            self.assertTrue((ROOT / "tests" / name).exists(), name)


class Imports(unittest.TestCase):

    def test_standard_library_and_agent_forge_only(self):
        allowed = set(sys.stdlib_module_names) | {"agent_forge", "tests"}
        for path in python_files():
            tree = ast.parse(path.read_text(), str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.level == 0:
                    names = [node.module]
                else:
                    continue
                for name in names:
                    with self.subTest(file=path.name, module=name):
                        self.assertIn(name.split(".")[0], allowed)


class Compiles(unittest.TestCase):

    def test_without_a_syntax_warning(self):
        for path in python_files():
            with warnings.catch_warnings(record=True) as caught, \
                    tempfile.NamedTemporaryFile(suffix=".pyc") as out:
                warnings.simplefilter("always")
                py_compile.compile(str(path), cfile=out.name, doraise=True)
            self.assertEqual([str(w.message) for w in caught
                              if issubclass(w.category, SyntaxWarning)], [],
                             path.name)


if __name__ == "__main__":
    unittest.main()
