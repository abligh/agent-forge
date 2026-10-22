"""Tests for `labels.rerun` in forge-pr-check (designs/0022): a red
head carrying the label is run once more, and `checks.host_fault_text`,
the project's own words for a red that is the host's fault."""

import datetime
import json
import os
import pathlib
import socket
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests import test_forge_pr_check as base
from tests.test_forge_pr_check import (REPO, SHA_A, SHA_B, T0, FakeRun,
                                       done, is_pulls, pc)

RERUN = "Check:rerun"
AUTHOR = "leaddevwidgetagent[bot]"
LABELLER = "uidevwidgetagent[bot]"
AT = "2026-10-09T11:55:00Z"
AT_2 = "2026-10-09T11:58:00Z"


def configured(rerun=RERUN, instance=None):
    data = example()
    if rerun:
        data.setdefault("labels", {})["rerun"] = rerun
    if instance:
        data["checks"]["instances"] = {instance: {
            "context": "other/check", "steps": [{"name": "t",
                                                 "run": ["true"]}]}}
    pc.configure(config.Project(data, None), instance)


def red(pr=7, failed=("test",), finished=T0 - datetime.timedelta(hours=1)):
    return {"pr": pr, "ok": False, "failed": list(failed), "seconds": 9,
            "seen": pc.iso(finished), "started": pc.iso(finished),
            "finished": pc.iso(finished), "posted": True}


def green(pr=7):
    r = red(pr, ())
    r["ok"] = True
    return r


def is_events(args):
    return args[:2] == ["gh", "api"] and any("/events" in a for a in args)


def is_unlabel(args):
    return args[:4] == ["gh", "api", "-X", "DELETE"] \
        and "/labels/" in args[4]


def is_comment(args):
    return args[:4] == ["gh", "api", "-X", "POST"] \
        and args[4].endswith("/comments")


class RerunConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_a_label_name(self):
        data = example()
        data.setdefault("labels", {})["rerun"] = RERUN
        self.assertEqual(config.check(data), [])
        data["labels"]["rerun"] = ["x"]
        self.assertEqual(len(config.check(data)), 1)

    def test_off_unless_configured(self):
        self.assertIsNone(pc.RERUN_LABEL)
        configured()
        self.assertEqual(pc.RERUN_LABEL, RERUN)

    def test_an_instance_leaves_it_to_the_check_itself(self):
        configured(instance="mac")
        self.assertIsNone(pc.RERUN_LABEL)


