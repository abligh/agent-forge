"""Tests for agent_forge.locks. Locks are taken in subprocesses where a
second holder must be another process: flock is per open file, so one
process could take the same lock twice through two handles, but that
is not what a tool meets."""
import os
import subprocess
import sys
import tempfile
import unittest

from agent_forge import locks
from tests.helpers import ROOT

HOLD = """
import sys, time
sys.path.insert(0, %r)
from agent_forge import locks
h = locks.try_lock(sys.argv[1], "held by child")
print("ok" if h else "busy", flush=True)
time.sleep(30)
""" % str(ROOT)


class Locks(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, "worker.lock")

    def hold_elsewhere(self):
        child = subprocess.Popen([sys.executable, "-c", HOLD, self.path],
                                 stdout=subprocess.PIPE, text=True)
        self.addCleanup(child.stdout.close)
        self.addCleanup(child.wait)
        self.addCleanup(child.kill)
        self.assertEqual(child.stdout.readline().strip(), "ok")
        return child

    def test_one_holder_at_a_time(self):
        self.hold_elsewhere()
        self.assertIsNone(locks.try_lock(self.path))

    def test_the_holder_names_itself_and_a_dead_one_is_gone(self):
        child = self.hold_elsewhere()
        self.assertEqual(locks.holder(self.path), "held by child")
        child.kill()
        child.wait()
        self.assertIsNone(locks.holder(self.path))
        mine = locks.try_lock(self.path, "me")
        self.addCleanup(mine.close)
        with open(self.path) as handle:
            self.assertEqual(handle.read(), "me\n")

    def test_no_file_is_no_holder(self):
        self.assertIsNone(locks.holder(self.path))

    def test_locked_waits_and_releases(self):
        with locks.locked(os.path.join(self.tmp.name, "sub", "state.lock")):
            pass
        self.assertTrue(os.path.exists(
            os.path.join(self.tmp.name, "sub", "state.lock")))


class StateDir(unittest.TestCase):

    def test_under_the_xdg_state_home_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = locks.state_dir("widget", "merger",
                                   {"XDG_STATE_HOME": tmp})
            self.assertEqual(path, os.path.join(tmp, "agent-forge", "widget",
                                                "merger"))
            self.assertTrue(os.path.isdir(path))

    def test_a_reader_creates_nothing(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = locks.state_dir("widget", "nightly",
                                   {"FORGE_STATE_DIR": tmp}, create=False)
            self.assertEqual(path, os.path.join(tmp, "widget", "nightly"))
            self.assertFalse(os.path.exists(os.path.join(tmp, "widget")))

    def test_forge_state_dir_moves_the_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                locks.state_dir("widget", "router", {"FORGE_STATE_DIR": tmp,
                                                     "XDG_STATE_HOME": "/x"}),
                os.path.join(tmp, "widget", "router"))

    def test_the_home_is_the_given_environments(self):
        """Not the process's: a caller passing an environment means it."""
        with tempfile.TemporaryDirectory() as tmp:
            self.assertEqual(
                locks.state_dir("widget", "router", {"HOME": tmp}),
                os.path.join(tmp, ".local", "state", "agent-forge", "widget",
                             "router"))


if __name__ == "__main__":
    unittest.main()
