"""Tests for agent_forge.proc."""
import sys
import unittest

from agent_forge import proc


class Sh(unittest.TestCase):

    def test_captures_text(self):
        got = proc.sh([sys.executable, "-c", "print('hi')"])
        self.assertEqual((got.returncode, got.stdout), (0, "hi\n"))

    def test_a_timeout_is_124_not_an_exception(self):
        got = proc.sh([sys.executable, "-c",
                       "import sys,time; print('started', flush=True); "
                       "time.sleep(30)"], timeout=1)
        self.assertEqual(got.returncode, 124)
        self.assertIn("timed out after 1s", got.stderr)

    def test_a_missing_program_is_127(self):
        got = proc.sh(["/nonexistent/program"])
        self.assertEqual(got.returncode, 127)
        self.assertIn("/nonexistent/program", got.stderr)


if __name__ == "__main__":
    unittest.main()
