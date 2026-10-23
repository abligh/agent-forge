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

    def test_the_approved_pull_requests_that_waited_in_the_window(self):
        # #7 and #8 were queued in the window; #9 only after it.
        self.assertEqual(self.got["waiting"], 2)
        self.assertEqual(tool.queue_stats(RECORDS[:1], SINCE, UNTIL)
                         ["waiting"], 0)

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


def job(start, end, status="completed", attempt=1, name="test",
        labels=("ubuntu-latest",)):
    return {"status": status, "started_at": start, "completed_at": end,
            "run_attempt": attempt, "name": name, "labels": list(labels)}


class JobMinutes(unittest.TestCase):
    """GitHub bills each job's run time rounded up to a whole minute."""

    def test_an_exact_minute_is_one(self):
        self.assertEqual(tool.job_minutes(
            job("2026-10-12T00:00:00Z", "2026-10-12T00:01:00Z")), 1)

    def test_a_second_over_rounds_up(self):
        self.assertEqual(tool.job_minutes(
            job("2026-10-12T00:00:00Z", "2026-10-12T00:01:01Z")), 2)
        self.assertEqual(tool.job_minutes(
            job("2026-10-12T00:00:00Z", "2026-10-12T00:00:01Z")), 1)

    def test_a_job_that_never_started_is_zero(self):
        self.assertEqual(tool.job_minutes(
            job(None, "2026-10-12T00:01:00Z")), 0)
        # A skipped job: started and completed in the same instant.
        self.assertEqual(tool.job_minutes(
            job("2026-10-12T00:00:00Z", "2026-10-12T00:00:00Z")), 0)

    def test_a_job_that_ends_before_it_begins_is_zero(self):
        self.assertEqual(tool.job_minutes(
            job("2026-10-12T00:02:00Z", "2026-10-12T00:00:30Z")), 0)

    def test_a_job_still_running_is_none(self):
        self.assertIsNone(tool.job_minutes(
            job("2026-10-12T00:00:00Z", None, status="in_progress")))
        self.assertIsNone(tool.job_minutes(
            job(None, None, status="queued")))


RUNS = [
    {"id": 1, "name": "ci", "event": "pull_request", "conclusion": "success",
     "triggering_actor": {"login": "leaddevwidgetagent[bot]"}},
    {"id": 2, "name": "ci", "event": "push", "conclusion": "cancelled",
     "actor": {"login": "someone"}},
    {"id": 3, "name": "docs", "event": "schedule", "conclusion": None},
]
JOBS = {
    1: [job("2026-10-12T00:00:00Z", "2026-10-12T00:04:30Z"),
        job("2026-10-12T00:00:00Z", "2026-10-12T00:01:00Z", name="lint"),
        job("2026-10-12T00:10:00Z", "2026-10-12T00:12:00Z", attempt=2)],
    2: [job("2026-10-12T00:00:00Z", "2026-10-12T00:00:10Z"),
        job("2026-10-12T00:00:00Z", "2026-10-12T00:30:00Z",
            labels=("self-hosted", "linux"))],
    3: [job("2026-10-12T00:00:00Z", None, status="in_progress")],
}


class Actions(unittest.TestCase):

    def setUp(self):
        self.got = tool.actions_stats(RUNS, JOBS)

    def test_totals(self):
        self.assertEqual((self.got["runs"], self.got["jobs"],
                          self.got["minutes"], self.got["still_running"],
                          self.got["self_hosted"]), (3, 4, 9, 1, 1))

    def test_breakdowns(self):
        by = self.got["by"]
        self.assertEqual(by["workflow"], {"ci": 9})
        self.assertEqual(by["job"], {"ci / test": 8, "ci / lint": 1})
        self.assertEqual(by["event"], {"pull_request": 8, "push": 1})
        self.assertEqual(by["conclusion"], {"success": 8, "cancelled": 1})
        self.assertEqual(by["attempt"], {"first": 7, "rerun": 2})
        # The triggering actor, else the run's actor.
        self.assertEqual(by["triggered_by"],
                         {"leaddevwidgetagent[bot]": 8, "someone": 1})

    def test_by_runner_os_and_weighted(self):
        jobs = {1: [job("2026-10-12T00:00:00Z", "2026-10-12T00:03:00Z",
                        labels=("macos-14",)),
                    job("2026-10-12T00:00:00Z", "2026-10-12T00:02:00Z",
                        labels=("Windows-latest",)),
                    job("2026-10-12T00:00:00Z", "2026-10-12T00:01:00Z")]}
        got = tool.actions_stats(RUNS[:1], jobs)
        self.assertEqual(got["by"]["runner_os"],
                         {"macOS": 3, "Windows": 2, "Linux": 1})
        self.assertEqual(got["minutes"], 6)
        self.assertEqual(got["weighted"], 3 * 10 + 2 * 2 + 1)

    def test_a_public_repository_has_no_weighted_total(self):
        got = tool.actions_stats(RUNS, JOBS, private=False)
        self.assertIsNone(got["weighted"])
        self.assertNotIn("weighted", "\n".join(tool.actions_text(got)))

    def test_the_multipliers_are_githubs(self):
        self.assertEqual(tool.RUNNER_MULTIPLIERS,
                         {"Linux": 1, "Windows": 2, "macOS": 10})

    def test_a_run_without_jobs_counts_as_a_run(self):
        got = tool.actions_stats(RUNS[:1], {})
        self.assertEqual((got["runs"], got["jobs"], got["minutes"]),
                         (1, 0, 0))

    def test_the_text(self):
        text = "\n".join(tool.actions_text(self.got, top=1))
        self.assertIn("9 min in 4 jobs of 3 runs; 1 still running, left "
                      "out; 1 self-hosted, not billed", text)
        self.assertIn("by job: ci / test 8; 1 more", text)
        self.assertIn("by attempt: first 7; 1 more", text)
        self.assertIn("by runner OS: Linux 9", text)
        self.assertIn("weighted for a private repository's billing "
                      "(Windows x2, macOS x10): 9 min", text)


