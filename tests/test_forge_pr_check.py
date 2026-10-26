"""Tests for bin/forge-pr-check.

Most tests inject `run` and `spawn`, so they need no network, no GitHub
and no toolchain. `PrepareRealGit` uses real git against a bare
repository carrying a `refs/pull/N/head`, because a fake can't say
whether a fetch of that ref, a checkout and a clean really leave the
worktree at the head, or whether a moved head is really seen.
"""

import datetime
import json
import os
import pathlib
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example, load_tool

pc = load_tool("forge-pr-check")
# The example project's checks: five steps, the first only with a web
# app, and a Rust build cache per slot.
pc.configure(config.Project(example(), None))
_real_start_next_tick = pc.start_next_tick


class RealChainReached(BaseException):
    """Not an Exception: the worker reports any Exception from its chain
    as CHAIN-FAILED and carries on, which would let a test pass."""


def _refuse_real_chain(popen=None, checkout=None, **dirs):
    """Stands in for start_next_tick in every test: the real one starts
    a real cron tick on this host. A test passes its own popen."""
    if popen is None:
        raise RealChainReached("a test reached the real start_next_tick")
    return _real_start_next_tick(popen=popen, checkout=checkout, **dirs)


pc.start_next_tick = _refuse_real_chain

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40
REPO = "example/widget"
T0 = datetime.datetime(2026, 10, 9, 12, 0, 0, tzinfo=datetime.timezone.utc)


def done(args, rc=0, out="", err=""):
    return subprocess.CompletedProcess(args, rc, out, err)


def heads_tsv(*rows):
    return "".join("%d\t%s\t%s\t%s\n" % row for row in rows)


class FakeRun:
    """`run` that answers by the first matching rule and records every
    call. A rule is (predicate on args, result or function of args)."""

    def __init__(self, *rules):
        self.rules = list(rules)
        self.calls = []

    def __call__(self, args, cwd=None, env=None, timeout=None):
        self.calls.append({"args": args, "cwd": cwd, "env": env})
        for match, answer in self.rules:
            if match(args):
                return answer(args) if callable(answer) else answer
        return done(args)


def is_mint(args):
    return os.path.basename(args[0]) == "forge-app-token"


def is_pulls(args):
    return args[:2] == ["gh", "api"] and any("/pulls?" in a for a in args)


def is_status(args):
    return args[:2] == ["gh", "api"] and any("/statuses/" in a for a in args)


class StateCase(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.state_dir = os.path.join(self._tmp.name, "state")
        os.makedirs(self.state_dir)
        self.lines = []

    def tearDown(self):
        self._tmp.cleanup()

    def state(self):
        return pc.load(self.state_dir)

    def write_state(self, state):
        pc.save(self.state_dir, state)


class Configure(unittest.TestCase):
    """The checks come from the project's configuration."""

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_the_example_projects_steps(self):
        self.assertEqual([args for _, args, only in pc.STEPS if not only], [
            ["make", "fmt-check"], ["make", "vet"], ["make", "test"],
            ["make", "doc-check"]])
        self.assertEqual(pc.STEPS[0][1:], (["npm", "ci", "--prefix", "web"],
                                           "web/package.json"))
        self.assertEqual(pc.CACHES, {"CARGO_TARGET_DIR": "cargo-target"})

    def test_the_project_and_its_poster(self):
        self.assertEqual((pc.REPO_SLUG, pc.MAIN, pc.POSTER, pc.HOST,
                          pc.STATUS_CONTEXT),
                         ("example/widget", "main", "Merger",
                          "build.example.org", "vm/build-and-test"))

    def test_the_defaults(self):
        data = example()
        data["checks"] = {}
        del data["merge"]
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.STEPS, [("test", ["make", "test"], None)])
        self.assertEqual(pc.CACHES, {})
        self.assertEqual((pc.SLOTS, pc.MEMORY_MAX, pc.STEP_TIMEOUT_S,
                          pc.POSTER, pc.STATUS_CONTEXT),
                         (2, "16G", 1800, "Merger", "vm/build-and-test"))
        self.assertIs(pc.MERGER_FIRST, False)

    def test_the_poster_defaults_to_the_merger(self):
        data = example()
        del data["checks"]["poster"]
        data["machinery"]["Shipper"] = {"app_id": 9, "installation_id": 9}
        data["merge"]["merger"] = "Shipper"
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.POSTER, "Shipper")

    def test_settings(self):
        data = example()
        data["checks"].update(slots=3, memory_max="off",
                              step_timeout_minutes=5, merger_first=True)
        pc.configure(config.Project(data, None))
        self.assertEqual((pc.slots_from_env({}), pc.memory_max_from_env({}),
                          pc.STEP_TIMEOUT_S, pc.MERGER_FIRST),
                         (3, None, 300, True))

    def test_test_ahead_and_its_defaults(self):
        self.assertIs(pc.TEST_AHEAD, True)
        self.assertEqual(pc.MATCH_MAX_AGE, datetime.timedelta(hours=24))
        data = example()
        data["merge"]["test_ahead"] = {"max_age_hours": 2}
        del data["checks"]["merger_first"]
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.MATCH_MAX_AGE, datetime.timedelta(hours=2))
        # It implies merger_first: the Merger's head goes first.
        self.assertIs(pc.MERGER_FIRST, True)

    def test_without_test_ahead_it_is_off(self):
        data = example()
        del data["merge"]["test_ahead"]
        data["checks"]["merger_first"] = False
        pc.configure(config.Project(data, None))
        self.assertEqual((pc.TEST_AHEAD, pc.MERGER_FIRST), (False, False))

    def test_no_host_says_nowhere(self):
        data = example()
        del data["host"]
        pc.configure(config.Project(data, None))
        self.assertEqual(pc.final_status({"ok": True, "seconds": 3}),
                         ("success", "green in 3s"))


class OpenHeads(unittest.TestCase):

    def test_reads_every_field_with_the_token(self):
        run = FakeRun((is_pulls, done([], 0, heads_tsv(
            (7, SHA_A, REPO, "false"), (8, SHA_B, "", "true")))))
        self.assertEqual(pc.open_heads("tok", run),
                         [(7, SHA_A, REPO, False, "", []),
                          (8, SHA_B, "", True, "", [])])
        call = run.calls[0]
        self.assertEqual(call["env"]["GH_TOKEN"], "tok")
        self.assertIn("--paginate", call["args"])
        self.assertIn("repos/%s/pulls?state=open&per_page=100" % REPO,
                      call["args"])

    def test_no_pull_requests_is_an_empty_list(self):
        self.assertEqual(pc.open_heads("t", FakeRun()), [])

    def test_a_failed_call_is_none(self):
        run = FakeRun((is_pulls, done([], 1, "", "HTTP 502")))
        self.assertIsNone(pc.open_heads("t", run))

    def test_a_failure_says_why(self):
        # A transient error and a token problem look alike without it.
        why = []
        run = FakeRun((is_pulls, done([], 1, "", "warning\n"
                                      "gh: Server Error (HTTP 502)\n")))
        self.assertIsNone(pc.open_heads("t", run, why))
        self.assertEqual(why, ["gh exited 1: gh: Server Error (HTTP 502)"])
        why = []
        run = FakeRun((is_pulls, done([], 0, "7\tabc\n")))
        self.assertIsNone(pc.open_heads("t", run, why))
        self.assertEqual(why, ["an unreadable line: '7\\tabc'"])

    def test_a_malformed_line_or_short_sha_is_none(self):
        for text in ("7\t%s\t%s\n" % (SHA_A, REPO),
                     heads_tsv((7, "abc", REPO, "false"))):
            run = FakeRun((is_pulls, done([], 0, text)))
            self.assertIsNone(pc.open_heads("t", run), text)


class NoteHeads(StateCase):

    def note(self, state, heads, post=None, when=T0):
        pc.note_heads(state, heads, when, self.state_dir, self.lines.append,
                      post)

    def test_queues_a_new_head_from_when_it_is_first_seen(self):
        state = self.state()
        self.note(state, [(7, SHA_A, REPO, False)])
        self.note(state, [(7, SHA_A, REPO, False)],
                  when=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(state["waiting"],
                         {SHA_A: {"pr": 7, "since": pc.iso(T0)}})

    def test_a_new_head_gets_a_queued_status_once(self):
        # Without it a head showed nothing until a slot came free, and
        # on a busy host it looked as if no check had seen it.
        posted = []
        state = self.state()
        post = lambda *a: posted.append(a)
        self.note(state, [(7, SHA_A, REPO, False), (8, SHA_B, REPO, True)],
                  post)
        self.note(state, [(7, SHA_A, REPO, False)], post,
                  when=T0 + datetime.timedelta(minutes=5))
        self.assertEqual(posted, [(SHA_A, "pending", pc.queued_description(
            state, SHA_A, T0))])
        self.assertRegex(posted[0][2], r"^queued at \d\d:\d\dZ for a check "
                         r"slot; 0 other heads waiting$")

    def test_skips_a_draft_and_a_head_already_checked(self):
        state = self.state()
        state["results"][SHA_B] = {"pr": 8}
        self.note(state, [(7, SHA_A, REPO, True), (8, SHA_B, REPO, False)])
        self.assertEqual(state["waiting"], {})

    def test_forgets_a_head_no_pull_request_has_any_more(self):
        state = self.state()
        self.note(state, [(7, SHA_A, REPO, False)])
        self.note(state, [(7, SHA_B, REPO, False)])
        self.assertEqual(list(state["waiting"]), [SHA_B])

    def test_refuses_a_foreign_head_once_and_posts_why(self):
        state, posts = self.state(), []
        post = lambda *a: posts.append(a) or True
        for _ in range(2):
            self.note(state, [(7, SHA_A, "someone/fork", False),
                              (8, SHA_B, "", False)], post)
        self.assertEqual(state["waiting"], {})
        self.assertEqual(sorted(state["refused"]), [SHA_A, SHA_B])
        self.assertEqual([(sha, st) for sha, st, _ in posts],
                         [(SHA_A, "error"), (SHA_B, "error")])
        self.assertIn("someone/fork", posts[0][2])
        self.assertIn("a deleted repository", posts[1][2])
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertEqual(logged.count("REFUSED"), 2)


class NextHead(unittest.TestCase):

    def state(self, **waiting):
        return {"waiting": {k: v for k, v in waiting.items()}}

    def test_none_when_nothing_waits(self):
        self.assertIsNone(pc.next_head({"waiting": {}}))

    def test_the_longest_waiting_first_and_ties_by_sha(self):
        state = {"waiting": {
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"},
            SHA_A: {"pr": 1, "since": "2026-10-09T10:00:00Z"},
            SHA_C: {"pr": 3, "since": "2026-10-09T11:00:00Z"}}}
        self.assertEqual(pc.next_head(state), (SHA_A, 1))

    def test_a_head_that_failed_goes_behind_and_is_given_up_on(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z", "errors": 1},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"}}}
        self.assertEqual(pc.next_head(state), (SHA_B, 2))
        del state["waiting"][SHA_B]
        self.assertEqual(pc.next_head(state), (SHA_A, 1))
        state["waiting"][SHA_A]["errors"] = pc.MAX_ERRORS
        self.assertIsNone(pc.next_head(state))

    def test_a_new_head_keeps_the_place_of_the_one_it_replaces(self):
        # Every push sent a pull request to the back of the queue, so
        # one that was updated while it waited never reached a slot.
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z", "errors": 1},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"}},
            "results": {}, "refused": {}}
        when = pc.parse("2026-10-09T11:00:00Z")
        pc.note_heads(state, [(1, SHA_C, pc.REPO_SLUG, False),
                              (2, SHA_B, pc.REPO_SLUG, False)],
                      when, tempfile.mkdtemp(), out=lambda *a: None)
        self.assertNotIn(SHA_A, state["waiting"])
        self.assertEqual(state["waiting"][SHA_C],
                         {"pr": 1, "since": "2026-10-09T09:00:00Z"})
        self.assertEqual(pc.next_head(state), (SHA_C, 1))

    def test_a_head_after_a_verdict_waits_from_when_it_came(self):
        # Nothing of the pull request was waiting: it starts afresh.
        state = {"waiting": {}, "results": {SHA_A: {"pr": 1}},
                 "refused": {}}
        when = pc.parse("2026-10-09T11:00:00Z")
        pc.note_heads(state, [(1, SHA_C, pc.REPO_SLUG, False)],
                      when, tempfile.mkdtemp(), out=lambda *a: None)
        self.assertEqual(state["waiting"][SHA_C]["since"],
                         "2026-10-09T11:00:00Z")

    def test_a_head_the_host_failed_keeps_its_place(self):
        # A run killed by a full disk was not the pull request's fault:
        # it sat behind 14 later heads for half an hour.
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z", "faults": 2},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"}}}
        self.assertEqual(pc.next_head(state), (SHA_A, 1))
        state["waiting"][SHA_A]["faults"] = pc.MAX_ERRORS
        self.assertEqual(pc.next_head(state), (SHA_B, 2))

    def test_a_head_just_started_is_skipped_until_the_grace_is_up(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z",
                    "started": pc.iso(T0)},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"}}}
        self.assertEqual(pc.next_head(state, when=T0), (SHA_B, 2))
        later = T0 + pc.START_GRACE
        self.assertEqual(pc.next_head(state, when=later), (SHA_A, 1))

    def test_an_unreadable_start_stamp_does_not_hide_a_head(self):
        state = {"waiting": {SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z",
                                     "started": "soon"}}}
        self.assertEqual(pc.next_head(state, when=T0), (SHA_A, 1))

    def test_the_mergers_head_just_started_counts_as_busy(self):
        # It holds nothing: the rest go on, oldest first.
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T11:00:00Z",
                    "started": pc.iso(T0)},
            SHA_B: {"pr": 2, "since": "2026-10-09T09:00:00Z"}}}
        self.assertEqual(pc.next_head(state, first_pr=1, when=T0), (SHA_B, 2))
        self.assertEqual(pc.next_head(state, first_pr=1,
                                      when=T0 + pc.START_GRACE), (SHA_A, 1))

    # The Merger's pull request first.

    def test_the_mergers_pull_request_goes_first(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"}}}
        self.assertEqual(pc.next_head(state, first_pr=2), (SHA_B, 2))

    def test_a_merger_pull_request_with_nothing_waiting_holds_nothing(self):
        # The Merger may be waiting on something no check here ends, so
        # its pull request only orders; it never keeps a slot empty.
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"}}}
        self.assertEqual(pc.next_head(state, first_pr=9), (SHA_A, 1))

    def test_the_mergers_head_running_in_a_slot_holds_nothing(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"}}}
        self.assertEqual(pc.next_head(state, {SHA_B}, first_pr=2), (SHA_A, 1))
        self.assertEqual(pc.next_head(state, {SHA_B}), (SHA_A, 1))

    def test_the_oldest_of_the_mergers_heads_goes_first(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T08:00:00Z"},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z"},
            SHA_C: {"pr": 2, "since": "2026-10-09T09:00:00Z"}}}
        self.assertEqual(pc.next_head(state, first_pr=2), (SHA_C, 2))
        self.assertEqual(pc.next_head(state, {SHA_C}, first_pr=2), (SHA_B, 2))

    def test_without_the_merger_the_oldest_head_is_picked(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"}}}
        self.assertEqual(pc.next_head(state, first_pr=None), (SHA_A, 1))

    def test_the_mergers_head_given_up_on_holds_nothing(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z",
                    "errors": pc.MAX_ERRORS}}}
        self.assertEqual(pc.next_head(state, first_pr=2), (SHA_A, 1))

    def test_the_mergers_head_one_short_of_max_errors_still_goes_first(self):
        state = {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z",
                    "errors": pc.MAX_ERRORS - 1}}}
        self.assertEqual(pc.next_head(state, first_pr=2), (SHA_B, 2))


