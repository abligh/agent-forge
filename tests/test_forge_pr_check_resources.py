"""Tests for what forge-pr-check gives each step and keeps after it:
CHECK_CPUS, a slot's share of the host; CHECK_RESULTS, a directory a
step may leave results in; and `checks.cache_limit`, the pruning of a
slot's build caches."""

import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import pc


class CheckCpus(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_the_cpus_shared_between_the_slots(self):
        self.assertEqual(pc.check_cpus(online=20, slots=2), 10)
        self.assertEqual(pc.check_cpus(online=20, slots=3), 6)

    def test_the_fast_lane_counts_as_a_slot(self):
        with mock.patch.object(pc, "FAST_LANE_S", 60):
            self.assertEqual(pc.check_cpus(online=20, slots=2), 6)

    def test_at_least_one(self):
        self.assertEqual(pc.check_cpus(online=1, slots=4), 1)

    def test_slots_from_the_environment(self):
        with mock.patch.dict("os.environ", {"FORGE_PR_CHECK_SLOTS": "4"}), \
                mock.patch.object(pc, "online_cpus", lambda: 20):
            self.assertEqual(pc.check_cpus(), 5)

    def test_every_step_gets_it_unless_the_host_set_it(self):
        with mock.patch.object(pc, "online_cpus", lambda: 8):
            self.assertEqual(pc.step_env("/s", {})["CHECK_CPUS"], "4")
            self.assertEqual(
                pc.step_env("/s", {"CHECK_CPUS": "3"})["CHECK_CPUS"], "3")

    def test_online_cpus_is_at_least_one(self):
        self.assertGreaterEqual(pc.online_cpus(), 1)


if __name__ == "__main__":
    unittest.main()
