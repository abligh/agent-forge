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
import io
import json
import os
import subprocess
import tempfile
import unittest
import urllib.error
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


def urgent_project(**merge):
    data = example()
    data["merge"] = dict({"gate": GATE}, **merge)
    data.setdefault("labels", {})["urgent"] = "Priority:urgent"
    return config.Project(data, None)


def labelled(p, *names):
    p["labels"] = {"nodes": [{"name": n} for n in names]}
    return p


class Urgent(unittest.TestCase):
    """labels.urgent: a pull request carrying it goes first."""

    def setUp(self):
        m.configure(urgent_project(yield_after_hand_back=2))
        self.addCleanup(m.configure, project())

    def test_before_older_ones_and_blockers(self):
        waiting = pr(1, T0, body="Blocked-by: #2")
        hot = labelled(pr(5, T3), "Priority:urgent")
        got = m.order([waiting, pr(2, T2), pr(3, T1), hot])
        self.assertEqual([p["number"] for p in got], [5, 2, 1, 3])

    def test_several_oldest_first(self):
        got = m.order([labelled(pr(5, T3), "Priority:urgent"), pr(3, T0),
                       labelled(pr(4, T1), "Priority:urgent")])
        self.assertEqual([p["number"] for p in got], [4, 5, 3])

    def test_another_label_is_nothing(self):
        got = m.order([pr(3, T0), labelled(pr(5, T3), "bug")])
        self.assertEqual([p["number"] for p in got], [3, 5])

    def test_off_without_the_setting(self):
        m.configure(project())
        got = m.order([pr(3, T0), labelled(pr(5, T3), "Priority:urgent")])
        self.assertEqual([p["number"] for p in got], [3, 5])

    def test_an_urgent_one_never_yields(self):
        hot = labelled(pr(5, T0), "Priority:urgent")
        queue = [hot, pr(6, T1), pr(7, T2)]
        got, moved = m.yield_places(queue, queue, {5: 3})
        self.assertEqual([p["number"] for p in got], [5, 6, 7])
        self.assertEqual(moved, {})

    def test_the_query_reads_labels(self):
        self.assertIn("labels(first:50) { nodes { name } }", m.PR_QUERY)

    def test_a_fix_of_the_red_nightly_goes_first_without_the_label(self):
        m.configure(project())
        self.addCleanup(setattr, m, "NIGHTLY_FIXES", frozenset())
        m.NIGHTLY_FIXES = frozenset({5})
        got = m.order([pr(3, T0), pr(5, T3), pr(4, T1)])
        self.assertEqual([p["number"] for p in got], [5, 3, 4])
        self.assertTrue(m.urgent(pr(5, T3)))
        self.assertFalse(m.urgent(pr(3, T0)))


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

    def test_the_author_is_quiet(self):
        # An author who wants it held makes it a draft. Their notes
        # after the approval held approved pull requests for hours.
        self.assertEqual(self.said([comment(AUTHOR, at=T2)]), [])
        self.assertEqual(self.said([comment(AUTHOR + "[bot]", at=T2)]), [])
        self.assertEqual(self.said(reviews=[review(AUTHOR, "COMMENTED",
                                                   at=T2)]), [])

    def test_someone_elses_comment_counts(self):
        self.assertEqual(self.said([comment(PEER, at=T2)]),
                         [(PEER, T2, "comment")])

    def test_a_comment_at_the_same_second_does_not_count(self):
        self.assertEqual(self.said([comment(PEER, at=T1)]), [])

    def test_a_bare_later_approval_is_not_said(self):
        # A code owner's approval after Reviewer's is assent, not a hold.
        self.assertEqual(self.said(reviews=[review(PEER, at=T2)]), [])
        self.assertEqual(self.said(reviews=[review(PEER, at=T2, body=" \n")]),
                         [])

    def test_a_later_approval_with_text_is_not_said(self):
        # An owner's sign-off for their part, with notes, is assent: it
        # held a pull request for five hours. Second thoughts request
        # changes instead.
        self.assertEqual(self.said(reviews=[review(PEER, at=T2,
                                                   body="nit: rename x")]),
                         [])

    def test_a_later_approval_with_line_comments_is_not_said(self):
        r = review(PEER, at=T2)
        r["comments"] = {"totalCount": 2}
        self.assertEqual(self.said(reviews=[r]), [])

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
        got = self.said([comment(REVIEWER, at=T3), comment(PEER, at=T2)])
        self.assertEqual([s[0] for s in got], [PEER, REVIEWER])


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
        self.assertHeld(pr(comments=[comment(PEER)]), set(),
                        "comment by %s at %s" % (PEER, T2))
        self.assertHeld(pr(body="Blocked-by: #7"), {7}, "Blocked-by: #7")

    def test_a_standing_request_for_changes_holds_it(self):
        # #458's case: one reviewer asked for changes, another approved
        # four seconds later. GitHub refuses the merge while it stands.
        other = "uireviewerwidgetagent"
        self.assertHeld(pr(reviews=[review(other, "CHANGES_REQUESTED", T1),
                                    review(REVIEWER, at=T2)]), set(),
                        "changes requested by %s at %s" % (other, T1))
        self.assertHeld(pr(reviews=[review(REVIEWER, at=T1),
                                    review("example-owner",
                                           "CHANGES_REQUESTED", T0)]), set(),
                        "changes requested by example-owner")

    def test_a_request_lifts_with_its_reviewers_next_verdict(self):
        other = "uireviewerwidgetagent"
        for verdict in ("APPROVED", "DISMISSED"):
            self.assertIsNotNone(m.eligible(pr(reviews=[
                review(other, "CHANGES_REQUESTED", T0),
                review(other + "[bot]", verdict, T1),
                review(REVIEWER, at=T2)]), set())[0], verdict)
        # A comment-only review is no verdict.
        self.assertHeld(pr(reviews=[review(other, "CHANGES_REQUESTED", T0),
                                    review(other, "COMMENTED", T1),
                                    review(REVIEWER, at=T1)]), set(),
                        "changes requested")

    def test_only_reviewers_the_owner_and_writers_hold_by_asking(self):
        self.assertIsNotNone(m.eligible(pr(reviews=[
            review(PEER, "CHANGES_REQUESTED", T0), review(REVIEWER)]),
            set())[0])
        # Anyone who can push counts with GitHub, so with the Merger.
        asked = review(PEER, "CHANGES_REQUESTED", T0)
        asked["authorCanPushToRepository"] = True
        self.assertHeld(pr(reviews=[asked, review(REVIEWER)]), set(),
                        "changes requested by %s at %s" % (PEER, T0))
        # The author's own review never holds it.
        mine = review(AUTHOR, "CHANGES_REQUESTED", T0)
        mine["authorCanPushToRepository"] = True
        self.assertIsNotNone(m.eligible(pr(reviews=[mine, review(REVIEWER)]),
                                        set())[0])

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


def git_env():
    """The environment every test's git runs in: an identity of its own,
    so a host with no global one runs the same."""
    return dict(os.environ, GIT_AUTHOR_NAME="t", GIT_AUTHOR_EMAIL="t@t",
                GIT_COMMITTER_NAME="t", GIT_COMMITTER_EMAIL="t@t")


def run_git(cwd, *args):
    got = subprocess.run(["git", "-C", str(cwd)] + list(args), env=git_env(),
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
        """The answer whose prefix matches the first or second argument,
        the longest such prefix winning: a run's jobs are asked for
        under the runs' own path, and with `--paginate` first."""
        self.calls.append(args)
        found = [prefix for prefix in self.answers
                 if args[0].startswith(prefix) or (
                     len(args) > 1 and args[1].startswith(prefix))]
        if not found:
            raise AssertionError("unexpected gh api %r" % (args,))
        value = self.answers[max(found, key=len)]
        return value() if callable(value) else value


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
             refuse=None, minutes=0, send=None, extra=None,
             check_state=None):
        github = FakeGitHub(dict(answers(conclusions, prs=prs, issues=issues),
                                 **(extra or {})), refuse)
        self.github = github
        lines = []
        state = self.state = os.path.join(self.tmp.name, "state")
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        record = m.tick(self.repo.path, state, github, tools_run(),
                        lines.append,
                        now=lambda: start + datetime.timedelta(minutes=minutes),
                        live=live, send=send, check_state=check_state)
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
        body = self.github.writes[0][2]["body"]
        self.assertIn("conflicts with main", body)
        self.assertIn(m.CONFLICT_ADVICE, body)
        # The author's other pull request, and where it stands.
        self.assertIn("Your other open pull requests, oldest first:\n\n"
                      "* #6: number 2 of 2 in the queue\n\n", body)
        self.assertTrue(body.endswith("\n\n— Merger"))
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
             {"expected_head_sha": self.head}),
            ("POST", SLUG + "/issues/5/comments",
             {"body": m.UPDATE_NOTE % ("main", self.head[:10], "— Merger")})])
        # The note says the approval stands, so nobody asks for it again.
        self.assertIn("no review is needed again",
                      self.github.writes[1][2]["body"])
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
        held = pr(7, T0, comments=[comment(PEER)])
        record, lines = self.tick([self.item(), held, pr(8, T0, reviews=[])])
        self.assertEqual(record["queue"], [5])
        self.assertEqual(record["held"], {"7": "comment by %s at %s, after "
                                          "the approval" % (PEER, T2)})
        self.assertEqual(record["spoken"], [
            {"pr": 7, "approved": T1, "said": T2}])
        self.assertEqual(record["decisions"], [
            {"action": "MERGE", "pr": 5, "head": self.head}])
        self.assertEqual(record["at"], "2026-10-07T12:00:00Z")
        self.assertEqual(record["main"], self.repo.main_tip())
        self.assertEqual(lines, ["DRY-RUN 1 eligible, 1 approved but held; "
                                 "#5 MERGE"])

    # A hold for a remark after the approval is said, once.

    def spoken_writes(self):
        return [f for _, path, f in self.github.writes
                if path == SLUG + "/issues/7/comments"]

    def test_a_hold_for_a_remark_is_said_once_to_the_approver(self):
        # Approved, green pull requests sat for hours, held
        # by a remark nobody knew was holding them.
        held = pr(7, T0, comments=[comment(PEER)])
        record, _ = self.tick([held], live=True)
        self.assertEqual(record["spoken"][0]["result"], "done")
        [said] = self.spoken_writes()
        body = said["body"]
        self.assertTrue(body.startswith("Not-a-review\n"))
        self.assertIn("%s's comment at %s came after" % (PEER, T2), body)
        self.assertIn("please approve again", body)
        self.assertTrue(body.endswith("— Merger"))
        record, _ = self.tick([held], live=True)
        self.assertEqual(record["spoken"][0]["result"], "already said")
        self.assertEqual(self.spoken_writes(), [])

    def test_a_new_remark_or_approval_is_said_again(self):
        self.tick([pr(7, T0, comments=[comment(PEER)])], live=True)
        record, _ = self.tick([pr(7, T0, comments=[comment(PEER),
                                                    comment(PEER, at=T3)])],
                              live=True)
        self.assertEqual(record["spoken"][0]["result"], "already said")
        record, _ = self.tick([pr(7, T0, reviews=[review(REVIEWER, at=T2)],
                                  comments=[comment(PEER, at=T3)])],
                              live=True)
        self.assertEqual(record["spoken"][0],
                         {"pr": 7, "approved": T2, "said": T3,
                          "result": "done"})

    def test_a_draft_or_a_released_hold_says_nothing(self):
        record, _ = self.tick([pr(7, T0, draft=True,
                                  comments=[comment(PEER)])], live=True)
        self.assertEqual(self.spoken_writes(), [])
        record, _ = self.tick([pr(7, T0, comments=[comment(AUTHOR)])],
                              live=True)
        self.assertFalse(record.get("spoken"))
        self.assertEqual(self.spoken_writes(), [])

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
        self.assertEqual(lines[1], (
            "STALLED: GitHub has refused the Merger's write to #5 at %s for "
            "30 minutes, most recently UPDATE refused: HTTP 403: forbidden. "
            "It stays first in the queue and is not handed back; look at the "
            "Merger App's access" % self.head[:10]))
        # A 403 is never taken for a lost head: GitHub is not asked.
        self.assertEqual([w[0] for w in self.github.writes], ["PUT"])

    def test_another_refusal_is_not_blamed_on_access(self):
        self.advance_main("n.txt")
        self.tick([self.item()], live=True, refuse="HTTP 502: bad gateway")
        record, lines = self.tick([self.item()], live=True,
                                  refuse="HTTP 502: bad gateway", minutes=30)
        self.assertEqual(lines[1], (
            "STALLED: GitHub has refused the Merger's write to #5 at %s for "
            "30 minutes, most recently UPDATE refused: HTTP 502: bad gateway. "
            "It stays first in the queue and is not handed back; GitHub's "
            "answer says why" % self.head[:10]))
        self.assertNotIn("access", m.stall_text(record["stalled"]))

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


# GitHub's own text, with its curly apostrophe, as a live Merger logged it.
STALE = ("gh: expected head sha didn\u2019t match current head ref. (HTTP 422)\n"
         '{"message":"expected head sha didn\u2019t match current head ref.",'
         '"documentation_url":"https://docs.github.com/rest/pulls/pulls'
         '#update-a-pull-request-branch","status":"422"}')


class LostHeadText(unittest.TestCase):
    def test_either_apostrophe_is_a_lost_head(self):
        self.assertTrue(m.LOST_HEAD_RE.search(STALE))
        self.assertTrue(m.LOST_HEAD_RE.search(
            "expected head sha didn't match current head ref."))
        self.assertTrue(m.LOST_HEAD_RE.search("Head branch was modified."))
        self.assertFalse(m.LOST_HEAD_RE.search("Resource not accessible"))