class FakeActions:
    """GitHub's runs and jobs endpoints over `runs` (each with `created`)
    and `jobs` ({run id: [job]}), paging at `per_page`."""

    def __init__(self, runs, jobs, per_page=100, refuse=None, private=True):
        self.runs, self.jobs, self.per_page = runs, jobs, per_page
        self.refuse, self.urls, self.private = refuse, [], private

    def __call__(self, url, token, scheme=None, method=None, body=None):
        import urllib.parse
        self.urls.append(url)
        parsed = urllib.parse.urlparse(url)
        if parsed.path.endswith("/graphql"):
            # Approval to merge: the merged pull requests are not asked of.
            return 200, {"data": {"repository": {"none": None}}}
        if parsed.path.count("/") == 3:
            if self.refuse == "repo":
                return 404, {"message": "Not Found"}
            return 200, {"private": self.private}
        query = dict(urllib.parse.parse_qsl(parsed.query))
        page = int(query["page"])
        if self.refuse and self.refuse in parsed.path:
            return 403, {"message": "no"}
        if parsed.path.endswith("/jobs"):
            self.assertion = query.get("filter")
            items = self.jobs.get(int(parsed.path.split("/")[-2]), [])
            key = "jobs"
        else:
            lo, hi = query["created"].split("..")
            items = [r for r in self.runs if lo <= r["created"] <= hi]
            key = "workflow_runs"
            # GitHub answers no more than 1000 runs to one filtered query.
            total, items = len(items), items[:1000]
            start = (page - 1) * 100
            return 200, {"total_count": total,
                         key: items[start:start + 100]}
        start = (page - 1) * 100
        return 200, {"total_count": len(items),
                     key: items[start:start + 100]}


def runs_at(n, start=SINCE, every=datetime.timedelta(seconds=5)):
    return [{"id": i, "name": "ci",
             "created": tool.iso(start + every * i)} for i in range(n)]


