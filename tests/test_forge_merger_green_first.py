"""Tests for merge.test_ahead.green_batch_first in forge-merger
(designs/0047, amendment of 2026-11-10): a batch green on the default
branch's tip merges before the pull request in flight while that one's
own run hasn't finished."""

import json
import os
import tempfile
import unittest

from agent_forge import config
from tests.helpers import example
from tests.test_forge_merger import m, pr, project

TIP = "0" * 40
HEAD_3, HEAD_4, HEAD_9 = "3" * 40, "4" * 40, "9" * 40


class GreenBatchFirst(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = tmp.name
        # The batch #3, #4 predicted on TIP; #9 in flight.
        self.queue = [pr(9, head=HEAD_9), pr(3, head=HEAD_3),
                      pr(4, head=HEAD_4)]
        self.write(TIP)
        self.check = {"results": {"p" * 40: {"tree": "t4", "ok": True}}}

    def write(self, base):
        with open(os.path.join(self.dir, m.AHEAD_FILE), "w") as handle:
            json.dump({"base": base, "entries": [
                {"pr": 3, "head": HEAD_3, "sha": "a" * 40, "tree": "t3"},
                {"pr": 4, "head": HEAD_4, "sha": "p" * 40, "tree": "t4"}],
                "batch": {"size": 2, "trees": ["t3", "t4"]}}, handle)

    def first(self, in_flight=9, tip=TIP, check=None):
        return m.green_batch_first(self.dir, self.queue,
                                   self.check if check is None else check,
                                   in_flight, tip)

    def test_a_green_batch_on_the_tip_goes_first(self):
        self.assertEqual(self.first(), (["t3", "t4"], {3, 4}))

    def test_not_while_the_top_is_not_green(self):
        self.assertIsNone(self.first(check={"results": {}}))
        self.assertIsNone(self.first(check={"results": {
            "p" * 40: {"tree": "t4", "ok": False}}}))

    def test_not_on_another_tip(self):
        self.assertIsNone(self.first(tip="1" * 40))

    def test_not_once_the_one_in_flight_has_its_verdict(self):
        check = {"results": dict(self.check["results"],
                                 **{HEAD_9: {"ok": True}})}
        self.assertIsNone(self.first(check=check))

    def test_not_for_a_member_or_none_in_flight(self):
        self.assertIsNone(self.first(in_flight=3))
        self.assertIsNone(self.first(in_flight=None))

    def test_the_switch_is_read(self):
        self.addCleanup(m.configure, project())
        data = example()
        data["merge"]["test_ahead"] = {"batch": 2, "green_batch_first": True}
        m.configure(config.Project(data, None))
        self.assertTrue(m.GREEN_BATCH_FIRST)
        m.configure(project())
        self.assertFalse(m.GREEN_BATCH_FIRST)


if __name__ == "__main__":
    unittest.main()
