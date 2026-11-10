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
        # No tag and no Cargo profile (no .cargo-lock, no .fingerprint).
        path = self.fx.cache(self.wt, age_hours=1000, tag=None, locks=())
        (cleared, kept, _), _, _ = run(self.fx, finished=True)
        self.assertEqual((cleared, kept), (0, 0))
        self.assertTrue(os.path.exists(os.path.join(path, "debug", "deps", "libx.rlib")))

    def test_a_profile_under_an_untagged_target_is_a_cache(self):
        # designs/0041, second amendment: Cargo didn't create target/
        # (a nested build did), so only target/vet-wasm is tagged, and
        # target/debug is judged as a cache of its own.
        path = self.fx.cache(self.wt, age_hours=1000, tag=None)
        nested = self.fx.cache(self.wt, rel="target/vet-wasm", age_hours=1)
        os.makedirs(os.path.join(path, "release", ".fingerprint"))
        with open(os.path.join(path, "keep.json"), "w") as f:
            f.write("{}")
        (cleared, kept, _), lines, _ = run(self.fx, finished=True)
        self.assertEqual((cleared, kept), (2, 1))
        self.assertFalse(os.path.exists(os.path.join(path, "debug")))
        self.assertFalse(os.path.exists(os.path.join(path, "release")))
        self.assertTrue(os.path.exists(os.path.join(path, "keep.json")))
        self.assertTrue(os.path.exists(nested))

    def test_a_debug_directory_that_isnt_cargos_is_never_touched(self):
        path = os.path.join(self.wt, "docs", "debug")
        os.makedirs(path)
        with open(os.path.join(path, "notes.txt"), "w") as f:
            f.write("mine")
        os.utime(os.path.join(path, "notes.txt"), (0, 0))
        run(self.fx, finished=True)
        self.assertTrue(os.path.exists(os.path.join(path, "notes.txt")))

    def test_a_profile_one_below_the_deepest_level_is_found(self):
        path = self.fx.cache(self.wt, rel="a/b/target", age_hours=1000,
                             tag=None)
        run(self.fx, finished=True)
        self.assertFalse(os.path.exists(os.path.join(path, "debug")))

    def test_a_held_lock_keeps_an_untagged_profile(self):
        path = self.fx.cache(self.wt, age_hours=1000, tag=None)
        lock = os.path.join(path, "debug", ".cargo-lock")
        with open(lock, "rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            run(self.fx, finished=True)
        self.assertTrue(os.path.exists(os.path.join(path, "debug")))

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


def run_pressure(fx, *, usage, high=85, low=70, floor=1.0, busy=(),
                 finished=False, shared=(), targets=(), dry_run=False):
    lines = []
    result = prune.prune(
        [fx.root], dry_run=dry_run, merged_hours=6, idle_hours=48,
        repo="o/r", token=None, now=NOW, busy=set(busy),
        branch_of=lambda wt: "b", finished_of=lambda *a: finished,
        out=lines.append, shared=list(shared), high_water=high,
        low_water=low, floor_hours=floor, targets=set(targets), usage=usage)
    return result, lines


class Pressure(unittest.TestCase):
    """designs/0041: over the high-water mark, the least recently used
    spare caches go, whatever their age, until under the low-water mark."""

    def setUp(self):
        self.fx = Fixture(self)
        self.caches = {}
        for name, age in (("a", 20), ("b", 5), ("c", 10), ("d", 0.5)):
            wt = self.fx.worktree(name)
            self.caches[name] = self.fx.cache(wt, age_hours=age)

    def left(self):
        return sorted(n for n, c in self.caches.items() if os.path.exists(c))

    def usage(self, per_cache=10, base=50):
        # Each cache still there is 10 points of the disk.
        return lambda root: base + per_cache * len(self.left())

    def test_under_high_water_nothing_more_goes(self):
        (cleared, kept, _), _ = run_pressure(self.fx, usage=lambda r: 84)
        self.assertEqual((cleared, kept), (0, 4))
        self.assertEqual(self.left(), ["a", "b", "c", "d"])

    def test_oldest_first_until_under_low_water(self):
        # 90% used; under 71 means two must go, the two oldest.
        (cleared, _, _), lines = run_pressure(self.fx, usage=self.usage(), low=71)
        self.assertEqual(cleared, 2)
        self.assertEqual(self.left(), ["b", "d"])
        self.assertTrue(any(l.startswith("cleared") and "pressure: used 20h ago" in l
                            for l in lines))
        self.assertIn("now 70% used", lines[-1])

    def test_the_floor_keeps_what_was_just_used(self):
        # Needs all four gone to reach 50; d (used 0.5h ago) stays.
        run_pressure(self.fx, usage=self.usage(), low=45)
        self.assertEqual(self.left(), ["d"])

    def test_each_cache_the_floor_keeps_is_logged(self):
        _, lines = run_pressure(self.fx, usage=self.usage(), low=45)
        self.assertIn("pressure: kept %s: used 0.5h ago, inside the floor "
                      "(1h)" % self.caches["d"], lines)

    def test_still_over_high_water_it_clears_inside_the_floor(self):
        # Each cache is 30 points over 60: after a, b and c go, d alone
        # keeps the disk at 90%, over high-water, so it goes too.
        (cleared, _, _), lines = run_pressure(
            self.fx, usage=self.usage(per_cache=30, base=60), low=45)
        self.assertEqual(self.left(), [])
        self.assertEqual(cleared, 4)
        self.assertTrue(any("clearing caches inside the floor" in l
                            for l in lines))

    def test_inside_the_floor_a_held_lock_still_keeps_it(self):
        lock = os.path.join(self.caches["d"], "debug", ".cargo-lock")
        with open(lock, "rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            run_pressure(self.fx, usage=self.usage(per_cache=30, base=60),
                         low=45)
        self.assertEqual(self.left(), ["d"])

    def test_inside_the_floor_a_busy_worktree_still_keeps_it(self):
        wt = os.path.dirname(self.caches["d"])
        run_pressure(self.fx, usage=self.usage(per_cache=30, base=60),
                     low=45, busy=[os.path.realpath(wt)])
        self.assertEqual(self.left(), ["d"])

    def test_a_busy_worktree_is_kept_under_pressure(self):
        wt = os.path.dirname(self.caches["a"])
        run_pressure(self.fx, usage=self.usage(), busy=[os.path.realpath(wt)],
                     low=71)
        self.assertEqual(self.left(), ["a", "d"])

    def test_a_held_lock_keeps_it_under_pressure(self):
        lock = os.path.join(self.caches["a"], "debug", ".cargo-lock")
        with open(lock, "rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            run_pressure(self.fx, usage=self.usage(), low=71)
        self.assertEqual(self.left(), ["a", "d"])

    def test_dry_run_estimates_and_deletes_nothing(self):
        (cleared, _, kib), lines = run_pressure(
            self.fx, usage=lambda r: 99.9, dry_run=True)
        self.assertEqual(self.left(), ["a", "b", "c", "d"])
        # Still 99.9% after the three, so d, inside the floor, too.
        self.assertEqual(cleared, 4)
        self.assertTrue(lines[-1].startswith("pressure: %s would be" % self.fx.root))


class LiveTrim(unittest.TestCase):
    """--live-trim-hours: under pressure a live session's cache gives up
    only what a build no longer needs."""

    def setUp(self):
        self.fx = Fixture(self)
        self.wt = self.fx.worktree("live")
        self.cache = self.fx.cache(self.wt, age_hours=100)
        self.old = os.path.join(self.cache, "debug", "deps", "libx.rlib")
        self.fresh = self.file("debug/deps/liby.rlib", age_hours=1)
        self.session = os.path.join(self.cache, "debug", "incremental",
                                    "x-1", "s-old")
        self.file("debug/incremental/x-1/s-old/a.bin", age_hours=100)
        self.mixed = os.path.join(self.cache, "debug", "incremental",
                                  "x-1", "s-new")
        self.file("debug/incremental/x-1/s-new/a.bin", age_hours=100)
        self.file("debug/incremental/x-1/s-new/b.bin", age_hours=1)
        self.other = self.file("debug/build/x/out", age_hours=100)

    def file(self, rel, age_hours):
        path = os.path.join(self.cache, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(b"y" * 4096)
        t = NOW - age_hours * HOUR
        os.utime(path, (t, t))
        return path

    def run_it(self, *, usage=lambda r: 90, busy=None, dry_run=False,
               hours=6, idle=False):
        lines = []
        result = prune.prune(
            [self.fx.root], dry_run=dry_run, merged_hours=6, idle_hours=48,
            repo="o/r", token=None, now=NOW,
            busy=(set() if idle else {os.path.realpath(self.wt)})
            | set(busy or ()),
            branch_of=lambda wt: "b", finished_of=lambda *a: False,
            out=lines.append, high_water=85, low_water=70,
            floor_hours=1.0, targets=set(), usage=usage,
            live_trim_hours=hours)
        return result, lines

    def test_it_takes_stale_outputs_and_nothing_else(self):
        self.run_it()
        self.assertFalse(os.path.exists(self.old))
        self.assertFalse(os.path.exists(self.session))
        self.assertTrue(os.path.exists(self.fresh))
        self.assertTrue(os.path.exists(self.mixed + "/a.bin"))
        self.assertTrue(os.path.exists(self.other))
        self.assertTrue(os.path.exists(
            os.path.join(self.cache, "debug", ".cargo-lock")))

    def test_off_unless_given(self):
        self.run_it(hours=None)
        self.assertTrue(os.path.exists(self.old))

    def test_under_high_water_it_does_nothing(self):
        self.run_it(usage=lambda r: 50)
        self.assertTrue(os.path.exists(self.old))

    def test_a_held_lock_keeps_everything(self):
        lock = os.path.join(self.cache, "debug", ".cargo-lock")
        with open(lock, "rb") as handle:
            fcntl.flock(handle, fcntl.LOCK_EX)
            _, lines = self.run_it()
        self.assertTrue(os.path.exists(self.old))
        self.assertIn("kept %s: a build holds its lock" % self.cache, lines)

    def test_an_open_file_stays(self):
        self.run_it(busy=[os.path.realpath(self.old)])
        self.assertTrue(os.path.exists(self.old))

    def test_a_link_is_not_followed_out_of_the_cache(self):
        keep = os.path.join(self.fx.root, "precious")
        with open(keep, "w") as f:
            f.write("mine")
        os.utime(keep, (NOW - 900 * HOUR, NOW - 900 * HOUR))
        link = os.path.join(self.cache, "debug", "deps", "elsewhere")
        os.symlink(keep, link)
        self.run_it()
        self.assertTrue(os.path.exists(keep))

    def test_it_stops_at_low_water(self):
        second = self.fx.worktree("second")
        other = self.fx.cache(second, age_hours=100)
        lines = []
        prune.prune(
            [self.fx.root], dry_run=False, merged_hours=6, idle_hours=48,
            repo="o/r", token=None, now=NOW,
            busy={os.path.realpath(self.wt), os.path.realpath(second)},
            branch_of=lambda wt: "b", finished_of=lambda *a: False,
            out=lines.append, high_water=85, low_water=70, floor_hours=1.0,
            targets=set(),
            usage=self.falling(),
            live_trim_hours=6)
        # The first cache was enough: the other is untouched.
        self.assertTrue(os.path.exists(
            os.path.join(other, "debug", "deps", "libx.rlib")))

    def falling(self):
        readings = iter([90, 90, 60])
        return lambda root: next(readings, 60)

    def test_a_spare_cache_the_floor_keeps_is_trimmed_too(self):
        # Nothing is using it, but it was used within the hour: the
        # floor keeps it whole, and the live trim takes its stale part.
        self.file("debug/deps/recent.rlib", age_hours=0.2)
        _, lines = self.run_it(usage=self.falling(), idle=True)
        self.assertFalse(os.path.exists(self.old))
        self.assertTrue(os.path.exists(self.fresh))
        self.assertTrue(any(l.startswith("trimmed") for l in lines))

    def test_dry_run_deletes_nothing(self):
        (_, _, kib), lines = self.run_it(dry_run=True)
        self.assertTrue(os.path.exists(self.old))
        self.assertTrue(os.path.exists(self.session))
        self.assertGreater(kib, 0)
        self.assertTrue(any(l.startswith("would trim") for l in lines))

    def test_the_flag_needs_a_high_water_mark(self):
        errors = []
        code = prune.main(["--root", self.fx.root, "--live-trim-hours", "6"],
                          out=lambda m: None, err=errors.append, environ={},
                          finished_of=lambda *a: None)
        self.assertEqual(code, 2)
        self.assertIn("needs --high-water", errors[0])


class Shared(unittest.TestCase):
    """designs/0041: tagged directories under --shared, outside worktrees,
    are caches shared between worktrees."""

    def setUp(self):
        self.fx = Fixture(self)
        self.shared = os.path.join(self.fx.root, "shared")
        os.makedirs(self.shared)

    def tgt(self, name, age, tag=TAG, locks=("debug",)):
        return self.fx.cache(self.shared, rel=name, age_hours=age, tag=tag,
                             locks=locks)

    def go(self, **kw):
        lines = []
        result = prune.prune(
            [], dry_run=False, merged_hours=6, idle_hours=48, repo="o/r",
            token=None, now=NOW, busy=set(kw.get("busy", ())),
            out=lines.append, shared=[self.shared],
            targets=set(kw.get("targets", ())), high_water=kw.get("high"),
            low_water=kw.get("low"), usage=kw.get("usage", lambda r: 0))
        return result, lines

    def test_idle_shared_cache_is_cleared(self):
        old = self.tgt(".tgt-old", 100)
        new = self.tgt(".tgt-new", 10)
        (cleared, kept, _), lines = self.go()
        self.assertEqual((cleared, kept), (1, 1))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))

    def test_untagged_is_never_touched(self):
        plain = self.tgt(".tgt-plain", 100, tag=None, locks=())
        self.go()
        self.assertTrue(os.path.exists(plain))

    def test_an_untagged_targets_profiles_are_shared_caches(self):
        plain = self.tgt(".tgt-plain", 100, tag=None)
        (cleared, _, _), _ = self.go()
        self.assertEqual(cleared, 1)
        self.assertFalse(os.path.exists(os.path.join(plain, "debug")))
        self.assertTrue(os.path.exists(plain))

    def test_a_worktree_is_not_a_shared_cache(self):
        wt = os.path.join(self.shared, "wt")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: /nowhere\n")
        with open(os.path.join(wt, "CACHEDIR.TAG"), "wb") as f:
            f.write(TAG)
        self.go()
        self.assertTrue(os.path.exists(os.path.join(wt, ".git")))

    def test_named_as_cargo_target_dir_is_busy(self):
        old = self.tgt(".tgt-old", 100)
        (_, _, _), lines = self.go(targets=[os.path.realpath(old)])
        self.assertTrue(os.path.exists(old))
        self.assertIn("busy", lines[0])

    def test_open_file_inside_is_busy(self):
        old = self.tgt(".tgt-old", 100)
        self.go(busy=[os.path.realpath(os.path.join(old, "debug", "x"))])
        self.assertTrue(os.path.exists(old))

    def test_a_cache_one_level_down_is_found(self):
        # A directory of per-kind targets carries its tags one level
        # down: .tgt-x/wasm32-wasip1, .tgt-x/vet-windows.
        old = self.tgt(".tgt-kinds/wasm32-wasip1", 100)
        new = self.tgt(".tgt-kinds/vet-windows", 10)
        (cleared, kept, _), lines = self.go()
        self.assertEqual((cleared, kept), (1, 1))
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.isdir(os.path.join(self.shared, ".tgt-kinds")))

    def test_nested_shared_dirs_judge_each_cache_once(self):
        old = self.tgt("agents/.tgt-kinds/wasm32-wasip1", 100)
        lines = []
        cleared, kept, _ = prune.prune(
            [], dry_run=False, merged_hours=6, idle_hours=48, repo="o/r",
            token=None, now=NOW, busy=set(), out=lines.append,
            shared=[self.shared, os.path.join(self.shared, "agents")],
            targets=set(), high_water=None, low_water=None,
            usage=lambda r: 0)
        self.assertEqual((cleared, kept), (1, 0))
        self.assertFalse(os.path.exists(old))

    def test_a_tagged_cache_is_not_searched_inside(self):
        outer = self.tgt(".tgt-outer", 10)
        inner = self.fx.cache(outer, rel="nested", age_hours=100)
        (cleared, kept, _), _ = self.go()
        self.assertEqual((cleared, kept), (0, 1))
        self.assertTrue(os.path.exists(inner))

    def test_nothing_inside_a_worktree_is_a_shared_cache(self):
        wt = os.path.join(self.shared, "wt")
        os.makedirs(wt)
        with open(os.path.join(wt, ".git"), "w") as f:
            f.write("gitdir: /nowhere\n")
        inner = self.fx.cache(wt, rel="sub/target", age_hours=100)
        self.go()
        self.assertTrue(os.path.exists(inner))

    def test_a_target_dir_above_the_cache_makes_it_busy(self):
        old = self.tgt(".tgt-kinds/wasm32-wasip1", 100)
        _, lines = self.go(targets=[os.path.realpath(
            os.path.join(self.shared, ".tgt-kinds"))])
        self.assertTrue(os.path.exists(old))
        self.assertIn("busy", lines[0])

    def test_a_busy_shared_cache_is_trimmed_live(self):
        cache = self.tgt(".tgt-live", 1)
        stale = os.path.join(cache, "debug", "deps", "libstale.rlib")
        with open(stale, "wb") as f:
            f.write(b"z" * 4096)
        os.utime(stale, (NOW - 100 * HOUR, NOW - 100 * HOUR))
        lines = []
        prune.prune(
            [], dry_run=False, merged_hours=6, idle_hours=48, repo="o/r",
            token=None, now=NOW, busy=set(), out=lines.append,
            shared=[self.shared], targets={os.path.realpath(cache)},
            high_water=85, low_water=70, floor_hours=1.0,
            usage=lambda r: 90, live_trim_hours=6)
        self.assertFalse(os.path.exists(stale))
        self.assertTrue(os.path.exists(
            os.path.join(cache, "debug", "deps", "libx.rlib")))

    def test_shared_caches_go_under_pressure_too(self):
        old = self.tgt(".tgt-a", 30)
        new = self.tgt(".tgt-b", 20)
        left = lambda: [p for p in (old, new) if os.path.exists(p)]
        self.go(high=85, low=71, usage=lambda r: 55 + 15 * len(left()))
        self.assertEqual(left(), [new])

    def test_target_dirs_reads_environments(self):
        proc = os.path.join(self.fx.root, "proc")
        for pid, env in (("1", b"A=1\0CARGO_TARGET_DIR=/x/t\0"), ("2", b"B=2\0")):
            os.makedirs(os.path.join(proc, pid))
            with open(os.path.join(proc, pid, "environ"), "wb") as f:
                f.write(env)
        self.assertEqual(prune.target_dirs(proc), {os.path.realpath("/x/t")})


