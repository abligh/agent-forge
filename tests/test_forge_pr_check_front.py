"""Tests for designs/0031: the run the next merge waits on gets a higher
CPU weight (`checks.merger_cpu_weight`) and its containers a slice of
their own (`checks.docker_slices`)."""

import datetime
import json
import os
import tempfile
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import SHA_A, SHA_B, T0, done, pc

STEPS = [("a", ["a"], None), ("b", ["b"], None)]
SLICES = {"front": "check-front.slice", "other": "check-other.slice"}


def configured(**checks):
    data = example()
    data["checks"].update(checks)
    pc.configure(config.Project(data, None))


class Config(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def problems(self, **checks):
        data = example()
        data["checks"].update(checks)
        return config.check(data)

    def test_off_unless_configured(self):
        self.assertIsNone(pc.MERGER_CPU_WEIGHT)
        self.assertIsNone(pc.DOCKER_SLICES)
        configured(merger_cpu_weight=8000, docker_slices=SLICES)
        self.assertEqual(pc.MERGER_CPU_WEIGHT, 8000)
        self.assertEqual(pc.DOCKER_SLICES, SLICES)

    def test_the_weight_is_between_cpu_weight_and_systemds_limit(self):
        self.assertEqual(self.problems(merger_cpu_weight=8000), [])
        self.assertEqual(self.problems(merger_cpu_weight=10000), [])
        self.assertEqual(self.problems(merger_cpu_weight=500), [
            "checks.merger_cpu_weight must be at least cpu_weight (1000) "
            "and at most 10000"])
        self.assertEqual(self.problems(merger_cpu_weight=20000), [
            "checks.merger_cpu_weight must be at least cpu_weight (1000) "
            "and at most 10000"])
        self.assertEqual(self.problems(cpu_weight=200,
                                       merger_cpu_weight=500), [])
        self.assertEqual(self.problems(merger_cpu_weight="8000"), [
            "checks.merger_cpu_weight must be an integer"])

    def test_the_slices_are_two_slice_names(self):
        self.assertEqual(self.problems(docker_slices=SLICES), [])
        self.assertEqual(self.problems(docker_slices={"front": "a.slice"}), [
            "checks.docker_slices.other must name a slice (\"<name>.slice\")"])
        self.assertEqual(self.problems(docker_slices=dict(
            SLICES, other="docker")), [
            "checks.docker_slices.other must name a slice (\"<name>.slice\")"])
        self.assertIn("checks.docker_slices: unknown key 'spare'",
                      self.problems(docker_slices=dict(SLICES,
                                                       spare="x.slice")))

    def test_an_instance_may_not_set_them(self):
        for key, value in (("merger_cpu_weight", 8000),
                           ("docker_slices", SLICES)):
            data = example()
            data["checks"]["instances"] = {"mac": {
                "context": "mac", "steps": [{"name": "t", "run": ["true"]}],
                key: value}}
            self.assertIn("checks.instances.mac.%s: only checks may set it, "
                          "since it belongs to the context the Merger "
                          "requires" % key, config.check(data))


class Weighted(unittest.TestCase):

    def test_the_weight_is_replaced(self):
        got = pc.weighted(pc.scope_prefix("16G"), 8000)
        self.assertIn("CPUWeight=8000", got)
        self.assertNotIn("CPUWeight=1000", got)
        self.assertEqual(got[-1], "--")
        self.assertEqual(len(got), len(pc.scope_prefix("16G")))

    def test_a_scope_without_one_gets_it(self):
        got = pc.weighted(pc.scope_prefix("16G", 0), 8000)
        self.assertEqual(got[-3:], ["-p", "CPUWeight=8000", "--"])

    def test_no_scope_no_weight(self):
        self.assertEqual(pc.weighted([], 8000), [])


class IsFront(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = self._tmp.name

    def write(self, name, data):
        with open(os.path.join(self.dir, name), "w") as handle:
            json.dump(data, handle)

    def test_a_head_of_the_pull_request_in_next_json(self):
        self.write("next.json", {"at": pc.iso(T0), "pr": 7})
        self.assertTrue(pc.is_front(self.dir, T0, SHA_A, 7))
        self.assertFalse(pc.is_front(self.dir, T0, SHA_A, 8))
        late = T0 + pc.MERGER_FRESH + datetime.timedelta(seconds=1)
        self.assertFalse(pc.is_front(self.dir, late, SHA_A, 7))

    def test_the_first_prediction_only(self):
        entry = {"pr": 7, "sha": SHA_A, "tree": "t"}
        self.write("ahead.json", {"at": pc.iso(T0), "repo": self.dir,
                                  "entries": [entry, dict(entry, pr=8,
                                                          sha=SHA_B)]})
        self.assertTrue(pc.is_front(self.dir, T0, SHA_A, 7, ahead=True))
        self.assertFalse(pc.is_front(self.dir, T0, SHA_B, 8, ahead=True))

    def test_nothing_to_read_says_no(self):
        self.assertFalse(pc.is_front(self.dir, T0, SHA_A, 7))
        self.assertFalse(pc.is_front(self.dir, T0, SHA_A, 7, ahead=True))
        self.assertFalse(pc.is_front(None, T0, SHA_A, 7))


class Check(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.calls = []

    def run_(self, args, cwd=None, env=None, timeout=None):
        self.calls.append((args, env))
        return done(args)

    def check(self, answers, **patches):
        answers = iter(answers)
        with mock.patch.multiple(pc, **dict(
                {"MERGER_CPU_WEIGHT": 8000, "DOCKER_SLICES": None},
                **patches)):
            return pc.check(self._tmp.name, {}, self.run_, steps=STEPS,
                            prefix=pc.scope_prefix("16G"),
                            front=lambda: next(answers))

    def weights(self):
        return [[a for a in args if a.startswith("CPUWeight=")]
                for args, _ in self.calls]

    def test_the_front_runs_steps_get_the_weight(self):
        ok, results = self.check([True, True])
        self.assertTrue(ok)
        self.assertEqual(self.weights(), [["CPUWeight=8000"]] * 2)
        self.assertEqual([r.get("merger_weight") for r in results],
                         [True, True])

    def test_a_run_that_comes_to_the_front_gets_it_from_its_next_step(self):
        _, results = self.check([False, True])
        self.assertEqual(self.weights(), [["CPUWeight=1000"],
                                          ["CPUWeight=8000"]])
        self.assertEqual([r.get("merger_weight") for r in results],
                         [None, True])

    def test_off_the_weight_stays(self):
        _, results = self.check([True, True], MERGER_CPU_WEIGHT=None)
        self.assertEqual(self.weights(), [["CPUWeight=1000"]] * 2)
        self.assertFalse(any("merger_weight" in r for r in results))

    def test_each_step_gets_its_docker_slice(self):
        self.check([True, False], MERGER_CPU_WEIGHT=None,
                   DOCKER_SLICES=SLICES)
        self.assertEqual([env["CHECK_DOCKER_SLICE"] for _, env in self.calls],
                         ["check-front.slice", "check-other.slice"])

    def test_without_front_nothing_changes(self):
        with mock.patch.object(pc, "MERGER_CPU_WEIGHT", 8000), \
                mock.patch.object(pc, "DOCKER_SLICES", SLICES):
            _, results = pc.check(self._tmp.name, {}, self.run_, steps=STEPS,
                                  prefix=pc.scope_prefix("16G"))
        self.assertEqual(self.weights(), [["CPUWeight=1000"]] * 2)
        self.assertFalse(any("CHECK_DOCKER_SLICE" in env
                             for _, env in self.calls))


class StepAlert(unittest.TestCase):

    def test_weighted_runs_are_compared_only_with_their_own(self):
        when = pc.iso(T0)
        state = {"results": {
            "1" * 40: {"finished": when, "step_seconds": {"a": 10}},
            "2" * 40: {"finished": when, "step_seconds": {"a": 4},
                       "merger_weight": True}}}
        since = T0 - datetime.timedelta(hours=1)

        def took(weighted):
            return [t for _, t, _, _ in
                    pc.step_runs(state, since, weighted=weighted)["a"]]
        self.assertEqual(took(False), [10])
        self.assertEqual(took(True), [4])
        self.assertEqual(sorted(took(None)), [4, 10])


if __name__ == "__main__":
    unittest.main()
