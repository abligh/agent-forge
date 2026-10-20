"""Tests for checks.instances (designs/0021): a second instance of the
build check, on another host, with its own context, steps, poster,
resources and state directory."""

import json
import os
import unittest

from agent_forge import config
from tests.helpers import example
from tests import test_forge_pr_check as base
from tests.test_forge_pr_check import T0, StateCase, is_mint, pc

MAC_STEPS = [{"name": "mac", "run": ["make", "mac"]}]


def with_mac(**block):
    """The example configuration with a `mac` instance and an App of its
    own to post as."""
    data = example()
    data["machinery"]["MacChecker"] = {"app_id": None,
                                       "installation_id": None}
    data["checks"].update({"depends": True, "fast_lane": {"max_seconds": 60},
                           "cpu_weight": 50, "drop_env": ["SECRET_*"]})
    entry = {"host": "mac-1", "context": "ci/mac", "steps": MAC_STEPS,
             "poster": "MacChecker"}
    entry.update(block)
    data["checks"]["instances"] = {"mac": entry}
    return data


class Validation(unittest.TestCase):

    def test_a_block_is_accepted(self):
        self.assertEqual(config.check(with_mac(slots=1, memory_max=None)), [])

    def test_memory_max_may_be_null(self):
        data = example()
        data["checks"]["memory_max"] = None
        self.assertEqual(config.check(data), [])

    def test_context_and_steps_are_required(self):
        data = with_mac()
        del data["checks"]["instances"]["mac"]["context"]
        del data["checks"]["instances"]["mac"]["steps"]
        self.assertEqual(config.check(data), [
            "checks.instances.mac: missing 'context'",
            "checks.instances.mac: missing 'steps'"])

    def test_the_mergers_keys_are_refused(self):
        problems = config.check(with_mac(
            merger_first=True, first_authors=["LeadDev"],
            main_red_issue={"label": "red"}))
        self.assertEqual(sorted(problems), sorted(
            "checks.instances.mac.%s: only checks may set it, since it "
            "belongs to the context the Merger requires" % key
            for key in ("merger_first", "first_authors", "main_red_issue")))

    def test_an_unknown_key_is_refused(self):
        self.assertEqual(config.check(with_mac(slot=2)), [
            "checks.instances.mac: unknown key 'slot'"])

    def test_a_blocks_own_keys_are_checked_under_its_name(self):
        self.assertEqual(config.check(with_mac(
            slots=0, poster="Nobody", host=7,
            steps=[{"run": ["x"]}])), [
            "checks.instances.mac.host must be a string",
            "checks.instances.mac.slots must be at least 1",
            "checks.instances.mac.steps[0]: missing 'name'",
            "checks.instances.mac.poster 'Nobody' is no App in the "
            "configuration"])

    def test_the_tracers_keys_need_the_blocks_own_depends(self):
        # checks.depends is true, but a block doesn't inherit it.
        self.assertEqual(config.check(with_mac(fast_lane={"max_seconds": 9})),
                         ["checks.instances.mac.fast_lane needs "
                          "checks.instances.mac.depends: without it every "
                          "head runs every step, and none is cheap"])
        self.assertEqual(config.check(with_mac(
            depends=True, fast_lane={"max_seconds": 9})), [])

    def test_a_name_fit_for_a_directory(self):
        data = with_mac()
        data["checks"]["instances"]["Mac/1"] = \
            data["checks"]["instances"].pop("mac")
        self.assertEqual(config.check(data), [
            "checks.instances.Mac/1: an instance's name is lower-case "
            "letters, digits and hyphens"])


class Resolve(unittest.TestCase):

    def test_the_block_overrides_and_inherits(self):
        section, host = config.instance_checks(with_mac(slots=1), "mac")
        self.assertEqual(host, "mac-1")
        self.assertEqual(section["context"], "ci/mac")
        self.assertEqual(section["steps"], MAC_STEPS)
        self.assertEqual(section["poster"], "MacChecker")
        self.assertEqual(section["slots"], 1)
        # Inherited from checks:
        self.assertEqual(section["memory_max"], "16G")
        self.assertEqual(section["cpu_weight"], 50)
        self.assertEqual(section["drop_env"], ["SECRET_*"])
        self.assertEqual(section["caches"], {"CARGO_TARGET_DIR":
                                             "cargo-target"})

    def test_the_tracers_and_the_mergers_keys_are_not_inherited(self):
        section, _ = config.instance_checks(with_mac(), "mac")
        for key in config.INSTANCE_BLOCK_ONLY + config.INSTANCE_CHECKS_ONLY:
            self.assertNotIn(key, section)

    def test_host_and_poster_fall_back(self):
        data = with_mac()
        del data["checks"]["instances"]["mac"]["host"]
        del data["checks"]["instances"]["mac"]["poster"]
        section, host = config.instance_checks(data, "mac")
        self.assertEqual(host, "build.example.org")
        self.assertEqual(section["poster"], "Merger")

    def test_an_unknown_name_lists_the_names(self):
        with self.assertRaises(KeyError) as err:
            config.instance_checks(with_mac(), "win")
        self.assertIn("instances: mac", str(err.exception))