class LostHead(RepoCase):
    """GitHub moved #5's branch but not its head, and refuses every
    update pinned to either: the Merger nudges it once with a no-op base
    update, and if that doesn't help, hands it back once it has waited
    STALL_AFTER, so the queue moves past it."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        # main moves on, so #5 (self.head) is behind it: an UPDATE.
        self.advance_main("n.txt")
        # The commit GitHub's own update put on the branch: the clean
        # merge of main into the head.
        self.moved = self.repo.merge("origin/main")
        self.nudged = False

    def refuse(self, method, path):
        if path.endswith("/update-branch"):
            return STALE
        return None

    def github_answers(self, fixed=False, branch=None):
        """GitHub's pull request #5 (head self.head until a nudge, and
        self.moved after one when `fixed`) and its branch (self.moved,
        or `branch`)."""
        def pull():
            head = self.moved if fixed and self.patched() else self.head
            return {"head": {"sha": head, "ref": "b5",
                             "repo": {"full_name": "example/widget"}},
                    "base": {"ref": "main"}}
        return {SLUG + "/pulls/5": pull,
                SLUG + "/git/ref/heads/b5": {"object": {
                    "sha": branch or self.moved}}}

    def patched(self):
        return any(w[0] == "PATCH" for w in self.github.writes)

    def resync_line(self, now):
        return ("RESYNC #5: GitHub had head %s, branch is at %s; nudged with "
                "a no-op base update, and GitHub now has %s"
                % (self.head[:12], self.moved[:12], now[:12]))

    def test_a_nudge_that_works_makes_it_a_changed_pull_request(self):
        other = self.other_pr()
        record, lines = self.tick([self.item(), other], live=True,
                                  refuse=self.refuse,
                                  extra=self.github_answers(fixed=True))
        self.assertEqual(self.github.writes[:2], [
            ("PUT", SLUG + "/pulls/5/update-branch",
             {"expected_head_sha": self.head}),
            ("PATCH", SLUG + "/pulls/5", {"base": "main"})])
        self.assertEqual(lines[0], self.resync_line(self.moved))
        self.assertEqual(
            [(d["pr"], d["action"]) for d in record["decisions"]],
            [(5, "UPDATE"), (5, "RESYNC"), (6, "MERGE")])
        self.assertEqual(record["decisions"][0]["lost"], {
            "github": self.head, "branch": self.moved, "now": self.moved,
            "nudge": "done"})
        self.assertEqual(record["decisions"][1]["result"],
                         "GitHub now has " + self.moved[:12])
        self.assertNotIn("stalled", record)
        with open(os.path.join(self.state, "resynced.json")) as handle:
            self.assertEqual(json.load(handle),
                             {"5": {"head": self.head, "result": "done"}})

        # The next tick sees the new head and judges it as usual: the
        # approval covers a clean merge of main, its check is green, it
        # merges at that head and nothing is nudged again.
        self.repo.publish_pr(5, self.moved)
        record, lines = self.tick([self.item(head=self.moved)], live=True,
                                  minutes=1)
        self.assertEqual(self.github.writes, [
            ("PUT", SLUG + "/pulls/5/merge",
             {"sha": self.moved, "merge_method": "squash"})])
        self.assertEqual(len(lines), 1)

    def test_a_nudge_that_does_not_help_is_not_repeated(self):
        other = self.other_pr()
        prs = [self.item(), other]
        record, lines = self.tick(prs, live=True, refuse=self.refuse,
                                  extra=self.github_answers())
        self.assertEqual(lines[0], self.resync_line(self.head))
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(5, "UPDATE")])
        self.assertEqual(self.github.writes[1][0], "PATCH")
        for minutes in (1, 10, 29):
            record, lines = self.tick(prs, live=True, refuse=self.refuse,
                                      extra=self.github_answers(),
                                      minutes=minutes)
            # No nudge again, no RESYNC line, still first and not stalled.
            self.assertEqual([w[0] for w in self.github.writes], ["PUT"])
            self.assertEqual(len(lines), 1)
            self.assertEqual(record["decisions"][0]["lost"]["nudge"], "done")
            self.assertNotIn("stalled", record)

        # At STALL_AFTER it goes back to its author, once, and the queue
        # moves on to #6 in the same tick.
        record, lines = self.tick(prs, live=True, refuse=self.refuse,
                                  extra=self.github_answers(), minutes=30)
        self.assertEqual(
            [(d["pr"], d["action"]) for d in record["decisions"]],
            [(5, "UPDATE"), (5, "HAND-BACK"), (6, "MERGE")])
        self.assertEqual(record["decisions"][1]["reason"],
                         m.LOST_HEAD_REASON)
        self.assertEqual(record["stalled"]["handed_back"], True)
        self.assertEqual(lines[1], (
            "STALLED: GitHub has refused the Merger's write to #5 at %s for "
            "30 minutes, most recently UPDATE refused: %s. It is handed back "
            "to its author, and the queue moves past it; GitHub's pull "
            "request head is %s but its branch is at %s, and a no-op base "
            "update didn't make GitHub read it again"
            % (self.head[:10], STALE, self.head[:12], self.moved[:12])))
        (_, path, fields), = [w for w in self.github.writes
                              if w[0] == "POST"]
        self.assertEqual(path, SLUG + "/issues/5/comments")
        self.assertEqual(fields["body"], (
            "Not merging %s: GitHub refuses to update or merge it at this "
            "head, saying the head it expected didn't match.\n\n"
            "GitHub's pull request head is %s but its branch is at %s, and "
            "a no-op base update didn't make GitHub read it again. A push to "
            "the branch, or closing and reopening the pull request, makes "
            "GitHub read its head again.\n\n"
            "This is back with its author. Once a reviewer approves a head "
            "that fixes it, the Merger takes it again.\n\n— Merger"
            % (self.head[:10], self.head[:12], self.moved[:12])))

        # Later ticks neither update #5 again nor say more: no loop.
        record, lines = self.tick(prs, live=True, refuse=self.refuse,
                                  extra=self.github_answers(), minutes=31)
        self.assertEqual(
            [(d["pr"], d["action"], d["result"])
             for d in record["decisions"]],
            [(5, "HAND-BACK", "already said"), (6, "MERGE", "done")])
        self.assertEqual([w[1] for w in self.github.writes],
                         [SLUG + "/pulls/6/merge"])
        self.assertNotIn("stalled", record)

    def test_a_refusal_where_head_and_branch_agree_is_never_nudged(self):
        prs = [self.item()]
        extra = self.github_answers(branch=self.head)
        self.tick(prs, live=True, refuse=self.refuse, extra=extra)
        record, lines = self.tick(prs, live=True, refuse=self.refuse,
                                  extra=extra, minutes=30)
        self.assertNotIn("PATCH", [w[0] for w in self.github.writes])
        self.assertEqual(record["decisions"][0]["lost"], {
            "github": self.head, "branch": self.head, "now": self.head})
        self.assertEqual(record["decisions"][1]["action"], "HAND-BACK")
        self.assertTrue(lines[1].endswith(
            "It is handed back to its author, and the queue moves past it; "
            "GitHub's pull request head and its branch agree, at %s"
            % self.head[:12]))

    def test_a_refused_nudge_is_said_and_not_tried_again(self):
        def refuse(method, path):
            return "HTTP 403: no" if method == "PATCH" else self.refuse(
                method, path)
        prs = [self.item()]
        _, lines = self.tick(prs, live=True, refuse=refuse,
                             extra=self.github_answers())
        self.assertEqual(lines[0], (
            "RESYNC #5: GitHub had head %s, branch is at %s; the no-op base "
            "update was refused: HTTP 403: no"
            % (self.head[:12], self.moved[:12])))
        _, lines = self.tick(prs, live=True, refuse=refuse,
                             extra=self.github_answers(), minutes=30)
        self.assertEqual([w[0] for w in self.github.writes],
                         ["PUT", "POST"])
        self.assertTrue(lines[1].endswith(
            "and a no-op base update to make GitHub read it again was "
            "refused: HTTP 403: no"))

    def test_a_pull_request_github_cannot_read_is_handed_back_too(self):
        def broken():
            raise RuntimeError("gh api: HTTP 502")
        prs = [self.item()]
        extra = {SLUG + "/pulls/5": broken}
        self.tick(prs, live=True, refuse=self.refuse, extra=extra)
        record, lines = self.tick(prs, live=True, refuse=self.refuse,
                                  extra=extra, minutes=30)
        self.assertIsNone(record["decisions"][0]["lost"])
        self.assertEqual(record["decisions"][1]["action"], "HAND-BACK")
        self.assertTrue(lines[1].endswith(
            "GitHub's pull request head and its branch could not be read"))

    def test_a_dry_run_never_nudges(self):
        record, _ = self.tick([self.item()], refuse=self.refuse,
                              extra=self.github_answers())
        self.assertEqual(record["decisions"][0]["action"], "UPDATE")
        self.assertFalse(os.path.exists(
            os.path.join(self.state, "resynced.json")))

    def test_the_stall_issue_says_what_github_lost(self):
        text = m.stall_text({
            "pr": 5, "head": self.head, "minutes": 30, "handed_back": True,
            "refused": "UPDATE refused: " + STALE,
            "lost": {"github": self.head, "branch": self.moved,
                     "now": self.head, "nudge": "done"}})
        self.assertIn("GitHub's pull request head is %s but its branch is at "
                      "%s, and a no-op base update didn't make GitHub read it "
                      "again." % (self.head[:12], self.moved[:12]), text)
        self.assertNotIn("access", text)
        self.assertTrue(text.endswith(
            "It has gone back to its author, and the queue moves past it."))


def job(status="completed", conclusion="success",
        completed="2026-10-07T11:50:00Z"):
    return {"status": status, "conclusion": conclusion,
            "completed_at": completed if status == "completed" else None}


def stuck_run(status="in_progress", since="2026-10-07T11:40:00Z", ident=7):
    return dict(ci_run(None, status, ident=ident), run_started_at=since)


class CiStale(RepoCase):
    """GitHub goes on reporting a ci run unfinished after its jobs have
    all completed: the Merger judges the run by its jobs. A run whose
    jobs are unfinished too is re-run once STALL_AFTER after it started,
    and STALL_AFTER after that the queue moves past it."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)

    def extra(self, jobs, run=None):
        # #5's head only: any other's ci is the default green run.
        return {SLUG + "/actions/runs?head_sha=" + self.head: {
                    "workflow_runs": [run or stuck_run()]},
                SLUG + "/actions/runs/7/jobs": jobs}

    def test_finished_green_jobs_merge_it(self):
        jobs = [job(), job(conclusion="skipped",
                           completed="2026-10-07T11:51:00Z")]
        record, lines = self.tick([self.item()], live=True,
                                  extra=self.extra(jobs))
        self.assertEqual(lines[0], (
            "CI-STALE #5: GitHub says run 7 is in_progress, but all its jobs "
            "finished at 2026-10-07T11:51:00Z; judged by its jobs (green)"))
        decision, = record["decisions"]
        self.assertEqual((decision["action"], decision["result"]),
                         ("MERGE", "done"))
        self.assertEqual(decision["ci_stale"], [{
            "run": 7, "status": "in_progress",
            "finished": "2026-10-07T11:51:00Z", "judged": "green"}])
        self.assertEqual([w[1] for w in self.github.writes],
                         [SLUG + "/pulls/5/merge"])
        # Every page of the jobs, as one list.
        self.assertIn(("--paginate", SLUG + "/actions/runs/7/jobs"
                       "?per_page=100", "--jq", ".jobs"), self.github.calls)

    def test_a_neutral_job_is_green_too(self):
        record, _ = self.tick([self.item()], extra=self.extra(
            [job(conclusion="neutral")], stuck_run("queued")))
        self.assertEqual(record["decisions"][0]["action"], "MERGE")

    def test_a_failed_job_makes_it_red(self):
        jobs = [job(), job(conclusion="failure")]
        record, lines = self.tick([self.item()], live=True,
                                  extra=self.extra(jobs))
        self.assertTrue(lines[0].endswith("judged by its jobs (red)"))
        decision, = record["decisions"]
        self.assertEqual((decision["action"], decision["to"],
                          decision["reason"]),
                         ("HAND-BACK", "author", "ci run 7 concluded failure"))
        self.assertEqual([w[1] for w in self.github.writes],
                         [SLUG + "/issues/5/comments"])

    def test_a_run_really_in_progress_is_still_a_wait(self):
        jobs = [job(), job("in_progress", None)]
        record, lines = self.tick([self.item()], live=True,
                                  extra=self.extra(jobs))
        decision, = record["decisions"]
        self.assertEqual((decision["action"], decision["reason"]),
                         ("WAIT", "ci has not finished on the head"))
        self.assertEqual(decision["ci_unfinished"], [{
            "run": 7, "status": "in_progress",
            "since": "2026-10-07T11:40:00Z"}])
        self.assertEqual(self.github.writes, [])
        self.assertEqual(len(lines), 1)

    def test_jobs_github_cannot_read_are_unfinished(self):
        def broken():
            raise RuntimeError("gh api: HTTP 502")
        record, _ = self.tick([self.item()], extra=self.extra(broken))
        self.assertEqual(record["decisions"][0]["action"], "WAIT")

    def test_long_unfinished_is_rerun_once_then_moved_past(self):
        other = self.other_pr()
        prs = [self.item(), other]
        extra = self.extra([job(), job("queued", None)])
        # 20 minutes after it started: waited on, no re-run.
        record, lines = self.tick(prs, live=True, extra=extra)
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        self.assertEqual(self.github.writes, [])
        # 30 minutes: re-run, once.
        record, lines = self.tick(prs, live=True, extra=extra, minutes=10)
        self.assertEqual(self.github.writes,
                         [("POST", SLUG + "/actions/runs/7/rerun", {})])
        self.assertEqual(lines[0], (
            "CI-RERUN #5: GitHub says run 7 is in_progress, and so are its "
            "jobs, after 30 minutes; re-ran it, once for this head"))
        self.assertEqual(record["decisions"][0]["rerun"], "done")
        with open(os.path.join(self.state, m.RERUN_FILE)) as handle:
            self.assertEqual(json.load(handle), {"5": {
                "head": self.head, "run": 7,
                "since": "2026-10-07T11:40:00Z",
                "at": "2026-10-07T12:10:00Z", "result": "done"}})
        # Never again for this head, and #5 stays first meanwhile.
        for minutes in (11, 30, 39):
            record, lines = self.tick(prs, live=True, extra=extra,
                                      minutes=minutes)
            self.assertEqual(self.github.writes, [])
            self.assertEqual([(d["pr"], d["action"])
                              for d in record["decisions"]], [(5, "WAIT")])
            self.assertFalse([l for l in lines if l.startswith("CI-")])
        # STALL_AFTER after the re-run: back to its author, once, and
        # the queue moves on to #6 in the same tick.
        record, lines = self.tick(prs, live=True, extra=extra, minutes=40)
        self.assertEqual(
            [(d["pr"], d["action"], d.get("result"))
             for d in record["decisions"]],
            [(5, "HAND-BACK", "done"), (6, "MERGE", "done")])
        self.assertEqual(record["decisions"][0]["reason"],
                         m.ci_stuck_reason())
        detail = ("GitHub has reported ci run 7 unfinished, its jobs too, "
                  "since 2026-10-07T11:40:00Z; the Merger re-ran it at "
                  "2026-10-07T12:10:00Z (done), and it still hasn't "
                  "finished. A push to the branch starts CI afresh, and if "
                  "the run does finish green, the Merger takes it again")
        self.assertEqual(lines[0], "CI-STUCK #5: %s; handed back to its "
                         "author, and the queue moves past it" % detail)
        self.assertNotIn("stalled", record)
        (_, path, fields), = [w for w in self.github.writes
                              if w[0] == "POST"]
        self.assertEqual(path, SLUG + "/issues/5/comments")
        self.assertEqual(fields["body"], (
            "Not merging %s: the ci workflow has not finished on this head, "
            "even after the Merger re-ran it.\n\n%s.\n\nThis is back with "
            "its author. Once a reviewer approves a head that fixes it, the "
            "Merger takes it again.\n\n— Merger" % (self.head[:10], detail)))
        # Later ticks say nothing more and never re-run it again.
        record, _ = self.tick(prs, live=True, extra=extra, minutes=41)
        self.assertEqual(
            [(d["pr"], d["action"], d["result"])
             for d in record["decisions"]],
            [(5, "HAND-BACK", "already said"), (6, "MERGE", "done")])
        self.assertEqual([w[1] for w in self.github.writes],
                         [SLUG + "/pulls/6/merge"])
        # Not the pull request's own failure: it never yields for it.
        with open(os.path.join(self.state, "merger.jsonl")) as handle:
            records = [json.loads(l) for l in handle]
        self.assertEqual(m.hand_backs(records), {})
        # Once the run does finish green, it merges.
        record, _ = self.tick([self.item()], live=True, minutes=50)
        self.assertEqual([(d["action"], d["result"])
                          for d in record["decisions"]], [("MERGE", "done")])

    def test_a_refused_rerun_is_said_and_not_tried_again(self):
        extra = self.extra([job("queued", None)])
        refuse = lambda method, path: (
            "HTTP 403: no" if path.endswith("/rerun") else None)
        _, lines = self.tick([self.item()], live=True, extra=extra,
                             refuse=refuse, minutes=10)
        self.assertTrue(lines[0].endswith(
            "after 30 minutes; re-running it was refused: HTTP 403: no"))
        record, _ = self.tick([self.item()], live=True, extra=extra,
                              refuse=refuse, minutes=20)
        self.assertEqual(record["decisions"][0]["rerun"],
                         "refused: HTTP 403: no")
        self.assertEqual(self.github.writes, [])
        record, _ = self.tick([self.item()], live=True, extra=extra,
                              refuse=refuse, minutes=40)
        self.assertEqual(record["decisions"][0]["action"], "HAND-BACK")

    def test_a_rerun_for_another_head_does_not_count(self):
        state = os.path.join(self.tmp.name, "state")
        os.makedirs(state)
        m.save_json(os.path.join(state, m.RERUN_FILE), {"5": {
            "head": "f" * 40, "run": 7, "since": "2026-10-07T10:00:00Z",
            "at": "2026-10-07T10:30:00Z", "result": "done"}})
        record, _ = self.tick([self.item()], live=True, minutes=10,
                              extra=self.extra([job("queued", None)]))
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        self.assertEqual(self.github.writes,
                         [("POST", SLUG + "/actions/runs/7/rerun", {})])

    def test_a_dry_run_writes_nothing(self):
        extra = self.extra([job("in_progress", None)])
        record, lines = self.tick([self.item()], extra=extra, minutes=10)
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        self.assertEqual(lines[0], (
            "CI-RERUN #5: GitHub says run 7 is in_progress, and so are its "
            "jobs, after 30 minutes; a live tick would re-run it"))
        self.assertFalse(os.path.exists(
            os.path.join(self.state, m.RERUN_FILE)))


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

    def test_after_a_merge_it_ticks_again(self):
        merged = {"decisions": [{"action": "MERGE", "result": "done"}]}
        waits = {"decisions": [{"action": "WAIT"}]}
        records = [merged, merged, waits, merged]
        rc, calls = self.run_main([], tick=lambda: records.pop(0))
        self.assertEqual((rc, len(calls), records), (0, 3, [merged]))

    def test_a_refused_merge_or_a_dry_run_ticks_once(self):
        refused = {"decisions": [{"action": "MERGE", "result": "refused: x"}]}
        self.assertEqual(len(self.run_main([], tick=lambda: refused)[1]), 1)
        merged = {"decisions": [{"action": "MERGE", "result": "done"}]}
        self.assertEqual(len(self.run_main(["--dry-run"],
                                           tick=lambda: merged)[1]), 1)

    def test_ticks_in_a_run_are_bounded(self):
        merged = {"decisions": [{"action": "MERGE", "result": "done"}]}
        self.assertEqual(len(self.run_main([], tick=lambda: merged)[1]),
                         m.TICKS_PER_RUN)

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

    def test_a_twin_takes_it_when_she_cannot(self):
        data = example()
        data["agents"]["LeadDev"]["twins"] = ["WidgetLeadDev2"]
        data["relay"] = {"url": "http://relay:8788",
                         "token_file": self.token_file}
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            if req.full_url.endswith("/WidgetLeadDev"):
                raise urllib.error.HTTPError(
                    req.full_url, 404, "Not Found", {},
                    io.BytesIO(b'{"error": "unknown-name"}'))
            return self.Response()
        send = m.relay_sender(config.Project(data, None), {}, opener)
        self.assertIsNone(send("LeadDev", "From: Merger\nx"))
        self.assertEqual(seen, ["http://relay:8788/agent/WidgetLeadDev",
                                "http://relay:8788/agent/WidgetLeadDev2"])


    def test_her_stand_in_hears_while_she_is_away(self):
        data = example()
        data["agents"]["UIDev"]["away"] = "LeadDev"
        data["relay"] = {"url": "http://relay:8788",
                         "token_file": self.token_file}
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return self.Response()
        send = m.relay_sender(config.Project(data, None), {}, opener)
        self.assertIsNone(send("UIDev", "From: Merger\nx"))
        self.assertEqual(seen, ["http://relay:8788/agent/WidgetLeadDev"])

    def test_a_name_that_is_no_agent_is_sent_as_it_is(self):
        """Such as the owner's address (forge-nightly's escalation)."""
        data = example()
        data["relay"] = {"url": "http://relay:8788",
                         "token_file": self.token_file}
        seen = []

        def opener(req, timeout):
            seen.append(req.full_url)
            return self.Response()
        send = m.relay_sender(config.Project(data, None), {}, opener)
        self.assertIsNone(send("owner@example.org", "From: x\ny"))
        self.assertEqual(seen, ["http://relay:8788/agent/owner%40example.org"])


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

    def test_one_that_conflicts_with_one_ahead_is_skipped(self):
        # #2 conflicts with #1, so #3 is predicted on #1 alone, and the
        # list still fills to the depth.
        q = [self.pr_on(1, self.base, {"shared.txt": "ONE\n"}),
             self.pr_on(2, self.base, {"shared.txt": "uno\n"}),
             self.pr_on(3, self.base, {"c.txt": "c\n"})]
        got = m.predict_ahead(self.repo.path, self.base, q, depth=1)
        self.assertEqual([e["pr"] for e in got], [1, 3])
        self.assertEqual(run_git(self.repo.path, "rev-parse",
                                 got[1]["sha"] + "^"), got[0]["sha"])

    def test_the_first_conflicting_with_main_ends_the_list(self):
        self.repo.branch("main2", self.base)
        self.repo.commit("shared.txt", "main's\n", "main moves")
        main = run_git(self.repo.path, "rev-parse", "HEAD")
        q = [self.pr_on(1, self.base, {"shared.txt": "ONE\n"}),
             self.pr_on(2, self.base, {"c.txt": "c\n"})]
        self.assertEqual(m.predict_ahead(self.repo.path, main, q), [])

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


