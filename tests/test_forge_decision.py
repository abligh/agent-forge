"""Tests for bin/forge-decision, with GitHub faked."""
import unittest

from agent_forge import config
from tests.helpers import EXAMPLE, example, load_tool

tool = load_tool("forge-decision")
OWNER = "example-owner"


def comment(cid, at, login=OWNER, kind="User", assoc="OWNER", updated=None,
            body="yes"):
    return {"id": cid, "created_at": at, "updated_at": updated or at,
            "author_association": assoc, "body": body,
            "user": {"login": login, "type": kind}}


class FakeGitHub:
    def __init__(self, comments, owner=OWNER, opened="2026-10-01T00:00:00Z",
                 fail=None):
        self.comments, self.owner, self.opened, self.fail = \
            comments, owner, opened, fail
        self.tokens = set()

    def __call__(self, url, token, scheme="Bearer", method="GET", body=None):
        self.tokens.add(token)
        path = url.split("api.github.com", 1)[1]
        if self.fail and self.fail in path:
            return 502, {}
        if path.startswith("/repos/example/widget/issues/7/comments"):
            page = int(path.rsplit("page=", 1)[1])
            return 200, self.comments[(page - 1) * 100:page * 100]
        if path == "/repos/example/widget/issues/7":
            return 200, {"created_at": self.opened}
        if path == "/repos/example/widget":
            return 200, {"owner": {"login": self.owner}}
        return 404, {}


class Decisions(unittest.TestCase):

    def run_tool(self, gh, answering="1", *extra):
        self.out, self.err = [], []
        return tool.main(["--issue", "7", "--answering", answering, *extra],
                         self.out.append, self.err.append, gh,
                         lambda: config.Project(example(), str(EXAMPLE)),
                         {"GH_TOKEN": "t"})

    ASK = comment(1, "2026-10-02T00:00:00Z", login="leaddevwidgetagent[bot]",
                  kind="Bot", assoc="NONE", body="May I?")

    def test_an_owner_comment_after_the_ask_is_a_decision(self):
        gh = FakeGitHub([self.ASK, comment(2, "2026-10-02T01:00:00Z")])
        self.assertEqual(self.run_tool(gh), 0)
        self.assertEqual(self.out[0], "decision: comment 2 by example-owner "
                                      "at 2026-10-02T01:00:00Z")
        self.assertIn("  | yes", self.out)
        self.assertEqual(gh.tokens, {"t"})

    def test_before_the_ask_is_not(self):
        gh = FakeGitHub([comment(2, "2026-10-01T23:00:00Z"), self.ASK])
        self.assertEqual(self.run_tool(gh), 1)

    def test_only_the_owner_as_a_user_counts(self):
        later = "2026-10-03T00:00:00Z"
        gh = FakeGitHub([self.ASK,
                         comment(3, later, kind="Bot"),
                         comment(4, later, assoc="MEMBER"),
                         comment(5, later, login="someone-else")])
        self.assertEqual(self.run_tool(gh), 1)

    def test_an_edited_candidate_is_2(self):
        gh = FakeGitHub([self.ASK, comment(2, "2026-10-03T00:00:00Z",
                                           updated="2026-10-04T00:00:00Z")])
        self.assertEqual(self.run_tool(gh), 2)
        self.assertIn("was edited", self.err[0])

    def test_an_ask_from_another_item_is_2(self):
        gh = FakeGitHub([comment(2, "2026-10-03T00:00:00Z")])
        self.assertEqual(self.run_tool(gh, "99"), 2)
        self.assertIn("not on example/widget#7", self.err[0])

    def test_the_body_as_the_ask(self):
        gh = FakeGitHub([comment(2, "2026-10-01T01:00:00Z")])
        self.assertEqual(self.run_tool(gh, "body"), 0)
        gh = FakeGitHub([comment(2, "2026-10-01T01:00:00Z")],
                        opened="2026-10-02T00:00:00Z")
        self.assertEqual(self.run_tool(gh, "body"), 1)

    def test_api_failures_are_2_not_no(self):
        for part in ("/comments", "/repos/example/widget"):
            gh = FakeGitHub([self.ASK, comment(2, "2026-10-03T00:00:00Z")],
                            fail=part)
            self.assertEqual(self.run_tool(gh), 2, part)

    def test_pages_are_followed(self):
        filler = [comment(100 + i, "2026-10-01T00:00:00Z", login="x",
                          kind="Bot", assoc="NONE") for i in range(99)]
        gh = FakeGitHub([self.ASK] + filler + [comment(2,
                                                       "2026-10-03T00:00:00Z")])
        self.assertEqual(self.run_tool(gh), 0)

    def test_a_mistyped_ask_is_refused(self):
        with self.assertRaises(SystemExit):
            import contextlib
            import io
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_tool(FakeGitHub([]), "bdoy")


if __name__ == "__main__":
    unittest.main()
