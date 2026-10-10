"""Tests for bin/forge-merger.

The eligibility rules and the order are pure functions over the plain
dictionaries `fetch_prs` builds, so most tests hand them dictionaries.
`covers_head`, `decide` and `tick` run real git against repositories
built in a temporary directory, because a fake can't say whether a
merge's tree really is the clean merge of its parents. GitHub is always
a fake: no test reaches the network or mints a token.
"""

import datetime
import fcntl
import json
import os
import subprocess
import tempfile
import unittest
from unittest import mock

from agent_forge import config
from tests.helpers import example, load_tool

m = load_tool("forge-merger")

# The gate the tests configure: a project tool, run from the checkout,
# and an agent-forge one, run from this installation.
GATE = [["tools/closing-keywords", "--pr", "{pr}", "--include-subject"],
        ["forge-commit-identity", "{base}..{head}"]]


def project(**merge):
    data = example()
    data["merge"] = dict({"gate": GATE}, **merge)
    return config.Project(data, None)


m.configure(project())

REVIEWER = "reviewerwidgetagent"
AUTHOR = "leaddevwidgetagent"
PEER = "someoneelse"
SLUG = "repos/example/widget"
T0 = "2026-10-07T10:00:00Z"
T1 = "2026-10-07T11:00:00Z"
T2 = "2026-10-07T12:00:00Z"
T3 = "2026-10-07T13:00:00Z"


def review(who, state="APPROVED", at=T1, oid="a" * 40, body=""):
    return {"author": {"login": who}, "state": state, "submittedAt": at,
            "body": body, "commit": {"oid": oid}}


def comment(who, at=T2, body="text"):
    return {"author": {"login": who}, "createdAt": at, "body": body}


def pr(number=1, created=T0, reviews=None, comments=None, body="",
       draft=False, base="main", author=AUTHOR, head="a" * 40):
    return {"number": number, "createdAt": created, "isDraft": draft,
            "baseRefName": base, "headRefName": "b%d" % number,
            "headRefOid": head, "body": body, "author": {"login": author},
            "reviews": [review(REVIEWER)] if reviews is None else reviews,
            "comments": comments or []}


class LoginAndMarkers(unittest.TestCase):

    def test_bot_suffix_is_dropped(self):
        self.assertEqual(m.login({"author": {"login": REVIEWER + "[bot]"}}),
                         REVIEWER)
        self.assertEqual(m.login({"author": {"login": REVIEWER}}), REVIEWER)

    def test_a_deleted_author_is_empty(self):
        self.assertEqual(m.login({"author": None}), "")
        self.assertEqual(m.login(None), "")

    def test_fenced_and_indented_code_is_not_a_marker(self):
        text = "a\n```\nNot-a-review\n```\n    Blocked-by: #4\nb"
        self.assertEqual(m.markers_of(text), "a\n\nb")
        self.assertEqual(m.markers_of(None), "")


class ApprovalOf(unittest.TestCase):

    def test_a_peer_approval_does_not_count(self):
        self.assertIsNone(m.approval_of(pr(reviews=[review(PEER)])))

    def test_reviewer_counts_with_or_without_suffix(self):
        for who in (REVIEWER, REVIEWER + "[bot]"):
            self.assertIsNotNone(m.approval_of(pr(reviews=[review(who)])), who)

    def test_only_approved_reviews(self):
        for state in ("COMMENTED", "CHANGES_REQUESTED", "DISMISSED"):
            self.assertIsNone(
                m.approval_of(pr(reviews=[review(REVIEWER, state)])), state)

    def test_reviewer_cannot_approve_its_own_pull_request(self):
        self.assertIsNone(m.approval_of(pr(author=REVIEWER)))

    def test_the_newest_approval_wins(self):
        old = review(REVIEWER, at=T1, oid="1" * 40)
        new = review(REVIEWER, at=T2, oid="2" * 40)
        self.assertIs(m.approval_of(pr(reviews=[new, old])), new)
        self.assertIs(m.approval_of(pr(reviews=[old, new])), new)


class SaidSince(unittest.TestCase):

    def said(self, comments=(), reviews=()):
        approval = review(REVIEWER, at=T1)
        item = pr(reviews=[approval] + list(reviews), comments=list(comments))
        return m.said_since(item, approval)

    def test_nothing_after_the_approval(self):
        self.assertEqual(self.said([comment(AUTHOR, at=T0)]), [])

    def test_the_authors_thanks_counts(self):
        # A hold carries no marker, so nothing said after an approval
        # can be read as harmless.
        self.assertEqual(self.said([comment(AUTHOR, at=T2)]),
                         [(AUTHOR, T2, "comment")])

    def test_a_comment_at_the_same_second_does_not_count(self):
        self.assertEqual(self.said([comment(AUTHOR, at=T1)]), [])

    def test_a_bare_later_approval_is_not_said(self):
        # A code owner's approval after Reviewer's is assent, not a hold.
        self.assertEqual(self.said(reviews=[review(PEER, at=T2)]), [])
        self.assertEqual(self.said(reviews=[review(PEER, at=T2, body=" \n")]),
                         [])

    def test_a_later_approval_with_text_counts(self):
        self.assertEqual(self.said(reviews=[review(PEER, at=T2,
                                                   body="nit: rename x")]),
                         [(PEER, T2, "review")])

    def test_a_later_approval_with_line_comments_counts(self):
        r = review(PEER, at=T2)
        r["comments"] = {"totalCount": 2}
        self.assertEqual(self.said(reviews=[r]), [(PEER, T2, "review")])

    def test_a_bare_later_comment_review_counts(self):
        self.assertEqual(self.said(reviews=[review(PEER, "COMMENTED", at=T2)]),
                         [(PEER, T2, "review")])

    def test_not_a_review_excuses_its_own_comment(self):
        self.assertEqual(self.said([comment(
            PEER, body="Not-a-review. A note for later.")]), [])

    def test_not_a_review_in_a_fence_excuses_nothing(self):
        body = "```\nNot-a-review\n```\nhold this"
        self.assertEqual(len(self.said([comment(PEER, body=body)])), 1)

    def test_the_mergers_own_app_is_quiet(self):
        self.assertEqual(self.said([comment("mergerwidgetagent[bot]")]), [])

    def test_a_pending_review_has_no_time_and_is_ignored(self):
        pending = review(REVIEWER, "PENDING", at=None)
        self.assertEqual(self.said(reviews=[pending]), [])

    def test_a_deleted_account_still_counts(self):
        ghost = {"author": None, "createdAt": T2, "body": "x"}
        self.assertEqual(self.said([ghost]), [("ghost", T2, "comment")])

    def test_sorted_oldest_first(self):
        got = self.said([comment(AUTHOR, at=T3), comment(PEER, at=T2)])
        self.assertEqual([s[0] for s in got], [PEER, AUTHOR])


class Eligible(unittest.TestCase):

    def test_an_approved_quiet_pull_request_is_eligible(self):
        approval, why = m.eligible(pr(), set())
        self.assertIsNotNone(approval)
        self.assertIsNone(why)

    def assertHeld(self, item, open_numbers, fragment):
        approval, why = m.eligible(item, open_numbers)
        self.assertIsNone(approval)
        self.assertIn(fragment, why)

    def test_each_reason_alone(self):
        self.assertHeld(pr(draft=True), set(), "draft")
        self.assertHeld(pr(base="x"), set(), "based on x")
        self.assertHeld(pr(reviews=[review(PEER)]), set(),
                        "no Reviewer approval")
        self.assertHeld(pr(comments=[comment(AUTHOR)]), set(),
                        "comment by %s at %s" % (AUTHOR, T2))
        self.assertHeld(pr(body="Blocked-by: #7"), {7}, "Blocked-by: #7")

    def test_a_closed_blocker_blocks_nothing(self):
        self.assertIsNotNone(m.eligible(pr(body="Blocked-by: #7"), {8})[0])

    def test_blockers_of_reads_every_line_and_not_code(self):
        body = "Blocked-by: #3\n**Blocked-by:** #4\n    Blocked-by: #5"
        self.assertEqual(m.blockers_of(pr(body=body)), {3, 4})
        self.assertEqual(m.blockers_of({"body": None}), set())


NOW = m.parse_defer("2026-10-08T00:00:00Z")


class Deferral(unittest.TestCase):

    def deferred(self, comments=(), body="", reviews=None):
        return m.deferral_of(pr(body=body, comments=list(comments),
                                reviews=reviews), NOW)

    def test_an_unexpired_deferral_holds(self):
        held = comment(REVIEWER, at=T2, body=(
            "Not-a-review. Hold this.\n\nDefer-until: 2026-10-08T06:00:00Z"))
        self.assertEqual(self.deferred([held]),
                         "deferred until 2026-10-08T06:00:00Z")
        approval, why = m.eligible(pr(comments=[held]), set(), NOW)
        self.assertIsNone(approval)
        self.assertEqual(why, "deferred until 2026-10-08T06:00:00Z")

    def test_a_passed_deferral_releases(self):
        self.assertIsNone(self.deferred(
            [comment(AUTHOR, body="Defer-until: 2026-10-07T23:59:59Z")]))

    def test_the_newest_line_wins(self):
        self.assertIsNone(self.deferred([
            comment(AUTHOR, at=T3, body="Defer-until: 2026-10-01"),
            comment(AUTHOR, at=T2, body="Defer-until: 2026-10-09")]))
        self.assertIsNotNone(self.deferred([
            comment(AUTHOR, at=T2, body="Defer-until: 2026-10-01"),
            comment(AUTHOR, at=T3, body="Defer-until: 2026-10-09")]))

    def test_a_deferral_that_is_not_a_time_holds(self):
        got = self.deferred([comment(AUTHOR, body="Defer-until: Friday")])
        self.assertEqual(got, "Defer-until: Friday is not a time")

    def test_the_body_and_reviews_count(self):
        self.assertIsNotNone(self.deferred(body="Defer-until: 2026-10-09"))
        self.assertIsNotNone(self.deferred(reviews=[review(
            REVIEWER, body="Defer-until: 2026-10-09")]))

    def test_quoted_in_a_fence_is_not_a_deferral(self):
        self.assertIsNone(self.deferred(
            [comment(AUTHOR, body="```\nDefer-until: 2026-10-09\n```")]))

    def test_formats(self):
        self.assertEqual(m.parse_defer("2026-10-08T06:00+01:00"),
                         m.parse_defer("2026-10-08T05:00:00Z"))
        self.assertIsNone(m.parse_defer("08/10/2026"))

    def test_none_at_all(self):
        self.assertIsNone(self.deferred())


class Order(unittest.TestCase):

    def test_oldest_opened_first(self):
        got = m.order([pr(2, T2), pr(1, T1), pr(3, T0)])
        self.assertEqual([p["number"] for p in got], [3, 1, 2])

    def test_a_blocker_goes_first_even_when_younger(self):
        waiting = pr(1, T0, body="Blocked-by: #2")
        got = m.order([waiting, pr(2, T3), pr(3, T1)])
        self.assertEqual([p["number"] for p in got], [2, 1, 3])

    def test_a_blocker_named_by_a_held_pull_request_goes_first(self):
        waiting = pr(1, T0, body="Blocked-by: #2")
        got = m.order([pr(2, T3), pr(3, T1)], [waiting, pr(2, T3), pr(3, T1)])
        self.assertEqual([p["number"] for p in got], [2, 3])

    def test_ties_by_number(self):
        got = m.order([pr(9, T1), pr(4, T1)])
        self.assertEqual([p["number"] for p in got], [4, 9])


