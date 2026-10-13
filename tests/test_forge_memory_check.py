"""Tests for forge-memory-check.

The defect it watches is silent: `MEMORY.md` past the harness limit is
truncated rather than refused, entries are dropped from the end, and the
evidence that memories are missing is itself a memory that got dropped.
So the check's own failure modes matter more than usual -- a check for an
invisible defect that quietly passes leaves the agents exactly where they
started, with one more reason to believe it is fine.

Every case therefore constructs the condition and asserts the exit
status, rather than asserting on output text alone. Output can be right
while the status is wrong, and it is the status cron reads.
"""

import io
import os
import pathlib
import subprocess
import tempfile
import unittest

from agent_forge import config
from tests.helpers import ROOT, example, load_tool

check = load_tool("forge-memory-check")


def run(*args, environ=None, project=None):
    out, err = io.StringIO(), io.StringIO()
    code = check.main(list(args), environ=environ or {}, out=out, err=err,
                      project=project)
    return code, out.getvalue().strip(), err.getvalue()


class Memory:
    """A throwaway memory directory with exactly the entries a test names."""

    def __init__(self, entries, extra_files=(), padding=0):
        self.dir = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.dir.name)
        lines = [f"- [{name}]({name}.md) — hook" for name in entries]
        if padding:
            lines.append("- [pad](pad.md) — " + "x" * padding)
            (self.root / "pad.md").write_text("pad\n")
        (self.root / "MEMORY.md").write_text("\n".join(lines) + "\n")
        for name in entries:
            (self.root / f"{name}.md").write_text(f"# {name}\n")
        for name in extra_files:
            (self.root / f"{name}.md").write_text(f"# {name}\n")

    def run(self, *args):
        return run("--dir", str(self.root), *args)[:2]

    def close(self):
        self.dir.cleanup()