class Batches(unittest.TestCase):
    """designs/0032: which predictions are the batch, and what is said
    of it."""

    def setUp(self):
        m.configure(project(test_ahead={"batch": 4}))
        self.addCleanup(m.configure, project())

    def entries(self, *trees):
        return [{"pr": 10 + k, "sha": "%d" % k * 40, "tree": t,
                 "head": "h%d" % k} for k, t in enumerate(trees)]

    def size(self, entries, last=None, red=(), green=()):
        verdict = lambda t: False if t in red else True if t in green \
            else None
        return m.batch_size(entries, last, verdict)

    def test_the_configuration(self):
        self.assertEqual(m.AHEAD_BATCH, 4)
        m.configure(project(test_ahead={}))
        self.assertEqual(m.AHEAD_BATCH, 1)

    def test_a_new_batch_is_the_first_few(self):
        self.assertEqual(self.size(self.entries(*"abcde")), (4, None))
        self.assertEqual(self.size(self.entries(*"ab")), (2, None))
        self.assertEqual(self.size([]), (0, None))

    def test_a_new_batch_ends_at_a_tree_already_red(self):
        # Every prediction after a red one contains it.
        self.assertEqual(self.size(self.entries(*"abcde"), red="b"),
                         (1, 2))

    def test_a_red_top_halves_it_down_to_one(self):
        entries = self.entries(*"abcde")
        self.assertEqual(self.size(entries, red="d"), (2, 4))
        self.assertEqual(self.size(entries, red="db"), (1, 2))
        self.assertEqual(self.size(entries, {"trees": list("abcd")},
                                   red="db"), (1, 4))
        self.assertEqual(self.size(entries, {"trees": list("ab")},
                                   red="d", green="b"), (2, None))

    def test_the_batch_goes_on_as_its_members_merge(self):
        # #10 merged: what is left of the batch still leads.
        last = {"trees": list("abcd")}
        self.assertEqual(self.size(self.entries(*"bcdef"), last), (3, None))
        self.assertEqual(self.size(self.entries(*"def"), last), (1, None))

    def test_a_changed_member_starts_a_new_batch(self):
        last = {"trees": list("abcd")}
        self.assertEqual(self.size(self.entries(*"bxdef"), last), (4, None))
        self.assertEqual(self.size(self.entries(*"xyz"), last), (3, None))

    def test_the_lines(self):
        entries = self.entries(*"abcd")
        verdict = lambda t: t == "d" or None
        self.assertEqual(m.batch_line(entries, 4, None, verdict),
                         "BATCH #10..#13: top %s green, merging 4"
                         % ("3" * 10))
        self.assertEqual(m.batch_line(entries, 2, 4, verdict),
                         "BATCH #10..#13: top red, checking #10..#11")
        self.assertIsNone(m.batch_line(entries, 3, None, verdict))
        self.assertIsNone(m.batch_line(entries, 1, None, verdict))
        self.assertIsNone(m.batch_line([], 0, None, verdict))

    def test_a_trees_verdict(self):
        state = {"results": {
            "1": {"tree": "a", "ok": False}, "2": {"tree": "a", "ok": True},
            "3": {"tree": "b", "ok": False},
            "4": {"tree": "c", "ok": True, "covered": "x"}}}
        self.assertIs(m.tree_verdict(state, "a"), True)
        self.assertIs(m.tree_verdict(state, "b"), False)
        self.assertIsNone(m.tree_verdict(state, "c"))
        self.assertIsNone(m.tree_verdict(None, "a"))

    def holds(self, prs, tree="a", verdict=True, number=10):
        entries = self.entries(*"abc")
        last = {"entries": entries, "batch": {"size": 3,
                                              "trees": list("abc")}}
        state = {"results": {"r": {"tree": "c", "ok": verdict}}}
        return m.batch_holds(last, number, tree, prs, state)

    def queue(self, *heads):
        return [{"number": 10 + k, "headRefOid": h}
                for k, h in enumerate(heads)]

    def test_a_member_merges_while_its_batch_holds(self):
        self.assertTrue(self.holds(self.queue("new", "h1", "h2")))
        self.assertTrue(self.holds(self.queue("x", "new", "h2")[1:],
                                   tree="b", number=11))

    def test_not_once_its_batch_breaks(self):
        self.assertFalse(self.holds(self.queue("new", "h1", "pushed")))
        self.assertFalse(self.holds(self.queue("new", "h1")))
        self.assertFalse(self.holds(self.queue("new", "h1", "h2"),
                                    verdict=False))
        self.assertFalse(self.holds(self.queue("new", "h1", "h2"),
                                    tree="other"))
        self.assertFalse(m.batch_holds({}, 10, "a", [], {}))


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

    def covered(self):
        return {SLUG + "/commits/": [status(
            description="green: covered by batch #6 (tested as 1234567890)")]}

    def test_a_batch_is_written_with_the_predictions(self):
        m.configure(project(test_ahead={"batch": 2}))
        record, lines = self.tick([self.item(), self.other_pr()],
                                  conclusions=(None,), live=True)
        ahead = self.ahead_json()
        self.assertEqual(ahead["batch"], {
            "size": 2, "trees": [e["tree"] for e in ahead["entries"]]})
        self.assertEqual(record["batch"], [5, 6])
        top = ahead["entries"][1]
        record, lines = self.tick([self.item(), self.other_pr()],
                                  conclusions=(None,), live=True,
                                  check_state={"results": {top["sha"]: {
                                      "tree": top["tree"], "ok": True}}})
        line = "BATCH #5..#6: top %s green, merging 2" % top["sha"][:10]
        self.assertIn(line, lines)
        self.assertEqual(self.ahead_json()["batch"]["said"], line)
        # Said once.
        _, lines = self.tick([self.item(), self.other_pr()],
                             conclusions=(None,), live=True, minutes=1,
                             check_state={"results": {top["sha"]: {
                                 "tree": top["tree"], "ok": True}}})
        self.assertNotIn(line, lines)

    def test_a_red_top_splits_the_batch(self):
        m.configure(project(test_ahead={"batch": 2}))
        self.tick([self.item(), self.other_pr()], conclusions=(None,),
                  live=True)
        top = self.ahead_json()["entries"][1]
        record, lines = self.tick(
            [self.item(), self.other_pr()], conclusions=(None,), live=True,
            check_state={"results": {top["sha"]: {"tree": top["tree"],
                                                  "ok": False}}})
        self.assertIn("BATCH #5..#6: top red, checking #5..#5", lines)
        self.assertEqual(record["batch"], [5])
        self.assertEqual(self.ahead_json()["batch"]["size"], 1)

    def test_without_a_batch_nothing_is_said_of_one(self):
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,), live=True)
        self.assertNotIn("batch", self.ahead_json())
        self.assertNotIn("batch", record)

    def test_a_member_merges_only_while_its_batch_holds(self):
        m.configure(project(test_ahead={"batch": 2}))
        six = self.other_pr()
        self.tick([self.item(), six], conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        green = {"results": {top["sha"]: {"tree": top["tree"], "ok": True}}}
        record, _ = self.tick([self.item(), six], extra=self.covered(),
                              check_state=green, minutes=1)
        self.assertEqual(record["decisions"][0]["action"], "MERGE")
        self.assertIs(record["decisions"][0]["covered"], True)
        # #6 has a new head: the batch's run no longer stands for #5.
        record, _ = self.tick([self.item(), dict(six, headRefOid="0" * 40)],
                              extra=self.covered(), check_state=green,
                              minutes=1)
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        self.assertEqual(record["decisions"][0]["reason"],
                         m.BATCH_WAIT_REASON)

    def test_a_covered_green_without_its_batch_waits(self):
        m.configure(project(test_ahead={"batch": 2}))
        record, _ = self.tick([self.item()], extra=self.covered())
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        # A run of its own tree, since, lets it go.
        tree = run_git(self.repo.path, "rev-parse", self.head + "^{tree}")
        record, _ = self.tick([self.item()], extra=self.covered(),
                              check_state={"results": {"x": {
                                  "tree": tree, "ok": True}}})
        self.assertEqual(record["decisions"][0]["action"], "MERGE")
        # Without batches the description means nothing.
        m.configure(project(test_ahead={}))
        record, _ = self.tick([self.item()], extra=self.covered())
        self.assertEqual(record["decisions"][0]["action"], "MERGE")

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
        self.assertEqual(m.IN_FLIGHT, ("UPDATE", "RESOLVE", "RESTACK",
                                       "STACK-UPDATE", "WAIT"))
        for action in m.IN_FLIGHT:
            with self.subTest(action=action):
                self.assertEqual(self.keeps_place(action), 6)

    def locked(self, *entries):
        os.makedirs(os.path.join(self.tmp.name, "state"), exist_ok=True)
        m.save_json(os.path.join(self.tmp.name, "state", m.AHEAD_FILE),
                    {"at": "2026-10-07T11:59:00Z", "entries": [
                        {"pr": n, "head": h} for n, h in entries]})

    def test_those_predicted_keep_their_order_ahead_of_older_ones(self):
        # #5 is older, but #7 and #6 were predicted, in that order: an
        # older one approved again goes behind them, not between.
        six, seven = self.other_pr(), self.other_pr(7)
        self.locked((7, seven["headRefOid"]), (6, six["headRefOid"]))
        record, _ = self.tick([self.item(), six, seven],
                              conclusions=(None,), live=True)
        self.assertEqual(record["queue"], [7, 6, 5])

    def test_a_new_head_loses_its_place(self):
        six = self.other_pr()
        self.locked((6, "0" * 40))
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True)
        self.assertEqual(record["queue"], [5, 6])

    def test_one_newly_urgent_goes_behind_those_predicted(self):
        m.configure(urgent_project(test_ahead={}))
        six, seven = self.other_pr(), self.other_pr(7)
        self.locked((6, six["headRefOid"]))
        labelled(seven, "Priority:urgent")
        record, _ = self.tick([self.item(), six, seven],
                              conclusions=(None,), live=True)
        self.assertEqual(record["queue"], [6, 7, 5])

    def test_locked_order_reads_what_it_can(self):
        q = [pr(5, head="5" * 40), pr(6, head="6" * 40)]
        for junk in (None, [], {"entries": "x"}, {"entries": [1, None]}):
            with self.subTest(junk=junk):
                os.makedirs(os.path.join(self.tmp.name, "s"), exist_ok=True)
                m.save_json(os.path.join(self.tmp.name, "s", m.AHEAD_FILE),
                            junk)
                self.assertEqual(m.locked_order(
                    os.path.join(self.tmp.name, "s"), q), {})
        self.assertEqual(m.locked_order(os.path.join(self.tmp.name, "no"),
                                        q), {})

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


def hand_back(number, at, head="h" * 40, reason="red", to="author",
              result="done"):
    """A tick record holding one HAND-BACK decision."""
    d = {"action": "HAND-BACK", "pr": number, "head": head, "reason": reason,
         "to": to}
    if result is not None:
        d["result"] = result
    return {"at": at, "decisions": [d]}