def run_git(cwd, *args):
    env = dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
               GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")
    got = subprocess.run(["git", "-C", str(cwd)] + list(args), env=env,
                         capture_output=True, text=True)
    if got.returncode != 0:
        raise AssertionError("git %s: %s" % (" ".join(args), got.stderr))
    return got.stdout.strip()


class Repo:
    """A work repository with `main`, a `main` remote-tracking ref, and
    an `origin` bare repository that carries refs/pull/N/head."""

    def __init__(self, root):
        self.origin = os.path.join(root, "origin.git")
        self.path = os.path.join(root, "work")
        run_git(root, "init", "-q", "--bare", "-b", "main", self.origin)
        run_git(root, "init", "-q", "-b", "main", self.path)
        run_git(self.path, "remote", "add", "origin", self.origin)
        self.commit("base.txt", "base\n", "base")
        self.push("main")

    def commit(self, name, text, message):
        with open(os.path.join(self.path, name), "w") as handle:
            handle.write(text)
        run_git(self.path, "add", name)
        run_git(self.path, "commit", "-q", "-m", message)
        return run_git(self.path, "rev-parse", "HEAD")

    def push(self, branch):
        run_git(self.path, "push", "-q", "origin", branch)
        run_git(self.path, "fetch", "-q", "origin")

    def branch(self, name, start="main"):
        run_git(self.path, "checkout", "-q", "-B", name, start)

    def merge(self, other):
        run_git(self.path, "merge", "-q", "--no-ff", "--no-edit", other)
        return run_git(self.path, "rev-parse", "HEAD")

    def publish_pr(self, number, sha):
        run_git(self.path, "push", "-q", "-f", "origin",
                "%s:refs/pull/%d/head" % (sha, number))

    def main_tip(self):
        return run_git(self.path, "rev-parse", "refs/remotes/origin/main")


class RepoCase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(self.tmp.name)
        self.repo.branch("feature")
        self.approved = self.repo.commit("f.txt", "feature\n", "feature")

    def advance_main(self, name="m.txt", text="main moved\n"):
        self.repo.branch("main")
        self.repo.commit(name, text, "main " + name)
        self.repo.push("main")
        self.repo.branch("feature", "feature")

    def covers(self, head):
        return m.covers_head(self.repo.path, self.approved, head,
                             "refs/remotes/origin/main")


class CoversHead(RepoCase):

    def test_the_approved_head_itself(self):
        self.assertEqual(self.covers(self.approved), (True, None))

    def test_a_clean_merge_of_main(self):
        self.advance_main()
        head = self.repo.merge("origin/main")
        self.assertEqual(self.covers(head), (True, None))

    def test_two_merges_of_main(self):
        self.advance_main("m1.txt")
        self.repo.merge("origin/main")
        self.advance_main("m2.txt")
        head = self.repo.merge("origin/main")
        self.assertEqual(self.covers(head), (True, None))

    def test_a_commit_after_the_approval(self):
        head = self.repo.commit("f.txt", "changed\n", "unreviewed")
        ok, why = self.covers(head)
        self.assertFalse(ok)
        self.assertIn("is not a merge", why)

    def test_a_merge_that_changes_the_tree(self):
        self.advance_main()
        self.repo.merge("origin/main")
        with open(os.path.join(self.repo.path, "f.txt"), "w") as handle:
            handle.write("slipped in\n")
        run_git(self.repo.path, "commit", "-q", "-a", "--amend", "--no-edit")
        ok, why = self.covers(run_git(self.repo.path, "rev-parse", "HEAD"))
        self.assertFalse(ok)
        self.assertIn("not the clean merge", why)

    def test_a_merge_of_a_branch_that_is_not_main(self):
        self.repo.branch("other", "main")
        self.repo.commit("o.txt", "other\n", "other")
        run_git(self.repo.path, "checkout", "-q", "feature")
        ok, why = self.covers(self.repo.merge("other"))
        self.assertFalse(ok)
        self.assertIn("merges nothing from main", why)

    def test_a_force_push_away_from_the_approval(self):
        self.repo.branch("feature", "main")
        head = self.repo.commit("f.txt", "rewritten\n", "rewritten")
        ok, why = self.covers(head)
        self.assertFalse(ok)
        self.assertIn("not in the head's history", why)

    def test_a_failing_rev_list_says_so(self):
        def run(args, cwd=None, env=None):
            if "rev-list" in args:
                return subprocess.CompletedProcess(args, 1, "", "boom")
            return m.sh(args, cwd, env)
        self.advance_main()
        head = self.repo.merge("origin/main")
        ok, why = m.covers_head(self.repo.path, self.approved, head,
                                "refs/remotes/origin/main", run)
        self.assertFalse(ok)
        self.assertIn("rev-list failed: boom", why)


class ReviewPaths(RepoCase):
    """designs/0013 as amended from #109: where a re-review starts."""

    def write(self, name, text):
        with open(os.path.join(self.repo.path, name), "w") as handle:
            handle.write(text)

    def resolve(self, files, message="resolve"):
        """Merge origin/main into the current branch, taking `files`
        ({path: text, or None to delete}) as the resolution."""
        run_git(self.repo.path, "merge", "-q", "--no-ff", "--no-commit",
                "-X", "ours", "origin/main")
        for name, text in files.items():
            if text is None:
                run_git(self.repo.path, "rm", "-q", name)
            else:
                self.write(name, text)
                run_git(self.repo.path, "add", name)
        run_git(self.repo.path, "commit", "-q", "--no-edit", "-m", message)
        return run_git(self.repo.path, "rev-parse", "HEAD")

    def squash_into_main(self, branch, message="squash"):
        self.repo.branch("main")
        run_git(self.repo.path, "merge", "-q", "--squash", branch)
        run_git(self.repo.path, "commit", "-q", "-m", message)
        self.repo.push("main")

    def paths(self, head):
        return m.review_paths(self.repo.path, self.approved, head,
                              "refs/remotes/origin/main")

    def test_a_clean_merge_lists_nothing(self):
        self.advance_main()
        self.assertEqual(self.paths(self.repo.merge("origin/main")), [])

    def test_a_hand_merged_path(self):
        self.advance_main("f.txt", "main's version\n")
        head = self.resolve({"f.txt": "a bit of both\n"})
        self.assertEqual(self.covers(head)[0], False)
        self.assertEqual(self.paths(head), [["neither", ["f.txt"]]])

    def test_keeping_the_approved_side_of_a_path_main_changed(self):
        # Every byte is approved, but main's change to f.txt is lost.
        self.advance_main("f.txt", "main's version\n")
        head = self.resolve({"f.txt": "feature\n"})
        self.assertEqual(self.covers(head)[0], False)
        self.assertEqual(self.paths(head), [["approved", ["f.txt"]]])

    def test_the_stacked_squash_case(self):
        """A child approved on its parent; the parent changed in review
        and was squash-merged; the child takes main's version of the
        parent's file, keeps its own, and hand-merges one."""
        self.repo.branch("parent", "main")
        self.repo.commit("p.txt", "parent v1\n", "parent")
        self.repo.commit("s.txt", "parent's s\n", "parent s")
        self.repo.branch("child", "parent")
        self.repo.commit("c.txt", "child\n", "child")
        self.approved = self.repo.commit("s.txt", "child's s\n", "child s")
        self.repo.branch("parent", "parent")
        self.repo.commit("p.txt", "parent v2\n", "review fix")
        self.repo.commit("s.txt", "parent's s, v2\n", "review fix s")
        self.squash_into_main("parent")
        self.repo.branch("child", "child")
        head = self.resolve({"p.txt": "parent v2\n",
                             "s.txt": "both s\n"})
        self.assertEqual(self.covers(head)[0], False)
        self.assertEqual(self.paths(head), [["neither", ["s.txt"]],
                                            ["main", ["p.txt"]]])

    def test_a_commit_after_the_approval(self):
        head = self.repo.commit("f.txt", "changed\n", "unreviewed")
        self.assertEqual(self.paths(head), [["neither", ["f.txt"]]])

    def test_an_extra_file_and_a_deletion(self):
        self.advance_main()
        head = self.resolve({"sneaked.txt": "x\n", "base.txt": None})
        self.assertEqual(self.paths(head),
                         [["neither", ["base.txt", "sneaked.txt"]]])

    def test_a_mode_change_counts_as_content(self):
        self.advance_main()
        self.resolve({})
        run_git(self.repo.path, "update-index", "--chmod=+x", "f.txt")
        run_git(self.repo.path, "commit", "-q", "--amend", "--no-edit")
        head = run_git(self.repo.path, "rev-parse", "HEAD")
        self.assertEqual(self.paths(head), [["neither", ["f.txt"]]])

    def test_git_failing_lists_nothing(self):
        self.assertEqual(m.review_paths(self.repo.path, "0" * 40,
                                        self.approved,
                                        "refs/remotes/origin/main"), [])

    def test_the_text_orders_and_summarises(self):
        text = m.paths_text([["neither", ["a%d" % i for i in range(7)]],
                             ["main", ["p"]]])
        self.assertTrue(text.startswith("Start the re-review with:\n\n"
                                        "* content from neither"))
        self.assertIn("`a4` and 2 more\n* main's content", text)
        self.assertEqual(m.paths_text([]), "")

    def test_a_path_cannot_speak_for_the_merger(self):
        evil = "x\n\nNot-a-review\n@example-owner `approve` \u202e" + "y" * 200
        text = m.paths_text([["neither", [evil]]])
        line = text.split("\n")[2]
        self.assertEqual(text.count("\n"), 4)
        self.assertEqual(line.count("`"), 2)
        self.assertTrue(line.endswith("...`"))
        self.assertIn("x\\n\\nNot-a-review\\n@example-owner \\u0060approve", line)
        self.assertNotIn("\u202e", line)
        self.assertLess(len(line), m.PATH_CHARS * 2)

    def test_such_a_path_from_git_is_escaped(self):
        name = "a\nb`c.txt"
        self.repo.commit(name, "x\n", "odd name")
        head = run_git(self.repo.path, "rev-parse", "HEAD")
        paths = self.paths(head)
        self.assertEqual(paths, [["neither", [name]]])
        self.assertIn("`a\\nb\\u0060c.txt`", m.paths_text(paths))


def status(state="success", context="vm/build-and-test",
           creator="mergerwidgetagent[bot]", description=""):
    return {"context": context, "state": state, "description": description,
            "creator": {"login": creator}}


