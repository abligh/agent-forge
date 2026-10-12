"""Tests for bin/forge-stats: figures from verdicts.log, merger.jsonl and
GitHub's approvals."""
import datetime
import json
import os
import tempfile
import unittest

from tests.helpers import EXAMPLE, load_tool

tool = load_tool("forge-stats")
T = tool.parse_time
SINCE, UNTIL = T("2026-10-12T00:00Z"), T("2026-10-12T02:00Z")

VERDICTS = [
    "2026-10-11T23:59:00Z VERDICT #1 aaaaaaaaaa: green waited=9s ran=99s",
    "2026-10-12T00:10:00Z VERDICT #2 bbbbbbbbbb: green waited=60s ran=300s",
    "2026-10-12T00:20:00Z VERDICT AHEAD #3 cccccccccc: red(test) "
    "waited=0s ran=120s",
    "2026-10-12T00:30:00Z MAIN-GREEN main dddddddddd: green waited=600s "
    "ran=240s",
    "2026-10-12T00:40:00Z VERDICT #4 eeeeeeeeee: red(conflict) conflicts "
    "with main at ffffffffff: a.txt",
    "2026-10-12T00:50:00Z MATCHED #5 1111111111: its tree tested green",
    "2026-10-12T01:00:00Z VERDICT #6 2222222222: red(vet) waited=30s "
    "ran=60s",
    "2026-10-12T01:10:00Z CACHE-PRUNED slot 2: freed 1 bytes",
]


def tick(at, queue, *decisions):
    return {"at": at, "queue": queue, "decisions": list(decisions)}


RECORDS = [
    tick("2026-10-11T23:50:00Z", [7]),
    tick("2026-10-12T00:05:00Z", [7, 8]),
    tick("2026-10-12T00:15:00Z", [7, 8],
         {"action": "MERGE", "pr": 7, "head": "a", "result": "done"}),
    tick("2026-10-12T00:16:00Z", [8],
         {"action": "HAND-BACK", "pr": 8, "head": "b",
          "reason": "it conflicts with main, so it can't be updated"}),
    tick("2026-10-12T00:17:00Z", [8],
         {"action": "HAND-BACK", "pr": 8, "head": "b",
          "reason": "it conflicts with main, so it can't be updated"}),
    tick("2026-10-12T00:18:00Z", []),
    tick("2026-10-12T01:00:00Z", [8]),
    tick("2026-10-12T01:30:00Z", [8],
         {"action": "MERGE", "pr": 8, "head": "c", "result": "done"}),
    tick("2026-10-12T03:00:00Z", [9],
         {"action": "MERGE", "pr": 9, "head": "d", "result": "done"}),
]


class Check(unittest.TestCase):

    def setUp(self):
        self.got = tool.check_stats(VERDICTS, SINCE, UNTIL, 2)

    def test_runs_by_kind_in_the_window(self):
        self.assertEqual(self.got["runs"], {"pull request": 2,
                                            "prediction": 1, "main": 1,
                                            "conflict": 1})
        self.assertEqual(self.got["red"], {"prediction": 1,
                                           "pull request": 2})
        self.assertEqual(self.got["matched"], 1)

    def test_waits_runs_and_busy_slots(self):
        self.assertEqual(self.got["ran"]["n"], 4)
        self.assertEqual(self.got["ran"]["max"], 300)
        self.assertEqual(self.got["waited"]["max"], 600)
        self.assertEqual(self.got["pull_request_seen_to_verdict"]["max"], 360)
        self.assertEqual(self.got["busy_seconds"], 720)

    def test_predictions_used_and_not(self):
        lines = VERDICTS + [
            "2026-10-12T01:20:00Z VERDICT AHEAD #8 3333333333: green "
            "waited=0s ran=200s",
            "2026-10-12T01:30:00Z MATCHED #8 4444444444: its tree tested "
            "green as the Merger's prediction for #8 3333333333; not run "
            "again"]
        got = tool.check_stats(lines, SINCE, UNTIL, 2)["predictions"]
        self.assertEqual(got, {"run": 2, "used": 1, "seconds": 320,
                               "unused_seconds": 120})
        self.assertEqual(self.got["busy_share"], round(720 / 14400, 3))


class Queue(unittest.TestCase):

    def setUp(self):
        self.got = tool.queue_stats(RECORDS, SINCE, UNTIL)

    def test_merges_in_the_window_from_when_each_last_joined(self):
        self.assertEqual(self.got["merged"], [7, 8])
        # #7 joined at 23:50 (before the window), #8 again at 01:00.
        self.assertEqual(self.got["queue_to_merge"]["n"], 2)
        self.assertEqual(self.got["queue_to_merge"]["median"], 25 * 60)
        self.assertEqual(self.got["queue_to_merge"]["max"], 30 * 60)
        self.assertEqual(self.got["per_hour"], {"2026-10-12T00": 1,
                                                "2026-10-12T01": 1})

    def test_a_hand_back_is_counted_once_per_head_and_cause(self):
        self.assertEqual(self.got["hand_backs"], {"conflict": 1})

    def test_causes(self):
        self.assertEqual(tool.cause_of("vm/x is failure: red"), "red")
        self.assertEqual(tool.cause_of("abc, after the approval, is not "
                                       "a merge"), "approval")
        self.assertEqual(tool.cause_of("forge-closing-keywords exited 1"),
                         "closing keywords")
        self.assertEqual(tool.cause_of("something new"), "other")


