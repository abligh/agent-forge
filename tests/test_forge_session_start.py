"""Tests for bin/forge-session-start, the restart brief."""
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import ROOT, example, load_tool

mod = load_tool("forge-session-start")
TOOL = ROOT / "bin" / "forge-session-start"


class Done:
    def __init__(self, rc=0, stdout="", stderr=""):
        self.returncode, self.stdout, self.stderr = rc, stdout, stderr


def fake_run(name="LeadDev", calls=None, shared=None, ext=True):
    """A subprocess.run that answers git config.

    `name` is the worktree's own user.name and `shared` the repository's;
    `ext` is extensions.worktreeConfig. Git without the extension reads
    `--worktree` as `--local`, so then `--worktree` answers `shared`."""
    def run(argv, **kw):
        if calls is not None:
            calls.append(argv)
        if argv[0] == "git":
            if "extensions.worktreeConfig" in argv:
                return Done(0, "true\n") if ext else Done(1, "")
            got = name if ("--worktree" in argv and ext) else (
                shared if "--worktree" in argv else
                (name if name is not None else shared))
            return Done(0 if got is not None else 1, (got or "") + "\n")
        raise AssertionError("unexpected command %r" % argv)
    return run


def fake_call(issues=None, status=200, calls=None):
    """github.api_call, answering the one listing."""
    def call(url, token, scheme="Bearer", method="GET", body=None):
        if calls is not None:
            calls.append((url, token))
        if "/issues?" not in url:
            raise AssertionError("unexpected request %r" % url)
        if status != 200:
            return status, {"message": "Bad Gateway"}
        return 200, issues if issues is not None else []
    return call


ISSUES = [
    {"number": 18, "title": "renderer drops a gradient stop",
     "labels": [{"name": "Owner:LeadDev"}]},
    {"number": 22, "title": "add the scene graph", "pull_request": {},
     "labels": [{"name": "Owner:LeadDev"}]},
    {"number": 9, "title": "designs/0002 color model",
     "labels": [{"name": "Status:waiting-owner"}]},
]
ROLES = "# Roles\n\n## LeadDev\n\nYou are LeadDev.\n\n## Reviewer\n\nYou are Reviewer.\n"