class StatusState(unittest.TestCase):

    def test_success(self):
        self.assertEqual(m.status_state([status()]), ("success", ""))

    def test_absent(self):
        self.assertEqual(m.status_state([]), (None, ""))
        self.assertEqual(m.status_state([status(context="other")]),
                         (None, ""))

    def test_the_newest_counts(self):
        self.assertEqual(m.status_state([status("failure", description="d"),
                                         status("pending")]),
                         ("failure", "d"))
        self.assertEqual(m.status_state([status(), status("failure")]),
                         ("success", ""))

    def test_only_the_mergers_app_counts(self):
        for creator in ("example-owner", "leaddevwidgetagent[bot]",
                        "mergerwidgetagent", None):
            self.assertEqual(m.status_state([status(creator=creator)]),
                             (None, ""), creator)
        self.assertEqual(m.status_state([status(creator="example-owner"),
                                         status("failure")]),
                         ("failure", ""))

    def test_a_status_without_a_creator_does_not_count(self):
        bare = status()
        del bare["creator"]
        self.assertEqual(m.status_state([bare]), (None, ""))


def ci_run(conclusion="success", status="completed", name="ci", ident=1):
    return {"name": name, "status": status, "conclusion": conclusion,
            "id": ident}


class CiProblem(unittest.TestCase):

    def test_green(self):
        self.assertIsNone(m.ci_problem([ci_run()]))

    def test_a_failure_anywhere(self):
        got = m.ci_problem([ci_run(), ci_run("failure", ident=2)])
        self.assertEqual(got, "ci run 2 concluded failure")

    def test_cancelled_is_excused_by_a_green_sibling_only(self):
        self.assertIsNone(m.ci_problem([ci_run("cancelled"), ci_run()]))
        self.assertIn("concluded cancelled",
                      m.ci_problem([ci_run("cancelled")]))

    def test_unfinished(self):
        self.assertEqual(m.ci_problem([ci_run(None, "in_progress")]),
                         "ci has not finished on the head")

    def test_no_run_at_all_has_not_started(self):
        for runs in ([], [ci_run(name="other")]):
            self.assertEqual(m.ci_problem(runs),
                             "ci has not started on the head")

    def test_only_skipped_runs_are_not_a_success(self):
        self.assertEqual(m.ci_problem([ci_run("skipped")]),
                         "ci has no successful run on the head")

    def test_other_workflows_are_ignored(self):
        self.assertIsNone(m.ci_problem([ci_run(), ci_run("failure",
                                                         name="other")]))


class ToolProblem(unittest.TestCase):

    def test_exit_zero_is_none(self):
        run = lambda args, cwd=None, env=None: subprocess.CompletedProcess(
            args, 0, "fine", "")
        self.assertIsNone(m.tool_problem(["/x/forge-tool"], {}, "/r", run))

    def test_a_refusal_keeps_its_last_lines(self):
        out = "\n".join("line %d" % i for i in range(10))
        run = lambda args, cwd=None, env=None: subprocess.CompletedProcess(
            args, 1, out, "the end")
        got = m.tool_problem(["/x/forge-tool"], {}, "/r", run)
        self.assertTrue(got.startswith("forge-tool exited 1: line 5"))
        self.assertTrue(got.endswith("the end"))


class FakeGitHub:
    """Answers `api` from a table of (argument prefix -> value), and
    records every write. `refuse` is a reason to refuse every write, or
    a function of (method, path) giving one or None."""

    def __init__(self, answers, refuse=None):
        self.answers = answers
        self.env = {"GH_TOKEN": "fake"}
        self.calls = []
        self.writes = []
        self.refuse = refuse

    def write(self, method, path, **fields):
        self.writes.append((method, path, fields))
        refuse = (self.refuse(method, path) if callable(self.refuse)
                  else self.refuse)
        return (False, refuse) if refuse else (True, "")

    def create(self, path, **fields):
        """As `write`, answering with the next issue number from 900."""
        ok, why = self.write("POST", path, **fields)
        self.created = getattr(self, "created", 899) + 1
        return (self.created, "") if ok else (None, why)

    def api(self, *args):
        self.calls.append(args)
        for prefix, value in self.answers.items():
            if args[0].startswith(prefix) or (
                    len(args) > 1 and args[1].startswith(prefix)):
                return value() if callable(value) else value
        raise AssertionError("unexpected gh api %r" % (args,))


def tools_run(results=None):
    """`run` that answers the two gate tools from `results` and passes
    git through."""
    results = results or {}

    def run(args, cwd=None, env=None):
        name = os.path.basename(args[0])
        if name in ("closing-keywords", "forge-commit-identity"):
            rc, text = results.get(name, (0, ""))
            return subprocess.CompletedProcess(args, rc, text, "")
        return m.sh(args, cwd, env)
    return run


def answers(conclusions=("success",), runs=None, prs=None, issues=()):
    table = {
        SLUG + "/commits/": [status(c or "pending") for c in conclusions],
        SLUG + "/actions/runs": {
            "workflow_runs": [ci_run()] if runs is None else runs}}
    if prs is not None:
        table["graphql"] = graphql_page(prs)
        table["--paginate"] = list(issues)
    return table


class Decide(RepoCase):

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)

    def item(self, head=None, approved=None):
        return pr(5, head=head or self.head,
                  reviews=[review(REVIEWER, oid=approved or self.approved)])

    def decide(self, conclusions=("success",), runs=None, results=None,
               item=None, main_tip=None):
        item = item or self.item()
        self.github = FakeGitHub(answers(conclusions, runs))
        return m.decide(item, m.approval_of(item), self.repo.path,
                        main_tip or self.repo.main_tip(), self.github, {},
                        tools_run(results))

    def test_merge_when_every_check_passes(self):
        self.assertEqual(self.decide(), {"action": "MERGE"})

    def test_it_reads_the_heads_commit_statuses(self):
        self.decide()
        self.assertEqual(self.github.calls[0], (
            "%s/commits/%s/statuses?per_page=100" % (SLUG, self.head),))

    def test_update_when_behind_main(self):
        self.advance_main("m3.txt")
        self.assertEqual(self.decide(main_tip=self.repo.main_tip()),
                         {"action": "UPDATE"})

    def test_a_conflict_with_main_goes_to_the_author(self):
        self.advance_main("f.txt", "main's own line\n")
        got = self.decide(main_tip=self.repo.main_tip())
        self.assertEqual(got, {"action": "HAND-BACK", "to": "author",
                               "reason": m.CONFLICT_REASON})
        self.assertEqual(self.github.calls, [])

    def test_a_merge_tree_git_cannot_run_leaves_it_to_github(self):
        self.advance_main("f.txt", "main's own line\n")
        passthrough = tools_run()

        def run(args, cwd=None, env=None):
            if "merge-tree" in args and "--no-messages" in args:
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            return passthrough(args, cwd, env)
        item = self.item()
        got = m.decide(item, m.approval_of(item), self.repo.path,
                       self.repo.main_tip(), FakeGitHub({}), {}, run)
        self.assertEqual(got, {"action": "UPDATE"})

    def test_wait_for_vm_build_and_test(self):
        self.assertEqual(self.decide(conclusions=()),
                         {"action": "WAIT",
                          "reason": "vm/build-and-test is absent"})
        self.assertEqual(self.decide(conclusions=(None,)),
                         {"action": "WAIT",
                          "reason": "vm/build-and-test is pending"})

    def test_a_red_check_goes_to_the_author(self):
        got = self.decide(conclusions=("failure",))
        self.assertEqual(got, {"action": "HAND-BACK", "to": "author",
                               "reason": "vm/build-and-test is failure"})

    def test_a_hand_back_quotes_the_statuss_description(self):
        item = self.item()
        github = FakeGitHub(dict(answers(), **{SLUG + "/commits/": [status(
            "failure", description="red on build.example.org: vet failed")]}))
        got = m.decide(item, m.approval_of(item), self.repo.path,
                       self.repo.main_tip(), github, {}, tools_run())
        self.assertEqual(got["reason"], "vm/build-and-test is failure: red "
                         "on build.example.org: vet failed")

    def test_a_green_status_from_anyone_else_is_a_wait(self):
        item = self.item()
        github = FakeGitHub(dict(answers(), **{
            SLUG + "/commits/": [status(creator="example-owner")]}))
        got = m.decide(item, m.approval_of(item), self.repo.path,
                       self.repo.main_tip(), github, {}, tools_run())
        self.assertEqual(got, {"action": "WAIT",
                               "reason": "vm/build-and-test is absent"})

    def test_no_ci_run_yet_is_a_wait(self):
        got = self.decide(runs=[])
        self.assertEqual(got["action"], "WAIT")

    def test_an_unfinished_ci_run_is_a_wait(self):
        got = self.decide(runs=[ci_run(None, "queued")])
        self.assertEqual(got["action"], "WAIT")

    def test_a_red_ci_run_goes_to_the_author(self):
        got = self.decide(runs=[ci_run("failure")])
        self.assertEqual((got["action"], got["to"]), ("HAND-BACK", "author"))

    def test_each_tool_refusal_goes_to_the_author(self):
        for name in ("closing-keywords", "forge-commit-identity"):
            got = self.decide(results={name: (1, "refused here")})
            self.assertEqual(got["to"], "author", name)
            self.assertIn("%s exited 1: refused here" % name, got["reason"])

    def test_the_tools_are_given_the_pull_request_and_range(self):
        seen = []
        base = tools_run()

        def run(args, cwd=None, env=None):
            name = os.path.basename(args[0])
            if name in ("closing-keywords", "forge-commit-identity"):
                self.assertEqual(cwd, self.repo.path)
                self.assertEqual(os.path.dirname(args[0]), m.BIN
                                 if name.startswith("forge-") else
                                 os.path.join(self.repo.path, "tools"))
                seen.append([name] + args[1:])
            return base(args, cwd, env)
        item = self.item()
        m.decide(item, m.approval_of(item), self.repo.path,
                 self.repo.main_tip(), FakeGitHub(answers()), {}, run)
        self.assertEqual(seen, [
            ["closing-keywords", "--pr", "5", "--include-subject"],
            ["forge-commit-identity",
             "%s..%s" % (self.repo.main_tip(), self.head)]])

    def test_an_unreviewed_commit_goes_to_the_approver(self):
        head = self.repo.commit("f.txt", "later\n", "unreviewed")
        self.repo.publish_pr(5, head)
        got = self.decide(item=self.item(head=head))
        self.assertEqual((got["action"], got["to"]), ("HAND-BACK", "Reviewer"))
        self.assertEqual(self.github.calls, [])

    def test_an_approval_without_a_commit_goes_to_the_approver(self):
        item = self.item()
        item["reviews"][0]["commit"] = None
        self.assertEqual(self.decide(item=item)["reason"],
                         "the approval names no commit")

    def test_a_head_that_moved_is_skipped(self):
        self.assertEqual(self.decide(item=self.item(head="f" * 40)),
                         {"action": "SKIP",
                          "reason": "the head moved while reading"})

    def test_a_failed_fetch_is_skipped(self):
        item = pr(6, reviews=[review(REVIEWER, oid=self.approved)])
        got = m.decide(item, m.approval_of(item), self.repo.path,
                       self.repo.main_tip(), FakeGitHub({}), {}, tools_run())
        self.assertEqual(got["action"], "SKIP")
        self.assertIn("fetch failed", got["reason"])


