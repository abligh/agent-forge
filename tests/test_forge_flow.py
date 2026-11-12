"""Tests for bin/forge-flow: the merge pipeline's figures from small
synthetic records, and the page, JSON and history it writes."""
import json
import os
import tempfile
import unittest

from tests.helpers import example, load_tool

tool = load_tool("forge-flow")
T = tool.parse_time
NOW = T("2026-11-04T11:00:00Z")


def sha(c):
    return c * 40


def header(at, pr, c, slot=1, ahead=False):
    who = "main" if pr is None else "#%d" % pr
    where = "fast lane" if slot == "fast" else "slot %d" % slot
    return "=== 2026-11-04T%sZ worker %s %s %s%s ===" % (
        at, who, sha(c), where, " ahead" if ahead else "")


WORKER = [
    header("10:00:00", 1, "a"),
    "  test                 rc=0    590s",
    header("10:00:10", 2, "b", 2),
    header("10:12:00", 3, "c", 2),
    header("10:21:00", 3, "e", 2, ahead=True),
    header("10:40:00", None, "f", 1),
    header("10:41:00", 6, "6", 3),
    header("10:42:00", 7, "7", 1),
]
VERDICTS = [
    "2026-11-04T10:00:11Z VERDICT #2 bbbbbbbbbb: red(merge) conflicts "
    "with main at 9999999999: x.txt",
    "2026-11-04T10:10:00Z VERDICT #1 aaaaaaaaaa: green waited=5s ran=590s",
    "2026-11-04T10:20:00Z VERDICT #3 cccccccccc: green waited=30s "
    "ran=470s",
    "2026-11-04T10:31:00Z VERDICT AHEAD #3 eeeeeeeeee: green waited=0s "
    "ran=600s",
    "2026-11-04T10:32:00Z COVERED #1 aaaaaaaaaa: the top of the Merger's "
    "batch #1..#3 tested green as eeeeeeeeee; not run",
    "2026-11-04T10:33:00Z COVERED #3 cccccccccc: the top of the Merger's "
    "batch #1..#3 tested green as eeeeeeeeee; not run",
    "2026-11-04T10:40:05Z MAIN-GREEN main ffffffffff: matched; its tree "
    "tested green as #3's head cccccccccc, not run again",
    "2026-11-04T10:43:00Z STOPPED #6 6666666666 after 120s: superseded; "
    "no verdict",
    "2026-11-04T10:44:00Z VERDICT #7 7777777777: red(test) waited=0s "
    "ran=60s",
]


def rec(at, queue=(), decisions=(), **more):
    return dict({"at": "2026-11-04T%sZ" % at, "queue": list(queue),
                 "held": {}, "decisions": list(decisions)}, **more)


def d(action, pr, **more):
    return dict({"action": action, "pr": pr}, **more)


CONFLICT = "it conflicts with main, so it can't be updated"
RECORDS = [
    rec("10:00:00"),
    rec("10:05:00", [1], [d("WAIT", 1)]),
    rec("10:15:00", [1, 3], [d("WAIT", 1)], batch=[1, 3],
        right_of_way={"pr": 1}),
    rec("10:32:00", [1, 3], [d("MERGE", 1, head=sha("a"), result="done")],
        batch=[3], right_of_way={"pr": 1}),
    rec("10:33:00", [3], [d("MERGE", 3, head=sha("c"), result="done")]),
    rec("10:34:00", [4], [d("HAND-BACK", 4, head="d", result="done",
                            reason=CONFLICT)], batch=[4]),
    rec("10:35:00", [4], [d("HAND-BACK", 4, head="d",
                            result="already said", reason=CONFLICT)],
        batch=[5]),
    rec("10:36:00", [4], [d("HAND-BACK", 4, head="d", result="done",
                            reason=CONFLICT)]),
    rec("10:40:00", [], [], held={"5": "stacked on #6"},
        overdue={"5": 30}),
]


def write_state(root, worker=WORKER, verdicts=VERDICTS, records=RECORDS,
                presence=None, ahead=None):
    for sub in ("pr-check", "merger", "router"):
        os.makedirs(os.path.join(root, sub), exist_ok=True)
    with open(os.path.join(root, "pr-check", "worker.log"), "w") as h:
        h.write("\n".join(worker) + "\n")
    with open(os.path.join(root, "pr-check", "verdicts.log"), "w") as h:
        h.write("\n".join(verdicts) + "\n")
    with open(os.path.join(root, "merger", "merger.jsonl"), "w") as h:
        h.write("".join(json.dumps(r) + "\n" for r in records)
                + "not json\n")
    with open(os.path.join(root, "merger", "handed-back.json"), "w") as h:
        json.dump({"4": {"head": "d", "reason": CONFLICT}}, h)
    if ahead is not None:
        with open(os.path.join(root, "merger", "ahead.json"), "w") as h:
            json.dump(ahead, h)
    if presence is not None:
        with open(os.path.join(root, "router", "presence.jsonl"), "w") as h:
            h.write("".join(json.dumps(p) + "\n" for p in presence))


