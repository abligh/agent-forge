"""Tests for a step's `env_file`: secrets from a private file on the
check host, added to that step's environment only."""

import os
import tempfile
import types
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import pc
from tests.test_forge_pr_check_side_by_side import side

SECRET = "s3cr3t-value"


def recorder():
    seen = {}

    def run(args, cwd=None, env=None, timeout=None, **kw):
        seen[args[-1]] = dict(env)
        return types.SimpleNamespace(returncode=0, stdout="", stderr="")
    run.seen = seen
    return run


class Case(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def write(self, text, mode=0o600, name="step.env"):
        path = os.path.join(self.dir, name)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, mode)
        return path


class ReadEnvFile(Case):

    def test_key_value_lines_with_blanks_and_comments(self):
        path = self.write("# tokens\n\nA=1\nB=x=y z\nEMPTY=\n")
        self.assertEqual(pc.read_env_file(path),
                         ({"A": "1", "B": "x=y z", "EMPTY": ""}, None, False))

    def test_missing_is_no_values_and_no_problem(self):
        self.assertEqual(pc.read_env_file(os.path.join(self.dir, "none")),
                         ({}, None, True))

    def test_readable_by_group_or_others_is_refused(self):
        for mode in (0o640, 0o604, 0o660, 0o644):
            path = self.write("A=%s\n" % SECRET, mode)
            values, problem, missing = pc.read_env_file(path)
            self.assertIsNone(values)
            self.assertIn("mode %04o" % mode, problem)
            self.assertNotIn(SECRET, problem)

    def test_another_owner_is_refused(self):
        path = self.write("A=%s\n" % SECRET)
        values, problem, _ = pc.read_env_file(path, uid=os.getuid() + 1)
        self.assertIsNone(values)
        self.assertIn("owned by uid %d" % os.getuid(), problem)

    def test_a_line_that_isnt_key_value_is_refused_without_its_text(self):
        for text in ("A=1\n%s\n" % SECRET, "A=1\n1BAD=%s\n" % SECRET,
                     "A=1\nexport B=%s\n" % SECRET):
            values, problem, _ = pc.read_env_file(self.write(text))
            self.assertIsNone(values)
            self.assertIn("line 2 is not KEY=VALUE", problem)
            self.assertNotIn(SECRET, problem)

    def test_a_directory_is_refused(self):
        path = os.path.join(self.dir, "d")
        os.mkdir(path, 0o700)
        values, problem, _ = pc.read_env_file(path)
        self.assertIsNone(values)
        self.assertIn("not a file", problem)


class Check(Case):

    STEPS = [("plain", ["plain"], None), ("google", ["google"], None)]

    def check(self, env_files, **kw):
        run, said = recorder(), []
        ok, results = pc.check(self.dir, {"X": "1"}, run, steps=self.STEPS,
                               env_files=env_files, out=said.append, **kw)
        return ok, results, run.seen, said

    def test_only_its_step_gets_the_values(self):
        path = self.write("TOKEN=%s\n" % SECRET)
        ok, results, seen, said = self.check({"google": path})
        self.assertTrue(ok)
        self.assertEqual(seen["google"]["TOKEN"], SECRET)
        self.assertEqual(seen["google"]["X"], "1")
        self.assertNotIn("TOKEN", seen["plain"])
        self.assertEqual(said, [])

    def test_an_exposed_file_makes_the_step_red_unrun(self):
        path = self.write("TOKEN=%s\n" % SECRET, 0o644)
        ok, results, seen, _ = self.check({"google": path})
        self.assertFalse(ok)
        self.assertNotIn("google", seen)
        self.assertIn("plain", seen)
        red = results[1]
        self.assertEqual((red["name"], red["rc"]), ("google", 1))
        self.assertTrue(red["tail"].startswith("not run: env_file "))
        self.assertNotIn(SECRET, repr(results))

    def test_a_missing_file_is_said_and_the_step_runs_without_it(self):
        path = os.path.join(self.dir, "absent.env")
        ok, _, seen, said = self.check({"google": path})
        self.assertTrue(ok)
        self.assertIn("google", seen)
        self.assertEqual(said, ["step google: env_file %s not found; it "
                                "runs without it" % path])

    def test_side_by_side_too(self):
        good = self.write("TOKEN=%s\n" % SECRET)
        bad = self.write("TOKEN=%s\n" % SECRET, 0o640, "bad.env")
        patches = side()
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        ok, results, seen, _ = self.check({"plain": good, "google": bad},
                                          side_by_side=2)
        self.assertFalse(ok)
        self.assertEqual(seen["plain"]["TOKEN"], SECRET)
        self.assertNotIn("google", seen)
        self.assertNotIn(SECRET, repr(results))


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_env_file_is_taken_with_home_expanded(self):
        data = example()
        data["checks"]["steps"].append({"name": "google", "run": ["g"],
                                        "env_file": "~/.config/x/g.env"})
        self.assertEqual(config.check(data), [])
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.ENV_FILES, {"google": os.path.expanduser(
            "~/.config/x/g.env")})

    def test_none_by_default(self):
        pc.configure(config.Project(example(), None))
        self.assertEqual(pc.ENV_FILES, {})

    def test_a_relative_path_is_refused(self):
        data = example()
        data["checks"]["steps"].append({"name": "google", "run": ["g"],
                                        "env_file": "secrets/g.env"})
        problems = config.check(data)
        self.assertEqual(len(problems), 1)
        self.assertIn("env_file must be an absolute path", problems[0])


if __name__ == "__main__":
    unittest.main()