class Checkout(unittest.TestCase):
    """A cron checkout with a configuration and a roles file."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tree = Path(tmp.name) / "cron"
        (self.tree / ".agent-forge").mkdir(parents=True)
        (self.tree / "docs").mkdir()
        (self.tree / "docs/roles.md").write_text(ROLES)
        self.data = example()
        self.data["key_dir"] = str(Path(tmp.name) / "no-keys")
        self.write_config()
        self.environ = {"HOME": tmp.name, "FORGE_CRON_CHECKOUT": str(self.tree),
                        "GH_TOKEN": "read-token"}

    def write_config(self):
        (self.tree / ".agent-forge/config.json").write_text(
            json.dumps(self.data))

    def context(self, source="startup", run=None, call=None):
        out = io.StringIO()
        rc = mod.main(stdin=io.StringIO(json.dumps({"source": source,
                                                    "cwd": "/w"})),
                      run=run or fake_run(), out=out, environ=self.environ,
                      call=call or fake_call(ISSUES))
        text = out.getvalue()
        return rc, (json.loads(text)["hookSpecificOutput"] if text else None)


class Brief(Checkout):

    def test_a_startup_gets_the_checklist_and_its_items(self):
        rc, hso = self.context()
        self.assertEqual(rc, 0)
        self.assertEqual(hso["hookEventName"], "SessionStart")
        ctx = hso["additionalContext"]
        for want in ("for LeadDev", "Background tasks are gone", "Monitor",
                     "CronList", "select:SendMessage,ListAgents",
                     "#18 issue", "#22 PR", "#9 issue Status:waiting-owner"):
            self.assertIn(want, ctx)

    def test_the_agents_own_role_follows(self):
        ctx = self.context()[1]["additionalContext"]
        self.assertIn("Your role (docs/roles.md, ## LeadDev):", ctx)
        self.assertIn("You are LeadDev", ctx)
        self.assertNotIn("You are Reviewer", ctx)

    def test_the_roles_file_is_configurable(self):
        (self.tree / "docs/roles.md").rename(self.tree / "TEAM.md")
        self.data["docs"]["roles"] = "TEAM.md"
        self.write_config()
        ctx = self.context()[1]["additionalContext"]
        self.assertIn("Your role (TEAM.md, ## LeadDev):", ctx)

    def test_a_role_section_ends_at_the_next(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md",
                                         delete=False) as handle:
            handle.write("# Roles\n\n## A\n\nA's brief.\n\n## B\n\nB's.\n")
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(mod.role_of("A", handle.name), "A's brief.")
        self.assertEqual(mod.role_of("B", handle.name), "B's.")
        self.assertIsNone(mod.role_of("C", handle.name))
        self.assertIsNone(mod.role_of("A", "/nonexistent/roles.md"))

    def test_one_section_briefs_the_agents_it_names(self):
        with tempfile.NamedTemporaryFile("w", suffix=".md",
                                         delete=False) as handle:
            handle.write("# Roles\n\n## A, A2\n\nA's brief.\n\n## AB\n\n"
                         "AB's.\n")
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(mod.role_of("A", handle.name), "A's brief.")
        self.assertEqual(mod.role_of("A2", handle.name), "A's brief.")
        self.assertEqual(mod.role_of("AB", handle.name), "AB's.")
        self.assertIsNone(mod.role_of("A, A2", handle.name))

    def test_no_section_says_so(self):
        text = mod.brief("startup", "Nobody", [], role=None)
        self.assertIn("docs/roles.md has no section for Nobody", text)

    def test_an_issue_is_not_called_a_pr(self):
        self.assertNotIn("#18 PR", self.context()[1]["additionalContext"])

    def test_a_resume_is_briefed_too(self):
        """Resuming after an app restart loses tasks the same way."""
        self.assertIsNotNone(self.context(source="resume")[1])

    def test_a_compaction_gets_nothing_and_lists_nothing(self):
        """Background tasks survive a compaction. Telling the agent they
        died would be false, whatever the settings matcher says."""
        runs, calls = [], []
        rc, hso = self.context(source="compact", run=fake_run(calls=runs),
                               call=fake_call(ISSUES, calls=calls))
        self.assertEqual((rc, hso, runs, calls), (0, None, [], []))

    def test_a_clear_gets_nothing(self):
        self.assertEqual(self.context(source="clear"), (0, None))

    def test_an_unstamped_worktree_is_told_to_stamp_and_lists_nothing(self):
        for name in ("Anonymous Agent", None):
            calls = []
            ctx = self.context(run=fake_run(name=name),
                               call=fake_call(ISSUES, calls=calls))[1][
                "additionalContext"]
            self.assertIn("forge-worktree-identity", ctx)
            self.assertEqual(calls, [], "no name, so no listing")

    def test_a_configured_unattributed_name_is_unstamped_too(self):
        self.data["identity"]["unattributed_name"] = "Nobody Yet"
        self.write_config()
        ctx = self.context(run=fake_run(name="Nobody Yet"))[1][
            "additionalContext"]
        self.assertIn("forge-worktree-identity", ctx)

    def test_a_name_set_for_the_whole_repository_is_not_a_stamp(self):
        """A name in the shared .git/config would make every unstamped
        worktree read as that agent. Neither a shared name nor a
        `--worktree` read without the extension (which git answers from
        the shared config) may count."""
        for kw in ({"name": None, "shared": "Reviewer (scratch)"},
                   {"name": "Reviewer", "shared": "Reviewer", "ext": False}):
            calls = []
            ctx = self.context(run=fake_run(**kw),
                               call=fake_call(ISSUES, calls=calls))[1][
                "additionalContext"]
            self.assertIn("whole repository does not count", ctx, kw)
            self.assertEqual(calls, [], kw)

    def test_the_stamp_is_read_from_the_worktree_scope(self):
        """The control for the test above."""
        calls = []
        self.assertEqual(mod.agent_name("/w", [], run=fake_run(
            name="LeadDev", shared="Reviewer (scratch)", calls=calls)),
            "LeadDev")
        self.assertIn("--worktree", calls[-1])

    def test_the_listing_asks_the_project_for_the_owner_label(self):
        calls = []
        self.context(call=fake_call(ISSUES, calls=calls))
        self.assertEqual(len(calls), 1)
        url, token = calls[0]
        self.assertIn("/repos/example/widget/issues?labels=Owner:LeadDev&",
                      url)
        self.assertEqual(token, "read-token")

    def test_the_label_prefixes_are_configurable(self):
        self.data["labels"]["owner_prefix"] = "owner/"
        self.data["labels"]["status_prefix"] = "state/"
        self.write_config()
        calls = []
        issues = [{"number": 1, "title": "t",
                   "labels": [{"name": "state/blocked"}]}]
        ctx = self.context(call=fake_call(issues, calls=calls))[1][
            "additionalContext"]
        self.assertIn("labels=owner%2FLeadDev", calls[0][0])
        self.assertIn("#1 issue state/blocked", ctx)

    def test_a_failed_listing_says_so_rather_than_reading_as_none(self):
        """An empty list and a failed list must not look alike: the first
        means "nothing to re-watch", which a failure can't claim."""
        ctx = self.context(call=fake_call(status=502))[1]["additionalContext"]
        self.assertIn("Could not list", ctx)
        self.assertIn("502: Bad Gateway", ctx)
        self.assertIn("gh api 'repos/example/widget/issues", ctx)
        self.assertNotIn("You own no open items", ctx)

    def test_no_answer_is_a_failed_listing(self):
        ctx = self.context(call=fake_call(status=0))[1]["additionalContext"]
        self.assertIn("GitHub answered nothing", ctx)

    def test_the_listing_is_bounded_inside_the_hooks_timeout(self):
        self.context()
        self.assertLess(mod.github.REQUEST_TIMEOUT_S + 5, 30)

    def test_owning_nothing_is_said_plainly(self):
        ctx = self.context(call=fake_call([]))[1]["additionalContext"]
        self.assertIn("You own no open items", ctx)

    def test_no_configuration_says_so_and_still_briefs(self):
        self.environ["FORGE_CRON_CHECKOUT"] = "/nonexistent"
        ctx = self.context()[1]["additionalContext"]
        self.assertIn("Background tasks are gone", ctx)
        self.assertIn("no agent-forge configuration", ctx)

    def test_garbage_on_stdin_is_treated_as_a_startup(self):
        out = io.StringIO()
        self.assertEqual(mod.main(stdin=io.StringIO("not json"),
                                  run=fake_run(), out=out,
                                  environ=self.environ,
                                  call=fake_call(ISSUES)), 0)
        self.assertIn("SessionStart", out.getvalue())