class RerunTick(base.StateCase):

    def setUp(self):
        super().setUp()
        configured()
        self.posts = []

    def tearDown(self):
        pc.configure(config.Project(example(), None))
        super().tearDown()

    def tick(self, labels=(RERUN,), events=None, unlabel=None,
             posting=True, sha=SHA_A):
        text = "7\t%s\t%s\tfalse\t%s\t%s\n" % (sha, REPO, AUTHOR,
                                              json.dumps(list(labels)))
        if events is None:
            events = done([], 0, "%s\t%s\n" % (AUTHOR, AT)
                          + "%s\t%s\n" % (LABELLER, AT_2))
        self.run = FakeRun((is_pulls, done([], 0, text)),
                           (is_events, events),
                           (is_unlabel, unlabel or done([], 0, "")),
                           (is_comment, done([], 0, "{}")))
        self.spawned = []
        post = (lambda *a: self.posts.append(a) or True) if posting else None
        return pc.tick(self.state_dir, "tok", self.run,
                       lambda *a: self.spawned.append(a), self.lines.append,
                       when=T0, post=post, slots=1)

    def calls(self, match):
        return [c["args"] for c in self.run.calls if match(c["args"])]

    def test_a_red_head_is_queued_again_and_its_verdict_kept(self):
        first = red()
        self.write_state({"results": {SHA_A: first}})
        self.assertEqual(self.tick(), 0)
        state = self.state()
        self.assertNotIn(SHA_A, state["results"])
        self.assertEqual(state["reruns"][SHA_A], {
            "pr": 7, "verdict": first, "by": LABELLER, "labelled": AT_2,
            "queued": pc.iso(T0), "answered": AT_2})
        self.assertEqual(self.spawned, [(self.state_dir, SHA_A, 7, True, 1)])
        (unlabel,) = self.calls(is_unlabel)
        self.assertEqual(unlabel[4], "repos/%s/issues/7/labels/Check%%3Arerun"
                         % pc.REPO_SLUG)
        self.assertEqual(self.calls(is_comment), [])
        self.assertIn((SHA_A, "pending", "queued again after a red, by %s"
                       % LABELLER), self.posts)
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("RERUN #7 aaaaaaaaaa: was red(test); queued by %s"
                      % LABELLER, logged)

    def test_the_labeller_is_read_from_the_label_s_own_events(self):
        self.write_state({"results": {SHA_A: red()}})
        self.tick()
        (events,) = self.calls(is_events)
        self.assertIn("repos/%s/issues/7/events?per_page=100"
                      % pc.REPO_SLUG, events)
        self.assertIn('.label.name == "Check:rerun"', events[-1])

    def test_a_labeller_github_cannot_name_leaves_it_for_next_tick(self):
        self.write_state({"results": {SHA_A: red()}})
        self.tick(events=done([], 1, "", "HTTP 502"))
        state = self.state()
        self.assertIn(SHA_A, state["results"])
        self.assertNotIn("reruns", state)
        self.assertEqual(self.calls(is_unlabel), [])
        self.assertTrue(any(l.startswith("RERUN-FAILED #7") for l in
                            self.lines))

    def test_a_second_label_is_refused_with_a_comment_and_nothing_runs(self):
        second = green()
        self.write_state({"results": {SHA_A: second}, "reruns": {SHA_A: {
            "pr": 7, "verdict": red(), "by": AUTHOR, "labelled": AT,
            "queued": pc.iso(T0), "answered": AT}}})
        self.tick()
        state = self.state()
        self.assertEqual(state["results"][SHA_A], second)
        self.assertEqual(state["reruns"][SHA_A]["answered"], AT_2)
        self.assertEqual(self.spawned, [])
        (comment,) = self.calls(is_comment)
        body = comment[comment.index("-f") + 1]
        self.assertTrue(body.startswith(
            "body=aaaaaaaaaa has already been run again once; push a new "
            "head to run it a third time."))
        self.assertTrue(body.endswith("— %s" % pc.POSTER))
        self.assertEqual(len(self.calls(is_unlabel)), 1)
        self.assertEqual(self.posts, [])

    def test_a_second_label_on_a_head_still_running_again_is_refused(self):
        self.write_state({"waiting": {SHA_A: {"pr": 7, "since": pc.iso(T0)}},
                          "reruns": {SHA_A: {
                              "pr": 7, "verdict": red(), "by": AUTHOR,
                              "labelled": AT, "queued": pc.iso(T0),
                              "answered": AT}}})
        self.tick()
        self.assertEqual(len(self.calls(is_comment)), 1)

    def test_a_label_already_answered_only_comes_off(self):
        # Taking it off failed last tick: no second comment.
        self.write_state({"results": {SHA_A: green()}, "reruns": {SHA_A: {
            "pr": 7, "verdict": red(), "by": LABELLER, "labelled": AT_2,
            "queued": pc.iso(T0), "answered": AT_2}}})
        self.tick()
        self.assertEqual(self.calls(is_comment), [])
        self.assertEqual(len(self.calls(is_unlabel)), 1)

    def test_a_label_that_will_not_come_off_is_said_and_runs_once(self):
        self.write_state({"results": {SHA_A: red()}})
        self.tick(unlabel=done([], 1, "", "HTTP 403: no"))
        self.assertTrue(any(l.startswith("RERUN-FAILED #7 aaaaaaaaaa: "
                                         "taking Check:rerun off") for l in
                            self.lines))
        self.tick()
        self.assertEqual(self.calls(is_comment), [])
        self.assertEqual(len(self.state()["reruns"]), 1)

    def test_a_label_already_gone_is_fine(self):
        self.write_state({"results": {SHA_A: green()}})
        self.tick(unlabel=done([], 1, "", "gh: Label does not exist "
                               "(HTTP 404)"))
        self.assertFalse(any("RERUN-FAILED" in l for l in self.lines))

    def check_nothing_to_run(self, state):
        self.write_state(state)
        before = self.state()["results"]
        self.tick()
        after = self.state()
        self.assertEqual(after["results"], before)
        self.assertNotIn("reruns", after)
        self.assertEqual(self.calls(is_events), [])
        self.assertEqual(self.calls(is_comment), [])
        self.assertEqual(len(self.calls(is_unlabel)), 1)
        self.assertFalse(any(p[1] == "pending" for p in self.posts))

    def test_a_green_head_just_loses_the_label(self):
        self.check_nothing_to_run({"results": {SHA_A: green()}})

    def test_a_pending_head_just_loses_the_label(self):
        self.check_nothing_to_run({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0 - datetime.timedelta(hours=1))}}})
        self.assertEqual(self.state()["waiting"][SHA_A]["since"],
                         pc.iso(T0 - datetime.timedelta(hours=1)))

    def test_an_unrun_head_just_loses_the_label(self):
        self.check_nothing_to_run({})

    def test_unset_a_label_of_that_name_does_nothing(self):
        pc.configure(config.Project(example(), None))
        self.write_state({"results": {SHA_A: red()}})
        self.tick()
        self.assertIn(SHA_A, self.state()["results"])
        self.assertEqual(self.calls(is_unlabel), [])
        self.assertEqual(self.calls(is_events), [])

    def test_a_tick_that_does_not_post_writes_nothing(self):
        self.write_state({"results": {SHA_A: red()}})
        self.tick(posting=False)
        self.assertIn(SHA_A, self.state()["results"])
        self.assertEqual(self.calls(is_unlabel), [])

    def test_a_head_without_the_label_is_left_alone(self):
        self.write_state({"results": {SHA_A: red()}})
        self.tick(labels=("bug",))
        self.assertIn(SHA_A, self.state()["results"])
        self.assertEqual(self.calls(is_unlabel), [])


    def test_a_head_given_up_on_after_host_faults_is_tried_again(self):
        since = pc.iso(T0 - datetime.timedelta(hours=2))
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": since, "faults": pc.MAX_ERRORS}}})
        self.tick()
        state = self.state()
        entry = state["waiting"][SHA_A]
        self.assertEqual(entry["since"], since)  # its old place
        self.assertNotIn("faults", entry)
        self.assertNotIn("reruns", state)
        self.assertEqual(self.spawned, [(self.state_dir, SHA_A, 7, True, 1)])
        self.assertEqual(len(self.calls(is_unlabel)), 1)
        self.assertEqual(self.calls(is_comment), [])
        self.assertIn((SHA_A, "pending", "queued again after it was given "
                       "up on, by %s" % LABELLER), self.posts)
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("RERUN #7 aaaaaaaaaa: given up after 3 host faults; "
                      "queued again by %s" % LABELLER, logged)

    def test_a_head_given_up_on_after_failing_to_prepare_is_tried_again(self):
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0), "errors": pc.MAX_ERRORS}}})
        self.tick()
        self.assertNotIn("errors", self.state()["waiting"][SHA_A])
        logged = pathlib.Path(self.state_dir, "verdicts.log").read_text()
        self.assertIn("given up after 3 failures to prepare it", logged)

    def test_a_head_not_given_up_on_only_loses_the_label(self):
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0), "faults": pc.MAX_ERRORS - 1}}})
        self.tick()
        self.assertEqual(self.state()["waiting"][SHA_A]["faults"],
                         pc.MAX_ERRORS - 1)
        self.assertEqual(self.calls(is_events), [])
        self.assertEqual(len(self.calls(is_unlabel)), 1)
        self.assertEqual(self.posts, [])

    def test_trying_a_given_up_head_again_does_not_use_its_one_rerun(self):
        # A head run again once, then given up on by the host: the label
        # still frees it, with no refusal comment.
        self.write_state({"waiting": {SHA_A: {
            "pr": 7, "since": pc.iso(T0), "faults": pc.MAX_ERRORS}},
            "reruns": {SHA_A: {
                "pr": 7, "verdict": red(), "by": AUTHOR, "labelled": AT,
                "queued": pc.iso(T0), "answered": AT}}})
        self.tick()
        self.assertNotIn("faults", self.state()["waiting"][SHA_A])
        self.assertEqual(self.calls(is_comment), [])