class RecentRecords(unittest.TestCase):

    def write(self, records, raw=""):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        path = os.path.join(tmp.name, "merger.jsonl")
        with open(path, "w") as handle:
            for r in records:
                handle.write(json.dumps(r) + "\n")
            handle.write(raw)
        return path

    def test_only_records_since_the_time_oldest_first(self):
        path = self.write([{"at": T0, "n": 0}, {"at": T1, "n": 1},
                           {"at": T2, "n": 2}])
        got = m.recent_records(path, m.parse_time(T1))
        self.assertEqual([r["n"] for r in got], [1, 2])

    def test_read_from_the_end_in_small_blocks(self):
        records = [{"at": "2026-10-07T%02d:00:00Z" % h, "pad": "x" * 50}
                   for h in range(24)]
        path = self.write(records)
        got = m.recent_records(path, m.parse_time("2026-10-07T20:00:00Z"),
                               block=37)
        self.assertEqual([r["at"][11:13] for r in got],
                         ["20", "21", "22", "23"])
        got = m.recent_records(path, m.parse_time(T0), block=37)
        self.assertEqual(len(got), 14)
        got = m.recent_records(path, m.parse_time("2026-10-06T00:00:00Z"),
                               block=37)
        self.assertEqual(len(got), 24)

    def test_a_bad_line_or_no_file_is_no_record(self):
        path = self.write([{"at": T1}], raw="{not json\n")
        self.assertEqual(len(m.recent_records(path, m.parse_time(T0))), 1)
        self.assertEqual(m.recent_records(path + ".none", m.parse_time(T0)),
                         [])


class HandBacks(unittest.TestCase):

    def test_distinct_heads_and_reasons_count_once_each(self):
        records = [hand_back(5, T0), hand_back(5, T1, result="already said"),
                   hand_back(5, T1, reason="conflict"),
                   hand_back(5, T2, head="g" * 40), hand_back(6, T2)]
        self.assertEqual(m.hand_backs(records), {5: 3, 6: 1})

    def test_a_hand_back_to_the_reviewer_does_not_count(self):
        records = [hand_back(5, T0, to="reviewer",
                             reason="its tree is not the clean merge of its "
                             "parents")]
        self.assertEqual(m.hand_backs(records), {})

    def test_a_refused_hand_back_does_not_count(self):
        self.assertEqual(m.hand_backs([hand_back(5, T0,
                                                 result="refused: 502")]), {})

    def test_a_dry_runs_hand_back_counts(self):
        self.assertEqual(m.hand_backs([hand_back(5, T0, result=None)]), {5: 1})

    def test_a_head_github_lost_does_not_count(self):
        records = [hand_back(5, T0, reason=m.LOST_HEAD_REASON),
                   hand_back(6, T0, reason=m.LOST_HEAD_REASON)]
        self.assertEqual(m.hand_backs(records), {})
        self.assertEqual(m.yields_spent(records), {})

    def test_other_decisions_do_not_count(self):
        records = [{"at": T0, "decisions": [
            {"action": a, "pr": 5, "head": "h" * 40, "to": "author"}
            for a in ("WAIT", "UPDATE", "SKIP", "MERGE")]},
            {"at": T0, "held": {"5": "conflicts with #4, ahead of it in "
                                     "the queue"}, "decisions": []},
            {"at": T0, "stalled": {"pr": 5}, "decisions": []}]
        self.assertEqual(m.hand_backs(records), {})


class YieldPlaces(unittest.TestCase):

    def setUp(self):
        m.configure(project(yield_after_hand_back=2))
        self.addCleanup(m.configure, project())

    def numbers(self, queue, counts, everyone=None, keep=None):
        got, moved = m.yield_places(queue, everyone or queue, counts, keep)
        return [p["number"] for p in got], moved

    def test_one_hand_back_yields_n_places(self):
        queue = [pr(n) for n in (1, 2, 3, 4)]
        self.assertEqual(self.numbers(queue, {1: 1}),
                         ([2, 3, 1, 4], {1: 2}))

    def test_k_hand_backs_yield_n_times_k_capped(self):
        m.configure(project(yield_after_hand_back=1, yield_cap=2))
        queue = [pr(n) for n in range(1, 7)]
        self.assertEqual(self.numbers(queue, {1: 2})[0], [2, 3, 1, 4, 5, 6])
        self.assertEqual(self.numbers(queue, {1: 5})[0], [2, 3, 1, 4, 5, 6])

    def test_not_past_the_end(self):
        queue = [pr(n) for n in (1, 2)]
        self.assertEqual(self.numbers(queue, {1: 3}), ([2, 1], {1: 1}))
        self.assertEqual(self.numbers([pr(1)], {1: 1}), ([1], {}))

    def test_two_yielding_keep_their_order_and_both_yield(self):
        queue = [pr(n) for n in (1, 2, 3, 4, 5)]
        self.assertEqual(self.numbers(queue, {1: 1, 2: 1}),
                         ([3, 4, 1, 2, 5], {1: 2, 2: 2}))

    def test_a_blocker_is_never_moved_behind_the_rest(self):
        # #1 is named by #9's Blocked-by, so it stays in the first group.
        queue = [pr(1), pr(2), pr(3)]
        blocked = pr(9, body="Blocked-by: #1")
        self.assertEqual(self.numbers(queue, {1: 1}, queue + [blocked]),
                         ([1, 2, 3], {}))

    def test_the_rest_yield_to_each_other(self):
        queue = [pr(1), pr(2), pr(3), pr(4)]
        blocked = pr(9, body="Blocked-by: #1")
        self.assertEqual(self.numbers(queue, {2: 1}, queue + [blocked]),
                         ([1, 3, 4, 2], {2: 2}))

    def test_the_one_in_flight_keeps_its_place(self):
        queue = [pr(n) for n in (1, 2, 3)]
        self.assertEqual(self.numbers(queue, {1: 1}, keep=1),
                         ([1, 2, 3], {}))


class YieldsSpent(unittest.TestCase):
    """A yield is spent by the merges that overtake it."""

    def setUp(self):
        m.configure(project(yield_after_hand_back=2))
        self.addCleanup(m.configure, project())

    @staticmethod
    def hand_back(pr, head="h", reason="red", queue=()):
        return {"at": "t", "queue": list(queue),
                "decisions": [{"action": "HAND-BACK", "pr": pr, "head": head,
                               "reason": reason, "to": "author",
                               "result": "done"}]}

    @staticmethod
    def merge(pr, queue):
        return {"at": "t", "queue": list(queue),
                "decisions": [{"action": "MERGE", "pr": pr,
                               "result": "done"}]}

    def test_each_merge_while_it_is_queued_is_a_place_spent(self):
        records = [self.hand_back(1),
                   self.merge(2, [2, 3, 1]), self.merge(3, [3, 4, 1])]
        self.assertEqual(m.yields_spent(records), {1: 2})

    def test_merges_while_it_is_not_a_candidate_spend_nothing(self):
        records = [self.hand_back(1), self.merge(2, [2, 3]),
                   self.merge(3, [3, 1])]
        self.assertEqual(m.yields_spent(records), {1: 1})

    def test_a_new_hand_back_starts_the_count_again(self):
        records = [self.hand_back(1), self.merge(2, [2, 1]),
                   self.hand_back(1, head="h2"), self.merge(3, [3, 1])]
        self.assertEqual(m.yields_spent(records), {1: 1})

    def test_a_refused_merge_or_ones_own_merge_spends_nothing(self):
        refused = self.merge(2, [2, 1])
        refused["decisions"][0]["result"] = "refused: 405"
        records = [self.hand_back(1), refused, self.merge(1, [1, 2])]
        self.assertEqual(m.yields_spent(records), {1: 0})

    def test_a_conflict_left_out_is_not_a_start(self):
        records = [self.hand_back(1, reason=m.CONFLICT_REASON),
                   self.merge(2, [2, 1])]
        self.assertEqual(m.yields_spent(records, conflicts=False), {})

    def test_the_oldest_candidate_reaches_the_front(self):
        """The case that found it: the oldest candidate, handed back once
        and approved again, behind a queue that never drains below
        three. Taken afresh each tick, its yield kept it third for three
        hours while fifteen others merged; spent, it goes first once two
        have."""
        queue = [pr(n) for n in (1, 2, 3, 4)]
        records = [self.hand_back(1)]
        newcomer = 5
        for _ in range(2):
            got, _ = m.yield_places(list(queue), queue, {1: 1},
                                    spent=m.yields_spent(records))
            self.assertNotEqual(got[0]["number"], 1)
            first = got[0]["number"]
            records.append(self.merge(first, [p["number"] for p in got]))
            queue = [p for p in queue if p["number"] != first]
            queue.append(pr(newcomer))
            newcomer += 1
        got, moved = m.yield_places(list(queue), queue, {1: 1},
                                    spent=m.yields_spent(records))
        self.assertEqual((got[0]["number"], moved), (1, {}))


class YieldAfterHandBack(RepoCase):
    """The tick: history read from this state directory's record."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        m.configure(project(yield_after_hand_back=1))
        self.addCleanup(m.configure, project())
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)

    def history(self, *records, name="dry-run.jsonl"):
        with open(os.path.join(self.state, name), "a") as handle:
            for r in records:
                handle.write(json.dumps(r) + "\n")

    def test_off_by_default(self):
        m.configure(project())
        self.history(hand_back(5, "2026-10-07T11:30:00Z"))
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertEqual(record["queue"], [5, 6])
        self.assertNotIn("yielded", record)

    def test_handed_back_lately_lets_the_next_go_first(self):
        self.history(hand_back(5, "2026-10-07T11:30:00Z"))
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertEqual(record["queue"], [6, 5])
        self.assertEqual(record["yielded"], {"5": 1})

    def test_a_live_tick_reads_the_live_record(self):
        self.history(hand_back(5, "2026-10-07T11:30:00Z"),
                     name="merger.jsonl")
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,), live=True)
        self.assertEqual(record["yielded"], {"5": 1})

    def test_a_hand_back_older_than_a_day_does_not_count(self):
        self.history(hand_back(5, "2026-10-06T11:59:00Z"))
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertEqual((record["queue"], record["yielded"]), ([5, 6], {}))

    def test_a_hand_back_to_the_reviewer_does_not_yield(self):
        self.history(hand_back(5, "2026-10-07T11:30:00Z", to="reviewer"))
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertEqual((record["queue"], record["yielded"]), ([5, 6], {}))

    def test_with_test_ahead_the_one_in_flight_keeps_its_place(self):
        m.configure(project(yield_after_hand_back=1, test_ahead={}))
        self.history(hand_back(5, "2026-10-07T11:30:00Z"))
        with open(os.path.join(self.state, "next.json"), "w") as handle:
            json.dump({"at": T1, "pr": 5, "action": "WAIT"}, handle)
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertEqual((record["queue"], record["yielded"]), ([5, 6], {}))


class HoldConflicting(RepoCase):
    """merge.hold_conflicting: a pull request that conflicts with one
    ahead of it in the queue waits for it, said once, instead of being
    handed back after every merge. Tick's helpers, without its tests."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        m.configure(project(hold_conflicting=True))
        self.addCleanup(m.configure, project())

    def rival(self, number=6, created=T1, files=(("f.txt", "rival\n"),)):
        """An approved pull request from main whose files conflict with
        #5's (f.txt) unless told otherwise; clean against main."""
        self.repo.branch("rival%d" % number, "origin/main")
        for name, text in files:
            sha = self.repo.commit(name, text, "rival " + name)
        self.repo.publish_pr(number, sha)
        self.repo.branch("feature", "feature")
        return pr(number, created, head=sha,
                  reviews=[review(REVIEWER, oid=sha)])

    def comments(self):
        return [(w[1], w[2]["body"]) for w in self.github.writes
                if w[1].endswith("/comments")]

    def test_the_configuration(self):
        self.assertIs(m.HOLD_CONFLICTING, True)
        m.configure(project())
        self.assertIs(m.HOLD_CONFLICTING, False)

    def test_one_conflicting_with_one_ahead_is_held_and_told_once(self):
        rival = self.rival()
        record, lines = self.tick([self.item(), rival], live=True)
        self.assertEqual(record["queue"], [5])
        self.assertEqual(record["held"],
                         {"6": "conflicts with #5, ahead of it in the queue"})
        self.assertEqual([d["pr"] for d in record["decisions"]], [5])
        self.assertEqual(record["holds"], [
            {"pr": 6, "head": rival["headRefOid"], "behind": 5,
             "result": "done"}])
        (where, body), = self.comments()
        self.assertEqual(where, SLUG + "/issues/6/comments")
        self.assertTrue(body.startswith(
            "Holding %s: it conflicts with #5" % rival["headRefOid"][:10]))
        self.assertNotIn("Not merging", body)
        self.assertTrue(body.endswith("\n\n— Merger"))
        self.assertIn("1 approved but held", lines[0])
        # Said once per head and Q.
        record, _ = self.tick([self.item(), rival], live=True, minutes=1)
        self.assertEqual(record["holds"][0]["result"], "already said")
        self.assertEqual(self.comments(), [])

    def test_a_new_head_is_told_again(self):
        rival = self.rival()
        self.tick([self.item(), rival], live=True)
        moved = self.rival(files=(("f.txt", "rival again\n"),))
        record, _ = self.tick([self.item(), moved], live=True, minutes=1)
        self.assertEqual(record["holds"][0]["result"], "done")
        self.assertEqual(len(self.comments()), 1)

    def test_nothing_is_held_without_the_key(self):
        m.configure(project())
        record, _ = self.tick([self.item(), self.rival()], live=True)
        self.assertEqual(record["held"], {})
        self.assertNotIn("holds", record)
        self.assertFalse(os.path.exists(
            os.path.join(self.state, m.HOLD_FILE)))

    def test_one_that_does_not_conflict_is_not_held(self):
        # #5 is waiting, so the tick reaches nothing behind it; the
        # queue says #6 stayed in it.
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,), live=True)
        self.assertEqual((record["queue"], record["held"], record["holds"]),
                         ([5, 6], {}, []))

    def test_the_first_in_the_queue_is_never_held(self):
        # #6 is older, so it is first, and #5 waits for it.
        rival = self.rival(created="2026-10-07T09:00:00Z")
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              live=True)
        self.assertEqual(record["queue"], [6])
        self.assertEqual(record["held"],
                         {"5": "conflicts with #6, ahead of it in the queue"})

    def test_only_ones_not_held_themselves_hold_others(self):
        # #6 conflicts with #5 on f.txt; #7 conflicts only with #6, on
        # g.txt. #6 is held, so it holds nothing: #7 can merge first.
        six = self.rival(6, files=(("f.txt", "rival\n"), ("g.txt", "6\n")))
        seven = self.rival(7, created=T2, files=(("g.txt", "7\n"),))
        record, _ = self.tick([self.item(), six, seven],
                              conclusions=(None,), live=True)
        self.assertEqual(record["queue"], [5, 7])
        self.assertEqual(sorted(record["held"]), ["6"])

    def test_once_the_one_ahead_merges_it_is_judged_as_usual(self):
        rival = self.rival()
        self.tick([self.item(), rival], live=True)
        # #5 merged: #6 is first now, and clean against main.
        record, _ = self.tick([rival], live=True, minutes=1)
        self.assertEqual(record["held"], {})
        self.assertEqual([(d["pr"], d["action"]) for d in
                          record["decisions"]], [(6, "MERGE")])
        self.assertEqual(m.load_json(os.path.join(self.state, m.HOLD_FILE),
                                     None), {"6": {"head": rival["headRefOid"],
                                                   "behind": 5}})

    def test_a_dry_tick_holds_and_says_nothing(self):
        rival = self.rival()
        record, _ = self.tick([self.item(), rival])
        self.assertIn("6", record["held"])
        self.assertEqual(record["holds"], [
            {"pr": 6, "head": rival["headRefOid"], "behind": 5}])
        self.assertFalse(os.path.exists(
            os.path.join(self.state, m.HOLD_FILE)))

    def test_a_held_one_is_not_predicted(self):
        m.configure(project(hold_conflicting=True, test_ahead={}))
        rival = self.rival()
        other = self.other_pr(7)
        record, _ = self.tick([self.item(), rival, other],
                              conclusions=(None,), live=True)
        self.assertEqual(record["ahead"], [5, 7])

    def test_nothing_waits_behind_one_that_conflicts_with_main(self):
        # #5 conflicts with main, so it goes back to its author: #6,
        # which conflicts with #5 but not with main, is not held behind
        # it, and the tick moves on to it.
        self.advance_main("f.txt", "main moved f\n")
        rival = self.rival()
        record, _ = self.tick([self.item(), rival], live=True)
        self.assertEqual(record["held"], {})
        self.assertEqual([(d["pr"], d["action"]) for d in
                          record["decisions"]][0], (5, "HAND-BACK"))
        self.assertIn(6, [d["pr"] for d in record["decisions"]])

    def test_heads_git_cannot_compare_hold_nothing(self):
        def run(args, cwd=None, env=None):
            if "merge-tree" in args:
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            return m.sh(args, cwd, env)
        self.assertEqual(m.conflicts_ahead(
            self.repo.path, [self.item(), self.rival()], run), {})


