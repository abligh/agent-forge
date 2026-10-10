"""Tests for bin/forge-host-settings.

What it has to get right is what a person relies on when running it on a
host whose every agent loads the result: a bad copy must never be written,
a failure part-way must leave the old file whole, and the old file must be
kept.
"""
import datetime
import io
import json
import os
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from tests.helpers import example, load_tool

tool = load_tool("forge-host-settings")

SETTINGS = json.dumps({"permissions": {"allow": ["Read(//home/claude/**)"]}},
                      indent=2) + "\n"
HOOK = '#!/bin/sh\nexec "$HOME/widget-cron/tools/session-start" "$@"\n'


class Host(unittest.TestCase):
    """A tree with the tracked copies, and a home to install into."""

    def setUp(self):
        self._dir = tempfile.TemporaryDirectory()
        root = Path(self._dir.name)
        self.tree, self.home = root / "tree", root / "home"
        (self.tree / "tools/host").mkdir(parents=True)
        self.home.mkdir()
        (self.tree / ".agent-forge").mkdir()
        self.write_config(example())
        self.write_tracked(SETTINGS, HOOK)
        self.addCleanup(self._dir.cleanup)

    def write_config(self, data):
        (self.tree / ".agent-forge/config.json").write_text(json.dumps(data))

    def write_tracked(self, settings, hook):
        (self.tree / "tools/host/claude-settings.json").write_text(settings)
        (self.tree / "tools/host/session-start-hook").write_text(hook)

    def settings(self):
        return self.home / ".claude/settings.json"

    def hook(self):
        return self.home / ".claude/hooks/session-start"

    def run_tool(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = tool.main([*args, "--tree", str(self.tree)], out=out, err=err,
                         environ={"HOME": str(self.home)})
        return code, out.getvalue(), err.getvalue()


class Check(Host):
    def test_a_matching_host_exits_0(self):
        self.run_tool("--install")
        code, out, _ = self.run_tool("--check")
        self.assertEqual(code, 0)
        self.assertIn("host matches", out)

    def test_a_differing_host_exits_1_with_a_diff(self):
        self.run_tool("--install")
        self.settings().write_text(SETTINGS.replace("Read", "Edit"))
        code, out, err = self.run_tool("--check")
        self.assertEqual(code, 1)
        self.assertIn('-      "Edit(//home/claude/**)"', out)
        self.assertIn('+      "Read(//home/claude/**)"', out)
        self.assertIn(str(self.settings()), err)

    def test_an_absent_host_file_differs_and_says_so(self):
        code, out, _ = self.run_tool("--check")
        self.assertEqual(code, 1)
        self.assertIn("(absent)", out)

    def test_check_never_writes(self):
        self.run_tool("--check")
        self.assertFalse(self.settings().exists())

    def test_a_missing_tracked_copy_exits_2(self):
        (self.tree / "tools/host/session-start-hook").unlink()
        code, _, err = self.run_tool("--check")
        self.assertEqual(code, 2)
        self.assertIn("no such file", err)


class Install(Host):
    def test_install_writes_both_files_with_their_modes(self):
        code, out, _ = self.run_tool("--install")
        self.assertEqual(code, 0)
        self.assertEqual(self.settings().read_text(), SETTINGS)
        self.assertEqual(self.hook().read_text(), HOOK)
        self.assertEqual(stat.S_IMODE(self.hook().stat().st_mode), 0o755)
        self.assertEqual(stat.S_IMODE(self.settings().stat().st_mode), 0o644)
        self.assertIn("next start", out)

    def test_invalid_json_is_refused_and_the_host_left_alone(self):
        self.run_tool("--install")
        before = self.settings().read_text()
        self.write_tracked('{"permissions": ', HOOK + "# changed\n")
        code, _, err = self.run_tool("--install")
        self.assertEqual(code, 2)
        self.assertIn("not valid JSON", err)
        self.assertEqual(self.settings().read_text(), before)
        self.assertEqual(self.hook().read_text(), HOOK)  # nothing at all written

    def backups(self):
        return self.home / ".local/state/agent-forge/widget/host-settings/backups"

    def test_the_replaced_file_is_kept(self):
        self.settings().parent.mkdir(parents=True)
        self.settings().write_text('{"old": true}\n')
        _, out, _ = self.run_tool("--install")
        kept = [p for p in self.backups().iterdir()
                if p.name.startswith("settings.json.")]
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].read_text(), '{"old": true}\n')
        self.assertIn("kept the old file as %s" % kept[0], out)

    def test_no_backup_is_left_beside_the_hook(self):
        """Every file in ~/.claude/hooks is taken for a hook, so a backup
        there would be one. Only the hook may be there."""
        self.hook().parent.mkdir(parents=True)
        self.hook().write_text("#!/bin/sh\nexec old\n")
        self.settings().write_text("{}\n")
        self.run_tool("--install")
        self.assertEqual(os.listdir(self.hook().parent), ["session-start"])
        self.assertEqual(sorted(p.name for p in self.settings().parent.iterdir()),
                         ["hooks", "settings.json"])
        self.assertEqual(len(list(self.backups().iterdir())), 2)

    def test_a_second_install_does_nothing(self):
        self.run_tool("--install")
        entries = sorted(os.listdir(self.settings().parent))
        code, out, _ = self.run_tool("--install")
        self.assertEqual(code, 0)
        self.assertIn("nothing to install", out)
        self.assertEqual(sorted(os.listdir(self.settings().parent)), entries)
        self.assertFalse(self.backups().exists())  # nothing was replaced

    def test_only_the_differing_file_is_written(self):
        self.run_tool("--install")
        self.write_tracked(SETTINGS, HOOK + "# changed\n")
        _, out, _ = self.run_tool("--install")
        self.assertIn(str(self.hook()), out)
        self.assertNotIn(str(self.settings()), out)

    def test_a_failure_part_way_leaves_the_old_file_whole(self):
        self.settings().parent.mkdir(parents=True)
        self.settings().write_text('{"old": true}\n')
        with mock.patch.object(tool.os, "replace", side_effect=OSError("disk")):
            with self.assertRaises(OSError):
                tool.install_one(str(self.settings()), SETTINGS, 0o644,
                                 str(self.backups()), now=datetime.datetime(2026, 9, 29,
                                                       tzinfo=datetime.timezone.utc))
        self.assertEqual(self.settings().read_text(), '{"old": true}\n')
        leftovers = [p.name for p in self.settings().parent.iterdir()
                     if p.name.startswith(".settings.json.")]
        self.assertEqual(leftovers, [])  # the temporary file is gone too


