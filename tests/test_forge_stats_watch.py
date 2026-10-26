"""Tests for bin/forge-stats-watch: judging forge-stats' figures, and
the one issue per figure it opens, reminds on and closes."""
import datetime
import json
import os
import tempfile
import unittest

from agent_forge import github
from tests.helpers import EXAMPLE, example, load_tool

tool = load_tool("forge-stats-watch")
NOW = datetime.datetime(2026, 10, 21, 12, 0, tzinfo=datetime.timezone.utc)
HOUR = datetime.timedelta(hours=1)
WATCH = {"owner": "LeadDev", "figures": {
    "check_run": {"limit": 12, "ratio": 1.5},
    "check_wait": {"limit": 20, "ratio": 2},
    "pr_turnaround": {"limit": 20, "ratio": 1.5},
    "merge_time": {"limit": 45, "ratio": 2},
    "merge_rate": {"ratio": 2}, "slots_busy": {"limit": 90},
    "main_red": {}}}


def spread(minutes, n=10, p90=None):
    """forge-stats' {n, median, p90, max}, in seconds."""
    p90 = minutes if p90 is None else p90
    return {"n": n, "median": minutes * 60, "p90": p90 * 60,
            "max": p90 * 60}


def stats(ran=6, waited=2, pr=8, merge=20, merges=12, waiting=12,
          busy=0.5, main_red=0, main_runs=6, n=10):
    """forge-stats' JSON, with the figures the watch reads."""
    return {"check": {
        "ran": spread(ran, n), "waited": spread(waited, n, waited),
        "pull_request_seen_to_verdict": spread(pr, n),
        "busy_share": busy, "red": {"main": main_red} if main_red else {},
        "runs": {"main": main_runs}},
        "queue": {"queue_to_merge": spread(merge, n), "merges": merges,
                  "waiting": waiting}}


class FakeGitHub:
    """The issues API: open issues listed by label, issues opened,
    commented on and closed. `fail` names a method that answers 500."""

    def __init__(self, issues=(), fail=None):
        self.issues = [dict(i) for i in issues]
        self.fail = fail
        self.writes = []

    def __call__(self, url, token, scheme="Bearer", method="GET", body=None):
        assert token == "tok", token
        if method == self.fail:
            return 500, {"message": "down"}
        if method == "GET":
            assert "/repos/example/widget/issues?state=open&labels=" \
                   "stats-watch&" in url, url
            if not url.endswith("&page=1"):
                return 200, []
            return 200, [i for i in self.issues if i["state"] == "open"]
        self.writes.append((method, url.split("/repos/example/widget")[1],
                            body))
        if method == "POST" and url.endswith("/issues"):
            number = 100 + len(self.issues)
            self.issues.append({"number": number, "state": "open",
                                "body": body["body"],
                                "labels": body["labels"]})
            return 201, {"number": number}
        if method == "PATCH":
            number = int(url.rsplit("/", 1)[1])
            for issue in self.issues:
                if issue["number"] == number:
                    issue["state"] = body["state"]
            return 200, {}
        return 201, {}


