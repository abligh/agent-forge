"""Tests for agent_forge.config."""
import json
import os
import tempfile
import unittest

from agent_forge import config
from tests.helpers import EXAMPLE, done, example


class Find(unittest.TestCase):

    def test_forge_config_names_the_file(self):
        self.assertEqual(config.find({"FORGE_CONFIG": "/a/b.json"}),
                         "/a/b.json")

    def test_project_root_names_the_checkout(self):
        self.assertEqual(config.find({"FORGE_PROJECT_ROOT": "/p"}),
                         "/p/.agent-forge/config.json")

    def test_else_the_git_checkout_around_the_working_directory(self):
        calls = []

        def run(args, cwd=None):
            calls.append((args, cwd))
            return done(args, 0, "/repo\n")
        self.assertEqual(config.find({}, cwd="/repo/sub", run=run),
                         "/repo/.agent-forge/config.json")
        self.assertEqual(calls, [(["git", "rev-parse", "--show-toplevel"],
                                  "/repo/sub")])

    def test_outside_a_checkout_says_so(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.find({}, run=lambda a, cwd=None: done(a, 128))
        self.assertIn("not inside a git checkout", str(caught.exception))


class Check(unittest.TestCase):

    def problems(self, **changes):
        data = example()
        for key, value in changes.items():
            if value is None:
                del data[key]
            else:
                data[key] = value
        return config.check(data)

    def test_test_ahead_spare_slots_only_is_a_boolean(self):
        data = example()
        data.setdefault("merge", {})["test_ahead"] = {"spare_slots_only": True}
        self.assertEqual(config.check(data), [])
        data["merge"]["test_ahead"] = {"spare_slots_only": "yes"}
        self.assertEqual(len(config.check(data)), 1)

    def test_twins_are_session_names(self):
        data = example()
        data["agents"]["LeadDev"]["twins"] = ["WidgetLeadDev2"]
        self.assertEqual(config.check(data), [])
        data["agents"]["LeadDev"]["twins"] = ["", 3]
        self.assertEqual(config.check(data),
                         ["agents.LeadDev.twins must be a list of session "
                          "names"])
        data["agents"]["LeadDev"]["twins"] = "WidgetLeadDev2"
        self.assertEqual(len(config.check(data)), 1)

    def test_areas_and_offer(self):
        data = example()
        data["agents"]["LeadDev"]["areas"] = {"paths": ["build/"],
                                              "labels": ["Area:build"]}
        data["agents"]["UIDev"]["areas"] = {}
        data["agents"]["Helper"]["offer"] = False
        self.assertEqual(config.check(data), [])
        data["agents"]["LeadDev"]["areas"] = {"paths": "build/",
                                              "labels": ["", 3],
                                              "owners": []}
        data["agents"]["UIDev"]["areas"] = ["ui/"]
        data["agents"]["Helper"]["offer"] = "no"
        self.assertEqual(config.check(data), [
            "agents.LeadDev.areas.paths must be a list",
            "agents.LeadDev.areas: unknown key 'owners'",
            "agents.LeadDev.areas.labels must be a list of label names",
            "agents.UIDev.areas must be an object",
            "agents.Helper.offer must be true or false"])
        data["agents"]["LeadDev"]["areas"] = {"paths": ["", "x"]}
        data["agents"]["UIDev"]["areas"] = {}
        data["agents"]["Helper"]["offer"] = True
        self.assertEqual(config.check(data), [
            "agents.LeadDev.areas.paths must be a list of path globs"])

    def test_away_names_another_agent_who_is_here(self):
        data = example()
        data["agents"]["UIDev"]["away"] = "LeadDev"
        self.assertEqual(config.check(data), [])
        for other, problem in (
                ("UIDev", "agents.UIDev.away: 'UIDev' is not another agent"),
                ("Nobody", "agents.UIDev.away: 'Nobody' is not another "
                           "agent"),
                ("Merger", "agents.UIDev.away: 'Merger' is not another "
                           "agent")):
            data["agents"]["UIDev"]["away"] = other
            self.assertEqual(config.check(data), [problem])
        data["agents"]["UIDev"]["away"] = "LeadDev"
        data["agents"]["LeadDev"]["away"] = "Helper"
        self.assertIn("agents.UIDev.away: LeadDev is away too; a stand-in "
                      "must be here", config.check(data))

    def test_a_reviewers_stand_in_reviews(self):
        data = example()
        data["agents"]["Reviewer"]["away"] = "LeadDev"
        self.assertEqual(config.check(data),
                         ["agents.Reviewer.away: Reviewer reviews, so her "
                          "stand-in must be a reviewer"])

    def test_no_session_name_belongs_to_two_agents(self):
        data = example()
        data["agents"]["LeadDev"]["twins"] = ["WidgetUIDev"]
        (problem,) = config.check(data)
        self.assertIn("session WidgetUIDev is named more than once "
                      "(LeadDev, UIDev)", problem)

    def test_drop_env_takes_names_and_prefixes(self):
        data = example()
        data["checks"]["drop_env"] = ["A_B", "RECEIVER_*"]
        self.assertEqual(config.check(data), [])
        data["checks"]["drop_env"] = ["*", "A-B", 3, "X*Y"]
        self.assertEqual(len(config.check(data)), 4)

    def test_keep_ignored_takes_plain_paths_inside_the_worktree(self):
        data = example()
        data["checks"]["keep_ignored"] = ["web/dist", "/out/"]
        self.assertEqual(config.check(data), [])
        data["checks"]["keep_ignored"] = ["", "/", "../x", "a/*", 3]
        self.assertEqual(len(config.check(data)), 5)

    def test_host_fault_text_takes_non_empty_strings(self):
        data = example()
        data["checks"]["host_fault_text"] = ["registry returned an error"]
        self.assertEqual(config.check(data), [])
        data["checks"]["host_fault_text"] = ["", "  ", 3, None]
        problems = config.check(data)
        self.assertEqual(len(problems), 4)
        self.assertIn("checks.host_fault_text: '' is not a non-empty "
                      "string", problems)
        data["checks"]["host_fault_text"] = "registry down"
        self.assertEqual(config.check(data),
                         ["checks.host_fault_text must be a list"])

    def test_cache_fault_text_takes_lists_of_non_empty_strings(self):
        data = example()
        data["checks"]["cache_fault_text"] = [["rust-lld: error:",
                                               "undefined symbol"]]
        self.assertEqual(config.check(data), [])
        data["checks"]["cache_fault_text"] = [[], ["", "x"], "x", [3]]
        problems = config.check(data)
        self.assertEqual(len(problems), 4)
        self.assertIn("checks.cache_fault_text: [] is not a list of "
                      "non-empty strings", problems)
        data["checks"]["cache_fault_text"] = "undefined symbol"
        self.assertEqual(config.check(data),
                         ["checks.cache_fault_text must be a list"])

    def test_the_example_is_good(self):
        self.assertEqual(config.check(example()), [])

    def test_required_keys(self):
        self.assertEqual(self.problems(repo=None, owner=None),
                         ["missing 'repo'", "missing 'owner'"])

    def test_a_misspelt_key_is_a_problem_not_ignored(self):
        self.assertEqual(self.problems(default_brnach="main"),
                         ["unknown key 'default_brnach'"])

    def test_comments_are_any_key_starting_with_underscore(self):
        self.assertEqual(self.problems(_note=["anything"]), [])

    def test_types(self):
        self.assertEqual(self.problems(agents=[], host=3),
                         ["'agents' must be an object",
                          "'host' must be a string"])

    def test_project_and_repo_shapes(self):
        self.assertEqual(self.problems(project="Widget App", repo="widget"), [
            "project 'Widget App' must be lower case letters, digits and "
            "hyphens", "repo 'widget' must be owner/name"])

    def test_every_problem_at_once(self):
        data = example()
        data["agents"]["LeadDev"]["app_id"] = "101"
        data["agents"]["LeadDev"]["colour"] = "blue"
        data["agents"]["UIDev"]["kind"] = "lead"
        data["agents"]["UIDev"]["reviews"] = ["web/**"]
        self.assertEqual(config.check(data), [
            "agents.LeadDev.app_id must be an integer or null",
            "agents.LeadDev: unknown key 'colour'",
            "agents.UIDev.kind must be one of developer, reviewer, collaborator",
            "agents.UIDev.reviews: only a reviewer reviews paths"])

    def test_a_collaborator_is_a_kind(self):
        data = example()
        data["agents"]["UIDev"]["kind"] = "collaborator"
        self.assertEqual(config.check(data), [])

    def test_a_bool_is_not_an_id(self):
        data = example()
        data["agents"]["LeadDev"]["app_id"] = True
        self.assertEqual(config.check(data), [
            "agents.LeadDev.app_id must be an integer or null"])

    def test_globs_are_strings(self):
        data = example()
        data["agents"]["Reviewer"]["reviews"] = ["docs/**", ""]
        self.assertEqual(config.check(data), [
            "agents.Reviewer.reviews must be a list of path globs"])

    def test_no_slug_and_no_suffix(self):
        data = example()
        del data["naming"]
        problems = config.check(data)
        self.assertIn("agents.LeadDev: no slug, and no "
                      "naming.app_slug_suffix to make one", problems)
        self.assertIn("machinery.Merger: no slug, and no "
                      "naming.app_slug_suffix to make one", problems)
        self.assertNotIn("agents.Helper: no slug, and no "
                         "naming.app_slug_suffix to make one", problems)

    def test_agent_names(self):
        data = example()
        data["agents"]["ui-dev"] = {"app_id": 1, "installation_id": 2}
        self.assertEqual(config.check(data), [
            "agents.ui-dev: an agent's name is letters and digits, "
            "starting with a letter"])

    def test_at_least_one_agent(self):
        # Without the merge section, whose stall_alert names agents.
        self.assertEqual(self.problems(agents={}, merge={}),
                         ["agents must name at least one agent"])

    def test_an_agent_is_not_also_machinery(self):
        data = example()
        data["machinery"]["LeadDev"] = {"app_id": 1, "installation_id": 2}
        self.assertEqual(config.check(data),
                         ["LeadDev is both an agent and machinery"])

    def test_host_settings_files(self):
        files = [{"from": "/etc/passwd", "to": "../x", "mode": "644"},
                 {"to": "a", "colour": "red"}]
        self.assertEqual(self.problems(host_settings={"files": files}), [
            "host_settings.files[0].from must be a relative path inside its "
            "tree",
            "host_settings.files[0].to must be a relative path inside its "
            "tree",
            'host_settings.files[0].mode must be octal, like "0644"',
            "host_settings.files[1]: unknown key 'colour'",
            "host_settings.files[1]: missing 'from'"])

    def test_queue(self):
        self.assertEqual(self.problems(queue={}), [])
        self.assertEqual(self.problems(queue={
            "queues": {"gate": {"slots": 2, "nice": 10,
                                "timeout_minutes": 90,
                                "keep_worktree": True},
                       "timing": {}},
            "state_dir": "~/q", "retain_days": 3}), [])
        self.assertEqual(self.problems(queue={
            "queues": {"Gate": {}, "gate": {"slots": 0, "nice": 20,
                                            "timeout_minutes": "9",
                                            "colour": "red"},
                       "x": []},
            "state_dir": " ", "retain_days": 0, "size": 1}), [
            "queue: unknown key 'size'",
            "queue.state_dir must not be empty",
            "queue.retain_days must be at least 1",
            "queue.queues.Gate: a queue's name is lower case letters, "
            "digits and hyphens",
            "queue.queues.gate.timeout_minutes must be an integer",
            "queue.queues.gate: unknown key 'colour'",
            "queue.queues.gate.slots must be at least 1",
            "queue.queues.gate.nice must be from 0 to 19",
            "queue.queues.x must be an object"])
        self.assertEqual(self.problems(queue=[]),
                         ["'queue' must be an object"])

    def test_stats_watch(self):
        self.assertEqual(self.problems(stats_watch={"owner": "LeadDev"}), [])
        self.assertEqual(self.problems(stats_watch={
            "owner": "LeadDev", "window_hours": 6, "baseline_days": 7,
            "label": "stats-watch", "remind_hours": 6, "min_samples": 5,
            "as": "Reviewer", "figures": {
                "check_run": {"limit": 12, "ratio": 1.5},
                "check_wait": {"limit": 20, "ratio": 2},
                "pr_turnaround": {"limit": 20, "ratio": 1.5},
                "merge_time": {"limit": 45, "ratio": 2},
                "merge_rate": {"ratio": 2}, "slots_busy": {"limit": 90},
                "main_red": {}}}), [])
        self.assertEqual(self.problems(stats_watch={
            "owner": "Merger", "window_hours": 0, "label": " ",
            "as": "Nobody", "colour": 1, "figures": {
                "check_run": {"limit": 0, "ratio": 1},
                "merge_rate": {"limit": 3, "ratio": True,
                               "owner": "Nobody"},
                "main_red": [], "speed": {}}}), [
            "stats_watch: unknown key 'colour'",
            "stats_watch.owner 'Merger' is not an agent",
            "stats_watch.as 'Nobody' is not an agent or machinery (it "
            "defaults to the Merger)",
            "stats_watch.window_hours must be at least 1",
            "stats_watch.label must not be empty",
            "stats_watch.figures.check_run.limit must be a number above 0",
            "stats_watch.figures.check_run.ratio must be more than 1",
            "stats_watch.figures.merge_rate.ratio must be a number above 0",
            "stats_watch.figures.merge_rate.owner 'Nobody' is not an agent",
            "stats_watch.figures.main_red must be an object",
            "stats_watch.figures.speed: unknown figure (one of check_run, "
            "check_wait, pr_turnaround, merge_time, merge_rate, "
            "slots_busy, main_red, nightly_red_hours)"])
        self.assertEqual(self.problems(stats_watch={}),
                         ["stats_watch: missing 'owner'"])
        self.assertEqual(self.problems(stats_watch=[]),
                         ["'stats_watch' must be an object"])

    def test_nightly(self):
        data = example()
        data["router"] = {"owners": {"triage": "LeadDev"}}
        data["nightly"] = {"workflow": "ci.yaml", "job": "build-and-test"}
        self.assertEqual(config.check(data), [])
        data["nightly"] = {
            "workflow": "nightly.yml", "job": "build-and-test",
            "owners": {"test-slow": "UIDev", "vet-*": "Helper"},
            "labels": ["bug"], "retry_hours": 3, "escalate_hours": [24, 48],
            "tell_owner": "owner@example.org", "as": "Merger"}
        self.assertEqual(config.check(data), [])
        data["nightly"]["stats_figure"] = {}
        data["nightly"].update(
            workflow="ci", owners={"test-slow": "Nobody"}, labels=[""],
            retry_hours=0, escalate_hours=[48, 24], tell_owner=" ",
            **{"as": "Nobody"})
        self.assertEqual(config.check(data), [
            "nightly: unknown key 'stats_figure'",
            "nightly.workflow must be the workflow's file name, such as "
            "ci.yaml",
            "nightly.owners.test-slow: 'Nobody' is not an agent",
            "nightly.labels must be a list of label names",
            "nightly.retry_hours must be at least 1",
            "nightly.escalate_hours must be one or two whole numbers of "
            "hours, at least 1, rising (the developers, then the owner)",
            "nightly.tell_owner must not be empty",
            "nightly.as 'Nobody' is not an agent or machinery (it "
            "defaults to the Merger)"])
        for hours in ([], [1, 2, 3], [0], [24, 24], [True]):
            with self.subTest(hours=hours):
                data["nightly"] = {"workflow": "ci.yaml", "job": "j",
                                   "escalate_hours": hours}
                self.assertEqual(len(config.check(data)), 1)

    def test_nightly_needs_its_workflow_job_and_triage(self):
        self.assertEqual(self.problems(nightly={"job": " "}), [
            "nightly: missing 'workflow'",
            "nightly.job must not be empty",
            "nightly needs router.owners.triage: a failed step that "
            "`owners` doesn't name is hers"])
        self.assertEqual(self.problems(nightly=[]),
                         ["'nightly' must be an object"])

    def test_stats_watch_files_as_the_merger_by_default(self):
        data = example()
        data["stats_watch"] = {"owner": "LeadDev"}
        data["merge"]["merger"] = "Other"
        data["machinery"]["Other"] = {"app_id": 1, "installation_id": 2}
        self.assertEqual(config.check(data), [])
        del data["machinery"]["Other"]
        self.assertIn("stats_watch.as 'Other' is not an agent or machinery "
                      "(it defaults to the Merger)", config.check(data))

    def test_memory(self):
        self.assertEqual(self.problems(memory={
            "dir": "", "git_dir": 3, "labels": ["bug", ""], "limit": 0,
            "colour": "red"}), [
            "memory.git_dir must be a string",
            "memory: unknown key 'colour'",
            "memory: missing 'remote'",
            "memory.dir must not be empty",
            "memory.labels must be a list of label names",
            "memory.limit must be at least 1"])
        self.assertEqual(self.problems(memory=[]),
                         ["'memory' must be an object"])
        self.assertEqual(self.problems(memory={
            "dir": "~/m", "remote": "git@example.org:o/m.git",
            "ssh_key": "~/.ssh/m", "branch": "main", "labels": ["bug"],
            "limit": 25000, "long_line": 200}), [])

    def test_merge(self):
        self.assertEqual(self.problems(merge={
            "merger": "Nobody", "gate": [["ok"], [], "x", [1]],
            "stall_minutes": "30"}), [
            "merge.stall_minutes must be an integer",
            "merge.gate[1] must be a non-empty list of strings",
            "merge.gate[2] must be a non-empty list of strings",
            "merge.gate[3] must be a non-empty list of strings",
            "merge.merger 'Nobody' is not in machinery"])

    def test_merge_stall_alert(self):
        self.assertEqual(self.problems(merge={"stall_alert": {
            "tell": ["Reviewer", "Merger", 3], "retell_minutes": 0,
            "owner_after_minutes": "120", "title": " ",
            "labels": ["bug", ""], "colour": "red", "_note": "a comment"}}), [
            "merge.stall_alert.owner_after_minutes must be an integer",
            "merge.stall_alert: unknown key 'colour'",
            "merge.stall_alert.tell: 'Merger' is not an agent",
            "merge.stall_alert.tell: 3 is not an agent",
            "merge.stall_alert.retell_minutes must be at least 1",
            "merge.stall_alert.title must not be empty",
            "merge.stall_alert.labels must be a list of label names"])
        self.assertEqual(self.problems(merge={"stall_alert": []}),
                         ["merge.stall_alert must be an object"])
        self.assertEqual(self.problems(merge={"stall_alert": {
            "tell": "Reviewer", "labels": "bug"}}), [
            "merge.stall_alert.tell must be a list",
            "merge.stall_alert.labels must be a list"])
        self.assertEqual(self.problems(merge={"stall_alert": {}}), [])

    def test_merge_settings_of_design_0026(self):
        self.assertEqual(self.problems(merge={
            "resolve": [{"paths": ["data/table.tsv"],
                         "command": ["tools/gen-table"],
                         "check": ["tools/gen-table", "--check"]}],
            "hold_labels": ["Hold"], "blocked_by": "everywhere",
            "said_since": {"author": True, "approvals": True,
                           "quiet": ["Helper", "Merger"]},
            "update_once": True, "ci_never_started": 20,
            "queued_stall_minutes": 90, "read_as": "Helper"}), [])
        self.assertEqual(self.problems(merge={
            "resolve": [{"paths": [], "command": ["x"]},
                        {"paths": ["a"], "command": ["x"], "check": ["y"],
                         "extra": 1}, "x"],
            "hold_labels": ["ok", ""], "blocked_by": "comments",
            "said_since": {"author": "yes", "quiet": ["Nobody", 3],
                           "other": 1},
            "update_once": "yes", "ci_never_started": 0,
            "queued_stall_minutes": 0, "read_as": "Nobody"}), [
            "merge.update_once must be true or false",
            "merge.resolve[0].paths must be a non-empty list of strings",
            "merge.resolve[0]: missing 'check'",
            "merge.resolve[1]: unknown key 'extra'",
            "merge.resolve[2] must be an object",
            "merge.hold_labels must be a list of label names",
            "merge.blocked_by must be one of body, everywhere",
            "merge.said_since.author must be true or false",
            "merge.said_since: unknown key 'other'",
            "merge.said_since.quiet: 'Nobody' is not an agent or machinery",
            "merge.said_since.quiet: 3 is not an agent or machinery",
            "merge.ci_never_started must be at least 1",
            "merge.queued_stall_minutes must be at least 1",
            "merge.read_as 'Nobody' is not an agent or machinery"])

    def test_merge_stacks(self):
        self.assertEqual(self.problems(merge={"stacks": {}}), [])
        self.assertEqual(self.problems(merge={"stacks": {"max_run": 3}}), [])
        self.assertEqual(self.problems(merge={"stacks": {
            "max_run": -1, "depth": 2}}), [
            "merge.stacks: unknown key 'depth'",
            "merge.stacks.max_run must be at least 0"])
        self.assertEqual(self.problems(merge={"stacks": {"max_run": True}}),
                         ["merge.stacks.max_run must be an integer"])
        self.assertEqual(self.problems(merge={"stacks": True}),
                         ["merge.stacks must be an object"])

    def test_merge_stacks_wait_hours(self):
        for hours in (1, 72, 168):
            self.assertEqual(self.problems(merge={"stacks": {
                "wait_hours": hours}}), [])
        for hours in (0, 169):
            self.assertEqual(self.problems(merge={"stacks": {
                "wait_hours": hours}}),
                ["merge.stacks.wait_hours must be 1 to 168"])
        self.assertEqual(self.problems(merge={"stacks": {
            "wait_hours": "72"}}),
            ["merge.stacks.wait_hours must be an integer"])

    def test_merge_test_ahead(self):
        self.assertEqual(self.problems(merge={"test_ahead": {
            "depth": -1, "max_age_hours": 0, "width": 2,
            "_note": "a comment"}}), [
            "merge.test_ahead: unknown key 'width'",
            "merge.test_ahead.depth must be at least 0",
            "merge.test_ahead.max_age_hours must be at least 1"])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "depth": "3", "max_age_hours": True}}), [
            "merge.test_ahead.depth must be an integer",
            "merge.test_ahead.max_age_hours must be an integer"])
        self.assertEqual(self.problems(merge={"test_ahead": 3}),
                         ["merge.test_ahead must be an object"])
        self.assertEqual(self.problems(merge={"test_ahead": {}}), [])
        self.assertEqual(self.problems(merge={"test_ahead": {"depth": 0}}),
                         [])

    def test_merge_test_ahead_batches(self):
        self.assertEqual(self.problems(merge={"test_ahead": {
            "depth": 15, "batch": 8, "batches": 2}}), [])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "depth": 7, "batch": 8, "batches": 2}}), [
            "merge.test_ahead.batches x batch must be at most depth + 1 (8)"])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "batches": 2}}), [
            "merge.test_ahead.batches above 1 needs batch above 1"])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "batches": 0}}), ["merge.test_ahead.batches must be at least 1"])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "batches": True}}),
            ["merge.test_ahead.batches must be an integer"])

    def test_merge_test_ahead_batch(self):
        self.assertEqual(self.problems(merge={"test_ahead": {"batch": 4}}),
                         [])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "depth": 1, "batch": 2}}), [])
        self.assertEqual(self.problems(merge={"test_ahead": {"batch": 5}}), [
            "merge.test_ahead.batch must be at least 1 and at most "
            "depth + 1 (4)"])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "depth": 1, "batch": 0}}), [
            "merge.test_ahead.batch must be at least 1 and at most "
            "depth + 1 (2)"])
        self.assertEqual(self.problems(merge={"test_ahead": {
            "batch": "2"}}), ["merge.test_ahead.batch must be an integer"])

    def test_merge_test_ahead_stacks(self):
        # designs/0035: a layer joins its base's batch.
        self.assertEqual(self.problems(merge={"stacks": {}, "test_ahead": {
            "batch": 2, "stacks": True}}), [])
        need = ["merge.test_ahead.stacks needs merge.stacks and "
                "merge.test_ahead.batch above 1"]
        self.assertEqual(self.problems(merge={"test_ahead": {
            "batch": 2, "stacks": True}}), need)
        self.assertEqual(self.problems(merge={"stacks": {}, "test_ahead": {
            "stacks": True}}), need)
        self.assertEqual(self.problems(merge={"stacks": {}, "test_ahead": {
            "batch": 2, "stacks": "yes"}}),
            ["merge.test_ahead.stacks must be true or false"])

    def test_merge_yield_after_hand_back(self):
        self.assertEqual(self.problems(merge={"yield_after_hand_back": 2,
                                              "yield_cap": 4}), [])
        self.assertEqual(self.problems(merge={"yield_after_hand_back": 0}),
                         ["merge.yield_after_hand_back must be at least 1"])
        self.assertEqual(self.problems(merge={"yield_after_hand_back": "2"}),
                         ["merge.yield_after_hand_back must be an integer"])
        self.assertEqual(self.problems(merge={"yield_after_hand_back": 1,
                                              "yield_cap": 0}),
                         ["merge.yield_cap must be at least 1"])
        self.assertEqual(self.problems(merge={"yield_cap": 2}),
                         ["merge.yield_cap needs "
                          "merge.yield_after_hand_back"])

    def test_merge_right_of_way(self):
        self.assertEqual(self.problems(merge={
            "yield_ignores_conflicts": True,
            "right_of_way": {"after_conflicts": 2, "minutes": 60}}), [])
        self.assertEqual(self.problems(merge={"right_of_way": {}}), [])
        self.assertEqual(self.problems(merge={"right_of_way": {
            "after_conflicts": 0, "minutes": "60", "width": 1}}), [
            "merge.right_of_way.minutes must be an integer",
            "merge.right_of_way: unknown key 'width'",
            "merge.right_of_way.after_conflicts must be at least 1"])
        self.assertEqual(self.problems(merge={"right_of_way": 2}),
                         ["merge.right_of_way must be an object"])
        self.assertEqual(self.problems(merge={"code_owners": True}), [])
        self.assertEqual(self.problems(merge={"code_owners": "yes"}),
                         ["merge.code_owners must be true or false"])
        self.assertEqual(self.problems(merge={"yield_ignores_conflicts": 1}),
                         ["merge.yield_ignores_conflicts must be true or false"])

    def test_merge_age_first(self):
        self.assertEqual(self.problems(merge={
            "age_first": {"after_minutes": 90}}), [])
        self.assertEqual(self.problems(merge={"age_first": {}}), [])
        self.assertEqual(self.problems(merge={"age_first": {
            "after_minutes": 0, "width": 1}}), [
            "merge.age_first: unknown key 'width'",
            "merge.age_first.after_minutes must be at least 1"])
        self.assertEqual(self.problems(merge={"age_first": 90}),
                         ["merge.age_first must be an object"])

    def test_merge_hold_conflicting(self):
        self.assertEqual(self.problems(merge={"hold_conflicting": True}), [])
        self.assertEqual(self.problems(merge={"hold_conflicting": "yes"}),
                         ["merge.hold_conflicting must be true or false"])

    def test_test_ahead_contradicts_merger_first_false(self):
        data = example()
        data["checks"]["merger_first"] = False
        self.assertEqual(config.check(data), [
            "merge.test_ahead puts the Merger's pull request first, so "
            "checks.merger_first must not be false"])
        del data["checks"]["merger_first"]
        self.assertEqual(config.check(data), [])
        del data["merge"]["test_ahead"]
        data["checks"]["merger_first"] = False
        self.assertEqual(config.check(data), [])

    def test_checks(self):
        self.assertEqual(self.problems(checks={
            "poster": "Nobody", "slots": 0, "memory_max": 16,
            "steps": [{"name": "a", "run": ["make"]}, {"run": []},
                      {"name": "b", "run": ["x"], "when": "y"}],
            "caches": {"CARGO_TARGET_DIR": "../up"}, "merger_first": 1}), [
            "checks.memory_max must be a string or null",
            "checks.merger_first must be true or false",
            "checks.slots must be at least 1",
            "checks.steps[1]: missing 'name'",
            "checks.steps[1].run must be a non-empty list of strings",
            "checks.steps[2]: unknown key 'when'",
            "checks.caches must map variables to directory names",
            "checks.poster 'Nobody' is no App in the configuration"])

    def test_depends(self):
        # designs/0005.
        self.assertEqual(self.problems(checks={
            "depends": True, "depends_minutes": 30, "tracer": "strace",
            "steps": [{"name": "a", "run": ["make"], "always": True}]}), [])
        self.assertEqual(self.problems(checks={
            "depends": "yes", "depends_minutes": 0, "tracer": "ptrace",
            "steps": [{"name": "a", "run": ["make"], "always": 1}]}), [
            "checks.depends must be true or false",
            "checks.depends_minutes must be at least 1",
            "checks.tracer must be one of preload, strace",
            "checks.steps[0].always must be true or false"])

    def test_cpu_weight(self):
        for weight in (0, 1, 1000, 10000):
            self.assertEqual(self.problems(checks={"cpu_weight": weight}),
                             [], weight)
        for weight in (-1, 10001):
            self.assertEqual(self.problems(checks={"cpu_weight": weight}),
                             ["checks.cpu_weight must be 0 (none) or 1 to "
                              "10000"])
        self.assertEqual(self.problems(checks={"cpu_weight": "high"}),
                         ["checks.cpu_weight must be an integer"])

    def test_cpu_share_and_side_by_side(self):
        # designs/0029.
        steps = [{"name": "a", "run": ["a"], "light": True},
                 {"name": "b", "run": ["b"], "after": ["a"],
                  "caches": {"CARGO_TARGET_DIR": "cargo-wasm"}},
                 {"name": "c", "run": ["c"], "alone": True, "after": ["b"]},
                 {"name": "d", "run": ["d"], "alone": True, "after": ["c"]}]
        self.assertEqual(self.problems(checks={
            "cpu_share": "step", "side_by_side": {"max_steps": 3},
            "steps": steps}), [])
        self.assertEqual(self.problems(checks={
            "cpu_share": "run", "side_by_side": {}}), [])
        self.assertEqual(self.problems(checks={
            "cpu_share": "head", "side_by_side": {"max_steps": 0,
                                                  "steps": 2}}), [
            "checks.cpu_share must be one of run, step",
            "checks.side_by_side: unknown key 'steps'",
            "checks.side_by_side.max_steps must be at least 1"])
        self.assertEqual(self.problems(checks={"steps": [
            {"name": "a", "run": ["a"], "after": "b", "light": 1,
             "caches": {"X": "a/b"}}]}), [
            "checks.steps[0].after must be a list",
            "checks.steps[0].light must be true or false",
            "checks.steps[0].caches must map variables to directory names"])

    def test_after(self):
        def steps(*entries):
            return {"steps": [dict({"run": ["x"]}, **e) for e in entries]}
        self.assertEqual(self.problems(checks=steps(
            {"name": "a", "after": ["nobody", "a"]})), [
            "checks.steps[0].after: 'nobody' is not a step's name",
            "checks.steps[0].after: a step can't wait for itself"])
        self.assertEqual(self.problems(checks=steps(
            {"name": "a", "alone": True},
            {"name": "b", "after": ["a"]})), [
            "checks.steps[1].after: 'a' is alone, so it runs after every "
            "step that isn't; 'b' would wait for ever"])
        self.assertEqual(self.problems(checks=steps(
            {"name": "a", "alone": True, "after": ["b"]},
            {"name": "b", "alone": True})), [
            "checks.steps[0].after: 'b' is alone and comes later, and "
            "alone steps run in the configured order"])
        self.assertEqual(self.problems(checks=steps(
            {"name": "a", "after": ["c"]}, {"name": "b", "after": ["a"]},
            {"name": "c", "after": ["b"]})), [
            "checks.steps: `after` makes a cycle: a -> c -> b -> a"])

    def test_fast_lane(self):
        # designs/0006.
        self.assertEqual(self.problems(checks={
            "depends": True, "fast_lane": {"max_seconds": 60}}), [])
        self.assertEqual(self.problems(checks={
            "depends": True, "fast_lane": {}}), [])
        self.assertEqual(self.problems(checks={
            "depends": True,
            "fast_lane": {"max_seconds": 0, "slots": 2}}), [
            "checks.fast_lane: unknown key 'slots'",
            "checks.fast_lane.max_seconds must be at least 1"])
        self.assertEqual(self.problems(checks={
            "depends": True, "fast_lane": {"max_seconds": True}}), [
            "checks.fast_lane.max_seconds must be an integer"])
        self.assertEqual(self.problems(checks={"fast_lane": 60}), [
            "checks.fast_lane must be an object"])

    def test_fast_lane_is_refused_without_depends(self):
        for checks in ({"fast_lane": {"max_seconds": 60}},
                       {"depends": False, "fast_lane": {}}):
            self.assertEqual(self.problems(checks=checks), [
                "checks.fast_lane needs checks.depends: without it every "
                "head runs every step, and none is cheap"])

    def test_labels_and_relay(self):
        self.assertEqual(self.problems(
            labels={"owner_prefix": 1, "other": "kept"},
            relay={"url": 2, "remind_seconds": 0}), [
            "labels.owner_prefix must be a string",
            "relay.url must be a string",
            "relay.remind_seconds must be at least 1"])

    def test_router(self):
        self.assertEqual(self.problems(router={"rules": "default"}), [])
        self.assertEqual(self.problems(router={"rules": 1, "rule": "x"}), [
            "router.rules must be a string",
            "router: unknown key 'rule'"])

    def test_router_quiet_labels(self):
        self.assertEqual(self.problems(router={
            "quiet_labels": ["Status:venue"]}), [])
        self.assertEqual(self.problems(router={"quiet_labels": ["", 1]}), [
            "router.quiet_labels must be a list of label names"])
        self.assertEqual(self.problems(router={"quiet_labels": "x"}), [
            "router.quiet_labels must be a list"])

    def test_router_forget_minutes(self):
        for minutes in (0, 360):
            self.assertEqual(self.problems(router={
                "forget_minutes": minutes}), [])
        self.assertEqual(self.problems(router={"forget_minutes": -1}), [
            "router.forget_minutes must be at least 0"])
        self.assertEqual(self.problems(router={"forget_minutes": "6h"}), [
            "router.forget_minutes must be an integer"])

    def test_receiver(self):
        self.assertEqual(self.problems(receiver={
            "port": 0, "path": "hook", "max_body": 10 ** 8, "colour": 1,
            "secret_file": 5, "_note": "a comment"}), [
            "receiver: unknown key 'colour'",
            "receiver.secret_file must be a string",
            "receiver.port must be from 1 to 65535",
            "receiver.path must start with /",
            "receiver.max_body must be from 1 to 99999999"])
        self.assertEqual(self.problems(receiver={"port": True}),
                         ["receiver.port must be an integer"])

    def test_receiver_ticks(self):
        self.assertEqual(self.problems(receiver={"ticks": {
            "push": ["forge-merger", ["widget-pr-check", "--post"]],
            "_comment": "x",
            "release": ["forge-merger"],
            "status": "forge-merger",
            "issues": ["", [], ["tools/x"], [".x"], [1], 2]}}), [
            "receiver.ticks.release: not an event the receiver reads "
            "(issues, issue_comment, pull_request, pull_request_review, "
            "status, check_suite, push)",
            "receiver.ticks.status must be a list"] + [
            "receiver.ticks.issues[%d] must be a tool's name or a non-empty "
            "list of strings starting with one" % i for i in range(6)])

    def test_receiver_route(self):
        for route in (["widget-router"], [], ["tools/r", "{items}"], [1]):
            self.assertEqual(self.problems(receiver={"route": route}), [
                "receiver.route must be a non-empty list of strings "
                "starting with a tool's name, with {items} in one"], route)
        self.assertEqual(self.problems(receiver={
            "route": ["r", "--only={items}"]}), [])

    def test_closing_keywords(self):
        self.assertEqual(self.problems(closing_keywords={
            "enforced_by": 1, "rerun": " ", "colour": "red"}), [
            "closing_keywords.enforced_by must be a string",
            "closing_keywords: unknown key 'colour'",
            "closing_keywords.rerun must not be empty"])
        self.assertEqual(self.problems(closing_keywords="CI"),
                         ["'closing_keywords' must be an object"])
        self.assertEqual(self.problems(closing_keywords={}), [])

    def test_host_keys_are_strings(self):
        self.assertEqual(
            self.problems(host_settings={"host_keys": ["a", 1]}),
            ["host_settings.host_keys must be a list of strings"])

    def test_docs_types(self):
        self.assertEqual(
            self.problems(docs={"paths": "docs", "width": "80",
                                "roles": 1, "designs_dir": None}),
            ["docs.paths must be a list", "docs.width must be an integer",
             "docs.roles must be a string",
             "docs.designs_dir must be a string"])
        self.assertEqual(self.problems(docs={"paths": ["docs", ""],
                                             "width": 0}),
                         ["docs.paths must be a list of path globs",
                          "docs.width must be at least 1"])
        self.assertEqual(self.problems(docs={"width": True}),
                         ["docs.width must be an integer"])

    def test_docs_keys_of_tools_not_here_yet_are_left_alone(self):
        self.assertEqual(self.problems(docs={"spelling": "words.txt"}), [])

    def test_not_an_object(self):
        self.assertEqual(config.check([]),
                         ["the configuration must be a JSON object"])