class Fetch(unittest.TestCase):

    def test_runs_are_paged(self):
        fake = FakeActions(runs_at(250), {})
        got = tool.fetch_runs("tok", "example/widget", SINCE, UNTIL, fake)
        self.assertEqual(len(got), 250)
        self.assertEqual(len(fake.urls), 3)
        self.assertIn("created=2026-10-12T00:00:00Z..2026-10-12T01:59:59Z",
                      fake.urls[0])

    def test_a_window_at_the_cap_is_split(self):
        fake = FakeActions(runs_at(1200), {})
        got = tool.fetch_runs("tok", "example/widget", SINCE, UNTIL, fake)
        self.assertEqual(sorted(r["id"] for r in got), list(range(1200)))

    def test_a_window_of_exactly_the_cap_is_split(self):
        fake = FakeActions(runs_at(1000), {})
        got = tool.fetch_runs("tok", "example/widget", SINCE, UNTIL, fake)
        self.assertEqual(len(got), 1000)
        self.assertNotIn("page=11", " ".join(fake.urls))

    def test_the_window_end_is_left_out(self):
        fake = FakeActions([{"id": 1, "created": tool.iso(UNTIL)}], {})
        self.assertEqual(tool.fetch_runs("tok", "o/r", SINCE, UNTIL, fake),
                         [])

    def test_jobs_of_every_attempt_are_paged(self):
        fake = FakeActions([], {7: [job(None, None)] * 130})
        got = tool.fetch_jobs("tok", "example/widget", 7, fake)
        self.assertEqual(len(got), 130)
        self.assertEqual(fake.assertion, "all")

    def test_whether_the_repository_is_private(self):
        self.assertTrue(tool.fetch_private("tok", "o/r", FakeActions([], {})))
        self.assertFalse(tool.fetch_private(
            "tok", "o/r", FakeActions([], {}, private=False)))
        with self.assertRaises(RuntimeError):
            tool.fetch_private("tok", "o/r",
                               FakeActions([], {}, refuse="repo"))

    def test_a_refusal_is_an_error(self):
        with self.assertRaises(RuntimeError):
            tool.fetch_runs("tok", "o/r", SINCE, UNTIL,
                            FakeActions([], {}, refuse="/runs"))
        with self.assertRaises(RuntimeError):
            tool.fetch_jobs("tok", "o/r", 7,
                            FakeActions([], {}, refuse="/jobs"))


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

    def test_nightly_red_hours_from_forge_nightlys_state(self):
        self.write()
        data = json.loads(EXAMPLE.read_text())
        data["router"] = {"owners": {"triage": "LeadDev"}}
        data["nightly"] = {"workflow": "ci.yaml", "job": "build"}
        path = os.path.join(self.dir.name, "config.json")
        with open(path, "w") as handle:
            json.dump(data, handle)
        self.env["FORGE_CONFIG"] = path
        self.main("--no-github", "--json")
        self.assertEqual(json.loads(self.out[-1])["nightly"],
                         {"red_hours": None})
        os.makedirs(os.path.join(self.dir.name, "widget", "nightly"))
        state = os.path.join(self.dir.name, "widget", "nightly",
                             "nightly.json")
        with open(state, "w") as handle:
            json.dump({"newest": "red",
                       "red_since": "2026-10-11T14:30:00Z"}, handle)
        self.main("--no-github", "--json")
        self.assertEqual(json.loads(self.out[-1])["nightly"],
                         {"red_hours": 11.5})
        self.main("--no-github")
        self.assertIn("Nightly\n  red for 11.5 h since the last green one "
                      "ended", self.out[-1])
        with open(state, "w") as handle:
            json.dump({"newest": "green"}, handle)
        self.main("--no-github")
        self.assertIn("Nightly\n  green", self.out[-1])

    def test_no_nightly_without_the_section(self):
        self.write()
        self.main("--no-github", "--json")
        self.assertIsNone(json.loads(self.out[-1])["nightly"])
        self.main("--no-github")
        self.assertNotIn("Nightly", self.out[-1])

    def fake(self):
        runs = [dict(r, created="2026-10-12T00:30:00Z") for r in RUNS]
        return FakeActions(runs, JOBS)

    def test_actions(self):
        self.write()
        self.assertEqual(tool.main(
            ["--since", "2026-10-12T00:00Z", "--until", "2026-10-12T02:00Z",
             "--actions"], self.out.append, self.env, call=self.fake(),
            token="tok"), 0)
        self.assertIn("9 min in 4 jobs of 3 runs", self.out[0])
        self.assertIn("by workflow: ci 9", self.out[0])

    def test_a_public_repository_reports_no_weighted_total(self):
        runs = [dict(r, created="2026-10-12T00:30:00Z") for r in RUNS]
        self.assertEqual(tool.main(
            ["--since", "2026-10-12T00:00Z", "--until", "2026-10-12T02:00Z",
             "--actions", "--json"], self.out.append, self.env,
            call=FakeActions(runs, JOBS, private=False), token="tok"), 0)
        got = json.loads(self.out[0])["actions"]
        self.assertEqual((got["minutes"], got["weighted"]), (9, None))

    def test_actions_needs_no_local_records(self):
        self.assertEqual(tool.main(
            ["--since", "2026-10-12T00:00Z", "--until", "2026-10-12T02:00Z",
             "--actions", "--json"], self.out.append, self.env,
            call=self.fake(), token="tok"), 0)
        self.assertEqual(json.loads(self.out[0])["actions"]["minutes"], 9)

    def test_no_github_leaves_actions_out(self):
        self.write()
        fake = self.fake()
        self.assertEqual(tool.main(
            ["--since", "2026-10-12T00:00Z", "--until", "2026-10-12T02:00Z",
             "--no-github", "--actions"], self.out.append, self.env,
            call=fake, token="tok"), 0)
        self.assertNotIn("Actions", self.out[0])
        self.assertEqual(fake.urls, [])

    def test_without_the_flag_actions_are_not_fetched(self):
        self.write()
        fake = FakeActions([], {})
        self.assertEqual(tool.main(
            ["--since", "2026-10-12T00:00Z", "--until", "2026-10-12T02:00Z",
             "--json"], self.out.append, self.env, call=fake, token="tok"), 0)
        self.assertIsNone(json.loads(self.out[0])["actions"])
        self.assertFalse(any("/actions/" in u for u in fake.urls))

    def test_json(self):
        self.write()
        self.assertEqual(self.main("--no-github", "--json"), 0)
        got = json.loads(self.out[0])
        self.assertEqual(got["queue"]["merged"], [7, 8])
        self.assertEqual(got["queue"]["waiting"], 2)
        self.assertEqual(got["since"], "2026-10-12T00:00Z")


if __name__ == "__main__":
    unittest.main()
