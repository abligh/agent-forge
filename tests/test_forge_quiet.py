"""Tests for bin/forge-quiet: holding a shared host quiet, and asking
whether it is held (the holds themselves: tests/test_quiet.py)."""

import datetime
import json
import os
import tempfile
import unittest

from agent_forge import config, quiet
from tests.helpers import example, load_tool

fq = load_tool("forge-quiet")

T0 = datetime.datetime(2026, 10, 19, 15, 0, tzinfo=datetime.timezone.utc)


def minutes(n):
    return datetime.timedelta(minutes=n)


class Cli(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "quiet")
        os.makedirs(self.dir)
        self.lines = []
        self.now = T0
        self.env = {"FORGE_QUIET_DIR": self.dir, "FORGE_AGENT": "Carol",
                    "FORGE_CONFIG": os.path.join(self._tmp.name, "none.json")}

    def tearDown(self):
        self._tmp.cleanup()

    def run_cli(self, *argv, env=None, sleep=None):
        def slept(seconds):
            self.now += datetime.timedelta(seconds=seconds)
        return fq.main(list(argv), environ=self.env if env is None else env,
                       clock=lambda: self.now, sleep=sleep or slept,
                       out=self.lines.append)

    def test_hold_status_release(self):
        self.assertEqual(self.run_cli("status"), 0)
        self.assertEqual(self.run_cli("hold", "15", "pause", "test"), 0)
        self.assertEqual(self.run_cli("status"), 1)
        self.assertIn("held: Carol until 2026-10-19T15:15:00Z: "
                      "pause test", self.lines)
        self.assertEqual(self.run_cli("release"), 0)
        self.assertEqual(self.run_cli("status"), 0)

    def test_status_waits_for_the_holds_to_end(self):
        quiet.hold(self.dir, "A", 10, "x", T0)
        self.assertEqual(self.run_cli("status", "--wait", "30"), 0)
        self.assertGreaterEqual(self.now, T0 + minutes(10))
        self.assertLess(self.now, T0 + minutes(11))

    def test_status_gives_up_when_the_wait_runs_out(self):
        quiet.hold(self.dir, "A", 60, "x", T0)
        self.assertEqual(self.run_cli("status", "--wait", "5"), 1)
        self.assertLess(self.now, T0 + minutes(6))

    def test_no_directory_means_nothing_is_held(self):
        env = dict(self.env)
        del env["FORGE_QUIET_DIR"]
        self.assertEqual(self.run_cli("status", env=env), 0)
        self.assertEqual(self.run_cli("hold", "5", "x", env=env), 2)

    def test_no_holder_is_refused(self):
        env = dict(self.env)
        del env["FORGE_AGENT"]
        self.assertEqual(self.run_cli("hold", "5", "x", env=env), 2)
        self.assertEqual(self.run_cli("hold", "5", "x", "--as", "Me", env=env),
                         0)

    def test_too_long_a_hold_is_refused(self):
        self.assertEqual(self.run_cli("hold", "121", "x"), 2)
        self.assertEqual(os.listdir(self.dir), [])

    def test_the_configuration_names_the_directory_and_the_limit(self):
        path = os.path.join(self._tmp.name, "config.json")
        data = example()
        data["quiet"] = {"dir": self.dir, "max_minutes": 200}
        with open(path, "w") as handle:
            json.dump(data, handle)
        env = {"FORGE_CONFIG": path, "FORGE_AGENT": "A"}
        self.assertEqual(self.run_cli("hold", "180", "long", env=env), 0)
        self.assertEqual(self.run_cli("status", env=env), 1)


class ConfigSection(unittest.TestCase):

    def base(self):
        return example()

    def test_a_quiet_section_is_checked(self):
        data = self.base()
        data["quiet"] = {"dir": "/var/lib/x-quiet"}
        self.assertEqual(config.check(data), [])
        data["quiet"] = {"dir": "", "max_minutes": 0, "other": 1}
        problems = config.check(data)
        self.assertIn("quiet: unknown key 'other'", problems)
        self.assertIn("quiet: missing 'dir'", problems)
        self.assertIn("quiet.max_minutes must be at least 1", problems)


if __name__ == "__main__":
    unittest.main()
