"""Tests for designs/0019: `checks.step_alert` opens an issue when a
build-check step gets slower, and closes it when the step is back."""

import datetime
import unittest

from agent_forge import config
from tests.helpers import example

from tests.test_forge_pr_check import FakeRun, StateCase, done, pc

NOW = datetime.datetime(2026, 10, 18, 12, 0, tzinfo=datetime.timezone.utc)
HOUR = datetime.timedelta(hours=1)
DAY = datetime.timedelta(days=1)


def configured(**alert):
    data = example()
    data["checks"]["step_alert"] = alert
    pc.configure(config.Project(data, None))


def sha_of(n):
    return ("%040x" % n)


class Writer:
    """A stand-in for make_writer's write: records each call and answers
    from `answers`, by method, else success."""

    def __init__(self, **answers):
        self.calls = []
        self.answers = answers
        self.next_issue = 100

    def __call__(self, method, path, body=None):
        self.calls.append((method, path, body))
        if method in self.answers:
            return self.answers[method]
        if method == "POST" and path.endswith("/issues"):
            self.next_issue += 1
            return 201, {"number": self.next_issue}
        if method == "POST":
            return 201, {}
        if method == "GET":
            return 200, {"state": "open"}
        return 200, {}

    def opened(self):
        return [body for method, path, body in self.calls
                if method == "POST" and path.endswith("/issues")]


class StepAlertConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def problems(self, alert):
        data = example()
        data["checks"]["step_alert"] = alert
        return config.check(data)

    def test_good_and_bad_settings(self):
        self.assertEqual(self.problems({}), [])
        self.assertEqual(self.problems({
            "growth": 1.5, "min_seconds": 30, "window_days": 7,
            "min_runs": 10, "labels": ["bug"], "owner": "LeadDev"}), [])
        self.assertEqual(self.problems({"growth": 1}),
                         ["checks.step_alert.growth must be above 1"])
        self.assertEqual(self.problems({"window_days": 21}),
                         ["checks.step_alert.window_days must be 1 to 20"])
        self.assertEqual(self.problems({"min_runs": 0}),
                         ["checks.step_alert.min_runs must be at least 1"])
        self.assertEqual(self.problems({"owner": "Nobody"}),
                         ["checks.step_alert.owner 'Nobody' is not an agent"])
        self.assertIn("unknown key 'budget'", self.problems(
            {"budget": 3})[0])
        self.assertEqual(len(self.problems({"labels": [""]})), 1)

    def test_off_unless_configured_and_defaults_filled_in(self):
        self.assertIsNone(pc.STEP_ALERT)
        configured(owner="LeadDev")
        self.assertEqual(pc.STEP_ALERT["growth"], 1.5)
        self.assertEqual(pc.STEP_ALERT["min_seconds"], 30)
        self.assertEqual(pc.STEP_ALERT["window_days"], 7)
        self.assertEqual(pc.STEP_ALERT["min_runs"], 10)
        self.assertEqual(pc.STEP_ALERT["owner_label"], "Owner:LeadDev")
        configured()
        self.assertIsNone(pc.STEP_ALERT["owner_label"])