class ClaudeCodesOwnWrites(Host):
    """Claude Code rewrites the file to record a dismissed warning, adding
    its own key and possibly reordering others. The settings are still the
    tracked ones; only the host's choice is new."""

    TRACKED = json.dumps({"autoMode": {"environment": ["e"],
                                       "soft_deny": ["s"]}}, indent=2) + "\n"
    REWRITTEN = json.dumps({"autoMode": {"soft_deny": ["s"],
                                         "environment": ["e"]},
                            "skipWorkflowUsageWarning": True}, indent=2) + "\n"

    def setUp(self):
        super().setUp()
        self.write_tracked(self.TRACKED, HOOK)
        self.run_tool("--install")
        self.settings().write_text(self.REWRITTEN)

    def test_a_reordered_file_with_a_host_key_matches(self):
        code, out, _ = self.run_tool("--check")
        self.assertEqual(code, 0, out)

    def test_a_real_difference_still_differs(self):
        self.settings().write_text(self.REWRITTEN.replace('"e"', '"x"'))
        code, out, _ = self.run_tool("--check")
        self.assertEqual(code, 1)
        self.assertIn('"x"', out)

    def test_a_reordered_list_is_still_a_difference(self):
        """Lists are rules in order: only object keys may move."""
        self.write_tracked(SETTINGS.replace(
            '"Read(//home/claude/**)"', '"A", "B"'), HOOK)
        self.run_tool("--install")
        self.settings().write_text(self.settings().read_text().replace(
            '"A",\n      "B"', '"B",\n      "A"'))
        code, _, _ = self.run_tool("--check")
        self.assertEqual(code, 1)

    def test_install_keeps_the_hosts_key(self):
        self.write_tracked(self.TRACKED.replace('"e"', '"new"'), HOOK)
        code, _, _ = self.run_tool("--install")
        self.assertEqual(code, 0)
        got = json.loads(self.settings().read_text())
        self.assertEqual(got["autoMode"]["environment"], ["new"])
        self.assertIs(got["skipWorkflowUsageWarning"], True)
        self.assertEqual(self.run_tool("--check")[0], 0)

    def test_without_a_host_key_install_writes_the_tracked_bytes(self):
        self.settings().write_text("{}\n")
        self.run_tool("--install")
        self.assertEqual(self.settings().read_text(), self.TRACKED)