class Alert(unittest.TestCase):
    """designs/0041, second amendment: a filesystem still nearly full
    after pruning is reported, and told through the relay."""

    def setUp(self):
        self.fx = Fixture(self)
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.env = {"FORGE_STATE_DIR": self.state.name,
                    "HOME": self.state.name}
        self.sent, self.refuse = [], False

    def send(self, name, text):
        self.sent.append((name, text))
        return "HTTP 503 queue-full" if self.refuse else None

    def main(self, *extra, now=NOW, usage=lambda r: 98):
        errors = []
        rc = prune.main(["--root", self.fx.root, "--high-water", "85",
                         "--low-water", "70", *extra],
                        out=lambda m: None, err=errors.append,
                        environ=self.env, finished_of=lambda *a: None,
                        busy=set(), branch_of=lambda wt: "b", now=now,
                        usage=usage, send=self.send)
        return rc, errors

    def test_still_full_is_reported_and_told_once_an_hour(self):
        rc, errors = self.main("--alert-to", "LeadDev",
                               "--alert-to", "ops@example.org")
        self.assertEqual(rc, 0)
        self.assertIn("forge-prune-builds: ALERT: %s is 98%% used after "
                      "pruning (alert at 97%%)" % self.fx.root, errors)
        self.assertEqual([n for n, _ in self.sent],
                         ["LeadDev", "ops@example.org"])
        self.assertIn("%s 98%% used" % self.fx.root, self.sent[0][1])
        self.main("--alert-to", "LeadDev", now=NOW + 600)
        self.assertEqual(len(self.sent), 2)
        self.main("--alert-to", "LeadDev", now=NOW + 3700)
        self.assertEqual(len(self.sent), 3)

    def test_not_taken_is_tried_again_next_run(self):
        self.refuse = True
        _, errors = self.main("--alert-to", "LeadDev")
        self.assertTrue(any("not taken: HTTP 503" in e for e in errors))
        self.refuse = False
        self.main("--alert-to", "LeadDev", now=NOW + 300)
        self.assertEqual(len(self.sent), 2)

    def test_under_the_mark_says_nothing(self):
        _, errors = self.main("--alert-to", "LeadDev", usage=lambda r: 96)
        self.assertEqual((errors, self.sent), ([], []))

    def test_alert_percent_sets_the_mark(self):
        _, errors = self.main("--alert-percent", "90", usage=lambda r: 92)
        self.assertTrue(any("ALERT" in e for e in errors))
        self.assertEqual(self.sent, [])

    def test_a_dry_run_reports_but_tells_nobody(self):
        _, errors = self.main("--alert-to", "LeadDev", "--dry-run")
        self.assertTrue(any("ALERT" in e for e in errors))
        self.assertEqual(self.sent, [])

    def test_the_real_sender_is_forge_mergers(self):
        import json
        from tests.helpers import example
        path = os.path.join(self.state.name, "config.json")
        with open(path, "w") as handle:
            json.dump(example(), handle)
        send = prune.relay_sender({"FORGE_CONFIG": path,
                                   "HOME": self.state.name})
        self.assertTrue(callable(send))

    def test_flags_are_checked(self):
        for extra in (["--alert-percent", "0"],
                      ["--alert-percent", "101"]):
            rc, errors = self.main(*extra)
            self.assertEqual(rc, 2, extra)
        errors = []
        rc = prune.main(["--root", self.fx.root, "--alert-to", "LeadDev"],
                        out=lambda m: None, err=errors.append, environ={},
                        finished_of=lambda *a: None)
        self.assertEqual(rc, 2)
        self.assertIn("--alert-to needs --high-water", errors[0])


