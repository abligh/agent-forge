"""Tests for tools/compare-check-states."""
import importlib.machinery
import importlib.util
import json
import os
import tempfile
import unittest
from pathlib import Path

_loader = importlib.machinery.SourceFileLoader(
    "compare_check_states",
    str(Path(__file__).resolve().parent.parent / "tools"
        / "compare-check-states"))
_spec = importlib.util.spec_from_loader(_loader.name, _loader)
tool = importlib.util.module_from_spec(_spec)
_loader.exec_module(tool)

OLD = {"results": {
    "a" * 40: {"ok": True, "failed": [], "pr": 1, "finished": "2026-10-12T01"},
    "b" * 40: {"ok": False, "failed": [["test", "out"]], "pr": 2,
               "finished": "2026-10-12T02"},
    "d" * 40: {"ok": True, "pr": 4, "finished": "2026-10-12T04"}}}
NEW = {"results": {
    "a" * 40: {"ok": False, "failed": ["test-rust"], "pr": 1,
               "finished": "2026-10-12T01"},
    "b" * 40: {"ok": False, "failed": ["test-rust"], "pr": 2,
               "finished": "2026-10-12T02"},
    "e" * 40: {"ok": True, "pr": 5, "finished": "2026-10-12T05"}}}


class Compare(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.paths = []
        for name, data in (("old", OLD), ("new", NEW)):
            path = os.path.join(self.dir.name, name + ".json")
            with open(path, "w") as handle:
                json.dump(data, handle)
            self.paths.append(path)
        self.out = []

    def test_a_green_red_split_is_a_disagreement(self):
        self.assertEqual(tool.main(self.paths, self.out.append), 1)
        self.assertTrue(self.out[0].startswith("DISAGREE #1 aaaaaaaaaa"))
        self.assertTrue(self.out[1].startswith("STEPS #2"))
        self.assertEqual(self.out[2], "both checked 2: 1 disagree, 1 red "
                         "with different steps; only old 1, only new 1")

    def test_since_leaves_earlier_heads_out(self):
        self.assertEqual(tool.main(self.paths + ["--since", "2026-10-12T02"],
                                   self.out.append), 0)

    def test_by_tree_pairs_two_commits_of_one_tree(self):
        # Each check makes its own commit for a prediction: one tree,
        # two SHAs.
        for path, sha, ok in ((self.paths[0], "1" * 40, True),
                              (self.paths[1], "2" * 40, False)):
            with open(path, "w") as handle:
                json.dump({"results": {
                    "9" * 40: {"ok": True, "tree": "t" * 40,
                               "finished": "2026-10-12T00"},
                    sha: {"ok": ok, "tree": "t" * 40, "pr": 7,
                          "failed": [] if ok else ["test"],
                          "finished": "2026-10-12T01"}}}, handle)
        self.assertEqual(tool.main(self.paths + ["--by-tree"],
                                   self.out.append), 1)
        self.assertEqual(self.out[0], "DISAGREE #7 tree tttttttttt (old "
                         "1111111111, new 2222222222): old green , new red "
                         "test")
        self.assertTrue(self.out[1].startswith("both checked 1 trees"))

    def test_since_limits_the_new_side_only(self):
        # The old check never checks a head again: its verdict on one
        # the trial reaches later can predate the trial.
        tool.main(self.paths + ["--since", "2026-10-12T02"], self.out.append)
        self.assertEqual(self.out[-1], "both checked 1: 0 disagree, 1 red "
                         "with different steps; only old 2, only new 1")
        self.out.clear()
        tool.main(self.paths + ["--since", "2026-10-12T02", "--old-since",
                                "2026-10-12T03"], self.out.append)
        self.assertEqual(self.out[-1], "both checked 0: 0 disagree, 0 red "
                         "with different steps; only old 1, only new 2")

    def test_an_unreadable_file_is_2(self):
        self.assertEqual(tool.main([self.paths[0], "/nonexistent"],
                                   self.out.append), 2)


if __name__ == "__main__":
    unittest.main()