class EarlyConflicts(RepoCase):
    """merge.early_conflicts: a candidate the tick doesn't reach that
    conflicts with main is handed back now (designs/0012). Tick's and
    HoldConflicting's helpers, without their tests."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr
    rival = HoldConflicting.rival
    comments = HoldConflicting.comments

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        m.configure(project(early_conflicts=True))
        self.addCleanup(m.configure, project())

    def behind(self):
        """#6, approved, made on main's g.txt, which main then changes:
        a conflict with main, none with #5."""
        six = self.rival(files=(("g.txt", "six\n"),))
        self.advance_main("g.txt", "main moved g\n")
        return six

    def test_the_configuration(self):
        self.assertIs(m.EARLY_CONFLICTS, True)
        m.configure(project())
        self.assertIs(m.EARLY_CONFLICTS, False)

    def test_one_behind_that_conflicts_with_main_is_told_now(self):
        six = self.behind()
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True)
        acted = [(d["pr"], d["action"], d.get("early")) for d in
                 record["decisions"]]
        self.assertEqual(acted[0][0], 5)
        self.assertIn((6, "HAND-BACK", True), acted)
        (where, body), = [c for c in self.comments() if "/6/" in c[0]]
        self.assertTrue(body.startswith("Not merging %s"
                                        % six["headRefOid"][:10]))
        # Said once per approval and head.
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True, minutes=1)
        self.assertNotIn(6, [d["pr"] for d in record["decisions"]])
        self.assertEqual([c for c in self.comments() if "/6/" in c[0]], [])

    def early(self, six, said, layers=()):
        github = FakeGitHub({})
        got = m.early_conflicts(self.repo.path, self.repo.main_tip(), [six],
                                {6: m.approval_of(six)}, github, said, True,
                                tools_run(), layers)
        return got, github.writes

    def test_a_stack_layer_is_left_to_its_restack(self):
        six = self.behind()
        self.assertEqual(self.early(six, {}, layers={6}), ([], []))
        got, writes = self.early(six, {})
        self.assertEqual([d["pr"] for d in got], [6])

    def test_a_conflict_already_told_in_other_words_is_not_told_again(self):
        # A layer's restack hand-back, then this plain one, replaced each
        # other in `said`, and each was posted again every tick.
        six = self.behind()
        said = {"6": m.hand_back_key(
            m.approval_of(six), six["headRefOid"],
            m.RESTACK_REASON % (m.CONFLICT_REASON, 5, "0" * 10))}
        self.assertEqual(self.early(six, said), ([], []))
        said["6"]["reason"] = "the Merger's gate failed"
        got, _ = self.early(six, said)
        self.assertEqual([d["pr"] for d in got], [6])

    def test_one_behind_that_is_clean_is_left_alone(self):
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,), live=True)
        self.assertEqual([d["pr"] for d in record["decisions"]], [5])

    def test_without_the_key_it_waits_to_be_reached(self):
        m.configure(project())
        six = self.behind()
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True)
        self.assertNotIn(6, [d["pr"] for d in record["decisions"]])

    def test_a_dry_tick_says_nothing(self):
        six = self.behind()
        record, _ = self.tick([self.item(), six], conclusions=(None,))
        self.assertIn(6, [d["pr"] for d in record["decisions"]])
        self.assertEqual(self.comments(), [])


class YieldIgnoresConflicts(unittest.TestCase):

    def test_hand_backs_without_conflicts(self):
        records = [hand_back(5, T0, reason=m.CONFLICT_REASON),
                   hand_back(5, T1, head="g" * 40),
                   hand_back(6, T1, reason=m.CONFLICT_REASON)]
        self.assertEqual(m.hand_backs(records), {5: 2, 6: 1})
        self.assertEqual(m.hand_backs(records, conflicts=False), {5: 1})
        self.assertEqual(m.conflict_hand_backs(records), {5: 1, 6: 1})

    def test_the_configuration(self):
        m.configure(project(yield_ignores_conflicts=True))
        self.addCleanup(m.configure, project())
        self.assertIs(m.YIELD_IGNORES_CONFLICTS, True)
        m.configure(project())
        self.assertIs(m.YIELD_IGNORES_CONFLICTS, False)


class YieldIgnoringConflicts(YieldAfterHandBack):

    def test_a_conflict_does_not_yield(self):
        m.configure(project(yield_after_hand_back=1,
                            yield_ignores_conflicts=True))
        self.history(hand_back(5, "2026-10-07T11:30:00Z",
                               reason=m.CONFLICT_REASON))
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertEqual((record["queue"], record["yielded"]), ([5, 6], {}))

    def test_another_reason_still_yields(self):
        m.configure(project(yield_after_hand_back=1,
                            yield_ignores_conflicts=True))
        self.history(hand_back(5, "2026-10-07T11:30:00Z"))
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,))
        self.assertEqual(record["queue"], [6, 5])


class RightOfWay(RepoCase):
    """merge.right_of_way (designs/0002): #5, overtaken twice, holds
    back what conflicts with its clean head for a while, and goes first.
    The rivals are older, so without it they would go first."""

    OLDER = "2026-10-07T09:00:00Z"
    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr
    rival = HoldConflicting.rival
    comments = HoldConflicting.comments

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        m.configure(project(right_of_way={}))
        self.addCleanup(m.configure, project())
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)
        self.overtaken()

    def overtaken(self, times=2, name="merger.jsonl"):
        with open(os.path.join(self.state, name), "a") as handle:
            for k in range(times):
                handle.write(json.dumps(hand_back(
                    5, "2026-10-07T11:%02d:00Z" % (10 + k), head=str(k) * 40,
                    reason=m.CONFLICT_REASON)) + "\n")

    def row(self):
        return {"pr": 5, "head": self.head, "since": "2026-10-07T12:00:00Z"}

    def test_the_configuration(self):
        self.assertEqual((m.RIGHT_OF_WAY, m.ROW_AFTER, m.ROW_FOR),
                         (True, 2, datetime.timedelta(minutes=60)))
        m.configure(project(right_of_way={"after_conflicts": 3,
                                          "minutes": 30}))
        self.assertEqual((m.ROW_AFTER, m.ROW_FOR),
                         (3, datetime.timedelta(minutes=30)))
        m.configure(project())
        self.assertIs(m.RIGHT_OF_WAY, False)

    def test_off_by_default(self):
        m.configure(project())
        record, _ = self.tick([self.item(), self.rival(created=self.OLDER)],
                              live=True)
        self.assertNotIn("right_of_way", record)
        self.assertEqual(record["held"], {})
        self.assertEqual(record["queue"], [6, 5])

    def test_it_holds_what_conflicts_and_goes_first(self):
        rival = self.rival(created=self.OLDER)
        other = self.other_pr(7)
        other["createdAt"] = self.OLDER
        record, _ = self.tick([self.item(), rival, other],
                              conclusions=(None,), live=True)
        self.assertEqual(record["right_of_way"], self.row())
        self.assertEqual(record["held"],
                         {"6": "conflicts with #5, which has right of way"})
        self.assertEqual(record["queue"], [5, 7])
        self.assertEqual(record["holds"], [
            {"pr": 6, "head": rival["headRefOid"], "behind": 5,
             "right_of_way": True, "result": "done"}])
        (where, body), = self.comments()
        self.assertEqual(where, SLUG + "/issues/6/comments")
        self.assertTrue(body.startswith(
            "Holding %s: it conflicts with #5, which has right of way."
            % rival["headRefOid"][:10]))
        self.assertIn("within 60 minutes", body)
        self.assertTrue(body.endswith("\n\n— Merger"))
        record, _ = self.tick([self.item(), rival, other],
                              conclusions=(None,), live=True, minutes=1)
        self.assertEqual(record["holds"][0]["result"], "already said")
        self.assertEqual(self.comments(), [])

    def test_one_conflict_has_not_earned_it(self):
        os.remove(os.path.join(self.state, "merger.jsonl"))
        self.overtaken(1)
        record, _ = self.tick([self.item(), self.rival(created=self.OLDER)],
                              live=True)
        self.assertIsNone(record["right_of_way"])
        self.assertEqual((record["queue"], record["held"]), ([6, 5], {}))

    def test_it_holds_while_not_yet_approved_again(self):
        unapproved = pr(5, head=self.head, reviews=[])
        record, _ = self.tick([unapproved, self.rival(created=self.OLDER)],
                              live=True)
        self.assertEqual(record["right_of_way"], self.row())
        self.assertEqual(record["queue"], [])
        self.assertEqual(list(record["held"]), ["6"])

    def test_it_ends_after_the_minutes_and_the_clock_survives(self):
        rival = self.rival(created=self.OLDER)
        self.tick([self.item(), rival], conclusions=(None,), live=True)
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              live=True, minutes=59)
        self.assertEqual(record["right_of_way"], self.row())
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              live=True, minutes=60)
        self.assertIsNone(record["right_of_way"])
        self.assertEqual(record["queue"], [6, 5])

    def test_a_new_head_starts_the_clock_again(self):
        rival = self.rival(created=self.OLDER)
        self.tick([self.item(), rival], conclusions=(None,), live=True)
        head = self.repo.commit("f2.txt", "more\n", "more")
        self.repo.publish_pr(5, head)
        moved = pr(5, head=head, reviews=[])
        record, _ = self.tick([moved, rival], live=True, minutes=61)
        self.assertEqual(record["right_of_way"],
                         {"pr": 5, "head": head,
                          "since": "2026-10-07T13:01:00Z"})

    def test_not_while_its_head_conflicts_with_main(self):
        self.advance_main("f.txt", "main moved f\n")
        record, _ = self.tick([self.item(), self.rival(created=self.OLDER)],
                              live=True)
        self.assertIsNone(record["right_of_way"])
        self.assertNotIn("6", record["held"])

    def test_not_when_its_head_was_handed_back_for_anything_else(self):
        with open(os.path.join(self.state, "merger.jsonl"), "a") as handle:
            handle.write(json.dumps(hand_back(
                5, "2026-10-07T11:50:00Z", head=self.head)) + "\n")
        record, _ = self.tick([self.item(), self.rival(created=self.OLDER)],
                              conclusions=(None,), live=True)
        self.assertIsNone(record["right_of_way"])

    def test_it_never_yields(self):
        m.configure(project(right_of_way={}, yield_after_hand_back=2))
        record, _ = self.tick([self.item(), self.other_pr()],
                              conclusions=(None,), live=True)
        self.assertEqual(record["queue"], [5, 6])
        self.assertEqual(record["yielded"], {})

    def test_the_one_in_flight_stays_ahead_unless_it_conflicts(self):
        m.configure(project(right_of_way={}, test_ahead={}))
        other = self.other_pr(7)
        other["createdAt"] = self.OLDER
        with open(os.path.join(self.state, "next.json"), "w") as handle:
            json.dump({"at": T1, "pr": 7, "action": "WAIT"}, handle)
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              live=True)
        self.assertEqual(record["queue"], [7, 5])
        rival = self.rival(created=self.OLDER)
        with open(os.path.join(self.state, "next.json"), "w") as handle:
            json.dump({"at": T1, "pr": 6, "action": "WAIT"}, handle)
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              live=True, minutes=1)
        self.assertEqual(record["queue"], [5])

    def test_a_dry_tick_keeps_its_own_clock_and_says_nothing(self):
        self.overtaken(name="dry-run.jsonl")
        record, _ = self.tick([self.item(), self.rival(created=self.OLDER)])
        self.assertEqual(record["right_of_way"], self.row())
        self.assertTrue(os.path.exists(
            os.path.join(self.state, "dry-run-" + m.ROW_FILE)))
        self.assertFalse(os.path.exists(os.path.join(self.state, m.ROW_FILE)))

    def test_of_two_the_first_in_order_has_it(self):
        six = self.rival(created=self.OLDER)
        with open(os.path.join(self.state, "merger.jsonl"), "a") as handle:
            for k in range(2):
                handle.write(json.dumps(hand_back(
                    6, "2026-10-07T11:3%d:00Z" % k, head=str(k) * 40,
                    reason=m.CONFLICT_REASON)) + "\n")
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True)
        self.assertEqual(record["right_of_way"]["pr"], 6)
        self.assertEqual(record["held"],
                         {"5": "conflicts with #6, which has right of way"})


class CodeownersMatch(unittest.TestCase):

    def test_patterns(self):
        cases = [("/tools/host/", "tools/host/README.md", True),
                 ("/tools/host/", "tools/hostile.md", False),
                 ("/tools/host/", "x/tools/host/a", False),
                 ("/.agent-forge/", ".agent-forge/config.json", True),
                 ("/.github/CODEOWNERS", ".github/CODEOWNERS", True),
                 ("/.github/CODEOWNERS", ".github/CODEOWNERS.bak", False),
                 ("*.js", "web/a/b.js", True),
                 ("*.js", "web/a/b.jsx", False),
                 ("docs/*", "docs/a.md", True),
                 ("docs/*", "docs/a/b.md", True),
                 ("docs/*", "x/docs/a.md", False),
                 ("build", "a/build/x", True),
                 ("/a/**/z", "a/z", True),
                 ("/a/**/z", "a/b/c/z", True),
                 ("*", "anything/at/all", True)]
        for pattern, path, want in cases:
            self.assertIs(m.codeowners_match(pattern, path), want,
                          (pattern, path))

    def test_the_last_matching_rule_wins(self):
        rules = [("*", ["@everyone"]), ("/tools/", ["@tools"]),
                 ("/tools/free.py", [])]
        self.assertEqual(m.owners_of("a.md", rules), ["@everyone"])
        self.assertEqual(m.owners_of("tools/x", rules), ["@tools"])
        self.assertEqual(m.owners_of("tools/free.py", rules), [])
        self.assertEqual(m.owners_of("a.md", []), [])