class MergerFirst(unittest.TestCase):
    """What the Merger said it waits on, from its next.json."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def said(self, value):
        with open(os.path.join(self.dir, "next.json"), "w") as handle:
            handle.write(value if isinstance(value, str)
                         else json.dumps(value))

    def test_a_fresh_pull_request_up_to_the_limit(self):
        self.said({"at": pc.iso(T0), "pr": 7})
        self.assertEqual(pc.merger_first(self.dir, T0), 7)
        self.assertEqual(pc.merger_first(self.dir, T0 + pc.MERGER_FRESH), 7)

    def test_a_stale_one_is_ignored(self):
        # A Merger that has stopped must not hold every check.
        self.said({"at": pc.iso(T0), "pr": 7})
        self.assertIsNone(pc.merger_first(
            self.dir, T0 + pc.MERGER_FRESH + datetime.timedelta(seconds=1)))

    def test_nothing_waited_on(self):
        self.said({"at": pc.iso(T0), "pr": None})
        self.assertIsNone(pc.merger_first(self.dir, T0))

    def test_unreadable_or_absent_is_none(self):
        self.assertIsNone(pc.merger_first(self.dir, T0))
        self.assertIsNone(pc.merger_first(None, T0))
        for bad in ("{", "[]", '{"at": "x", "pr": 1}', '{"pr": 1}',
                    '{"at": "%s", "pr": "1"}' % pc.iso(T0),
                    '{"at": "%s", "pr": true}' % pc.iso(T0)):
            self.said(bad)
            self.assertIsNone(pc.merger_first(self.dir, T0), bad)


class Prune(unittest.TestCase):

    def test_drops_only_what_is_older_than_keep(self):
        old = pc.iso(T0 - pc.KEEP - datetime.timedelta(seconds=1))
        new = pc.iso(T0 - pc.KEEP)
        state = {"waiting": {SHA_C: {"pr": 3, "since": old}},
                 "results": {SHA_A: {"finished": old},
                             SHA_B: {"finished": new}},
                 "refused": {SHA_A: {"since": old}}}
        pc.prune(state, T0)
        self.assertEqual(list(state["results"]), [SHA_B])
        self.assertEqual(state["refused"], {})
        self.assertEqual(list(state["waiting"]), [SHA_C])


class Posting(unittest.TestCase):

    def test_final_status_names_the_first_failed_step(self):
        self.assertEqual(pc.final_status({"ok": True, "seconds": 61}),
                         ("success", "green on build.example.org in 61s"))
        self.assertEqual(
            pc.final_status({"ok": False, "failed": ["vet", "test"]}),
            ("failure", "red on build.example.org: vet failed (then test)"))

    def test_a_post_mints_and_writes_the_status_as_the_merger(self):
        run = FakeRun((is_mint, done([], 0, "tok\n")))
        post = pc.make_poster(run, lambda line: None)
        self.assertTrue(post(SHA_A, "failure", "x" * 200))
        self.assertEqual(run.calls[0]["args"][1:], ["--token", "Merger"])
        call = run.calls[1]
        self.assertEqual(call["env"]["GH_TOKEN"], "tok")
        self.assertEqual(call["args"], [
            "gh", "api", "-X", "POST", "repos/%s/statuses/%s" % (REPO, SHA_A),
            "-f", "state=failure", "-f", "context=vm/build-and-test",
            "-f", "description=" + "x" * pc.DESCRIPTION_MAX])

    def test_a_token_is_reused_until_it_may_be_near_expiry(self):
        # agent-forge #13: a mint per post cost a run about a second.
        now = [0.0]
        run = FakeRun((is_mint, done([], 0, "tok\n")))
        post = pc.make_poster(run, lambda line: None, timer=lambda: now[0])
        post(SHA_A, "pending", "p")
        post(SHA_A, "success", "s")
        self.assertEqual(sum(is_mint(c["args"]) for c in run.calls), 1)
        now[0] = pc.TOKEN_REUSE_S
        post(SHA_B, "success", "s")
        self.assertEqual(sum(is_mint(c["args"]) for c in run.calls), 2)

    def test_with_the_project_it_posts_in_process(self):
        calls = []

        def call(url, token, scheme="Bearer", method="GET", body=None):
            calls.append((url, token, method, body))
            if url.endswith("/access_tokens"):
                return 201, {"token": "tok"}
            if url.endswith("/app"):
                return 200, {"slug": "x"}
            return 201, {}
        project = pc.config.Project(example(), None)
        with mock.patch.object(pc.github, "assertion_for",
                               return_value=("jwt", {})):
            post = pc.make_poster(None, lambda line: None, project, call)
            self.assertTrue(post(SHA_A, "success", "x" * 200))
            self.assertTrue(post(SHA_A, "success", "y"))
        statuses = [c for c in calls if "/statuses/" in c[0]]
        self.assertEqual(len(calls) - len(statuses), 1)
        self.assertEqual(statuses[0][1:], ("tok", "POST", {
            "state": "success", "context": "vm/build-and-test",
            "description": "x" * pc.DESCRIPTION_MAX}))
        self.assertTrue(statuses[0][0].endswith(
            "/repos/%s/statuses/%s" % (REPO, SHA_A)))

    def test_in_process_a_refusal_is_false_and_said(self):
        lines = []

        def call(url, token, scheme="Bearer", method="GET", body=None):
            if url.endswith("/access_tokens"):
                return 201, {"token": "tok"}
            return 403, {"message": "Resource not accessible"}
        project = pc.config.Project(example(), None)
        with mock.patch.object(pc.github, "assertion_for",
                               return_value=("jwt", {})):
            post = pc.make_poster(None, lines.append, project, call)
            self.assertFalse(post(SHA_A, "success", "s"))
        self.assertIn("HTTP 403 Resource not accessible", lines[0])

    def test_a_failed_mint_or_post_is_false_and_said(self):
        lines = []
        post = pc.make_poster(FakeRun((is_mint, done([], 6))), lines.append)
        self.assertFalse(post(SHA_A, "success", "s"))
        refused = done([], 1, "", "HTTP 403")
        post = pc.make_poster(FakeRun((is_mint, done([], 0, "t")),
                                      (is_status, refused)), lines.append)
        self.assertFalse(post(SHA_A, "success", "s"))
        self.assertTrue(lines[0].startswith("POST-FAILED aaaaaaaaaa success: "
                                            "could not mint"))
        self.assertIn("HTTP 403", lines[1])

    def test_repost_only_unposted_verdicts_on_open_heads_oldest_first(self):
        def result(minute, posted):
            r = {"pr": 1, "ok": True, "seconds": 1, "failed": [],
                 "finished": "2026-10-09T10:%02d:00Z" % minute}
            if posted is not None:
                r["posted"] = posted
            return r
        state = {"results": {SHA_A: result(2, False), SHA_B: result(1, None),
                             SHA_C: result(0, True),
                             "d" * 40: result(0, False)}}
        heads = [(1, s, REPO, False) for s in (SHA_A, SHA_B, SHA_C)]
        posts = []
        pc.repost(state, heads, lambda sha, *a: posts.append(sha) or True,
                  lambda line: None)
        self.assertEqual(posts, [SHA_B, SHA_A])
        self.assertTrue(state["results"][SHA_A]["posted"])
        self.assertFalse(state["results"]["d" * 40]["posted"])

    def test_repost_is_batched(self):
        state = {"results": {"%040x" % i: {
            "pr": 1, "ok": True, "seconds": 1, "failed": [],
            "finished": pc.iso(T0)} for i in range(pc.POST_BATCH + 3)}}
        heads = [(1, s, REPO, False) for s in state["results"]]
        posts = []
        pc.repost(state, heads, lambda sha, *a: posts.append(sha) or False,
                  lambda line: None)
        self.assertEqual(len(posts), pc.POST_BATCH)


class Tick(StateCase):

    def tick(self, heads=None, post=None, fail=False, slots=2,
             merger_dir=None):
        run = FakeRun((is_pulls, done([], 1 if fail else 0,
                                      heads_tsv(*(heads or [])))))
        self.spawned = []
        rc = pc.tick(self.state_dir, "tok", run,
                     lambda *a: self.spawned.append(a), self.lines.append,
                     when=T0, post=post, slots=slots, merger_dir=merger_dir)
        return rc

    def merger_says(self, pr, age=datetime.timedelta(minutes=1)):
        """A Merger state directory whose next.json names pr, written
        `age` before the tick."""
        merger = os.path.join(self._tmp.name, "merger")
        os.makedirs(merger, exist_ok=True)
        with open(os.path.join(merger, "next.json"), "w") as handle:
            json.dump({"at": pc.iso(T0 - age), "pr": pr}, handle)
        return merger

    def hold(self, slot, holder=None):
        held = pc.try_lock(pc.lock_path(self.state_dir, slot), holder)
        self.addCleanup(held.close)
        return held

    def test_cannot_list_pull_requests_is_2_and_starts_nothing(self):
        self.assertEqual(self.tick(fail=True), 2)
        self.assertEqual(self.spawned, [])
        self.assertTrue(self.lines[0].startswith("NO-PULLS"))
        self.assertIn("gh exited", self.lines[0])

    def test_idle_when_nothing_waits(self):
        self.assertEqual(self.tick(), 0)
        self.assertEqual(self.lines, ["IDLE: no open head is waiting"])

    def test_idle_says_when_every_head_was_given_up_on(self):
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0), "errors": pc.MAX_ERRORS}}})
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(self.lines, ["IDLE: 1 waiting, each given up on "
                                      "after 3 failures"])

    def test_starts_the_oldest_waiting_head_after_saving_the_queue(self):
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")])
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 1)])
        self.assertEqual(self.lines, ["STARTED #8 bbbbbbbbbb; 1 waiting"])
        self.assertEqual(sorted(self.state()["waiting"]), [SHA_A, SHA_B])

    def test_a_live_quiet_hold_starts_nothing_and_says_whose(self):
        from agent_forge import quiet
        folder = os.path.join(self._tmp.name, "quiet")
        os.makedirs(folder)
        quiet.hold(folder, "Alice", 30, "timing run",
                   T0 - datetime.timedelta(minutes=5))
        with mock.patch.object(pc, "QUIET_DIR", folder):
            self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.lines, [
            "QUIET: Alice until 2026-%s: timing run; starting "
            "nothing; 1 waiting" % pc.iso(T0 + datetime.timedelta(
                minutes=25))[5:]])
        self.assertEqual(sorted(self.state()["waiting"]), [SHA_A])

    def test_the_quiet_directory_comes_from_the_environment_first(self):
        data = example()
        data["quiet"] = {"dir": "~/quiet"}
        project = config.Project(data, None)
        self.assertEqual(pc.quiet_dir(project, {}),
                         os.path.expanduser("~/quiet"))
        self.assertEqual(
            pc.quiet_dir(project, {"FORGE_QUIET_DIR": "/Users/q"}),
            "/Users/q")
        data.pop("quiet")
        self.assertIsNone(pc.quiet_dir(config.Project(data, None), {}))
        self.assertEqual(pc.quiet_dir(config.Project(data, None),
                                      {"FORGE_QUIET_DIR": "~/q"}),
                         os.path.expanduser("~/q"))

    def test_a_hold_past_its_time_holds_nothing(self):
        from agent_forge import quiet
        folder = os.path.join(self._tmp.name, "quiet")
        os.makedirs(folder)
        quiet.hold(folder, "Alice", 30, "old",
                   T0 - datetime.timedelta(hours=1))
        with mock.patch.object(pc, "QUIET_DIR", folder):
            self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual([s[1] for s in self.spawned], [SHA_A])

    def test_a_posting_tick_starts_a_posting_worker_and_reposts(self):
        self.write_state({"results": {SHA_B: {
            "pr": 8, "ok": True, "seconds": 3, "failed": [],
            "finished": pc.iso(T0)}}})
        posts = []
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                  post=lambda *a: posts.append(a) or True)
        self.assertEqual(self.spawned, [(self.state_dir, SHA_A, 7, True, 1)])
        self.assertEqual(posts, [
            (SHA_A, "pending", "queued at %s for a check slot; 0 other "
             "heads waiting" % T0.strftime("%H:%MZ")),
            (SHA_B, "success", "green on build.example.org in 3s")])
        self.assertIs(self.state()["results"][SHA_B]["posted"], True)

    def test_starts_nothing_while_a_worker_holds_the_lock(self):
        held = pc.try_lock(os.path.join(self.state_dir, "worker.lock"),
                           "#7 %s %s" % (SHA_A, pc.iso(T0)))
        try:
            self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                      slots=1)
        finally:
            held.close()
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.lines, ["RUNNING #7 aaaaaaaaaa since %s; "
                                      "1 waiting" % pc.iso(T0)])

    def test_a_run_of_a_superseded_head_is_stopped(self):
        # #7 was pushed to (SHA_A -> SHA_C) while its old head ran.
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        self.hold(1, "#7 %s %s pid=4242" % (SHA_A, pc.iso(T0)))
        stopped = []
        run = FakeRun((is_pulls, done([], 0, heads_tsv(
            (7, SHA_C, REPO, "false"), (8, SHA_B, REPO, "false")))))
        self.spawned = []
        pc.tick(self.state_dir, "tok", run,
                lambda *a: self.spawned.append(a), self.lines.append,
                when=T0, slots=1, stop=lambda pid: stopped.append(pid) or True)
        self.assertEqual(stopped, [4242])
        self.assertEqual(self.spawned, [])
        line = ("%s SUPERSEDED #7 aaaaaaaaaa: no longer the head of any "
                "open pull request; stopping the run in slot 1 (pid 4242), "
                "since %s" % (pc.iso(T0), pc.iso(T0)))
        self.assertEqual(self.lines, [line])
        self.assertIn(line, pathlib.Path(self.state_dir,
                                         "verdicts.log").read_text())

    def test_a_superseded_run_frees_nothing_until_it_exits(self):
        # The slot is still held, so the other slot starts the next head;
        # the stopped worker's chain starts the new one.
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        self.hold(1, "#7 %s %s pid=4242" % (SHA_A, pc.iso(T0)))
        run = FakeRun((is_pulls, done([], 0, heads_tsv(
            (8, SHA_B, REPO, "false")))))
        self.spawned = []
        pc.tick(self.state_dir, "tok", run,
                lambda *a: self.spawned.append(a), self.lines.append,
                when=T0, slots=2, stop=lambda pid: True)
        self.assertEqual(self.spawned, [(self.state_dir, SHA_B, 8, False, 2)])
        self.assertIn(" SUPERSEDED #7 ", self.lines[0])

    def test_a_run_that_cannot_be_signalled_says_so(self):
        self.hold(1, "#7 %s %s pid=4242" % (SHA_A, pc.iso(T0)))
        run = FakeRun((is_pulls, done([], 0, "")))
        pc.tick(self.state_dir, "tok", run, lambda *a: None,
                self.lines.append, when=T0, slots=1, stop=lambda pid: False)
        self.assertIn("could not stop the run in slot 1 (pid 4242)",
                      self.lines[0])

    def test_current_heads_predictions_and_old_lines_are_not_stopped(self):
        self.hold(1, "#7 %s %s pid=1" % (SHA_A, pc.iso(T0)))
        self.hold(2, "#8 %s %s pid=2 ahead" % (SHA_C, pc.iso(T0)))
        self.hold(3, "#9 %s %s" % (SHA_B, pc.iso(T0)))
        stopped = []
        run = FakeRun((is_pulls, done([], 0, heads_tsv(
            (7, SHA_A, REPO, "false")))))
        pc.tick(self.state_dir, "tok", run, lambda *a: None,
                self.lines.append, when=T0, slots=3,
                stop=lambda pid: stopped.append(pid) or True)
        self.assertEqual(stopped, [])
        self.assertEqual([l.split()[0] for l in self.lines],
                         ["RUNNING"] * 3)

    def test_a_second_slot_starts_the_next_head(self):
        self.write_state({"waiting": {
            SHA_A: {"pr": 7, "since": pc.iso(T0 - datetime.timedelta(hours=2))},
            SHA_B: {"pr": 8, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        self.hold(1, "#7 %s %s" % (SHA_A, pc.iso(T0)))
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")])
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 2)])
        self.assertEqual(self.lines, [
            "RUNNING #7 aaaaaaaaaa since %s; 1 waiting" % pc.iso(T0),
            "STARTED #8 bbbbbbbbbb in slot 2; 0 waiting"])

    def test_a_head_just_started_is_not_started_again_in_the_other_slot(self):
        # The race: the first tick's worker hasn't taken its lock yet, so
        # no slot shows the head as running.
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(self.spawned, [(self.state_dir, SHA_A, 7, False, 1)])
        self.assertEqual(self.state()["waiting"][SHA_A]["started"], pc.iso(T0))
        self.lines.clear()
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.lines, [
            "STARTING: 1 just started, not yet running; 0 waiting"])

    def test_a_head_whose_worker_never_ran_is_started_after_the_grace(self):
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0 - datetime.timedelta(hours=1)),
            "started": pc.iso(T0 - pc.START_GRACE)}}})
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(self.spawned, [(self.state_dir, SHA_A, 7, False, 1)])

    def test_a_head_running_in_one_slot_is_not_started_in_another(self):
        self.hold(1, "#7 %s %s" % (SHA_A, pc.iso(T0)))
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(self.spawned, [])

    def test_slot_one_free_is_used_first(self):
        self.hold(2, "#7 %s %s" % (SHA_A, pc.iso(T0)))
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")])
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 1)])

    def test_every_slot_held_starts_nothing(self):
        self.hold(1, "#7 %s %s" % (SHA_A, pc.iso(T0)))
        self.hold(2, "#8 %s %s" % (SHA_B, pc.iso(T0)))
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false"),
                   (9, "c" * 40, REPO, "false")])
        self.assertEqual(self.spawned, [])
        self.assertEqual(len(self.lines), 2)

    def test_slots_from_the_environment(self):
        self.assertEqual(pc.slots_from_env({}), pc.DEFAULT_SLOTS)
        self.assertEqual(pc.slots_from_env({"FORGE_PR_CHECK_SLOTS": "3"}),
                         3)
        for bad in ("0", "-1", "two", "\u00b2", ""):
            self.assertEqual(pc.slots_from_env(
                {"FORGE_PR_CHECK_SLOTS": bad}), pc.DEFAULT_SLOTS, bad)

    def test_each_slot_has_its_own_lock_worktree_and_cache(self):
        self.assertEqual(os.path.basename(pc.lock_path("/s", 1)),
                         "worker.lock")
        self.assertEqual(os.path.basename(pc.lock_path("/s", 2)),
                         "worker-2.lock")
        self.assertEqual(pc.step_env("/s", {}, 2)["CARGO_TARGET_DIR"],
                         "/s/cargo-target-2")
        self.assertEqual(pc.step_env("/s", {})["CARGO_TARGET_DIR"],
                         "/s/cargo-target")

    def test_a_holder_that_has_not_said_who_is_busy(self):
        held = pc.try_lock(os.path.join(self.state_dir, "worker.lock"))
        try:
            self.tick([(7, SHA_A, REPO, "false")], slots=1)
        finally:
            held.close()
        self.assertEqual(self.lines,
                         ["BUSY: the worker lock is held; 1 waiting"])

    # The Merger's pull request first.

    def test_starts_the_mergers_head_before_an_older_one(self):
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                  merger_dir=self.merger_says(7))
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_A, 7, False, 1)])
        self.assertEqual(self.lines, ["STARTED #7 aaaaaaaaaa; 1 waiting, "
                                      "first: the Merger waits on it"])

    def test_a_stuck_wait_holds_nothing(self):
        # The Merger names #7 for a wait no check here ends (its CI
        # workflow hasn't finished, say), and next.json stays fresh all
        # the while. Nothing of #7 is waiting, so #8 is started.
        self.tick([(8, SHA_B, REPO, "false")],
                  merger_dir=self.merger_says(7))
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 1)])
        self.assertEqual(self.lines, ["STARTED #8 bbbbbbbbbb; 0 waiting"])
        self.assertFalse(any(l.startswith("HELD") for l in self.lines))

    def test_the_mergers_head_running_leaves_the_free_slot_to_the_rest(self):
        self.hold(1, "#7 %s %s" % (SHA_A, pc.iso(T0)))
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                  merger_dir=self.merger_says(7))
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 2)])
        self.assertEqual(self.lines, [
            "RUNNING #7 aaaaaaaaaa since %s; 1 waiting" % pc.iso(T0),
            "STARTED #8 bbbbbbbbbb in slot 2; 0 waiting"])

    def test_the_mergers_head_just_started_leaves_the_slot_to_the_rest(self):
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0), "started": pc.iso(T0)}}})
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                  merger_dir=self.merger_says(7))
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 1)])

    def test_a_stale_merger_holds_nothing(self):
        merger = self.merger_says(
            7, age=pc.MERGER_FRESH + datetime.timedelta(minutes=1))
        self.tick([(8, SHA_B, REPO, "false")], merger_dir=merger)
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 1)])

    def test_a_merger_waiting_on_nothing_holds_nothing(self):
        self.tick([(8, SHA_B, REPO, "false")],
                  merger_dir=self.merger_says(None))
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 1)])

    def test_the_mergers_head_given_up_on_holds_nothing(self):
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0), "errors": pc.MAX_ERRORS}}})
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                  merger_dir=self.merger_says(7))
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_B, 8, False, 1)])

    def test_nothing_waiting_with_the_merger_named_is_idle(self):
        self.tick([], merger_dir=self.merger_says(7))
        self.assertEqual(self.lines, ["IDLE: no open head is waiting"])

    def test_a_dead_holders_line_is_not_read(self):
        pathlib.Path(self.state_dir, "worker.lock").write_text(
            "#7 %s %s\n" % (SHA_A, pc.iso(T0)))
        self.tick([(7, SHA_A, REPO, "false")])
        self.assertEqual(len(self.spawned), 1)


def git(*args, cwd):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    return subprocess.run(["git"] + list(args), cwd=cwd, env=env, check=True,
                          capture_output=True, text=True).stdout.strip()


class PrepareRealGit(unittest.TestCase):
    """A bare origin with `main` and `refs/pull/7/head`, the cron
    checkout cloned from it, and the real `prepare`."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = self._tmp.name
        self.origin = os.path.join(root, "origin.git")
        self.seed = os.path.join(root, "seed")
        self.repo = os.path.join(root, "repo")
        self.wt = os.path.join(root, "state", "wt")
        os.makedirs(os.path.dirname(self.wt))
        git("init", "-q", "--bare", "-b", "main", self.origin, cwd=root)
        git("init", "-q", "-b", "main", self.seed, cwd=root)
        self.commit("base")
        git("push", "-q", self.origin, "main", cwd=self.seed)
        git("checkout", "-q", "-b", "feature", cwd=self.seed)
        self.head = self.commit("feature")
        git("push", "-q", self.origin, "feature:refs/pull/7/head",
            cwd=self.seed)
        # Not a local clone, which would bring refs/pull/7/head's
        # objects along: the head isn't here until prepare fetches it.
        git("clone", "-q", "file://" + self.origin, self.repo, cwd=root)

    def tearDown(self):
        self._tmp.cleanup()

    def commit(self, name):
        pathlib.Path(self.seed, name).write_text(name)
        git("add", name, cwd=self.seed)
        git("commit", "-q", "-m", name, cwd=self.seed)
        return git("rev-parse", "HEAD", cwd=self.seed)

    def test_leaves_a_detached_worktree_at_the_head(self):
        self.assertEqual(pc.prepare(self.repo, self.wt, self.head, 7),
                         ("ready", ""))
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.wt), self.head)
        self.assertEqual(
            subprocess.run(["git", "symbolic-ref", "-q", "HEAD"],
                           cwd=self.wt).returncode, 1)

    def test_a_second_prepare_comes_back_clean(self):
        pc.prepare(self.repo, self.wt, self.head, 7)
        pathlib.Path(self.wt, "leftover").write_text("x")
        pathlib.Path(self.wt, "feature").write_text("edited")
        self.assertEqual(pc.prepare(self.repo, self.wt, self.head, 7),
                         ("ready", ""))
        self.assertFalse(os.path.exists(os.path.join(self.wt, "leftover")))
        self.assertEqual(git("status", "--porcelain", cwd=self.wt), "")

    def test_sees_a_head_that_moved(self):
        newer = self.commit("more")
        git("push", "-q", "-f", self.origin, "feature:refs/pull/7/head",
            cwd=self.seed)
        self.assertEqual(pc.prepare(self.repo, self.wt, self.head, 7),
                         ("moved", newer))

    def test_a_head_already_here_is_not_fetched(self):
        # agent-forge #13: the fetch was up to 1.6 s of every run.
        git("fetch", "-q", "origin", "refs/pull/7/head", cwd=self.repo)
        calls = []

        def run(args, **kw):
            calls.append(args)
            return pc.sh(args, **kw)
        self.assertEqual(pc.prepare(self.repo, self.wt, self.head, 7, run),
                         ("ready", ""))
        self.assertFalse([a for a in calls if "fetch" in a])
        self.assertEqual(git("rev-parse", "HEAD", cwd=self.wt), self.head)

    def test_a_pull_request_that_does_not_exist_is_an_error(self):
        status, detail = pc.prepare(self.repo, self.wt, "e" * 40, 99)
        self.assertEqual(status, "error")
        self.assertIn("pull/99", detail)