class Judge(unittest.TestCase):

    def judge(self, figure, window, base=None, rules=None):
        rules = WATCH["figures"][figure] if rules is None else rules
        return tool.judge(figure, rules, window, base or (None, 0), 5)

    def test_each_time_figure_trips_on_its_limit(self):
        for figure, limit in (("check_run", 12), ("check_wait", 20),
                              ("pr_turnaround", 20), ("merge_time", 45)):
            with self.subTest(figure=figure):
                got = self.judge(figure, (limit + 1, 10), (limit, 100))
                self.assertEqual(got[0], tool.OUT)
                self.assertIn("over the limit", got[1])
                self.assertEqual(self.judge(figure, (limit, 10),
                                            (limit, 100))[0], tool.IN)

    def test_each_time_figure_trips_on_its_ratio(self):
        for figure, ratio in (("check_run", 1.5), ("check_wait", 2),
                              ("pr_turnaround", 1.5), ("merge_time", 2)):
            with self.subTest(figure=figure):
                got = self.judge(figure, (4 * ratio + 0.1, 10), (4, 100))
                self.assertEqual(got[0], tool.OUT)
                self.assertIn("x the baseline's 4.0 min", got[1])
                self.assertEqual(self.judge(figure, (4 * ratio, 10),
                                            (4, 100))[0], tool.IN)

    def test_slots_busy_trips_on_its_limit(self):
        self.assertEqual(self.judge("slots_busy", (91, 10))[0], tool.OUT)
        self.assertEqual(self.judge("slots_busy", (90, 10))[0], tool.IN)

    def test_merge_rate_trips_under_the_baseline_over_its_ratio(self):
        got = self.judge("merge_rate", (0.9, 6), (2, 300))
        self.assertEqual(got[0], tool.OUT)
        self.assertIn("under the baseline's 2.0 an hour over 2", got[1])
        self.assertEqual(self.judge("merge_rate", (1, 6), (2, 300))[0],
                         tool.IN)

    def test_merge_rate_trips_under_its_limit(self):
        # Seen on 2026-10-26: 5.2 an hour all afternoon was in line by
        # the ratio alone.
        rules = {"limit": 6, "ratio": 2}
        got = self.judge("merge_rate", (5.2, 6), (7, 300), rules)
        self.assertEqual(got, (tool.OUT, "5.2 an hour, under the limit "
                               "of 6.0 an hour"))
        self.assertEqual(self.judge("merge_rate", (6, 6), None,
                                    rules)[0], tool.IN)

    def test_merge_rate_with_nothing_waiting_is_not_judged(self):
        got = self.judge("merge_rate", (0, 0), (2, 300))
        self.assertEqual(got, (tool.UNJUDGED,
                               "no approved pull request waited"))

    def test_main_red_trips_on_any(self):
        self.assertEqual(self.judge("main_red", (1, 1))[0], tool.OUT)
        self.assertEqual(self.judge("main_red", (0, 0))[0], tool.IN)

    def test_nightly_red_hours(self):
        def judge(window, rules):
            return tool.judge("nightly_red_hours", rules, window,
                              (None, 0), 5)
        self.assertEqual(judge((30, 1), {"limit": 24}), (
            tool.OUT, "red for 30.0 h, over the limit of 24.0 h"))
        self.assertEqual(judge((20, 1), {"limit": 24}), (
            tool.IN, "red for 20.0 h"))
        self.assertEqual(judge((0.5, 1), {}), (tool.OUT, "red for 0.5 h"))
        self.assertEqual(judge((0, 1), {}), (tool.IN, "green"))
        self.assertEqual(judge((None, 0), {})[0], tool.UNJUDGED)
        got = tool.figures(dict(stats(), nightly={"red_hours": 30}), 6)
        self.assertEqual(got["nightly_red_hours"], (30, 1))
        self.assertEqual(tool.figures(stats(), 6)["nightly_red_hours"],
                         (None, 0))

    def test_too_few_samples_is_not_judged(self):
        for figure in ("check_run", "slots_busy", "merge_rate"):
            with self.subTest(figure=figure):
                got = self.judge(figure, (500, 4), (1, 100))
                self.assertEqual(got, (tool.UNJUDGED,
                                       "4 samples, fewer than 5"))

    def test_a_thin_baseline_is_no_ratio(self):
        # Over 1.5 x the baseline, but the baseline has 4 samples.
        self.assertEqual(self.judge("check_run", (10, 10), (4, 4))[0],
                         tool.IN)
        self.assertEqual(self.judge("check_run", (10, 10), (4, 4),
                                    {"ratio": 1.5})[0], tool.UNJUDGED)

    def test_figures_from_forge_stats_json(self):
        got = tool.figures(stats(ran=6, waited=3, busy=0.25, main_red=2),
                           6)
        self.assertEqual(got["check_run"], (6, 10))
        self.assertEqual(got["check_wait"], (3, 10))
        self.assertEqual(got["merge_rate"], (2, 12))
        self.assertEqual(got["slots_busy"], (25, 10))
        self.assertEqual(got["main_red"], (2, 6))
        self.assertEqual(tool.figures(None, 6)["check_run"], (None, 0))


