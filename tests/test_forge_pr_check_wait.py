"""Tests for `checks.max_wait_minutes`: a pull request's head that has
waited that long goes before every other pull request's but the
Merger's own head, so priority (first_authors, the Merger's
predictions) can't starve it."""

import datetime
import unittest

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import SHA_A, SHA_B, SHA_C, T0, pc

MINUTE = datetime.timedelta(minutes=1)
LEAD = "leaddevwidgetagent[bot]"


def configured(**checks):
    data = example()
    data["checks"].update(checks)
    pc.configure(config.Project(data, None))


class MaxWaitConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def problems(self, value):
        data = example()
        data["checks"]["max_wait_minutes"] = value
        return config.check(data)

    def test_whole_minutes_at_least_one(self):
        self.assertEqual(self.problems(30), [])
        self.assertEqual(self.problems(0), [
            "checks.max_wait_minutes must be at least 1"])
        self.assertEqual(self.problems("30"), [
            "checks.max_wait_minutes must be an integer"])

    def test_off_unless_configured(self):
        self.assertIsNone(pc.MAX_WAIT)
        configured(max_wait_minutes=30)
        self.assertEqual(pc.MAX_WAIT, 30 * MINUTE)


class MaxWaitOrder(unittest.TestCase):

    def setUp(self):
        configured(max_wait_minutes=30, first_authors=["LeadDev"])

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def state(self):
        return {"waiting": {
            SHA_A: {"pr": 1, "since": pc.iso(T0 - 40 * MINUTE)},
            SHA_B: {"pr": 2, "since": pc.iso(T0 - 5 * MINUTE),
                    "author": LEAD},
            SHA_C: {"pr": 3, "since": pc.iso(T0 - 10 * MINUTE)}}}

    def test_an_overdue_head_goes_before_first_authors(self):
        self.assertEqual(pc.next_head(self.state(), when=T0), (SHA_A, 1))

    def test_but_not_before_the_mergers_pull_request(self):
        # Ten heads waited, all overdue, and the Merger's (just updated
        # onto main) went behind each: nothing merged for half an hour.
        state = self.state()
        del state["waiting"][SHA_B]["author"]
        self.assertEqual(pc.next_head(state, first_pr=3, when=T0),
                         (SHA_C, 3))
        # first_authors still go before the Merger's, as without the cap.
        self.assertEqual(pc.next_head(self.state(), first_pr=3, when=T0),
                         (SHA_B, 2))

    def test_otherwise_the_order_is_as_before(self):
        self.assertEqual(pc.next_head(self.state(), when=T0 - 15 * MINUTE),
                         (SHA_B, 2))

    def test_the_oldest_overdue_first(self):
        state = self.state()
        state["waiting"][SHA_C]["since"] = pc.iso(T0 - 50 * MINUTE)
        self.assertEqual(pc.next_head(state, when=T0), (SHA_C, 3))

    def test_one_that_failed_to_prepare_is_never_overdue(self):
        state = self.state()
        state["waiting"][SHA_A]["errors"] = 1
        self.assertEqual(pc.next_head(state, when=T0), (SHA_B, 2))

    def test_off_changes_nothing(self):
        configured(first_authors=["LeadDev"])
        self.assertEqual(pc.next_head(self.state(), when=T0), (SHA_B, 2))
