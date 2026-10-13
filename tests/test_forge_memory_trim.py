"""Tests for forge-memory-trim.

The failure that matters is **losing the only copy of something**, not
leaving the index long. A trim that drops a clause nobody wrote down
elsewhere is the harm the tool exists to prevent, and it is silent from
both ends: the writer believes it wrote, the reader sees a
plausible line and never learns there was more.

So every test here asks what happened to the *text*, and the negative
cases assert the difference between "moved, and still readable in the
topic file" and "gone".
"""

import io
import os
import pathlib
import subprocess
import tempfile
import unittest

from agent_forge import config
from tests.helpers import ROOT, example, load_tool

trim = load_tool("forge-memory-trim")

# A clause whose substance IS in the topic file, and one whose is not.
COVERED = ("**Added 2026-09-09**: the partition rebalances whenever a "
           "coordinator restarts unexpectedly.")
UNCOVERED = ("**Added 2026-09-09**: the timestamp stays frozen at attempt "
             "one throughout a rerun.")


class Store:
    def __init__(self):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        self.lines = []

    def topic(self, name, body):
        (self.root / name).write_text(
            f"---\nname: {name[:-3]}\ndescription: d\n---\n\n{body}\n")

    def entry(self, title, name, hook):
        self.lines.append(f"- [{title}]({name}) — {hook}")

    def write(self):
        (self.root / "MEMORY.md").write_text("\n".join(self.lines) + "\n")

    def index(self):
        return (self.root / "MEMORY.md").read_text()

    def run(self, *args):
        out, err = io.StringIO(), io.StringIO()
        code = trim.main(["--dir", str(self.root), *args], environ={},
                         out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def close(self):
        self.dir.cleanup()


class MemoryTrimTest(unittest.TestCase):
    def store(self):
        s = Store()
        self.addCleanup(s.close)
        return s

    def covered(self):
        """An entry whose appended clause is already in its topic file."""
        s = self.store()
        s.topic("a.md", "The partition rebalances whenever a coordinator "
                        "restarts unexpectedly, which is the whole point.")
        s.entry("A", "a.md", "the short hook. " + COVERED)
        s.write()
        return s

    # --- the thing it exists to do ---------------------------------------

    def test_a_covered_clause_is_movable(self):
        code, out, _ = self.covered().run()
        self.assertIn("CANDIDATE", out)
        self.assertEqual(code, 0)

    def test_apply_shortens_the_index_line(self):
        s = self.covered()
        before = len(s.index())
        s.run("--apply", "--entry", "a.md")
        self.assertLess(len(s.index()), before)
        self.assertIn("the short hook.", s.index())

    def test_apply_keeps_the_link_and_title_intact(self):
        """The line is still an index entry, not a fragment of one."""
        s = self.covered()
        s.run("--apply", "--entry", "a.md")
        self.assertIn("- [A](a.md) — ", s.index())

    # --- the thing it must never do --------------------------------------

    def test_an_uncovered_clause_is_refused_not_dropped(self):
        """The load-bearing case. Dropping this loses the only copy."""
        s = self.store()
        s.topic("b.md", "Unrelated prose about something else entirely.")
        s.entry("B", "b.md", "the short hook. " + UNCOVERED)
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", err)
        self.assertIn("b.md", err)

    def test_a_refused_clause_survives_apply(self):
        """--apply must not quietly take the refused one with it."""
        s = self.store()
        s.topic("b.md", "Unrelated prose about something else entirely.")
        s.entry("B", "b.md", "the short hook. " + UNCOVERED)
        s.write()
        s.run("--apply", "--entry", "b.md")
        self.assertIn("frozen", s.index())

    def test_the_refusal_names_the_missing_words(self):
        """So the fix -- append it to the topic file -- is actionable."""
        s = self.store()
        s.topic("b.md", "Unrelated prose about something else entirely.")
        s.entry("B", "b.md", "hook. " + UNCOVERED)
        s.write()
        _, _, err = s.run()
        self.assertIn("frozen", err)

    def test_nothing_is_ever_truncated(self):
        """A character cap's damage: cutting mid-sentence.

        A very long hook with no appended clause has nothing to move, and
        must come through byte-identical rather than shortened."""
        s = self.store()
        long_hook = "x" * 400 + " and the operative half is at the very end."
        s.topic("c.md", "anything")
        s.entry("C", "c.md", long_hook)
        s.write()
        before = s.index()
        s.run("--apply", "--entry", "c.md")
        self.assertEqual(s.index(), before)
        self.assertNotIn("…", s.index())

    def test_a_missing_topic_file_is_refused_not_assumed_empty(self):
        """A failed read is not evidence the text is elsewhere."""
        s = self.store()
        s.entry("D", "gone.md", "hook. " + COVERED)
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("cannot read", err)

    # --- controls ---------------------------------------------------------

    def test_coverage_can_come_out_both_ways_on_one_index(self):
        """The control that makes the refusal mean something: the same
        run must move one and refuse the other, or 'refused' could be
        what it always says."""
        s = self.store()
        s.topic("a.md", "The partition rebalances whenever a coordinator "
                        "restarts unexpectedly.")
        s.topic("b.md", "Unrelated prose about something else entirely.")
        s.entry("A", "a.md", "hook one. " + COVERED)
        s.entry("B", "b.md", "hook two. " + UNCOVERED)
        s.write()
        code, out, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("a.md", out)
        self.assertIn("REFUSED  b.md", err)

    def test_one_missing_word_is_enough_to_refuse(self):
        """Every content word must be present, not most of them: in "the
        timestamp stays frozen", `frozen` is the entire finding. A
        majority threshold accepts the rest and deletes it, because the
        words that survive rewording are the common ones and the word that
        does not is the one carrying the news."""
        s = self.store()
        s.topic("f.md", "The partition rebalances whenever a coordinator "
                        "restarts unexpectedly and the timestamp matters.")
        s.entry("F", "f.md", "hook. **Added 2026-09-09**: the partition "
                             "rebalances whenever a coordinator restarts "
                             "unexpectedly and the timestamp stays frozen.")
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("frozen", err)

    def test_an_index_with_nothing_to_move_reports_zero_and_passes(self):
        s = self.store()
        s.topic("a.md", "anything")
        s.entry("A", "a.md", "a short hook with no appended clause.")
        s.write()
        code, out, err = s.run()
        self.assertEqual(code, 0)
        self.assertNotIn("CANDIDATE", out)
        self.assertIn("0 candidate", err)

    def test_check_fails_when_a_line_could_be_shortened(self):
        code, _, _ = self.covered().run("--check")
        self.assertEqual(code, 1)

    def test_check_passes_once_apply_has_run(self):
        """--check and --apply must agree, or the check is decoration."""
        s = self.covered()
        s.run("--apply", "--entry", "a.md")
        code, _, _ = s.run("--check")
        self.assertEqual(code, 0)

    def test_an_unverifiable_clause_is_refused_not_accepted(self):
        """Deny-by-default. A clause with no long words cannot be checked
        against the topic file, and 'cannot check' must not read as
        'safe to delete': otherwise raising the word threshold would make
        every clause uncheckable and therefore removable, and the tool
        would empty the index by being unable to read any of it."""
        s = self.store()
        s.topic("e.md", "anything at all")
        s.entry("E", "e.md", "hook. **Added 2026-09-09**: see #123.")
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", err)
        s.run("--apply", "--entry", "e.md")
        self.assertIn("#123", s.index())

    def test_an_already_truncated_line_is_reported(self):
        """Detection is mechanical even though repair is not."""
        s = self.store()
        s.topic("g.md", "anything")
        s.entry("G", "g.md", "a hook cut off mid-sent\u2026")
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("TRUNCATED", err)
        self.assertIn("g.md", err)

    def test_a_full_stop_after_the_ellipsis_still_counts(self):
        """A cut line sometimes gained a full stop AFTER the ellipsis,
        which a trailing-anchor test misses."""
        s = self.store()
        s.topic("h.md", "anything")
        s.entry("H", "h.md", "a hook cut off mid-sent\u2026.")
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("TRUNCATED", err)

    def test_an_ellipsis_mid_sentence_is_not_damage(self):
        """The control. `00:17/03:17/\u2026, not 02:17` is an "and so on",
        and a bare character test flags it -- which would train everyone
        to ignore the warning."""
        s = self.store()
        s.topic("i.md", "anything")
        s.entry("I", "i.md", "fires at 00:17/03:17/\u2026, not 02:17; enumerate "
                             "the range instead.")
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 0)
        self.assertNotIn("TRUNCATED", err)

    # --- safe to lose is not should go -----------------------------------

    def test_apply_refuses_without_an_entry(self):
        """No bulk mode. The safety test says nothing would be lost; it
        cannot say the hook still tells a reader to open the file, and a
        bulk run can keep the superseded half of a line and delete the
        live one."""
        s = self.covered()
        before = s.index()
        code, _, err = s.run("--apply")
        self.assertEqual(code, 2)
        self.assertIn("no bulk mode", err.lower())
        self.assertEqual(s.index(), before)

    def test_apply_touches_only_the_named_entry(self):
        s = self.store()
        s.topic("a.md", "The partition rebalances whenever a coordinator "
                        "restarts unexpectedly.")
        s.topic("z.md", "The partition rebalances whenever a coordinator "
                        "restarts unexpectedly.")
        s.entry("A", "a.md", "hook one. " + COVERED)
        s.entry("Z", "z.md", "hook two. " + COVERED)
        s.write()
        s.run("--apply", "--entry", "a.md")
        self.assertNotIn("rebalances", s.index().split("z.md")[0])
        self.assertIn("rebalances", s.index().split("z.md")[1])

    def test_a_superseding_clause_is_flagged(self):
        """The dangerous case: the clause replaces what the hook says, so
        removing it leaves the line advertising the superseded fact."""
        s = self.store()
        s.topic("a.md", "The replacement is the step count, executed "
                        "twelve versus never-ran zero, measured.")
        s.entry("A", "a.md", "the cause is on /annotations. "
                             "**Added 2026-09-09**: the replacement is the "
                             "step count, executed twelve versus never-ran "
                             "zero, measured.")
        s.write()
        _, out, err = s.run()
        self.assertIn("SUPERSEDES", out)
        self.assertIn("candidates, not", err)

    def test_an_additive_clause_is_not_flagged_as_superseding(self):
        """The control. Most clauses add rather than replace, and a flag
        on all of them would carry no information."""
        s = self.covered()
        _, out, _ = s.run()
        self.assertIn("CANDIDATE", out)
        self.assertNotIn("SUPERSEDES", out)

    def test_the_topic_file_is_never_written(self):
        """It removes, having verified; it does not move."""
        s = self.covered()
        before = (s.root / "a.md").read_bytes()
        s.run("--apply", "--entry", "a.md")
        self.assertEqual((s.root / "a.md").read_bytes(), before)

    def test_a_missing_index_is_status_2_not_success(self):
        """A check that cannot see its subject must not report on it."""
        s = self.store()
        code, _, err = s.run()
        self.assertEqual(code, 2)
        self.assertIn("no index", err)

    def test_short_shared_words_do_not_count_as_coverage(self):
        """'the', 'and', 'that' are in every file in the directory. If
        they counted, every clause would look covered and the tool would
        delete text for a living."""
        s = self.store()
        s.topic("b.md", "the and that with this from they have been were")
        s.entry("B", "b.md", "hook. **Added 2026-09-09**: the coordinator "
                             "rebalances and that is that.")
        s.write()
        code, _, err = s.run()
        self.assertEqual(code, 1)
        self.assertIn("REFUSED", err)