class CodeOwners(RepoCase):
    """merge.code_owners: a candidate that changes an owned path waits
    for its owner's approval of the head."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr
    OWNER = "theowner"

    def setUp(self):
        super().setUp()
        self.repo.branch("main")
        os.makedirs(os.path.join(self.repo.path, ".github"))
        self.repo.commit(".github/CODEOWNERS",
                         "# owned\n/owned/ @TheOwner\n/team.txt @org/team\n",
                         "codeowners")
        self.repo.push("main")
        self.repo.branch("feature", "feature")
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        m.configure(project(code_owners=True))
        self.addCleanup(m.configure, project())

    def owned_pr(self, number=6, reviews=None, name="owned/x.json",
                 author=AUTHOR):
        self.repo.branch("owned%d" % number, "origin/main")
        os.makedirs(os.path.join(self.repo.path, os.path.dirname(name)
                                 or "."), exist_ok=True)
        sha = self.repo.commit(name, "x\n", "owned")
        self.repo.publish_pr(number, sha)
        self.repo.branch("feature", "feature")
        self.sha = sha
        return pr(number, T1, head=sha, author=author,
                  reviews=[review(REVIEWER, oid=sha)] + (reviews or []))

    def test_the_configuration(self):
        self.assertIs(m.CODE_OWNERS, True)
        m.configure(project())
        self.assertIs(m.CODE_OWNERS, False)

    def test_off_by_default(self):
        m.configure(project())
        record, _ = self.tick([self.owned_pr()], live=True)
        self.assertEqual(record["held"], {})
        self.assertEqual([d["action"] for d in record["decisions"]],
                         ["MERGE"])

    def test_an_owned_path_waits_for_its_owner(self):
        record, _ = self.tick([self.item(), self.owned_pr()],
                              conclusions=(None,), live=True)
        self.assertEqual(record["held"], {
            "6": "waits for code owner @TheOwner: owned/x.json"})
        self.assertEqual(record["queue"], [5])
        # Held, not handed back: nothing is said.
        self.assertEqual(self.github.writes, [])

    def test_the_owners_approval_of_the_head_lets_it_merge(self):
        item = self.owned_pr()
        item["reviews"].append(review(self.OWNER, oid=self.sha, at=T2))
        record, _ = self.tick([item], live=True)
        self.assertEqual(record["held"], {})
        self.assertEqual([d["action"] for d in record["decisions"]],
                         ["MERGE"])

    def test_an_approval_that_does_not_cover_the_head_is_not_enough(self):
        item = self.owned_pr()
        item["reviews"].append(review(self.OWNER, oid=self.approved, at=T2))
        record, _ = self.tick([item], live=True)
        self.assertIn("6", record["held"])

    def test_a_later_change_request_takes_it_back(self):
        item = self.owned_pr()
        item["reviews"] += [review(self.OWNER, oid=self.sha, at=T2),
                            review(self.OWNER, "CHANGES_REQUESTED",
                                   oid=self.sha, at=T3)]
        record, _ = self.tick([item], live=True)
        self.assertIn("6", record["held"])

    def test_a_later_comment_leaves_the_approval_standing(self):
        item = self.owned_pr()
        item["reviews"] += [review(self.OWNER, oid=self.sha, at=T2),
                            review(self.OWNER, "COMMENTED", oid=self.sha,
                                   at=T3)]
        # (The tick holds it anyway, for speaking after the approval.)
        rules = m.codeowners_rules(self.repo.path, "refs/remotes/origin/main")
        self.assertIsNone(m.code_owner_wait(self.repo.path, item, rules,
                                            self.repo.main_tip()))

    def test_an_owners_own_pull_request_needs_nobody_else(self):
        record, _ = self.tick([self.owned_pr(author=self.OWNER)], live=True)
        self.assertEqual(record["held"], {})

    def test_a_team_owner_cannot_be_checked(self):
        item = self.owned_pr(name="team.txt")
        item["reviews"].append(review("org", oid=self.sha, at=T2))
        record, _ = self.tick([item], live=True)
        self.assertEqual(record["held"],
                         {"6": "waits for code owner @org/team: team.txt"})

    def test_unowned_paths_and_no_codeowners_pass(self):
        record, _ = self.tick([self.item()], live=True)
        self.assertEqual(record["held"], {})
        self.assertEqual(m.codeowners_rules(self.repo.path, "HEAD~5"), [])

    def test_paths_that_cannot_be_listed_wait(self):
        def run(args, cwd=None, env=None):
            if "diff" in args:
                return subprocess.CompletedProcess(args, 128, "", "fatal")
            return m.sh(args, cwd, env)
        rules = m.codeowners_rules(self.repo.path, "refs/remotes/origin/main")
        self.assertIn("can't be listed", m.code_owner_wait(
            self.repo.path, self.item(), rules, self.repo.main_tip(), run))

class OthersText(unittest.TestCase):
    """The hand-back's list of the author's other open pull requests."""

    def view(self):
        return {"queue": [7, 5], "held": {"8": "stacked on #7"},
                "prs": [pr(5), pr(7), pr(8), pr(9, draft=True),
                        pr(10, reviews=[]), pr(11, author="someone-else")]}

    def test_each_other_one_of_the_author_with_where_it_stands(self):
        self.assertEqual(m.others_text(5, self.view()), (
            "Your other open pull requests, oldest first:\n\n"
            "* #7: number 1 of 2 in the queue\n"
            "* #8: held: stacked on #7\n"
            "* #9: a draft\n"
            "* #10: not approved at its head yet\n\n"
            "Fix the one nearest the front first. Two that change the same "
            "lines merge more easily as a stack, one based on the other's "
            "branch, than side by side.\n\n"))

    def test_nothing_when_the_author_has_no_other(self):
        self.assertEqual(m.others_text(11, self.view()), "")
        self.assertEqual(m.others_text(5, {}), "")

    def test_only_a_conflict_gets_the_advice(self):
        decision = {"to": "author", "head": "a" * 40, "pr": 5,
                    "reason": "the ci workflow failed"}
        self.assertNotIn(m.CONFLICT_ADVICE, m.hand_back_text(decision, "R"))
        decision["reason"] = m.CONFLICT_REASON
        self.assertIn(m.CONFLICT_ADVICE, m.hand_back_text(decision, "R"))


if __name__ == "__main__":
    unittest.main()


class StackCoversHead(RepoCase):
    """designs/0025: a clean merge of a merged lower layer's head covers
    the head of the layer above it; a hand-resolved one doesn't."""

    def setUp(self):
        super().setUp()
        self.repo.branch("upper", "feature")
        self.approved = self.repo.commit("u.txt", "upper\n", "upper")

    def lower_change(self, name, text):
        self.repo.branch("feature", "feature")
        lower = self.repo.commit(name, text, "lower " + name)
        self.repo.branch("upper", "upper")
        return lower

    def test_a_clean_merge_of_the_merged_layer_below(self):
        lower = self.lower_change("l.txt", "lower\n")
        head = self.repo.merge("feature")
        self.assertEqual(m.covers_head(
            self.repo.path, self.approved, head, "refs/remotes/origin/main",
            below=(lower,)), (True, None))
        covered, why = self.covers(head)
        self.assertFalse(covered)
        self.assertIn("merges nothing from main", why)

    def test_a_hand_resolved_merge_of_the_layer_below(self):
        lower = self.lower_change("u.txt", "lower's own\n")
        got = subprocess.run(["git", "-C", self.repo.path, "merge", "-q",
                              "--no-edit", "feature"], capture_output=True,
                             text=True, env=dict(
                                 os.environ, GIT_AUTHOR_NAME="t",
                                 GIT_AUTHOR_EMAIL="t@t",
                                 GIT_COMMITTER_NAME="t",
                                 GIT_COMMITTER_EMAIL="t@t"))
        self.assertNotEqual(got.returncode, 0)
        with open(os.path.join(self.repo.path, "u.txt"), "w") as handle:
            handle.write("both\n")
        run_git(self.repo.path, "add", "u.txt")
        run_git(self.repo.path, "commit", "-q", "--no-edit")
        head = run_git(self.repo.path, "rev-parse", "HEAD")
        covered, why = m.covers_head(
            self.repo.path, self.approved, head, "refs/remotes/origin/main",
            below=(lower,))
        self.assertFalse(covered)
        self.assertIn("not the clean merge", why)


class Stacks(RepoCase):
    """designs/0025: with merge.stacks, a stack merges as a unit."""

    def setUp(self):
        super().setUp()
        m.configure(project(stacks={}))
        self.addCleanup(m.configure, project())
        self.state = os.path.join(self.tmp.name, "state")
        # #5 is the bottom layer, on main; #6 is stacked on #5's branch.
        self.lower = self.approved
        self.repo.publish_pr(5, self.lower)
        self.repo.branch("upper", "feature")
        self.upper = self.repo.commit("u.txt", "upper\n", "upper")
        self.repo.publish_pr(6, self.upper)
        self.repo.branch("feature", "feature")

    def bottom(self):
        return pr(5, head=self.lower,
                  reviews=[review(REVIEWER, oid=self.lower)])

    def layer(self, base="b5", reviews=None, head=None):
        return pr(6, T1, base=base, head=head or self.upper, reviews=[
            review(REVIEWER, oid=self.upper)] if reviews is None else reviews)

    def tick(self, prs, live=True, minutes=0):
        github = FakeGitHub(answers(prs=prs))
        self.github = github
        lines = []
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        base = tools_run()

        def run(args, cwd=None, env=None):
            # A push to GitHub goes to `origin`.
            if "push" in args:
                args = [self.repo.origin if a.startswith("https://github.com/")
                        else a for a in args]
            return base(args, cwd, env)
        record = m.tick(self.repo.path, self.state, github, run,
                        lines.append,
                        now=lambda: start + datetime.timedelta(minutes=minutes),
                        live=live)
        if not live:
            self.assertEqual(github.writes, [])
        return record, lines

    def squash_bottom_onto_main(self):
        """What GitHub's squash merge of #5 leaves on main."""
        self.repo.branch("main", "main")
        run_git(self.repo.path, "merge", "-q", "--squash", "feature")
        run_git(self.repo.path, "commit", "-q", "-m", "#5, squashed")
        self.repo.push("main")
        self.repo.branch("feature", "feature")

    def stack_file(self):
        return m.load_json(os.path.join(self.state, m.STACK_FILE), None)

    def merge_bottom(self):
        record, _ = self.tick([self.bottom(), self.layer()])
        self.assertEqual([(d["pr"], d["action"], d.get("result"))
                          for d in record["decisions"]],
                         [(5, "MERGE", "done")])
        self.squash_bottom_onto_main()
        return record

    def test_held_as_stacked_only_with_the_setting(self):
        record, _ = self.tick([self.bottom(), self.layer()], live=False)
        self.assertEqual(record["held"], {"6": "stacked on #5"})
        m.configure(project())
        record, _ = self.tick([self.bottom(), self.layer()], live=False)
        self.assertEqual(record["held"], {"6": "based on b5, not main"})

    def test_a_merge_records_the_layers_above(self):
        record = self.merge_bottom()
        self.assertEqual(record["stack"], [{"pr": 6, "after": 5}])
        self.assertEqual(self.stack_file(), {
            "at": "2026-10-07T12:00:00Z", "heads": [self.lower], "run": 1,
            "pending": [{"pr": 6, "after": 5, "branch": "b5",
                         "head": self.lower}]})
        self.assertTrue(m.moved_a_stack(record))

    def test_the_next_tick_retargets_the_layer(self):
        self.merge_bottom()
        record, lines = self.tick([self.layer()], live=False, minutes=1)
        self.assertEqual(record["decisions"], [{
            "action": "RETARGET", "pr": 6, "head": self.upper, "after": 5}])
        self.assertIn("STACK #6: would be retargeted to main after #5 "
                      "merged; 0 above", lines)
        record, lines = self.tick([self.layer()], minutes=1)
        self.assertEqual(self.github.writes, [
            ("PATCH", SLUG + "/pulls/6", {"base": "main"})])
        self.assertEqual(record["decisions"][0]["result"], "done")
        self.assertIn("STACK #6: retargeted to main after #5 merged; "
                      "0 above", lines)
        self.assertTrue(m.moved_a_stack(record))

    def other(self, number=4):
        """An approved pull request on main, opened before the stack."""
        self.repo.branch("other", "origin/main")
        sha = self.repo.commit("o.txt", "other\n", "other")
        self.repo.publish_pr(number, sha)
        self.repo.branch("feature", "feature")
        return pr(number, "2026-10-07T09:00:00Z", head=sha,
                  reviews=[review(REVIEWER, oid=sha)])

    def test_a_layer_blocked_by_the_merged_layer_below_goes_on(self):
        # GitHub went on listing #5 as open just after it merged, and a
        # layer saying `Blocked-by: #5` fell out of the stack for good.
        self.merge_bottom()
        layer = self.layer()
        layer["body"] = "Blocked-by: #5"
        github = FakeGitHub(dict(answers(prs=[layer]), **{"--paginate": [5]}))
        self.github = github
        start = datetime.datetime(2026, 10, 7, 12, 1, 0,
                                  tzinfo=datetime.timezone.utc)
        record = m.tick(self.repo.path, self.state, github, tools_run(),
                        lambda line: None, now=lambda: start, live=True)
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(6, "RETARGET")])

    def test_the_retargeted_layer_goes_first(self):
        self.merge_bottom()
        older = self.other()
        record, _ = self.tick([older, self.layer("main")], minutes=2)
        self.assertEqual(record["queue"], [6, 4])
        # Behind main by #5's squash, which merges cleanly into it.
        self.assertEqual((record["decisions"][0]["pr"],
                          record["decisions"][0]["action"]), (6, "UPDATE"))
        self.assertEqual(record["stack"], [{"pr": 6, "after": 5}])

    def test_max_run_lets_the_queue_have_a_turn(self):
        m.configure(project(stacks={"max_run": 1}))
        self.merge_bottom()
        older = self.other()
        record, _ = self.tick([older, self.layer("main")], live=False,
                              minutes=2)
        self.assertEqual(record["queue"], [4, 6])

    def test_a_layer_that_is_not_eligible_ends_the_stack(self):
        self.merge_bottom()
        record, lines = self.tick([self.layer(reviews=[])], minutes=1)
        self.assertEqual(self.github.writes, [])
        self.assertEqual(record["held"], {
            "6": "stacked on #5, which merged, but no Reviewer approval"})
        self.assertNotIn("stack", record)
        self.assertIsNone(self.stack_file())

    def test_a_held_layer_says_the_one_below_merged(self):
        self.merge_bottom()
        spoken = self.layer()
        spoken["comments"] = [comment(PEER, T3)]
        record, _ = self.tick([spoken], minutes=1)
        self.assertNotIn("PATCH", [w[0] for w in self.github.writes])
        self.assertEqual(record["held"]["6"],
                         "stacked on #5, which merged, but comment by %s "
                         "at %s, after the approval" % (PEER, T3))
        self.assertIsNone(self.stack_file())

    def test_a_stale_or_closed_stack_is_forgotten(self):
        self.merge_bottom()
        record, _ = self.tick([], minutes=1)
        self.assertNotIn("stack", record)
        self.assertIsNone(self.stack_file())
        m.save_json(os.path.join(self.state, m.STACK_FILE), {
            "at": "2026-10-07T12:00:00Z", "heads": [self.lower], "run": 1,
            "pending": [{"pr": 6, "after": 5, "branch": "b5"}]})
        record, _ = self.tick([self.layer()], minutes=60 * 25)
        self.assertEqual(record["decisions"], [])
        self.assertEqual(record["held"], {"6": "based on b5, not main"})
        self.assertIsNone(self.stack_file())

    def test_a_dry_run_merge_records_nothing(self):
        self.tick([self.bottom(), self.layer()], live=False)
        self.assertIsNone(self.stack_file())

    # The amendment's case: #6 was approved on an older head of #5, #5
    # gained a commit before it merged, and main holds #5 as one squash.
    LINES = ["1", "X", "3", "4", "5", "6", "7", "8", "9"]

    def lines(self, **at):
        return "".join(at.get("l%d" % n, line) + "\n"
                       for n, line in enumerate(self.LINES, 1))

    def lower_moves_on(self, upper, later, new=False):
        """main has l.txt; #5 changes it, #6 (on it) makes it `upper`,
        then #5 makes it `later`; both published, and #5 merged and
        squashed. (Were l.txt new in #5, main and #6 would add it
        differently, and only a merge against #5's head would help.)"""
        if not new:
            self.repo.branch("main", "main")
            self.repo.commit("l.txt", self.lines(l2="2"), "main l.txt")
            self.repo.push("main")
            self.repo.branch("feature", "feature")
            self.repo.merge("origin/main")
        self.repo.branch("feature", "feature")
        first = self.repo.commit("l.txt", self.lines(), "lower l.txt")
        self.repo.branch("upper", first)
        self.upper = self.repo.commit("l.txt", upper, "upper l.txt")
        self.repo.publish_pr(6, self.upper)
        self.repo.branch("feature", "feature")
        self.lower = self.repo.commit("l.txt", later, "lower again")
        self.repo.publish_pr(5, self.lower)
        self.merge_bottom()
        record, _ = self.tick([self.layer()], minutes=1)
        self.assertEqual(record["decisions"][0]["action"], "RETARGET")

    def test_a_layer_main_conflicts_with_is_restacked(self):
        # #5's later line 3 is next to its own line 2, which #6 also
        # holds at its older version: main conflicts, the restack doesn't.
        self.lower_moves_on(self.lines(l8="U"), self.lines(l3="Y"))
        record, lines = self.tick([self.layer("main")], minutes=2)
        self.assertEqual(record["decisions"], [{
            "action": "RESTACK", "pr": 6, "head": self.upper, "after": 5,
            "below": self.lower, "branch": "b6", "result": "done"}])
        self.assertEqual(self.github.writes[0], (
            "POST", SLUG + "/merges", {
                "base": "b6", "head": self.lower,
                "commit_message": "Merge #5's final head %s into b6"
                                  % self.lower[:10]}))
        note = self.github.writes[1][2]["body"]
        self.assertIn("Merged #5's final head %s into this" % self.lower[:10],
                      note)
        self.assertIn("no review is needed again", note)
        self.assertIn("STACK #6: merged #5's head %s into it"
                      % self.lower[:12], lines)
        # What GitHub's merge makes: covered by the approval, and main
        # now merges into it cleanly.
        self.repo.branch("upper", "upper")
        head = self.repo.merge("feature")
        self.repo.publish_pr(6, head)
        record, _ = self.tick([self.layer("main", head=head)], minutes=3)
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(6, "UPDATE")])

    def test_a_restack_is_tried_once_per_head(self):
        self.lower_moves_on(self.lines(l8="U"), self.lines(l3="Y"))
        self.tick([self.layer("main")], minutes=2)
        record, _ = self.tick([self.layer("main")], minutes=3)
        self.assertEqual([(d["action"], d.get("result"))
                          for d in record["decisions"]], [
            ("RESTACK", "failed: already tried at this head"),
            ("HAND-BACK", "done")])
        self.assertEqual(record["decisions"][1]["reason"], (
            "%s, and the Merger could not merge #5's final head %s into it"
            % (m.CONFLICT_REASON, self.lower[:10])))
        self.assertNotIn("/merges", self.github.writes[0][1])

    def test_a_restack_that_conflicts_goes_to_the_author(self):
        # #5 later changes the line #6 changed: a real conflict.
        self.lower_moves_on(self.lines(l8="U"), self.lines(l8="V"))
        record, _ = self.tick([self.layer("main")], minutes=2)
        self.assertEqual([(d["action"], d.get("to"), d.get("result"))
                          for d in record["decisions"]],
                         [("HAND-BACK", "author", "done")])
        self.assertEqual(record["decisions"][0]["reason"],
                         m.RESTACK_REASON % (m.CONFLICT_REASON, 5,
                                             self.lower[:10]))
        body = self.github.writes[0][2]["body"]
        self.assertIn("Merge #5's final head, %s, into the branch first, "
                      "then `main`." % self.lower[:10], body)
        self.assertNotIn("/merges", "".join(w[1] for w in self.github.writes))

    def github_restacks(self):
        """What GitHub's merges API makes of the RESTACK: #6 holds #5's
        final head, on GitHub's branch b6 too."""
        self.repo.branch("upper", "upper")
        head = self.repo.merge("feature")
        self.repo.publish_pr(6, head)
        run_git(self.repo.path, "push", "-q", "-f", "origin",
                "%s:refs/heads/b6" % head)
        return head

    def test_a_layer_is_merged_with_main_against_the_layer_below(self):
        # #5 adds l.txt and #6 changes it: main and #6 each add it, so a
        # plain merge conflicts even once #6 holds all of #5.
        m.RESOLVE_IDENTITY = IDENTITY
        self.addCleanup(setattr, m, "RESOLVE_IDENTITY", None)
        self.lower_moves_on(self.lines(l8="U"), self.lines(l3="Y"), new=True)
        record, _ = self.tick([self.layer("main")], minutes=2)
        self.assertEqual(record["decisions"][0]["action"], "RESTACK")
        head = self.github_restacks()
        record, lines = self.tick([self.layer("main", head=head)], minutes=3)
        decision = record["decisions"][0]
        self.assertEqual((decision["action"], decision["result"]),
                         ("STACK-UPDATE", "done"))
        self.assertIn("STACK #6: merged main into it against #5's head %s"
                      % self.lower[:12], lines)
        pushed = run_git(self.repo.origin, "rev-parse", "refs/heads/b6")
        self.assertEqual(run_git(self.repo.origin, "rev-list", "--parents",
                                 "-n", "1", pushed).split()[1:],
                         [head, self.repo.main_tip()])
        self.assertEqual(run_git(self.repo.origin, "show", pushed + ":l.txt"),
                         self.lines(l3="Y", l8="U").strip())
        self.assertIn("no review is needed again",
                      self.github.writes[-1][2]["body"])
        # The approval of #6 covers the pushed head: it merges.
        self.repo.publish_pr(6, pushed)
        record, _ = self.tick([self.layer("main", head=pushed)], minutes=4)
        self.assertEqual([(d["action"], d.get("result"))
                          for d in record["decisions"]], [("MERGE", "done")])

    def test_never_against_a_head_the_layer_lacks(self):
        # Without the restack, the base would undo #5's line 3 silently.
        self.lower_moves_on(self.lines(l8="U"), self.lines(l3="Y"), new=True)
        main_ref = "refs/remotes/origin/main"
        self.assertIsNone(m.based_merge(self.repo.path, main_ref,
                                        self.upper, self.lower))
        head = self.github_restacks()
        self.assertIsNotNone(m.based_merge(self.repo.path, main_ref, head,
                                           self.lower))
        # Nor when main doesn't hold #5's squash.
        self.assertFalse(m.squashed(self.repo.path, self.upper, main_ref))
        self.assertIsNone(m.based_merge(self.repo.path, main_ref, head,
                                        self.upper))

    def test_a_dry_run_says_it_would_restack(self):
        self.lower_moves_on(self.lines(l8="U"), self.lines(l3="Y"))
        record, lines = self.tick([self.layer("main")], live=False,
                                  minutes=2)
        self.assertEqual(record["decisions"][0]["action"], "RESTACK")
        self.assertIn("STACK #6: would have merged #5's head %s into it"
                      % self.lower[:12], lines)