class Often(unittest.TestCase):
    """designs/0041: --every-minutes skips a scan while all is calm."""

    def setUp(self):
        self.fx = Fixture(self)
        self.state = tempfile.TemporaryDirectory()
        self.addCleanup(self.state.cleanup)
        self.env = {"FORGE_STATE_DIR": self.state.name, "HOME": self.state.name}

    def main(self, *extra, now=NOW, usage=lambda r: 50):
        lines = []
        rc = prune.main(["--root", self.fx.root, "--every-minutes", "60",
                         "--high-water", "85", "--low-water", "70", *extra],
                        out=lines.append, err=lines.append, environ=self.env,
                        finished_of=lambda *a: None, busy=set(),
                        branch_of=lambda wt: "b", now=now, usage=usage)
        return rc, lines

    def test_first_run_scans_then_a_recent_one_skips(self):
        _, first = self.main()
        self.assertIn("cleared 0", first[-1])
        _, second = self.main(now=NOW + 10 * 60)
        self.assertIn("skipped", second[-1])

    def test_after_the_interval_it_scans_again(self):
        self.main()
        _, later = self.main(now=NOW + 61 * 60)
        self.assertIn("cleared 0", later[-1])

    def test_over_high_water_it_never_skips(self):
        self.main()
        _, hot = self.main(now=NOW + 60, usage=lambda r: 90)
        self.assertNotIn("skipped", hot[-1])

    def test_flags_are_checked(self):
        for bad in (["--high-water", "85"], ["--low-water", "90", "--high-water", "80"],
                    ["--every-minutes", "0"], ["--pressure-floor-hours", "-1"]):
            rc = prune.main(["--root", self.fx.root, *bad], out=lambda m: None,
                            err=lambda m: None, environ=self.env,
                            finished_of=lambda *a: None, busy=set())
            self.assertEqual(rc, 2, bad)


