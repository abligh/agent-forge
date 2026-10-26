"""Tests for checks.instances (designs/0021): a second instance of the
build check, on another host, with its own context, steps, poster,
resources and state directory."""

import datetime
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
            merger_first=True, first_authors=["LeadDev"]))
        self.assertEqual(sorted(problems), sorted(
            "checks.instances.mac.%s: only checks may set it, since it "
            "belongs to the context the Merger requires" % key
            for key in ("merger_first", "first_authors")))

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


SCOPE = {"paths": ["desktop/mac/*", "crates/*/src/mac/*"], "label": "Check:mac"}


class PullRequestsValidation(unittest.TestCase):
    """designs/0034: a block's pull_requests."""

    def test_a_block_may_set_it(self):
        self.assertEqual(config.check(with_mac(pull_requests=SCOPE)), [])
        self.assertEqual(config.check(with_mac(
            pull_requests={"label": "Check:mac"})), [])

    def test_checks_itself_may_not(self):
        data = example()
        data["checks"]["pull_requests"] = SCOPE
        self.assertEqual(config.check(data), [
            "checks.pull_requests: only an instance may set it, since the "
            "Merger requires checks.context on every head"])

    def test_it_needs_paths_or_a_label(self):
        self.assertEqual(config.check(with_mac(pull_requests={})), [
            "checks.instances.mac.pull_requests needs paths or a label: "
            "otherwise no pull request is run"])
        self.assertEqual(config.check(with_mac(pull_requests={
            "paths": ["", 3], "label": " ", "path": []})), [
            "checks.instances.mac.pull_requests: unknown key 'path'",
            "checks.instances.mac.pull_requests.paths: each must be a "
            "non-empty string",
            "checks.instances.mac.pull_requests.label must not be empty"])

    def test_configure_reads_it_for_the_instance_only(self):
        self.addCleanup(pc.configure, config.Project(example(), None))
        pc.configure(config.Project(with_mac(pull_requests=SCOPE), None),
                     "mac")
        self.assertEqual(pc.PULL_REQUESTS, (tuple(SCOPE["paths"]),
                                            "Check:mac"))
        pc.configure(config.Project(with_mac(pull_requests=SCOPE), None))
        self.assertIsNone(pc.PULL_REQUESTS)


def is_files(args):
    return args[:2] == ["gh", "api"] and any("/files?" in a for a in args)


class PullRequests(StateCase):
    """designs/0034: an instance with pull_requests runs only the heads
    whose pull requests change its paths or carry its label."""

    def setUp(self):
        super().setUp()
        pc.configure(config.Project(with_mac(pull_requests=SCOPE), None),
                     "mac")
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.files = {7: "desktop/mac/App.swift\nREADME.md\n",
                      8: "web/src/app.ts\n", 9: "docs/x.md\n"}

    def tick(self, heads, post=None, files_rc=0):
        rows = "".join("%d\t%s\t%s\t%s\t\t%s\n" % (pr, sha, base.REPO, draft,
                                                    json.dumps(labels))
                       for pr, sha, draft, labels in heads)

        def files(args):
            pr = int([a for a in args if "/files?" in a][0]
                     .split("/pulls/")[1].split("/")[0])
            return base.done(args, files_rc, self.files.get(pr, ""))
        self.run = base.FakeRun((base.is_pulls, base.done([], 0, rows)),
                                (is_files, files))
        self.spawned = []
        return pc.tick(self.state_dir, "tok", self.run,
                       lambda *a: self.spawned.append(a), self.lines.append,
                       when=T0, post=post, slots=1)

    def file_calls(self):
        return [c for c in self.run.calls if is_files(c["args"])]

    def test_only_a_head_that_changes_its_paths_is_queued(self):
        posts = []
        self.tick([(7, base.SHA_A, "false", []), (8, base.SHA_B, "false", [])],
                  post=lambda *a: posts.append(a) or True)
        self.assertEqual(sorted(self.state()["waiting"]), [base.SHA_A])
        self.assertEqual([p[0] for p in posts], [base.SHA_A])
        self.assertIn("OUT-OF-SCOPE mac #8 bbbbbbbbbb: changes no path this "
                      "instance runs, and has no Check:mac label",
                      [l.split(" ", 1)[1] for l in self.lines])

    def test_a_star_crosses_directories(self):
        self.assertTrue(pc.touches(["desktop/mac/Sub/App.swift"],
                                   ["desktop/mac/*"]))
        self.assertTrue(pc.touches(["crates/widget-native/src/mac/a.rs"],
                                   ["crates/*/src/mac/*"]))
        self.assertFalse(pc.touches(["desktop/macos.md"], ["desktop/mac/*"]))

    def test_the_label_runs_any_head_without_reading_its_files(self):
        self.tick([(8, base.SHA_B, "false", ["Check:mac"])])
        self.assertEqual(sorted(self.state()["waiting"]), [base.SHA_B])
        self.assertEqual(self.file_calls(), [])

    def test_a_label_added_later_queues_the_head(self):
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(self.state()["waiting"], {})
        self.tick([(8, base.SHA_B, "false", ["Check:mac"])])
        self.assertEqual(sorted(self.state()["waiting"]), [base.SHA_B])

    def test_files_are_read_once_a_head_and_said_once(self):
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(len(self.file_calls()), 1)
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(self.file_calls(), [])
        self.assertEqual(sum("OUT-OF-SCOPE" in l for l in self.lines), 1)

    def test_a_head_whose_files_cant_be_read_waits_for_them(self):
        self.tick([(7, base.SHA_A, "false", [])], files_rc=1)
        self.assertEqual(self.state()["waiting"], {})
        self.assertIn("SCOPE-UNKNOWN #7 aaaaaaaaaa: could not list its "
                      "files; asked again next tick", self.lines)
        self.tick([(7, base.SHA_A, "false", [])])
        self.assertEqual(sorted(self.state()["waiting"]), [base.SHA_A])

    def test_a_queued_head_left_out_is_posted_not_run(self):
        # Queued before the block had pull_requests, or while labelled.
        self.tick([(8, base.SHA_B, "false", ["Check:mac"])])
        posts = []
        self.tick([(8, base.SHA_B, "false", [])],
                  post=lambda *a: posts.append(a) or True)
        self.assertEqual(self.state()["waiting"], {})
        self.assertEqual(posts, [(base.SHA_B, "success",
                                  pc.OUT_OF_SCOPE_TEXT)])

    def test_drafts_are_not_read(self):
        self.tick([(8, base.SHA_B, "true", [])])
        self.assertEqual(self.file_calls(), [])

    def test_without_it_every_head_runs(self):
        pc.configure(config.Project(with_mac(), None), "mac")
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(sorted(self.state()["waiting"]), [base.SHA_B])
        self.assertEqual(self.file_calls(), [])