class Configure(StateCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))
        super().tearDown()

    SETTINGS = ("STATUS_CONTEXT", "POSTER", "HOST", "STEPS", "CACHES",
                "SLOTS", "MEMORY_MAX", "CPU_WEIGHT", "STEP_TIMEOUT_S",
                "MERGER_FIRST", "TEST_AHEAD", "DEPENDS", "TRACER",
                "FAST_LANE_S", "FIRST_AUTHORS", "MAIN_EVERY",
                "MAIN_RED_ISSUE", "DROP_PREFIXES", "INSTANCE")

    def settings(self):
        return {name: getattr(pc, name) for name in self.SETTINGS}

    def test_without_an_instance_a_block_changes_nothing(self):
        data = with_mac()
        plain = with_mac()
        del plain["checks"]["instances"]
        pc.configure(config.Project(plain, None))
        before = self.settings()
        pc.configure(config.Project(data, None))
        self.assertEqual(self.settings(), before)
        self.assertIsNone(pc.INSTANCE)

    def test_an_instance_runs_its_own_block(self):
        pc.configure(config.Project(with_mac(memory_max=None), None), "mac")
        self.assertEqual(pc.STATUS_CONTEXT, "ci/mac")
        self.assertEqual(pc.POSTER, "MacChecker")
        self.assertEqual(pc.HOST, "mac-1")
        self.assertEqual(pc.STEPS, [("mac", ["make", "mac"], None)])
        self.assertEqual(pc.CPU_WEIGHT, 50)
        self.assertIsNone(pc.MEMORY_MAX)
        # Not the first host's tracer, nor the Merger's keys.
        self.assertFalse(pc.DEPENDS)
        self.assertIsNone(pc.FAST_LANE_S)
        self.assertFalse(pc.MERGER_FIRST)
        self.assertFalse(pc.TEST_AHEAD)

    def test_memory_max_null_starts_no_scope_and_says_nothing(self):
        pc.configure(config.Project(with_mac(memory_max=None), None), "mac")
        self.assertIsNone(pc.memory_max_from_env({}))
        said = []
        self.assertEqual(pc.memory_cap(None, {}, run=None, out=said.append),
                         [])
        self.assertEqual(said, [])

    def test_log_lines_carry_the_instances_name(self):
        pc.configure(config.Project(with_mac(), None), "mac")
        pc.log(self.state_dir, "GREEN #7 abc on mac-1", T0,
               self.lines.append)
        pc.log(self.state_dir, "IDLE", T0, self.lines.append)
        self.assertEqual([l.split(" ", 1)[1] for l in self.lines],
                         ["GREEN mac #7 abc on mac-1", "IDLE mac"])

    def test_without_an_instance_log_lines_are_as_before(self):
        pc.log(self.state_dir, "GREEN #7 abc", T0, self.lines.append)
        self.assertEqual(self.lines[0].split(" ", 1)[1], "GREEN #7 abc")


class InstanceMain(base.Main):
    """forge-pr-check's main() with an instance (and Main's own tests,
    which must still pass with a block present)."""

    def setUp(self):
        super().setUp()
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(with_mac(), f)

    def test_an_unknown_instance_is_refused_with_the_names(self):
        rc, _ = self.main("--instance", "win")
        self.assertEqual(rc, 2)
        self.assertEqual(self.lines, ["NO-INSTANCE: no checks.instances.win "
                                      "(instances: mac)"])

    def test_the_environment_selects_it(self):
        rc, _ = self.main(environ={"FORGE_PR_CHECK_INSTANCE": "win"})
        self.assertEqual(rc, 2)
        self.assertTrue(self.lines[0].startswith("NO-INSTANCE"))

    def test_it_mints_as_its_poster(self):
        rc, run = self.main("--instance", "mac")
        self.assertEqual(rc, 0)
        mint = [c for c in run.calls if is_mint(c["args"])][0]
        self.assertEqual(mint["args"][1:], ["--token", "MacChecker"])

    def test_its_own_state_directory(self):
        rc, _ = self.main("--instance", "mac", state=False)
        self.assertEqual(rc, 0)
        root = os.path.join(self._tmp.name, ".local", "state", "agent-forge",
                            example()["project"])
        self.assertTrue(os.path.isdir(os.path.join(root, "pr-check-mac")))
        self.assertFalse(os.path.exists(os.path.join(root, "pr-check")))

    def test_without_one_the_state_directory_is_as_before(self):
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(example(), f)
        rc, _ = self.main(state=False)
        self.assertEqual(rc, 0)
        root = os.path.join(self._tmp.name, ".local", "state", "agent-forge",
                            example()["project"])
        self.assertIn("pr-check", os.listdir(root))
        self.assertFalse([d for d in os.listdir(root)
                          if d.startswith("pr-check-")])


if __name__ == "__main__":
    unittest.main()
