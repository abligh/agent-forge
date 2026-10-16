"""Tests for forge-prune-builds.

The failure that matters is **deleting something that was not a
cache**, or a cache a build was using. So the negative cases assert
that a file is still there, and every rule that keeps a directory has a
test where only that rule stands between the directory and deletion.
"""

import fcntl
import os
import tempfile
import unittest

from tests.helpers import load_tool

prune = load_tool("forge-prune-builds")

NOW = 1_800_000_000.0
HOUR = 3600.0
TAG = b"Signature: 8a477f597d28d172789f06886806bc55\n# a cache\n"


class Fixture:
    def __init__(self, test):
        tmp = tempfile.TemporaryDirectory()
        test.addCleanup(tmp.cleanup)
        self.root = tmp.name

    def worktree(self, name):
        wt = os.path.join(self.root, name)
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: /nowhere\n")
        with open(os.path.join(wt, "main.rs"), "w") as f:
            f.write("fn main() {}\n")
        return wt

    def cache(self, wt, rel="target", age_hours=100, tag=TAG, locks=("debug",)):
        path = os.path.join(wt, rel)
        os.makedirs(os.path.join(path, "debug", "deps"))
        if tag is not None:
            with open(os.path.join(path, "CACHEDIR.TAG"), "wb") as f:
                f.write(tag)
        artefact = os.path.join(path, "debug", "deps", "libx.rlib")
        with open(artefact, "wb") as f:
            f.write(b"x" * 4096)
        for profile in locks:
            os.makedirs(os.path.join(path, profile), exist_ok=True)
            open(os.path.join(path, profile, ".cargo-lock"), "w").close()
        t = NOW - age_hours * HOUR
        for dirpath, _dirs, files in os.walk(path):
            for name in files:
                os.utime(os.path.join(dirpath, name), (t, t))
        return path


def run(fx, *, finished=None, busy=(), branch="feature", dry_run=False, **kw):
    lines = []
    asked = []

    def finished_of(repo, token, br):
        asked.append(br)
        return finished

    result = prune.prune(
        [fx.root], dry_run=dry_run, merged_hours=kw.get("merged_hours", 6),
        idle_hours=kw.get("idle_hours", 48), repo="o/r", token=None, now=NOW,
        busy=set(busy), branch_of=lambda wt: branch, finished_of=finished_of,
        out=lines.append)
    return result, lines, asked


class Rules(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)
        self.wt = self.fx.worktree("a")

    def test_long_idle_is_cleared_without_asking_github(self):
        cache = self.fx.cache(self.wt, age_hours=49)
        (cleared, kept, _), lines, asked = run(self.fx, finished=False)
        self.assertEqual((cleared, kept), (1, 0))
        self.assertFalse(os.path.exists(cache))
        self.assertEqual(asked, [])
        self.assertTrue(os.path.exists(os.path.join(self.wt, "main.rs")))

    def test_recent_is_kept_even_when_merged(self):
        cache = self.fx.cache(self.wt, age_hours=5)
        (cleared, kept, _), lines, asked = run(self.fx, finished=True)
        self.assertEqual((cleared, kept), (0, 1))
        self.assertTrue(os.path.exists(cache))
        self.assertEqual(asked, [])

    def test_between_thresholds_cleared_only_when_finished(self):
        cache = self.fx.cache(self.wt, age_hours=10)
        (cleared, _, _), _, asked = run(self.fx, finished=False)
        self.assertEqual(cleared, 0)
        self.assertTrue(os.path.exists(cache))
        self.assertEqual(asked, ["feature"])
        (cleared, _, _), lines, _ = run(self.fx, finished=True)
        self.assertEqual(cleared, 1)
        self.assertFalse(os.path.exists(cache))

    def test_unknown_pr_state_waits_for_the_long_threshold(self):
        cache = self.fx.cache(self.wt, age_hours=10)
        (cleared, _, _), lines, _ = run(self.fx, finished=None)
        self.assertEqual(cleared, 0)
        self.assertTrue(os.path.exists(cache))
        self.assertIn("state unknown", lines[0])

    def test_busy_worktree_is_kept_however_idle(self):
        cache = self.fx.cache(self.wt, age_hours=500)
        (cleared, _, _), lines, _ = run(
            self.fx, finished=True, busy=[os.path.realpath(self.wt) + "/src"])
        self.assertEqual(cleared, 0)
        self.assertTrue(os.path.exists(cache))
        self.assertIn("busy", lines[0])

    def test_a_held_cargo_lock_keeps_it(self):
        cache = self.fx.cache(self.wt, age_hours=500, locks=("debug", "release"))
        with open(os.path.join(cache, "release", ".cargo-lock"), "rb") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            (cleared, _, _), lines, _ = run(self.fx, finished=True)
        self.assertEqual(cleared, 0)
        self.assertTrue(os.path.exists(cache))
        self.assertIn("locked", lines[0])

    def test_directory_times_are_not_evidence(self):
        cache = self.fx.cache(self.wt, age_hours=100)
        os.utime(os.path.join(cache, "debug"), (NOW, NOW))  # a scan touched it
        (cleared, _, _), _, _ = run(self.fx, finished=False)
        self.assertEqual(cleared, 1)

    def test_reading_the_tag_is_not_use(self):
        # The tool reads CACHEDIR.TAG to recognise the cache, which sets
        # its atime: on the first host every cache then looked "used 0h ago".
        cache = self.fx.cache(self.wt, age_hours=100)
        os.utime(os.path.join(cache, "CACHEDIR.TAG"), (NOW, NOW))
        (cleared, _, _), _, _ = run(self.fx, finished=False)
        self.assertEqual(cleared, 1)

    def test_atime_counts_as_use(self):
        cache = self.fx.cache(self.wt, age_hours=100)
        f = os.path.join(cache, "debug", "deps", "libx.rlib")
        os.utime(f, (NOW - HOUR, NOW - 100 * HOUR))  # read an hour ago
        (cleared, _, _), _, _ = run(self.fx, finished=True)
        self.assertEqual(cleared, 0)