class Check(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.wt = self._tmp.name

    def tearDown(self):
        self._tmp.cleanup()

    def test_every_step_runs_after_a_failure_and_is_named(self):
        run = FakeRun((lambda a: a == ["make", "vet"],
                       done([], 2, "out\n", "vet broke\n")))
        ok, results = pc.check(self.wt, {"X": "1"}, run)
        self.assertFalse(ok)
        self.assertEqual([(r["name"], r["rc"]) for r in results], [
            ("fmt-check", 0), ("vet", 2), ("test", 0), ("doc-check", 0)])
        self.assertEqual(results[1]["tail"], "out\nvet broke")
        for call in run.calls:
            self.assertEqual((call["cwd"], call["env"]), (self.wt, {"X": "1"}))

    def test_web_dependencies_only_once_the_head_has_a_web_app(self):
        os.makedirs(os.path.join(self.wt, "web"))
        pathlib.Path(self.wt, "web", "package.json").write_text("{}")
        run = FakeRun()
        ok, results = pc.check(self.wt, {}, run)
        self.assertTrue(ok)
        self.assertEqual(results[0]["name"], "web dependencies")
        self.assertEqual(len(results), 5)

    def test_the_tail_is_the_last_lines(self):
        text = "".join("line %d\n" % i for i in range(100))
        run = FakeRun((lambda a: True, done([], 1, text)))
        _, results = pc.check(self.wt, {}, run)
        self.assertEqual(results[0]["tail"].splitlines()[0],
                         "line %d" % (100 - pc.TAIL_LINES))

    def test_an_early_failure_line_is_kept_above_the_tail(self):
        text = ("--- FAIL: TestX (0.00s)\n"
                + "".join("line %d\n" % i for i in range(100)))
        run = FakeRun((lambda a: True, done([], 1, text)))
        _, results = pc.check(self.wt, {}, run)
        self.assertEqual(results[0]["tail"].splitlines()[0],
                         "--- FAIL: TestX (0.00s)")


class Tail(unittest.TestCase):

    def test_short_output_is_all_kept(self):
        self.assertEqual(pc.tail("a\nb\n", lines=3), "a\nb")

    def test_nothing_left_out_says_nothing_was(self):
        self.assertEqual(pc.tail("--- FAIL: TestA\nx\ny\n", lines=2),
                         "--- FAIL: TestA\nx\ny")

    def test_no_failure_line_before_the_tail_keeps_only_the_tail(self):
        text = "".join("line %d\n" % i for i in range(10))
        self.assertEqual(pc.tail(text, lines=3), "line 7\nline 8\nline 9")

    def test_a_go_test_failure_above_the_tail_is_kept(self):
        # A package that logs much pushes the failed test's name and
        # message out of the last TAIL_LINES lines.
        text = ("=== RUN   TestOther\n"
                "--- FAIL: TestAHoldExpires (0.02s)\n"
                "    hold_test.go:88: got 2 attempts, want 1\n"
                + "".join('time=x level=INFO msg="treatment_held" n=%d\n' % i
                          for i in range(60))
                + "FAIL\nFAIL\texample.com/pkg\t8.591s\n")
        got = pc.tail(text).splitlines()
        self.assertEqual(got[:3], [
            "--- FAIL: TestAHoldExpires (0.02s)",
            "    hold_test.go:88: got 2 attempts, want 1",
            "[23 earlier lines left out]"])
        self.assertEqual(got[3:], text.splitlines()[-pc.TAIL_LINES:])

    def test_a_subtest_failure_and_a_panic_are_kept(self):
        text = ("    --- FAIL: TestX/sub (0.00s)\n"
                "panic: runtime error: index out of range\n"
                "goroutine 7 [running]:\n"
                + "".join("filler\n" for _ in range(5)))
        self.assertEqual(pc.tail(text, lines=2).splitlines(), [
            "    --- FAIL: TestX/sub (0.00s)",
            "panic: runtime error: index out of range",
            "[4 earlier lines left out]", "filler", "filler"])

    def test_a_rust_tests_panic_and_its_message_are_kept(self):
        # A failed shard's output is printed long before the step ends:
        # the rest of the step pushes it out of the last TAIL_LINES.
        text = ("running 2 tests\n"
                "test a_bad_input_is_refused ... FAILED\n"
                "test renders ... ok\n"
                "\n"
                "failures:\n"
                "\n"
                "---- a_bad_input_is_refused stdout ----\n"
                "\n"
                "thread 'a_bad_input_is_refused' panicked at "
                "tests/cli.rs:412:5:\n"
                "assertion `left == right` failed\n"
                "  left: 0\n"
                " right: 2\n"
                "note: run with `RUST_BACKTRACE=1` environment variable "
                "to display a backtrace\n"
                "\n"
                + "".join("   Compiling crate%d v0.1.0\n" % i
                          for i in range(50)))
        got = pc.tail(text).splitlines()
        self.assertEqual(got[:7], [
            "test a_bad_input_is_refused ... FAILED",
            "---- a_bad_input_is_refused stdout ----",
            "thread 'a_bad_input_is_refused' panicked at "
            "tests/cli.rs:412:5:",
            "assertion `left == right` failed",
            "  left: 0",
            " right: 2",
            "[18 earlier lines left out]"])
        self.assertEqual(got[7:], text.splitlines()[-pc.TAIL_LINES:])

    def test_a_panics_message_stops_at_a_blank_line_and_its_cap(self):
        text = ("thread 'a' panicked at x.rs:1:1:\n"
                + "".join("message %d\n" % i
                          for i in range(pc.PANIC_MESSAGE_LINES + 2))
                + "thread 'b' panicked at y.rs:2:2:\n"
                "short\n"
                "\n"
                "not part of it\n"
                "end\n")
        got = pc.tail(text, lines=1).splitlines()
        self.assertEqual(got, (
            ["thread 'a' panicked at x.rs:1:1:"]
            + ["message %d" % i for i in range(pc.PANIC_MESSAGE_LINES)]
            + ["thread 'b' panicked at y.rs:2:2:", "short",
               "[4 earlier lines left out]", "end"]))

    def test_one_line_left_out_is_singular(self):
        text = "--- FAIL: TestX (0.00s)\nnoise\na\nb\n"
        self.assertEqual(pc.tail(text, lines=2).splitlines()[1],
                         "[1 earlier line left out]")

    def test_failure_lines_inside_the_tail_are_not_repeated(self):
        text = "noise\n--- FAIL: TestX (0.00s)\nFAIL\n"
        self.assertEqual(pc.tail(text, lines=2),
                         "--- FAIL: TestX (0.00s)\nFAIL")

    def test_failure_lines_are_capped_and_the_rest_counted(self):
        text = ("".join("--- FAIL: Test%d (0.00s)\n" % i for i in range(5))
                + "end\n")
        self.assertEqual(pc.tail(text, lines=1, failures=2).splitlines(), [
            "--- FAIL: Test0 (0.00s)", "--- FAIL: Test1 (0.00s)",
            "[3 earlier lines left out, 3 of them naming a failure]",
            "end"])

    def test_lines_that_only_mention_a_failure_are_not_kept(self):
        for line in ("ok  \tgithub.com/x/y\t0.1s", "FAIL",
                     "msg=\"--- FAIL is a word here\"",
                     "x.go:12: not a test file", "a panic: later",
                     "test renders ... ok", "test result: FAILED. 1 passed",
                     "log: thread 'x' panicked at y.rs:1:1:",
                     "---- not a header"):
            self.assertIsNone(pc.FAILURE_LINE.search(line), line)


class StepEnv(unittest.TestCase):

    def test_drops_tokens_and_this_tools_settings(self):
        env = pc.step_env("/s", {"GH_TOKEN": "x", "GITHUB_TOKEN": "y",
                                 "FORGE_STATE_DIR": "/d",
                                 "FORGE_CRON_CHECKOUT": "/c", "PATH": "/b"})
        self.assertEqual(env, {"PATH": "/b",
                               "CARGO_TARGET_DIR": "/s/cargo-target",
                               "XDG_RUNTIME_DIR": "/run/user/%d"
                               % os.getuid(),
                               "CHECK_CPUS": str(pc.check_cpus(busy=1)),
                               "TMPDIR": "/s/tmp"})

    def test_drops_what_the_project_names(self):
        # agent-forge #18: checks.drop_env, names and prefixes.
        data = example()
        data["checks"]["drop_env"] = ["INVOCATION_ID", "RECEIVER_*"]
        pc.configure(config.Project(data, None))
        self.addCleanup(pc.configure, config.Project(example(), None))
        env = pc.step_env("/s", {"INVOCATION_ID": "1", "RECEIVER_PORT": "2",
                                 "RECEIVER": "3", "GH_TOKEN": "x",
                                 "PATH": "/b"})
        self.assertNotIn("INVOCATION_ID", env)
        self.assertNotIn("RECEIVER_PORT", env)
        self.assertNotIn("GH_TOKEN", env)
        self.assertEqual((env["RECEIVER"], env["PATH"]), ("3", "/b"))

    def test_keeps_the_hosts_cargo_target_dir(self):
        env = pc.step_env("/s", {"CARGO_TARGET_DIR": "/t"})
        self.assertEqual(env["CARGO_TARGET_DIR"], "/t")

    def test_keeps_the_hosts_runtime_dir(self):
        env = pc.step_env("/s", {"XDG_RUNTIME_DIR": "/r"})
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/r")

    def test_each_slot_has_its_own_tmpdir_whatever_the_host_sets(self):
        # A step stopped by SIGTERM leaves go-build* and test directories
        # behind; they must land where the slot's next run clears them.
        self.assertEqual(pc.step_env("/s", {"TMPDIR": "/tmp"})["TMPDIR"],
                         "/s/tmp")
        self.assertEqual(pc.step_env("/s", {}, slot=2)["TMPDIR"], "/s/tmp-2")
        self.assertEqual(pc.step_env("/s", {}, slot=pc.LANE)["TMPDIR"],
                         "/s/tmp-lane")


class MemoryCap(unittest.TestCase):
    """#147: each step in its own user scope under a memory limit."""

    def test_sixteen_gigabytes_unless_the_host_says_otherwise(self):
        self.assertEqual(pc.memory_max_from_env({}), "16G")
        self.assertEqual(pc.memory_max_from_env(
            {"FORGE_PR_CHECK_MEMORY_MAX": " 8G "}), "8G")
        for off in ("off", "OFF", ""):
            self.assertIsNone(pc.memory_max_from_env(
                {"FORGE_PR_CHECK_MEMORY_MAX": off}))

    def test_the_scope_gets_the_cpu_first(self):
        # Agents building on the same host must not starve the check
        # every merge waits on.
        self.assertIn("CPUWeight=1000", pc.scope_prefix("16G"))
        self.assertIn("CPUWeight=50", pc.scope_prefix("16G", 50))
        self.assertFalse([w for w in pc.scope_prefix("16G", 0)
                          if w.startswith("CPUWeight")])

    def test_the_scope_kills_only_what_ran_out_and_never_swaps(self):
        prefix = pc.scope_prefix("16G")
        self.assertEqual(prefix[:3], ["systemd-run", "--user", "--scope"])
        self.assertEqual(prefix[-1], "--")
        for prop in ("MemoryMax=16G", "MemorySwapMax=0",
                     "OOMPolicy=continue"):
            self.assertEqual(prefix[prefix.index(prop) - 1], "-p")

    def test_a_scope_that_starts_gives_the_prefix(self):
        lines = []
        run = FakeRun()
        self.assertEqual(pc.memory_cap("4G", {"E": "1"}, run, lines.append),
                         pc.scope_prefix("4G"))
        self.assertEqual(run.calls[0]["args"],
                         pc.scope_prefix("4G") + ["true"])
        self.assertEqual(run.calls[0]["env"], {"E": "1"})
        self.assertEqual(lines, ["  each step limited to 4G"])

    def test_no_user_scope_runs_uncapped_and_says_why(self):
        lines = []
        run = FakeRun((lambda a: True, done([], 1, "", "Failed to connect "
                                            "to bus: No medium found\n")))
        self.assertEqual(pc.memory_cap("4G", {}, run, lines.append), [])
        self.assertEqual(lines, [
            "UNCAPPED: could not start a user scope (rc=1: Failed to connect "
            "to bus: No medium found); the steps run without a memory limit"])

    def test_no_limit_asks_nothing(self):
        run = FakeRun()
        self.assertEqual(pc.memory_cap(None, {}, run, print), [])
        self.assertEqual(run.calls, [])

    def test_each_step_runs_after_the_prefix(self):
        with tempfile.TemporaryDirectory() as wt:
            run = FakeRun()
            pc.check(wt, {}, run, prefix=["cap", "--"])
        self.assertEqual([c["args"] for c in run.calls], [
            ["cap", "--", "make", target]
            for target in ("fmt-check", "vet", "test", "doc-check")])


class StepCpu(unittest.TestCase):
    """Each step's CPU seconds beside its wall time (#394: work or
    waiting?)."""

    def test_each_step_gets_what_its_tree_used(self):
        used = iter([1.0, 3.5, 3.5, 3.5, 10.0, 10.25, 10.25, 20.0])
        with tempfile.TemporaryDirectory() as wt:
            _, results = pc.check(wt, {}, FakeRun(), cpu=lambda: next(used))
        self.assertEqual([(r["name"], r["cpu"]) for r in results],
                         [("fmt-check", 2.5), ("vet", 0.0), ("test", 0.25),
                          ("doc-check", 9.75)])

    def test_a_reaped_childs_cpu_counts(self):
        before = pc.children_cpu()
        pc.sh([sys.executable, "-c",
               "import time\nt = time.process_time()\n"
               "while time.process_time() - t < 0.2: pass"])
        self.assertGreaterEqual(pc.children_cpu() - before, 0.15)


class MemoryCapRealScope(unittest.TestCase):
    """The real systemd, where this user has a manager (the build check's
    host does): only the process that ran out dies, and the step goes
    on to report it."""

    def setUp(self):
        self.env = pc.step_env("/s", dict(os.environ))
        got = pc.sh(pc.scope_prefix("64M") + ["true"], env=self.env,
                    timeout=60)
        if got.returncode != 0:
            self.skipTest("no user scope here: %s" % got.stderr.strip())

    def test_the_step_outlives_the_process_that_ran_out(self):
        # Filled, not just reserved, so every page counts.
        hog = "x = b'1' * (256 << 20)"
        script = '"%s" -c "%s"; echo "hog rc=$?"' % (sys.executable, hog)
        got = pc.sh(pc.scope_prefix("64M") + ["sh", "-c", script],
                    env=self.env, timeout=120)
        self.assertEqual(got.returncode, 0, got.stderr)
        self.assertIn("hog rc=137", got.stdout)


class Worker(StateCase):
    """The worker with `prepare`'s git and the steps faked."""

    def run_for(self, fetched=SHA_A, failing=(), fail_git=None, here=True):
        """`here`: whether the head is in the object store already, so
        `prepare` doesn't fetch it."""
        def is_failing_step(args):
            return args[0] in ("make", "npm") and args[-1] in failing
        rules = [(lambda a: "FETCH_HEAD" in a, done([], 0, fetched + "\n")),
                 (is_failing_step, done([], 1, "", "it broke\n"))]
        if not here:
            rules.insert(0, (lambda a: "cat-file" in a
                             and a[-1] == SHA_A + "^{commit}",
                             done([], 128, "", "fatal: not a commit")))
        if fail_git:
            rules.insert(0, (lambda a: fail_git in a,
                             done([], 128, "", "fatal: no")))
        return FakeRun(*rules)

    def work(self, run, post=None, chain=None, sha=SHA_A, pr=7, slot=1):
        os.makedirs(os.path.join(self.state_dir, pc.slot_name("wt", slot),
                                 ".git"), exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        return pc.worker("/repo", self.state_dir, sha, pr, run,
                         self.lines.append, lambda: next(clock), post, chain,
                         slot=slot)

    def queue(self, errors=0, faults=0):
        entry = {"pr": 7, "since": pc.iso(T0 - datetime.timedelta(minutes=3))}
        if errors:
            entry["errors"] = errors
        if faults:
            entry["faults"] = faults
        self.write_state({"waiting": {SHA_A: entry}})

    def test_green_records_a_verdict_and_leaves_the_queue(self):
        self.queue()
        self.assertEqual(self.work(self.run_for()), 0)
        state = self.state()
        self.assertEqual(state["waiting"], {})
        record = state["results"][SHA_A]
        seen = pc.iso(T0 - datetime.timedelta(minutes=3))
        self.assertEqual((record["ok"], record["failed"], record["seen"]),
                         (True, [], seen))
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("VERDICT #7 aaaaaaaaaa: green waited=180s", logged)

    def test_red_names_the_failed_steps_logs_their_tail_and_exits_1(self):
        self.queue()
        self.assertEqual(self.work(self.run_for(failing=("vet", "test"))), 1)
        self.assertEqual(self.state()["results"][SHA_A]["failed"],
                         ["vet", "test"])
        self.assertIn("    | it broke", self.lines)
        self.assertEqual(self.lines.count("    | it broke"), 2)

    def test_the_steps_run_in_the_worktree_without_a_token(self):
        self.queue()
        run = self.run_for()
        with mock.patch.dict(os.environ, {"GH_TOKEN": "secret"}):
            self.work(run)
        steps = [c for c in run.calls if c["args"][0] == "make"]
        self.assertEqual(len(steps), 4)
        for call in steps:
            self.assertEqual(call["cwd"], os.path.join(self.state_dir, "wt"))
            self.assertNotIn("GH_TOKEN", call["env"])

    def test_posting_says_pending_then_the_verdict(self):
        self.queue()
        posts = []
        self.work(self.run_for(failing=("test",)),
                  post=lambda *a: posts.append(a) or True)
        self.assertEqual([p[1] for p in posts], ["pending", "failure"])
        self.assertEqual(posts[1][2], "red on build.example.org: test failed")
        self.assertIs(self.state()["results"][SHA_A]["posted"], True)

    def test_a_failed_post_is_recorded_for_the_next_tick(self):
        self.queue()
        self.work(self.run_for(), post=lambda *a: False)
        self.assertIs(self.state()["results"][SHA_A]["posted"], False)

    def test_a_moved_head_is_dropped_and_nothing_runs(self):
        self.queue()
        run = self.run_for(fetched=SHA_B, here=False)
        self.assertEqual(self.work(run), 0)
        self.assertEqual(self.state()["waiting"], {})
        self.assertFalse([c for c in run.calls if c["args"][0] == "make"])
        self.assertIn("MOVED #7", self.lines[-1])

    def test_a_prepare_failure_counts_an_error_and_runs_nothing(self):
        self.queue()
        posts, chained = [], []
        rc = self.work(self.run_for(fail_git="fetch", here=False),
                       post=lambda *a: posts.append(a) or True,
                       chain=lambda: chained.append(1) or ["x", "y"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.state()["waiting"][SHA_A]["errors"], 1)
        self.assertTrue(self.lines[-1].startswith("NO-WORKTREE #7"))
        self.assertEqual((posts, chained), ([], []))

    def test_the_last_allowed_failure_gives_up_and_posts_error(self):
        self.queue(errors=pc.MAX_ERRORS - 1)
        posts = []
        self.work(self.run_for(fail_git="fetch", here=False),
                  post=lambda *a: posts.append(a) or True)
        self.assertEqual([p[1] for p in posts], ["error"])
        self.assertIn("could not check the head out 3 times", posts[0][2])
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("GAVE-UP #7", logged)

    def faulty(self, rc=1, text="it broke\n", step="test"):
        fetched = done([], 0, SHA_A + "\n")
        return FakeRun((lambda a: "FETCH_HEAD" in a, fetched),
                       (lambda a: a[0] == "make" and a[-1] == step,
                        done([], rc, "", text)))

    def test_a_missing_command_is_a_host_fault_not_red(self):
        self.queue()
        posts, chained = [], []
        rc = self.work(self.faulty(rc=127, text="npx: not found\n"),
                       post=lambda *a: posts.append(a) or True,
                       chain=lambda: chained.append(1) or ["x", "y"])
        self.assertEqual(rc, 2)
        state = self.state()
        self.assertNotIn(SHA_A, state.get("results", {}))
        self.assertEqual(state["waiting"][SHA_A]["faults"], 1)
        self.assertNotIn("errors", state["waiting"][SHA_A])
        self.assertEqual([p[1] for p in posts], ["pending"])
        self.assertEqual(chained, [])
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("HOST-FAULT #7 aaaaaaaaaa: test: exit 127: its command "
                      "could not be run; no verdict", logged)

    def test_a_full_disk_is_a_host_fault(self):
        self.queue()
        self.assertEqual(self.work(self.faulty(
            text="error: failed to write: No space left on device (os "
                 "error 28)\n")), 2)
        self.assertNotIn(SHA_A, self.state().get("results", {}))
        self.assertIn("test: no space left on device", pathlib.Path(
            self.state_dir, "verdicts.log").read_text())

    def test_a_missing_command_behind_make_is_a_host_fault(self):
        # agent-forge #31: make exits 2 for a recipe's 127, so only its
        # output says the command could not be run.
        self.queue()
        self.assertEqual(self.work(self.faulty(rc=2, text=(
            "cd ui/console && npm ci --no-fund --no-audit\n"
            "/bin/sh: 1: npm: not found\n"
            "make: *** [Makefile:22: ui/console/node_modules/"
            ".package-lock.json] Error 127\n"))), 2)
        self.assertNotIn(SHA_A, self.state().get("results", {}))
        self.assertIn("HOST-FAULT #7 aaaaaaaaaa: test: a command behind it "
                      "could not be run: \"/bin/sh: 1: npm: not found\"",
                      pathlib.Path(self.state_dir, "verdicts.log").read_text())

    def test_a_container_that_could_not_start_is_a_host_fault(self):
        # Docker's runc failing to mount the container's root (an overlay
        # race): the step's own work never ran.
        line = ("docker: Error response from daemon: failed to create task "
                "for container: failed to create shim task: OCI runtime "
                "create failed: runc create failed: unable to start "
                "container process: error during container init: error "
                "mounting \"/usr/libexec/docker/docker-init\" to rootfs at "
                "\"/sbin/docker-init\": no such file or directory: unknown.")
        name, why = pc.host_fault([{"name": "svg", "rc": 2, "tail": (
            "1.597 2 " + line + "\n"
            "1.601 1 test result: FAILED. 0 passed; 1 failed\n"
            "1.612 2 make: *** [Makefile:498: test-svg] Error 101\n")}])
        self.assertEqual(name, "svg")
        self.assertTrue(why.startswith(
            "a container could not be started: \"1.597 2 docker: Error"), why)
        self.assertLessEqual(len(why), 340)
        self.assertIsNone(pc.host_fault([{"name": "s", "rc": 1, "tail":
            "docker: Error response from daemon: No such image: x\n"}]))

    def test_what_says_a_command_could_not_be_run(self):
        def fault(text, rc=2):
            return pc.host_fault([{"name": "s", "rc": rc, "tail": text}])
        for text in ("make: *** [Makefile:3: x] Error 127\n",
                     "make[2]: *** [sub/Makefile:9: all] Error 126\n",
                     "gmake: *** [x] Error 127\n",
                     "bash: line 1: cargo-nextest: command not found\n",
                     "/bin/bash: wasm-pack: command not found\n",
                     "sh: 1: npx: not found\n",
                     "/usr/bin/sh: 1: npx: not found\n"):
            self.assertIsNotNone(fault(text), text)
        for text in ("make: *** [Makefile:3: test] Error 2\n",
                     "make: *** [Makefile:3: test] Error 1270\n",
                     "--- FAIL: TestOpen: file not found\n",
                     "    assert 'x: command not found' in out\n",
                     "warning: sh: 1: npx: not found, retrying\n"):
            self.assertIsNone(fault(text), text)
        self.assertIsNone(fault("make: *** [x] Error 127\n", rc=0))

    def test_repeated_host_faults_give_up_with_error(self):
        self.queue(faults=pc.MAX_ERRORS - 1)
        posts, chained = [], []
        self.work(self.faulty(rc=126),
                  post=lambda *a: posts.append(a) or True,
                  chain=lambda: chained.append(1) or ["x", "y"])
        self.assertEqual([p[1] for p in posts], ["pending", "error"])
        self.assertIn("the host failed 3 times; the last: test: exit 126",
                      posts[1][2])
        self.assertEqual(chained, [1])
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("GAVE-UP #7 aaaaaaaaaa after 3 host faults", logged)

    def test_an_ordinary_failure_is_still_red(self):
        self.queue()
        self.assertEqual(self.work(self.faulty(rc=2)), 1)
        self.assertEqual(self.state()["results"][SHA_A]["failed"], ["test"])

    def test_busy_while_another_check_holds_the_lock(self):
        held = pc.try_lock(os.path.join(self.state_dir, "worker.lock"))
        try:
            run = self.run_for()
            self.assertEqual(self.work(run), 0)
        finally:
            held.close()
        self.assertEqual(run.calls, [])
        self.assertTrue(self.lines[0].startswith("BUSY"))

    def test_slot_two_uses_its_own_lock_worktree_and_cache(self):
        seen = []

        def peek(args):
            seen.append((pc.running(self.state_dir),
                         pc.running(self.state_dir, 2)))
            return done(args)
        held = pc.try_lock(os.path.join(self.state_dir, "worker.lock"))
        self.addCleanup(held.close)
        run = self.run_for()
        run.rules.insert(0, (lambda a: a == ["make", "test"], peek))
        # A host's own CARGO_TARGET_DIR wins (step_env), and these tests
        # run inside a check that sets one.
        env = {k: v for k, v in os.environ.items()
               if k != "CARGO_TARGET_DIR"}
        with mock.patch.dict(os.environ, env, clear=True):
            self.work(run, slot=2)
        self.assertEqual(seen, [("", "#7 %s %s pid=%d"
                                 % (SHA_A, pc.iso(T0), os.getpid()))])
        test, = [c for c in run.calls if c["args"] == ["make", "test"]]
        self.assertEqual(test["cwd"], os.path.join(self.state_dir, "wt-2"))
        self.assertEqual(test["env"]["CARGO_TARGET_DIR"],
                         os.path.join(self.state_dir, "cargo-target-2"))

    def test_the_lock_names_the_check_while_it_runs(self):
        seen = []

        def peek(args):
            if args == ["make", "test"]:
                seen.append(pc.running(self.state_dir))
            return done(args)
        run = self.run_for()
        run.rules.insert(0, (lambda a: a[0] == "make", peek))
        self.work(run)
        self.assertEqual(seen, ["#7 %s %s pid=%d"
                                % (SHA_A, pc.iso(T0), os.getpid())])
        self.assertIsNone(pc.running(self.state_dir))

    def test_the_lock_goes_even_when_a_step_raises(self):
        def boom(args):
            raise OSError("no make")
        run = self.run_for()
        run.rules.insert(0, (lambda a: a[0] == "make", boom))
        with self.assertRaises(OSError):
            self.work(run)
        self.assertIsNone(pc.running(self.state_dir))

    def test_the_chain_starts_after_the_lock_is_free(self):
        self.queue()
        free = []
        chain = lambda: free.append(pc.running(self.state_dir)) or [
            "run", "forge-pr-check", "--post"]
        self.work(self.run_for(), chain=chain)
        self.assertEqual(free, [None])
        self.assertEqual(self.lines[-1], "CHAINED forge-pr-check --post")

    def test_a_memory_limit_prefixes_every_step(self):
        self.queue()
        run = self.run_for()
        os.makedirs(os.path.join(self.state_dir, "wt", ".git"), exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        pc.worker("/repo", self.state_dir, SHA_A, 7, run, self.lines.append,
                  lambda: next(clock), memory_max="12G")
        prefix = pc.scope_prefix("12G")
        steps = [c["args"][len(prefix):] for c in run.calls
                 if c["args"][:len(prefix)] == prefix]
        self.assertEqual(steps, [["true"], ["make", "fmt-check"],
                                 ["make", "vet"], ["make", "test"],
                                 ["make", "doc-check"]])
        self.assertIn("  each step limited to 12G", self.lines)

    def test_no_memory_limit_runs_the_steps_bare(self):
        self.queue()
        run = self.run_for()
        self.work(run)
        self.assertFalse([c for c in run.calls
                          if c["args"][0] == "systemd-run"])

    def test_sigterm_stops_the_run_frees_the_slot_and_chains(self):
        self.queue()
        self.addCleanup(signal.signal, signal.SIGTERM,
                        signal.getsignal(signal.SIGTERM))
        held = []

        def superseded(args):
            os.kill(os.getpid(), signal.SIGTERM)
            return done(args)
        run = self.run_for()
        run.rules.insert(0, (lambda a: a == ["make", "test"], superseded))
        chain = lambda: held.append(pc.running(self.state_dir)) or [
            "run", "forge-pr-check", "--post"]
        self.assertEqual(self.work(run, chain=chain), 3)
        state = self.state()
        self.assertEqual((state["waiting"], state["results"]), ({}, {}))
        self.assertFalse([c for c in run.calls
                          if c["args"] == ["make", "doc-check"]])
        self.assertIn("STOPPED #7 aaaaaaaaaa after 600s: superseded; "
                      "no verdict", pathlib.Path(
                          self.state_dir, "verdicts.log").read_text())
        self.assertEqual(held, [None])
        self.assertEqual(self.lines[-1], "CHAINED forge-pr-check --post")

    def test_the_steps_get_a_fresh_tmpdir_which_goes_afterwards(self):
        self.queue()
        tmp = os.path.join(self.state_dir, "tmp")
        os.makedirs(os.path.join(tmp, "go-build123"))
        seen = []

        def look(args):
            seen.append(os.listdir(tmp))
            return done(args)
        run = self.run_for()
        run.rules.insert(0, (lambda a: a == ["make", "test"], look))
        self.work(run)
        self.assertEqual(seen, [[]])
        steps = [c for c in run.calls if c["args"] == ["make", "test"]]
        self.assertEqual(steps[0]["env"]["TMPDIR"], tmp)
        self.assertFalse(os.path.exists(tmp))

    def test_a_stopped_run_leaves_no_tmpdir(self):
        self.queue()
        self.addCleanup(signal.signal, signal.SIGTERM,
                        signal.getsignal(signal.SIGTERM))
        tmp = os.path.join(self.state_dir, "tmp")

        def superseded(args):
            os.makedirs(os.path.join(tmp, "go-build456"))
            os.kill(os.getpid(), signal.SIGTERM)
            return done(args)
        run = self.run_for()
        run.rules.insert(0, (lambda a: a == ["make", "test"], superseded))
        self.assertEqual(self.work(run), 3)
        self.assertFalse(os.path.exists(tmp))

    def test_the_sigterm_handler_is_restored_afterwards(self):
        self.queue()
        before = signal.getsignal(signal.SIGTERM)
        self.work(self.run_for())
        self.assertIs(signal.getsignal(signal.SIGTERM), before)

    def test_nothing_is_posted_for_a_stopped_run(self):
        self.queue()
        self.addCleanup(signal.signal, signal.SIGTERM,
                        signal.getsignal(signal.SIGTERM))
        posts = []

        def superseded(args):
            os.kill(os.getpid(), signal.SIGTERM)
            return done(args)
        run = self.run_for()
        run.rules.insert(0, (lambda a: a == ["make", "test"], superseded))
        self.work(run, post=lambda *a: posts.append(a) or True)
        self.assertEqual([p[1] for p in posts], ["pending"])

    def test_a_failing_chain_is_reported_not_raised(self):
        self.queue()

        def chain():
            raise OSError("no fork")
        self.assertEqual(self.work(self.run_for(), chain=chain), 0)
        self.assertEqual(self.lines[-1], "CHAIN-FAILED: OSError: no fork")


class HolderFields(unittest.TestCase):

    def test_a_line_naming_its_process(self):
        self.assertEqual(
            pc.holder_fields("#7 %s 2026-10-09T12:00:00Z pid=99" % SHA_A),
            (7, SHA_A, "2026-10-09T12:00:00Z", 99, False))

    def test_a_prediction(self):
        self.assertEqual(
            pc.holder_fields("#7 %s 2026-10-09T12:00:00Z pid=99 ahead"
                             % SHA_A)[3:], (99, True))

    def test_a_line_from_before_pids(self):
        self.assertEqual(pc.holder_fields("#7 %s T" % SHA_A),
                         (7, SHA_A, "T", None, False))

    def test_lines_that_name_no_check(self):
        for line in (None, "", "someone", "#x %s T" % SHA_A, "#7 %s" % SHA_A):
            self.assertIsNone(pc.holder_fields(line), line)


class StopWorker(unittest.TestCase):
    """Real processes: a group leader and its child both get SIGTERM."""

    def test_the_whole_group_is_stopped(self):
        leader = subprocess.Popen(
            ["sh", "-c", "sleep 30 & echo $!; wait"], start_new_session=True,
            stdout=subprocess.PIPE, text=True)
        self.addCleanup(leader.stdout.close)
        self.addCleanup(leader.wait)
        child = int(leader.stdout.readline())
        self.assertTrue(pc.stop_worker(leader.pid))
        self.assertEqual(leader.wait(timeout=10), -signal.SIGTERM)
        for _ in range(100):
            try:
                os.kill(child, 0)
            except ProcessLookupError:
                break
            # The orphaned child is reaped by init; give it a moment.
            time.sleep(0.05)
        else:
            self.fail("the step under the worker was not stopped")

    def test_a_gone_process_is_false(self):
        gone = subprocess.Popen(["true"])
        gone.wait()
        self.assertFalse(pc.stop_worker(gone.pid))


class StartNextTick(unittest.TestCase):

    def test_starts_its_own_cron_line_detached_on_its_checkout(self):
        calls = []
        argv = _real_start_next_tick(
            popen=lambda argv, **kw: calls.append((argv, kw)),
            checkout="/cron")
        self.assertEqual(argv, [pc.CRON_RUN, "forge-pr-check", "--post"])
        self.assertEqual(os.path.basename(pc.CRON_RUN), "forge-cron-run")
        (got, kw), = calls
        self.assertEqual(got, argv)
        self.assertEqual(kw["env"]["FORGE_CRON_CHECKOUT"], "/cron")
        self.assertTrue(kw["start_new_session"] and kw["close_fds"])


    def test_carries_the_cron_lines_state_and_merger_dirs(self):
        # A chained tick on the default directories would be a second
        # queue posting to the same context.
        argv = _real_start_next_tick(popen=lambda argv, **kw: None,
                                     checkout="/cron", state_dir="/s",
                                     merger_dir="/m")
        self.assertEqual(argv[1:], ["forge-pr-check", "--post",
                                    "--state-dir", "/s", "--merger-dir",
                                    "/m"])


class Main(StateCase):

    def setUp(self):
        super().setUp()
        self.checkout = os.path.join(self._tmp.name, "cron")
        os.makedirs(os.path.join(self.checkout, ".agent-forge"))
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(example(), f)
        self.environ = {"HOME": self._tmp.name,
                        "FORGE_CRON_CHECKOUT": self.checkout}

    def tearDown(self):
        pc.configure(config.Project(example(), None))
        super().tearDown()

    def fake_run(self, mint_rc=0):
        return FakeRun(
            (lambda a: "--show-toplevel" in a,
             done([], 0, self.checkout + "\n")),
            (is_mint, done([], mint_rc, "tok\n" if mint_rc == 0 else "")))

    def main(self, *argv, mint_rc=0, spawn=None, environ=None, state=True):
        run = self.fake_run(mint_rc)
        self.spawned = []
        rc = pc.main((["--state-dir", self.state_dir] if state else [])
                     + list(argv), run,
                     spawn or (lambda *a: self.spawned.append(a)),
                     self.lines.append,
                     environ=dict(self.environ, **(environ or {})))
        return rc, run

    def test_no_token_is_2(self):
        rc, _ = self.main(mint_rc=6)
        self.assertEqual(rc, 2)
        self.assertEqual(self.lines, ["NO-TOKEN: could not mint Merger's "
                                      "token"])

    def test_a_tick_reads_as_the_merger(self):
        rc, run = self.main()
        self.assertEqual(rc, 0)
        listing = [c for c in run.calls if is_pulls(c["args"])]
        self.assertEqual(listing[0]["env"]["GH_TOKEN"], "tok")

    def test_the_mint_is_told_which_configuration(self):
        _, run = self.main()
        mint = [c for c in run.calls if is_mint(c["args"])][0]
        self.assertEqual(mint["args"][0],
                         os.path.join(pc.BIN, "forge-app-token"))
        self.assertEqual(mint["args"][1:], ["--token", "Merger"])
        self.assertEqual(mint["env"]["FORGE_CONFIG"],
                         os.path.join(self.checkout, config.CONFIG_NAME))

    def test_outside_a_checkout(self):
        run = FakeRun((lambda a: "--show-toplevel" in a, done([], 128)))
        rc = pc.main(["--state-dir", self.state_dir], run, None,
                     self.lines.append, environ=self.environ)
        self.assertEqual(rc, 2)
        self.assertTrue(self.lines[0].startswith("NO-REPO"))

    def test_no_checkout_at_all(self):
        rc = pc.main(["--state-dir", self.state_dir], None, None,
                     self.lines.append, environ={})
        self.assertEqual(rc, 2)
        self.assertIn("FORGE_CRON_CHECKOUT", self.lines[0])

    def test_a_bad_configuration(self):
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            f.write("{")
        rc, _ = self.main()
        self.assertEqual(rc, 2)
        self.assertIn("NO-CONFIG", self.lines[0])

    def test_worker_needs_pr(self):
        with mock.patch("sys.stderr"), self.assertRaises(SystemExit):
            pc.main(["--state-dir", self.state_dir, "--worker", SHA_A],
                    environ=self.environ)

    def test_a_posting_worker_chains_and_a_plain_one_does_not(self):
        for argv, chains in (([], False), (["--post"], True)):
            with mock.patch.object(pc, "worker", return_value=0) as worker:
                self.main("--worker", SHA_A, "--pr", "7", *argv)
            chain = worker.call_args.kwargs["chain"]
            self.assertEqual(chain is not None, chains)
            self.assertEqual(worker.call_args.kwargs["post"] is None,
                             not argv)
        started = []
        with mock.patch.object(pc, "start_next_tick",
                               lambda **kw: started.append(kw)):
            chain()
        self.assertEqual(started, [{"checkout": self.checkout,
                                    "state_dir": self.state_dir,
                                    "merger_dir": None}])

    def test_the_chained_tick_reads_the_cron_lines_merger(self):
        self.addCleanup(setattr, pc, "MERGER_DIR_ARG", None)
        with mock.patch.object(pc, "worker", return_value=0) as worker:
            self.main("--worker", SHA_A, "--pr", "7", "--post",
                      "--merger-dir", "/m")
        started = []
        with mock.patch.object(pc, "start_next_tick",
                               lambda **kw: started.append(kw)):
            worker.call_args.kwargs["chain"]()
        self.assertEqual(started[0]["merger_dir"], "/m")

    def test_the_worker_takes_its_memory_limit_from_the_environment(self):
        for environ, limit in (({}, "16G"),
                               ({"FORGE_PR_CHECK_MEMORY_MAX": "off"}, None)):
            with mock.patch.object(pc, "worker", return_value=0) as worker:
                self.main("--worker", SHA_A, "--pr", "7", environ=environ)
            self.assertEqual(worker.call_args.kwargs["memory_max"], limit)

    def test_a_tick_spawns_workers_on_its_checkout(self):
        with mock.patch.object(pc, "open_heads",
                               return_value=[(7, SHA_A, REPO, False)]):
            self.main()
        self.assertEqual(self.spawned, [
            (self.state_dir, SHA_A, 7, False, 1, self.checkout)])

    def test_the_slots_come_from_the_environment(self):
        with mock.patch.object(pc, "tick", return_value=0) as tick:
            self.main(environ={"FORGE_PR_CHECK_SLOTS": "4"})
        self.assertEqual(tick.call_args.kwargs["slots"], 4)

    def write_config(self, data):
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(data, f)

    def test_the_mergers_directory_is_read_only_when_configured(self):
        data = example()
        data["checks"]["merger_first"] = False
        del data["merge"]["test_ahead"]
        self.write_config(data)
        with mock.patch.object(pc, "tick", return_value=0) as tick:
            self.main()
        self.assertIsNone(tick.call_args.kwargs["merger_dir"])
        mine = os.path.join(self._tmp.name, ".local", "state", "agent-forge",
                            "widget", "merger")
        # merger_first on its own, and test_ahead, which implies it.
        first = example()
        del first["merge"]["test_ahead"]
        ahead = example()
        del ahead["checks"]["merger_first"]
        for data in (first, ahead):
            self.write_config(data)
            for argv, where in (([], mine), (["--merger-dir", "/m"], "/m")):
                with mock.patch.object(pc, "tick", return_value=0) as tick:
                    self.main(*argv)
                self.assertEqual(tick.call_args.kwargs["merger_dir"], where)

    def test_a_prediction_worker_is_started_with_ahead(self):
        with mock.patch.object(pc, "worker", return_value=0) as worker:
            self.main("--worker", SHA_A, "--pr", "7", "--ahead", "--post")
        self.assertIs(worker.call_args.kwargs["ahead"], True)
        with mock.patch.object(pc, "worker", return_value=0) as worker:
            self.main("--worker", SHA_A, "--pr", "7")
        self.assertIs(worker.call_args.kwargs["ahead"], False)

    def test_the_state_directory_defaults_under_the_home(self):
        with mock.patch.object(pc, "tick", return_value=0) as tick:
            self.main(state=False)
        self.assertEqual(tick.call_args.args[0], os.path.join(
            self._tmp.name, ".local", "state", "agent-forge", "widget",
            "pr-check"))


# merge.test_ahead: the Merger's predictions, and green by tree.

SHA_P = "d" * 40
SHA_Q = "e" * 40
TREE_1 = "1" * 40
TREE_2 = "2" * 40


def entry(pr, sha, tree, **more):
    return dict({"pr": pr, "head": "f" * 40, "sha": sha, "tree": tree},
                **more)


def write_ahead(merger_dir, entries, when=T0, repo="/repo", batch=None,
                later=()):
    said = {"at": pc.iso(when), "base": "0" * 40, "repo": repo,
            "entries": entries}
    if batch:
        # designs/0032: the first `batch` entries are the batch.
        said["batch"] = {"size": batch,
                         "trees": [e["tree"] for e in entries[:batch]]}
    if later:
        # designs/0035 part 2: the batches after it, by size.
        said["later"], at = [], batch
        for size in later:
            said["later"].append({"size": size, "trees": [
                e["tree"] for e in entries[at:at + size]]})
            at += size
    with open(os.path.join(merger_dir, "ahead.json"), "w") as handle:
        json.dump(said, handle)


def verdict(pr=4, ok=True, tree=TREE_1, finished=T0, **more):
    """A verdict here, on this host with these steps."""
    return dict({"pr": pr, "ok": ok, "failed": [] if ok else ["test"],
                 "seconds": 60, "seen": pc.iso(finished),
                 "started": pc.iso(finished), "finished": pc.iso(finished),
                 "tree": tree, "host": socket.gethostname(),
                 "steps": pc.steps_digest()}, **more)


def empty():
    return {"waiting": {}, "results": {}, "refused": {}}


TREE_3 = "3" * 40


class Batch(unittest.TestCase):
    """designs/0032: the Merger's batch, of which only the top is run."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        self.entries = [entry(7, SHA_P, TREE_1), entry(8, SHA_Q, TREE_3),
                        entry(9, SHA_C, TREE_2)]

    def test_the_batch_is_read(self):
        write_ahead(self.dir, self.entries, batch=2)
        self.assertEqual(pc.merger_batch(self.dir, T0), self.entries[:2])

    def test_no_batch_of_two_or_more_is_none(self):
        for size in (None, 1):
            write_ahead(self.dir, self.entries, batch=size)
            self.assertEqual(pc.merger_batch(self.dir, T0), [])
        self.assertEqual(pc.merger_batch(os.path.join(self.dir, "no"), T0),
                         [])

    def test_a_batch_whose_trees_differ_is_none(self):
        write_ahead(self.dir, self.entries, batch=2)
        with open(os.path.join(self.dir, "ahead.json")) as handle:
            said = json.load(handle)
        said["batch"]["trees"] = [TREE_1, TREE_2]
        with open(os.path.join(self.dir, "ahead.json"), "w") as handle:
            json.dump(said, handle)
        self.assertEqual(pc.merger_batch(self.dir, T0), [])

    def test_the_top_is_the_front(self):
        write_ahead(self.dir, self.entries, batch=2)
        self.assertTrue(pc.is_front(self.dir, T0, SHA_Q, 8, ahead=True))
        self.assertFalse(pc.is_front(self.dir, T0, SHA_P, 7, ahead=True))

    def test_only_the_top_is_run(self):
        state = empty()
        self.assertEqual(pc.next_ahead(state, self.entries, when=T0,
                                       batch=self.entries[:2]), (SHA_Q, 8))
        state["results"][SHA_Q] = verdict(pr=8, tree=TREE_3)
        self.assertIsNone(pc.next_ahead(state, self.entries, when=T0,
                                        batch=self.entries[:2]))

    def test_a_top_that_is_a_heads_own_tree_is_run_still(self):
        top = dict(self.entries[1], same_as_head=True)
        self.assertEqual(pc.next_ahead(empty(), self.entries, when=T0,
                                       batch=[self.entries[0], top]),
                         (SHA_Q, 8))

    def test_a_covered_heads_own_tree_is_run(self):
        # Its check is over, and no run tested its tree.
        own = [entry(7, SHA_P, TREE_1, same_as_head=True, head=SHA_A)]
        state = empty()
        self.assertIsNone(pc.next_ahead(state, own, when=T0))
        state["results"][SHA_A] = verdict(pr=7, covered="#7..#8",
                                          matched=SHA_Q)
        self.assertEqual(pc.next_ahead(state, own, when=T0), (SHA_P, 7))

    def test_a_cover_is_not_a_verdict_on_the_tree(self):
        state = empty()
        state["results"][SHA_A] = verdict(covered="#7..#8", matched=SHA_Q)
        self.assertIsNone(pc.tested(state, TREE_1, T0))

    def later(self):
        """Batches of 2 and 1 and 1: #7..#8, then #9, then #10."""
        entries = self.entries + [entry(10, SHA_A, "4" * 40)]
        write_ahead(self.dir, entries, batch=2, later=(1, 1))
        return entries

    def test_the_later_tops_are_read(self):
        entries = self.later()
        self.assertEqual(pc.merger_later(self.dir, T0), entries[2:])
        # One whose trees aren't those that follow ends the list.
        with open(os.path.join(self.dir, "ahead.json")) as handle:
            said = json.load(handle)
        said["later"][1]["trees"] = [TREE_1]
        with open(os.path.join(self.dir, "ahead.json"), "w") as handle:
            json.dump(said, handle)
        self.assertEqual(pc.merger_later(self.dir, T0), entries[2:3])
        write_ahead(self.dir, entries, batch=2)
        self.assertEqual(pc.merger_later(self.dir, T0), [])

    def test_a_later_top_runs_while_the_top_is_running(self):
        entries = self.later()
        tops = pc.merger_later(self.dir, T0)
        state = empty()
        self.assertEqual(pc.next_ahead(state, entries, when=T0,
                                       batch=entries[:2], later=tops),
                         (SHA_Q, 8))
        self.assertEqual(pc.next_ahead(state, entries, busy={SHA_Q},
                                       when=T0, batch=entries[:2],
                                       later=tops), (SHA_C, 9))
        self.assertEqual(pc.next_ahead(state, entries, busy={SHA_Q, SHA_C},
                                       when=T0, batch=entries[:2],
                                       later=tops), (SHA_A, 10))
        # Not without them: the caller passes none when a head waits.
        self.assertIsNone(pc.next_ahead(state, entries, busy={SHA_Q},
                                        when=T0, batch=entries[:2]))

    def test_nothing_after_a_red_top(self):
        entries = self.later()
        tops = pc.merger_later(self.dir, T0)
        state = empty()
        state["results"][SHA_Q] = verdict(pr=8, tree=TREE_3, ok=False)
        self.assertIsNone(pc.next_ahead(state, entries, when=T0,
                                        batch=entries[:2], later=tops))
        state = empty()
        state["results"][SHA_C] = verdict(pr=9, tree=TREE_2, ok=False)
        self.assertIsNone(pc.next_ahead(state, entries, busy={SHA_Q},
                                        when=T0, batch=entries[:2],
                                        later=tops))

    def test_the_status_says_which_batch(self):
        record = verdict(pr=7, matched=SHA_Q, covered="#7..#8")
        state, said = pc.final_status(record)
        self.assertEqual(state, "success")
        self.assertIn("covered by batch #7..#8 (tested as %s)" % SHA_Q[:10],
                      said)


class MergerAhead(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name

    def test_fresh_entries_are_read(self):
        write_ahead(self.dir, [entry(2, SHA_P, TREE_1)])
        self.assertEqual(pc.merger_ahead(self.dir, T0),
                         [entry(2, SHA_P, TREE_1)])

    def test_a_stale_file_is_ignored(self):
        write_ahead(self.dir, [entry(2, SHA_P, TREE_1)])
        self.assertEqual(len(pc.merger_ahead(self.dir, T0 + pc.MERGER_FRESH)),
                         1)
        self.assertEqual(pc.merger_ahead(
            self.dir, T0 + pc.MERGER_FRESH + datetime.timedelta(seconds=1)),
            [])

    def test_another_checkouts_predictions_are_ignored(self):
        # Its commits are in an object store this worktree doesn't share.
        write_ahead(self.dir, [entry(2, SHA_P, TREE_1)], repo="/elsewhere")
        self.assertEqual(pc.merger_ahead(self.dir, T0, "/repo"), [])
        self.assertEqual(len(pc.merger_ahead(self.dir, T0, "/elsewhere")), 1)

    def test_malformed_entries_are_dropped(self):
        write_ahead(self.dir, [entry("2", SHA_P, TREE_1),
                               entry(True, SHA_P, TREE_1),
                               entry(3, "short", TREE_1),
                               entry(4, SHA_Q, ""),
                               entry(5, SHA_Q, TREE_2)])
        self.assertEqual(pc.merger_ahead(self.dir, T0),
                         [entry(5, SHA_Q, TREE_2)])

    def test_unreadable_or_absent_is_empty(self):
        self.assertEqual(pc.merger_ahead(self.dir, T0), [])
        self.assertEqual(pc.merger_ahead(None, T0), [])
        for bad in ("{", "[]", '{"at": "x", "entries": []}',
                    '{"at": "%s"}' % pc.iso(T0),
                    '{"at": "%s", "entries": [1]}' % pc.iso(T0)):
            with open(os.path.join(self.dir, "ahead.json"), "w") as handle:
                handle.write(bad)
            self.assertEqual(pc.merger_ahead(self.dir, T0), [], bad)


class Tested(unittest.TestCase):

    def state(self, **results):
        return dict(empty(), results=results)

    def test_a_verdict_on_the_tree_here(self):
        state = self.state(**{SHA_A: verdict()})
        self.assertEqual(pc.tested(state, TREE_1, T0), SHA_A)
        self.assertEqual(pc.tested(state, TREE_1, T0, ok=True), SHA_A)
        self.assertIsNone(pc.tested(state, TREE_2, T0))
        self.assertIsNone(pc.tested(state, None, T0))

    def test_green_only_when_asked(self):
        state = self.state(**{SHA_A: verdict(ok=False)})
        self.assertEqual(pc.tested(state, TREE_1, T0), SHA_A)
        self.assertIsNone(pc.tested(state, TREE_1, T0, ok=True))

    def test_not_another_hosts_or_another_step_lists(self):
        for other in (verdict(host="elsewhere"), verdict(steps="other"),
                      verdict(tree=None)):
            self.assertIsNone(pc.tested(self.state(**{SHA_A: other}),
                                        TREE_1, T0), other)

    def test_a_different_step_list_is_a_different_digest(self):
        self.assertNotEqual(pc.steps_digest(),
                            pc.steps_digest([("test", ["make"], None)]))
        self.assertEqual(pc.steps_digest(), pc.steps_digest(list(pc.STEPS)))

    def test_the_newest_green_is_named(self):
        hour = datetime.timedelta(hours=1)
        state = self.state(**{SHA_B: verdict(finished=T0 - 5 * hour),
                              SHA_A: verdict(finished=T0 - hour)})
        self.assertEqual(pc.tested(state, TREE_1, T0, ok=True), SHA_A)

    def test_a_verdict_older_than_the_limit_does_not_count(self):
        # The step digest can't see a toolchain upgrade.
        limit = pc.MATCH_MAX_AGE
        state = self.state(**{SHA_A: verdict()})
        self.assertEqual(pc.tested(state, TREE_1, T0 + limit, ok=True), SHA_A)
        later = T0 + limit + datetime.timedelta(seconds=1)
        self.assertIsNone(pc.tested(state, TREE_1, later, ok=True))
        self.assertIsNone(pc.tested(state, TREE_1, later))

    def test_a_verdict_from_the_future_or_undated_does_not_count(self):
        state = self.state(**{SHA_A: verdict(
            finished=T0 + datetime.timedelta(minutes=5))})
        self.assertIsNone(pc.tested(state, TREE_1, T0))
        bad = verdict()
        bad["finished"] = "yesterday"
        self.assertIsNone(pc.tested(self.state(**{SHA_A: bad}), TREE_1, T0))


class NextAhead(unittest.TestCase):
    AHEAD = [entry(2, SHA_P, TREE_1), entry(3, SHA_Q, TREE_2)]

    def test_the_first_entry_in_order(self):
        self.assertEqual(pc.next_ahead(empty(), self.AHEAD, when=T0),
                         (SHA_P, 2))

    def test_an_entry_whose_tree_a_slot_is_checking_is_skipped(self):
        # Two predictions can differ only in their parents: #674's ran
        # side by side at 09:00Z on 2026-10-12.
        self.assertEqual(pc.next_ahead(empty(), self.AHEAD, when=T0,
                                       busy_trees={TREE_1}), (SHA_Q, 3))

    def test_busy_trees_asks_git_for_each_running_commit(self):
        run = FakeRun((lambda a: SHA_P + "^{tree}" in a,
                       done([], 0, TREE_1 + "\n")),
                      (lambda a: True, done([], 128, "", "bad")))
        self.assertEqual(pc.busy_trees({SHA_P, SHA_Q}, "/repo", run),
                         frozenset({TREE_1}))
        self.assertEqual(pc.busy_trees({SHA_P}, None, run), frozenset())

    def test_an_entry_with_a_verdict_is_not_run_again(self):
        state = empty()
        state["results"][SHA_P] = dict(verdict(pr=2), ahead=True)
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_Q, 3))

    def test_an_entry_run_without_a_tree_is_not_run_again(self):
        state = empty()
        state["results"][SHA_P] = verdict(pr=2, tree=None, ahead=True)
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_Q, 3))

    def test_an_entry_whose_tree_is_green_is_skipped(self):
        state = empty()
        state["results"][SHA_A] = verdict(pr=9)
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_Q, 3))

    def test_nothing_after_an_entry_whose_tree_is_red(self):
        # Every later prediction contains it: red too, a wasted run.
        state = empty()
        state["results"][SHA_A] = verdict(pr=9, ok=False)
        self.assertIsNone(pc.next_ahead(state, self.AHEAD, when=T0))

    def test_a_red_heads_tree_stops_the_list_too(self):
        ahead = [entry(2, SHA_P, TREE_1, same_as_head=True)] + self.AHEAD[1:]
        state = empty()
        state["results"][SHA_A] = verdict(pr=2, ok=False)
        self.assertIsNone(pc.next_ahead(state, ahead, when=T0))

    def test_a_red_tree_green_since_does_not_stop_the_list(self):
        # A flake: the tree is green after all.
        state = empty()
        state["results"][SHA_A] = verdict(pr=9, ok=False)
        state["results"][SHA_B] = verdict(pr=9)
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_Q, 3))

    def test_another_hosts_red_does_not_stop_the_list(self):
        state = empty()
        state["results"][SHA_A] = verdict(pr=9, ok=False, host="elsewhere")
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_P, 2))

    def test_another_hosts_verdict_does_not_count(self):
        state = empty()
        state["results"][SHA_A] = verdict(pr=9, host="elsewhere")
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_P, 2))

    def test_the_heads_own_tree_is_left_to_the_heads_check(self):
        ahead = [entry(2, SHA_P, TREE_1, same_as_head=True)] + self.AHEAD[1:]
        self.assertEqual(pc.next_ahead(empty(), ahead, when=T0), (SHA_Q, 3))

    def test_one_running_or_just_started_is_not_started_again(self):
        self.assertEqual(pc.next_ahead(empty(), self.AHEAD, {SHA_P}, T0),
                         (SHA_Q, 3))
        state = dict(empty(), ahead_started={SHA_P: pc.iso(T0)})
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_Q, 3))
        self.assertEqual(pc.next_ahead(state, self.AHEAD,
                                       when=T0 + pc.START_GRACE), (SHA_P, 2))

    def test_an_entry_that_errored_out_is_skipped(self):
        state = dict(empty(), ahead_errors={SHA_P: pc.MAX_ERRORS})
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_Q, 3))
        state["ahead_errors"] = {SHA_P: pc.MAX_ERRORS - 1}
        self.assertEqual(pc.next_ahead(state, self.AHEAD, when=T0),
                         (SHA_P, 2))

    def test_nothing_left(self):
        self.assertIsNone(pc.next_ahead(empty(), [], when=T0))

    def test_what_the_merger_no_longer_lists_is_forgotten(self):
        state = dict(empty(), ahead_started={SHA_P: "x", SHA_C: "y"},
                     ahead_errors={SHA_C: 1})
        pc.forget_ahead(state, self.AHEAD)
        self.assertEqual(state["ahead_started"], {SHA_P: "x"})
        self.assertNotIn("ahead_errors", state)