def graphql_page(nodes, more=False, cursor="c1"):
    nodes = [dict(n, reviews={"nodes": n["reviews"]},
                  comments={"nodes": n["comments"]}) for n in nodes]
    return {"data": {"repository": {"pullRequests": {
        "pageInfo": {"hasNextPage": more, "endCursor": cursor},
        "nodes": nodes}}}}


class FetchPrs(unittest.TestCase):

    def test_pages_are_followed_and_flattened(self):
        pages = [graphql_page([pr(1)], more=True), graphql_page([pr(2)])]
        github = FakeGitHub({"graphql": lambda: pages.pop(0)})
        got = m.fetch_prs(github)
        self.assertEqual([p["number"] for p in got], [1, 2])
        self.assertEqual(got[0]["reviews"][0]["state"], "APPROVED")
        self.assertIn("cursor=c1", github.calls[1])
        self.assertIn("owner=example", github.calls[0])
        self.assertIn("name=widget", github.calls[0])

    def test_open_issues(self):
        self.assertEqual(m.fetch_open_issues(FakeGitHub({"--paginate":
                                                         [4, 9]})), {4, 9})
        self.assertEqual(m.fetch_open_issues(FakeGitHub({"--paginate": None})),
                         set())


class GitHubApi(unittest.TestCase):

    def test_passes_the_token_and_parses_json(self):
        seen = {}

        def run(args, cwd=None, env=None):
            seen.update(args=args, token=env["GH_TOKEN"])
            return subprocess.CompletedProcess(args, 0, '{"a": 1}', "")
        self.assertEqual(m.GitHub("tok", run).api("x"), {"a": 1})
        self.assertEqual(seen, {"args": ["gh", "api", "x"], "token": "tok"})

    def answer(self, text):
        run = lambda args, cwd=None, env=None: subprocess.CompletedProcess(
            args, 0, text, "")
        return m.GitHub("tok", run).api("--paginate", "x")

    def test_one_array_per_page_is_joined(self):
        self.assertEqual(self.answer("[4,5]\n[3,2]\n"), [4, 5, 3, 2])

    def test_empty_output_is_none(self):
        self.assertIsNone(self.answer(""))

    def test_pages_that_are_not_arrays_are_refused(self):
        with self.assertRaisesRegex(ValueError, "not all arrays"):
            self.answer('[1]\n{"a": 2}')

    def test_a_failure_raises(self):
        run = lambda args, cwd=None, env=None: subprocess.CompletedProcess(
            args, 1, "", "nope")
        with self.assertRaisesRegex(RuntimeError, "gh api x: nope"):
            m.GitHub("tok", run).api("x")

    def test_a_refused_write_is_returned_not_raised(self):
        run = lambda args, cwd=None, env=None: subprocess.CompletedProcess(
            args, 1, "", "HTTP 405")
        self.assertEqual(m.GitHub("tok", run).write("PUT", "p", a="b"),
                         (False, "HTTP 405"))