class MemoryCheckTest(unittest.TestCase):
    def mem(self, *a, **kw):
        m = Memory(*a, **kw)
        self.addCleanup(m.close)
        return m

    def test_a_healthy_directory_is_clean(self):
        code, out = self.mem(["alpha", "beta"]).run()
        self.assertEqual(code, 0)
        self.assertIn("clean", out)

    def test_over_the_limit_fails_and_says_it_is_truncating(self):
        """The status is what cron reads; the wording is what a person reads."""
        code, out = self.mem(["alpha"], padding=500).run("--limit", "200")
        self.assertEqual(code, 1)
        self.assertIn("TRUNCATED", out)

    def test_just_under_the_limit_warns_without_failing(self):
        """The fix is editorial, so the useful moment to speak is before
        the file is already losing entries."""
        m = self.mem(["alpha"], padding=100)
        size = (m.root / "MEMORY.md").stat().st_size
        code, out = m.run("--limit", str(int(size / 0.9)))
        self.assertEqual(code, 0, "a warning must not fail the job")
        self.assertIn("%", out)
        self.assertNotIn("clean", out)

    def test_an_index_entry_with_no_file_fails(self):
        m = self.mem(["alpha"])
        (m.root / "alpha.md").unlink()
        code, out = m.run()
        self.assertEqual(code, 1)
        self.assertIn("point at no file", out)

    def test_a_file_in_no_index_entry_fails(self):
        """Unindexed is the same silent loss as truncation by another route:
        the file is written and never loaded, so it is never read."""
        code, out = self.mem(["alpha"], extra_files=["orphan"]).run()
        self.assertEqual(code, 1)
        self.assertIn("in no index entry", out)

    def test_a_missing_directory_is_reported_not_passed_quietly(self):
        """A check that cannot see its subject must not report on it —
        and must not do so silently, which is how an inert check survives."""
        with tempfile.TemporaryDirectory() as tmp:
            code, out, _ = run("--dir", os.path.join(tmp, "absent"))
        self.assertEqual(code, 0)
        self.assertIn("nothing checked", out)

    def test_headroom_is_reported_in_entries_not_only_bytes(self):
        """A level with no denominator does not say how long anyone has:
        "21 more entries" does; "3897 bytes" and "91% full" do not.
        """
        m = self.mem(["alpha", "beta", "gamma"])
        code, out = m.run()
        self.assertEqual(code, 0)
        self.assertIn("room for about", out)
        self.assertIn("per entry", out)

    def test_headroom_appears_on_the_warning_path_too(self):
        """The reader who most needs the denominator is the one who has
        just been told the level."""
        m = self.mem(["alpha"], padding=100)
        size = (m.root / "MEMORY.md").stat().st_size
        code, out = m.run("--limit", str(int(size / 0.9)))
        self.assertEqual(code, 0)
        self.assertIn("%", out)
        self.assertIn("room for about", out)

    def test_an_index_gone_from_a_populated_directory_fails(self):
        """The severe case: not partial truncation, but every memory
        unreachable at once. Reachable rather than theoretical: repairing
        an over-long index reads `MEMORY.md`, transforms it and writes it
        back, so a crash between the two produces exactly this.
        """
        m = self.mem(["alpha", "beta"])
        (m.root / "MEMORY.md").unlink()
        code, out = m.run()
        self.assertEqual(code, 1)
        self.assertIn("MISSING", out)
        self.assertIn("2 topic file", out)

    def test_an_empty_directory_is_still_nothing_to_check(self):
        """The control for the case above: the branch must split on whether
        there is anything to lose, not merely on the index being absent."""
        m = self.mem([])
        (m.root / "MEMORY.md").unlink()
        code, out = m.run()
        self.assertEqual(code, 0)
        self.assertIn("nothing checked", out)

    def test_the_size_check_and_the_coupling_check_are_independent(self):
        """Both broken must still fail, and neither must mask the other.

        A check that stops at its first finding reports one defect and
        hides the rest, so the next run looks like a regression when it is
        the same run's second sentence.
        """
        m = self.mem(["alpha"], extra_files=["orphan"], padding=500)
        code, out = m.run("--limit", "200")
        self.assertEqual(code, 1)
        self.assertIn("TRUNCATED", out)
        self.assertIn("in no index entry", out)


def padded_to(total_bytes):
    """The padding that makes Memory's pad line exactly total_bytes long.

    The pad line opens with `- [pad](pad.md) — `, which is 20 bytes
    because the em dash is three.
    """
    prefix = len("- [pad](pad.md) — ".encode())
    return total_bytes - prefix