class EndToEnd(unittest.TestCase):
    """The real script, on a real stamped repository, with nothing to
    reach GitHub through: what the hook runs is the thing tested, and a
    failed listing must still be a brief."""

    def test_the_script_prints_hook_json_for_a_stamped_worktree(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo = Path(tmp) / "wt"
            repo.mkdir()
            env = {"PATH": os.environ.get("PATH", ""), "HOME": tmp,
                   "GIT_CONFIG_GLOBAL": os.path.join(tmp, "gitconfig"),
                   "GIT_CONFIG_NOSYSTEM": "1",
                   "FORGE_SESSION_START_TIMEOUT": "2",
                   "FORGE_GITHUB_API": "http://127.0.0.1:9"}
            subprocess.run(["git", "init", "-q"], cwd=repo, check=True,
                           env=env)
            (repo / ".agent-forge").mkdir()
            (repo / ".agent-forge/config.json").write_text(
                json.dumps(example()))
            # Stamped as forge-worktree-identity stamps: the extension,
            # then the worktree's own config. The shared name beside it
            # must not win.
            for args in (["extensions.worktreeConfig", "true"],
                         ["user.name", "Reviewer (scratch)"],
                         ["--worktree", "user.name", "LeadDev"]):
                subprocess.run(["git", "config"] + args, cwd=repo, check=True,
                               env=env)
            got = subprocess.run(
                [str(TOOL)], input=json.dumps({"source": "startup",
                                               "cwd": str(repo)}),
                capture_output=True, text=True, env=env, timeout=30)
            self.assertEqual(got.returncode, 0, got.stderr)
            ctx = json.loads(got.stdout)[
                "hookSpecificOutput"]["additionalContext"]
            self.assertIn("for LeadDev", ctx)
            self.assertIn("Could not list", ctx)


if __name__ == "__main__":
    unittest.main()