class RerunVerdict(base.StateCase):
    """The second run of a head and what it posts."""

    run_for, work = base.Worker.run_for, base.Worker.work

    def setUp(self):
        super().setUp()
        configured()

    def tearDown(self):
        pc.configure(config.Project(example(), None))
        super().tearDown()

    def test_the_second_verdict_names_the_first_red(self):
        self.write_state({
            "waiting": {SHA_A: {"pr": 7, "since": pc.iso(T0)}},
            "reruns": {SHA_A: {"pr": 7, "verdict": red(failed=("vet",
                                                                "test")),
                               "by": LABELLER, "labelled": AT,
                               "queued": pc.iso(T0), "answered": AT}}})
        posts = []
        self.assertEqual(self.work(self.run_for(), post=lambda *a: posts.append(
            a) or True), 0)
        record = self.state()["results"][SHA_A]
        self.assertEqual(record["rerun_of"], ["vet", "test"])
        _, state, description = posts[-1]
        self.assertEqual(state, "success")
        self.assertIn("(run again after red(vet,test))", description)
        self.assertIn(SHA_A, self.state()["reruns"])

    def test_a_red_second_verdict_stands_and_says_so_too(self):
        r = dict(red(), rerun_of=["test"])
        self.assertEqual(pc.final_status(r), (
            "failure", "red on build.example.org (run again after "
            "red(test)): test failed"))

    def test_a_first_verdict_says_nothing_of_it(self):
        self.assertNotIn("run again", pc.final_status(red())[1])

    def test_a_conflict_says_so_too(self):
        r = {"ok": False, "failed": ["merge"], "base": SHA_B,
             "conflicts": ["a.txt"], "rerun_of": ["merge"]}
        self.assertIn("(run again after red(merge))",
                      pc.final_status(r)[1])