class Tick(Decide):

    def tick(self, prs, issues=(), conclusions=("success",), live=False,
             refuse=None, minutes=0, send=None):
        github = FakeGitHub(answers(conclusions, prs=prs, issues=issues),
                            refuse)
        self.github = github
        lines = []
        state = self.state = os.path.join(self.tmp.name, "state")
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        record = m.tick(self.repo.path, state, github, tools_run(),
                        lines.append,
                        now=lambda: start + datetime.timedelta(minutes=minutes),
                        live=live, send=send)
        name = "merger.jsonl" if live else "dry-run.jsonl"
        with open(os.path.join(state, name)) as handle:
            written = [json.loads(l) for l in handle]
        self.assertEqual(written[-1], record)
        if not live:
            self.assertEqual(github.writes, [])
        return record, lines

    def other_pr(self, number=6):
        """An approved pull request on its own branch, clean against
        main, opened after #5."""
        self.repo.branch("other", "origin/main")
        sha = self.repo.commit("o.txt", "other\n", "other")
        self.repo.publish_pr(number, sha)
        self.repo.branch("feature", "feature")
        return pr(number, T1, head=sha, reviews=[review(REVIEWER, oid=sha)])

    def unreviewed(self, number=4):
        bad = self.repo.commit("f.txt", "later\n", "unreviewed")
        self.repo.publish_pr(number, bad)
        return pr(number, "2026-10-07T09:00:00Z", head=bad,
                  reviews=[review(REVIEWER, oid=self.approved)])

    # The conflict path: handed back exactly once, and the queue moves on.

    def test_a_conflict_is_handed_back_once_and_the_queue_moves_on(self):
        self.advance_main("f.txt", "main's own line\n")
        other = self.other_pr()
        record, lines = self.tick([self.item(), other], live=True)
        self.assertEqual([(d["pr"], d["action"], d["result"])
                          for d in record["decisions"]],
                         [(5, "HAND-BACK", "done"), (6, "MERGE", "done")])
        # No update-branch was ever asked for the conflicted head.
        self.assertEqual([w[1] for w in self.github.writes],
                         [SLUG + "/issues/5/comments", SLUG + "/pulls/6/merge"])
        self.assertIn("conflicts with main", self.github.writes[0][2]["body"])
        self.assertTrue(self.github.writes[0][2]["body"].endswith(
            "\n\n— Merger"))
        # Later ticks say nothing more to #5 and go straight on.
        for minutes in (1, 2, 40):
            record, _ = self.tick([self.item(), other], live=True,
                                  minutes=minutes)
            self.assertEqual(record["decisions"][0]["result"], "already said")
            self.assertEqual(record["decisions"][1]["pr"], 6)
            self.assertNotIn("stalled", record)
            self.assertEqual([w[1] for w in self.github.writes],
                             [SLUG + "/pulls/6/merge"])

    def test_a_conflicted_update_branch_is_handed_back_once(self):
        # The backstop: git saw no conflict, GitHub refused update-branch
        # as one. The hand-back follows in the same tick, once, and the
        # queue moves on rather than retrying the update forever.
        self.advance_main("n.txt")
        other = self.other_pr()

        def refuse(method, path):
            if path.endswith("/update-branch"):
                return ('{"message":"merge conflict between base and head",'
                        '"status":"422"}')
            return None
        record, _ = self.tick([self.item(), other], live=True, refuse=refuse)
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(5, "UPDATE"), (5, "HAND-BACK"), (6, "MERGE")])
        self.assertTrue(record["decisions"][0]["result"].startswith(
            "refused"))
        self.assertEqual(record["decisions"][1]["to"], "author")
        self.assertEqual(record["decisions"][1]["result"], "done")
        comments = [w for w in self.github.writes if w[0] == "POST"]
        self.assertEqual(len(comments), 1)
        self.assertEqual(comments[0][1], SLUG + "/issues/5/comments")

        # The next tick neither asks GitHub to update #5 again nor says
        # anything more; it goes straight on to #6.
        record, _ = self.tick([self.item(), other], live=True, refuse=refuse,
                              minutes=1)
        self.assertEqual([(d["pr"], d["action"], d["result"])
                          for d in record["decisions"]],
                         [(5, "HAND-BACK", "already said"),
                          (6, "MERGE", "done")])
        self.assertEqual([w[1] for w in self.github.writes],
                         [SLUG + "/pulls/6/merge"])

    def test_a_new_approval_after_a_conflicted_update_tries_again(self):
        self.advance_main("n.txt")
        refuse = lambda method, path: (
            "merge conflict" if path.endswith("/update-branch") else None)
        self.tick([self.item()], live=True, refuse=refuse)
        again = self.item()
        again["reviews"].append(review(REVIEWER, at=T3, oid=self.approved))
        record, _ = self.tick([again], live=True)
        self.assertEqual(record["decisions"][0]["action"], "UPDATE")
        self.assertEqual(record["decisions"][0]["result"], "done")

    def test_a_dry_run_hands_back_a_conflict_without_writing(self):
        self.advance_main("f.txt", "main's own line\n")
        record, lines = self.tick([self.item(), self.other_pr()])
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(5, "HAND-BACK"), (6, "MERGE")])
        self.assertEqual(lines, ["DRY-RUN 2 eligible, 0 approved but held; "
                                 "#5 HAND-BACK to author; #6 MERGE"])

    def test_any_other_refused_update_ends_the_tick(self):
        self.advance_main("n.txt")
        record, _ = self.tick([self.item(), self.other_pr()], live=True,
                              refuse="HTTP 502: bad gateway")
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(5, "UPDATE")])

    # Writes.

    def test_live_merges_the_judged_head_by_squash(self):
        record, lines = self.tick([self.item()], live=True)
        self.assertEqual(self.github.writes, [
            ("PUT", SLUG + "/pulls/5/merge",
             {"sha": self.head, "merge_method": "squash"})])
        self.assertEqual(record["decisions"], [
            {"action": "MERGE", "pr": 5, "head": self.head,
             "result": "done"}])
        self.assertEqual(lines, ["TICK 1 eligible, 0 approved but held; "
                                 "#5 MERGE (done)"])

    def test_live_updates_pinned_to_the_judged_head(self):
        self.advance_main("n.txt")
        record, _ = self.tick([self.item()], live=True)
        self.assertEqual(self.github.writes, [
            ("PUT", SLUG + "/pulls/5/update-branch",
             {"expected_head_sha": self.head})])
        self.assertEqual(record["decisions"][0]["result"], "done")

    def test_live_waits_without_writing(self):
        record, _ = self.tick([self.item()], conclusions=(), live=True)
        self.assertEqual(self.github.writes, [])
        self.assertNotIn("result", record["decisions"][0])

    def test_a_refused_merge_is_recorded_and_the_tick_ends(self):
        record, lines = self.tick([self.item(), self.other_pr()], live=True,
                                  refuse="HTTP 405: not mergeable")
        self.assertEqual(len(record["decisions"]), 1)
        self.assertEqual(record["decisions"][0]["result"],
                         "refused: HTTP 405: not mergeable")

    # Hand-backs.

    def test_a_hand_back_to_the_approver_is_not_a_review(self):
        older = self.unreviewed()
        record, _ = self.tick([older], live=True)
        (method, path, fields), = self.github.writes
        self.assertEqual((method, path), ("POST", SLUG + "/issues/4/comments"))
        body = fields["body"]
        self.assertTrue(body.startswith("Not-a-review\n"))
        self.assertIn(older["headRefOid"][:10], body)
        self.assertIn("needs Reviewer's approval", body)
        self.assertIn("Start the re-review with:\n\n* content from "
                      "neither the approved head nor main: `f.txt`\n\n"
                      "— Merger", body)
        self.assertTrue(body.endswith("\n\n— Merger"))
        self.assertEqual(record["decisions"][0]["paths"],
                         [["neither", ["f.txt"]]])
        self.assertEqual(record["decisions"][0]["result"], "done")

    def test_a_hand_back_to_the_author_is_plain_and_signed(self):
        self.tick([self.item()], conclusions=("failure",), live=True)
        body = self.github.writes[0][2]["body"]
        self.assertTrue(body.startswith("Not merging %s: vm/build-and-test "
                                        "is failure." % self.head[:10]))
        self.assertNotIn("Not-a-review", body)
        self.assertTrue(body.endswith("\n\n— Merger"))

    def test_a_hand_back_is_said_once_per_head_and_reason(self):
        older = self.unreviewed()
        self.tick([older], live=True)
        record, _ = self.tick([older], live=True)
        self.assertEqual(self.github.writes, [])
        self.assertEqual(record["decisions"][0]["result"], "already said")

    def test_a_new_reason_on_the_same_head_is_said_again(self):
        self.tick([self.item()], conclusions=("failure",), live=True)
        self.tick([self.item()], conclusions=("error",), live=True)
        self.assertEqual(len(self.github.writes), 1)
        self.assertIn("is error", self.github.writes[0][2]["body"])

    def test_a_refused_hand_back_is_tried_again(self):
        older = self.unreviewed()
        self.tick([older], live=True, refuse="HTTP 502")
        self.tick([older], live=True)
        self.assertEqual(len(self.github.writes), 1)

    def test_a_new_approval_of_the_same_head_is_answered_again(self):
        older = self.unreviewed()
        self.tick([older], live=True)
        again = dict(older, reviews=older["reviews"] + [
            review(REVIEWER, at=T3, oid=self.approved)])
        record, _ = self.tick([again], live=True)
        self.assertEqual(record["decisions"][0]["result"], "done")
        record, _ = self.tick([again], live=True)
        self.assertEqual(record["decisions"][0]["result"], "already said")

    def test_a_skip_writes_nothing(self):
        self.assertIsNone(m.carry_out(
            {"action": "SKIP", "pr": 1, "head": "a"}, review(REVIEWER),
            FakeGitHub({}), {}))

    def test_live_hands_back_then_waits_on_the_next(self):
        record, lines = self.tick([self.item(), self.unreviewed()],
                                  conclusions=(), live=True)
        self.assertEqual(lines, ["TICK 2 eligible, 0 approved but held; "
                                 "#4 HAND-BACK to Reviewer (done); #5 WAIT"])

    # The queue.

    def test_a_blocker_of_a_held_pull_request_is_queued_first(self):
        younger = self.other_pr()
        held = pr(7, T0, body="Blocked-by: #6")
        record, _ = self.tick([self.item(), younger, held])
        self.assertEqual(record["queue"], [6, 5])
        self.assertIn("7", record["held"])

    def test_records_the_queue_the_action_and_the_held(self):
        held = pr(7, T0, comments=[comment(AUTHOR)])
        record, lines = self.tick([self.item(), held, pr(8, T0, reviews=[])])
        self.assertEqual(record["queue"], [5])
        self.assertEqual(record["held"], {"7": "comment by %s at %s, after "
                                          "the approval" % (AUTHOR, T2)})
        self.assertEqual(record["decisions"], [
            {"action": "MERGE", "pr": 5, "head": self.head}])
        self.assertEqual(record["at"], "2026-10-07T12:00:00Z")
        self.assertEqual(record["main"], self.repo.main_tip())
        self.assertEqual(lines, ["DRY-RUN 1 eligible, 1 approved but held; "
                                 "#5 MERGE"])

    def test_an_open_issue_named_as_a_blocker_holds(self):
        record, _ = self.tick([pr(5, body="Blocked-by: #40")], issues=[40])
        self.assertEqual(record["held"], {"5": "Blocked-by: #40"})

    def test_the_ticks_clock_decides_a_deferral(self):
        item = self.item()
        item["comments"] = [comment(REVIEWER, at=T2, body=(
            "Not-a-review\nDefer-until: 2026-10-07T13:00Z"))]
        record, _ = self.tick([item])
        self.assertEqual(record["held"],
                         {"5": "deferred until 2026-10-07T13:00:00Z"})

    def test_nothing_to_do(self):
        record, lines = self.tick([])
        self.assertEqual(record["decisions"], [])
        self.assertEqual(lines, ["DRY-RUN 0 eligible, 0 approved but held; "
                                 "nothing to do"])

    # Stalls.

    def waits(self, minutes, item=None, live=True):
        return self.tick([item or self.item()], conclusions=(None,),
                         live=live, minutes=minutes)

    def test_a_wait_under_the_limit_is_not_stalled(self):
        self.waits(0)
        record, lines = self.waits(29)
        self.assertNotIn("stalled", record)
        self.assertEqual(len(lines), 1)

    def test_a_wait_of_30_minutes_on_one_head_is_stalled(self):
        self.waits(0)
        record, lines = self.waits(30)
        self.assertEqual(record["stalled"],
                         {"pr": 5, "head": self.head, "minutes": 30})
        self.assertEqual(self.github.writes, [])
        self.assertEqual(lines[1], (
            "STALLED: #5 has waited 30 minutes on %s: vm/build-and-test is "
            "pending. It stays first in the queue and is not handed back; "
            "look at the build check on build.example.org, or the ci workflow "
            "in GitHub Actions" % self.head[:10]))

    def test_a_new_head_starts_the_wait_again(self):
        self.waits(0)
        newer = self.repo.commit("g.txt", "newer\n", "newer")
        self.repo.publish_pr(5, newer)
        item = self.item(head=newer, approved=newer)
        record, _ = self.waits(31, item=item)
        self.assertNotIn("stalled", record)
        record, _ = self.waits(61, item=item)
        self.assertEqual(record["stalled"]["minutes"], 30)

    def test_a_tick_that_waits_on_nothing_forgets_the_wait(self):
        self.waits(0)
        self.tick([], live=True, minutes=10)
        self.assertFalse(os.path.exists(
            os.path.join(self.state, "waiting.json")))
        record, _ = self.waits(31)
        self.assertNotIn("stalled", record)

    def test_an_update_refused_for_30_minutes_is_stalled(self):
        self.advance_main("n.txt")
        self.tick([self.item()], live=True, refuse="HTTP 403: forbidden")
        record, lines = self.tick([self.item()], live=True,
                                  refuse="HTTP 403: forbidden", minutes=30)
        self.assertEqual(record["stalled"], {
            "pr": 5, "head": self.head, "minutes": 30,
            "refused": "UPDATE refused: HTTP 403: forbidden"})
        self.assertIn("look at the Merger App's access", lines[1])

    def test_an_update_is_not_a_wait(self):
        self.advance_main("n.txt")
        self.tick([self.item()], live=True)
        record, _ = self.tick([self.item()], live=True, minutes=31)
        self.assertEqual(record["decisions"][0]["action"], "UPDATE")
        self.assertNotIn("stalled", record)

    def test_a_dry_run_keeps_no_wait(self):
        self.waits(0, live=False)
        record, _ = self.waits(31, live=False)
        self.assertNotIn("stalled", record)
        self.assertFalse(os.path.exists(
            os.path.join(self.state, "waiting.json")))

    # What the build check reads: the pull request the tick stopped at.

    def next_json(self):
        with open(os.path.join(self.state, "next.json")) as handle:
            return json.load(handle)

    def test_a_live_tick_names_the_pull_request_it_waits_on(self):
        self.waits(0)
        self.assertEqual(self.next_json(),
                         {"at": "2026-10-07T12:00:00Z", "pr": 5})

    def test_an_update_names_its_pull_request_too(self):
        # Its new head is the one the next merge waits on.
        self.advance_main("n.txt")
        self.tick([self.item()], live=True)
        self.assertEqual(self.next_json()["pr"], 5)

    def test_a_pull_request_handed_back_is_not_named(self):
        self.advance_main("f.txt", "main's own line\n")
        other = self.other_pr()
        self.tick([self.item(), other], live=True)
        self.assertEqual(self.next_json()["pr"], 6)

    def test_a_tick_with_nothing_to_do_names_nothing(self):
        self.waits(0)
        self.tick([], live=True, minutes=1)
        self.assertEqual(self.next_json(),
                         {"at": "2026-10-07T12:01:00Z", "pr": None})

    def test_a_dry_run_names_nothing(self):
        self.waits(0, live=False)
        self.assertFalse(os.path.exists(
            os.path.join(self.state, "next.json")))

    # Fetching main.

    def test_a_lost_race_for_the_main_ref_is_retried_once(self):
        fetches, pauses = [], []

        def run(args, cwd=None, env=None):
            if args[3:6] == ["fetch", "-q", "origin"] and args[6] == "main":
                fetches.append(args)
                if len(fetches) == 1:
                    return subprocess.CompletedProcess(
                        args, 1, "", "error: cannot lock ref 'x'")
            return tools_run()(args, cwd, env)
        github = FakeGitHub({"graphql": graphql_page([]), "--paginate": []})
        m.tick(self.repo.path, self.tmp.name, github, run, lambda line: None,
               sleep=pauses.append)
        self.assertEqual((len(fetches), pauses), (2, [m.FETCH_RETRY_S]))

    def test_a_failed_fetch_of_main_raises(self):
        run_git(self.repo.path, "remote", "set-url", "origin", "/nonexistent")
        with self.assertRaisesRegex(RuntimeError, "fetch main"):
            m.tick(self.repo.path, self.tmp.name, FakeGitHub({}), tools_run(),
                   lambda line: None, sleep=lambda s: None)