class TickAhead(Tick):

    def merger(self, pr=7, entries=(entry(2, SHA_P, TREE_1),)):
        merger = self.merger_says(pr)
        write_ahead(merger, list(entries), when=T0 - datetime.timedelta(
            minutes=1))
        return merger

    def test_a_prediction_runs_while_the_mergers_head_is_not_waiting(self):
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        self.tick([(8, SHA_B, REPO, "false")], merger_dir=self.merger(),
                  post=lambda *a: True)
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_P, 2, True, 1, None, True)])
        self.assertEqual(self.lines[-1], "STARTED-AHEAD #2 dddddddddd, the "
                         "Merger's prediction; 1 waiting")
        self.assertEqual(self.state()["ahead_started"], {SHA_P: pc.iso(T0)})

    def test_the_mergers_own_head_comes_first(self):
        self.tick([(7, SHA_A, REPO, "false")], merger_dir=self.merger())
        self.assertEqual(self.spawned, [(self.state_dir, SHA_A, 7, False, 1)])

    def test_the_mergers_head_running_leaves_the_other_slot_to_a_prediction(
            self):
        self.hold(1, "#7 %s %s" % (SHA_A, pc.iso(T0)))
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                  merger_dir=self.merger())
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_P, 2, False, 2, None, True)])

    def test_a_prediction_started_is_not_started_in_another_slot(self):
        merger = self.merger()
        self.tick([], merger_dir=merger)
        self.assertEqual(len(self.spawned), 1)
        self.tick([], merger_dir=merger)
        self.assertEqual(self.spawned, [])

    def test_one_running_is_not_started_again(self):
        self.hold(1, "#2 %s %s" % (SHA_P, pc.iso(T0)))
        self.tick([], merger_dir=self.merger(
            entries=[entry(2, SHA_P, TREE_1), entry(3, SHA_Q, TREE_2)]))
        self.assertEqual(self.spawned,
                         [(self.state_dir, SHA_Q, 3, False, 2, None, True)])

    def test_predictions_go_before_other_heads_and_then_they_resume(self):
        self.write_state({"waiting": {SHA_B: {"pr": 8, "since": pc.iso(T0)}}})
        merger = self.merger()
        self.tick([(8, SHA_B, REPO, "false")], merger_dir=merger)
        self.assertEqual(self.spawned[0][1], SHA_P)
        # Once tested, the prediction is spent and the queue goes on.
        state = self.state()
        state["results"][SHA_P] = verdict(pr=2, ahead=True)
        self.write_state(state)
        self.tick([(8, SHA_B, REPO, "false")], merger_dir=merger)
        self.assertEqual(self.spawned, [(self.state_dir, SHA_B, 8, False, 1)])

    def test_spare_slots_only_puts_any_waiting_head_first(self):
        # designs/0014: predictions only in a slot nothing else wants.
        self.write_state({"waiting": {SHA_B: {"pr": 8, "since": pc.iso(T0)}}})
        with mock.patch.object(pc, "SPARE_ONLY", True):
            self.tick([(8, SHA_B, REPO, "false")], merger_dir=self.merger())
        self.assertEqual(self.spawned, [(self.state_dir, SHA_B, 8, False, 1)])

    def test_spare_slots_only_still_predicts_into_an_idle_slot(self):
        with mock.patch.object(pc, "SPARE_ONLY", True):
            self.tick([], merger_dir=self.merger())
        self.assertEqual(self.spawned[0][1], SHA_P)

    def test_a_default_branch_head_due_goes_before_predictions(self):
        # checks.main: once its head has waited depends_minutes, the
        # default branch goes first, before the Merger's predictions too.
        self.write_state({"waiting": {
            SHA_B: {"pr": 8, "since": pc.iso(T0)},
            SHA_C: {"pr": 0, "main": True, "since": pc.iso(
                T0 - pc.DEPENDS_WAIT - datetime.timedelta(minutes=1))}}})
        spawned = []
        pc.tick(self.state_dir, "tok", FakeRun((is_pulls, done(
            [], 0, heads_tsv((8, SHA_B, REPO, "false"))))),
            lambda *a, **k: spawned.append((a, k)), self.lines.append,
            when=T0, slots=2, merger_dir=self.merger())
        self.assertEqual(spawned, [((self.state_dir, SHA_C, 0, False, 1,
                                     None), {"main": True})])

    def test_a_default_branch_head_not_yet_due_waits_for_predictions(self):
        self.write_state({"waiting": {SHA_C: {"pr": 0, "main": True,
                                              "since": pc.iso(T0)}}})
        self.tick([], merger_dir=self.merger())
        self.assertEqual(self.spawned[0][1], SHA_P)

    def test_a_head_that_has_waited_max_wait_goes_before_predictions(self):
        # checks.max_wait_minutes: priority can't starve a head.
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(minutes=31))}}})
        with mock.patch.object(pc, "MAX_WAIT",
                               datetime.timedelta(minutes=30)):
            self.tick([(8, SHA_B, REPO, "false")], merger_dir=self.merger())
        self.assertEqual(self.spawned, [(self.state_dir, SHA_B, 8, False, 1)])
        self.assertEqual(self.lines[-1], "STARTED #8 %s; 0 waiting, first: "
                         "it has waited 30 minutes" % SHA_B[:10])

    def test_a_head_put_first_goes_before_predictions(self):
        # checks.first_authors: put first on purpose, so not behind the
        # Merger's predictions either.
        lead = "leaddevwidgetagent[bot]"
        with mock.patch.object(pc, "FIRST_AUTHORS", frozenset({lead})):
            self.tick([(8, SHA_B, REPO, "false\t" + lead)],
                      merger_dir=self.merger())
        self.assertEqual(self.spawned, [(self.state_dir, SHA_B, 8, False, 1)])

    def test_the_mergers_head_waiting_stops_predictions(self):
        # A pick put before the Merger's head takes one slot; the next
        # goes to the Merger's head, never to a prediction, since every
        # prediction assumes it merges first.
        lead = "leaddevwidgetagent[bot]"
        self.hold(1, "#9 %s %s" % (SHA_C, pc.iso(T0)))
        heads = [(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false\t" + lead),
                 (9, SHA_C, REPO, "false")]
        with mock.patch.object(pc, "FIRST_AUTHORS", frozenset({lead})):
            self.tick(heads, merger_dir=self.merger())
        self.assertEqual(self.spawned, [(self.state_dir, SHA_B, 8, False, 2)])
        self.assertNotIn(SHA_P, self.state().get("ahead_started", {}))

    def test_put_first(self):
        self.assertTrue(pc.put_first({"urgent": True}))
        self.assertFalse(pc.put_first({"urgent": True, "errors": 1}))
        self.assertFalse(pc.put_first({"main": True, "urgent": True}))
        self.assertFalse(pc.put_first({"pr": 3}))

    def test_merger_head_waits(self):
        state = {"waiting": {SHA_A: {"pr": 7, "since": pc.iso(T0)}}}
        self.assertTrue(pc.merger_head_waits(state, 7, (), T0))
        self.assertFalse(pc.merger_head_waits(state, 7, {SHA_A}, T0))
        self.assertFalse(pc.merger_head_waits(state, 8, (), T0))
        self.assertFalse(pc.merger_head_waits(state, None, (), T0))

    def test_one_that_has_not_yet_still_goes_after_them(self):
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(minutes=29))}}})
        with mock.patch.object(pc, "MAX_WAIT",
                               datetime.timedelta(minutes=30)):
            self.tick([(8, SHA_B, REPO, "false")], merger_dir=self.merger())
        self.assertEqual(self.spawned[0][1], SHA_P)

    def test_a_head_whose_prediction_is_running_waits_to_be_matched(self):
        # #621 at 06:23Z: its head ran beside its own prediction.
        self.hold(1, "#8 %s %s pid=5 ahead" % (SHA_P, pc.iso(T0)))
        self.write_state({"waiting": {SHA_B: {
            "pr": 8, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        why = "its tree is being checked now as the Merger's prediction"
        with mock.patch.object(pc, "twin_heads",
                               lambda *a: {SHA_B: why}):
            self.tick([(8, SHA_B, REPO, "false")], merger_dir=self.merger(
                entries=[]))
        self.assertEqual(self.spawned, [])
        self.assertIn("HELD #8 %s: %s" % (SHA_B[:10], why), self.lines)

    def test_nothing_to_predict_and_nothing_waiting_is_idle(self):
        self.write_state(dict(empty(), results={SHA_P: verdict(pr=2)}))
        self.tick([], merger_dir=self.merger())
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.lines, ["IDLE: no open head is waiting"])

    def test_without_test_ahead_the_predictions_are_not_read(self):
        data = example()
        del data["merge"]["test_ahead"]
        pc.configure(config.Project(data, None))
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.tick([], merger_dir=self.merger())
        self.assertEqual(self.spawned, [])
        self.assertNotIn("ahead_started", self.state())


class TwinHeads(unittest.TestCase):
    """A waiting head whose tree a slot is checking as its own pull
    request's prediction is held for that verdict."""

    def run_with(self, trees):
        return FakeRun((lambda a: a[-1].endswith("^{tree}"),
                        lambda a: done(a, 0, trees[a[-1][:-7]] + "\n")))

    def state(self):
        return {"waiting": {SHA_A: {"pr": 7, "since": pc.iso(T0)},
                            SHA_B: {"pr": 8, "since": pc.iso(T0)},
                            SHA_C: {"pr": 0, "main": True,
                                    "since": pc.iso(T0)}}}

    def test_the_same_tree_under_the_same_pull_request_waits(self):
        running = [(7, SHA_P, pc.iso(T0), 5, True)]
        run = self.run_with({SHA_P: TREE_1, SHA_A: TREE_1, SHA_B: TREE_1,
                             SHA_C: TREE_1})
        self.assertEqual(list(pc.twin_heads(self.state(), running, "/repo",
                                            run)), [SHA_A])

    def test_not_another_tree_nor_a_head_running_nor_without_a_checkout(
            self):
        run = self.run_with({SHA_P: TREE_1, SHA_A: TREE_2})
        self.assertEqual(pc.twin_heads(
            self.state(), [(7, SHA_P, pc.iso(T0), 5, True)], "/repo", run), {})
        # A slot running a pull request's head isn't a prediction.
        self.assertEqual(pc.twin_heads(
            self.state(), [(7, SHA_P, pc.iso(T0), 5, False)], "/repo",
            self.run_with({SHA_P: TREE_1, SHA_A: TREE_1})), {})
        self.assertEqual(pc.twin_heads(
            self.state(), [(7, SHA_P, pc.iso(T0), 5, True)], None,
            self.run_with({SHA_P: TREE_1, SHA_A: TREE_1})), {})


TIP = "9" * 40


class SettledHeads(StateCase):
    """A head that a worker would only match is matched by the tick, with
    no slot: one waited 38 minutes behind six runs for a slot to match
    it."""

    hold, merger_says, merger = Tick.hold, Tick.merger_says, TickAhead.merger

    def run_with(self, contains=True, trees=None):
        trees = trees or {SHA_A: TREE_1}
        return FakeRun(
            (is_pulls, done([], 0, heads_tsv((7, SHA_A, REPO, "false")))),
            (lambda a: "refs/remotes/origin/" + pc.MAIN in a,
             done([], 0, "%s\n%s\n" % (TIP, TREE_2))),
            (lambda a: a[-1].endswith("^{tree}"),
             lambda a: done(a, 0, trees.get(a[-1][:-7], "") + "\n")),
            (lambda a: "--is-ancestor" in a,
             done([], 0 if contains else 1)))

    def settle(self, result, contains=True, busy=True, **estimate):
        self.write_state({"waiting": {SHA_A: dict({"pr": 7, "since": pc.iso(
            T0 - datetime.timedelta(minutes=5))}, **estimate)},
            "results": {SHA_P: result}})
        if busy:
            self.hold(1, "#8 %s %s" % (SHA_B, pc.iso(T0)))
            self.hold(2, "#9 %s %s" % (SHA_C, pc.iso(T0)))
        self.spawned, posted = [], []
        pc.tick(self.state_dir, "tok", self.run_with(contains),
                lambda *a: self.spawned.append(a), self.lines.append,
                when=T0, post=lambda *a: posted.append(a) or True, slots=2,
                checkout="/repo", merger_dir=self.merger(
                    entries=[entry(7, SHA_P, TREE_1)]))
        return posted

    def test_the_mergers_head_on_its_predicted_tree_is_matched_at_once(self):
        posted = self.settle(verdict(pr=7, ahead=True))
        state = self.state()
        self.assertNotIn(SHA_A, state["waiting"])
        self.assertEqual(state["results"][SHA_A]["matched"], SHA_P)
        self.assertEqual([p[:2] for p in posted], [(SHA_A, "success")])
        self.assertEqual(self.spawned, [])
        self.assertTrue(any("MATCHED #7 %s" % SHA_A[:10] in line
                            for line in self.lines))

    def batched(self, results, contains=None):
        """A tick with #7's head on the tree of the first member of the
        Merger's batch #7..#8, whose top is SHA_Q. `contains(a, b)`
        answers git's --is-ancestor, yes by default."""
        self.write_state({"waiting": {SHA_A: {"pr": 7, "since": pc.iso(
            T0 - datetime.timedelta(minutes=5))}}, "results": results})
        merger = self.merger_says(7)
        write_ahead(merger, [entry(7, SHA_P, TREE_1),
                             entry(8, SHA_Q, TREE_3)],
                    when=T0 - datetime.timedelta(minutes=1), batch=2)
        self.spawned, posted = [], []
        run = self.run_with()
        if contains is not None:
            run.rules.insert(0, (lambda a: "--is-ancestor" in a, lambda a:
                                 done(a, 0 if contains(a[-2], a[-1]) else 1)))
        pc.tick(self.state_dir, "tok", run, lambda *a:
                self.spawned.append(a), self.lines.append, when=T0,
                post=lambda *a: posted.append(a) or True, slots=1,
                checkout="/repo", merger_dir=merger)
        return posted

    def test_a_member_with_no_estimate_is_covered_by_its_tops_base(self):
        # Seen on 2026-10-26: after a batch's top went green, each
        # member, updated just after a merge moved the tip, had no
        # estimate yet, and ran for a slot.
        top = verdict(pr=8, tree=TREE_3, ahead=True,
                      selection={"ran": ["lint"], "base": SHA_B})
        posted = self.batched({SHA_Q: top})
        self.assertEqual(self.state()["results"][SHA_A]["covered"], "#7..#8")
        self.assertEqual(posted[0][:2], (SHA_A, "success"))
        self.assertEqual(self.spawned, [])

    def test_a_member_not_on_its_tops_base_is_run(self):
        top = verdict(pr=8, tree=TREE_3, ahead=True,
                      selection={"ran": ["lint"], "base": SHA_B})
        self.batched({SHA_Q: top}, contains=lambda a, b: a != SHA_B)
        self.assertIn(SHA_A, self.state()["waiting"])
        self.assertEqual([a[1] for a in self.spawned], [SHA_A])

    def test_a_member_is_covered_by_its_batchs_green_top(self):
        posted = self.batched({SHA_Q: verdict(pr=8, tree=TREE_3,
                                              ahead=True)})
        record = self.state()["results"][SHA_A]
        self.assertEqual((record["matched"], record["covered"]),
                         (SHA_Q, "#7..#8"))
        self.assertEqual(posted[0][:2], (SHA_A, "success"))
        self.assertIn("covered by batch #7..#8 (tested as %s)"
                      % SHA_Q[:10], posted[0][2])
        self.assertTrue(any("COVERED #7 %s" % SHA_A[:10] in line
                            for line in self.lines))

    def test_a_member_waits_while_its_top_is_run(self):
        posted = self.batched({})
        self.assertEqual(posted, [])
        self.assertIn(SHA_A, self.state()["waiting"])
        # The slot goes to the top, not to the member.
        self.assertEqual([a[1] for a in self.spawned], [SHA_Q])
        self.assertTrue(any("HELD #7" in line and "batch #7..#8" in line
                            for line in self.lines))

    def test_a_red_top_leaves_the_member_to_its_own_run(self):
        self.batched({SHA_Q: verdict(pr=8, tree=TREE_3, ok=False,
                                     ahead=True)})
        self.assertEqual([a[1] for a in self.spawned], [SHA_A])

    def test_a_head_without_the_tip_is_left_to_a_worker(self):
        # It would be checked merged with the tip: another tree.
        self.settle(verdict(pr=7, ahead=True), contains=False)
        self.assertIn(SHA_A, self.state()["waiting"])

    def test_a_red_verdict_is_not_matched(self):
        self.settle(verdict(pr=7, ok=False, ahead=True))
        self.assertIn(SHA_A, self.state()["waiting"])

    def test_one_that_ran_fewer_steps_is_not_matched(self):
        self.settle(verdict(pr=7, ahead=True,
                            selection={"ran": [], "base": TIP}))
        self.assertIn(SHA_A, self.state()["waiting"])

    def narrow(self):
        # A prediction runs only the steps its changes chose.
        return verdict(pr=7, ahead=True, selection={"ran": [], "base": TIP})

    def test_one_that_ran_every_step_the_head_would_is_matched(self):
        # Seen on 2026-10-18: the prediction ran 13 s, then the Merger's
        # head on its tree waited 50 minutes behind two full runs.
        posted = self.settle(self.narrow(), lane_steps=[], lane_base=TIP)
        self.assertEqual(self.state()["results"][SHA_A]["matched"], SHA_P)
        self.assertEqual([p[:2] for p in posted], [(SHA_A, "success")])

    def test_a_head_that_would_run_more_steps_is_not_matched(self):
        self.settle(self.narrow(), lane_steps=["test"], lane_base=TIP)
        self.assertIn(SHA_A, self.state()["waiting"])

    def test_an_estimate_against_another_tip_is_not_used(self):
        self.settle(self.narrow(), lane_steps=[], lane_base=SHA_B)
        self.assertIn(SHA_A, self.state()["waiting"])

    def test_with_fresh_an_estimate_from_older_records_is_not_used(self):
        with mock.patch.object(pc, "FRESH_WAIT",
                               datetime.timedelta(minutes=15)):
            self.settle(self.narrow(), lane_steps=[], lane_base=TIP,
                        lane_key="stale")
        self.assertIn(SHA_A, self.state()["waiting"])

    def test_with_fresh_a_current_estimate_is_used(self):
        with mock.patch.object(pc, "FRESH_WAIT",
                               datetime.timedelta(minutes=15)):
            self.settle(self.narrow(), lane_steps=[], lane_base=TIP,
                        lane_key=pc.records_key(self.state_dir, TIP))
        self.assertEqual(self.state()["results"][SHA_A]["matched"], SHA_P)

    def test_with_a_free_slot_it_is_matched_and_the_slot_goes_on(self):
        self.settle(verdict(pr=7, ahead=True), busy=False)
        self.assertIn(SHA_A, self.state()["results"])
        self.assertNotIn(SHA_A, [s[1] for s in self.spawned])


class PrepareAhead(unittest.TestCase):

    def test_checks_the_commit_out_without_fetching(self):
        with tempfile.TemporaryDirectory() as wt:
            os.mkdir(os.path.join(wt, ".git"))
            run = FakeRun()
            self.assertEqual(pc.prepare_ahead("/repo", wt, SHA_P, run),
                             ("ready", ""))
            self.assertEqual([c["args"] for c in run.calls], [
                ["git", "-C", wt, "cat-file", "-e", SHA_P + "^{commit}"],
                ["git", "-C", wt, "checkout", "-q", "--detach", "--force",
                 SHA_P],
                ["git", "-C", wt, "clean", "-q", "-ffdx"]])

    def test_keep_ignored_paths_survive_the_clean(self):
        # checks.keep_ignored: a build's own output, updated in place,
        # so a step embedding it isn't rebuilt from nothing every head.
        with tempfile.TemporaryDirectory() as wt:
            os.mkdir(os.path.join(wt, ".git"))
            run = FakeRun()
            with mock.patch.object(pc, "KEEP_IGNORED",
                                   ("web/dist", "/out/")):
                pc.prepare_ahead("/repo", wt, SHA_P, run)
            self.assertEqual(run.calls[-1]["args"], [
                "git", "-C", wt, "clean", "-q", "-ffdx",
                "-e", "/web/dist/", "-e", "/out/"])

    def test_keep_ignored_keeps_the_files_for_real(self):
        with tempfile.TemporaryDirectory() as wt:
            for args in (["init", "-q"], ["config", "user.name", "t"],
                         ["config", "user.email", "t@t"]):
                subprocess.run(["git", "-C", wt] + args, check=True)
            with open(os.path.join(wt, ".gitignore"), "w") as handle:
                handle.write("dist/\nnode/\n")
            subprocess.run(["git", "-C", wt, "add", ".gitignore"], check=True)
            subprocess.run(["git", "-C", wt, "commit", "-qm", "i"],
                           check=True)
            for path in ("web/dist/x", "web/node/y", "stray"):
                os.makedirs(os.path.dirname(os.path.join(wt, path)) or wt,
                            exist_ok=True)
                open(os.path.join(wt, path), "w").close()
            with mock.patch.object(pc, "KEEP_IGNORED", ("web/dist",)):
                subprocess.run(pc.clean_args(wt), check=True)
            left = sorted(os.path.relpath(os.path.join(r, f), wt)
                          for r, _, fs in os.walk(wt) if "/.git" not in r
                          and not r.endswith(".git") for f in fs)
            self.assertEqual(left, [".gitignore", "web/dist/x"])

    def test_a_missing_commit_is_an_error_and_nothing_is_checked_out(self):
        with tempfile.TemporaryDirectory() as wt:
            os.mkdir(os.path.join(wt, ".git"))
            run = FakeRun((lambda a: "cat-file" in a,
                           done([], 1, "", "bad object")))
            self.assertEqual(pc.prepare_ahead("/repo", wt, SHA_P, run),
                             ("error", "cat-file -e: bad object"))
            self.assertFalse([c for c in run.calls if "checkout" in c["args"]])

    def test_a_missing_worktree_is_added_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            run = FakeRun()
            pc.prepare_ahead("/repo", os.path.join(tmp, "wt"), SHA_P, run)
            self.assertEqual(run.calls[1]["args"][:5],
                             ["git", "-C", "/repo", "worktree", "add"])


class WorkerAhead(Worker):
    """A prediction is run as a head is, recorded by tree, and posted
    nowhere; a head whose tree is green already is posted green unrun."""

    def run_for(self, fetched=SHA_A, failing=(), fail_git=None,
                tree=TREE_1, here=True):
        run = super().run_for(fetched, failing, fail_git, here)
        run.rules.insert(0, (lambda a: a[-1].endswith("^{tree}"),
                             done([], 0, tree + "\n")))
        return run

    def queue(self, errors=0, faults=0):
        """As Worker's, keeping the verdicts already written."""
        results = self.state()["results"]
        super().queue(errors, faults)
        state = self.state()
        state["results"] = results
        self.write_state(state)

    def ahead(self, run, sha=SHA_P, pr=2, post=None, chain=None):
        os.makedirs(os.path.join(self.state_dir, "wt", ".git"), exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        return pc.worker("/repo", self.state_dir, sha, pr, run,
                         self.lines.append, lambda: next(clock), post, chain,
                         ahead=True)

    def ran(self, run):
        return any(c["args"][0] == "make" for c in run.calls)

    def logged(self):
        return pathlib.Path(self.state_dir, "verdicts.log").read_text()

    def test_a_prediction_is_checked_recorded_and_not_posted(self):
        posts, chained = [], []
        run = self.run_for()
        self.assertEqual(self.ahead(run, post=lambda *a: posts.append(a),
                                    chain=lambda: chained.append(1) or
                                    ["x", "y"]), 0)
        self.assertEqual(posts, [])
        # Still chained: the next tick drains the queue.
        self.assertEqual(chained, [1])
        self.assertFalse([c for c in run.calls if "fetch" in c["args"]])
        self.assertTrue(self.ran(run))
        r = self.state()["results"][SHA_P]
        self.assertEqual((r["pr"], r["ok"], r["tree"], r["ahead"], r["host"],
                          r["steps"]),
                         (2, True, TREE_1, True, socket.gethostname(),
                          pc.steps_digest()))
        self.assertIn("VERDICT AHEAD #2 dddddddddd: green", self.logged())

    def test_a_prediction_is_run_even_when_its_tree_is_green(self):
        # It is only chosen when its tree has no verdict; if one lands
        # meanwhile, running it costs one run and posts nothing.
        self.write_state(dict(empty(), results={SHA_A: verdict(pr=9)}))
        run = self.run_for()
        self.ahead(run)
        self.assertTrue(self.ran(run))
        self.assertNotIn("matched", self.state()["results"][SHA_P])

    def test_a_prediction_that_cannot_be_prepared_counts_its_errors(self):
        run = self.run_for(fail_git="cat-file")
        for _ in range(pc.MAX_ERRORS):
            self.assertEqual(self.ahead(run), 2)
        state = self.state()
        self.assertEqual(state["ahead_errors"], {SHA_P: pc.MAX_ERRORS})
        self.assertNotIn(SHA_P, state["results"])
        self.assertIn("GAVE-UP AHEAD #2 dddddddddd", self.logged())

    def test_a_head_whose_tree_tested_green_is_posted_without_a_run(self):
        self.write_state(dict(empty(), results={
            SHA_P: verdict(pr=2, ahead=True)}))
        self.queue()
        posts = []
        run = self.run_for()
        self.assertEqual(self.work(run, post=lambda *a: posts.append(a)
                                   or True), 0)
        self.assertFalse(self.ran(run))
        self.assertEqual(posts, [(
            SHA_A, "success", "green on build.example.org: tree tested as "
            "dddddddddd (the Merger's prediction for #2)")])
        state = self.state()
        r = state["results"][SHA_A]
        self.assertEqual((r["ok"], r["matched"], r["seconds"], r["posted"],
                          r["tree"]), (True, SHA_P, 0, True, TREE_1))
        self.assertEqual(state["waiting"], {})
        self.assertIn("MATCHED #7 aaaaaaaaaa", self.logged())

    def test_another_heads_green_is_named_as_that_head(self):
        self.write_state(dict(empty(), results={SHA_B: verdict(pr=4)}))
        self.queue()
        posts = []
        self.work(self.run_for(), post=lambda *a: posts.append(a) or True)
        self.assertEqual(posts[0][2], "green on build.example.org: tree "
                         "tested as bbbbbbbbbb (#4's head)")

    def test_a_red_verdict_is_never_reused(self):
        self.write_state(dict(empty(), results={SHA_B: verdict(
            pr=4, ok=False)}))
        self.queue()
        run = self.run_for()
        posts = []
        self.work(run, post=lambda *a: posts.append(a) or True)
        self.assertTrue(self.ran(run))
        self.assertEqual([p[1] for p in posts], ["pending", "success"])
        self.assertNotIn("matched", self.state()["results"][SHA_A])

    def test_a_green_after_a_red_on_the_same_tree_logs_a_flake(self):
        self.write_state(dict(empty(), results={SHA_B: verdict(
            pr=4, ok=False)}))
        self.queue()
        self.work(self.run_for())
        self.assertIn("FLAKE tree 1111111111: red(test) on #4 bbbbbbbbbb, "
                      "then green on #7 aaaaaaaaaa", self.logged())

    def test_another_hosts_step_lists_or_trees_green_is_not_reused(self):
        for other, tree in ((verdict(pr=4, host="elsewhere"), TREE_1),
                            (verdict(pr=4, steps="other"), TREE_1),
                            (verdict(pr=4), TREE_2)):
            self.write_state(dict(empty(), results={SHA_B: other}))
            self.queue()
            run = self.run_for(tree=tree)
            self.work(run)
            self.assertTrue(self.ran(run), other)

    def test_a_green_older_than_the_limit_is_not_reused(self):
        self.write_state(dict(empty(), results={SHA_B: verdict(
            pr=4, finished=T0 - pc.MATCH_MAX_AGE
            - datetime.timedelta(seconds=1))}))
        self.queue()
        run = self.run_for()
        self.work(run)
        self.assertTrue(self.ran(run))

    def test_a_match_is_dated_from_the_run_it_stands_in_for(self):
        # So a match of a match ages out after the run, not after the
        # last match.
        before = T0 - datetime.timedelta(hours=1)
        self.write_state(dict(empty(), results={SHA_B: verdict(
            pr=4, finished=before)}))
        self.queue()
        self.work(self.run_for())
        r = self.state()["results"][SHA_A]
        self.assertEqual((r["finished"], r["started"]),
                         (pc.iso(before), pc.iso(T0)))
        self.assertIsNone(pc.tested(self.state(), TREE_1, before +
                                    pc.MATCH_MAX_AGE +
                                    datetime.timedelta(seconds=1)))

    def test_its_own_earlier_verdict_is_run_again(self):
        # A head queued again after its verdict is run, not matched
        # against itself.
        self.write_state(dict(empty(), results={SHA_A: verdict(pr=7)}))
        self.queue()
        run = self.run_for()
        self.work(run)
        self.assertTrue(self.ran(run))

    def test_without_test_ahead_nothing_is_matched_or_recorded_by_tree(self):
        data = example()
        del data["merge"]["test_ahead"]
        pc.configure(config.Project(data, None))
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.write_state(dict(empty(), results={SHA_B: verdict(pr=4)}))
        self.queue()
        run = self.run_for()
        self.work(run)
        self.assertTrue(self.ran(run))
        self.assertFalse([c for c in run.calls
                          if c["args"][-1].endswith("^{tree}")])
        self.assertNotIn("tree", self.state()["results"][SHA_A])


class FinalStatusMatched(unittest.TestCase):

    def test_a_matched_verdict_names_its_source(self):
        r = {"ok": True, "seconds": 0, "matched": SHA_P,
             "matched_as": "the Merger's prediction for #2"}
        self.assertEqual(pc.final_status(r), (
            "success", "green on build.example.org: tree tested as "
            "dddddddddd (the Merger's prediction for #2)"))
        self.assertLessEqual(len(pc.final_status(r)[1]), pc.DESCRIPTION_MAX)


if __name__ == "__main__":
    unittest.main()
