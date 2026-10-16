"""Tests for bin/forge-cron-run, with real git: an origin, a cron
checkout of it, and tools that report what they saw."""
import fcntl
import os
import subprocess
import tempfile
import unittest

from agent_forge import github
from tests.helpers import Repo, load_tool

tool = load_tool("forge-cron-run")

REPORT = """#!/bin/sh
echo "cwd=$(pwd)"
echo "args=$*"
echo "token=${GH_TOKEN:-none}"
echo "path=$PATH"
exit ${EXIT:-0}
"""


class CronRun(unittest.TestCase):

    def setUp(self):
        self.r = Repo(self)
        self.origin = os.path.join(self.r.dir, "origin")
        self.work = os.path.join(self.r.dir, "work")
        self.cron = os.path.join(self.r.dir, "cron")
        self.logs = os.path.join(self.r.dir, "logs")
        self.r.git(self.r.dir, "init", "-q", "--bare", "-b", "trunk",
                   self.origin)
        self.r.git(self.r.dir, "clone", "-q", self.origin, self.work)
        self.r.git(self.work, "checkout", "-q", "-b", "trunk")
        os.makedirs(os.path.join(self.work, ".agent-forge"))
        with open(os.path.join(self.work, ".agent-forge", "config.json"),
                  "w") as handle:
            handle.write('{"project": "widget", "repo": "example/widget", '
                         '"owner": "o", "default_branch": "trunk", '
                         '"naming": {"app_slug_suffix": "w"}, '
                         '"agents": {"A": {"app_id": 1, '
                         '"installation_id": 2}}}')
        self.add_tool("report", REPORT)
        self.push("first")
        self.r.git(self.r.dir, "clone", "-q", "-b", "trunk", self.origin,
                   self.cron)
        self.env = dict(self.r.env, FORGE_CRON_CHECKOUT=self.cron,
                        FORGE_CRON_LOGDIR=self.logs,
                        FORGE_CRON_LOCK_WAIT_S="1",
                        FORGE_CRON_PATH="/opt/tool/bin")
        self.err = []

    def add_tool(self, name, text):
        os.makedirs(os.path.join(self.work, "tools"), exist_ok=True)
        path = os.path.join(self.work, "tools", name)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, 0o755)

    def push(self, message):
        self.r.git(self.work, "add", "-A")
        self.r.git(self.work, "-c", "user.name=t", "-c", "user.email=t@t",
                   "commit", "-q", "--allow-empty", "-m", message)
        self.r.git(self.work, "push", "-q", "origin", "trunk")
        return self.r.git(self.work, "rev-parse", "--short",
                          "HEAD").stdout.strip()

    def run_tool(self, *argv, env=None, mint=None):
        # Not the real installation: its lock file would land beside it.
        return tool.main(list(argv), dict(self.env, **(env or {})),
                         mint or (lambda repo, app: "ghs_" + app),
                         err=self.err.append,
                         install=os.path.join(self.r.dir, "install"))

    def log(self, name="report"):
        with open(os.path.join(self.logs, name + ".log")) as handle:
            return handle.read()

    def test_runs_the_reviewed_copy_in_the_checkout_and_logs_it(self):
        head = self.push("second")
        self.assertEqual(self.run_tool("report", "x", "y"), 0)
        log = self.log()
        self.assertIn("at %s\n" % head, log)
        self.assertIn("cwd=%s\n" % self.cron, log)
        self.assertIn("args=x y\n", log)
        self.assertIn("token=none\n", log)
        self.assertTrue(log.rstrip().endswith("exit=0"))

    def test_path_is_this_release_the_hosts_toolchains_then_the_system(self):
        self.run_tool("report")
        path = [l for l in self.log().splitlines()
                if l.startswith("path=")][0][5:].split(":")
        # A tool a job runs by name comes from the release the job
        # started on (designs/0015), not through a symlink that moves.
        self.assertEqual(path[0], tool.HERE)
        self.assertEqual(path[1], "/opt/tool/bin")
        self.assertEqual(path[-3:], ["/usr/local/bin", "/usr/bin", "/bin"])

    def test_local_edits_are_reset(self):
        with open(os.path.join(self.cron, "tools", "report"), "a") as handle:
            handle.write("echo tampered\n")
        self.run_tool("report")
        self.assertNotIn("tampered", self.log())

    def test_the_tools_status_passes_through_unchanged(self):
        for status in (1, 2, 7):
            self.assertEqual(self.run_tool("report",
                                           env={"EXIT": str(status)}), status)
            self.assertTrue(self.log().rstrip().endswith("exit=%d" % status))

    def test_a_signal_is_reported_as_a_shell_would(self):
        self.add_tool("die", "#!/bin/sh\nkill -9 $$\n")
        self.push("die")
        self.assertEqual(self.run_tool("die"), 137)

    def test_no_such_tool_or_a_path_is_2(self):
        for name in ("nothing", "../report"):
            self.assertEqual(self.run_tool(name), 2)
        self.assertIn("NO-SUCH-TOOL", self.log("nothing"))

    def test_forge_tools_are_this_installations(self):
        self.assertEqual(self.run_tool("forge-config", "get", "repo"), 0)
        self.assertIn("example/widget\n", self.log("forge-config"))

    def install_at(self):
        """A stand-in installation: a git repository with one commit."""
        install = os.path.join(self.r.dir, "install")
        self.r.git(self.r.dir, "init", "-q", install)
        self.r.git(install, "-c", "user.name=t", "-c", "user.email=t@t",
                   "commit", "-q", "--allow-empty", "-m", "v0")
        return install, self.r.git(install, "rev-parse",
                                   "HEAD").stdout.strip()

    def pin(self, text):
        with open(os.path.join(self.work, ".agent-forge", "pin"), "w") as h:
            h.write(text)
        self.push("pin")

    def run_installed(self, install):
        return tool.main(["report"], self.env, lambda repo, app: "x",
                         err=self.err.append, install=install)

    def test_an_installation_at_the_pin_runs(self):
        install, sha = self.install_at()
        self.pin("%s v0.1.0\n" % sha.upper())
        self.assertEqual(self.run_installed(install), 0)
        self.assertIn("cwd=", self.log())

    def test_an_installation_not_at_the_pin_runs_nothing(self):
        install, sha = self.install_at()
        self.pin("%s v0.1.0\n" % ("0" * 40))
        self.assertEqual(self.run_installed(install), 2)
        log = self.log()
        self.assertIn("PIN-MISMATCH: agent-forge here is at %s" % sha[:12], log)
        self.assertNotIn("cwd=", log)

    def test_the_pin_is_the_reviewed_branchs(self):
        install, sha = self.install_at()
        self.pin(sha + "\n")
        with open(os.path.join(self.cron, ".agent-forge", "pin"), "w") as h:
            h.write("0" * 40)
        self.assertEqual(self.run_installed(install), 0)

    def test_a_pin_that_isnt_a_full_sha_runs_nothing(self):
        install, sha = self.install_at()
        for text in ("v0.1.0\n", sha[:12], ""):
            self.pin(text)
            self.assertEqual(self.run_installed(install), 2)
            self.assertIn("BAD-PIN", self.log())

    def test_a_pin_needs_an_installation_with_a_commit(self):
        _, sha = self.install_at()
        self.pin(sha)
        self.assertEqual(self.run_installed(os.path.join(self.r.dir, "x")), 2)
        self.assertIn("isn't a git checkout", self.log())

    def test_no_pin_checks_nothing(self):
        self.assertEqual(self.run_installed(os.path.join(self.r.dir, "x")), 0)

    # The installation's lock (forge-install moves it only between jobs).

    HOLDS = """#!/bin/sh
echo "fd=${FORGE_INSTALL_LOCK_FD:-none}"
if flock -n -x "$LOCK" true; then echo lock=free; else echo lock=held; fi
"""

    def test_the_tool_holds_the_installations_lock_for_its_run(self):
        install, sha = self.install_at()
        self.add_tool("holds", self.HOLDS)
        self.pin("%s v0.1.0\n" % sha)
        lock = install + ".lock"
        self.assertEqual(tool.main(["holds"], dict(self.env, LOCK=lock),
                                   lambda repo, app: "x",
                                   err=self.err.append, install=install), 0)
        log = self.log("holds")
        self.assertIn("lock=held\n", log)
        self.assertNotIn("fd=none", log)
        # And lets it go when the run ends.
        with open(lock) as handle:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)

    def test_nothing_starts_while_an_install_is_wanted(self):
        install, sha = self.install_at()
        self.pin("%s v0.1.0\n" % sha)
        open(install + ".install-wanted", "w").close()
        self.assertEqual(self.run_installed(install), 2)
        log = self.log()
        self.assertIn("INSTALL-PENDING", log)
        self.assertNotIn("cwd=", log)

    def test_a_stale_install_marker_is_ignored(self):
        install, sha = self.install_at()
        self.pin("%s v0.1.0\n" % sha)
        marker = install + ".install-wanted"
        open(marker, "w").close()
        os.utime(marker, (1, 1))
        self.assertEqual(self.run_installed(install), 0)

    def test_an_install_in_progress_is_waited_for_then_given_up_on(self):
        install, sha = self.install_at()
        self.pin("%s v0.1.0\n" % sha)
        with open(install + ".lock", "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            self.assertEqual(self.run_installed(install), 2)
        self.assertIn("LOCK-TIMEOUT: forge-install held", self.log())

    def test_no_checkout_is_2_and_says_so(self):
        self.assertEqual(self.run_tool("report", env={
            "FORGE_CRON_CHECKOUT": os.path.join(self.r.dir, "none")}), 2)
        self.assertIn("NO-CHECKOUT", self.log())

    def test_no_checkout_named_is_2(self):
        env = dict(self.env)
        del env["FORGE_CRON_CHECKOUT"]
        self.assertEqual(tool.main(["report"], env, err=self.err.append), 2)

    def test_a_failed_fetch_is_2_and_the_tool_does_not_run(self):
        self.r.git(self.cron, "remote", "set-url", "origin",
                   os.path.join(self.r.dir, "gone"))
        self.assertEqual(self.run_tool("report"), 2)
        self.assertIn("SYNC-FAILED: fetch", self.log())
        self.assertNotIn("cwd=", self.log())

    def test_an_app_token_for_the_job(self):
        self.run_tool("report", env={"FORGE_CRON_APP": "Merger"})
        self.assertIn("as Merger\n", self.log())
        self.assertIn("token=ghs_Merger\n", self.log())

    def test_no_app_token_is_2_never_the_hosts_login(self):
        def mint(repo, app):
            raise github.MintError(3, "refused")
        self.assertEqual(self.run_tool("report", env={"FORGE_CRON_APP": "M"},
                                       mint=mint), 2)
        self.assertIn("APP-TOKEN-FAILED", self.log())
        self.assertNotIn("cwd=", self.log())

    def hold(self, path, exclusive=True):
        os.makedirs(self.logs, exist_ok=True)
        handle = open(path, "a")
        fcntl.flock(handle, fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        self.addCleanup(handle.close)
        return handle

    def checkout_lock(self, suffix="lock"):
        key = os.path.realpath(self.cron).strip("/").replace("/", "_")
        return os.path.join(self.logs, ".forge-cron.%s.%s" % (key, suffix))

    def test_one_run_of_a_tool_at_a_time_and_a_give_up_is_logged(self):
        self.hold(os.path.join(self.logs, ".report.lock"))
        self.assertEqual(self.run_tool("report"), 2)
        log = self.log()
        self.assertIn("LOCK-TIMEOUT: another report run held its own lock",
                      log)
        self.assertTrue(log.rstrip().endswith("exit=2"))

    def test_jobs_share_a_current_checkout(self):
        self.run_tool("report")
        self.hold(self.checkout_lock(), exclusive=False)
        self.assertEqual(self.run_tool("report"), 0)

    def test_a_behind_checkout_waits_for_running_jobs_and_asks_its_turn(self):
        self.hold(self.checkout_lock(), exclusive=False)
        self.push("moved")
        self.assertEqual(self.run_tool("report"), 2)
        self.assertTrue(os.path.exists(self.checkout_lock("sync-wanted")))

    def test_an_old_wrappers_lock_held_for_a_job_is_waited_for(self):
        # Another wrapper running a job from the same checkout holds its
        # own lock exclusively; with FORGE_CRON_LOCK naming it, nothing
        # runs, and nothing is reset, until that job is done.
        old = os.path.join(self.logs, ".old-cron.lock")
        self.run_tool("report")
        self.push("moved")
        held = self.hold(old)
        self.assertEqual(self.run_tool("report", env={"FORGE_CRON_LOCK": old}),
                         2)
        self.assertIn("another job held the checkout's lock", self.log())
        held.close()
        self.assertEqual(self.run_tool("report", env={"FORGE_CRON_LOCK": old}),
                         0)

    def test_without_forge_cron_lock_an_old_wrappers_lock_is_not_seen(self):
        old = os.path.join(self.logs, ".old-cron.lock")
        self.run_tool("report")
        self.hold(old)
        self.assertEqual(self.run_tool("report"), 0)

    def test_a_job_holds_the_named_lock_while_it_runs(self):
        old = os.path.join(self.logs, ".old-cron.lock")
        self.add_tool("probe", "#!/usr/bin/env python3\n"
                      "import fcntl, sys\n"
                      "h = open(%r, 'a')\n"
                      "try:\n"
                      "    fcntl.flock(h, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
                      "except OSError:\n"
                      "    sys.exit(7)\n" % old)
        self.push("probe")
        self.assertEqual(self.run_tool("probe", env={"FORGE_CRON_LOCK": old}),
                         7)

    def test_with_a_reset_wanted_a_job_queues_rather_than_joins(self):
        self.run_tool("report")
        open(self.checkout_lock("sync-wanted"), "w").close()
        held = self.hold(self.checkout_lock(), exclusive=False)
        self.assertEqual(self.run_tool("report"), 2)
        held.close()
        self.assertEqual(self.run_tool("report"), 0)
        self.assertFalse(os.path.exists(self.checkout_lock("sync-wanted")))


if __name__ == "__main__":
    unittest.main()