# designs/0026: settings for a project with stricter holds.

TA = "2026-10-07T10:10:00Z"
TB = "2026-10-07T10:20:00Z"


class Configure0026(unittest.TestCase):

    def tearDown(self):
        m.configure(project())

    def test_all_off_by_default(self):
        self.assertEqual((m.RESOLVE, m.HOLD_LABELS, m.BLOCKED_BY), ((), (),
                                                                    "body"))
        self.assertEqual((m.SAID_AUTHOR, m.SAID_APPROVALS, m.UPDATE_ONCE),
                         (False, False, False))
        self.assertEqual((m.CI_NEVER_STARTED, m.QUEUED_STALL_AFTER,
                          m.READ_AS), (None, None, None))

    def test_each_setting(self):
        p = project(resolve=[{"paths": ["g.txt"], "command": ["tools/gen"],
                              "check": ["tools/gen", "--check"]}],
                    hold_labels=["Hold"], blocked_by="everywhere",
                    said_since={"author": True, "approvals": True,
                                "quiet": ["Helper"]},
                    update_once=True, ci_never_started=20,
                    queued_stall_minutes=90, read_as="Helper")
        m.configure(p)
        self.assertEqual(m.RESOLVE, ({"paths": frozenset(["g.txt"]),
                                      "command": ("tools/gen",),
                                      "check": ("tools/gen", "--check")},))
        self.assertEqual((m.HOLD_LABELS, m.BLOCKED_BY), (("Hold",),
                                                         "everywhere"))
        self.assertTrue(m.SAID_AUTHOR and m.SAID_APPROVALS and m.UPDATE_ONCE)
        self.assertEqual(m.QUIET_LOGINS, {"mergerwidgetagent",
                                          p.slug("Helper")})
        self.assertEqual((m.CI_NEVER_STARTED, m.QUEUED_STALL_AFTER),
                         (datetime.timedelta(minutes=20),
                          datetime.timedelta(minutes=90)))
        self.assertEqual(m.READ_AS, "Helper")


class HoldLabels(unittest.TestCase):

    def tearDown(self):
        m.configure(project())

    def test_a_listed_label_holds(self):
        m.configure(project(hold_labels=["Hold", "Status:waiting-owner"]))
        approval, why = m.eligible(labelled(pr(), "bug", "Hold"), set())
        self.assertIsNone(approval)
        self.assertEqual(why, "labelled Hold")

    def test_other_labels_and_flat_lists_are_read(self):
        m.configure(project(hold_labels=["Hold"]))
        self.assertIsNotNone(m.eligible(labelled(pr(), "bug"), set())[0])
        flat = dict(pr(), labels=["Hold"])
        self.assertEqual(m.eligible(flat, set())[1], "labelled Hold")

    def test_without_the_setting_a_label_holds_nothing(self):
        self.assertIsNotNone(m.eligible(labelled(pr(), "Hold"), set())[0])


class BlockedByEverywhere(unittest.TestCase):
    """Blockers named before the approval, so that nothing said since it
    holds the pull request first."""

    def setUp(self):
        m.configure(project(blocked_by="everywhere"))
        self.addCleanup(m.configure, project())

    def held(self, item, open_numbers=(2, 3, 4)):
        return m.eligible(item, set(open_numbers))[1]

    def test_by_default_only_the_body_counts(self):
        m.configure(project())
        item = pr(comments=[comment(PEER, at=TA, body="Blocked-by: #2")])
        self.assertIsNone(self.held(item))
        self.assertEqual(self.held(pr(body="Blocked-by: #2")),
                         "Blocked-by: #2")

    def test_a_comment_blocks(self):
        item = pr(comments=[comment(PEER, at=TA, body="Blocked-by: #2")])
        self.assertEqual(self.held(item), "Blocked-by: #2")

    def test_two_lines_in_one_comment_both_count(self):
        item = pr(comments=[comment(PEER, at=TA, body="Waiting on two.\n"
                                    "Blocked-by: #2\nBlocked-by: #3")])
        self.assertEqual(self.held(item), "Blocked-by: #2, #3")
        self.assertEqual(m.blockers_of(item), {2, 3})

    def test_the_newest_text_decides(self):
        item = pr(body="Blocked-by: #2",
                  comments=[comment(PEER, at=TA, body="Blocked-by: #3")])
        self.assertEqual(self.held(item), "Blocked-by: #3")

    def test_none_clears_it(self):
        item = pr(body="Blocked-by: #2",
                  comments=[comment(PEER, at=TA, body="Blocked-by: none")])
        self.assertIsNone(self.held(item))

    def test_a_text_without_the_line_changes_nothing(self):
        item = pr(comments=[comment(PEER, at=TA, body="Blocked-by: #2"),
                            comment(PEER, at=TB, body="any news?")])
        self.assertEqual(self.held(item), "Blocked-by: #2")

    def test_a_review_and_a_not_a_review_comment_count(self):
        item = pr(reviews=[review(REVIEWER),
                           review(PEER, "COMMENTED", at=TA,
                                  body="Blocked-by: #4")])
        self.assertEqual(self.held(item), "Blocked-by: #4")
        item = pr(comments=[comment(PEER, at=TA, body="Not-a-review.\n"
                                    "Blocked-by: #2")])
        self.assertEqual(self.held(item), "Blocked-by: #2")

    def test_a_closed_blocker_holds_nothing(self):
        item = pr(comments=[comment(PEER, at=TA, body="Blocked-by: #9")])
        self.assertIsNone(self.held(item))

    def test_an_unparsed_line_holds(self):
        item = pr(comments=[comment(PEER, at=TA, body="Blocked-by: the release")])
        self.assertEqual(self.held(item),
                         "unparsed Blocked-by line: Blocked-by: the release")

    def test_a_line_in_a_fence_is_not_read(self):
        item = pr(comments=[comment(PEER, at=TA, body="```\nBlocked-by: #2\n```")])
        self.assertIsNone(self.held(item))


class SaidSince0026(unittest.TestCase):

    def tearDown(self):
        m.configure(project())

    def said(self, comments=(), reviews=()):
        approval = review(REVIEWER, at=T1)
        item = pr(reviews=[approval] + list(reviews), comments=list(comments))
        return m.said_since(item, approval)

    def test_the_authors_remark_holds_with_author(self):
        m.configure(project(said_since={"author": True}))
        self.assertEqual(self.said([comment(AUTHOR, at=T2)]),
                         [(AUTHOR, T2, "comment")])

    def test_a_later_approval_holds_with_approvals(self):
        m.configure(project(said_since={"approvals": True}))
        self.assertEqual(self.said(reviews=[review(PEER, at=T2)]),
                         [(PEER, T2, "review")])

    def test_quiet_logins_never_hold(self):
        p = project(said_since={"quiet": ["Helper"]})
        m.configure(p)
        self.assertEqual(self.said([comment(p.slug("Helper") + "[bot]")]), [])
        self.assertEqual(len(self.said([comment(PEER)])), 1)


class FetchPr(Decide):

    def test_a_missing_pull_ref_is_fetched_by_sha(self):
        self.repo.branch("hidden", "feature")
        sha = self.repo.commit("h.txt", "hidden\n", "hidden")
        run_git(self.repo.path, "push", "-q", "origin", "hidden")
        got = m.fetch_pr(self.repo.path, 7, sha)
        self.assertEqual(got.returncode, 0)
        self.assertEqual(run_git(self.repo.path, "rev-parse", m.PR_REF % 7),
                         sha)

    def test_a_sha_that_cannot_be_fetched_returns_the_first_failure(self):
        got = m.fetch_pr(self.repo.path, 7, "e" * 40)
        self.assertNotEqual(got.returncode, 0)
        self.assertIn("refs/pull/7/head", got.stderr)

    def test_the_pull_ref_is_used_when_it_exists(self):
        self.assertEqual(m.fetch_pr(self.repo.path, 5, "f" * 40).returncode, 0)
        self.assertEqual(run_git(self.repo.path, "rev-parse", m.PR_REF % 5),
                         self.head)


