"""Tests for `checks.red_comment`: a red pull request head gets one
comment saying which steps failed, with the end of each one's output,
for agents and people who can't read worker.log on the host."""

import datetime
import os
import unittest

from agent_forge import config
from tests.helpers import example
from tests.test_forge_pr_check import SHA_A, T0, StateCase, Worker, pc


class Writer:
    """GitHub's REST API, each write recorded and answered `status`."""

    def __init__(self, status=201):
        self.writes, self.status = [], status

    def __call__(self, method, path, body=None):
        self.writes.append((method, path, body))
        return self.status, {"message": "nope"}


class RedCommentConfig(unittest.TestCase):

    def tearDown(self):
        pc.configure(config.Project(example(), None))

    def test_a_boolean_off_unless_configured(self):
        data = example()
        self.assertFalse(pc.RED_COMMENT)
        data["checks"]["red_comment"] = True
        self.assertEqual(config.check(data), [])
        pc.configure(config.Project(data, None))
        self.assertTrue(pc.RED_COMMENT)
        data["checks"]["red_comment"] = "yes"
        self.assertEqual(len(config.check(data)), 1)


class RedCommentText(unittest.TestCase):

    def test_names_the_steps_and_quotes_each_tail_without_colour(self):
        text = pc.red_comment_text(SHA_A, [
            ("browser", "\x1b[31mFAIL\x1b[39m src/a.test.ts\n"),
            ("vet", "error: unused\n")])
        lines = text.splitlines()
        self.assertTrue(lines[0].startswith(
            "%s is red at %s" % (pc.STATUS_CONTEXT, SHA_A[:10])))
        self.assertTrue(lines[0].endswith(": browser, vet."))
        self.assertIn("FAIL src/a.test.ts", text)
        self.assertNotIn("\x1b", text)
        self.assertIn("error: unused", text)
        self.assertEqual(lines[-1], "— %s" % pc.POSTER)

    def test_a_fence_outlasts_backticks_in_the_output(self):
        text = pc.red_comment_text(SHA_A, [("t", "a ```` b")])
        self.assertIn("\n`````\na ```` b\n`````\n", text)

    def test_a_long_tail_keeps_its_end(self):
        long = "x" * (pc.RED_COMMENT_CHARS + 50) + "THE END"
        text = pc.red_comment_text(SHA_A, [("t", long)])
        self.assertIn("[…]\n", text)
        self.assertIn("THE END", text)
        self.assertLess(len(text), pc.RED_COMMENT_CHARS + 1000)


class ReportPr(StateCase):

    run_for, queue = Worker.run_for, Worker.queue

    def test_comments_on_the_pull_request(self):
        write = Writer()
        pc.report_pr(self.state_dir, 7, SHA_A, [("vet", "boom")], write,
                     self.lines.append)
        (method, path, body), = write.writes
        self.assertEqual((method, path), (
            "POST", "/repos/%s/issues/7/comments" % pc.REPO_SLUG))
        self.assertIn("boom", body["body"])
        self.assertIn("COMMENTED #7: red at %s" % SHA_A[:10], self.lines[-1])

    def test_a_refused_comment_is_logged(self):
        pc.report_pr(self.state_dir, 7, SHA_A, [("vet", "boom")],
                     Writer(403), self.lines.append)
        self.assertIn("REPORT-FAILED #7 %s: commenting: HTTP 403 nope"
                      % SHA_A[:10], self.lines[-1])

    def told(self, failing):
        said = []
        os.makedirs(os.path.join(self.state_dir, pc.slot_name("wt", 1),
                                 ".git"), exist_ok=True)
        clock = iter([T0, T0 + datetime.timedelta(minutes=10)])
        self.queue()
        pc.worker("/repo", self.state_dir, SHA_A, 7, self.run_for(
            failing=failing), self.lines.append, lambda: next(clock),
            report=lambda *a: said.append(a))
        return said

    def test_the_worker_reports_a_red_head_with_its_failed_tails(self):
        (sha, ok, failed, same), = self.told(("vet",))
        self.assertEqual((sha, ok, same), (SHA_A, False, None))
        self.assertEqual([n for n, _ in failed], ["vet"])
        self.assertIn("it broke", failed[0][1])

    def test_and_says_nothing_of_a_green_one(self):
        self.assertEqual(self.told(()), [])


if __name__ == "__main__":
    unittest.main()
