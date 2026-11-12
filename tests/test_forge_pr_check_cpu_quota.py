"""Tests for `checks.cpu_quota`: a slot's steps share one user slice
whose CPUQuota limits the slot, not each step."""

import types
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import pc

STEPS = [("a", ["a"], None), ("b", ["b"], None), ("c", ["c"], None)]
PREFIX = ["systemd-run", "--user", "--scope", "--"]


def recorder(rc=0):
    calls = []

    def run(args, cwd=None, env=None, timeout=None, **kw):
        calls.append(list(args))
        refuse = args[:2] == ["systemctl", "--user"]
        return types.SimpleNamespace(returncode=rc if refuse else 0,
                                     stdout="", stderr="refused" if rc
                                     else "")
    run.calls = calls
    return run


def quota_calls(calls):
    return [c for c in calls if c[:2] == ["systemctl", "--user"]
            and c[2] not in ("show", "kill", "stop")]


class Slice(unittest.TestCase):

    def test_one_slice_per_state_directory_and_slot(self):
        one = pc.slot_slice("/tmp/a", 3)
        self.assertRegex(one, r"^forgecheck[0-9a-f]{8}slot3\.slice$")
        self.assertNotEqual(one, pc.slot_slice("/tmp/a", 4))
        self.assertNotEqual(one, pc.slot_slice("/tmp/b", 3))
        self.assertEqual(one, pc.slot_slice("/tmp/a", 3))

    def test_no_dash_even_for_the_fast_lane(self):
        name = pc.slot_slice("/tmp/a", pc.LANE)
        self.assertNotIn("-", name)
        self.assertTrue(name.endswith(".slice"))

    def test_the_scope_goes_in_the_slice(self):
        prefix = pc.scope_prefix("16G", slice_="x.slice")
        self.assertIn("--slice=x.slice", prefix)
        self.assertLess(prefix.index("--slice=x.slice"), prefix.index("--"))
        self.assertNotIn("--slice=x.slice", pc.scope_prefix("16G"))

    def test_weighting_keeps_the_slice(self):
        prefix = pc.weighted(pc.scope_prefix("16G", slice_="x.slice"), 9000)
        self.assertIn("--slice=x.slice", prefix)
        self.assertIn("CPUWeight=9000", prefix)


class Cpus(unittest.TestCase):

    def test_a_number_is_itself(self):
        self.assertEqual(pc.quota_cpus(14), 14)

    def test_share_takes_the_share_now(self):
        self.assertEqual(pc.quota_cpus("share", share=lambda: 16), 16)

    def test_share_without_a_function_takes_check_cpus(self):
        self.assertEqual(pc.quota_cpus("share", env={"CHECK_CPUS": "13"}), 13)
        self.assertIsNone(pc.quota_cpus("share", env={}))

    def test_off(self):
        self.assertIsNone(pc.quota_cpus(None, share=lambda: 16))


class SlotQuota(unittest.TestCase):

    def test_set_once_per_number(self):
        run, said = recorder(), []
        set_quota = pc.slot_quota("x.slice", {}, run, said.append)
        for cpus in (14, 14, 16, None):
            set_quota(cpus)
        self.assertEqual(quota_calls(run.calls), [
            ["systemctl", "--user", "set-property", "--runtime", "x.slice",
             "CPUQuota=1400%"],
            ["systemctl", "--user", "set-property", "--runtime", "x.slice",
             "CPUQuota=1600%"]])
        self.assertEqual(said, ["  the slot's steps share x.slice, at most "
                                "14 CPUs"])

    def test_a_refusal_is_said_once(self):
        run, said = recorder(rc=1), []
        set_quota = pc.slot_quota("x.slice", {}, run, said.append)
        set_quota(14)
        set_quota(16)
        self.assertEqual(len(said), 1)
        self.assertTrue(said[0].startswith(
            "UNCAPPED-CPU: could not set x.slice's CPUQuota (rc=1: refused)"))


class Check(unittest.TestCase):

    def tearDown(self):
        pc.CPU_QUOTA = None

    def run_check(self, quota, prefix=PREFIX, share=None, run=None):
        pc.CPU_QUOTA = quota
        run = run or recorder()
        set_quota = pc.slot_quota("x.slice", {}, run, lambda line: None)
        ok, _ = pc.check("/tmp", {"CHECK_CPUS": "13"}, run, steps=STEPS,
                         prefix=prefix, quota=set_quota, share=share,
                         out=lambda line: None)
        return ok, run.calls

    def test_a_number_is_set_before_the_first_step(self):
        ok, calls = self.run_check(14)
        self.assertTrue(ok)
        self.assertEqual(calls[0][-1], "CPUQuota=1400%")
        self.assertEqual(len(quota_calls(calls)), 1)

    def test_share_follows_the_share_as_each_step_starts(self):
        # The third step starts after two have run: the share is 16 then.
        run = recorder()
        steps_run = lambda: len([c for c in run.calls
                                 if c[:2] != ["systemctl", "--user"]])
        ok, calls = self.run_check(
            "share", run=run, share=lambda: 16 if steps_run() >= 2 else 13)
        self.assertEqual([c[-1] for c in quota_calls(calls)],
                         ["CPUQuota=1300%", "CPUQuota=1600%"])
        # Set before the step that took the new share.
        steps_and_quotas = [c for c in calls
                            if c[:2] != ["systemctl", "--user"]
                            or c[2] not in ("show", "kill", "stop")]
        self.assertEqual(steps_and_quotas[-2][-1], "CPUQuota=1600%")

    def test_no_scope_no_quota(self):
        ok, calls = self.run_check(14, prefix=())
        self.assertEqual(quota_calls(calls), [])


class Configure(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_off_by_default(self):
        pc.configure(config.Project(example(), None))
        self.assertIsNone(pc.CPU_QUOTA)

    def test_taken(self):
        for value in (14, "share"):
            data = example()
            data["checks"]["cpu_quota"] = value
            self.assertEqual(config.check(data), [])
            pc.configure(config.Project(data, None))
            self.assertEqual(pc.CPU_QUOTA, value)

    def test_refused(self):
        for value in (0, -2, "half", True, 1.5):
            data = example()
            data["checks"]["cpu_quota"] = value
            problems = config.check(data)
            self.assertEqual(len(problems), 1, (value, problems))
            self.assertIn("cpu_quota", problems[0])

    def test_an_instance_inherits_it(self):
        self.assertIn("cpu_quota", config.INSTANCE_INHERITED)


class Worker(unittest.TestCase):

    def test_the_worker_passes_the_slot_slice(self):
        with mock.patch.object(pc, "CPU_QUOTA", 14):
            prefix = pc.memory_cap("16G", {}, recorder(), lambda line: None,
                                   slice_=pc.slot_slice("/tmp/s", 2))
        self.assertIn("--slice=" + pc.slot_slice("/tmp/s", 2), prefix)


if __name__ == "__main__":
    unittest.main()