class RerunState(unittest.TestCase):

    def entry(self, queued):
        return {"pr": 7, "verdict": red(), "by": LABELLER, "labelled": AT,
                "queued": pc.iso(queued), "answered": AT}

    def test_kept_while_the_head_waits_or_has_a_verdict(self):
        old = T0 - pc.KEEP - datetime.timedelta(seconds=1)
        state = {"waiting": {SHA_B: {"pr": 8, "since": pc.iso(old)}},
                 "results": {}, "refused": {},
                 "reruns": {SHA_A: self.entry(old), SHA_B: self.entry(old),
                            "c" * 40: self.entry(T0)}}
        pc.prune(state, T0)
        self.assertEqual(sorted(state["reruns"]), [SHA_B, "c" * 40])
        state["waiting"] = {}
        pc.prune(state, T0)
        self.assertEqual(sorted(state["reruns"]), ["c" * 40])

    def test_none_left_drops_the_key(self):
        old = T0 - pc.KEEP - datetime.timedelta(seconds=1)
        state = {"waiting": {}, "results": {}, "refused": {},
                 "reruns": {SHA_A: self.entry(old)}}
        pc.prune(state, T0)
        self.assertNotIn("reruns", state)

    def test_steps_the_red_run_passed_may_be_reused(self):
        first = {"pr": 7, "ok": False, "failed": ["t"],
                 "finished": pc.iso(T0), "digests": {"s": "d", "t": "e"},
                 "host": socket.gethostname(), "steps": pc.steps_digest(),
                 "step_seconds": {"s": 5, "t": 5}}
        state = {"results": {}, "reruns": {SHA_A: {"verdict": first}}}
        with mock.patch.object(pc, "REUSE_AGE", datetime.timedelta(days=7)):
            self.assertEqual(pc.reusable(state, "s", "d", T0)["reused"],
                             SHA_A)
            self.assertIsNone(pc.reusable(state, "t", "e", T0))