class StepAlert(StateCase):

    def setUp(self):
        super().setUp()
        configured(labels=["bug"], owner="LeadDev", min_runs=3)
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.results = {}
        self.n = 0

    def verdict(self, at, main=False, **steps):
        self.n += 1
        record = {"pr": 0 if main else 7, "ok": True,
                  "finished": pc.iso(at), "step_seconds": steps}
        if main:
            record["main"] = True
        self.results[sha_of(self.n)] = record
        return sha_of(self.n)

    def history(self, usual, recent, steps=("test",), main_from=None):
        """Three runs a day of each step over the eight days before NOW:
        `usual` seconds until the last day, `recent` in it. Runs from
        `main_from` back are the default branch's."""
        for k in range(24):
            at = NOW - DAY * 8 + HOUR * 8 * k + HOUR
            took = recent if at > NOW - DAY else usual
            self.verdict(at, main=main_from is not None and k >= main_from,
                         **{name: took for name in steps})

    def alert(self, writer, when=NOW, run=None):
        self.write_state({"results": self.results, "waiting": {}})
        return pc.alert_steps(self.state_dir, "/repo", writer,
                              self.lines.append, run or FakeRun(), when)

    def slow_day(self, until):
        """Three slow runs in the day before `until`, saved."""
        for k in range(3):
            self.verdict(until - HOUR * (k * 3), test=300)
        self.save_results()

    def save_results(self):
        state = self.state()
        state["results"] = self.results
        self.write_state(state)

    def test_a_slower_step_opens_one_issue(self):
        self.history(60, 300, main_from=0)
        run = FakeRun((lambda a: a[3:4] == ["log"],
                       done([], 0, "abc1234 Compare the whole corpus (#41)\n")))
        writer = Writer()
        self.assertEqual(self.alert(writer, run=run), 0)
        (issue,), = [writer.opened()]
        self.assertEqual(issue["title"],
                         "Build check step test got slower on build.example.org: "
                         "300s, was 60s")
        self.assertEqual(issue["labels"], ["bug", "Owner:LeadDev"])
        self.assertIn("its median over the last day is **300s** (3 runs), "
                      "against **60s** over the 7 days before (21 runs): "
                      "5.0 times as long", issue["body"])
        self.assertIn("* abc1234 Compare the whole corpus (#41)",
                      issue["body"])
        # The last default-branch run under the limit, then the first over.
        self.assertIn("under the limit (90s) at %s (60s) and over it from "
                      "%s (300s)" % (sha_of(21)[:10], sha_of(22)[:10]),
                      issue["body"])
        self.assertEqual(run.calls[0]["args"][-1], "%s..%s" % (sha_of(21), sha_of(22)))
        self.assertEqual(self.state()["step_alert"]["issues"],
                         {"test": {"issue": 101, "told": pc.iso(NOW)}})
        self.assertIn("FILED #101: Build check step test got slower",
                      self.lines[0])

    def test_nothing_again_without_a_new_verdict(self):
        self.history(60, 300)
        self.alert(Writer())
        writer = Writer()
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW + DAY * 2)
        self.assertEqual(writer.calls, [])

    def test_a_small_step_needs_min_seconds_more(self):
        self.history(4, 7)
        writer = Writer()
        self.alert(writer)
        self.assertEqual(writer.calls, [])
        self.assertEqual(self.state()["step_alert"]["issues"], {})

    def test_too_few_runs_judge_nothing(self):
        configured(min_runs=30)
        self.history(60, 300)
        writer = Writer()
        self.alert(writer)
        self.assertEqual(writer.calls, [])

    def test_matched_reused_and_failed_runs_are_not_its_time(self):
        self.history(60, 60)
        for k in range(5):
            at = NOW - HOUR * (k + 1)
            sha = self.verdict(at, test=900)
            self.results[sha]["matched"] = sha_of(1)
            sha = self.verdict(at, test=900)
            self.results[sha]["reused"] = {"test": {"reused": sha_of(1)}}
            sha = self.verdict(at, test=900)
            self.results[sha]["failed"] = ["test"]
            self.results[sha]["ok"] = False
        writer = Writer()
        self.alert(writer)
        self.assertEqual(writer.opened(), [])

    def test_back_under_comments_and_closes(self):
        self.history(60, 300)
        self.alert(Writer())
        for k in range(10):
            self.verdict(NOW + HOUR * k, test=60)
        writer = Writer()
        self.save_results()
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW + DAY)
        comment, close = writer.calls
        self.assertEqual(comment[1], "/repos/%s/issues/101/comments"
                         % pc.REPO_SLUG)
        self.assertIn("Back under the limit", comment[2]["body"])
        self.assertEqual(close[0:3], ("PATCH", "/repos/%s/issues/101"
                                      % pc.REPO_SLUG, {"state": "closed"}))
        self.assertEqual(self.state()["step_alert"]["issues"], {})

    def test_still_slow_comments_at_most_once_a_day(self):
        self.history(60, 300)
        self.alert(Writer())
        self.verdict(NOW + HOUR, test=300)
        self.save_results()
        writer = Writer()
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW + HOUR)
        self.assertEqual(writer.calls, [])
        self.slow_day(NOW + DAY)
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW + DAY)
        self.assertEqual([c[0] for c in writer.calls], ["GET", "POST"])
        self.assertIn("Still slow", writer.calls[1][2]["body"])

    def test_closed_by_hand_stays_closed_until_the_step_is_back(self):
        self.history(60, 300)
        self.alert(Writer())
        self.slow_day(NOW + DAY)
        writer = Writer(GET=(200, {"state": "closed"}))
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW + DAY)
        self.assertEqual([c[0] for c in writer.calls], ["GET"])
        self.assertTrue(self.state()["step_alert"]["issues"]["test"][
            "dismissed"])
        self.slow_day(NOW + DAY * 2)
        writer = Writer()
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW + DAY * 2)
        self.assertEqual(writer.calls, [])
        for k in range(10):
            self.verdict(NOW + DAY * 2 + HOUR * k, test=60)
        self.save_results()
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW + DAY * 3)
        self.assertEqual(writer.calls, [])
        self.assertEqual(self.state()["step_alert"]["issues"], {})

    def test_most_steps_slow_at_once_is_one_issue_about_the_host(self):
        self.history(60, 300, steps=("a", "b", "c"))
        writer = Writer()
        self.alert(writer)
        (issue,) = writer.opened()
        self.assertEqual(issue["title"],
                         "The build check on build.example.org got slower at "
                         "every step")
        self.assertIn("3 of the 3 with enough runs", issue["body"])
        self.assertIn("| a (slow) | 300s | 60s | 5.0 |", issue["body"])
        self.assertEqual(list(self.state()["step_alert"]["issues"]),
                         [pc.STEP_ALERT_HOST])

    def test_one_slow_step_of_three_is_that_step(self):
        self.history(60, 60, steps=("a", "b"))
        self.history(60, 300, steps=("c",))
        writer = Writer()
        self.alert(writer)
        (issue,) = writer.opened()
        self.assertTrue(issue["title"].startswith("Build check step c "))

    def test_a_failed_write_is_tried_again(self):
        self.history(60, 300)
        writer = Writer(POST=(502, {"message": "Bad gateway"}))
        self.assertEqual(self.alert(writer), 1)
        self.assertNotIn("checked", self.state()["step_alert"])
        self.assertIn("REPORT-FAILED step alert: opening an issue for test",
                      self.lines[0])
        writer = Writer()
        pc.alert_steps(self.state_dir, "/repo", writer, self.lines.append,
                       FakeRun(), NOW)
        self.assertEqual(len(writer.opened()), 1)


if __name__ == "__main__":
    unittest.main()
