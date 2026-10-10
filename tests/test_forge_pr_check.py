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
import subprocess
import sys
import tempfile
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


def _refuse_real_chain(popen=None, checkout=None):
    """Stands in for start_next_tick in every test: the real one starts
    a real cron tick on this host. A test passes its own popen."""
    if popen is None:
        raise RealChainReached("a test reached the real start_next_tick")
    return _real_start_next_tick(popen=popen, checkout=checkout)


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
                              step_timeout_minutes=5)
        pc.configure(config.Project(data, None))
        self.assertEqual((pc.slots_from_env({}), pc.memory_max_from_env({}),
                          pc.STEP_TIMEOUT_S), (3, None, 300))

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
                         [(7, SHA_A, REPO, False), (8, SHA_B, "", True)])
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

    def test_each_post_mints_its_own_token(self):
        run = FakeRun((is_mint, done([], 0, "tok\n")))
        post = pc.make_poster(run, lambda line: None)
        post(SHA_A, "pending", "p")
        post(SHA_A, "success", "s")
        self.assertEqual(sum(is_mint(c["args"]) for c in run.calls), 2)

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

    def tick(self, heads=None, post=None, fail=False, slots=2):
        run = FakeRun((is_pulls, done([], 1 if fail else 0,
                                      heads_tsv(*(heads or [])))))
        self.spawned = []
        rc = pc.tick(self.state_dir, "tok", run,
                     lambda *a: self.spawned.append(a), self.lines.append,
                     when=T0, post=post, slots=slots)
        return rc

    def hold(self, slot, holder=None):
        held = pc.try_lock(pc.lock_path(self.state_dir, slot), holder)
        self.addCleanup(held.close)
        return held

    def test_cannot_list_pull_requests_is_2_and_starts_nothing(self):
        self.assertEqual(self.tick(fail=True), 2)
        self.assertEqual(self.spawned, [])
        self.assertTrue(self.lines[0].startswith("NO-PULLS"))

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

    def test_a_posting_tick_starts_a_posting_worker_and_reposts(self):
        self.write_state({"results": {SHA_B: {
            "pr": 8, "ok": True, "seconds": 3, "failed": [],
            "finished": pc.iso(T0)}}})
        posts = []
        self.tick([(7, SHA_A, REPO, "false"), (8, SHA_B, REPO, "false")],
                  post=lambda *a: posts.append(a) or True)
        self.assertEqual(self.spawned, [(self.state_dir, SHA_A, 7, True, 1)])
        self.assertEqual(posts, [(SHA_B, "success",
                                  "green on build.example.org in 3s")])
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
        git("clone", "-q", self.origin, self.repo, cwd=root)

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

    def test_a_pull_request_that_does_not_exist_is_an_error(self):
        status, detail = pc.prepare(self.repo, self.wt, self.head, 99)
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


class StepEnv(unittest.TestCase):

    def test_drops_tokens_and_this_tools_settings(self):
        env = pc.step_env("/s", {"GH_TOKEN": "x", "GITHUB_TOKEN": "y",
                                 "FORGE_STATE_DIR": "/d",
                                 "FORGE_CRON_CHECKOUT": "/c", "PATH": "/b"})
        self.assertEqual(env, {"PATH": "/b",
                               "CARGO_TARGET_DIR": "/s/cargo-target",
                               "XDG_RUNTIME_DIR": "/run/user/%d"
                               % os.getuid()})

    def test_keeps_the_hosts_cargo_target_dir(self):
        env = pc.step_env("/s", {"CARGO_TARGET_DIR": "/t"})
        self.assertEqual(env["CARGO_TARGET_DIR"], "/t")

    def test_keeps_the_hosts_runtime_dir(self):
        env = pc.step_env("/s", {"XDG_RUNTIME_DIR": "/r"})
        self.assertEqual(env["XDG_RUNTIME_DIR"], "/r")


class MemoryCap(unittest.TestCase):
    """#147: each step in its own user scope under a memory limit."""

    def test_sixteen_gigabytes_unless_the_host_says_otherwise(self):
        self.assertEqual(pc.memory_max_from_env({}), "16G")
        self.assertEqual(pc.memory_max_from_env(
            {"FORGE_PR_CHECK_MEMORY_MAX": " 8G "}), "8G")
        for off in ("off", "OFF", ""):
            self.assertIsNone(pc.memory_max_from_env(
                {"FORGE_PR_CHECK_MEMORY_MAX": off}))

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

    def run_for(self, fetched=SHA_A, failing=(), fail_git=None):
        def is_failing_step(args):
            return args[0] in ("make", "npm") and args[-1] in failing
        rules = [(lambda a: "FETCH_HEAD" in a, done([], 0, fetched + "\n")),
                 (is_failing_step, done([], 1, "", "it broke\n"))]
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

    def queue(self, errors=0):
        entry = {"pr": 7, "since": pc.iso(T0 - datetime.timedelta(minutes=3))}
        if errors:
            entry["errors"] = errors
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
        run = self.run_for(fetched=SHA_B)
        self.assertEqual(self.work(run), 0)
        self.assertEqual(self.state()["waiting"], {})
        self.assertFalse([c for c in run.calls if c["args"][0] == "make"])
        self.assertIn("MOVED #7", self.lines[-1])

    def test_a_prepare_failure_counts_an_error_and_runs_nothing(self):
        self.queue()
        posts, chained = [], []
        rc = self.work(self.run_for(fail_git="fetch"),
                       post=lambda *a: posts.append(a) or True,
                       chain=lambda: chained.append(1) or ["x", "y"])
        self.assertEqual(rc, 2)
        self.assertEqual(self.state()["waiting"][SHA_A]["errors"], 1)
        self.assertTrue(self.lines[-1].startswith("NO-WORKTREE #7"))
        self.assertEqual((posts, chained), ([], []))

    def test_the_last_allowed_failure_gives_up_and_posts_error(self):
        self.queue(errors=pc.MAX_ERRORS - 1)
        posts = []
        self.work(self.run_for(fail_git="fetch"),
                  post=lambda *a: posts.append(a) or True)
        self.assertEqual([p[1] for p in posts], ["error"])
        self.assertIn("could not check the head out 3 times", posts[0][2])
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("GAVE-UP #7", logged)

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
        self.assertEqual(seen, [("", "#7 %s %s" % (SHA_A, pc.iso(T0)))])
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
        self.assertEqual(seen, ["#7 %s %s" % (SHA_A, pc.iso(T0))])
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

    def test_a_failing_chain_is_reported_not_raised(self):
        self.queue()

        def chain():
            raise OSError("no fork")
        self.assertEqual(self.work(self.run_for(), chain=chain), 0)
        self.assertEqual(self.lines[-1], "CHAIN-FAILED: OSError: no fork")


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
        self.assertEqual(started, [{"checkout": self.checkout}])

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

    def test_the_state_directory_defaults_under_the_home(self):
        with mock.patch.object(pc, "tick", return_value=0) as tick:
            self.main(state=False)
        self.assertEqual(tick.call_args.args[0], os.path.join(
            self._tmp.name, ".local", "state", "agent-forge", "widget",
            "pr-check"))


if __name__ == "__main__":
    unittest.main()