class StallAlert(Decide):
    """`merge.stall_alert`: a stall opens its own issue and tells
    people, through a `send` the tests fake, never the relay."""

    ALERT = {"tell": ["LeadDev", "Reviewer"], "retell_minutes": 60,
             "owner_after_minutes": 120,
             "title": "forge-merger: the queue is stalled on #{pr}",
             "labels": ["bug", "Owner:LeadDev"]}
    OWNER_LABEL = SLUG + "/issues/900/labels/Status%3Awaiting-owner"

    tick = Tick.tick

    def setUp(self):
        super().setUp()
        self.addCleanup(m.configure, project())
        self.alert()

    def alert(self, **changes):
        m.configure(project(stall_alert=dict(self.ALERT, **changes)))

    def relay(self, confirm=True):
        """(sent, send): `sent` lists each (agent, text) sent, and every
        one is taken, or none when not `confirm`."""
        sent = []

        def send(name, text):
            sent.append((name, text))
            return None if confirm else "HTTP 503 queue-full"
        return sent, send

    def waits(self, minutes, item=None, live=True, send=None):
        return self.tick([item or self.item()], conclusions=(None,),
                         live=live, minutes=minutes, send=send)

    def stall_writes(self):
        return [w for w in self.github.writes if "/pulls/" not in w[1]]

    def saved(self):
        path = os.path.join(self.state, "stall-alert.json")
        if not os.path.exists(path):
            return None
        with open(path) as handle:
            return json.load(handle)

    def test_a_stall_opens_one_issue_and_tells_each_agent(self):
        sent, send = self.relay()
        self.waits(0, send=send)
        self.assertEqual((self.stall_writes(), sent), ([], []))
        _, lines = self.waits(30, send=send)
        created, *labels = self.stall_writes()
        self.assertEqual(created[:2], ("POST", SLUG + "/issues"))
        self.assertEqual(created[2]["title"],
                         "forge-merger: the queue is stalled on #5")
        body = created[2]["body"]
        self.assertIn("stalled on #5 at `%s`" % self.head[:10], body)
        self.assertIn("vm/build-and-test is pending", body)
        self.assertIn("build check on build.example.org", body)
        self.assertTrue(body.endswith("\n\n— Merger"))
        self.assertEqual([(w[1], w[2]) for w in labels],
                         [(SLUG + "/issues/900/labels", {"labels[]": "bug"}),
                          (SLUG + "/issues/900/labels",
                           {"labels[]": "Owner:LeadDev"})])
        self.assertEqual([name for name, _ in sent], ["LeadDev", "Reviewer"])
        text = sent[0][1]
        self.assertTrue(text.startswith("From: Merger\n"))
        self.assertIn("Issue: #900.", text)
        self.assertTrue(text.endswith("— Merger"))
        self.assertIn("STALL-ISSUE: opened #900", lines)
        self.assertIn("STALL-TOLD: LeadDev,Reviewer", lines)

    def test_the_next_tick_opens_nothing_and_says_nothing(self):
        sent, send = self.relay()
        self.waits(0, send=send)
        self.waits(30, send=send)
        self.waits(31, send=send)
        self.assertEqual(self.stall_writes(), [])
        self.assertEqual(len(sent), 2)

    def test_it_is_said_again_every_retell_minutes(self):
        sent, send = self.relay()
        self.waits(0, send=send)
        self.waits(30, send=send)
        self.waits(89, send=send)
        self.assertEqual(len(sent), 2)
        self.waits(90, send=send)
        self.assertEqual(len(sent), 4)

    def test_a_message_nobody_took_is_tried_on_the_next_tick(self):
        sent, send = self.relay(confirm=False)
        self.waits(0, send=send)
        _, lines = self.waits(30, send=send)
        self.assertIn("STALL-TOLD: nobody; LeadDev: HTTP 503 queue-full; "
                      "Reviewer: HTTP 503 queue-full", lines)
        self.waits(31, send=send)
        self.assertEqual(len(sent), 4)

    def test_owner_after_minutes_puts_it_on_the_owners_list_once(self):
        _, send = self.relay()
        self.waits(0, send=send)
        self.waits(30, send=send)
        self.waits(119, send=send)
        self.assertEqual(self.stall_writes(), [])
        _, lines = self.waits(120, send=send)
        (labeled,) = self.stall_writes()
        self.assertEqual((labeled[1], labeled[2]),
                         (SLUG + "/issues/900/labels",
                          {"labels[]": "Status:waiting-owner"}))
        self.assertIn("STALL-TO-OWNER: labeled #900 Status:waiting-owner",
                      lines)
        self.waits(121, send=send)
        self.assertEqual(self.stall_writes(), [])

    def test_the_owners_label_is_the_configured_one(self):
        data = example()
        data["merge"] = {"gate": GATE, "stall_alert": dict(
            self.ALERT, owner_after_minutes=30)}
        data["labels"]["waiting_on_owner"] = "Blocked on owner"
        m.configure(config.Project(data, None))
        self.waits(0)
        self.waits(30)
        self.assertIn((SLUG + "/issues/900/labels",
                       {"labels[]": "Blocked on owner"}),
                      [(w[1], w[2]) for w in self.stall_writes()])

    def test_the_end_of_a_stall_closes_its_issue_and_takes_the_label_off(self):
        _, send = self.relay()
        self.waits(0, send=send)
        self.waits(30, send=send)
        self.waits(120, send=send)
        _, lines = self.tick([self.item()], live=True, minutes=121,
                             send=send)
        said, unlabeled, closed = self.stall_writes()
        self.assertEqual(said[1], SLUG + "/issues/900/comments")
        self.assertIn("this tick's MERGE of #5", said[2]["body"])
        self.assertTrue(said[2]["body"].endswith("— Merger"))
        self.assertEqual(unlabeled[:2], ("DELETE", self.OWNER_LABEL))
        self.assertEqual(closed, ("PATCH", SLUG + "/issues/900",
                                  {"state": "closed"}))
        self.assertIn("STALL-ENDED: closed #900", lines)
        self.assertIsNone(self.saved())

    def test_an_end_before_owner_after_minutes_removes_no_label(self):
        _, send = self.relay()
        self.waits(0, send=send)
        self.waits(30, send=send)
        self.tick([self.item()], live=True, minutes=31, send=send)
        self.assertEqual([w[0] for w in self.stall_writes()],
                         ["POST", "PATCH"])

    def test_a_queue_that_empties_ends_it_too(self):
        self.waits(0)
        self.waits(30)
        self.tick([], live=True, minutes=31)
        said, _ = self.stall_writes()
        self.assertIn("#5 is no longer first in the queue", said[2]["body"])

    def test_without_a_sender_nothing_is_messaged(self):
        """Only `main` passes the relay, so no test messages an agent."""
        self.waits(0)
        record, _ = self.waits(30)
        self.assertIn("stalled", record)
        self.assertEqual(self.saved()["issue"], 900)
        self.assertNotIn("told", self.saved())

    def test_nobody_to_tell_still_opens_the_issue(self):
        self.alert(tell=[])
        sent, send = self.relay()
        self.waits(0, send=send)
        _, lines = self.waits(30, send=send)
        self.assertEqual(sent, [])
        self.assertIn("STALL-ISSUE: opened #900", lines)
        self.assertFalse(any(l.startswith("STALL-TOLD") for l in lines))

    def test_a_refused_label_is_tried_on_the_next_tick(self):
        """Without its Owner: label, the router can't say whose the
        issue is."""
        _, send = self.relay()
        self.waits(0, send=send)
        _, lines = self.tick(
            [self.item()], conclusions=(None,), live=True, minutes=30,
            send=send, refuse=lambda method, path: "HTTP 502"
            if path.endswith("/labels") else None)
        self.assertIn("STALL-LABEL refused: bug on #900: HTTP 502", lines)
        self.waits(31, send=send)
        self.assertEqual([w[2] for w in self.stall_writes()],
                         [{"labels[]": "bug"}, {"labels[]": "Owner:LeadDev"}])
        self.waits(32, send=send)
        self.assertEqual(self.stall_writes(), [])

    def test_a_refused_issue_is_tried_on_the_next_tick(self):
        _, send = self.relay()
        self.waits(0, send=send)
        _, lines = self.tick(
            [self.item()], conclusions=(None,), live=True, minutes=30,
            send=send, refuse=lambda method, path: "HTTP 502"
            if path == SLUG + "/issues" else None)
        self.assertIn("STALL-ISSUE refused: HTTP 502", lines)
        self.assertIsNone(self.saved())
        self.waits(31, send=send)
        self.assertEqual(self.stall_writes()[0][1], SLUG + "/issues")

    def test_a_new_head_on_the_same_pull_request_is_a_new_stall(self):
        """The stall was on a head; a push ends it, and if the new head
        stalls too, that is told as itself, with its own issue."""
        _, send = self.relay()
        self.waits(0, send=send)
        self.waits(30, send=send)
        other = self.repo.commit("g.txt", "pushed\n", "pushed")
        self.repo.publish_pr(5, other)
        moved = self.item(head=other, approved=other)
        self.waits(31, item=moved, send=send)
        said, closed = self.stall_writes()
        self.assertIn("this tick's WAIT of #5", said[2]["body"])
        self.assertEqual(closed[1:], (SLUG + "/issues/900",
                                      {"state": "closed"}))
        self.waits(61, item=moved, send=send)
        created, *_ = self.stall_writes()
        self.assertEqual(created[1], SLUG + "/issues")
        self.assertIn(other[:10], created[2]["body"])

    def test_a_stall_on_a_new_head_is_never_taken_for_the_old_one(self):
        """Through a tick, a new head isn't stalled until it has waited
        `stall_minutes`, so the old stall ends in between; this pins the
        rule itself, for a Merger that missed those ticks."""
        github, when, lines = FakeGitHub({}), m.now_utc(), []
        for head in ("a" * 40, "b" * 40):
            m.tell_stall(self.tmp.name, github,
                         {"pr": 5, "head": head, "minutes": 30}, [], when,
                         None, lines.append)
        self.assertEqual(lines[-2:], ["STALL-ENDED: closed #900",
                                      "STALL-ISSUE: opened #901"])

    def test_an_ended_stall_is_closed_once_even_if_the_next_issue_is_refused(
            self):
        github, when, lines = FakeGitHub({}), m.now_utc(), []
        m.tell_stall(self.tmp.name, github,
                     {"pr": 5, "head": "a" * 40, "minutes": 30}, [], when,
                     None, lines.append)
        github.refuse = lambda method, path: (
            "HTTP 502" if path == SLUG + "/issues" else None)
        for _ in range(2):
            m.tell_stall(self.tmp.name, github,
                         {"pr": 6, "head": "b" * 40, "minutes": 30}, [],
                         when, None, lines.append)
        self.assertEqual(lines.count("STALL-ENDED: closed #900"), 1)
        self.assertEqual(lines.count("STALL-ISSUE refused: HTTP 502"), 2)

    def test_a_refused_close_is_said_and_not_tried_again(self):
        self.waits(0)
        self.waits(30)
        _, lines = self.tick([], live=True, minutes=31,
                             refuse=lambda method, path: "HTTP 403"
                             if method == "PATCH" else None)
        self.assertIn("STALL-ENDED: #900 could not be closed: HTTP 403", lines)
        self.assertIsNone(self.saved())

    def test_a_refused_write_is_quoted_in_the_issue(self):
        self.advance_main("n.txt")
        self.tick([self.item()], live=True, refuse=lambda method, path:
                  "HTTP 403: forbidden" if "/pulls/" in path else None)
        self.tick([self.item()], live=True, minutes=30,
                  refuse=lambda method, path: "HTTP 403: forbidden"
                  if "/pulls/" in path else None)
        created = self.stall_writes()[0]
        self.assertIn("UPDATE refused: HTTP 403: forbidden",
                      created[2]["body"])
        self.assertIn("the Merger App's access", created[2]["body"])

    def test_a_dry_run_opens_nothing_and_tells_nobody(self):
        sent, send = self.relay()
        self.waits(0, live=False, send=send)
        record, _ = self.waits(30, live=False, send=send)
        self.assertEqual((self.github.writes, sent), ([], []))
        self.assertIsNone(self.saved())

    def test_without_stall_alert_a_stall_is_only_printed(self):
        m.configure(project())
        sent, send = self.relay()
        self.waits(0, send=send)
        record, lines = self.waits(30, send=send)
        self.assertIn("stalled", record)
        self.assertEqual((self.github.writes, sent), ([], []))
        self.assertEqual(len(lines), 2)
        self.assertIsNone(self.saved())


