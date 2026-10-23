"""Tests for bin/forge-nightly: the one issue while the nightly is red,
the fixes it hands the other tools, its retries and its escalation."""
import base64
import datetime
import json
import os
import tempfile
import unittest

from tests.helpers import example, load_tool

tool = load_tool("forge-nightly")
NOW = datetime.datetime(2026, 10, 23, 12, 0, tzinfo=datetime.timezone.utc)
HOUR = datetime.timedelta(hours=1)
GREEN_SHA, RED_SHA, NEXT_SHA = "a" * 40, "b" * 40, "c" * 40
NIGHTLY = {"workflow": "ci.yaml", "job": "build-and-test",
           "owners": {"test-slow": "UIDev", "vet-*": "Helper"},
           "labels": ["bug"]}
LOG = "\n".join([
    "2026-10-23T09:00:00.1000000Z ##[group]Run make build",
    "2026-10-23T09:00:01.0000000Z built",
    "2026-10-23T09:10:00.0000000Z ##[group]Run make test-slow",
    "2026-10-23T09:10:05.0000000Z \x1b[31mFAIL\x1b[0m test_wait",
    "    a line without a stamp",
    "2026-10-23T09:20:00.0000000Z ##[group]Run make vet",
    "2026-10-23T09:20:01.0000000Z RUSTSEC-2026-0001"])
WORKFLOW = """name: ci
on:
  schedule:
    - cron: "0 2 * * *"
  workflow_dispatch:
    inputs:
      first:
        description: steps to run first
        required: false
jobs:
  build-and-test:
    runs-on: ubuntu-latest
"""


def stamp(when):
    return when.strftime("%Y-%m-%dT%H:%M:%SZ")


def run(number, sha, conclusion="failure", status="completed",
        event="schedule", ended=NOW - 2 * HOUR, started=None):
    started = started or ended - HOUR
    return {"id": number, "head_sha": sha, "status": status,
            "conclusion": conclusion if status == "completed" else None,
            "event": event, "created_at": stamp(started),
            "run_started_at": stamp(started), "updated_at": stamp(ended),
            "html_url": "https://github.com/example/widget/actions/runs/%d"
            % number}


def step(name, conclusion, start, end):
    return {"name": name, "conclusion": conclusion,
            "started_at": "2026-10-23T%s:00Z" % start,
            "completed_at": "2026-10-23T%s:00Z" % end}


def red_jobs(steps=("test-slow",)):
    all_steps = [step("build", "success", "09:00", "09:09"),
                 step("test-slow", "failure" if "test-slow" in steps
                      else "success", "09:10", "09:19"),
                 step("vet-advisories", "failure" if "vet-advisories" in steps
                      else "success", "09:20", "09:29")]
    return [{"id": 900, "name": "build-and-test", "conclusion": "failure",
             "steps": all_steps},
            {"id": 901, "name": "release", "conclusion": "skipped",
             "steps": []}]


GREEN_JOBS = [{"id": 902, "name": "build-and-test", "conclusion": "success",
               "steps": []}]


def pr(number, title="", body="", messages=(), declared=(), merged=None):
    node = {"number": number, "title": title, "body": body,
            "closingIssuesReferences": {"nodes": [{"number": n}
                                                  for n in declared]},
            "commits": {"nodes": [{"commit": {"message": m}}
                                  for m in messages]}}
    if merged:
        node["mergedAt"] = merged
    return node


