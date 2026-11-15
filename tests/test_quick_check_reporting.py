"""Tests for designs/0055 step 3, reporting on the two tiers: forge-stats'
`tiers` and `red_train_tops_per_merge`, forge-stats-watch's figure of it,
forge-flow's two contexts for each pull request, and the fast lane
costing a head by its quick tier's steps."""

import os
import tempfile
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import load_tool
from tests.test_forge_pr_check import pc
from tests.test_forge_pr_check_lane import waits
from tests import test_forge_flow as flow_tests

stats = load_tool("forge-stats")
watch = load_tool("forge-stats-watch")
flow = load_tool("forge-flow")
T = stats.parse_time
SINCE, UNTIL = T("2026-10-12T00:00Z"), T("2026-10-12T02:00Z")

TIERED = [
    "2026-10-12T00:10:00Z VERDICT #2 bbbbbbbbbb: green waited=60s "
    "ran=120s tier=quick",
    "2026-10-12T00:15:00Z VERDICT #4 4444444444: red(vet) waited=30s "
    "ran=60s tier=quick",
    "2026-10-12T00:20:00Z VERDICT AHEAD #3 cccccccccc: red(test) "
    "waited=0s ran=600s tier=full",
    "2026-10-12T00:30:00Z MAIN-GREEN main dddddddddd: green waited=600s "
    "ran=900s tier=full",
    "2026-10-12T00:40:00Z VERDICT AHEAD #5 5555555555: green waited=10s "
    "ran=700s merger_weight tier=full",
]


class Stats(unittest.TestCase):

    def test_the_tiers_apart(self):
        got = stats.check_stats(TIERED, SINCE, UNTIL, 2)["tiers"]
        self.assertEqual(sorted(got), ["full", "quick"])
        self.assertEqual((got["quick"]["runs"], got["quick"]["red"],
                          got["quick"]["seconds"]), (2, 1, 180))
        self.assertEqual((got["full"]["runs"], got["full"]["red"],
                          got["full"]["seconds"]), (3, 1, 2200))
        self.assertEqual(got["quick"]["waited"]["n"], 2)
        self.assertEqual(got["full"]["waited"]["max"], 600)

    def test_no_tiers_without_checks_quick(self):
        lines = [line.rsplit(" tier=", 1)[0] for line in TIERED]
        self.assertIsNone(stats.check_stats(lines, SINCE, UNTIL, 2)["tiers"])
        self.assertEqual(stats.tiers_text(None), [])

    def test_red_train_tops_per_merge(self):
        check = stats.check_stats(TIERED, SINCE, UNTIL, 2)
        self.assertEqual(check["red"]["prediction"], 1)
        self.assertEqual(stats.red_train_tops_per_merge(check,
                                                        {"merges": 4}), 0.25)
        self.assertIsNone(stats.red_train_tops_per_merge(check,
                                                         {"merges": 0}))

    def test_the_report_says_both(self):
        check = stats.check_stats(TIERED, SINCE, UNTIL, 2)
        check["red_train_tops_per_merge"] = 0.25
        queue = {"merges": 4, "per_hour": {}, "held_seconds": 0,
                 "queue_to_merge": stats.summary([]), "hand_backs": {}}
        text = stats.report("widget", SINCE, UNTIL, check, queue, None, {})
        self.assertIn("  quick runs: 2, 1 red; 0.1 slot-hours; waited for "
                      "a slot:", text)
        self.assertIn("  full runs: 3, 1 red; 0.6 slot-hours;", text)
        self.assertIn("  red train tops per merge: 0.25 (1 red)", text)


class Watch(unittest.TestCase):

    def stats(self, merges, red):
        return {"check": {"red": {"prediction": red}},
                "queue": {"merges": merges}}

    def test_the_figure_and_its_rules(self):
        self.assertEqual(config.STATS_WATCH_FIGURES[
            "red_train_tops_per_merge"], ("limit", "ratio", "short_hours"))
        self.assertNotIn("red_train_tops_per_merge", watch.FLOORS)
        self.assertEqual(watch.figures(self.stats(8, 2), 6)[
            "red_train_tops_per_merge"], (0.25, 8))
        self.assertEqual(watch.figures(self.stats(0, 2), 6)[
            "red_train_tops_per_merge"], (None, 0))

    def test_judged_as_a_ceiling(self):
        self.assertEqual(
            watch.judge("red_train_tops_per_merge", {"limit": 0.2},
                        (0.25, 8), (None, 0), 5),
            (watch.OUT, "0.25 per merge, over the limit of 0.20 per merge"))
        self.assertEqual(
            watch.judge("red_train_tops_per_merge", {"limit": 0.3},
                        (0.25, 8), (None, 0), 5)[0], watch.IN)