class Main(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = os.path.join(tmp.name, "state")
        os.mkdir(self.state)
        self.checkout = os.path.join(tmp.name, "cron")
        os.makedirs(os.path.join(self.checkout, ".agent-forge"))
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(example(), f)
        self.environ = {"HOME": tmp.name,
                        "FORGE_CRON_CHECKOUT": self.checkout}
        self.lines = []
        self.addCleanup(m.configure, project())

    def run_main(self, argv, mint="token", tick=None, state_flag=True):
        minted, self.senders = [], []

        def run(args, cwd=None, env=None):
            if args[:4] == ["git", "-C", self.checkout, "rev-parse"]:
                return subprocess.CompletedProcess(
                    args, 0, self.checkout + "\n", "")
            raise AssertionError("unexpected %r" % (args,))

        def fake_mint(project_, agent):
            minted.append((project_.name, agent))
            return mint
        calls = []

        def fake_tick(repo, state_dir, github, run, out, live, send=None):
            calls.append((repo, state_dir, github.env["GH_TOKEN"], live))
            self.sent_with = send
            return tick() if tick else None

        def fake_sender(project_, environ):
            self.senders.append(project_.name)
            return fake_sender
        flag = ["--state-dir", self.state] if state_flag else []
        with mock.patch.object(m, "tick", fake_tick):
            rc = m.main(argv + flag, run, self.lines.append,
                        environ=self.environ, mint=fake_mint,
                        sender=fake_sender)
        if calls:
            self.assertEqual(minted, [("widget", "Merger")])
        return rc, calls

    def test_without_dry_run_it_acts(self):
        self.assertEqual(self.run_main([]),
                         (0, [(self.checkout, self.state, "token", True)]))

    def test_a_dry_run_does_not_act(self):
        self.assertEqual(self.run_main(["--dry-run"]),
                         (0, [(self.checkout, self.state, "token", False)]))

    def configure_alert(self, alert):
        data = example()
        if alert is None:
            del data["merge"]["stall_alert"]
        else:
            data["merge"]["stall_alert"] = alert
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(data, f)

    def test_a_live_tick_with_a_stall_alert_is_given_the_relay(self):
        # Only main passes it, so a test that ticks through a stall
        # never messages a real agent.
        self.run_main([])
        self.assertEqual(self.senders, ["widget"])
        self.assertIsNotNone(self.sent_with)

    def test_a_dry_run_is_given_no_relay(self):
        self.run_main(["--dry-run"])
        self.assertEqual(self.senders, [])
        self.assertIsNone(self.sent_with)

    def test_without_a_stall_alert_no_relay(self):
        self.configure_alert(None)
        self.run_main([])
        self.assertEqual(self.senders, [])
        self.assertIsNone(self.sent_with)

    def test_no_token(self):
        rc, calls = self.run_main([], mint=None)
        self.assertEqual((rc, calls), (2, []))
        self.assertEqual(self.lines, ["NO-TOKEN: could not mint Merger's token"])

    def test_a_failed_tick_exits_2(self):
        def boom():
            raise RuntimeError("gh api graphql: down")
        rc, _ = self.run_main(["--dry-run"], tick=boom)
        self.assertEqual(rc, 2)
        self.assertEqual(self.lines, ["FAILED: gh api graphql: down"])

    def test_a_stalled_tick_exits_2(self):
        rc, _ = self.run_main([], tick=lambda: {
            "stalled": {"pr": 5, "head": "a", "minutes": 30}})
        self.assertEqual(rc, 2)

    def test_a_tick_that_waits_without_stalling_exits_0(self):
        rc, _ = self.run_main([], tick=lambda: {
            "decisions": [{"action": "WAIT"}]})
        self.assertEqual(rc, 0)

    def test_a_held_lock_is_busy_not_an_error(self):
        with open(os.path.join(self.state, "lock"), "a") as held:
            fcntl.flock(held, fcntl.LOCK_EX)
            rc, calls = self.run_main(["--dry-run"])
        self.assertEqual((rc, calls), (0, []))
        self.assertIn("BUSY", self.lines[0])

    def test_outside_a_checkout(self):
        run = lambda args, cwd=None, env=None: subprocess.CompletedProcess(
            args, 128, "", "not a repo")
        rc = m.main(["--dry-run", "--state-dir", self.state], run,
                    self.lines.append, environ=self.environ)
        self.assertEqual(rc, 2)
        self.assertIn("NO-REPO", self.lines[0])

    def test_no_checkout_at_all(self):
        rc = m.main(["--dry-run"], None, self.lines.append, environ={})
        self.assertEqual(rc, 2)
        self.assertIn("FORGE_CRON_CHECKOUT", self.lines[0])

    def test_a_bad_configuration(self):
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            f.write("{")
        rc, calls = self.run_main(["--dry-run"])
        self.assertEqual((rc, calls), (2, []))
        self.assertIn("NO-CONFIG", self.lines[0])

    def test_the_state_directory_defaults_under_the_home(self):
        rc, calls = self.run_main(["--dry-run"], state_flag=False)
        self.assertEqual((rc, calls[0][1]), (0, os.path.join(
            self.environ["HOME"], ".local", "state", "agent-forge", "widget",
            "merger")))


class RelaySender(unittest.TestCase):
    """The stall message goes out through the router's own poster, to
    the agent's session name."""

    class Response:
        status = 200

        def read(self, limit=-1):
            return b'{"status": "queued", "targets": [{"to": "x"}]}'

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.token_file = os.path.join(tmp.name, "token")
        with open(self.token_file, "w") as handle:
            handle.write("WEBHOOK_TOKEN=s3cret\n")

    def send(self, environ):
        data = example()
        data["relay"] = {"url": "http://relay:8788",
                         "token_file": self.token_file}
        seen = []

        def opener(req, timeout):
            seen.append(req)
            return self.Response()
        got = m.relay_sender(config.Project(data, None), environ,
                             opener)("LeadDev", "From: Merger\nx")
        return got, seen

    def test_the_message_is_the_relays_verbatim_agent_post(self):
        got, (req,) = self.send({})
        self.assertIsNone(got)
        self.assertEqual(req.full_url, "http://relay:8788/agent/WidgetLeadDev")
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["x-webhook-token"], "s3cret")
        self.assertEqual(headers["x-xmpp-verbatim"], "1")
        self.assertEqual(req.data, b"From: Merger\nx")

    def test_the_routers_override_of_the_relay_applies(self):
        _, (req,) = self.send({"FORGE_ROUTER_RELAY": "http://other:1"})
        self.assertEqual(req.full_url, "http://other:1/agent/WidgetLeadDev")


class Configure(unittest.TestCase):
    """What the configuration decides."""

    def tearDown(self):
        m.configure(project())

    def test_every_agent_of_kind_reviewer_approves(self):
        self.assertEqual(m.APPROVERS, {
            "reviewerwidgetagent": "Reviewer",
            "uireviewerwidgetagent": "UIReviewer"})

    def test_a_second_reviewers_approval_counts_but_not_on_its_own(self):
        approval = {"state": "APPROVED", "submittedAt": "2026-10-10T01:00:00Z",
                    "author": {"login": "uireviewerwidgetagent"}}
        pr_ = {"author": {"login": AUTHOR}, "reviews": [approval]}
        self.assertIs(m.approval_of(pr_), approval)
        own = dict(pr_, author={"login": "uireviewerwidgetagent"})
        self.assertIsNone(m.approval_of(own))

    def test_the_project_and_its_apps(self):
        self.assertEqual((m.REPO_SLUG, m.MAIN, m.ACT_AS, m.SIGNATURE),
                         ("example/widget", "main", "Merger", "— Merger"))
        self.assertEqual(m.QUIET_LOGINS, {"mergerwidgetagent"})
        self.assertEqual((m.STATUS_CONTEXT, m.STATUS_CREATOR),
                         ("vm/build-and-test", "mergerwidgetagent[bot]"))

    def test_the_defaults(self):
        data = example()
        del data["merge"]
        m.configure(config.Project(data, None))
        self.assertEqual(m.GATE, (("forge-commit-identity", "{base}..{head}"),))
        self.assertEqual(m.CI_WORKFLOW, "ci")
        self.assertEqual(m.STALL_AFTER, datetime.timedelta(minutes=30))
        self.assertFalse(m.STALL_ALERT)

    def test_a_stall_alert_and_its_defaults(self):
        m.configure(project(stall_alert={}))
        self.assertTrue(m.STALL_ALERT)
        self.assertEqual((m.STALL_TELL, m.STALL_LABELS), ((), ()))
        self.assertEqual((m.STALL_RETELL, m.STALL_TO_OWNER), (
            datetime.timedelta(minutes=60), datetime.timedelta(minutes=120)))
        self.assertEqual(m.STALL_TITLE,
                         "forge-merger: the queue is stalled on #{pr}")
        self.assertEqual(m.WAITING_OWNER, "Status:waiting-owner")

    def test_the_example_stall_alert(self):
        m.configure(config.Project(example(), None))
        self.assertEqual(m.STALL_TELL, ("LeadDev", "Reviewer"))
        self.assertEqual(m.STALL_LABELS, ("bug", "Owner:LeadDev"))
        self.assertEqual(m.STALL_RETELL, datetime.timedelta(minutes=60))

    def test_a_status_poster_other_than_the_merger(self):
        data = example()
        data["checks"]["poster"] = "Helper"
        m.configure(config.Project(data, None))
        self.assertEqual(m.STATUS_CREATOR, "helperbot[bot]")

    def test_no_ci_workflow_needs_no_run(self):
        m.configure(project(ci_workflow=""))
        self.assertIsNone(m.ci_problem([]))

    def test_the_stall_time(self):
        m.configure(project(stall_minutes=5))
        self.assertEqual(m.STALL_AFTER, datetime.timedelta(minutes=5))

    def test_gate_arguments_are_filled_in(self):
        self.assertEqual(
            m.gate_args(GATE[0], "/r", 7, "b" * 40, "h" * 40),
            ["/r/tools/closing-keywords", "--pr", "7", "--include-subject"])
        self.assertEqual(
            m.gate_args(GATE[1], "/r", 7, "b", "h"),
            [os.path.join(m.BIN, "forge-commit-identity"), "b..h"])

    def test_a_forge_tool_named_by_path_runs_from_the_checkout(self):
        self.assertEqual(m.gate_args(["tools/forge-x"], "/r", 1, "b", "h"),
                         ["/r/tools/forge-x"])


# merge.test_ahead.

