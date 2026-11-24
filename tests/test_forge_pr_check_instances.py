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

    def test_approved_first_is_allowed_in_a_block_and_in_checks(self):
        data = with_mac(approved_first=True)
        data["checks"]["approved_first"] = False
        self.assertEqual(config.check(data), [])
        data["checks"]["instances"]["mac"]["approved_first"] = "yes"
        self.assertTrue(any("approved_first" in p
                            for p in config.check(data)))

    def test_an_unknown_key_is_refused(self):
        self.assertEqual(config.check(with_mac(slot=2)), [
            "checks.instances.mac: unknown key 'slot'"])

    def test_a_host_lock_is_checked(self):
        # agent-forge issue 54: the host's own `mkdir` lock.
        self.assertEqual(config.check(with_mac(
            lock={"dir": "~/.machine.lock", "wait_minutes": 60})), [])
        self.assertEqual(config.check(with_mac(
            lock={"wait_minutes": 0, "path": "x"})), [
            "checks.instances.mac.lock: unknown key 'path'",
            "checks.instances.mac.lock: missing 'dir'",
            "checks.instances.mac.lock.wait_minutes must be at least 1"])

    def test_a_blocks_own_keys_are_checked_under_its_name(self):
        self.assertEqual(config.check(with_mac(
            slots=0, poster="Nobody", host=7,
            steps=[{"run": ["x"]}])), [
            "checks.instances.mac.host must be a string",
            "checks.instances.mac.slots must be at least 1",
            "checks.instances.mac.steps[0]: missing 'name'",
            "checks.instances.mac.poster 'Nobody' is no App in the "
            "configuration"])

    def test_a_blocks_fast_lane_needs_no_depends(self):
        # agent-forge #261: without depends a head's chosen steps are
        # every step that applies, so a block's lane needs none either.
        self.assertEqual(config.check(with_mac(fast_lane={"max_seconds": 9})),
                         [])
        self.assertEqual(config.check(with_mac(
            depends=True, fast_lane={"max_seconds": 9})), [])

    def test_the_tracers_keys_need_the_blocks_own_depends(self):
        # checks.depends is true, but a block doesn't inherit it.
        self.assertEqual(config.check(with_mac(reuse={"max_age_days": 7})),
                         ["checks.instances.mac.reuse needs "
                          "checks.instances.mac.depends: a step's inputs are "
                          "what its dependency record says it read"])

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

    def test_approved_first_and_the_reviewers_are_read(self):
        data = with_mac(approved_first=True)
        pc.configure(config.Project(data, None), "mac")
        self.assertTrue(pc.APPROVED_FIRST)
        project = config.Project(data, None)
        self.assertEqual(pc.REVIEWER_LOGINS, frozenset(
            project.bot_login(n).lower() for n in project.reviewers()))
        pc.configure(config.Project(with_mac(), None), "mac")
        self.assertFalse(pc.APPROVED_FIRST)

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

    def rescope(self, paths):
        pc.configure(config.Project(with_mac(pull_requests=dict(
            SCOPE, paths=paths)), None), "mac")

    def test_widened_paths_judge_an_out_of_scope_head_again(self):
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(self.state()["waiting"], {})
        self.rescope(list(SCOPE["paths"]) + ["web/*"])
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(sorted(self.state()["waiting"]), [base.SHA_B])
        self.assertEqual(len(self.file_calls()), 1)

    def test_still_out_after_a_change_is_not_said_again(self):
        self.tick([(8, base.SHA_B, "false", [])])
        self.rescope(list(SCOPE["paths"]) + ["other/*"])
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(self.state()["waiting"], {})
        self.assertEqual(sum("OUT-OF-SCOPE" in l for l in self.lines), 1)
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(self.file_calls(), [])

    def test_narrowing_or_a_label_change_queues_nothing(self):
        self.tick([(8, base.SHA_B, "false", [])])
        pc.configure(config.Project(with_mac(pull_requests=dict(
            SCOPE, label="Check:other")), None), "mac")
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(self.file_calls(), [])
        self.rescope(["desktop/mac/*"])
        self.tick([(8, base.SHA_B, "false", [])])
        self.assertEqual(self.state()["waiting"], {})

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