class FakeGitHub:
    """The REST and GraphQL endpoints forge-nightly reads and writes.
    `fail` names a (method, path start) that answers 500."""

    def __init__(self):
        self.runs = []
        self.jobs = {}
        self.issues = []
        self.open_prs, self.merged_prs = [], []
        self.workflow = WORKFLOW
        self.fail = None
        self.writes = []

    def __call__(self, url, token, scheme="Bearer", method="GET", body=None):
        assert token == "tok", token
        path = url.split("https://api.github.com", 1)[1]
        if self.fail and method == self.fail[0] and \
                path.startswith(self.fail[1]):
            return 500, {"message": "down"}
        repo = "/repos/example/widget"
        if path == "/graphql":
            assert body["variables"]["n"] == 101, body
            issue = next((i for i in self.issues if i["number"] == 101),
                         {"state": "open"})
            return 200, {"data": {"repository": {
                "issue": {"state": issue["state"].upper()},
                "open": {"nodes": self.open_prs},
                "merged": {"nodes": self.merged_prs}}}}
        assert path.startswith(repo), path
        path = path[len(repo):]
        if method == "GET":
            if path.startswith("/actions/workflows/ci.yaml/runs?branch=main"):
                return 200, {"workflow_runs": self.runs}
            if path.startswith("/issues?state=open&creator="):
                assert "creator=mergerwidgetagent%5Bbot%5D" in path, path
                if not path.endswith("&page=1"):
                    return 200, []
                return 200, [i for i in self.issues if i["state"] == "open"]
            if path.startswith("/actions/runs/"):
                number = int(path.split("/")[3])
                return 200, {"jobs": self.jobs[number]}
            if path.startswith("/compare/"):
                return 200, {"total_commits": 2, "commits": [
                    {"sha": "1" * 40, "commit": {"message": "Fix one\n\nx"}},
                    {"sha": "2" * 40, "commit": {"message": "Fix two"}}]}
            if path == "/commits/main":
                return 200, {"sha": NEXT_SHA}
            if path == "/contents/.github/workflows/ci.yaml?ref=main":
                return 200, {"content": base64.b64encode(
                    self.workflow.encode()).decode()}
            raise AssertionError(path)
        self.writes.append((method, path, body))
        if method == "POST" and path == "/issues":
            number = 101 + len(self.issues)
            self.issues.append({"number": number, "state": "open",
                                "body": body["body"],
                                "created_at": stamp(NOW)})
            return 201, {"number": number}
        if method == "PATCH":
            number = int(path.rsplit("/", 1)[1])
            for issue in self.issues:
                if issue["number"] == number:
                    issue["state"] = body["state"]
            return 200, {}
        if path.endswith("/labels"):
            return 200, []
        if path.endswith("/dispatches"):
            return 204, {}
        return 201, {}