class AgedCache(unittest.TestCase):
    """--aged-cache: an untagged cache trimmed file by file by age."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.top = tmp.name
        self.cache = os.path.join(self.top, "gocache")
        self.outside = os.path.join(self.top, "outside")
        os.makedirs(os.path.join(self.cache, "0a"))
        os.makedirs(self.outside)

    def file(self, path, age_days):
        with open(path, "wb") as f:
            f.write(b"x" * 4096)
        t = NOW - age_days * 24 * HOUR
        os.utime(path, (t, t))
        return path

    def main(self, *extra):
        lines, errs = [], []
        rc = prune.main(["--root", self.top, "--aged-cache", self.cache]
                        + list(extra), out=lines.append, err=errs.append,
                        finished_of=lambda *a: None, busy=set(), now=NOW)
        return rc, lines, errs

    def test_old_files_go_recent_files_and_directories_stay(self):
        old = self.file(os.path.join(self.cache, "0a", "old-d"), 9)
        new = self.file(os.path.join(self.cache, "0a", "new-d"), 1)
        rc, lines, _ = self.main()
        self.assertEqual(rc, 0)
        self.assertFalse(os.path.exists(old))
        self.assertTrue(os.path.exists(new))
        self.assertTrue(os.path.isdir(os.path.join(self.cache, "0a")))
        self.assertTrue(any("trimmed 1 file" in l for l in lines), lines)

    def test_a_recent_atime_keeps_an_old_mtime(self):
        path = self.file(os.path.join(self.cache, "0a", "read"), 9)
        os.utime(path, (NOW - HOUR, NOW - 9 * 24 * HOUR))
        self.main()
        self.assertTrue(os.path.exists(path))

    def test_aged_days_sets_the_age(self):
        path = self.file(os.path.join(self.cache, "0a", "x"), 3)
        self.main()
        self.assertTrue(os.path.exists(path))
        self.main("--aged-days", "2")
        self.assertFalse(os.path.exists(path))

    def test_a_dry_run_deletes_nothing(self):
        path = self.file(os.path.join(self.cache, "0a", "x"), 9)
        rc, lines, _ = self.main("--dry-run")
        self.assertTrue(os.path.exists(path))
        self.assertTrue(any("would trim 1 file" in l for l in lines), lines)

    def test_a_link_is_never_followed_out_of_the_cache(self):
        precious = self.file(os.path.join(self.outside, "precious"), 30)
        os.symlink(self.outside, os.path.join(self.cache, "0a", "dirlink"))
        os.symlink(precious, os.path.join(self.cache, "0a", "filelink"))
        self.main("--aged-days", "0.0001")
        self.assertTrue(os.path.exists(precious))

    def test_a_link_as_the_cache_or_home_is_refused(self):
        link = os.path.join(self.top, "link")
        os.symlink(self.outside, link)
        errs = []
        rc = prune.main(["--root", self.top, "--aged-cache", link],
                        out=lambda m: None, err=errs.append,
                        finished_of=lambda *a: None, busy=set(), now=NOW)
        self.assertEqual(rc, 2)
        rc = prune.main(["--root", self.top, "--aged-cache", "/"],
                        out=lambda m: None, err=errs.append,
                        finished_of=lambda *a: None, busy=set(), now=NOW)
        self.assertEqual(rc, 2)
        rc = prune.main(["--root", self.top, "--aged-cache", self.cache,
                         "--aged-days", "0"], out=lambda m: None,
                        err=errs.append, finished_of=lambda *a: None,
                        busy=set(), now=NOW)
        self.assertEqual(rc, 2)