class Approvals(unittest.TestCase):

    def call(self, url, token, scheme=None, method=None, body=None):
        self.queries.append(body["query"])
        return 200, {"data": {"repository": {
            "p7": {"number": 7, "author": {"login": "leaddevwidgetagent[bot]"},
                   "mergedAt": "2026-10-12T00:15:00Z",
                   "reviews": {"nodes": [
                       {"submittedAt": "2026-10-11T23:00:00Z"},
                       {"submittedAt": "2026-10-12T00:05:00Z"},
                       {"submittedAt": "2026-10-12T00:20:00Z"}]}},
            "p8": {"number": 8, "author": {"login": "leaddevwidgetagent[bot]"},
                   "mergedAt": "2026-10-12T01:30:00Z",
                   "reviews": {"nodes": [
                       {"submittedAt": "2026-10-12T01:00:00Z"}]}}}}}

    def test_the_latest_approval_before_the_merge(self):
        self.queries = []
        got = tool.fetch_merged("tok", "example/widget", [8, 7, 7],
                                call=self.call)
        self.assertEqual(len(self.queries), 1)
        self.assertEqual(got[7]["approved"], T("2026-10-12T00:05:00Z"))
        approval, authors = tool.author_stats(got, SINCE, UNTIL,
                                              lambda login: "LeadDev")
        self.assertEqual(approval["max"], 30 * 60)
        self.assertEqual(authors["LeadDev"]["merges"], 2)
        self.assertEqual(authors["LeadDev"]
                         ["hours_with_approved_work_waiting"], 0.7)

    def test_a_refusal_is_an_error(self):
        with self.assertRaises(RuntimeError):
            tool.fetch_merged("tok", "o/r", [1],
                              call=lambda *a, **k: (401, {"message": "no"}))


class Idle(unittest.TestCase):

    LINES = [json.dumps(l) for l in (
        {"at": "2026-10-12T00:10:00Z", "name": "WidgetLeadDev",
         "status": "idle", "since": "2026-10-12T00:00:00Z"},
        {"at": "2026-10-12T00:20:00Z", "name": "WidgetLeadDev",
         "status": "busy", "since": None},
        {"at": "2026-10-12T01:00:00Z", "name": "WidgetLeadDev",
         "status": "idle", "since": "2026-10-12T01:00:00Z"})] + ["junk"]

    def test_idle_from_since_to_the_next_change_or_the_end(self):
        spans = tool.idle_spans(self.LINES, UNTIL)
        self.assertEqual(spans["WidgetLeadDev"], [
            (SINCE, T("2026-10-12T00:20Z")),
            (T("2026-10-12T01:00Z"), UNTIL)])

    def test_idle_while_approved_work_waits(self):
        merged = {7: {"author": "x", "merged": T("2026-10-12T01:30Z"),
                      "approved": T("2026-10-12T00:10Z")}}
        idle = {"LeadDev": tool.idle_spans(self.LINES,
                                           UNTIL)["WidgetLeadDev"]}
        _, authors = tool.author_stats(merged, SINCE, UNTIL,
                                       lambda login: "LeadDev", idle)
        # Waiting 00:10-01:30; idle 00:10-00:20 and 01:00-01:30.
        self.assertEqual(authors["LeadDev"]
                         ["hours_idle_with_approved_work_waiting"], 0.7)


class Spans(unittest.TestCase):

    def test_overlaps_count_once_and_are_clipped(self):
        h = datetime.timedelta(hours=1)
        spans = [(SINCE - h, SINCE + h / 2), (SINCE + h / 4, SINCE + h),
                 (SINCE + h * 3 / 2, UNTIL + h)]
        self.assertEqual(tool.union_seconds(spans, SINCE, UNTIL), 5400)


class Main(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        self.env = {"FORGE_CONFIG": str(EXAMPLE),
                    "FORGE_STATE_DIR": self.dir.name, "HOME": self.dir.name}
        self.out = []

    def write(self):
        root = os.path.join(self.dir.name, "widget")
        os.makedirs(os.path.join(root, "pr-check"))
        os.makedirs(os.path.join(root, "merger"))
        with open(os.path.join(root, "pr-check", "verdicts.log"), "w") as f:
            f.write("\n".join(VERDICTS) + "\n")
        with open(os.path.join(root, "merger", "merger.jsonl"), "w") as f:
            f.write("".join(json.dumps(r) + "\n" for r in RECORDS))

    def main(self, *argv):
        return tool.main(["--since", "2026-10-12T00:00Z", "--until",
                          "2026-10-12T02:00Z"] + list(argv),
                         self.out.append, self.env)

    def test_no_records_is_2(self):
        self.assertEqual(self.main("--no-github"), 2)
        self.assertIn("NO-RECORDS", self.out[0])

    def test_the_report(self):
        self.write()
        self.assertEqual(self.main("--no-github"), 0)
        text = self.out[0]
        self.assertIn("widget, 2026-10-12T00:00Z to 2026-10-12T02:00Z", text)
        self.assertIn("runs: 4 (main 1, prediction 1, pull request 2)", text)
        self.assertIn("slots busy: 5% of 2 slots", text)
        self.assertIn("merges: 2 (1.0 an hour)", text)
        self.assertIn("hand-backs: conflict 1", text)
        self.assertNotIn("Approval to merge", text)

    def test_json(self):
        self.write()
        self.assertEqual(self.main("--no-github", "--json"), 0)
        got = json.loads(self.out[0])
        self.assertEqual(got["queue"]["merged"], [7, 8])
        self.assertEqual(got["since"], "2026-10-12T00:00Z")


if __name__ == "__main__":
    unittest.main()
