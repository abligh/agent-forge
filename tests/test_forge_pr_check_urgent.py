"""Tests for `labels.urgent` in forge-pr-check: a pull request carrying
the label is checked before every other."""

import json
import unittest

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import (REPO, SHA_A, SHA_B, SHA_C, FakeRun,
                                       T0, done, is_pulls, pc)

LEAD = "leaddevwidgetagent[bot]"
URGENT = "Priority:urgent"


def configured(urgent=URGENT, first=()):
    data = example()
    data.setdefault("labels", {})
    if urgent:
        data["labels"]["urgent"] = urgent
    data["checks"]["first_authors"] = list(first)
    data["checks"]["max_wait_minutes"] = 30
    pc.configure(config.Project(data, None))


class UrgentConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_a_label_name(self):
        data = example()
        data.setdefault("labels", {})["urgent"] = URGENT
        self.assertEqual(config.check(data), [])
        data["labels"]["urgent"] = 3
        self.assertEqual(len(config.check(data)), 1)

    def test_off_unless_configured(self):
        self.assertIsNone(pc.URGENT_LABEL)
        configured()
        self.assertEqual(pc.URGENT_LABEL, URGENT)


class UrgentOrder(unittest.TestCase):

    def setUp(self):
        configured(first=["LeadDev"])

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def state(self):
        return {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z",
                    "author": LEAD},
            SHA_C: {"pr": 3, "since": "2026-10-09T11:00:00Z",
                    "urgent": True}}}

    def test_before_first_authors_and_the_merger(self):
        self.assertEqual(pc.next_head(self.state(), first_pr=1), (SHA_C, 3))

    def test_before_an_overdue_head(self):
        when = pc.parse("2026-10-09T12:00:00Z")
        self.assertEqual(pc.next_head(self.state(), when=when), (SHA_C, 3))

    def test_one_that_failed_to_prepare_still_goes_behind(self):
        state = self.state()
        state["waiting"][SHA_C]["errors"] = 1
        self.assertEqual(pc.next_head(state), (SHA_B, 2))

    def test_several_go_oldest_first(self):
        state = self.state()
        state["waiting"][SHA_A]["urgent"] = True
        self.assertEqual(pc.next_head(state), (SHA_A, 1))

    def test_first_in_the_fast_lane_too(self):
        self.assertEqual(pc.lane_head(self.state(), {SHA_A, SHA_C}),
                         (SHA_C, 3))


class UrgentHeads(unittest.TestCase):

    def setUp(self):
        configured()

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_open_heads_reads_the_labels(self):
        text = "7\t%s\t%s\tfalse\t%s\t%s\n" % (
            SHA_A, REPO, LEAD, json.dumps([URGENT, "bug"]))
        run = FakeRun((is_pulls, done([], 0, text)))
        self.assertEqual(pc.open_heads("t", run),
                         [(7, SHA_A, REPO, False, LEAD, [URGENT, "bug"])])

    def test_unreadable_labels_are_none(self):
        text = "7\t%s\t%s\tfalse\t%s\tnot json\n" % (SHA_A, REPO, LEAD)
        run = FakeRun((is_pulls, done([], 0, text)))
        self.assertEqual(pc.open_heads("t", run)[0][5], [])

    def test_the_label_marks_the_head_and_its_removal_unmarks_it(self):
        state = {"waiting": {}, "results": {}, "refused": {}}
        quiet = lambda *a: None
        pc.note_heads(state, [(7, SHA_A, REPO, False, LEAD, [URGENT])], T0,
                      None, out=quiet)
        self.assertTrue(state["waiting"][SHA_A]["urgent"])
        pc.note_heads(state, [(7, SHA_A, REPO, False, LEAD, ["bug"])], T0,
                      None, out=quiet)
        self.assertNotIn("urgent", state["waiting"][SHA_A])

    def test_nothing_is_urgent_without_the_setting(self):
        pc.configure(config.Project(example(), None))
        state = {"waiting": {}, "results": {}, "refused": {}}
        pc.note_heads(state, [(7, SHA_A, REPO, False, LEAD, [URGENT])], T0,
                      None, out=lambda *a: None)
        self.assertNotIn("urgent", state["waiting"][SHA_A])


if __name__ == "__main__":
    unittest.main()