class LoadReviewed(unittest.TestCase):
    """The identity tools' configuration: the cron checkout's when there
    is one, so a branch can't change who its agent is."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.cron = os.path.join(self.tmp.name, "cron")
        self.branch = os.path.join(self.tmp.name, "branch")
        for root, name in ((self.cron, "widget"), (self.branch, "gadget")):
            os.makedirs(os.path.join(root, ".agent-forge"))
            data = example()
            data["project"] = name
            with open(os.path.join(root, config.CONFIG_NAME), "w") as handle:
                json.dump(data, handle)

    def test_the_cron_checkout_wins_over_the_working_directory(self):
        def run(args, cwd=None):
            return done(args, 0, self.branch + "\n")
        project = config.load_reviewed(
            {"FORGE_CRON_CHECKOUT": self.cron}, cwd=self.branch, run=run)
        self.assertEqual(project.name, "widget")

    def test_forge_config_still_names_the_file(self):
        path = os.path.join(self.branch, config.CONFIG_NAME)
        project = config.load_reviewed({"FORGE_CONFIG": path,
                                        "FORGE_CRON_CHECKOUT": self.cron})
        self.assertEqual(project.name, "gadget")

    def test_without_a_cron_checkout_it_is_found_as_usual(self):
        def run(args, cwd=None):
            return done(args, 0, self.branch + "\n")
        project = config.load_reviewed({}, cwd=self.branch, run=run)
        self.assertEqual(project.name, "gadget")

    def test_the_identity_tools_use_it(self):
        from tests.helpers import load_tool
        self.assertIs(load_tool("forge-git-credential").mint.__defaults__[0],
                      config.load_reviewed)
        self.assertIs(
            load_tool("forge-worktree-identity").main.__defaults__[2],
            config.load_reviewed)
        for name in ("forge-app-token", "forge-gh-signed"):
            path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "bin", name)
            with open(path) as handle:
                text = handle.read()
            self.assertIn("config.load_reviewed()", text, name)
            self.assertNotIn("config.load()", text, name)


class Load(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = os.path.join(self.tmp.name, ".agent-forge", "config.json")
        os.makedirs(os.path.dirname(self.path))

    def write(self, data):
        with open(self.path, "w") as handle:
            handle.write(data if isinstance(data, str) else json.dumps(data))

    def test_loads_a_good_file(self):
        self.write(example())
        project = config.load(self.path)
        self.assertEqual((project.name, project.root),
                         ("widget", self.tmp.name))

    def test_found_by_the_environment(self):
        self.write(example())
        project = config.load(environ={"FORGE_PROJECT_ROOT": self.tmp.name})
        self.assertEqual(project.path, self.path)

    def test_problems_name_the_file(self):
        data = example()
        del data["owner"]
        self.write(data)
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.path)
        self.assertEqual(caught.exception.problems, ["missing 'owner'"])
        self.assertEqual(str(caught.exception),
                         "%s: missing 'owner'" % self.path)

    def test_missing_and_unreadable(self):
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.path)
        self.assertEqual(caught.exception.problems, ["no configuration file"])
        self.write("{not json")
        with self.assertRaises(config.ConfigError) as caught:
            config.load(self.path)
        self.assertTrue(caught.exception.problems[0].startswith("unreadable"))


class ProjectQueries(unittest.TestCase):

    def setUp(self):
        self.p = config.Project(example(), str(EXAMPLE))

    def test_defaults(self):
        data = example()
        del data["default_branch"], data["host"]
        p = config.Project(data)
        self.assertEqual((p.default_branch, p.host, p.root), ("main", "", None))

    def test_slugs_from_the_suffix_unless_named(self):
        self.assertEqual(self.p.slug("LeadDev"), "leaddevwidgetagent")
        self.assertEqual(self.p.slug("Helper"), "helperbot")
        self.assertEqual(self.p.slug("Merger"), "mergerwidgetagent")
        self.assertEqual(self.p.bot_login("Helper"), "helperbot[bot]")

    def test_by_login_with_or_without_bot(self):
        self.assertEqual(self.p.by_login("leaddevwidgetagent[bot]"), "LeadDev")
        self.assertEqual(self.p.by_login("HelperBot"), "Helper")
        self.assertEqual(self.p.by_login("mergerwidgetagent"), "Merger")
        self.assertIsNone(self.p.by_login("example-owner"))

    def test_kinds(self):
        self.assertEqual(self.p.reviewers(), ["Reviewer", "UIReviewer"])
        self.assertEqual(self.p.developers(), ["LeadDev", "UIDev", "Helper"])

    def test_session_names(self):
        self.assertEqual(self.p.session_name("LeadDev"), "WidgetLeadDev")
        self.assertEqual(self.p.session_name("Helper"), "Helper")

    def test_sessions_are_hers_then_her_twins(self):
        self.assertEqual(self.p.sessions("LeadDev"), ["WidgetLeadDev"])
        data = example()
        data["agents"]["LeadDev"]["twins"] = ["WidgetLeadDev2", "Spare"]
        p = config.Project(data, None)
        self.assertEqual(p.sessions("LeadDev"),
                         ["WidgetLeadDev", "WidgetLeadDev2", "Spare"])
        self.assertEqual(p.by_session()["WidgetLeadDev2"], "LeadDev")
        self.assertEqual(p.by_session()["Helper"], "Helper")

    def test_areas_and_offered(self):
        self.assertIsNone(self.p.areas("LeadDev"))
        self.assertTrue(self.p.offered("LeadDev"))
        data = example()
        data["agents"]["LeadDev"]["areas"] = {"labels": ["Area:build"]}
        data["agents"]["LeadDev"]["offer"] = False
        data["agents"]["UIDev"]["offer"] = True
        p = config.Project(data, None)
        self.assertEqual(p.areas("LeadDev"), {"paths": [],
                                              "labels": ["Area:build"]})
        self.assertEqual((p.offered("LeadDev"), p.offered("UIDev")),
                         (False, True))

    def test_a_stand_in_works_for_an_agent_who_is_away(self):
        self.assertEqual(self.p.stand_in("LeadDev"), "LeadDev")
        data = example()
        data["agents"]["UIDev"]["away"] = "LeadDev"
        p = config.Project(data, None)
        self.assertEqual(p.stand_in("UIDev"), "LeadDev")
        # Her own sessions stay hers: forge-stats still names them.
        self.assertEqual(p.sessions("UIDev"), ["WidgetUIDev"])
        self.assertEqual(p.by_session()["WidgetUIDev"], "UIDev")

    def test_an_app_not_yet_made(self):
        self.assertTrue(self.p.exists("LeadDev"))
        self.assertFalse(self.p.exists("UIReviewer"))

    def test_key_paths(self):
        home = os.path.expanduser("~")
        self.assertEqual(self.p.key_path("LeadDev"),
                         home + "/agent-keys/widget/LeadDev.pem")
        self.assertEqual(self.p.key_path("Helper"),
                         home + "/agent-keys/widget/helper.2026.pem")
        data = example()
        del data["key_dir"]
        self.assertIsNone(config.Project(data).key_path("LeadDev"))

    def test_reviewers_for_a_path(self):
        self.assertEqual(self.p.reviewers_for("web/src/app.ts"),
                         ["UIReviewer"])
        self.assertEqual(self.p.reviewers_for("src/core/deep/x.rs"),
                         ["Reviewer"])
        self.assertEqual(self.p.reviewers_for("Cargo.lock"), [])

    def test_sections_and_dotted_get(self):
        self.assertEqual(self.p.section("checks")["poster"], "Merger")
        self.assertEqual(self.p.section("nothing"), {})
        self.assertEqual(self.p.get("agents.Reviewer.kind"), "reviewer")
        with self.assertRaises(KeyError):
            self.p.get("agents.Nobody.kind")


if __name__ == "__main__":
    unittest.main()