class HostFaultText(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def configured(self, texts):
        data = example()
        data["checks"]["host_fault_text"] = texts
        pc.configure(config.Project(data, None))

    def step(self, tail, rc=1):
        return [{"name": "audit", "rc": rc, "tail": tail}]

    def test_off_unless_configured(self):
        self.assertEqual(pc.HOST_FAULT_EXTRA, ())
        self.assertIsNone(pc.host_fault(self.step(
            "registry returned an error\n")))

    def test_a_configured_text_is_the_host_s_fault_and_named(self):
        self.configured(["registry returned an error"])
        self.assertEqual(pc.host_fault(self.step(
            "warn x\nregistry returned an error\n")), (
            "audit", 'its output says "registry returned an error"'))

    def test_the_built_ins_still_count(self):
        self.configured(["something else"])
        self.assertEqual(pc.host_fault(self.step("ENOSPC\n")),
                         ("audit", "no space left on device"))

    def test_only_a_failed_step_counts(self):
        self.configured(["registry down"])
        self.assertIsNone(pc.host_fault(self.step("registry down", rc=0)))

    def test_an_instance_inherits_it(self):
        data = example()
        data["checks"]["host_fault_text"] = ["registry down"]
        data["checks"]["instances"] = {"mac": {
            "context": "other/check", "steps": [{"name": "t",
                                                 "run": ["true"]}]}}
        pc.configure(config.Project(data, None), "mac")
        self.assertEqual(pc.HOST_FAULT_EXTRA, ("registry down",))


class HostFaultTextWorker(base.StateCase):

    work, queue = base.Worker.work, base.Worker.queue
    faulty = base.Worker.faulty

    def tearDown(self):
        pc.configure(config.Project(example(), None))
        super().tearDown()

    def test_the_worker_records_no_verdict_and_tries_again(self):
        data = example()
        data["checks"]["host_fault_text"] = ["registry down"]
        pc.configure(config.Project(data, None))
        self.queue()
        self.assertEqual(self.work(self.faulty(
            text="error: registry down\n")), 2)
        state = self.state()
        self.assertNotIn(SHA_A, state.get("results", {}))
        self.assertEqual(state["waiting"][SHA_A]["faults"], 1)
        self.assertIn('HOST-FAULT #7 aaaaaaaaaa: test: its output says '
                      '"registry down"; no verdict', pathlib.Path(
                          self.state_dir, "verdicts.log").read_text())


if __name__ == "__main__":
    unittest.main()


class MainRerun(unittest.TestCase):
    """labels.rerun on the open checks.main_red_issue issue runs the
    default branch's red head again: it has no pull request to carry
    the label."""

    def setUp(self):
        configured()
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.state = {"results": {SHA_A: dict(red(0), main=True)},
                      "waiting": {}, "main_red": {"issue": 92}}

    def labels(self, *names):
        return FakeRun((lambda a: a[:3] == ["gh", "api", "repos/%s/issues/92"
                                            % pc.REPO_SLUG],
                        done([], 0, "".join(n + "\n" for n in names))))

    def test_the_label_on_the_main_red_issue_names_the_head(self):
        got = pc.main_rerun_labelled(self.state, (SHA_A, "t"), "tok",
                                     self.labels("bug", RERUN))
        self.assertEqual(got, [(92, SHA_A, False, None)])

    def test_nothing_without_the_label_an_issue_or_the_setting(self):
        self.assertEqual(pc.main_rerun_labelled(
            self.state, (SHA_A, "t"), "tok", self.labels("bug")), [])
        self.assertEqual(pc.main_rerun_labelled(
            {"results": {}, "waiting": {}}, (SHA_A, "t"), "tok",
            self.labels(RERUN)), [])
        configured(rerun=None)
        self.assertEqual(pc.main_rerun_labelled(
            self.state, (SHA_A, "t"), "tok", self.labels(RERUN)), [])

    def test_the_red_head_is_queued_again_and_kept_queued(self):
        lines = []
        with mock.patch.object(pc, "log", lambda *a, **k: lines.append(a[1])):
            todo = pc.take_reruns(self.state, [(92, SHA_A, False, None)],
                                  {SHA_A: (LABELLER, AT)}, T0, "/s")
        self.assertEqual(todo, [(92, SHA_A, "rerun", LABELLER)])
        self.assertNotIn(SHA_A, self.state["results"])
        self.assertEqual(self.state["waiting"][SHA_A],
                         {"pr": 0, "since": pc.iso(T0), "main": True,
                          "rerun": True})
        self.assertTrue(lines[0].startswith("RERUN #92 "))
        # A tick whose records say nothing needs tracing keeps it.
        self.assertFalse(pc.queue_main(self.state, (SHA_A, "t"), T0, "/s",
                                       needs=[]))
        self.assertIn(SHA_A, self.state["waiting"])

    def test_only_once(self):
        with mock.patch.object(pc, "log", lambda *a, **k: None):
            pc.take_reruns(self.state, [(92, SHA_A, False, None)],
                           {SHA_A: (LABELLER, AT)}, T0, "/s")
        self.state["waiting"].pop(SHA_A)
        self.state["results"][SHA_A] = dict(red(0), main=True)
        with mock.patch.object(pc, "log", lambda *a, **k: None):
            todo = pc.take_reruns(self.state, [(92, SHA_A, False, None)],
                                  {SHA_A: (LABELLER, AT_2)}, T0, "/s")
        self.assertEqual(todo, [(92, SHA_A, "again", LABELLER)])