class Main(unittest.TestCase):

    def setUp(self):
        self.dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.dir.cleanup)
        data = example()
        data["router"] = {"owners": {"triage": "LeadDev"}}
        data["nightly"] = dict(NIGHTLY)
        self.data = data
        self.config = os.path.join(self.dir.name, "config.json")
        self.save_config()
        self.env = {"FORGE_CONFIG": self.config, "HOME": self.dir.name,
                    "FORGE_STATE_DIR": self.dir.name}
        self.state = os.path.join(self.dir.name, "widget", "nightly",
                                  "nightly.json")
        self.gh = FakeGitHub()
        self.out, self.err, self.sent = [], [], []
        self.refuse = set()

    def save_config(self):
        with open(self.config, "w") as handle:
            json.dump(self.data, handle)

    def send(self, project, environ):
        def send(name, text):
            if name in self.refuse:
                return "unknown-name"
            self.sent.append((name, text))
            return None
        return send

    def main(self, *argv, now=NOW):
        return tool.main(list(argv), self.out.append, self.err.append,
                         self.env, now, call=self.gh,
                         mint=lambda project, name: "tok",
                         logs=lambda job: LOG, sender=self.send)

    def saved(self):
        with open(self.state) as handle:
            return json.load(handle)

    def red_night(self, steps=("test-slow",)):
        self.gh.runs = [run(2, RED_SHA), run(1, GREEN_SHA, "success",
                                             ended=NOW - 26 * HOUR)]
        self.gh.jobs = {2: red_jobs(steps), 1: GREEN_JOBS}

    def writes(self, method=None, end=""):
        return [w for w in self.gh.writes
                if (method is None or w[0] == method) and w[1].endswith(end)]

    def test_without_a_section_it_does_nothing(self):
        del self.data["nightly"]
        self.save_config()
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.out, ["forge-nightly: no nightly section; "
                                    "nothing is watched"])
        self.assertFalse(os.path.exists(self.state))

    def test_a_red_run_opens_one_issue_for_the_failed_steps_owners(self):
        self.red_night(("test-slow", "vet-advisories"))
        self.assertEqual(self.main(), 0)
        [(method, path, body)] = self.writes("POST", "/issues")
        self.assertEqual(body["title"], "The nightly is red at bbbbbbbbbb")
        self.assertEqual(body["labels"], ["bug", "Owner:UIDev",
                                          "Owner:Helper"])
        text = body["body"]
        self.assertIn("Failed: `test-slow` (UIDev), `vet-advisories` "
                      "(Helper).", text)
        self.assertIn("**test-slow**, UIDev's, the end of its output:", text)
        # Its own lines only, stamps and colour codes removed.
        self.assertIn("```\n##[group]Run make test-slow\nFAIL test_wait\n"
                      "    a line without a stamp\n```", text)
        self.assertIn("RUSTSEC-2026-0001", text)
        self.assertNotIn("built", text)
        self.assertIn("Commits since the last green nightly (aaaaaaaaaa, "
                      "run 1), 2:\n\n- `1111111111` Fix one\n"
                      "- `2222222222` Fix two", text)
        self.assertIn("\nNightly: ci.yaml\n", text)
        saved = self.saved()
        self.assertEqual(saved["issue"], 101)
        self.assertEqual(saved["judged"], 2)
        self.assertEqual(saved["newest"], "red")
        self.assertEqual(saved["red"]["steps"], ["test-slow",
                                                 "vet-advisories"])
        self.assertEqual(saved["red_since"], stamp(NOW - 26 * HOUR))
        self.assertEqual(saved["green"]["sha"], GREEN_SHA)
        self.assertIn("RED run 2 at bbbbbbbbbb: test-slow, vet-advisories",
                      self.out)

    def test_an_unnamed_step_is_triages(self):
        self.data["nightly"]["owners"] = {}
        self.save_config()
        self.red_night()
        self.main()
        [(_, _, body)] = self.writes("POST", "/issues")
        self.assertEqual(body["labels"], ["bug", "Owner:LeadDev"])

    def test_a_run_is_judged_once(self):
        self.red_night()
        self.main()
        self.gh.writes.clear()
        self.assertEqual(self.main(now=NOW + datetime.timedelta(minutes=5)),
                         0)
        self.assertEqual(self.writes("POST", "/issues"), [])
        self.assertEqual(self.writes("POST", "/comments"), [])

    def test_a_later_red_run_comments_and_adds_new_owners(self):
        self.red_night()
        self.main()
        self.gh.writes.clear()
        self.gh.runs.insert(0, run(3, NEXT_SHA, ended=NOW - HOUR / 2))
        self.gh.jobs[3] = red_jobs(("vet-advisories",))
        self.main()
        [(_, _, comment)] = self.writes("POST", "/comments")
        self.assertTrue(comment["body"].startswith("Red again.\n\nThe nightly "
                                                   "(`ci.yaml`"), comment)
        self.assertIn("Failed: `vet-advisories` (Helper).", comment["body"])
        [(_, path, labels)] = self.writes("POST", "/labels")
        self.assertEqual((path, labels["labels"]),
                         ("/issues/101/labels", ["bug", "Owner:Helper"]))
        self.assertEqual(self.saved()["red"]["steps"], ["vet-advisories"])

    def test_a_red_run_reopens_an_issue_a_fix_closed(self):
        self.red_night()
        self.main()
        self.gh.issues[0]["state"] = "closed"
        self.main(now=NOW + datetime.timedelta(minutes=5))
        self.assertFalse(self.saved()["issue_open"])
        self.gh.runs.insert(0, run(3, NEXT_SHA, ended=NOW))
        self.gh.jobs[3] = red_jobs()
        self.gh.writes.clear()
        self.main(now=NOW + datetime.timedelta(minutes=10))
        self.assertEqual(self.writes("PATCH")[0][1:],
                         ("/issues/101", {"state": "open"}))
        self.assertIn("REOPEN #101", self.out)
        self.assertTrue(self.saved()["issue_open"])

    def test_the_first_green_run_comments_and_closes(self):
        self.red_night()
        self.main()
        self.gh.writes.clear()
        self.gh.runs.insert(0, run(3, NEXT_SHA, "success", ended=NOW))
        self.gh.jobs[3] = GREEN_JOBS
        self.assertEqual(self.main(now=NOW + HOUR), 0)
        [(_, path, comment)] = self.writes("POST", "/comments")
        self.assertEqual(path, "/issues/101/comments")
        self.assertIn("The nightly is green at cccccccccc", comment["body"])
        self.assertEqual(self.writes("PATCH")[0][1:], (
            "/issues/101", {"state": "closed", "state_reason": "completed"}))
        saved = self.saved()
        self.assertNotIn("issue", saved)
        self.assertEqual((saved["newest"], saved["fixes"],
                          saved["green"]["run"]), ("green", [], 3))

    def test_a_cancelled_run_is_neither(self):
        self.gh.runs = [run(2, RED_SHA, "cancelled")]
        self.gh.jobs = {2: [{"id": 9, "name": "build-and-test",
                             "conclusion": "cancelled", "steps": []}]}
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.gh.writes, [])
        self.assertIn("NEITHER run 2 at bbbbbbbbbb: build-and-test cancelled",
                      self.out)

    def test_an_issue_opened_earlier_is_found_without_state(self):
        self.gh.issues = [{"number": 101, "state": "open",
                           "created_at": stamp(NOW - HOUR),
                           "body": "x\n\nNightly: ci.yaml\n\n— Merger"}]
        self.red_night()
        self.main()
        self.assertEqual(self.writes("POST", "/issues"), [])
        self.assertEqual(len(self.writes("POST", "/issues/101/comments")), 1)

    def test_the_pull_requests_that_close_it_are_its_fixes(self):
        self.red_night()
        self.main()
        self.gh.open_prs = [
            pr(5, body="Fixes #101"), pr(6, title="Fix #101 (wait longer)"),
            pr(7, messages=["Raise the wait\n\nCloses #101"]),
            pr(8, declared=[101]), pr(9, body="See `fixes #101`"),
            pr(10, body="Fixes #1010"), pr(11, body="Unrelated")]
        self.main(now=NOW + datetime.timedelta(minutes=5))
        self.assertEqual(self.saved()["fixes"], [5, 6, 7, 8])
        self.assertIn("FIXES #101: #5, #6, #7, #8", self.out)
        self.gh.issues[0]["state"] = "closed"
        self.main(now=NOW + datetime.timedelta(minutes=10))
        self.assertEqual(self.saved()["fixes"], [])

    def test_retry_after_retry_hours_with_the_failed_steps_first(self):
        self.red_night(("test-slow", "vet-advisories"))
        self.main(now=NOW)
        self.assertEqual(self.writes("POST", "/dispatches"), [])
        self.main(now=NOW + HOUR)
        [(_, path, body)] = self.writes("POST", "/dispatches")
        self.assertEqual(path, "/actions/workflows/ci.yaml/dispatches")
        self.assertEqual(body, {"ref": "main", "inputs": {
            "first": "test-slow,vet-advisories"}})
        comment = self.writes("POST", "/comments")[-1][2]["body"]
        self.assertTrue(comment.startswith(
            "Ran the nightly again on `main` at cccccccccc: 3 h since run 2 "
            "ended. The steps that failed last go first: "
            "`test-slow,vet-advisories`."), comment)
        # Not again while its run isn't seen, nor while it runs.
        self.main(now=NOW + HOUR + datetime.timedelta(minutes=5))
        self.assertEqual(len(self.writes("POST", "/dispatches")), 1)
        self.gh.runs.insert(0, run(3, NEXT_SHA, status="in_progress",
                                   event="workflow_dispatch",
                                   started=NOW + HOUR))
        self.main(now=NOW + 5 * HOUR)
        self.assertEqual(len(self.writes("POST", "/dispatches")), 1)
        self.assertIn("RUNNING run 3: in_progress", self.out)

    def test_a_run_on_another_event_is_not_the_nightly(self):
        self.red_night()
        self.gh.runs.insert(0, run(3, NEXT_SHA, status="in_progress",
                                   event="push"))
        self.main(now=NOW + HOUR)
        self.assertEqual(len(self.writes("POST", "/dispatches")), 1)

    def test_a_merged_fix_runs_it_again_at_once(self):
        self.red_night()
        self.main()
        self.gh.merged_prs = [
            pr(5, body="Fixes #101", merged=stamp(NOW - 3 * HOUR)),
            pr(6, body="Fixes #101", merged=stamp(NOW - HOUR / 2)),
            pr(7, body="Unrelated", merged=stamp(NOW - HOUR / 4))]
        self.main(now=NOW + datetime.timedelta(minutes=5))
        [(_, _, body)] = self.writes("POST", "/dispatches")
        comment = self.writes("POST", "/comments")[-1][2]["body"]
        self.assertIn("#6, which closes #101, merged at 2026-10-23T11:30Z, "
                      "after run 2 started", comment)

    def test_without_the_first_input_it_is_dispatched_plain(self):
        self.gh.workflow = WORKFLOW.replace("      first:", "      other:")
        self.red_night()
        self.main(now=NOW + HOUR)
        [(_, _, body)] = self.writes("POST", "/dispatches")
        self.assertEqual(body, {"ref": "main"})

    def test_escalation_to_the_developers_then_the_owner(self):
        self.red_night()
        self.main()
        self.main(now=NOW + 23 * HOUR)
        self.assertEqual(self.sent, [])
        self.refuse = {"Helper"}
        self.main(now=NOW + 24 * HOUR)
        self.assertEqual([n for n, _ in self.sent], ["LeadDev", "UIDev"])
        self.assertIn("The nightly has been red for 24 h: https://github.com/"
                      "example/widget/issues/101 (test-slow).",
                      self.sent[0][1])
        comment = self.writes("POST", "/comments")[-1][2]["body"]
        self.assertTrue(comment.startswith(
            "Red for 24 h: messaged LeadDev, UIDev through the relay. Not "
            "taken: Helper (unknown-name)."), comment)
        self.sent.clear()
        self.main(now=NOW + 30 * HOUR)
        self.assertEqual(self.sent, [])
        self.main(now=NOW + 48 * HOUR)
        self.assertEqual([n for n, _ in self.sent], ["example-owner"])
        comment = self.writes("POST", "/comments")[-1][2]["body"]
        self.assertTrue(comment.startswith(
            "@example-owner, the nightly has been red for 48 h."), comment)
        self.assertEqual(self.saved()["escalated"], [0, 1])

    def test_an_escalation_nobody_took_is_tried_again(self):
        self.data["nightly"]["tell_owner"] = "owner@example.org"
        self.data["nightly"]["escalate_hours"] = [1]
        self.save_config()
        self.red_night()
        self.main()
        self.refuse = {"LeadDev", "UIDev", "Helper"}
        self.gh.writes.clear()
        self.main(now=NOW + HOUR)
        self.assertEqual(self.writes("POST", "/comments")[1:], [])
        self.assertIn("NOT SENT escalation #101 after 1 h: LeadDev "
                      "(unknown-name); UIDev (unknown-name); Helper "
                      "(unknown-name); tried again next tick", self.out)
        self.refuse = set()
        self.main(now=NOW + 2 * HOUR)
        self.assertEqual(self.saved()["escalated"], [0])

    def test_a_dry_run_changes_nothing(self):
        self.red_night()
        self.assertEqual(self.main("--dry-run", now=NOW + 30 * HOUR), 0)
        self.assertEqual(self.gh.writes, [])
        self.assertEqual(self.sent, [])
        self.assertFalse(os.path.exists(self.state))
        self.assertIn("WOULD OPEN The nightly is red at bbbbbbbbbb, for UIDev",
                      self.out)

    def test_github_failing_skips_the_tick(self):
        self.red_night()
        self.gh.fail = ("GET", "/repos/example/widget/actions/workflows")
        self.assertEqual(self.main(), 2)
        self.assertIn("skipping this tick", self.err[0])
        self.assertFalse(os.path.exists(self.state))

    def test_a_refused_write_is_exit_2_and_judged_again(self):
        self.red_night()
        self.gh.fail = ("POST", "/repos/example/widget/issues")
        self.assertEqual(self.main(), 2)
        self.assertNotIn("judged", self.saved())
        self.gh.fail = None
        self.assertEqual(self.main(), 0)
        self.assertEqual(self.saved()["issue"], 101)


