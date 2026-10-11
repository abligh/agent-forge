"""Tests for `checks.first_authors`: the pull requests of the agents it
names are checked before any other's."""

import unittest

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import (REPO, SHA_A, SHA_B, SHA_C, FakeRun,
                                       T0, done, is_pulls, pc)

LEAD = "leaddevwidgetagent[bot]"


def configured(names):
    data = example()
    data["checks"]["first_authors"] = names
    pc.configure(config.Project(data, None))


class FirstAuthorsConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def problems(self, value):
        data = example()
        data["checks"]["first_authors"] = value
        return config.check(data)

    def test_agent_names(self):
        self.assertEqual(self.problems(["LeadDev", "Reviewer"]), [])
        self.assertEqual(self.problems(["Nobody"]), [
            "checks.first_authors: 'Nobody' is not an agent"])
        self.assertEqual(self.problems("LeadDev"),
                         ["checks.first_authors must be a list"])

    def test_off_unless_configured(self):
        self.assertEqual(pc.FIRST_AUTHORS, frozenset())
        configured(["LeadDev"])
        self.assertEqual(pc.FIRST_AUTHORS, {LEAD})


class FirstAuthorsOrder(unittest.TestCase):

    def setUp(self):
        configured(["LeadDev"])

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def state(self):
        return {"waiting": {
            SHA_A: {"pr": 1, "since": "2026-10-09T09:00:00Z"},
            SHA_B: {"pr": 2, "since": "2026-10-09T10:00:00Z",
                    "author": "LeadDevWidgetAgent[bot]"},
            SHA_C: {"pr": 3, "since": "2026-10-09T11:00:00Z"}}}

    def test_their_heads_go_before_older_ones(self):
        self.assertEqual(pc.next_head(self.state()), (SHA_B, 2))

    def test_even_before_the_mergers_pull_request(self):
        self.assertEqual(pc.next_head(self.state(), first_pr=3), (SHA_B, 2))

    def test_then_the_merger_and_the_oldest_as_before(self):
        state = self.state()
        del state["waiting"][SHA_B]
        self.assertEqual(pc.next_head(state, first_pr=3), (SHA_C, 3))
        self.assertEqual(pc.next_head(state), (SHA_A, 1))

    def test_one_that_failed_to_prepare_still_goes_behind(self):
        state = self.state()
        state["waiting"][SHA_B]["errors"] = 1
        self.assertEqual(pc.next_head(state), (SHA_A, 1))

    def test_first_in_the_fast_lane_too(self):
        self.assertEqual(pc.lane_head(self.state(), {SHA_A, SHA_B}),
                         (SHA_B, 2))

    def test_nobody_first_without_it(self):
        pc.configure(config.Project(example(), None))
        self.assertEqual(pc.next_head(self.state()), (SHA_A, 1))


class Authors(unittest.TestCase):

    def test_open_heads_reads_the_author(self):
        text = "7\t%s\t%s\tfalse\t%s\n" % (SHA_A, REPO, LEAD)
        run = FakeRun((is_pulls, done([], 0, text)))
        self.assertEqual(pc.open_heads("t", run),
                         [(7, SHA_A, REPO, False, LEAD)])

    def test_note_heads_keeps_it_with_the_head(self):
        state = {"waiting": {}, "results": {}, "refused": {}}
        pc.note_heads(state, [(7, SHA_A, REPO, False, LEAD)], T0, None,
                      out=lambda *a: None)
        self.assertEqual(state["waiting"][SHA_A]["author"], LEAD)
