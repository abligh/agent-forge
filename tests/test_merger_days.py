"""Tests for tests/merger_days.py and tests/merger_compare.py: the
recorded-days library, and the comparison of two versions on it
(agent-forge #395)."""
import json
import os
import subprocess
import unittest

from tests import merger_compare as mc
from tests import merger_days as md
from tests import merger_replay as mr


class Library(unittest.TestCase):

    def test_the_files_are_the_generators(self):
        for kind in md.KINDS:
            with open(md.path(kind)) as handle:
                self.assertEqual(handle.read(), md.text(kind),
                                 "run python3 -m tests.merger_days --write")

    def test_the_days_are_what_they_say(self):
        ordinary, heavy, bad = (md.day(k) for k in ("ordinary", "heavy",
                                                     "bad"))
        self.assertEqual(len(heavy["prs"]), 2 * len(ordinary["prs"]))

        def count(t, key):
            # Conflicts come in pairs: count them, not who has one.
            return sum(len(p[key]) if isinstance(p[key], list) else 1
                       for p in t["prs"].values() if p[key])
        for key in ("red", "conflicts", "gaps"):
            self.assertGreater(count(bad, key), 2 * count(ordinary, key),
                               key)
        # The same pull requests: each red, push and conflict of the
        # ordinary day is the bad day's too.
        self.assertEqual([p["opened"] for p in bad["prs"].values()],
                         [p["opened"] for p in ordinary["prs"].values()])
        for key, pr in ordinary["prs"].items():
            worse = bad["prs"][key]
            self.assertTrue(pr["red"] is None or worse["red"] is not None)
            self.assertTrue(not pr["gaps"] or worse["gaps"])
            self.assertLessEqual(set(pr["conflicts"]),
                                 set(worse["conflicts"]))

    def test_no_project_is_in_them(self):
        for kind in md.KINDS:
            t = md.day(kind)
            self.assertEqual(sorted(map(int, t["prs"])),
                             list(range(1, len(t["prs"]) + 1)))
            self.assertEqual(t["real"]["merges"], [])
            self.assertTrue(all("files" not in p for p in t["prs"].values()))

    def test_the_smoke_day_replays_and_keeps_every_rule(self):
        got = mr.replay(md.day("smoke"), {"merge": {"test_ahead": {
            "batch": 3, "batches": 2, "trains": "live"}}}, idle=5)
        self.assertEqual(got["broken"], [])
        self.assertGreater(got["merged"], 0, got)
        self.assertIsNotNone(got["p90_approval_to_merge_minutes"])
        self.assertGreaterEqual(got["hand_backs"], 0)


class Compare(unittest.TestCase):

    def test_the_working_tree_against_itself(self):
        lines = []
        self.assertEqual(mc.main(["--day", "smoke", "--before", ".",
                                  "--after", "."], out=lines.append), 0)
        table = lines[0].split("\n")
        self.assertEqual(table[0], "| | smoke: . | smoke: . |")
        rows = {r.split(" | ")[0][2:]: r.split(" | ")[1:] for r in table[2:]}
        self.assertEqual(list(rows), [name for _k, name in mc.FIGURES])
        for name, (before, after) in rows.items():
            self.assertEqual(before, after.rstrip(" |"), name)

    def test_a_ref_is_checked_out_and_removed(self):
        calls = []

        def run(argv, **kw):
            calls.append(argv)
            if "merger_replay" in " ".join(argv):
                self.assertEqual(kw["env"]["PYTHONHASHSEED"], "0")
                self.assertEqual(kw["env"]["PYTHONPATH"], kw["cwd"])
                return subprocess.CompletedProcess(argv, 0, json.dumps(
                    {"replay": {"merged": 3, "hand_backs": None}}), "")
            return subprocess.CompletedProcess(argv, 0, "", "")
        got = mc.compare(["smoke"], "v1", ".", ["--set", "x=1"], run)
        self.assertEqual(got["smoke"]["before"]["merged"], 3)
        added = [c for c in calls if c[3:5] == ["worktree", "add"]]
        removed = [c for c in calls if c[3:5] == ["worktree", "remove"]]
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0][-1], "v1")
        self.assertEqual(removed[0][-1], added[0][-2])
        replays = [c for c in calls if "tests.merger_replay" in c]
        self.assertEqual(len(replays), 2)
        self.assertTrue(all(c[3] == md.path("smoke") and c[-2:] ==
                            ["--set", "x=1"] for c in replays))
        self.assertIn("| hand-backs | - | - |",
                      mc.markdown(got, "v1", "."))

    def test_a_day_is_a_kind_or_a_file(self):
        self.assertEqual(mc.trace_path("heavy"), md.path("heavy"))
        self.assertEqual(mc.trace_path("x.json"), os.path.abspath("x.json"))