class Main(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        data = example()
        data["stats_watch"] = WATCH
        self.config = os.path.join(self.dir.name, "config.json")
        with open(self.config, "w") as handle:
            json.dump(data, handle)
        self.env = {"FORGE_CONFIG": self.config, "HOME": self.dir.name,
                    "FORGE_STATE_DIR": self.dir.name}
        self.state = os.path.join(self.dir.name, "widget", "stats-watch",
                                  "state.json")
        self.gh = FakeGitHub()
        self.window = stats()
        self.base = stats()
        self.out, self.err, self.minted = [], [], []

    def stats(self, since, until):
        return self.window if until - since < datetime.timedelta(days=1) \
            else self.base

    def mint(self, project, name):
        self.minted.append(name)
        return "tok"

    def main(self, *argv, now=NOW, mint=None):
        return tool.main(list(argv), self.out.append, self.err.append,
                         self.env, now, call=self.gh, stats=self.stats,
                         mint=mint or self.mint)

    def saved(self):
        with open(self.state) as handle:
            return json.load(handle)

    def test_all_in_line_writes_nothing(self):
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.gh.writes, [])
        self.assertIn("IN check_run: 6.0 min", self.out)
        self.assertEqual(self.minted, ["Merger"])

    def test_out_of_line_opens_one_issue_for_the_owner(self):
        self.window = stats(ran=14)
        self.assertEqual(self.main(), 0)
        [(method, path, body)] = self.gh.writes
        self.assertEqual((method, path), ("POST", "/issues"))
        self.assertEqual(body["title"], "forge-stats-watch: check_run "
                         "14.0 min, over the limit of 12.0 min")
        self.assertEqual(body["labels"], ["stats-watch", "Owner:LeadDev"])
        self.assertIn("| `check_run` | 14.0 min (10 samples) | 6.0 min "
                      "(10 samples) |", body["body"])
        self.assertIn("forge-stats --since 2026-10-21T06:00Z --until "
                      "2026-10-21T12:00Z", body["body"])
        self.assertIn("\nFigure: check_run\n", body["body"])
        self.assertTrue(body["body"].endswith("— Merger"))
        self.assertEqual(self.saved()["figures"]["check_run"]["issue"], 100)
        # The next hour finds it open: no second issue, no reminder yet.
        self.assertEqual(self.main(now=NOW + HOUR), 0)
        self.assertEqual(len(self.gh.writes), 1)

    def test_a_figure_names_its_own_owner(self):
        with open(self.config) as handle:
            data = json.load(handle)
        data["stats_watch"]["figures"]["check_run"]["owner"] = "Reviewer"
        with open(self.config, "w") as handle:
            json.dump(data, handle)
        self.window = stats(ran=14)
        self.main()
        [(_, _, body)] = self.gh.writes
        self.assertEqual(body["labels"], ["stats-watch", "Owner:Reviewer"])
        self.assertIn("is Reviewer's to decide", body["body"])

    def test_it_files_as_the_app_named_by_as(self):
        with open(self.config) as handle:
            data = json.load(handle)
        data["stats_watch"]["as"] = "Reviewer"
        with open(self.config, "w") as handle:
            json.dump(data, handle)
        self.window = stats(ran=14)
        self.main()
        self.assertEqual(self.minted, ["Reviewer"])
        self.assertTrue(self.gh.writes[0][2]["body"].endswith("— Reviewer"))

    def test_it_reminds_every_remind_hours(self):
        self.window = stats(ran=14)
        self.main()
        for hours in range(1, 6):
            self.main(now=NOW + hours * HOUR)
        self.assertEqual(len(self.gh.writes), 1)
        self.window = stats(ran=15)
        self.main(now=NOW + 6 * HOUR)
        method, path, body = self.gh.writes[-1]
        self.assertEqual((method, path), ("POST", "/issues/100/comments"))
        self.assertIn("Still out of line: 15.0 min, over the limit",
                      body["body"])
        self.main(now=NOW + 7 * HOUR)
        self.assertEqual(len(self.gh.writes), 2)

    def test_it_closes_after_a_whole_clean_window(self):
        self.window = stats(ran=14)
        self.main()
        self.window = stats()
        for hours in range(1, 7):
            self.main(now=NOW + hours * HOUR)
        self.assertEqual(len(self.gh.writes), 1)
        self.assertIn("IN LINE #100 check_run since 2026-10-21T13:00:00Z; "
                      "closes after 6 h", self.out)
        self.main(now=NOW + 7 * HOUR)
        (_, path, comment), (method, shut, body) = self.gh.writes[1:]
        self.assertEqual(path, "/issues/100/comments")
        self.assertIn("Back in line for a whole window (6 h", comment["body"])
        self.assertEqual((method, shut, body["state"]),
                         ("PATCH", "/issues/100", "closed"))
        self.assertEqual(self.saved()["figures"], {})

    def test_out_again_restarts_the_clean_window(self):
        self.window = stats(ran=14)
        self.main()
        self.window = stats()
        self.main(now=NOW + 3 * HOUR)
        self.window = stats(ran=14)
        self.main(now=NOW + 4 * HOUR)
        self.window = stats()
        self.main(now=NOW + 5 * HOUR)
        self.main(now=NOW + 10 * HOUR)
        self.assertEqual([w[0] for w in self.gh.writes], ["POST"])

    def test_not_judged_does_not_count_as_clean(self):
        self.window = stats(ran=14)
        self.main()
        self.window = stats(n=2, waiting=0)
        self.main(now=NOW + HOUR)
        self.main(now=NOW + 8 * HOUR)
        self.assertEqual(len(self.gh.writes), 1)
        self.assertNotIn("clear_since",
                         self.saved()["figures"]["check_run"])

    def test_too_few_samples_files_nothing(self):
        self.window = stats(ran=60, waited=60, pr=60, merge=200, busy=1,
                            merges=0, waiting=4, n=4)
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.gh.writes, [])
        self.assertIn("UNJUDGED merge_rate: 4 samples, fewer than 5",
                      self.out)

    def test_merge_rate_with_nothing_waiting_files_nothing(self):
        self.window = stats(merges=0, waiting=0)
        self.main()
        self.assertEqual(self.gh.writes, [])
        self.window = stats(merges=0, waiting=8)
        self.main()
        self.assertEqual(self.gh.writes[0][2]["title"],
                         "forge-stats-watch: merge_rate 0.0 an hour, under "
                         "the baseline's 0.1 an hour over 2")

    def test_it_finds_an_issue_an_earlier_run_filed(self):
        self.gh = FakeGitHub([
            {"number": 7, "state": "open", "updated_at":
             "2026-10-21T10:00:00Z",
             "body": "old\n\nFigure: check_run\n\n— Merger"},
            {"number": 8, "state": "open", "pull_request": {},
             "body": "Figure: slots_busy"}])
        self.window = stats(ran=14, busy=0.95)
        self.main()
        # check_run's is found and not yet due a reminder; slots_busy's
        # "issue" was a pull request, so it gets its own.
        [(method, path, body)] = self.gh.writes
        self.assertEqual((method, path), ("POST", "/issues"))
        self.assertIn("slots_busy", body["title"])
        self.assertEqual(self.saved()["figures"]["check_run"]["issue"], 7)
        self.main(now=NOW + 4 * HOUR)
        self.assertEqual(self.gh.writes[-1][1], "/issues/7/comments")

    def test_an_issue_closed_by_hand_is_forgotten(self):
        self.window = stats(ran=14)
        self.main()
        self.gh.issues[0]["state"] = "closed"
        self.main(now=NOW + HOUR)
        self.assertEqual([w[1] for w in self.gh.writes],
                         ["/issues", "/issues"])
        self.assertEqual(self.saved()["figures"]["check_run"]["issue"], 101)

    def test_a_github_failure_skips_the_hour(self):
        self.gh = FakeGitHub(fail="GET")
        self.window = stats(ran=14)
        self.assertEqual(self.main(), 2)
        self.assertEqual(self.gh.writes, [])
        self.assertIn("skipping this hour: GitHub answered 500",
                      self.err[0])
        self.assertFalse(os.path.exists(self.state))

    def test_a_token_that_does_not_mint_skips_the_hour(self):
        def mint(project, name):
            raise github.MintError(github.UNREACHABLE, "no network")
        self.window = stats(ran=14)
        self.assertEqual(self.main(mint=mint), 2)
        self.assertIn("skipping this hour: no network", self.err[0])

    def test_a_refused_write_is_tried_again_next_hour(self):
        self.gh = FakeGitHub(fail="POST")
        self.window = stats(ran=14)
        self.assertEqual(self.main(), 2)
        self.assertIn("POST /repos/example/widget/issues refused: HTTP 500",
                      self.err[0])
        self.gh.fail = None
        self.assertEqual(self.main(now=NOW + HOUR), 0)
        self.assertEqual(len(self.gh.writes), 1)

    def test_dry_run_changes_nothing(self):
        self.window = stats(ran=14, main_red=1)
        self.assertEqual(self.main("--dry-run"), 0)
        self.assertEqual(self.gh.writes, [])
        self.assertFalse(os.path.exists(self.state))
        self.assertIn("WOULD OPEN check_run: forge-stats-watch: check_run "
                      "14.0 min, over the limit of 12.0 min, for LeadDev",
                      self.out)
        self.assertTrue(any(line.startswith("WOULD OPEN main_red")
                            for line in self.out))

    def test_dry_run_says_it_would_remind_and_close(self):
        self.window = stats(ran=14, busy=0.95)
        self.main()
        self.window = stats(ran=14)
        for hours in range(1, 7):
            self.main(now=NOW + hours * HOUR)
        writes = len(self.gh.writes)
        self.window = stats(ran=14)
        self.main("--dry-run", now=NOW + 12 * HOUR)
        self.assertIn("WOULD REMIND #100 check_run: 14.0 min, over the "
                      "limit of 12.0 min", self.out)
        self.assertIn("WOULD CLOSE #101 slots_busy: back in line since "
                      "2026-10-21T13:00:00Z", self.out)
        self.assertEqual(len(self.gh.writes), writes)

    def test_a_figure_left_out_is_not_watched(self):
        with open(self.config) as handle:
            data = json.load(handle)
        data["stats_watch"]["figures"] = {"main_red": {}}
        with open(self.config, "w") as handle:
            json.dump(data, handle)
        self.window = stats(ran=60)
        self.main()
        self.assertEqual(self.gh.writes, [])
        self.assertEqual(self.out, ["IN main_red: no red build checks of "
                                    "the default branch"])

    def test_without_the_section_it_does_nothing(self):
        self.env["FORGE_CONFIG"] = str(EXAMPLE)
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.minted, [])
        self.assertIn("no stats_watch section", self.out[0])


class ForgeStats(unittest.TestCase):
    """The figures come from forge-stats itself, run in this process."""

    def test_its_json_for_the_window(self):
        with tempfile.TemporaryDirectory() as root:
            os.makedirs(os.path.join(root, "widget", "pr-check"))
            with open(os.path.join(root, "widget", "pr-check",
                                   "verdicts.log"), "w") as handle:
                handle.write("2026-10-21T11:00:00Z VERDICT #2 bbbbbbbbbb: "
                             "green waited=60s ran=300s\n")
            env = {"FORGE_CONFIG": str(EXAMPLE), "FORGE_STATE_DIR": root,
                   "HOME": root}
            stats = tool.forge_stats(env)
            got = stats(NOW - 6 * HOUR, NOW)
            self.assertEqual(got["check"]["ran"]["median"], 300)
            self.assertEqual(tool.figures(got, 6)["check_run"], (5, 1))
            self.assertEqual(got["queue"]["waiting"], 0)


if __name__ == "__main__":
    unittest.main()