class GateValidation(unittest.TestCase):
    """checks.instances.<name>.gate: the Merger waits for the context."""

    def test_a_block_may_set_it(self):
        self.assertEqual(config.check(with_mac(gate=True,
                                               pull_requests=SCOPE)), [])
        self.assertEqual(config.gated_instances(with_mac(
            gate=True, pull_requests=SCOPE)),
            [("mac", "ci/mac", "MacChecker", SCOPE)])
        self.assertEqual(config.gated_instances(with_mac()), [])

    def test_it_is_a_boolean(self):
        self.assertEqual(config.check(with_mac(gate="yes")), [
            "checks.instances.mac.gate must be true or false"])

    def test_checks_itself_may_not(self):
        data = example()
        data["checks"]["gate"] = True
        self.assertEqual(config.check(data), [
            "checks.gate: only an instance may set it; the Merger always "
            "requires checks.context"])



def reviewed_mac(reviewed=True, armed="Review:armed", **block):
    """with_mac, with reviewed_heads (designs/0064) and labels.armed."""
    data = with_mac(reviewed_heads=reviewed, **block)
    if armed:
        data["labels"]["armed"] = armed
    return data


class ReviewedHeadsValidation(unittest.TestCase):
    """designs/0064: checks.instances.<name>.reviewed_heads."""

    def test_a_block_may_set_it(self):
        for value in (True, False, {}, {"early_label": "run-early"}):
            self.assertEqual(config.check(reviewed_mac(value)), [], value)

    def test_checks_itself_may_not(self):
        data = example()
        data["checks"]["reviewed_heads"] = True
        self.assertEqual(config.check(data), [
            "checks.reviewed_heads: only an instance may set it, since the "
            "Merger requires checks.context on every head, and the "
            "reviewers read it"])

    def test_its_shape(self):
        self.assertEqual(config.check(reviewed_mac("yes")), [
            "checks.instances.mac.reviewed_heads must be true or false or "
            "an object"])
        self.assertEqual(config.check(reviewed_mac(
            {"early_label": " ", "label": "x"})), [
            "checks.instances.mac.reviewed_heads: unknown key 'label'",
            "checks.instances.mac.reviewed_heads.early_label must not be "
            "empty"])

    def test_it_needs_a_reviewer_to_release_a_head(self):
        data = reviewed_mac()
        data["merge"]["approval"] = "green"
        data["merge"]["own_head_green"] = True
        self.assertIn("checks.instances.mac.reviewed_heads: merge.approval "
                      "is \"green\", so no reviewer approves a head to "
                      "release it", config.check(data))
        data = reviewed_mac()
        for entry in data["agents"].values():
            if entry.get("kind") == "reviewer":
                entry["kind"] = "developer"
                entry.pop("reviews", None)
        self.assertIn("checks.instances.mac.reviewed_heads needs an agent "
                      "of kind reviewer, to release a head",
                      config.check(data))

    def test_it_warns_without_the_armed_label(self):
        self.assertEqual(config.warnings(reviewed_mac()), [])
        self.assertEqual(config.warnings(reviewed_mac(False, armed=None)),
                         [])
        self.assertEqual(config.warnings(reviewed_mac(armed=None)), [
            "checks.instances.mac.reviewed_heads without labels.armed: "
            "only an approval releases a head, so its run starts later"])

    def test_configure_reads_it_for_the_instance_only(self):
        self.addCleanup(pc.configure, config.Project(example(), None))
        pc.configure(config.Project(reviewed_mac(
            {"early_label": "run-early"}), None), "mac")
        self.assertEqual((pc.REVIEWED_HEADS, pc.EARLY_LABEL),
                         (True, "run-early"))
        pc.configure(config.Project(reviewed_mac(), None), "mac")
        self.assertEqual((pc.REVIEWED_HEADS, pc.EARLY_LABEL), (True, None))
        pc.configure(config.Project(reviewed_mac(False), None), "mac")
        self.assertFalse(pc.REVIEWED_HEADS)
        pc.configure(config.Project(reviewed_mac(), None))
        self.assertFalse(pc.REVIEWED_HEADS)


def is_reviews(args):
    return args[:2] == ["gh", "api"] and any("/reviews" in a for a in args)


def is_events(args):
    return args[:2] == ["gh", "api"] and any("/events" in a for a in args)


def is_unlabel(args):
    return args[:2] == ["gh", "api"] and "DELETE" in args


def pr_of(args):
    return int(next(a for a in args if "/pulls/" in a or "/issues/" in a)
               .replace("/issues/", "/pulls/").split("/pulls/")[1]
               .split("/")[0])