class Where(unittest.TestCase):
    """The directory: --dir, else $FORGE_MEMORY_DIR, else memory.dir."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = pathlib.Path(tmp.name)
        (self.root / "MEMORY.md").write_text("- [A](a.md) — hook\n")
        (self.root / "a.md").write_text("a\n")

    def run_with(self, environ, project=None):
        out, err = io.StringIO(), io.StringIO()
        code = trim.main([], environ=environ, out=out, err=err,
                         project=project)
        return code, err.getvalue()

    def test_from_the_variable(self):
        code, err = self.run_with({"FORGE_MEMORY_DIR": str(self.root)})
        self.assertEqual(code, 0, err)

    def test_from_the_configuration(self):
        data = example()
        data["memory"] = {"dir": str(self.root), "remote": "/r"}
        code, err = self.run_with({}, config.Project(data, None))
        self.assertEqual(code, 0, err)

    def test_nowhere_is_2(self):
        data = example()
        data.pop("memory", None)
        code, err = self.run_with({}, config.Project(data, None))
        self.assertEqual(code, 2)
        self.assertIn("no --dir", err)

    def test_the_script_runs_as_a_program(self):
        r = subprocess.run([str(ROOT / "bin" / "forge-memory-trim"), "--dir",
                            str(self.root)], capture_output=True, text=True,
                           env=dict(os.environ))
        self.assertEqual(r.returncode, 0, r.stderr)


if __name__ == "__main__":
    unittest.main()