class Pieces(unittest.TestCase):

    def test_owner_of_by_name_then_glob_then_triage(self):
        owners = {"vet-*": "Helper", "vet-advisories": "UIDev"}
        self.assertEqual(tool.owner_of("vet-advisories", owners, "T"),
                         "UIDev")
        self.assertEqual(tool.owner_of("vet-licences", owners, "T"),
                         "Helper")
        self.assertEqual(tool.owner_of("test", owners, "T"), "T")

    def test_declares_first(self):
        self.assertTrue(tool.declares_first(WORKFLOW))
        self.assertTrue(tool.declares_first(
            "on:\n  workflow_dispatch:\n    inputs:\n      other: {}\n"
            "      # a comment\n      'first':\n        type: string\n"))
        for text in ("on: [push, workflow_dispatch]\n",
                     "on:\n  workflow_dispatch:\n  push:\n    inputs:\n"
                     "      first: {}\n",
                     "on:\n  workflow_dispatch:\n    inputs:\n      other:\n"
                     "        first: {}\n",
                     "on:\n  workflow_call:\n    inputs:\n      first: {}\n",
                     ""):
            with self.subTest(text=text):
                self.assertFalse(tool.declares_first(text))

    def test_quote_keeps_the_end(self):
        self.assertEqual(tool.quote("\x1b[1mab\x1b[0mcdef\n", 3), "[…]\ndef")
        self.assertEqual(tool.quote("abc", 3), "abc")

    def test_the_jobs_that_decide(self):
        jobs = [{"name": "build-and-test (linux)", "conclusion": "success"},
                {"name": "build-and-test (mac)", "conclusion": "failure"},
                {"name": "build-and-test-extra", "conclusion": "failure"}]
        decisive = tool.decisive_jobs(jobs, "build-and-test")
        self.assertEqual(len(decisive), 2)
        self.assertEqual(tool.verdict(decisive), "red")
        self.assertEqual(tool.verdict(decisive[:1]), "green")
        self.assertIsNone(tool.verdict([]))


if __name__ == "__main__":
    unittest.main()