class CiNeverStarted(Decide):

    def setUp(self):
        super().setUp()
        m.configure(project(ci_never_started=30))
        self.addCleanup(m.configure, project())

    def decide_at(self, minutes, checks=()):
        item = self.item()
        table = dict(answers(runs=[]), **{
            "repos/example/widget/commits/%s/check-runs" % self.head: {
                "check_runs": [{"app": {"slug": s}} for s in checks]}})
        committed = int(run_git(self.repo.path, "log", "-1", "--format=%ct",
                                self.head))
        now = datetime.datetime.fromtimestamp(
            committed, datetime.timezone.utc) + datetime.timedelta(
                minutes=minutes)
        return m.decide(item, m.approval_of(item), self.repo.path,
                        self.repo.main_tip(), FakeGitHub(table), {},
                        tools_run(), now=now)

    def test_a_young_head_waits(self):
        got = self.decide_at(10)
        self.assertEqual(got["action"], "WAIT")
        self.assertIn("has not started", got["reason"])

    def test_an_old_head_with_no_jobs_goes_back(self):
        got = self.decide_at(31, checks=["someone-else"])
        self.assertEqual((got["action"], got["to"]), ("HAND-BACK", "author"))
        self.assertIn("never started", got["reason"])
        self.assertIn("push a new head", got["reason"])
        self.assertIn("close and reopen it", got["reason"])

    def test_jobs_already_started_wait_however_old(self):
        got = self.decide_at(300, checks=["github-actions"])
        self.assertEqual(got["action"], "WAIT")
        self.assertIn("jobs have started", got["reason"])

    def test_a_listed_run_is_judged_as_before(self):
        m.configure(project(ci_never_started=30))
        item = self.item()
        got = m.decide(item, m.approval_of(item), self.repo.path,
                       self.repo.main_tip(),
                       FakeGitHub(answers(runs=[ci_run(None, "queued")])), {},
                       tools_run(), now=m.now_utc() + datetime.timedelta(
                           days=1))
        self.assertEqual(got["action"], "WAIT")
        self.assertNotIn("never", got["reason"])


class QueuedStall(unittest.TestCase):

    START = datetime.datetime(2026, 10, 7, 12, 0, 0,
                              tzinfo=datetime.timezone.utc)

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name
        m.configure(project(queued_stall_minutes=90))
        self.addCleanup(m.configure, project())

    def stalled(self, minutes, entry, action="WAIT"):
        waiting = {"pr": 5, "head": "h" * 40, "action": action}
        check = {"waiting": {"h" * 40: entry}} if entry is not None else {}
        m.stalled_for(self.state, waiting, self.START, check)
        return m.stalled_for(
            self.state, waiting,
            self.START + datetime.timedelta(minutes=minutes), check)

    def test_a_queued_head_has_the_longer_limit(self):
        self.assertIsNone(self.stalled(60, {}))
        self.assertEqual(self.stalled(90, {}), 90)

    def test_a_running_head_counts_from_its_start(self):
        started = {"started": "2026-10-07T12:20:00Z"}
        self.assertIsNone(self.stalled(45, started))
        self.assertEqual(self.stalled(50, started), 50)

    def test_a_head_the_check_does_not_know_is_as_before(self):
        self.assertEqual(self.stalled(30, None), 30)

    def test_a_refused_write_is_as_before(self):
        self.assertEqual(self.stalled(30, {}, action="UPDATE"), 30)

    def test_without_the_setting_the_check_state_is_ignored(self):
        m.configure(project())
        self.assertEqual(self.stalled(30, {}), 30)


GEN = """#!/bin/sh
# Lists src/ in gen.txt; --check exits 1 when gen.txt is out of date.
[ "$1" = --broken ] && { echo "broken here" >&2; exit 3; }
ls src | sort > gen.new
if [ "$1" = --check ]; then
    cmp -s gen.new gen.txt; rc=$?; rm -f gen.new; exit $rc
fi
mv gen.new gen.txt
"""
RULE = {"paths": ["gen.txt"], "command": ["tools/gen"],
        "check": ["tools/gen", "--check"]}
IDENTITY = ("Merger", "1+mergerwidgetagent[bot]@users.noreply.github.com")


class Resolve(unittest.TestCase):
    """A generated file both sides changed: gen.txt lists src/, and the
    feature and main each add a file to src/."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(self.tmp.name)
        self.state = os.path.join(self.tmp.name, "state")
        os.mkdir(self.state)
        self.write("tools/gen", GEN, mode=0o755)
        self.write("src/base", "")
        self.write("gen.txt", "base\n")
        self.commit("generator")
        self.repo.push("main")
        self.repo.branch("feature")
        self.write("src/f", "")
        self.write("gen.txt", "base\nf\n")
        self.head = self.commit("feature")
        self.repo.publish_pr(5, self.head)
        run_git(self.repo.path, "push", "-q", "origin", "feature:b5")
        self.repo.branch("main", "main")
        self.write("src/m", "")
        self.write("gen.txt", "base\nm\n")
        self.commit("main")
        self.repo.push("main")
        self.repo.branch("feature", "feature")
        self.configure()
        self.addCleanup(m.configure, project())
        m.RESOLVE_IDENTITY = IDENTITY
        self.addCleanup(setattr, m, "RESOLVE_IDENTITY", None)

    def configure(self, **rule):
        m.configure(project(resolve=[dict(RULE, **rule)]))

    def write(self, name, text, mode=0o644):
        path = os.path.join(self.repo.path, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w") as handle:
            handle.write(text)
        os.chmod(path, mode)
        run_git(self.repo.path, "add", name)

    def commit(self, message):
        run_git(self.repo.path, "commit", "-q", "-m", message)
        return run_git(self.repo.path, "rev-parse", "HEAD")

    def git_run(self):
        """tools_run, with a push to GitHub sent to `origin` instead."""
        base = tools_run()

        def run(args, cwd=None, env=None):
            if "push" in args:
                args = [self.repo.origin if a.startswith("https://github.com/")
                        else a for a in args]
                self.pushed_env = env
            return base(args, cwd, env)
        return run

    def item(self):
        return pr(5, head=self.head,
                  reviews=[review(REVIEWER, oid=self.head)])

    def decide(self):
        item = self.item()
        return m.decide(item, m.approval_of(item), self.repo.path,
                        self.repo.main_tip(), FakeGitHub(answers()), {},
                        self.git_run())

    def resolve(self):
        return m.resolve(self.repo.path, {"action": "RESOLVE", "rule": 0,
                                          "pr": 5, "head": self.head},
                         "b5", self.repo.main_tip(), FakeGitHub({}),
                         self.git_run())

    def checker(self):
        return m.remembered(self.state, lambda sha, rule: m.regenerated_in(
            self.repo.path, sha, rule))

    def test_a_conflict_only_in_generated_files_is_resolved(self):
        self.assertEqual(self.decide(), {"action": "RESOLVE", "rule": 0})

    def test_without_the_setting_it_is_handed_back(self):
        m.configure(project())
        self.assertEqual(self.decide()["action"], "HAND-BACK")

    def test_another_conflict_is_handed_back(self):
        self.configure(paths=["other.txt"])
        got = self.decide()
        self.assertEqual((got["action"], got["reason"]),
                         ("HAND-BACK", m.CONFLICT_REASON))

    def test_the_merge_is_regenerated_committed_and_pushed(self):
        self.assertEqual(self.resolve(), "done")
        new = run_git(self.repo.origin, "rev-parse", "b5")
        self.assertEqual(run_git(self.repo.origin, "rev-list", "--parents",
                                 "-n1", new).split()[1:],
                         [self.head, self.repo.main_tip()])
        self.assertEqual(run_git(self.repo.origin, "show", new + ":gen.txt"),
                         "base\nf\nm")
        self.assertEqual(run_git(self.repo.origin, "log", "-1",
                                 "--format=%an <%ae>", new),
                         "%s <%s>" % IDENTITY)
        self.assertIn("gen.txt regenerated", run_git(
            self.repo.origin, "log", "-1", "--format=%s", new))
        self.assertEqual(self.pushed_env["GIT_CONFIG_KEY_0"],
                         "http.https://github.com/.extraheader")
        # No worktree is left behind.
        self.assertEqual(len(run_git(self.repo.path, "worktree",
                                     "list").splitlines()), 1)

    def test_the_resolved_merge_covers_the_head_once_checked(self):
        self.resolve()
        self.repo.push("main")
        new = run_git(self.repo.path, "rev-parse", "origin/b5")
        main_ref = "refs/remotes/origin/main"
        self.assertFalse(m.covers_head(self.repo.path, self.head, new,
                                       main_ref)[0])
        self.assertEqual(m.covers_head(self.repo.path, self.head, new,
                                       main_ref, regenerated=self.checker()),
                         (True, None))
        self.assertEqual(m.load_json(os.path.join(
            self.state, m.RESOLVE_CHECKED), {}), {"%s 0" % new: True})

    def test_a_hand_made_merge_with_stale_files_does_not_cover(self):
        got = subprocess.run(["git", "-C", self.repo.path, "merge", "-q",
                              "origin/main"], env=git_env(),
                             capture_output=True, text=True)
        # Stopping on the conflict is the point; failing any other way isn't.
        self.assertIn("CONFLICT", got.stdout + got.stderr)
        self.write("gen.txt", "base\nf\n")
        run_git(self.repo.path, "add", "src/m")
        new = self.commit("hand merge")
        got = m.covers_head(self.repo.path, self.head, new,
                            "refs/remotes/origin/main",
                            regenerated=self.checker())
        self.assertFalse(got[0])
        self.assertIn("not the clean merge", got[1])

    def test_a_check_that_cannot_say_is_asked_again(self):
        self.resolve()
        self.repo.push("main")
        new = run_git(self.repo.path, "rev-parse", "origin/b5")
        self.configure(check=["tools/gen", "--broken"])
        got = m.covers_head(self.repo.path, self.head, new,
                            "refs/remotes/origin/main",
                            regenerated=self.checker())
        self.assertIsNone(got[0])
        self.assertEqual(m.load_json(os.path.join(
            self.state, m.RESOLVE_CHECKED), {}), {})

    def test_a_moved_head_refuses_the_push(self):
        run_git(self.repo.path, "commit", "-q", "--allow-empty", "-m", "x")
        run_git(self.repo.path, "push", "-q", "-f", "origin", "HEAD:b5")
        self.assertTrue(self.resolve().startswith("refused: "))

    def test_a_failing_command_fails(self):
        self.configure(command=["tools/gen", "--broken"])
        self.assertEqual(self.resolve(),
                         "failed: gen exited 3: broken here")

    def test_a_missing_command_fails(self):
        self.configure(command=["tools/nothing"])
        self.assertTrue(self.resolve().startswith(
            "failed: nothing could not run"))

    def test_without_an_identity_it_fails(self):
        m.RESOLVE_IDENTITY = None
        self.assertTrue(self.resolve().startswith("failed: identity"))

    def test_once_per_approval_and_head(self):
        decision = {"action": "RESOLVE", "rule": 0, "pr": 5,
                    "head": self.head}
        approval = review(REVIEWER, oid=self.head)
        args = (self.state, self.repo.path, decision, approval, "b5",
                self.repo.main_tip(), FakeGitHub({}), self.git_run())
        run_git(self.repo.path, "push", "-q", "-f", "origin",
                "%s^:refs/heads/b5" % self.head)
        self.assertTrue(m.resolve_once(*args).startswith("refused"))
        self.assertEqual(m.resolve_once(*args),
                         "failed: already tried at this head")

    def tick(self, minutes=0):
        item = self.item()
        github = FakeGitHub(answers(prs=[item]))
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        record = m.tick(self.repo.path, self.state, github, self.git_run(),
                        lambda line: None,
                        now=lambda: start + datetime.timedelta(
                            minutes=minutes), live=True)
        return record, github

    def test_a_tick_resolves_it(self):
        record, github = self.tick()
        self.assertEqual([(d["pr"], d["action"], d.get("result"))
                          for d in record["decisions"]],
                         [(5, "RESOLVE", "done")])
        self.assertEqual(github.writes, [])
        self.assertNotEqual(run_git(self.repo.origin, "rev-parse", "b5"),
                            self.head)

    def test_a_tick_that_cannot_resolve_hands_it_back_once(self):
        self.configure(command=["tools/gen", "--broken"])
        record, github = self.tick()
        self.assertEqual([(d["action"], d.get("result"))
                          for d in record["decisions"]],
                         [("RESOLVE", "failed: gen exited 3: broken here"),
                          ("HAND-BACK", "done")])
        self.assertIn("could not resolve it (gen exited 3)",
                      record["decisions"][1]["reason"])
        self.assertEqual([w[1] for w in github.writes],
                         [SLUG + "/issues/5/comments"])
        record, github = self.tick(minutes=1)
        self.assertEqual([(d["action"], d.get("result"))
                          for d in record["decisions"]],
                         [("RESOLVE", "failed: gen exited 3: broken here"),
                          ("HAND-BACK", "already said")])
        self.assertEqual(github.writes, [])


class UpdateOnce(Tick):

    def test_no_update_after_a_hand_back_under_the_same_approval(self):
        m.configure(project(update_once=True))
        self.addCleanup(m.configure, project())
        record, _ = self.tick([self.item()], conclusions=("failure",),
                              live=True)
        self.assertEqual(record["decisions"][0]["action"], "HAND-BACK")
        self.advance_main("m3.txt")
        record, _ = self.tick([self.item()], live=True, minutes=1)
        got = record["decisions"][0]
        self.assertEqual(got["action"], "SKIP")
        self.assertIn("not updated until it is approved again", got["reason"])
        self.assertEqual(self.github.writes, [])
        again = self.item()
        again["reviews"].append(review(REVIEWER, at=T3, oid=self.approved))
        record, _ = self.tick([again], live=True, minutes=2)
        self.assertEqual(record["decisions"][0]["action"], "UPDATE")

    def test_without_it_the_update_goes_ahead(self):
        self.tick([self.item()], conclusions=("failure",), live=True)
        self.advance_main("m3.txt")
        record, _ = self.tick([self.item()], live=True, minutes=1)
        self.assertEqual(record["decisions"][0]["action"], "UPDATE")


class Main0026(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = tmp.name
        self.state = os.path.join(tmp.name, "state")
        os.mkdir(self.state)
        self.checkout = os.path.join(tmp.name, "cron")
        os.makedirs(os.path.join(self.checkout, ".agent-forge"))
        self.environ = {"HOME": tmp.name,
                        "FORGE_CRON_CHECKOUT": self.checkout}
        self.addCleanup(m.configure, project())
        self.addCleanup(setattr, m, "RESOLVE_IDENTITY", None)

    def run_main(self, merge, argv):
        data = example()
        data["merge"].update(merge)
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(data, f)
        self.minted, self.ticks, self.identities = [], [], []

        def run(args, cwd=None, env=None):
            return subprocess.CompletedProcess(args, 0, self.checkout + "\n",
                                               "")

        def mint(project_, agent):
            self.minted.append(agent)
            return "token"

        def identity(project_, name):
            self.identities.append(name)
            return IDENTITY

        def fake_tick(repo, state_dir, github, run, out, live, send=None,
                      **kwargs):
            self.ticks.append(dict(kwargs, live=live,
                                   identity=m.RESOLVE_IDENTITY))
        with mock.patch.object(m, "tick", fake_tick):
            return m.main(argv + ["--state-dir", self.state], run,
                          lambda line: None, environ=self.environ,
                          mint=mint, sender=lambda p, e: None,
                          identity=identity)

    def test_a_dry_run_reads_as_read_as(self):
        self.assertEqual(self.run_main({"read_as": "Helper"}, ["--dry-run"]),
                         0)
        self.assertEqual(self.minted, ["Helper"])

    def test_a_live_run_acts_as_the_merger(self):
        self.run_main({"read_as": "Helper"}, [])
        self.assertEqual(self.minted, ["Merger"])

    def test_resolve_gives_a_live_run_the_mergers_identity(self):
        self.run_main({"resolve": [RULE]}, [])
        self.assertEqual(self.identities, ["Merger"])
        self.assertEqual(self.ticks[0]["identity"], IDENTITY)
        m.RESOLVE_IDENTITY = None
        self.run_main({"resolve": [RULE]}, ["--dry-run"])
        self.assertEqual(self.identities, [])

    def test_the_check_state_is_read_for_queued_stalls(self):
        check = os.path.join(self.tmp, "check")
        os.mkdir(check)
        with open(os.path.join(check, "state.json"), "w") as f:
            json.dump({"waiting": {"h": {}}}, f)
        self.run_main({"queued_stall_minutes": 90},
                      ["--check-state", check])
        self.assertEqual(self.ticks[0]["check_state"], {"waiting": {"h": {}}})
        self.run_main({}, ["--check-state", check])
        self.assertNotIn("check_state", self.ticks[0])