FLOW_VERDICTS = flow_tests.VERDICTS + [
    "2026-11-04T10:45:00Z VERDICT #20 aaaaaaaaaa: green waited=5s "
    "ran=60s tier=quick",
    "2026-11-04T10:46:00Z VERDICT #21 bbbbbbbbbb: red(vet) waited=5s "
    "ran=30s tier=quick",
    "2026-11-04T10:47:00Z VERDICT #22 cccccccccc: green waited=5s "
    "ran=500s tier=full",
]
AHEAD = {"entries": [
    {"pr": 20, "train": 0, "sha": "e" * 40, "head": "a" * 40},
    {"pr": 21, "train": 0, "sha": "f" * 40, "head": "b" * 40}]}


class Flow(flow_tests.Base):

    def test_off_without_tiers(self):
        got = self.flow(ahead=AHEAD)
        self.assertFalse(got.tiered)
        self.assertIsNone(got.trains()[0]["quick"])
        queue = {"queue": [{"pr": 20, "sha": "aaaaaaaaaa"}]}
        self.assertNotIn("quick", flow.with_contexts(
            queue, got, got.trains())["queue"][0])

    def test_each_head_has_both_contexts(self):
        got = self.flow(verdicts=FLOW_VERDICTS, ahead=AHEAD)
        self.assertTrue(got.tiered)
        self.assertEqual(got.context(20, "a" * 40, "quick"), "green")
        self.assertEqual(got.context(21, "b" * 40, "quick"), "red")
        self.assertIsNone(got.context(20, "a" * 40, "full"))
        self.assertEqual(got.context(22, "c" * 40, "full"), "green")
        self.assertIsNone(got.context(22, None, "full"))
        trains = got.trains()
        self.assertEqual(trains[0]["quick"], {20: "green", 21: "red"})
        queue = flow.with_contexts({"queue": [
            {"pr": 20, "sha": "aaaaaaaaaa"}, {"pr": 22, "sha": "cccccccccc"},
            {"pr": 0, "sha": "dddddddddd"}]}, got, trains)["queue"]
        state = trains[0]["state"]
        self.assertEqual((queue[0]["quick"], queue[0]["full"]),
                         ("green", "train 1: %s" % state))
        self.assertEqual((queue[1]["quick"], queue[1]["full"]),
                         (None, "green"))
        self.assertNotIn("quick", queue[2])

    def test_the_page_shows_them(self):
        got = self.flow(verdicts=FLOW_VERDICTS, ahead=AHEAD)
        trains = got.trains()
        data = {"tiered": True, "trains": trains, "check_queue": {
            "queue": [{"pr": 20, "sha": "aaaaaaaaaa", "quick": "green",
                       "full": "red"}]}}
        self.assertIn("<th>checks</th>", flow.queue_html(data))
        self.assertIn("quick green · full red", flow.queue_html(data))
        self.assertIn("quick check on its head: red", flow.trains_html(data))
        data["tiered"] = False
        self.assertNotIn("<th>checks</th>", flow.queue_html(data))

    def test_flow_json_says_whether_tiered(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        flow_tests.write_state(self.root, verdicts=FLOW_VERDICTS,
                               ahead=AHEAD)
        out = os.path.join(tmp.name, "out")
        self.assertEqual(flow.main(["--state-dir", self.root, "--out", out,
                                    "--now", "2026-11-04T11:00:00Z"],
                                   out=lambda *_: None,
                                   err=lambda *_: None, environ={}), 0)
        with open(os.path.join(out, "flow.json")) as handle:
            got = handle.read()
        self.assertIn('"tiered": true', got)


class FastLane(unittest.TestCase):

    def setUp(self):
        for name, value in (("FAST_LANE_S", 60),
                            ("TIERS", {"build": "train", "docs": "head"})):
            patch = mock.patch.object(pc, name, value)
            patch.start()
            self.addCleanup(patch.stop)
        self.state = {"waiting": {"a" * 40: waits(7, 5, ["docs", "build"]),
                                  "b" * 40: waits(8, 5, ["docs", "fmt"])}}

    def test_every_step_counts_without_checks_quick(self):
        with mock.patch.object(pc, "QUICK_CONTEXT", None):
            self.assertEqual(pc.cheap_heads(
                self.state, {"docs": 3, "build": 300, "fmt": 1}),
                {"b" * 40: 4})
            self.assertEqual(pc.cheap_heads(self.state, {"docs": 3}), {})

    def test_only_the_quick_tier_counts_with_it(self):
        with mock.patch.object(pc, "QUICK_CONTEXT", "ci/quick"):
            # build is a "train" step: it neither costs nor, without a
            # cost, makes the head expensive.
            self.assertEqual(pc.cheap_heads(
                self.state, {"docs": 3, "build": 300, "fmt": 1}),
                {"a" * 40: 3, "b" * 40: 4})
            self.assertEqual(pc.cheap_heads(self.state, {"docs": 3}),
                             {"a" * 40: 3})


if __name__ == "__main__":
    unittest.main()