class ACorruptHostFile(Host):
    """A host file that isn't a settings object never reads as matching,
    and --install replaces it with the tracked copy as it stands."""

    def check_and_repair(self, junk):
        self.settings().parent.mkdir(parents=True, exist_ok=True)
        self.settings().write_text(junk)
        code, _, _ = self.run_tool("--check")
        self.assertEqual(code, 1, junk)
        code, _, _ = self.run_tool("--install")
        self.assertEqual(code, 0, junk)
        self.assertEqual(self.settings().read_text(), SETTINGS)
        self.assertEqual(self.run_tool("--check")[0], 0)

    def test_invalid_json_differs_and_is_repaired(self):
        self.check_and_repair('{"permissions": ')

    def test_a_json_list_differs_and_is_repaired(self):
        self.check_and_repair("[]\n")

    def test_json_null_differs_and_is_repaired(self):
        self.check_and_repair("null\n")


class Configuration(Host):
    """Which files, and which keys are the host's, come from the project."""

    def test_no_files_listed_exits_2(self):
        data = example()
        del data["host_settings"]
        self.write_config(data)
        code, _, err = self.run_tool("--check")
        self.assertEqual(code, 2)
        self.assertIn("host_settings.files", err)

    def test_a_bad_configuration_exits_2(self):
        (self.tree / ".agent-forge/config.json").write_text("{")
        self.assertEqual(self.run_tool("--check")[0], 2)

    def test_no_tree_exits_2(self):
        err = io.StringIO()
        self.assertEqual(tool.main(["--check"], out=io.StringIO(), err=err,
                                   environ={"HOME": str(self.home)}), 2)
        self.assertIn("FORGE_CRON_CHECKOUT", err.getvalue())

    def test_the_tree_comes_from_the_cron_checkout(self):
        out = io.StringIO()
        code = tool.main(["--install"], out=out, err=io.StringIO(),
                         environ={"HOME": str(self.home),
                                  "FORGE_CRON_CHECKOUT": str(self.tree)})
        self.assertEqual(code, 0)
        self.assertEqual(self.settings().read_text(), SETTINGS)

    def test_no_host_keys_means_every_key_counts(self):
        data = example()
        del data["host_settings"]["host_keys"]
        self.write_config(data)
        self.run_tool("--install")
        got = json.loads(SETTINGS)
        got["skipWorkflowUsageWarning"] = True
        self.settings().write_text(json.dumps(got))
        self.assertEqual(self.run_tool("--check")[0], 1)

    def test_a_file_not_marked_json_compares_as_bytes(self):
        data = example()
        data["host_settings"]["files"][0]["json"] = False
        self.write_config(data)
        self.run_tool("--install")
        self.settings().write_text(json.dumps(json.loads(SETTINGS)))
        self.assertEqual(self.run_tool("--check")[0], 1)


if __name__ == "__main__":
    unittest.main()