class ReviewedHeads(StateCase):
    """designs/0064: with reviewed_heads, an instance runs a head only
    once its pull request is approved by a reviewer agent, armed, or
    asked for early."""

    REVIEWER = "reviewerwidgetagent[bot]"

    def setUp(self):
        super().setUp()
        pc.configure(config.Project(reviewed_mac(
            {"early_label": "run-early"}), None), "mac")
        self.addCleanup(pc.configure, config.Project(example(), None))
        self.approvers = {}
        self.posts = []

    def tick(self, heads, posting=True, busy=(), checkout=None, git=()):
        rows = "".join("%d\t%s\t%s\t%s\t\t%s\n" % (pr, sha, base.REPO,
                                                    draft, json.dumps(labels))
                       for pr, sha, draft, labels in heads)

        def reviews(args):
            return base.done(args, 0, "".join(
                login + "\n" for login in self.approvers.get(pr_of(args),
                                                             ())))
        self.run = base.FakeRun(
            (base.is_pulls, base.done([], 0, rows)), (is_reviews, reviews),
            (is_events, base.done([], 0, "alice\t2026-10-09T11:00:00Z\n")),
            *git)
        self.spawned = []
        post = (lambda *a: self.posts.append(a) or True) if posting else None
        return pc.tick(self.state_dir, "tok", self.run,
                       lambda *a: self.spawned.append(a), self.lines.append,
                       when=T0, post=post, slots=1, checkout=checkout)

    def said(self, word):
        return [l.split(" ", 1)[1] for l in self.lines
                if l.split(" ", 2)[1:2] == [word]]

    def calls(self, match):
        return [c["args"] for c in self.run.calls if match(c["args"])]

    def test_a_head_nobody_reviewed_is_held_with_a_pending_status(self):
        self.tick([(7, base.SHA_A, "false", [])])
        self.assertEqual(self.state()["waiting"], {})
        self.assertEqual(self.spawned, [])
        self.assertEqual(self.posts, [(base.SHA_A, "pending",
                                       pc.HELD_TEXT)])
        self.assertEqual(self.said("UNREVIEWED"), [
            "UNREVIEWED mac #7 aaaaaaaaaa: runs once a reviewer approves "
            "or arms it"])
        # Said and posted once; its reviews are asked again every tick.
        self.tick([(7, base.SHA_A, "false", [])])
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.said("UNREVIEWED")), 1)
        self.assertEqual(len(self.calls(is_reviews)), 1)

    def test_an_approval_releases_it_on_the_next_tick(self):
        self.tick([(7, base.SHA_A, "false", [])])
        self.approvers[7] = [self.REVIEWER]
        self.tick([(7, base.SHA_A, "false", [])])
        self.assertEqual([s[1] for s in self.spawned], [base.SHA_A])
        self.assertEqual(self.said("RELEASED"), [
            "RELEASED mac #7 aaaaaaaaaa: approved by %s" % self.REVIEWER])

    def test_only_a_reviewer_agents_approval_releases(self):
        self.approvers[7] = ["someone"]
        self.tick([(7, base.SHA_A, "false", [])])
        self.assertEqual(self.spawned, [])

    def test_the_armed_label_releases_it(self):
        self.tick([(7, base.SHA_A, "false", ["Review:armed"])])
        self.assertEqual([s[1] for s in self.spawned], [base.SHA_A])
        self.assertEqual(self.said("RELEASED"), [
            "RELEASED mac #7 aaaaaaaaaa: armed"])
        self.assertEqual(self.said("UNREVIEWED"), [])

    def test_the_armed_label_taken_off_holds_a_head_not_started(self):
        self.tick([(7, base.SHA_A, "false", ["Review:armed"]),
                   (8, base.SHA_B, "false", ["Review:armed"])])
        self.assertIn(base.SHA_B, self.state()["waiting"])
        self.tick([(7, base.SHA_A, "false", []),
                   (8, base.SHA_B, "false", [])])
        self.assertNotIn(base.SHA_B, self.state()["waiting"])
        self.assertEqual(self.posts[-1], (base.SHA_B, "pending",
                                          pc.HELD_TEXT))

    def test_a_run_started_is_kept_when_its_release_is_taken_back(self):
        heads = [(7, base.SHA_A, base.REPO, False, "", [])]
        kept, todo = pc.reviewed(heads, self.state_dir, frozenset(), {},
                                 {base.SHA_A}, T0, out=self.lines.append,
                                 posting=True)
        self.assertEqual((kept, todo), (heads, []))

    def test_the_early_label_releases_one_head_and_comes_off(self):
        self.tick([(7, base.SHA_A, "false", ["run-early"])])
        self.assertEqual([s[1] for s in self.spawned], [base.SHA_A])
        self.assertEqual(self.said("RELEASED"), [
            "RELEASED mac #7 aaaaaaaaaa: early, asked by alice"])
        self.assertEqual(len(self.calls(is_unlabel)), 1)
        self.assertIn("repos/example/widget/issues/7/labels/run-early",
                      self.calls(is_unlabel)[0])
        # A later push needs it again.
        self.tick([(7, base.SHA_B, "false", [])])
        self.assertNotIn(base.SHA_B, self.state()["waiting"])
        self.assertEqual(self.said("UNREVIEWED"), [
            "UNREVIEWED mac #7 bbbbbbbbbb: runs once a reviewer approves "
            "or arms it"])

    def test_the_early_label_waits_for_a_posting_tick(self):
        self.tick([(7, base.SHA_A, "false", ["run-early"])], posting=False)
        self.assertEqual(self.state()["waiting"], {})
        self.assertEqual(self.calls(is_unlabel), [])

    def test_a_push_after_approval_runs_at_once(self):
        self.approvers[7] = [self.REVIEWER]
        self.tick([(7, base.SHA_A, "false", [])])
        state = self.state()
        state["waiting"].clear()
        state["results"][base.SHA_A] = {"pr": 7, "ok": True, "failed": [],
                                        "finished": pc.iso(T0),
                                        "posted": True}
        self.write_state(state)
        # The approval is kept, not asked again, while the pull request
        # is open.
        self.approvers.clear()
        self.tick([(7, base.SHA_B, "false", [])])
        self.assertEqual(sorted(self.state()["waiting"]), [base.SHA_B])
        self.assertEqual(self.calls(is_reviews), [])

    def test_a_merge_of_main_into_a_judged_head_is_not_run(self):
        state = self.state()
        state["results"][base.SHA_A] = {"pr": 7, "ok": True, "failed": [],
                                        "finished": pc.iso(T0),
                                        "posted": True}
        self.write_state(state)
        self.approvers[7] = [self.REVIEWER]
        tip = "9" * 40

        def parents(args):
            return base.done(args, 0, "%s %s %s\n" % (base.SHA_B, base.SHA_A,
                                                      tip))
        git = ((lambda a: a[:1] == ["git"] and "--parents" in a, parents),)
        self.tick([(7, base.SHA_B, "false", [])],
                  checkout=self.state_dir, git=git)
        self.assertNotIn(base.SHA_B, self.state()["waiting"])
        self.assertEqual(self.posts, [])
        self.assertEqual(self.said("UPDATED"), [
            "UPDATED mac #7 bbbbbbbbbb: only merges main into aaaaaaaaaa, "
            "judged here; not run"])

    def test_a_merge_of_main_into_a_red_head_is_run(self):
        # #575: the merge may be what fixes the red, so it isn't carried.
        state = self.state()
        state["results"][base.SHA_A] = {"pr": 7, "ok": False,
                                        "failed": ["test-mac"],
                                        "finished": pc.iso(T0),
                                        "posted": True}
        self.write_state(state)
        self.approvers[7] = [self.REVIEWER]
        tip = "9" * 40

        def parents(args):
            return base.done(args, 0, "%s %s %s\n" % (base.SHA_B, base.SHA_A,
                                                      tip))
        git = ((lambda a: a[:1] == ["git"] and "--parents" in a, parents),)
        self.tick([(7, base.SHA_B, "false", [])],
                  checkout=self.state_dir, git=git)
        self.assertIn(base.SHA_B, self.state()["waiting"])
        self.assertEqual(self.said("UPDATED"), [])

    def test_drafts_and_judged_heads_are_as_before(self):
        state = self.state()
        state["results"][base.SHA_B] = {"pr": 8, "ok": True, "failed": [],
                                        "finished": pc.iso(T0),
                                        "posted": True}
        self.write_state(state)
        self.tick([(7, base.SHA_A, "true", []), (8, base.SHA_B, "false", [])])
        self.assertEqual(self.posts, [])
        self.assertEqual(self.calls(is_reviews), [])

    def test_without_it_every_head_runs_and_nothing_is_asked(self):
        pc.configure(config.Project(reviewed_mac(False), None), "mac")
        self.tick([(7, base.SHA_A, "false", [])])
        self.assertEqual([s[1] for s in self.spawned], [base.SHA_A])
        self.assertEqual(self.calls(is_reviews), [])
        self.assertFalse(os.path.exists(os.path.join(
            self.state_dir, pc.REVIEWED_FILE)))