class Base(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = os.path.join(self.tmp.name, "widget")

    def flow(self, now=NOW, slots=4, reviewers=(), **state):
        write_state(self.root, **state)
        return tool.Flow(self.root, now, slots, reviewers)


class Runs(Base):

    def test_each_run_gets_its_end_and_slot_time(self):
        flow = self.flow()
        got = {(r["kind"], r["pr"]): r for r in flow.runs}
        self.assertEqual(got[("head", 1)]["end"]["res"], "green")
        self.assertEqual(got[("head", 1)]["dur"], 600)
        self.assertEqual(got[("head", 2)]["end"]["steps"], "merge")
        self.assertEqual(got[("ahead", 3)]["members"], [1, 3])
        self.assertEqual(tool.category(got[("head", 6)]),
                         "stopped or superseded")
        self.assertEqual(tool.category(got[("head", 7)]), "head red")

    def test_a_run_that_posted_without_building_is_no_run(self):
        flow = self.flow()
        self.assertNotIn("main", {r["kind"] for r in flow.runs})
        # But a pull request's first worker still marks its arrival.
        self.assertEqual(flow.arrived[1], T("2026-11-04T10:00:00Z"))

    def test_a_second_run_of_the_same_commit_is_a_rerun(self):
        flow = self.flow(worker=WORKER + [header("10:50:00", 7, "7", 2)])
        last = flow.runs[-1]
        self.assertEqual(last["nth"], 1)
        self.assertEqual(tool.category(last), "rerun")

    def test_a_run_with_no_end_holds_its_slot_until_now(self):
        flow = self.flow(worker=WORKER + [header("10:55:00", 8, "8", 4)])
        self.assertEqual(tool.category(flow.runs[-1]), "running")
        self.assertEqual(flow.runs[-1]["dur"], 300)


class States(Base):

    def test_each_tick_is_one_state_until_the_next(self):
        flow = self.flow()
        got = flow.state_minutes(T("2026-11-04T10:00:00Z"), NOW)
        self.assertEqual(got[tool.NOTHING], 5)
        self.assertEqual(got[tool.HEAD_WAIT], 10)
        # 17 minutes to the next tick: GAP waiting, then not ticking.
        self.assertEqual(got[tool.BATCH_WAIT], 10)
        self.assertEqual(got[tool.MERGING], 2)
        self.assertEqual(got[tool.HANDED], 6)
        self.assertEqual(got[tool.HELD], 10)
        self.assertEqual(got[tool.NO_TICKS], 17)
        self.assertEqual(sum(got.values()), 60)

    def test_before_the_first_record_the_merger_was_not_ticking(self):
        flow = self.flow()
        got = flow.state_minutes(T("2026-11-04T09:00:00Z"),
                                 T("2026-11-04T10:00:00Z"))
        self.assertEqual(got[tool.NO_TICKS], 60)

    def test_an_update_waits_on_a_head_check(self):
        self.assertEqual(tool.tick_state(rec("1", [1], [d("UPDATE", 1)]),
                                         False), tool.HEAD_WAIT)

    def test_a_red_batch_top_starts_a_culprit_search(self):
        worker = [header("10:00:00", 2, "e", 1, ahead=True)]
        verdicts = ["2026-11-04T10:10:00Z VERDICT AHEAD #2 eeeeeeeeee: "
                    "red(test) waited=0s ran=600s"]
        records = [rec("09:59:00", [1, 2], [d("WAIT", 1)], batch=[1, 2]),
                   rec("10:11:00", [1, 2], [d("WAIT", 1)], batch=[1]),
                   rec("10:20:00", [1, 2], [d("MERGE", 1, head="x",
                                                result="done")]),
                   rec("10:21:00", [2], [d("WAIT", 2)]),
                   rec("10:30:00", [9], [d("WAIT", 9)])]
        flow = self.flow(worker=worker, verdicts=verdicts, records=records)
        self.assertEqual([r["_state"] for r in flow.records], [
            tool.BATCH_WAIT, tool.SEARCH, tool.MERGING, tool.SEARCH,
            tool.HEAD_WAIT])

    def test_ahead_json_says_a_search_is_on_now(self):
        flow = self.flow(ahead={"batch": {"size": 1, "suspects": True}})
        self.assertEqual(flow.records[-1]["_state"], tool.SEARCH)
        self.assertTrue(flow.snapshot()["searching"])


class Figures(Base):

    def setUp(self):
        super().setUp()
        self.got = self.flow().figures(T("2026-11-04T10:00:00Z"), NOW)

    def test_merges_and_arrivals(self):
        self.assertEqual(self.got["merges"], 2)
        self.assertEqual(self.got["arrivals"], 5)

    def test_both_merges_rest_on_one_batch_top(self):
        slots = self.got["merge_slots"]
        self.assertEqual(slots["slots"], 1)
        self.assertEqual(slots["prs_per_slot"], 2.0)
        self.assertEqual(slots["sizes"], {"1": 0, "2-3": 1, "4+": 0})
        self.assertEqual(self.got["green_batch_tops"],
                         {"green": 1, "all": 1, "part": 0, "none": 0})

    def test_runs_per_merged_pull_request_count_its_batch_runs(self):
        per = self.got["runs_per_merged_pr"]
        self.assertEqual((per["mean"], per["heads_mean"],
                          per["batches_mean"]), (2.0, 1.0, 1.0))
        # Six runs: four heads, a prediction and a stopped one.
        self.assertEqual(self.got["runs_total"], 6)
        self.assertEqual(self.got["runs_per_merge"], 3.0)

    def test_hand_backs_told_and_repeated(self):
        hb = self.got["hand_backs"]
        self.assertEqual((hb["told"], hb["repeats_total"]), (2, 1))
        self.assertEqual(hb["per_merge"], 1.0)
        self.assertEqual(hb["per_merge_by_cause"], {"conflict": 1.0})
        self.assertEqual(self.got["head_checks"],
                         {"green": 2, "red(merge)": 1, "red": 1})

    def test_batch_tops_are_counted_apart_from_heads(self):
        # The one prediction, #3's batch top, went green; no head's
        # count includes it.
        self.assertEqual(self.got["batch_checks"], {"green": 1})

    def test_first_approval_is_the_first_tick_that_lists_it(self):
        lat = self.got["approval_to_merge"]["local_minutes"]
        # #1 listed at 10:05, merged 10:32; #3 at 10:15, merged 10:33.
        self.assertEqual((lat["n"], lat["median"]), (2, 18))

    def test_capacity(self):
        self.assertEqual(self.got["slot_hours_available"], 4.0)
        self.assertEqual(self.got["head_wait_seconds"]["median"], 5)
        self.assertIn("batch green", self.got["slot_hours"])

    def test_policy(self):
        self.assertEqual(self.got["right_of_way_share"], round(2 / 9, 3))
        # 10:35 cut [5] where [4] was; 10:32 took the merged #1 off,
        # and 10:34 followed a tick with no batch.
        self.assertEqual(self.got["recuts"], 1)


class TrainFigures(Base):
    """designs/0047's figures: approvals an hour, pull requests per green
    top, and re-cuts by cause."""

    def test_approvals_and_pull_requests_per_green_top(self):
        got = self.flow().figures(T("2026-11-04T10:00:00Z"), NOW)
        # #1, #3, #4 queued and #5 held, each first listed in the hour.
        self.assertEqual((got["approvals"], got["approvals_per_hour"]),
                         (4, 4.0))
        # The one green top had #1 and #3.
        self.assertEqual(got["prs_per_green_top"], 2.0)

    def test_re_cuts_by_cause(self):
        records = RECORDS + [
            rec("10:45:00", [6, 7], recut=True, trains=[[6, 7]],
                train_events=["left", "cut"]),
            rec("10:46:00", [6, 7], recut=True),
            rec("10:47:00", [6, 7], recut=True, trains=[[6], [7]],
                trains_dry=True, recut_avoided=True,
                train_events=["cut"]),
            rec("10:48:00", [6, 7], trains=[[7]], trains_dry=True,
                train_events=["left"])]
        got = self.flow(records=records).figures(
            T("2026-11-04T10:00:00Z"), NOW)
        self.assertEqual(got["recuts_by_cause"], {"other": 2, "left": 1})
        self.assertEqual(got["recuts_avoided"], 1)
        self.assertEqual(got["dry_train_changes"], {"left": 1})


class PerHour(Base):
    """Section 2's counts, per hour in every window (designs/0045,
    amendment of 2026-11-04)."""

    def test_counts_are_per_hour_of_the_window(self):
        got = self.flow().figures(T("2026-11-04T09:00:00Z"), NOW)
        self.assertEqual(got["hours"], 2.0)
        self.assertEqual((got["merges"], got["merges_per_hour"]), (2, 1.0))
        self.assertEqual(got["arrivals_per_hour"], 2.5)
        self.assertEqual(got["runs_per_hour"], 3.0)
        self.assertEqual(got["hand_backs_per_hour"], 1.0)

    def test_run_times_by_kind(self):
        got = self.flow().figures(T("2026-11-04T10:00:00Z"), NOW)
        # Heads #1, #3 and #7 built to a verdict; #2's red(merge) and the
        # stopped #6 built nothing to time.
        self.assertEqual(got["run_seconds_by_kind"]["head"],
                         {"n": 3, "mean": 373, "median": 470, "p90": 590})
        self.assertEqual(got["run_seconds_by_kind"]["batch"]["mean"], 600)
        self.assertEqual(got["run_seconds_by_kind"]["main"]["n"], 0)
        self.assertIsNone(got["run_seconds_by_kind"]["main"]["mean"])
        self.assertEqual(got["run_seconds"]["mean"], 430)


class BuildsPerPullRequest(Base):
    """The owner's two figures: all runs over merges, and each merged
    pull request's own runs."""

    def test_runs_per_merge_is_every_run_started_over_merges(self):
        # Each merged pull request has one head run and was in one batch
        # run; nothing else ran.
        worker = [header("10:00:00", 1, "a"), header("10:01:00", 2, "b", 2),
                  header("10:20:00", 2, "e", 1, ahead=True)]
        verdicts = [
            "2026-11-04T10:10:00Z VERDICT #1 aaaaaaaaaa: green waited=0s "
            "ran=500s",
            "2026-11-04T10:11:00Z VERDICT #2 bbbbbbbbbb: green waited=0s "
            "ran=500s",
            "2026-11-04T10:30:00Z VERDICT AHEAD #2 eeeeeeeeee: green "
            "waited=0s ran=500s"]
        records = [rec("10:15:00", [1, 2], [d("WAIT", 1)], batch=[1, 2]),
                   rec("10:31:00", [1, 2], [
                       d("MERGE", 1, head=sha("a"), result="done"),
                       d("MERGE", 2, head=sha("b"), result="done")])]
        got = self.flow(worker=worker, verdicts=verdicts,
                        records=records).figures(T("2026-11-04T10:00:00Z"),
                                                 NOW)
        # Three runs for two merges; two runs on each one's own path.
        self.assertEqual(got["runs_per_merge"], 1.5)
        self.assertEqual(got["runs_per_merged_pr"]["mean"], 2.0)

    def test_own_path_counts_every_push_but_not_runs_after_the_merge(self):
        worker = [header("09:00:00", 1, "1"), header("09:30:00", 1, "2"),
                  header("10:00:00", 1, "a"), header("10:50:00", 1, "a")]
        verdicts = [
            "2026-11-04T10:10:00Z VERDICT #1 aaaaaaaaaa: green waited=0s "
            "ran=500s"]
        records = [rec("10:15:00", [1], [d("MERGE", 1, head=sha("a"),
                                             result="done")])]
        got = self.flow(worker=worker, verdicts=verdicts,
                        records=records).figures(T("2026-11-04T10:00:00Z"),
                                                 NOW)
        self.assertEqual(got["runs_per_merged_pr"]["mean"], 3.0)
        # Runs started in the window: 10:00 and 10:50.
        self.assertEqual(got["runs_per_merge"], 2.0)


class Graphs(Base):

    def test_the_last_24_whole_hours_with_gaps_where_no_records(self):
        rows = self.flow().graph_hours()
        self.assertEqual(len(rows), 24)
        self.assertEqual(rows[-1]["hour"], "2026-11-04T10:00Z")
        self.assertEqual(rows[0]["hour"], "2026-11-03T11:00Z")
        # Before the first record: a gap, not zero.
        self.assertEqual(rows[-2], {"hour": "2026-11-04T09:00Z",
                                    "records": False})
        got = rows[-1]
        self.assertTrue(got["records"])
        self.assertEqual((got["merges"], got["runs_per_merge"],
                          got["runs_per_merged_pr"], got["prs_per_slot"]),
                         (2, 3.0, 2.0, 2.0))
        self.assertEqual((got["approval_median"], got["approval_n"]),
                         (18, 2))

    def test_an_hour_without_ticks_inside_the_records_is_a_gap(self):
        records = [rec("08:10:00"), rec("10:05:00", [1], [d("WAIT", 1)])]
        rows = self.flow(records=records).graph_hours()
        by = {r["hour"][11:13]: r for r in rows}
        self.assertTrue(by["08"]["records"])
        self.assertEqual(by["08"]["merges"], 0)
        self.assertFalse(by["09"]["records"])
        self.assertTrue(by["10"]["records"])

    def test_build_times_in_6_hour_buckets_for_7_days(self):
        rows = self.flow().build_times()
        self.assertEqual(len(rows), 28)
        self.assertEqual(rows[-1]["since"], "2026-11-04T06:00Z")
        self.assertEqual(rows[-1]["until"], "2026-11-04T11:00Z")
        self.assertTrue(rows[-1]["partial"])
        self.assertEqual(rows[-1]["head"]["n"], 3)
        self.assertEqual(rows[-1]["batch"]["median"], 600)
        self.assertEqual(rows[0]["since"], "2026-10-28T12:00Z")
        self.assertEqual(rows[0]["head"]["n"], 0)
        self.assertFalse(rows[0]["partial"])


def line(hour, merges, **more):
    states = dict.fromkeys(tool.STATES, 0.0)
    states[tool.MERGING if merges else tool.HEAD_WAIT] = 60.0
    return json.dumps(dict({"hour": "2026-11-04T%s:00Z" % hour,
                            "merges": merges, "states": states}, **more))


class SavedHistory(unittest.TestCase):
    """A flow.json written before the graphs takes them from
    flow-history.jsonl."""

    LINES = [
        # An older line: no runs per merged pull request, no latency.
        line("06", 4, runs_per_merge=2.5),
        line("08", 5, runs_per_merge=2.0, runs_per_merged_pr={"mean": 3.0},
             approval_to_merge={"local_minutes": {"n": 5, "median": 40,
                                                  "p90": 90}},
             run_seconds_by_kind={"head": {"n": 2, "mean": 600},
                                  "batch": {"n": 0, "mean": None}}),
        # The Merger was not ticking all hour.
        json.dumps({"hour": "2026-11-04T09:00Z", "merges": 0,
                    "states": {tool.NO_TICKS: 60.0}}),
        line("10", 0, run_seconds_by_kind={"head": {"n": 1,
                                                    "mean": 900}}),
        "not json",
    ]

    def setUp(self):
        self.data = tool.with_graphs({"generated": "2026-11-04T11:20Z"},
                                     self.LINES)

    def test_a_missing_hour_is_a_gap_never_a_zero(self):
        by = {r["hour"][11:13]: r for r in self.data["graph_hours"]}
        self.assertFalse(by["07"]["records"])
        self.assertNotIn("merges", by["07"])
        self.assertFalse(by["09"]["records"])
        self.assertEqual(by["10"]["merges"], 0)
        self.assertEqual(tool.graph_values(self.data["graph_hours"][-5:],
                                           "merges"), [4, None, 5, None, 0])

    def test_an_older_line_lacks_some_figures(self):
        by = {r["hour"][11:13]: r for r in self.data["graph_hours"]}
        self.assertEqual(by["06"]["runs_per_merge"], 2.5)
        self.assertIsNone(by["06"]["runs_per_merged_pr"])
        self.assertEqual((by["08"]["runs_per_merged_pr"],
                          by["08"]["approval_median"],
                          by["08"]["approval_p90"]), (3.0, 40, 90))

    def test_build_times_weight_each_hours_mean_by_its_runs(self):
        last = self.data["build_times"][-1]
        self.assertEqual(last["since"], "2026-11-04T06:00Z")
        self.assertEqual(last["head"], {"n": 3, "mean": 700, "median": None,
                                        "p90": None})
        self.assertIsNone(last["batch"]["mean"])

    def test_own_graphs_are_kept(self):
        data = tool.with_graphs({"generated": "2026-11-04T11:20Z",
                                 "graph_hours": [], "build_times": []},
                                self.LINES)
        self.assertEqual((data["graph_hours"], data["build_times"]),
                         ([], []))


class Svg(unittest.TestCase):

    def chart(self, values, bars=False, ref=None):
        return tool.chart("t", "A title", "What it shows.", "units",
                          ["s%d" % i for i in range(len(values))],
                          [("series", values)], [(0, "00")], ref=ref,
                          bars=bars, gap="no records")

    def test_it_has_a_title_a_description_and_a_table(self):
        got = self.chart([1, 2, 3])
        self.assertIn('aria-labelledby="t-t t-d"', got)
        self.assertIn('<title id="t-t">A title</title>', got)
        self.assertIn('<desc id="t-d">What it shows.</desc>', got)
        self.assertIn("<details><summary>Values</summary>", got)
        self.assertIn(">units</text>", got)

    def test_a_gap_breaks_the_line(self):
        got = self.chart([1.0, 2.0, None, 4.0, 5.0])
        self.assertEqual(got.count('class="line"'), 2)
        self.assertIn("<title>s2: no records</title>", got)
        self.assertIn("<title>s3: series: 4.00</title>", got)
        # Points alone are dots, plus the end of the last line.
        got = self.chart([1.0, None, 3.0])
        self.assertEqual(got.count('class="line"'), 0)
        self.assertEqual(got.count('class="dot"'), 3)

    def test_a_zero_bar_differs_from_a_gap(self):
        got = self.chart([0, None, None, None, 2], bars=True)
        self.assertEqual(got.count('class="zero"'), 1)
        self.assertEqual(got.count("<path"), 1)
        self.assertIn("no records</text>", got)

    def test_a_reference_line_is_keyed(self):
        got = self.chart([3, 4], bars=True, ref=(10, "bar 10/h"))
        self.assertEqual(got.count('class="ref"'), 2)
        self.assertIn(">bar 10/h</text>", got)
        # The axis reaches past the reference.
        self.assertIn(">15</text>", got)

    def test_no_values_says_so(self):
        self.assertIn("no data yet", self.chart([None, None]))

    def test_nice_ticks(self):
        self.assertEqual(tool.nice_ticks(1.265), [0, 0.5, 1, 1.5])
        self.assertEqual(tool.nice_ticks(14), [0, 5, 10, 15])
        self.assertEqual(tool.nice_ticks(0), [0, 0.25, 0.5, 0.75, 1])


class Presence(Base):

    def test_busy_is_the_share_listed_that_was_busy(self):
        presence = [
            {"at": "2026-11-04T10:00:00Z", "name": "Rev", "status": "busy"},
            {"at": "2026-11-04T10:30:00Z", "name": "Rev", "status": "shell"},
            {"at": "2026-11-04T10:45:00Z", "name": "Rev", "status": "busy"},
            {"at": "2026-11-04T10:00:00Z", "name": "Dev", "status": "idle"},
            {"at": "2026-11-04T10:30:00Z", "name": "Dev", "status": "gone"},
            "not json"]
        flow = self.flow(presence=presence, reviewers={"Rev"})
        got = flow.figures(T("2026-11-04T10:00:00Z"), NOW)["busy"]
        self.assertEqual(list(got), ["Rev", "Dev"])
        self.assertEqual(got["Rev"], {"busy": 0.75, "listed_hours": 1.0,
                                      "reviewer": True})
        self.assertEqual(got["Dev"]["busy"], 0.0)
        self.assertEqual(got["Dev"]["listed_hours"], 0.5)
        watch = flow.watch_figures(T("2026-11-04T10:00:00Z"), NOW)
        self.assertEqual(watch["reviewer_busy"], (75.0, 1))
        self.assertEqual(watch["hand_backs_per_merge"], (1.0, 2))
        self.assertEqual(watch["prs_per_slot"], (2.0, 1))


class Snapshot(Base):

    def test_where_each_pull_request_waits(self):
        records = RECORDS[:-1] + [rec(
            "10:40:00", [4, 8, 9, 10], [d("HAND-BACK", 4, head="d",
                                          result="already said",
                                          reason=CONFLICT), d("WAIT", 8)],
            batch=[8], later=[[9]], right_of_way={"pr": 8},
            held={"11": "stacked on #8",
                  "12": "conflicts with #8, which has right of way",
                  "13": "review by someone at 2026-11-04T10:39:00Z, after "
                        "the approval", "14": "draft"},
            overdue={"8": 5, "9": 1})]
        got = self.flow(records=records).snapshot()
        self.assertEqual(got["batch"], [8])
        self.assertEqual(got["later"], [9])
        self.assertEqual(got["queued"], [10])
        self.assertEqual(got["handed_back"], [4])
        self.assertEqual(got["handed_back_causes"], {4: "conflict"})
        self.assertEqual(got["held"], {
            "stacked": [11], "right of way": [12],
            "review after the approval": [13], "other": [14]})
        self.assertEqual(got["right_of_way"],
                         {"pr": 8, "since": "2026-11-04T10:40Z"})
        self.assertEqual(got["overdue"], 2)


class StacksAndTrains(Base):
    """The stacks as trees and the trains now, for the page."""

    def last(self, **more):
        return RECORDS[:-1] + [rec("10:40:00", [20, 30], [d("WAIT", 20)],
                                   **more)]

    def test_stacks_are_trees_rooted_at_their_base(self):
        # 20 <- 21 <- 22 <- 23, and 21 <- 24 (a branch); 30 <- 31;
        # 40's base is not open or not approved (not held, not queued).
        held = {"21": "stacked on #20", "22": "stacked on #21",
                "23": "stacked on #22", "24": "stacked on #21",
                "31": "stacked on #30", "41": "stacked on #40",
                "30": "conflicts with #20, ahead of it in the queue",
                "50": "draft"}
        got = self.flow(records=self.last(held=held), ahead={
            "entries": [{"pr": 20, "train": 1, "sha": sha("a")}]}).stacks()
        self.assertEqual([(t["pr"], t["count"], t["depth"]) for t in got],
                         [(20, 4, 3), (30, 1, 1), (40, 1, 1)])
        twenty = got[0]
        self.assertEqual([k["pr"] for k in twenty["layers"]], [21])
        self.assertEqual([k["pr"] for k in twenty["layers"][0]["layers"]],
                         [22, 24])
        self.assertEqual(twenty["root_where"], "in train 1")
        self.assertEqual(got[1]["root_where"],
                         "held: conflicts with #20, ahead of it in the queue")
        self.assertTrue(got[2]["root_where"].startswith("not a candidate"))

    def test_a_loop_in_the_holds_does_not_hang(self):
        held = {"21": "stacked on #20", "20": "stacked on #21"}
        self.assertEqual(self.flow(records=self.last(held=held)).stacks(),
                         [])

    def test_trains_from_ahead_json(self):
        worker = WORKER + [header("10:45:00", 33, "9", 4, ahead=True)]
        ahead = {"at": "2026-11-04T10:50:00Z",
                 "batch": {"said": "BATCH #11..#12: top bbbbbbbbbb green, "
                           "merging 2", "size": 2},
                 "urgent_cut": [21],
                 "entries": [
                     {"pr": 11, "train": 0, "sha": sha("a")},
                     {"pr": 12, "train": 0, "sha": sha("b")},
                     {"pr": 21, "train": 1, "sha": sha("8")},
                     {"pr": 31, "train": 2, "sha": sha("c")},
                     {"pr": 33, "train": 2, "sha": sha("9")}]}
        got = self.flow(worker=worker, ahead=ahead).trains()
        self.assertEqual([(t["train"], t["prs"], t["top"], t["state"],
                           t["urgent"]) for t in got], [
            (0, [11, 12], "b" * 10, "green, merging", False),
            (1, [21], "8" * 10, "waiting for a slot", True),
            (2, [31, 33], "9" * 10, "testing", False)])
        self.assertEqual(got[2]["since"], "2026-11-04T10:45Z")

    def test_without_trains_the_batch_sizes_cut_them(self):
        ahead = {"batch": {"size": 2}, "later": [{"size": 1}],
                 "entries": [{"pr": str(n), "sha": sha(c)} for n, c in
                             ((11, "a"), (12, "b"), (13, "c"), (14, "d"))]}
        got = self.flow(ahead=ahead).trains()
        self.assertEqual([(t["train"], t["prs"]) for t in got],
                         [(0, [11, 12]), (1, [13]), (None, [14])])
        self.assertEqual(got[2]["state"], "predicted, not yet in a train")
        self.assertIn("in no train yet", tool.trains_html({"trains": got}))

    def test_nothing_ahead_is_no_train_and_no_stack(self):
        flow = self.flow(records=self.last())
        self.assertEqual(flow.trains(), [])
        self.assertEqual(flow.stacks(), [])

    def test_the_page_draws_them(self):
        data = {"repo": "owner/widget", "ahead_at": "2026-11-04T10:50:00Z",
                "trains": [{"train": 0, "prs": [11, 12], "top": "b" * 10,
                            "state": "green, merging", "since": None,
                            "urgent": False},
                           {"train": 1, "prs": [21], "top": "8" * 10,
                            "state": "waiting for a slot", "since": None,
                            "urgent": True}],
                "stacks": [{"pr": 20, "count": 2, "depth": 2,
                            "root_where": "in the queue", "layers": [
                                {"pr": 21, "count": 1, "depth": 1,
                                 "layers": [{"pr": 22, "count": 0,
                                             "depth": 0, "layers": []}]}]}]}
        trains = tool.trains_html(data)
        self.assertIn('class="train t-green"', trains)
        self.assertIn("cut for urgent PRs", trains)
        self.assertIn('href="https://github.com/owner/widget/pull/12"',
                      trains)
        self.assertEqual(trains.count('class="car top"'), 2)
        stacks = tool.stacks_html(data)
        self.assertIn("<b>2</b> layers in <b>1</b> stacks", stacks)
        self.assertIn("2 layers, 2 deep", stacks)
        self.assertEqual(stacks.count("<ul"), 3)
        self.assertNotIn("<script", trains + stacks)
        self.assertIn("No train", tool.trains_html({}))
        self.assertIn("No stacked", tool.stacks_html({}))
        self.assertIn(">#20<", tool.stacks_html(dict(data, repo=None)))


class StatusPriorityCarriages(Base):
    """The stacks' status dots, the priority marks and the trains'
    carriages."""

    def last(self, queue=(30,), **more):
        return RECORDS[:-1] + [rec("10:40:00", list(queue), [], **more)]

    def write_json(self, sub, name, data):
        os.makedirs(os.path.join(self.root, sub), exist_ok=True)
        with open(os.path.join(self.root, sub, name), "w") as handle:
            json.dump(data, handle)

    def test_carriages_from_ahead_json_or_from_the_stacks(self):
        named = [{"pr": 11, "carriage": 11}, {"pr": 12, "carriage": 11},
                 {"pr": 13, "carriage": 13}, {"pr": 14, "carriage": 14},
                 {"pr": 15, "carriage": 14}]
        self.assertEqual(tool.carriages_of(named, {}),
                         [[11, 12], [13], [14, 15]])
        bare = [{"pr": n} for n in (11, 12, 13, 14, 15)]
        # 12 rides on 11, 15 on 14; 13 is stacked, but not on 12.
        self.assertEqual(tool.carriages_of(bare, {12: 11, 13: 9, 15: 14}),
                         [[11, 12], [13], [14, 15]])

    def test_trains_carry_their_carriages(self):
        held = {"12": "stacked on #11", "15": "stacked on #14"}
        ahead = {"trains": True, "batch": {"size": 4}, "later": [
            {"size": 1}], "entries": [
            {"pr": 11, "train": 0, "carriage": 11, "sha": sha("a")},
            {"pr": 12, "train": 0, "carriage": 11, "sha": sha("b")},
            {"pr": 13, "train": 0, "carriage": 13, "sha": sha("c")},
            {"pr": 14, "train": 0, "carriage": 14, "sha": sha("d")},
            {"pr": 15, "train": 1, "sha": sha("e")}]}
        got = self.flow(records=self.last(held=held), ahead=ahead).trains()
        self.assertEqual([t["carriages"] for t in got],
                         [[[11, 12], [13], [14]], [[15]]])
        html = tool.trains_html({"trains": got})
        self.assertIn("4 PRs in 3 carriages", html)
        self.assertIn("1 PR in 1 carriage ", html)
        self.assertEqual(html.count('<span class="carriage'), 4 + 2)
        self.assertEqual(html.count('class="carriage van"'), 2)
        self.assertEqual(html.count('<svg class="engine"'), 2 + 4)

    def test_priority_inferred_without_github(self):
        self.write_json("nightly", "nightly.json", {
            "at": "2026-11-04T10:55:00Z", "issue_open": True,
            "fixes": [41]})
        self.write_json("pr-check", "state.json", {
            "main_red": {"issue": 900, "fixes": [42]}})
        flow = self.flow(records=self.last(right_of_way={"pr": 43}),
                         ahead={"urgent_cut": [40, 41, 42]})
        self.assertEqual({k: v["level"] for k, v in
                          flow.priorities().items()},
                         {"40": "urgent", "41": "elevated",
                          "42": "elevated", "43": "elevated"})
        self.assertIn("fixes nothing red", flow.priorities()["40"]["why"])
        self.assertIn("right of way", flow.priorities()["43"]["why"])

    def test_priority_from_the_merger_record(self):
        # forge-merger names every labelled and elevated pull request it
        # saw, held ones and layers too; that beats inferring.
        flow = self.flow(records=self.last(urgent=[50, 51], elevated=[52]),
                         ahead={"urgent_cut": [40]})
        self.assertEqual({k: v["level"] for k, v in
                          flow.priorities().items()},
                         {"50": "urgent", "51": "urgent",
                          "52": "elevated"})
        self.assertNotIn("fixes nothing red", flow.priorities()["50"]["why"])

    def test_priority_from_github_labels_and_stale_fixes(self):
        self.write_json("nightly", "nightly.json", {
            "at": "2026-11-04T09:00:00Z", "issue_open": True,
            "fixes": [41]})
        self.write_json("pr-check", "state.json", {
            "main_red": {"issue": None, "fixes": [42]}})
        flow = self.flow(records=self.last(), ahead={
            "urgent_cut": [40, 41, 42]})
        flow.labelled = {44}
        self.assertEqual(flow.priorities(), {"44": {
            "level": "urgent", "why": "urgent: carries the urgent label"}})

    def test_status_of_each_layer(self):
        worker = WORKER + [header("10:45:00", 52, "5", 2),
                           header("10:46:00", 53, "3", 2),
                           header("10:46:30", 54, "4", 2)]
        verdicts = VERDICTS + [
            "2026-11-04T10:47:00Z VERDICT #53 3333333333: red(test) "
            "waited=0s ran=60s",
            "2026-11-04T10:48:00Z VERDICT #54 4444444444: green "
            "waited=0s ran=60s"]
        held = {"51": "stacked on #50", "52": "stacked on #51",
                "53": "stacked on #52", "54": "stacked on #50",
                "55": "stacked on #54",
                "56": "vm/build-and-test is pending on 1234567890, and no "
                      "approved commit is green; waits for the build "
                      "check to pass",
                "57": "conflicts with #30, ahead of it in the queue",
                "58": "stacked on #57, which merged; waiting for: draft"}
        ahead = {"trains": True, "batch": {"size": 1}, "entries": [
            {"pr": 60, "train": 0, "carriage": 60, "sha": sha("6")}]}
        flow = self.flow(worker=worker, verdicts=verdicts, ahead=ahead,
                         records=self.last(queue=(30, 59), held=held))
        got = {pr: flow.status(pr)[0] for pr in range(50, 61)}
        self.assertEqual(got, {
            50: "red",      # not held, not queued: not a candidate
            51: "orange",   # stacked, own check never run
            52: "orange",   # stacked, own check running
            53: "red",      # stacked, own check red
            54: "green",    # stacked, own check green
            55: "orange",
            56: "orange",   # held for a pending check
            57: "red",      # held for a conflict
            58: "red",      # waits for something of its own
            59: "green",    # eligible
            60: "orange"})  # in a train waiting for a slot
        self.assertIn("waits for #50 below it", flow.status(54)[1])
        stacks = flow.stacks()
        self.assertEqual(stacks[0]["pr"], 50)
        self.assertEqual(stacks[0]["status"], "red")
        self.assertEqual(stacks[0]["layers"][0]["status"], "orange")
        # A base that merged, as a layer's hold says.
        held["50"] = "stacked on #49"
        held["58"] = "stacked on #49, which merged; waiting for: draft"
        flow = self.flow(records=self.last(held=held))
        self.assertEqual(flow.status(49), ("green", "merged"))

    def test_the_page_marks_priority_and_status(self):
        data = {"priority": {"21": {"level": "urgent", "why": "u"},
                             "22": {"level": "elevated", "why": "e"}},
                "trains": [{"train": 0, "prs": [20, 21, 22],
                            "carriages": [[20, 21], [22]], "top": "c" * 10,
                            "state": "testing", "since": None,
                            "urgent": True}],
                "stacks": [{"pr": 20, "count": 1, "depth": 1,
                            "status": "orange", "why": "in train 0",
                            "root_where": "in train 0", "layers": [
                                {"pr": 21, "count": 0, "depth": 0,
                                 "status": "green", "why": "ok",
                                 "layers": []}]}]}
        trains = tool.trains_html(data)
        self.assertIn("3 PRs in 2 carriages", trains)
        self.assertIn('class="train t-testing"', trains)
        self.assertIn('<span class="pri p-urgent" title="u">', trains)
        self.assertIn('<span class="pri p-elevated" title="e">', trains)
        self.assertIn('class="carriage van" title="the guard\'s van, '
                      'carriage 2"', trains)
        stacks = tool.stacks_html(data)
        self.assertIn('class="dot d-orange" role="img" aria-label="testing '
                      'underway: in train 0"', stacks)
        self.assertIn('class="dot d-green"', stacks)
        self.assertIn('<span class="pri p-urgent" title="u">', stacks)
        # The legends.
        for want in ("can merge", "testing underway", "cannot merge",
                     "elevated:", "guard's van"):
            self.assertIn(want, stacks + trains)
        css = tool.css_vars()
        self.assertIn("prefers-reduced-motion:reduce", css)
        self.assertIn("@keyframes puff{0%{", css)


class RecentTrains(Base):
    """What became of the last trains, from merger.jsonl."""

    RECORDS = [
        rec("10:00:00", [1, 2, 3, 4, 5], [d("WAIT", 1)],
            trains=[[1, 2, 3], [4, 5]], train_events=["cut"],
            held={"2": "stacked on #1"}),
        rec("10:05:00", [1, 2, 3, 4, 5], [d("WAIT", 1)],
            trains=[[1, 2, 3], [4, 5]]),
        rec("10:10:00", [4, 5], [d("MERGE", n, result="done", train=True)
                                 for n in (1, 2, 3)], trains=[[4, 5]]),
        # A red train's search: no trains in the record.
        rec("10:15:00", [4, 5], [d("HAND-BACK", 5, result="done",
                                   suspect=True, reason="red")]),
        rec("10:20:00", [6, 7], [d("MERGE", 4, result="done")],
            trains=[[6, 7]], train_events=["cut"]),
        rec("10:25:00", [6, 7, 8], [], trains=[[6], [7, 8]],
            train_events=["main moved", "cut"]),
        rec("10:30:00", [7, 8], [], trains=[[7, 8]],
            held={"6": "conflicts with #9, ahead of it in the queue"}),
        rec("10:35:00", [7, 8], [], trains=[[9]], trains_dry=True),
    ]

    def flow_of(self):
        worker = [header("10:11:00", 5, "5", 1, ahead=True)]
        verdicts = ["2026-11-04T10:14:00Z VERDICT AHEAD #5 5555555555: "
                    "red(test) waited=0s ran=180s"]
        return self.flow(worker=worker, verdicts=verdicts,
                         records=self.RECORDS)

    def test_each_train_and_member(self):
        got = self.flow_of().recent_trains()
        self.assertEqual([(t["prs"], t["outcome"]) for t in got], [
            ([7, 8], "running"), ([6], "re-cut"), ([6, 7], "re-cut"),
            ([4, 5], "partly merged"), ([1, 2, 3], "merged")])
        first = got[-1]
        self.assertEqual(first["carriages"], [[1, 2], [3]])
        self.assertEqual((first["cut"], first["end"], first["minutes"]),
                         ("2026-11-04T10:00Z", "2026-11-04T10:10Z", 10))
        self.assertEqual(first["why"], "all 3 merged in one train merge")
        second = got[-2]
        self.assertEqual({k: m["outcome"] for k, m in
                          second["members"].items()},
                         {"4": "merged", "5": "culprit"})
        self.assertEqual(second["members"]["4"]["why"], "merged on its own")
        self.assertIn("culprit #5", second["why"])
        self.assertEqual(second["tops"][0]["res"], "red")
        third = got[2]
        self.assertEqual(third["members"]["6"]["outcome"], "recut")
        self.assertIn("default branch moved", third["members"]["6"]["why"])
        self.assertIn("default branch moved", third["why"])
        self.assertEqual(got[1]["why"], "every member left")
        self.assertIn("conflicts with #9", got[1]["members"]["6"]["why"])
        self.assertEqual(got[0]["members"]["8"]["outcome"], "waiting")
        self.assertIsNone(got[0]["end"])
        self.assertEqual([t["prs"] for t in
                          self.flow_of().recent_trains(2)], [[7, 8], [6]])
        self.assertEqual(self.flow(records=RECORDS).recent_trains(), [])

    def test_the_page_tells_them(self):
        data = {"recent_trains": self.flow_of().recent_trains()}
        html = tool.recent_html(data)
        self.assertEqual(html.count('<div class="rtrain '), 5)
        # Each a train: an engine lit by its outcome, coupled carriages
        # with the top in the guard's van.
        self.assertEqual(html.count('<svg class="engine"'), 5)
        self.assertEqual(html.count('class="carriage van"'), 5)
        for cls, says in (("t-green", "merged: green lamp"),
                          ("t-partly", "partly merged: amber lamp"),
                          ("t-wait", "re-cut: lamp unlit"),
                          ("t-testing", "still running")):
            self.assertIn('<div class="rtrain %s">' % cls, html)
            self.assertIn('aria-label="engine, %s' % says, html)
        self.assertIn(".rtrain .engine .puff{animation:none", tool.css_vars())
        self.assertIn('<span class="ob o-partly-merged">partly merged'
                      "</span> cut 2026-11-04T10:00Z", html)
        self.assertIn("3 PRs in 2 carriages · 10 min to 10:10Z", html)
        self.assertIn('class="car m-culprit top" title="red on its own',
                      html)
        self.assertIn("top run red after 3 min", html)
        self.assertIn("so far", html)
        for kind in ("merged", "culprit", "left", "recut", "waiting"):
            self.assertIn('class="car m-%s lg"' % kind, html)
        self.assertIn("No train", tool.recent_html({}))
        page = tool.page(dict(tool.compute(self.flow_of(), "widget"),
                              repo=None))
        self.assertLess(page.index("Recent trains"),
                        page.index("Trains now"))


class Main(Base):

    def main(self, *argv, call=None, environ=None):
        self.out, self.err = [], []
        return tool.main(["--state-dir", self.root, "--out", self.dest,
                          *argv], self.out.append, self.err.append,
                         environ if environ is not None else {
                             "HOME": self.tmp.name,
                             "FORGE_CONFIG": "/nonexistent"},
                         NOW, call=call or (lambda *a, **k: 1 / 0),
                         token="tok")

    def setUp(self):
        super().setUp()
        self.dest = os.path.join(self.tmp.name, "out")
        write_state(self.root)

    def read(self, name):
        with open(os.path.join(self.dest, name)) as handle:
            return handle.read()

    def test_it_writes_the_page_the_json_and_the_history(self):
        self.assertEqual(self.main(), 0)
        page = self.read("index.html")
        self.assertTrue(page.startswith("<!DOCTYPE html>"))
        self.assertNotIn("<script", page)
        self.assertIn("prefers-color-scheme:dark", page)
        self.assertIn("1. Why so few merges", page)
        self.assertIn("7. Policy", page)
        self.assertIn("At a glance", page)
        self.assertIn("Builds per merge (all build runs &divide; PRs "
                      "merged)", page)
        self.assertIn("Builds per merged PR along its own path", page)
        self.assertIn("hand-backs told, all pull requests, &divide; merges",
                      page)
        self.assertIn("batch tops: green / red", page)
        self.assertIn('<span title="2 in 1.0 h">2.0 /h</span>', page)
        self.assertEqual(page.count("<svg"), 8)
        self.assertLess(len(page.encode()), 200 * 1024)
        data = json.loads(self.read("flow.json"))
        self.assertEqual(set(data["windows"]), {"1h", "6h", "24h", "7d"})
        self.assertEqual(data["windows"]["1h"]["merges"], 2)
        self.assertEqual(len(data["hours"]), 24)
        self.assertEqual(len(data["graph_hours"]), 24)
        self.assertEqual(len(data["build_times"]), 28)
        self.assertEqual(data["now"]["state"], tool.HELD)
        history = self.read("flow-history.jsonl").splitlines()
        self.assertEqual(json.loads(history[-1])["hour"], "2026-11-04T10:00Z")
        self.assertEqual(json.loads(history[-1])["merges"], 2)
        self.assertEqual(json.loads(history[-1])["runs_per_merged_pr"]
                         ["mean"], 2.0)
        self.assertIn("run_seconds_by_kind", json.loads(history[-1]))
        # A second run adds no hour twice.
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.read("flow-history.jsonl").splitlines(),
                         history)

    def test_no_records_is_exit_2(self):
        self.root = os.path.join(self.tmp.name, "empty")
        self.assertEqual(self.main(), 2)
        self.assertIn("NO-RECORDS", self.err[0])

    def test_without_a_state_dir_it_needs_the_configuration(self):
        self.out, self.err = [], []
        self.assertEqual(tool.main(["--out", self.dest], self.out.append,
                                   self.err.append, {
                                       "HOME": self.tmp.name,
                                       "FORGE_CONFIG": "/nonexistent"},
                                   NOW), 2)
        self.assertIn("NO-CONFIG", self.err[0])

    def test_github_gives_the_first_approval_once(self):
        config = os.path.join(self.tmp.name, "config.json")
        with open(config, "w") as handle:
            json.dump(example(), handle)
        asked = []

        def call(url, token, scheme=None, method=None, body=None):
            asked.append(body["query"])
            return 200, {"data": {"repository": {
                "p1": {"reviews": {"nodes": [
                    {"submittedAt": "2026-11-04T10:02:00Z"}]}},
                "p3": {"reviews": {"nodes": []}}}}}
        env = {"HOME": self.tmp.name, "FORGE_CONFIG": config}
        self.assertEqual(self.main("--github", call=call, environ=env), 0)
        data = json.loads(self.read("flow.json"))
        got = data["windows"]["1h"]["approval_to_merge"]["github_minutes"]
        self.assertEqual((got["n"], got["median"]), (1, 30))
        self.assertEqual(json.loads(self.read("approvals.json")),
                         {"1": "2026-11-04T10:02:00Z", "3": None})
        self.assertEqual(self.main("--github", call=call, environ=env), 0)
        self.assertEqual(len(asked), 1)
        self.assertIn("from GitHub's first approval",
                      self.read("index.html"))

    def test_github_gives_the_urgent_label(self):
        config = os.path.join(self.tmp.name, "config.json")
        conf = example()
        conf["labels"]["urgent"] = "Urgent"
        with open(config, "w") as handle:
            json.dump(conf, handle)
        asked = []

        def call(url, token, scheme=None, method=None, body=None):
            asked.append(url)
            if body is None:
                return 200, [{"number": 5, "pull_request": {}},
                             {"number": 6}]
            return 200, {"data": {"repository": {"p1": {}, "p3": {}}}}
        env = {"HOME": self.tmp.name, "FORGE_CONFIG": config}
        self.assertEqual(self.main("--github", call=call, environ=env), 0)
        self.assertTrue(any("issues?state=open&labels=Urgent" in u
                            for u in asked))
        data = json.loads(self.read("flow.json"))
        self.assertEqual(data["priority"], {"5": {
            "level": "urgent", "why": "urgent: carries the urgent label"}})
        self.assertEqual(self.main("--github", call=lambda *a, **k: (
            502, {}), environ=env), 0)
        self.assertIn("urgent labels inferred", self.err[-1])

    def test_a_github_failure_leaves_github_out(self):
        config = os.path.join(self.tmp.name, "config.json")
        with open(config, "w") as handle:
            json.dump(example(), handle)
        env = {"HOME": self.tmp.name, "FORGE_CONFIG": config}
        self.assertEqual(self.main("--github", call=lambda *a, **k: (
            502, {}), environ=env), 0)
        self.assertIn("GitHub's approvals left out", self.err[0])


if __name__ == "__main__":
    unittest.main()


class LegacyStateNames(unittest.TestCase):
    def test_history_reads_the_held_state_by_its_old_name(self):
        line = json.dumps({"hour": "2026-11-05T10:00Z",
                           "states": {"eligible but held": 7,
                                      tool.HELD: 2, tool.MERGING: 3}})
        got = tool.history_lines([line])["2026-11-05T10:00Z"]["states"]
        self.assertEqual(got[tool.HELD], 9)
        self.assertNotIn("eligible but held", got)
        self.assertEqual(got[tool.MERGING], 3)