class SafetyLine(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)
        self.wt = self.fx.worktree("a")

    def test_untagged_target_is_never_touched(self):
        path = self.fx.cache(self.wt, age_hours=1000, tag=None)
        (cleared, kept, _), _, _ = run(self.fx, finished=True)
        self.assertEqual((cleared, kept), (0, 0))
        self.assertTrue(os.path.exists(os.path.join(path, "debug", "deps", "libx.rlib")))

    def test_wrong_signature_is_not_a_tag(self):
        path = self.fx.cache(self.wt, age_hours=1000, tag=b"Signature: nope\n")
        run(self.fx, finished=True)
        self.assertTrue(os.path.exists(path))

    def test_not_a_worktree_is_skipped(self):
        plain = os.path.join(self.fx.root, "notes")
        os.makedirs(plain)
        path = self.fx.cache(plain, age_hours=1000)
        run(self.fx, finished=True)
        self.assertTrue(os.path.exists(path))

    def test_nested_caches_found_to_depth_three_only(self):
        shallow = self.fx.cache(self.wt, rel="desktop/target", age_hours=100)
        deep = self.fx.cache(self.wt, rel="a/b/c/target", age_hours=100)
        run(self.fx, finished=True)
        self.assertFalse(os.path.exists(shallow))
        self.assertTrue(os.path.exists(deep))


class Mechanics(unittest.TestCase):
    def setUp(self):
        self.fx = Fixture(self)
        self.wt = self.fx.worktree("a")

    def test_dry_run_deletes_nothing(self):
        cache = self.fx.cache(self.wt, age_hours=100)
        (cleared, _, kib), lines, _ = run(self.fx, finished=True, dry_run=True)
        self.assertEqual(cleared, 1)
        self.assertGreater(kib, 0)
        self.assertTrue(os.path.exists(cache))
        self.assertTrue(lines[0].startswith("would clear"))

    def test_leftover_from_an_interrupted_run_is_deleted(self):
        left = os.path.join(self.wt, "target.forge-pruning")
        os.makedirs(os.path.join(left, "debug"))
        run(self.fx, finished=False)
        self.assertFalse(os.path.exists(left))
        self.assertTrue(os.path.exists(os.path.join(self.wt, "main.rs")))

    def test_main_rejects_inverted_thresholds(self):
        errs = []
        rc = prune.main(["--root", self.fx.root, "--merged-hours", "50"],
                        out=lambda m: None, err=errs.append,
                        finished_of=lambda *a: None, busy=set())
        self.assertEqual(rc, 2)

    def test_main_runs_end_to_end(self):
        cache = self.fx.cache(self.wt, age_hours=100)
        lines = []
        rc = prune.main(["--root", self.fx.root], out=lines.append,
                        err=lambda m: None, finished_of=lambda *a: False,
                        busy=set(), branch_of=lambda wt: "b", now=NOW)
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(cache))
        self.assertIn("cleared 1", lines[-1])


class PullRequestState(unittest.TestCase):
    def call(self, items, status=200):
        def fake(url, token, scheme="Bearer", method="GET", body=None):
            self.urls.append(url)
            return status, items
        self.urls = []
        return fake

    def test_closed_and_merged_count_as_finished(self):
        self.assertTrue(prune.pr_finished(
            "o/r", None, "b", call=self.call([{"state": "closed"}])))

    def test_an_open_one_is_not(self):
        self.assertFalse(prune.pr_finished(
            "o/r", None, "b", call=self.call([{"state": "closed"}, {"state": "open"}])))

    def test_no_pull_request_is_not_finished(self):
        self.assertFalse(prune.pr_finished("o/r", None, "b", call=self.call([])))

    def test_api_failure_and_detached_are_unknown(self):
        self.assertIsNone(prune.pr_finished("o/r", None, "b", call=self.call(None, 500)))
        self.assertIsNone(prune.pr_finished("o/r", None, None, call=self.call([])))

    def test_branch_is_quoted_with_owner(self):
        fake = self.call([])
        prune.pr_finished("own/rep", None, "x/y z", call=fake)
        self.assertIn("head=own%3Ax%2Fy%20z", self.urls[0])


if __name__ == "__main__":
    unittest.main()
