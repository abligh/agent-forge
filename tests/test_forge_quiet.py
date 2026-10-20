"""Tests for bin/forge-quiet: holding a shared host quiet, and asking
whether it is held (the holds themselves: tests/test_quiet.py)."""

import datetime
import json
import os
import sys
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


class Settle(unittest.TestCase):
    """hold's settle command: run after the hold is written, until it
    passes or its time runs out."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = os.path.join(self._tmp.name, "quiet")
        os.makedirs(self.dir)
        self.root = os.path.join(self._tmp.name, "checkout")
        os.makedirs(os.path.dirname(os.path.join(self.root,
                                                 config.CONFIG_NAME)))
        self.path = os.path.join(self.root, config.CONFIG_NAME)
        self.write({"dir": self.dir, "settle": ["tools/busy", "--host"],
                    "settle_minutes": 5})
        self.env = {"FORGE_CONFIG": self.path, "FORGE_AGENT": "Carol"}
        self.lines, self.calls, self.now = [], [], T0
        self.results = []

    def tearDown(self):
        self._tmp.cleanup()

    def write(self, section):
        data = example()
        data["quiet"] = section
        with open(self.path, "w") as handle:
            json.dump(data, handle)

    def run_cli(self, *argv):
        def slept(seconds):
            self.now += datetime.timedelta(seconds=seconds)

        def run(args, cwd, env):
            # The hold is on disk before the command first runs.
            self.calls.append((args, cwd, dict(env),
                               quiet.live(self.dir, self.now)))
            return self.results.pop(0) if self.results else (0, "")
        return fq.main(list(argv), environ=self.env, clock=lambda: self.now,
                       sleep=slept, out=self.lines.append, run=run)

    def test_it_runs_after_the_hold_with_the_hold_in_its_environment(self):
        self.assertEqual(self.run_cli("hold", "20", "timing"), 0)
        (args, cwd, env, holds), = self.calls
        self.assertEqual(args, [os.path.join(self.root, "tools/busy"),
                                "--host"])
        self.assertEqual(cwd, self.root)
        self.assertEqual([h[0] for h in holds], ["Carol"])
        self.assertEqual((env["FORGE_QUIET_HOLDER"], env["FORGE_QUIET_MINUTES"],
                          env["FORGE_QUIET_UNTIL"], env["FORGE_QUIET_DIR"]),
                         ("Carol", "20", "2026-10-19T15:20:00Z", self.dir))
        self.assertEqual(self.lines[-1],
                         "settled; held by Carol until 2026-10-19T15:20:00Z")

    def test_it_waits_until_the_command_passes(self):
        self.results = [(1, "deploy 41 building"), (1, "deploy 41 building"),
                        (0, "")]
        self.assertEqual(self.run_cli("hold", "20", "timing"), 0)
        self.assertEqual(len(self.calls), 3)
        self.assertEqual(self.now, T0 + datetime.timedelta(seconds=60))
        # A repeated message is said once.
        self.assertEqual(self.lines.count("settling: deploy 41 building"), 1)

    def test_settling_does_not_spend_the_hold(self):
        # 4 minutes of a busy host, then settled: the 20-minute hold
        # runs 20 minutes from then, and was live all along.
        self.results = [(1, "deploy 41 building")] * 8
        self.assertEqual(self.run_cli("hold", "20", "timing"), 0)
        settled = T0 + datetime.timedelta(seconds=30 * 8)
        self.assertEqual(self.now, settled)
        self.assertTrue(all(holds for *_, holds in self.calls))
        (_, until, _), = quiet.live(self.dir, self.now)
        self.assertEqual(until, settled + minutes(20))
        self.assertEqual(self.calls[-1][2]["FORGE_QUIET_UNTIL"],
                         quiet.stamp(settled + minutes(20)))

    def test_a_failing_command_is_an_error_and_ends_the_wait(self):
        self.results = [(2, "gh auth login")] * 100
        self.assertEqual(self.run_cli("hold", "20", "timing"), 3)
        self.assertEqual(len(self.calls), fq.SETTLE_ERRORS)
        self.assertIn("settling: settle error (exit 2): gh auth login",
                      self.lines)
        self.assertIn("failed 3 times in a row", self.lines[-1])
        self.assertEqual([h[0] for h in quiet.live(self.dir, self.now)],
                         ["Carol"])

    def test_an_error_between_busy_runs_is_waited_out(self):
        self.results = [(2, "timeout"), (1, "busy"), (2, "timeout"),
                        (2, "timeout"), (1, "busy"), (0, "")]
        self.assertEqual(self.run_cli("hold", "20", "timing"), 0)
        self.assertEqual(len(self.calls), 6)

    def test_running_out_keeps_the_hold_and_exits_3(self):
        self.results = [(1, "deploy 41 building")] * 100
        self.assertEqual(self.run_cli("hold", "20", "timing"), 3)
        self.assertLessEqual(self.now, T0 + minutes(5))
        self.assertIn("deploy 41 building", self.lines[-1])
        self.assertEqual([h[0] for h in quiet.live(self.dir, self.now)],
                         ["Carol"])

    def test_no_settle_and_release_skip_it(self):
        self.assertEqual(self.run_cli("hold", "20", "x", "--no-settle"), 0)
        self.assertEqual(self.run_cli("release"), 0)
        self.assertEqual(self.run_cli("status"), 0)
        self.assertEqual(self.calls, [])

    def test_a_forge_tool_runs_from_the_installation(self):
        self.write({"dir": self.dir, "settle": ["forge-thing"]})
        self.assertEqual(self.run_cli("hold", "20", "x"), 0)
        self.assertEqual(self.calls[0][0], [os.path.join(fq.BIN,
                                                         "forge-thing")])

    def test_one_real_run_reports_what_the_command_said(self):
        status, text = fq.run_settle(
            [sys.executable, "-c", "import sys; print('busy', flush=True); "
             "print('more', file=sys.stderr); sys.exit(1)"], None, None)
        self.assertEqual((status, text.split()), (1, ["busy", "more"]))
        status, text = fq.run_settle(
            [os.path.join(self._tmp.name, "missing")], None, None)
        self.assertEqual(status, 127)


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
        data["quiet"] = {"dir": "/x", "settle": [], "settle_minutes": 0}
        problems = config.check(data)
        self.assertIn("quiet.settle must be a non-empty list of strings",
                      problems)
        self.assertIn("quiet.settle_minutes must be at least 1", problems)
        data["quiet"] = {"dir": "/x", "settle": ["tools/busy"],
                         "settle_minutes": 10}
        self.assertEqual(config.check(data), [])


if __name__ == "__main__":
    unittest.main()
