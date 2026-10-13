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
            "agents.UIDev.kind must be one of developer, reviewer",
            "agents.UIDev.reviews: only a reviewer reviews paths"])

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
            "checks.memory_max must be a string",
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
            "status, push)",
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
