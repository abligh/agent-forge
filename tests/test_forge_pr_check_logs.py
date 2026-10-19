"""Tests for forge-pr-check's step logs: each step's whole output, a
time on every line (sh_logged, check's `logs`, prune_logs). sh_logged
runs real programs, since what is under test is how it reads them."""

import datetime
import gzip
import os
import sys
import tempfile
import unittest
import unittest.mock

from agent_forge import config
from tests.helpers import example, load_tool

pc = load_tool("forge-pr-check")
pc.configure(config.Project(example(), None))

T0 = datetime.datetime(2026, 10, 19, 12, 0, tzinfo=datetime.timezone.utc)


def lines_of(path):
    with gzip.open(path, "rt", encoding="utf-8") as handle:
        return handle.read().splitlines()


def parsed(line):
    seconds, stream, text = line.split(" ", 2)
    return float(seconds), int(stream), text


def python(code):
    return [sys.executable, "-c", code]


class ShLogged(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.log = os.path.join(self._tmp.name, "step.log.gz")

    def tearDown(self):
        self._tmp.cleanup()

    def test_each_line_has_its_time_and_stream_and_the_output_comes_back(self):
        got = pc.sh_logged(python(
            "import sys, time\n"
            "print('one', flush=True)\n"
            "time.sleep(0.3)\n"
            "print('two', file=sys.stderr, flush=True)\n"
            "sys.exit(3)\n"), log=self.log)
        self.assertEqual((got.returncode, got.stdout, got.stderr),
                         (3, "one\ntwo\n", ""))
        rows = [parsed(line) for line in lines_of(self.log)]
        self.assertEqual([(s, t) for _, s, t in rows],
                         [(1, "one"), (2, "two")])
        self.assertGreaterEqual(rows[1][0] - rows[0][0], 0.25)

    def test_a_last_line_without_a_newline_is_kept(self):
        got = pc.sh_logged(python(
            "import sys; sys.stdout.write('end')"), log=self.log)
        self.assertEqual(got.stdout, "end\n")
        self.assertEqual([parsed(line)[1:] for line in lines_of(self.log)],
                         [(1, "end")])

    def test_the_output_comes_back_in_the_order_it_was_written(self):
        # Stdout then stderr put a failed step's earlier
        # compiler lines after its failure, at the end of the tail.
        got = pc.sh_logged(python(
            "import sys, time\n"
            "print('compiling', file=sys.stderr, flush=True)\n"
            "time.sleep(0.2)\n"
            "print('FAILED: / is 302, got 502', flush=True)\n"
            "sys.exit(1)\n"), log=self.log)
        self.assertEqual(got.stdout,
                         "compiling\nFAILED: / is 302, got 502\n")
        self.assertEqual(pc.tail(got.stdout + got.stderr).splitlines()[-1],
                         "FAILED: / is 302, got 502")

    def test_a_timeout_is_exit_124_and_says_so_in_the_log(self):
        got = pc.sh_logged(python(
            "import time\nprint('started', flush=True)\ntime.sleep(30)\n"),
            timeout=1, log=self.log)
        self.assertEqual((got.returncode, got.stdout, got.stderr),
                         (124, "started\n", "timed out after 1s"))
        lines = lines_of(self.log)
        self.assertEqual(parsed(lines[0])[1:], (1, "started"))
        self.assertEqual(parsed(lines[-1])[1:], (0, "[timed out after 1s]"))

    def test_the_log_stops_at_its_limit_but_the_output_does_not(self):
        with unittest.mock.patch.object(pc, "LOGS_MAX", 200):
            got = pc.sh_logged(python(
                "for i in range(100): print('line %d' % i)"), log=self.log)
        self.assertEqual(len(got.stdout.splitlines()), 100)
        lines = lines_of(self.log)
        self.assertLess(len(lines), 20)
        self.assertEqual(parsed(lines[-1])[1:],
                         (0, "[log stopped at 200 bytes]"))

    def test_non_utf8_output_is_replaced_not_fatal(self):
        got = pc.sh_logged(python(
            "import sys; sys.stdout.buffer.write(b'a\\xffb\\n')"),
            log=self.log)
        self.assertEqual(got.returncode, 0)
        self.assertEqual(parsed(lines_of(self.log)[0])[2], "a�b")


class CheckLogs(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wt = os.path.join(self._tmp.name, "wt")
        self.logs = os.path.join(self._tmp.name, "logs", "a" * 40)
        os.makedirs(self.wt)

    def tearDown(self):
        self._tmp.cleanup()

    def test_with_a_stream_each_step_writes_its_own_log(self):
        calls = []

        def stream(args, cwd=None, env=None, timeout=None, log=None):
            calls.append(log)
            with gzip.open(log, "wt") as handle:
                handle.write("0.000 1 hi\n")
            return pc.subprocess.CompletedProcess(args, 0, "hi\n", "")

        def run(*args, **kw):
            raise AssertionError("run is not used for a step with a stream")

        ok, results = pc.check(self.wt, {}, run, logs=self.logs,
                               stream=stream)
        self.assertTrue(ok)
        self.assertEqual(
            calls, [os.path.join(self.logs, pc.results_name(r["name"])
                                 + ".log.gz") for r in results])
        self.assertEqual(sorted(os.listdir(self.logs)), sorted(
            pc.results_name(r["name"]) + ".log.gz" for r in results))

    def test_without_a_stream_nothing_is_logged(self):
        ok, _ = pc.check(self.wt, {}, lambda args, **kw:
                         pc.subprocess.CompletedProcess(args, 0, "", ""),
                         logs=self.logs)
        self.assertTrue(ok)
        self.assertFalse(os.path.exists(self.logs))


class PruneLogs(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def folder(self, name, age):
        path = os.path.join(self.state_dir, pc.LOGS_DIR, name)
        os.makedirs(path)
        when = (T0 - age).timestamp()
        os.utime(path, (when, when))
        return path

    def test_logs_go_once_they_are_old(self):
        old = self.folder("a" * 40, pc.LOGS_AGE + datetime.timedelta(hours=1))
        young = self.folder("b" * 40, datetime.timedelta(days=2))
        pc.prune_logs(self.state_dir, T0)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.isdir(young))

    def test_no_logs_directory_is_fine(self):
        pc.prune_logs(self.state_dir, T0)


if __name__ == "__main__":
    unittest.main()