class MainNewest(StateCase):
    """designs/0034: an instance checks the default branch's newest tip
    first, and keeps its own red-default-branch issue."""

    def tearDown(self):
        pc.configure(config.Project(example(), None))
        super().tearDown()

    def test_a_block_may_set_main_newest_and_its_own_red_issue(self):
        self.assertEqual(config.check(with_mac(
            main="newest", main_red_issue={"label": "mac-red"})), [])
        self.assertEqual(config.check(with_mac(
            main_red_issue={"label": "mac-red"})), [
            "checks.instances.mac.main_red_issue needs "
            "checks.instances.mac.main \"every\" or \"newest\" (or "
            "checks.instances.mac.depends): otherwise the default branch "
            "is never checked"])

    def test_the_red_issue_is_not_inherited(self):
        data = with_mac(main="newest")
        data["checks"].update(main="every", main_red_issue={"label": "red"})
        pc.configure(config.Project(data, None), "mac")
        self.assertIsNone(pc.MAIN_RED_ISSUE)
        data["checks"]["instances"]["mac"]["main_red_issue"] = {
            "label": "mac-red"}
        pc.configure(config.Project(data, None), "mac")
        self.assertEqual(pc.MAIN_RED_ISSUE, {"label": "mac-red"})
        self.assertTrue(pc.MAIN_EVERY)
        self.assertTrue(pc.MAIN_NEWEST)

    def waiting(self, since_main):
        return {"waiting": {
            base.SHA_A: {"pr": 7, "since": pc.iso(T0 - datetime.timedelta(
                hours=2))},
            base.SHA_B: {"pr": 0, "main": True, "since": pc.iso(since_main)}},
            "results": {}, "refused": {}}

    def test_the_newest_tip_goes_before_any_pull_request_at_once(self):
        pc.configure(config.Project(with_mac(main="newest"), None), "mac")
        self.assertEqual(pc.next_head(self.waiting(T0), when=T0),
                         (base.SHA_B, 0))

    def test_with_every_it_still_waits_its_turn(self):
        pc.configure(config.Project(with_mac(main="every"), None), "mac")
        self.assertEqual(pc.next_head(self.waiting(T0), when=T0),
                         (base.SHA_A, 7))

    def test_a_tip_overtaken_while_waiting_is_dropped(self):
        pc.configure(config.Project(with_mac(main="newest"), None), "mac")
        state = self.waiting(T0)
        self.assertTrue(pc.queue_main(state, (base.SHA_C, "t"), T0,
                                      self.state_dir))
        self.assertEqual(sorted(s for s, w in state["waiting"].items()
                                if w.get("main")), [base.SHA_C])

    def test_its_issue_names_its_context(self):
        pc.configure(config.Project(with_mac(
            main="newest", main_red_issue={"label": "mac-red"}), None), "mac")
        text = pc.red_text(base.SHA_C, [("test-ios", "boom")], [], None)
        self.assertTrue(text.startswith("`main` is red for `ci/mac` at "
                                        "cccccccccc on mac-1: test-ios."))
        self.assertIn("closed when a later tip is green for `ci/mac`", text)
