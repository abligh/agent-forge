"""Tests for bin/forge-install, with real git: an upstream standing in
for agent-forge, a project checkout with a pin, and an installation."""
import fcntl
import os
import subprocess
import unittest
from unittest import mock

from agent_forge import locks
from tests.helpers import Repo, load_tool

tool = load_tool("forge-install")


class Install(unittest.TestCase):

    def setUp(self):
        self.r = Repo(self)
        self.upstream = os.path.join(self.r.dir, "upstream")
        self.r.git(self.r.dir, "init", "-q", "-b", "main", self.upstream)
        self.v1 = self.commit("v1")
        self.v2 = self.commit("v2")
        self.url = "file://" + self.upstream
        self.checkout = os.path.join(self.r.dir, "project")
        os.makedirs(os.path.join(self.checkout, ".agent-forge"))
        self.install = os.path.join(self.r.dir, "agent-forge")
        self.out, self.err = [], []
        self.naps = []
        # git as the tests' git: the user's own configuration kept out.
        self.git = lambda args, cwd=None: subprocess.run(
            ["git"] + args, cwd=cwd, capture_output=True, text=True,
            env=self.r.env)

    def commit(self, message):
        self.r.git(self.upstream, "-c", "user.name=t", "-c",
                   "user.email=t@t", "commit", "-q", "--allow-empty",
                   "-m", message)
        return self.r.git(self.upstream, "rev-parse",
                          "HEAD").stdout.strip()

    def pin(self, text):
        with open(os.path.join(self.checkout, ".agent-forge", "pin"),
                  "w") as handle:
            handle.write(text)

    def main(self, *argv, wait="0"):
        self.out, self.err = [], []
        return tool.main(["--checkout", self.checkout, "--install",
                          self.install, "--url", self.url, "--wait", wait]
                         + list(argv), self.git, self.out.append,
                         self.err.append, {}, nap=self.naps.append)

    def head(self):
        return self.r.git(self.install, "rev-parse", "HEAD").stdout.strip()

    def test_no_pin_installs_nothing(self):
        self.assertEqual(self.main(), 0)
        self.assertIn("NO-PIN", self.out[0])
        self.assertFalse(os.path.exists(self.install))

    def test_a_first_install_is_cloned_at_the_pin(self):
        self.pin("%s v1\n" % self.v1)
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.head(), self.v1)
        self.assertIn("INSTALLED", self.out[0])
        self.assertFalse(os.path.exists(self.install + ".new"))

    def test_a_new_pin_is_installed_and_then_left_alone(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v2\n" % self.v2)
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.head(), self.v2)
        self.assertEqual(self.main(), 0)
        self.assertIn("AT-PIN", self.out[0])
        self.assertFalse(os.path.exists(
            locks.install_wanted_path(self.install)))

    def test_only_the_pinned_sha_never_a_moved_tag(self):
        self.r.git(self.upstream, "tag", "v1", self.v1)
        self.pin("%s v1\n" % self.v1)
        self.main()
        v3 = self.commit("v3")
        self.r.git(self.upstream, "tag", "-f", "v1", v3)
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.head(), self.v1)

    def test_a_pin_that_isnt_a_full_sha_is_refused(self):
        self.pin("v1\n")
        self.assertEqual(self.main(), 2)
        self.assertIn("BAD-PIN", self.err[0])

    def test_a_pin_upstream_doesnt_have_changes_nothing(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v9\n" % ("1" * 40))
        self.assertEqual(self.main(), 1)
        self.assertEqual(self.head(), self.v1)

    def test_a_dirty_or_foreign_installation_is_refused(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v2\n" % self.v2)
        with open(os.path.join(self.install, "local"), "w") as handle:
            handle.write("x")
        self.assertEqual(self.main(), 2)
        self.assertIn("DIRTY", self.err[0])
        os.unlink(os.path.join(self.install, "local"))
        self.r.git(self.install, "remote", "set-url", "origin",
                   "file:///elsewhere")
        self.assertEqual(self.main(), 2)
        self.assertIn("FOREIGN", self.err[0])
        self.assertEqual(self.head(), self.v1)

    def test_a_running_job_is_waited_for_and_new_ones_are_held_off(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v2\n" % self.v2)
        with open(locks.install_lock_path(self.install), "a") as job:
            fcntl.flock(job, fcntl.LOCK_SH)
            self.assertEqual(self.main(), 1)
            self.assertIn("still hold", self.err[0])
            self.assertEqual(self.head(), self.v1)
            # forge-cron-run starts nothing new meanwhile.
            self.assertTrue(locks.install_wanted(self.install))
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.head(), self.v2)
        self.assertFalse(locks.install_wanted(self.install))

    def test_dry_run_changes_nothing(self):
        self.pin("%s v1\n" % self.v1)
        self.assertEqual(self.main("--dry-run"), 0)
        self.assertIn("WOULD-CLONE", self.out[0])
        self.assertFalse(os.path.exists(self.install))
        self.main()
        self.pin("%s v2\n" % self.v2)
        self.assertEqual(self.main("--dry-run"), 0)
        self.assertIn("WOULD-INSTALL", self.out[0])
        self.assertEqual(self.head(), self.v1)

    def test_no_checkout_is_2(self):
        self.assertEqual(tool.main(["--install", self.install], self.git,
                                   self.out.append, self.err.append, {}), 2)



class Versions(Install):
    """--versions (designs/0015): each release beside the last, and the
    installation a symlink that moves between them."""

    def main(self, *argv, wait="0"):
        return super().main("--versions", *argv, wait=wait)

    def release(self, sha):
        return os.path.join(self.install + ".d", sha)

    def test_a_new_pin_goes_beside_and_the_link_moves(self):
        self.pin("%s v1\n" % self.v1)
        self.assertEqual(self.main(), 0)
        self.assertTrue(os.path.islink(self.install))
        self.assertEqual(os.path.realpath(self.install),
                         self.release(self.v1))
        self.pin("%s v2\n" % self.v2)
        self.assertEqual(self.main(), 0)
        self.assertIn("INSTALLED", self.out[0])
        self.assertEqual(os.path.realpath(self.install),
                         self.release(self.v2))
        self.assertEqual(self.head(), self.v2)
        # The release a job may still run is left as it was.
        self.assertEqual(self.r.git(self.release(self.v1), "rev-parse",
                                    "HEAD").stdout.strip(), self.v1)
        self.assertEqual(self.r.git(self.release(self.v2), "remote",
                                    "get-url", "origin").stdout.strip(),
                         self.url)
        self.assertEqual(self.main(), 0)
        self.assertIn("AT-PIN", self.out[0])

    def test_a_running_job_is_not_waited_for(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v2\n" % self.v2)
        old = self.release(self.v1)
        with open(locks.install_lock_path(old), "a") as job:
            fcntl.flock(job, fcntl.LOCK_SH)
            self.assertEqual(self.main(), 0)
            self.assertEqual(self.head(), self.v2)
            self.assertEqual(self.naps, [])
            # Nothing is held off: there is no marker in this layout.
            self.assertFalse(locks.install_wanted(self.install))
            self.assertFalse(locks.install_wanted(old))
            with mock.patch.object(tool, "RETIRE_AFTER_S", 0):
                self.main()
            self.assertTrue(os.path.isdir(old), "pruned under its job")
        with mock.patch.object(tool, "RETIRE_AFTER_S", 0):
            self.assertEqual(self.main(), 0)
        self.assertIn("deleted %s" % self.v1[:12], self.out[0])
        self.assertFalse(os.path.exists(old))
        self.assertFalse(os.path.exists(old + ".retired"))
        self.assertTrue(os.path.isdir(self.release(self.v2)))

    def test_a_retired_release_is_kept_for_a_while(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v2\n" % self.v2)
        self.main()
        self.main()
        self.assertTrue(os.path.isdir(self.release(self.v1)))

    def test_a_directory_is_moved_once_the_old_way(self):
        self.pin("%s v1\n" % self.v1)
        self.assertEqual(super().main(), 0)
        self.assertFalse(os.path.islink(self.install))
        self.pin("%s v2\n" % self.v2)
        with open(locks.install_lock_path(self.install), "a") as job:
            fcntl.flock(job, fcntl.LOCK_SH)
            self.assertEqual(self.main(), 1)
            self.assertTrue(locks.install_wanted(self.install))
            self.assertFalse(os.path.islink(self.install))
        self.assertEqual(self.main(), 0)
        self.assertIn("moved into", self.out[0])
        self.assertTrue(os.path.islink(self.install))
        self.assertEqual(self.head(), self.v2)
        self.assertTrue(os.path.isdir(self.release(self.v1)))
        self.assertFalse(locks.install_wanted(self.install))

    def test_without_versions_a_symlink_is_refused(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v2\n" % self.v2)
        self.assertEqual(super().main(), 2)
        self.assertIn("VERSIONED", self.err[0])
        self.assertEqual(self.head(), self.v1)

    def test_a_failed_clone_leaves_the_link_where_it_was(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v9\n" % ("1" * 40))
        self.assertEqual(self.main(), 1)
        self.assertEqual(os.path.realpath(self.install),
                         self.release(self.v1))
        self.assertFalse(os.path.exists(self.release("1" * 40) + ".new"))

    def test_the_installation_is_the_link_from_inside_a_release(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        here = os.path.join(self.release(self.v1), "bin")
        with mock.patch.object(tool, "HERE", here):
            self.assertEqual(tool.default_install(), self.install)
        with mock.patch.object(tool, "HERE", os.path.join(self.r.dir, "x",
                                                          "bin")):
            self.assertEqual(tool.default_install(),
                             os.path.join(self.r.dir, "x"))

    def test_one_installer_at_a_time(self):
        self.pin("%s v1\n" % self.v1)
        self.main()
        self.pin("%s v2\n" % self.v2)
        with open(self.install + ".installer.lock", "a") as other:
            fcntl.flock(other, fcntl.LOCK_EX)
            self.assertEqual(self.main(), 0)
            self.assertIn("BUSY", self.out[0])
            self.assertEqual(self.head(), self.v1)
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.head(), self.v2)

    def test_a_move_done_meanwhile_is_not_done_again(self):
        # Another run moved the installation while this one waited for
        # the jobs: the release it made must survive.
        self.pin("%s v1\n" % self.v1)
        super().main()
        real = tool.drained
        moved = []

        def drained(install, *args):
            if not moved:
                moved.append(True)
                tool.move_into_versions(install, self.url, 0, self.git)
            return real(install, *args)
        with mock.patch.object(tool, "drained", drained):
            release = tool.move_into_versions(self.install, self.url, 0,
                                              self.git)
        self.assertTrue(os.path.islink(self.install))
        self.assertEqual(os.path.realpath(self.install), release)
        self.assertEqual(self.head(), self.v1)
        self.assertFalse(locks.install_wanted(self.install))

    # Inherited tests that describe the in-place layout.
    test_a_running_job_is_waited_for_and_new_ones_are_held_off = None

    def test_dry_run_changes_nothing(self):
        self.pin("%s v1\n" % self.v1)
        self.assertEqual(self.main("--dry-run"), 0)
        self.assertFalse(os.path.lexists(self.install))
        self.main()
        self.pin("%s v2\n" % self.v2)
        self.assertEqual(self.main("--dry-run"), 0)
        self.assertIn("WOULD-INSTALL", self.out[0])
        self.assertEqual(self.head(), self.v1)


if __name__ == "__main__":
    unittest.main()