class LongLineTest(unittest.TestCase):
    """One index line that has grown is reported by itself.

    The total fires near the limit, long after whoever appended a lesson
    to a pointer has gone. These pin the per-line report: that it fires,
    where its boundary is, what it names, and that it only warns.
    """

    def mem(self, *a, **kw):
        m = Memory(*a, **kw)
        self.addCleanup(m.close)
        return m

    def test_a_long_line_warns_and_names_its_topic_file(self):
        code, out = self.mem(["alpha", "beta"], padding=padded_to(260)).run()
        self.assertEqual(code, 0, "a long line is editorial: it warns, it doesn't fail")
        self.assertIn("1 index line(s) over 200 bytes", out)
        # Line 3 is the pad line, after alpha and beta.
        self.assertIn("L3  260  pad.md", out)
        self.assertNotIn("clean", out, "a directory with a long line is not reported clean")

    def test_the_boundary_is_strictly_over_the_threshold(self):
        """The near-miss case: exactly 200 bytes is allowed, 201 is not."""
        code, out = self.mem(["alpha"], padding=padded_to(200)).run()
        self.assertEqual(code, 0)
        self.assertNotIn("over 200 bytes", out)
        self.assertIn("clean", out)
        code, out = self.mem(["alpha"], padding=padded_to(201)).run()
        self.assertEqual(code, 0)
        self.assertIn("L2  201  pad.md", out)

    def test_length_is_bytes_not_characters(self):
        """The limit is bytes, so the line length is too. This line is 81
        characters and 203 bytes (61 em dashes at three bytes each), which a
        character count would pass."""
        m = self.mem(["alpha"])
        index = m.root / "MEMORY.md"
        index.write_text(index.read_text() + "- [alpha](alpha.md) " + "—" * 61 + "\n")
        line = index.read_text().splitlines()[1]
        self.assertLessEqual(len(line), 200, "the fixture must be short in characters")
        self.assertGreater(len(line.encode()), 200, "and long in bytes")
        code, out = m.run()
        self.assertEqual(code, 0)
        self.assertIn(f"L2  {len(line.encode())}  alpha.md", out)

    def test_a_long_line_does_not_mask_a_real_failure(self):
        """The warning leaves the status to the checks that fail."""
        m = self.mem(["alpha"], extra_files=["orphan"], padding=padded_to(300))
        code, out = m.run()
        self.assertEqual(code, 1)
        self.assertIn("in no index entry", out)
        self.assertIn("over 200 bytes", out)

    def test_the_threshold_is_a_flag(self):
        m = self.mem(["alpha"], padding=padded_to(150))
        self.assertNotIn("over", m.run()[1])
        code, out = m.run("--long-line", "100")
        self.assertEqual(code, 0)
        self.assertIn("1 index line(s) over 100 bytes", out)

    def test_many_long_lines_are_capped_with_a_count(self):
        m = self.mem(["alpha"])
        index = m.root / "MEMORY.md"
        extra = "".join(f"- [alpha](alpha.md) {'x' * 250}\n" for _ in range(12))
        index.write_text(index.read_text() + extra)
        code, out = m.run()
        self.assertEqual(code, 0)
        self.assertIn("12 index line(s) over 200 bytes", out)
        self.assertEqual(out.count("  alpha.md"), 10)
        self.assertIn("... and 2 more", out)


class Where(unittest.TestCase):
    """The directory and limits: flags, else the variable, else the
    configuration."""

    def setUp(self):
        self.m = Memory(["alpha"], padding=100)
        self.addCleanup(self.m.close)

    def project(self, **memory):
        data = example()
        data.pop("memory", None)
        if memory:
            data["memory"] = dict({"remote": "/r"}, **memory)
        return config.Project(data, None)

    def test_the_variable_names_the_directory(self):
        code, out, _ = run(environ={"FORGE_MEMORY_DIR": str(self.m.root)})
        self.assertEqual(code, 0)
        self.assertIn("clean", out)

    def test_the_configuration_names_the_directory_and_its_limits(self):
        code, out, _ = run(project=self.project(dir=str(self.m.root)))
        self.assertIn("clean", out)
        code, out, _ = run(project=self.project(dir=str(self.m.root),
                                                limit=100))
        self.assertEqual(code, 1)
        self.assertIn("100-byte limit", out)
        code, out, _ = run(project=self.project(dir=str(self.m.root),
                                                long_line=50))
        self.assertIn("over 50 bytes", out)

    def test_a_flag_beats_the_configuration(self):
        code, out, _ = run("--limit", "100000", project=self.project(
            dir=str(self.m.root), limit=100))
        self.assertEqual(code, 0)

    def test_nowhere_named_is_2(self):
        code, _, err = run(project=self.project())
        self.assertEqual(code, 2)
        self.assertIn("no memory.dir", err)

    def test_no_configuration_and_no_variable_is_2(self):
        with tempfile.TemporaryDirectory() as tmp:
            code, _, err = run(environ={"FORGE_CONFIG": os.path.join(
                tmp, "absent.json")})
        self.assertEqual(code, 2)
        self.assertIn("no --dir", err)

    def test_the_script_runs_as_a_program(self):
        r = subprocess.run([str(ROOT / "bin" / "forge-memory-check"),
                            "--dir", str(self.m.root)],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("clean", r.stdout)


if __name__ == "__main__":
    unittest.main()