class PredictAhead(unittest.TestCase):
    """The predicted commits are the ones a squash of each pull request
    ahead, then GitHub's update, would make."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repo(tmp.name)
        self.repo.commit("shared.txt", "one\ntwo\nthree\n", "shared")
        self.repo.push("main")
        self.base = self.repo.main_tip()

    def pr_on(self, number, start, files):
        self.repo.branch("b%d" % number, start)
        for name, text in files.items():
            sha = self.repo.commit(name, text, "#%d %s" % (number, name))
        self.repo.publish_pr(number, sha)
        return pr(number, T1, head=sha)

    def landed(self, onto, head):
        """What merging `head` makes on top of `onto`, the way a squash
        then an update does it, with `git merge` rather than the code
        under test."""
        self.repo.branch("land", head)
        run_git(self.repo.path, "merge", "-q", "--no-edit", onto)
        return run_git(self.repo.path, "rev-parse", "HEAD^{tree}")

    def squash(self, parent, tree):
        return run_git(self.repo.path, "commit-tree", tree, "-p", parent,
                       "-m", "squash")

    def refs(self):
        return run_git(self.repo.path, "for-each-ref",
                       "--format=%(refname)", m.AHEAD_REF).split()

    def test_each_prediction_is_what_the_merges_ahead_of_it_make(self):
        # #2 branches from an older main, and #3 edits a file #1 and #2
        # leave alone, so each needs a real three-way merge.
        old = run_git(self.repo.path, "rev-parse", "HEAD~1")
        q = [self.pr_on(1, self.base, {"a.txt": "a\n"}),
             self.pr_on(2, old, {"b.txt": "b\n"}),
             self.pr_on(3, self.base, {"shared.txt": "one\ntwo\nTHREE\n"})]
        got = m.predict_ahead(self.repo.path, self.base, q)
        self.assertEqual([e["pr"] for e in got], [1, 2, 3])
        main = self.base
        for entry, item in zip(got, q):
            tree = self.landed(main, item["headRefOid"])
            self.assertEqual(entry["tree"], tree, "#%d" % entry["pr"])
            self.assertEqual(run_git(self.repo.path, "rev-parse",
                                     entry["sha"] + "^{tree}"), tree)
            main = self.squash(main, tree)
        self.assertEqual([run_git(self.repo.path, "rev-parse",
                                  m.AHEAD_REF + str(k)) for k in range(3)],
                         [e["sha"] for e in got])

    def test_each_prediction_is_a_child_of_the_one_before(self):
        q = [self.pr_on(n, self.base, {"f%d.txt" % n: "x\n"})
             for n in (1, 2)]
        got = m.predict_ahead(self.repo.path, self.base, q)
        parents = [run_git(self.repo.path, "rev-parse", e["sha"] + "^")
                   for e in got]
        self.assertEqual(parents, [self.base, got[0]["sha"]])

    def test_an_up_to_date_head_is_its_own_tree(self):
        q = [self.pr_on(1, self.base, {"a.txt": "a\n"}),
             self.pr_on(2, self.base, {"b.txt": "b\n"})]
        got = m.predict_ahead(self.repo.path, self.base, q)
        self.assertIs(got[0].get("same_as_head"), True)
        self.assertNotIn("same_as_head", got[1])

    def test_the_same_prediction_is_the_same_commit(self):
        q = [self.pr_on(1, self.base, {"a.txt": "a\n"}),
             self.pr_on(2, self.base, {"b.txt": "b\n"})]
        self.assertEqual(m.predict_ahead(self.repo.path, self.base, q),
                         m.predict_ahead(self.repo.path, self.base, q))

    def test_a_signing_configuration_does_not_change_the_commit(self):
        q = [self.pr_on(1, self.base, {"a.txt": "a\n"})]
        first = m.predict_ahead(self.repo.path, self.base, q)
        run_git(self.repo.path, "config", "commit.gpgsign", "true")
        run_git(self.repo.path, "config", "gpg.program", "/nonexistent")
        self.assertEqual(m.predict_ahead(self.repo.path, self.base, q), first)

    def test_a_conflict_ends_the_list(self):
        q = [self.pr_on(1, self.base, {"shared.txt": "ONE\n"}),
             self.pr_on(2, self.base, {"shared.txt": "uno\n"}),
             self.pr_on(3, self.base, {"c.txt": "c\n"})]
        self.assertEqual([e["pr"] for e in m.predict_ahead(
            self.repo.path, self.base, q)], [1])

    def test_a_head_not_fetched_yet_is_fetched(self):
        q = [self.pr_on(1, self.base, {"a.txt": "a\n"})]
        other = os.path.join(os.path.dirname(self.repo.path), "other")
        run_git(os.path.dirname(self.repo.path), "clone", "-q",
                self.repo.origin, other)
        run_git(other, "fetch", "-q", "origin", "main")
        got = m.predict_ahead(other, run_git(other, "rev-parse",
                                             "origin/main"), q)
        self.assertEqual([e["pr"] for e in got], [1])

    def test_no_more_than_the_depth_and_old_refs_go(self):
        q = [self.pr_on(n, self.base, {"f%d.txt" % n: "x\n"})
             for n in range(1, 7)]
        self.assertEqual(len(m.predict_ahead(self.repo.path, self.base, q)),
                         m.DEFAULT_AHEAD_DEPTH + 1)
        self.assertEqual(len(m.predict_ahead(self.repo.path, self.base, q,
                                             depth=1)), 2)
        m.predict_ahead(self.repo.path, self.base, q[:1])
        self.assertEqual(self.refs(), [m.AHEAD_REF + "0"])
        m.predict_ahead(self.repo.path, self.base, [])
        self.assertEqual(self.refs(), [])

    def test_the_build_check_checks_a_prediction_out_unfetched(self):
        # The build check's worktree shares the Merger's object store, so
        # the prediction is there without any fetch or push.
        pc = load_tool("forge-pr-check")
        q = [self.pr_on(1, self.base, {"a.txt": "a\n"}),
             self.pr_on(2, self.base, {"b.txt": "b\n"})]
        got = m.predict_ahead(self.repo.path, self.base, q)
        wt = os.path.join(os.path.dirname(self.repo.path), "wt")
        pc.MAIN = "main"
        self.assertEqual(pc.prepare_ahead(self.repo.path, wt, got[1]["sha"]),
                         ("ready", ""))
        self.assertEqual(pc.tree_of(wt, got[1]["sha"]), got[1]["tree"])
        self.assertEqual(sorted(os.listdir(wt)),
                         [".git", "a.txt", "b.txt", "base.txt", "shared.txt"])


class TickAhead(RepoCase):
    """A live tick predicts from the pull request it acts on. Tick's
    helpers, without running its tests again."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr
    next_json = Tick.next_json

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        m.configure(project(test_ahead={}))
        self.addCleanup(m.configure, project())

    def ahead_json(self):
        return m.load_json(os.path.join(self.state, m.AHEAD_FILE), None)

    def test_the_configuration(self):
        self.assertEqual((m.TEST_AHEAD, m.AHEAD_DEPTH), (True, 3))
        m.configure(project(test_ahead={"depth": 1}))
        self.assertEqual(m.AHEAD_DEPTH, 1)
        m.configure(project())
        self.assertIs(m.TEST_AHEAD, False)

    def test_a_live_tick_predicts_from_the_one_it_acts_on(self):
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,), live=True)
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        self.assertEqual(record["ahead"], [5, 6])
        ahead = self.ahead_json()
        self.assertEqual([e["pr"] for e in ahead["entries"]], [5, 6])
        self.assertEqual((ahead["base"], ahead["repo"], ahead["at"]),
                         (self.repo.main_tip(), self.repo.path,
                          record["at"]))
        self.assertEqual(self.next_json(), {"at": record["at"], "pr": 5,
                                            "action": "WAIT"})

    def test_an_update_predicts_the_tree_the_update_makes(self):
        self.advance_main("n.txt")
        self.tick([self.item()], live=True)
        entry, = self.ahead_json()["entries"]
        self.assertEqual(entry["tree"], run_git(
            self.repo.path, "merge-tree", "--write-tree",
            self.repo.main_tip(), self.head))
        self.assertNotIn("same_as_head", entry)

    def test_a_dry_tick_predicts_nothing(self):
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertIsNone(self.ahead_json())
        self.assertNotIn("ahead", record)

    def test_handed_back(self):
        other = self.other_pr()
        approval = other["reviews"][0]
        said = {"6": {"approved": approval["submittedAt"],
                      "head": other["headRefOid"]}}
        self.assertTrue(m.handed_back(said, other, approval))
        self.assertFalse(m.handed_back(
            said, other, dict(approval, submittedAt="later")))
        self.assertFalse(m.handed_back(
            said, dict(other, headRefOid="0" * 40), approval))
        self.assertFalse(m.handed_back({}, other, approval))

    def test_one_handed_back_under_its_approval_is_not_predicted(self):
        other = self.other_pr()
        os.makedirs(os.path.join(self.tmp.name, "state"), exist_ok=True)
        m.save_json(os.path.join(self.tmp.name, "state", "handed-back.json"),
                    {"6": {"head": other["headRefOid"], "reason": "red",
                           "approved": other["reviews"][0]["submittedAt"]}})
        self.tick([self.item(), other], conclusions=(None,), live=True)
        self.assertEqual([e["pr"] for e in self.ahead_json()["entries"]], [5])

    def test_a_tick_waiting_on_nothing_predicts_nothing(self):
        # The old predictions and refs go.
        self.tick([self.item(), self.other_pr()], conclusions=(None,),
                  live=True)
        self.assertTrue(run_git(self.repo.path, "for-each-ref", m.AHEAD_REF))
        self.tick([], live=True, minutes=1)
        self.assertEqual(self.ahead_json()["entries"], [])
        self.assertEqual(run_git(self.repo.path, "for-each-ref",
                                 m.AHEAD_REF), "")

    def keeps_place(self, action):
        os.makedirs(os.path.join(self.tmp.name, "state"), exist_ok=True)
        m.save_json(os.path.join(self.tmp.name, "state", "next.json"),
                    {"at": "2026-10-07T11:59:00Z", "pr": 6, "action": action})
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,), live=True)
        return record["queue"][0]

    def test_one_with_a_run_in_flight_keeps_its_place(self):
        # #5 is older, so it would go first; #6 is mid-run.
        self.assertEqual(m.IN_FLIGHT, ("UPDATE", "WAIT"))
        for action in m.IN_FLIGHT:
            with self.subTest(action=action):
                self.assertEqual(self.keeps_place(action), 6)

    def test_the_control_one_merged_or_handed_back_keeps_nothing(self):
        for action in ("MERGE", "HAND-BACK", None):
            with self.subTest(action=action):
                self.assertEqual(self.keeps_place(action), 5)

    def test_a_blocker_is_not_promoted_past_one_in_flight(self):
        # #6 names #7 as its blocker, which would put #7 first.
        os.makedirs(os.path.join(self.tmp.name, "state"), exist_ok=True)
        m.save_json(os.path.join(self.tmp.name, "state", "next.json"),
                    {"at": "2026-10-07T11:59:00Z", "pr": 5, "action": "WAIT"})
        blocker = self.other_pr(7)
        held = pr(6, T1, body="Blocked-by: #7")
        record, _ = self.tick([self.item(), held, blocker],
                              conclusions=(None,), live=True)
        self.assertEqual(record["queue"], [5, 7])

    def test_without_test_ahead_a_blocker_still_goes_first(self):
        m.configure(project())
        os.makedirs(os.path.join(self.tmp.name, "state"), exist_ok=True)
        m.save_json(os.path.join(self.tmp.name, "state", "next.json"),
                    {"at": "2026-10-07T11:59:00Z", "pr": 5, "action": "WAIT"})
        blocker = self.other_pr(7)
        held = pr(6, T1, body="Blocked-by: #7")
        record, _ = self.tick([self.item(), held, blocker],
                              conclusions=(None,), live=True)
        self.assertEqual(record["queue"], [7, 5])
        self.assertIsNone(self.ahead_json())
        self.assertNotIn("action", self.next_json())
        self.assertNotIn("ahead", record)


if __name__ == "__main__":
    unittest.main()
