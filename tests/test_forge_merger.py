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

from agent_forge import checkout, config, locks
from tests.helpers import example, load_tool

m = load_tool("forge-merger")

# The gate the tests configure: a project tool, run from the checkout,
# and an agent-forge one, run from this installation.
GATE = [["tools/closing-keywords", "--pr", "{pr}", "--include-subject"],
        ["forge-commit-identity", "{base}..{head}"]]


def old_defaults(ahead):
    """These tests were written when a test_ahead object meant depth 3,
    batches of one and no trains unless it said so; designs/0050 made
    trains the default. The defaults themselves are tested in Trains
    and in test_config."""
    if not isinstance(ahead, dict):
        return ahead
    return dict({"depth": 3, "batch": 1, "batches": 1, "trains": "off"},
                **ahead)


def project(**merge):
    # own_head_green is off here, so the older tests' answers needn't
    # carry statuses for every approved commit; OwnGreenWait and the
    # Tick tests that name it turn it on.
    data = example()
    # designs/0050 makes testing ahead the default; these tests say
    # when they want it.
    data["merge"] = dict({"gate": GATE, "own_head_green": False,
                          "test_ahead": False}, **merge)
    data["merge"]["test_ahead"] = old_defaults(data["merge"]["test_ahead"])
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
    # designs/0050 makes testing ahead the default; these tests say
    # when they want it.
    data["merge"] = dict({"gate": GATE, "own_head_green": False,
                          "test_ahead": False}, **merge)
    data["merge"]["test_ahead"] = old_defaults(data["merge"]["test_ahead"])
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

    def test_labelled_ones_go_before_a_tools_fixes(self):
        # A person's Urgent label always goes first; a fix a tool found
        # (red nightly, red default branch) comes after, older or not.
        self.addCleanup(setattr, m, "NIGHTLY_FIXES", frozenset())
        self.addCleanup(setattr, m, "MAIN_RED_FIXES", frozenset())
        m.NIGHTLY_FIXES = frozenset({5})
        m.MAIN_RED_FIXES = frozenset({6})
        hot = labelled(pr(7, T3), "Priority:urgent")
        got = m.order([pr(3, T0), pr(5, T1), pr(6, T0), hot])
        self.assertEqual([p["number"] for p in got], [7, 6, 5, 3])
        got = m.fixes_first([pr(3, T0), pr(6, T0), hot], None, set())
        self.assertEqual([p["number"] for p in got], [7, 6, 3])


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

    def test_a_body_edited_after_a_comment_is_the_newer_line(self):
        older = comment(AUTHOR, at=T2, body="Defer-until: 2026-10-01")
        edited = pr(body="Defer-until: 2026-10-09", comments=[older])
        self.assertIsNone(m.deferral_of(edited, NOW))
        edited["lastEditedAt"] = T3
        self.assertEqual(m.deferral_of(edited, NOW),
                         "deferred until 2026-10-09T00:00:00Z")

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

    def prefetch(self, paths, workers=8):
        """Asks nothing ahead: `api` answers from the table."""
        list(paths)

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

    def test_a_tool_refusal_names_the_description_it_judged(self):
        got = self.decide(results={"closing-keywords": (1, "refused")})
        self.assertEqual(got["text"], m.description(self.item()))
        got = self.decide(runs=[ci_run("failure")])
        self.assertNotIn("text", got)

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

    def test_a_stack_layer_names_the_layer_below_to_the_gate(self):
        seen = []
        base = tools_run()

        def run(args, cwd=None, env=None):
            if os.path.basename(args[0]) == "closing-keywords":
                seen.append(args[1:])
            return base(args, cwd, env)
        item = self.item()
        gate = (("tools/closing-keywords", "--pr", "{pr}", "--below",
                 "{below}"),)
        with mock.patch.object(m, "GATE", gate):
            for restack in ((4, "0" * 40), None):
                m.decide(item, m.approval_of(item), self.repo.path,
                         self.repo.main_tip(), FakeGitHub(answers()), {},
                         run, restack=restack)
        self.assertEqual(seen, [["--pr", "5", "--below", "4"],
                                ["--pr", "5", "--below"]])

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


class MergedAnotherMember(unittest.TestCase):
    """A train member whose author merged in another member's head
    (amendment of 2026-11-12): kept when that member is ahead of it in
    the train, since its tree in its place is then unchanged; out
    otherwise."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repo(tmp.name)
        self.addCleanup(m.configure, project())
        m.configure(project(test_ahead={
            "batch": 3, "batches": 1, "depth": 3, "trains": "live"}))
        self.heads = {}
        for number in (5, 6, 7, 8):
            self.repo.branch("b%d" % number, "origin/main")
            self.heads[number] = self.repo.commit(
                "f%d.txt" % number, "%d\n" % number, "#%d" % number)
            self.repo.publish_pr(number, self.heads[number])
        self.entries, sizes, _ = m.cut_trains(
            self.repo.path, self.repo.main_tip(), self.carriages(), None,
            dict(self.heads), set(self.heads))
        self.assertEqual(m.train_lists(self.entries, sizes)[0], [5, 6, 7])
        self.last = {"base": self.repo.main_tip(), "trains": True,
                     "entries": self.entries,
                     "batch": {"size": sizes[0], "trees": [
                         e["tree"] for e in self.entries[:sizes[0]]]}}

    def carriages(self, **moved):
        return [[pr(n, T1, head=moved.get("p%d" % n, self.heads[n]))]
                for n in sorted(self.heads)]

    def merged_into(self, number, other):
        """#`number`'s boarded head with #`other`'s head merged in."""
        self.repo.branch("m%d" % number, self.heads[number])
        head = self.repo.merge(self.heads[other])
        self.repo.publish_pr(number, head)
        return head

    def recut(self, number, head):
        return m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            self.carriages(**{"p%d" % number: head}), self.last,
            {**self.heads, number: head}, set(self.heads))

    def test_merging_the_member_ahead_of_it_keeps_its_place(self):
        # #7 merges #6, the member just ahead of it; #5 is ahead of both,
        # so #7's own tree is not the one predicted for it.
        head = self.merged_into(7, 6)
        self.assertFalse(m.own_push(self.repo.path, self.heads[7], head,
                                    self.repo.main_tip()))
        self.assertNotEqual(run_git(self.repo.path, "rev-parse",
                                    head + "^{tree}"), self.entries[2]["tree"])
        got, sizes, events = self.recut(7, head)
        self.assertEqual(events, [("kept", "KEPT #7: pushed %s, same tree "
                                   "in its place" % head[:10])])
        self.assertEqual(m.train_lists(got, sizes)[0], [5, 6, 7])
        # The same predictions, so the same top and the same run.
        self.assertEqual([e["sha"] for e in got[:3]],
                         [e["sha"] for e in self.entries[:3]])
        self.assertEqual(got[2]["head"], head)
        # And with train_merge the train merges that head.
        queue = [pr(n, T1, head=head if n == 7 else self.heads[n])
                 for n in (5, 6, 7)]
        green = {"results": {"r": {"ok": True,
                                   "tree": self.entries[2]["tree"]}}}
        ready = m.train_ready(self.last, queue, self.repo.main_tip(), green,
                              self.repo.path)
        self.assertEqual([e["head"] for e in ready],
                         [self.heads[5], self.heads[6], head])

    def test_merging_the_first_member_keeps_the_second_silently(self):
        # Its own tree is then the prediction itself.
        head = self.merged_into(6, 5)
        got, sizes, events = self.recut(6, head)
        self.assertEqual(events, [])
        self.assertEqual([e["sha"] for e in got[:3]],
                         [e["sha"] for e in self.entries[:3]])

    def test_merging_a_member_behind_it_is_a_push(self):
        head = self.merged_into(6, 7)
        _, _, events = self.recut(6, head)
        self.assertEqual(events[0][0], "left")
        self.assertIn("LEFT #6: pushed %s mid-train" % head[:10],
                      events[0][1])

    def test_merging_one_not_in_the_train_is_a_push(self):
        head = self.merged_into(6, 8)
        _, _, events = self.recut(6, head)
        self.assertEqual(events[0], ("left", "LEFT #6: pushed %s mid-train"
                                     % head[:10]))


class TrainSetup(RepoCase):
    """A green train of #5 and #6, predicted on main's tip."""

    def setUp(self):
        super().setUp()
        self.repo.publish_pr(5, self.approved)
        self.repo.branch("other", "main")
        self.other = self.repo.commit("g.txt", "other\n", "other")
        self.repo.publish_pr(6, self.other)
        self.repo.branch("main", "main")
        self.tip = self.repo.main_tip()
        self.queue = [dict(pr(5, head=self.approved), title="five"),
                      dict(pr(6, head=self.other), title="six")]
        self.entries, parent = [], self.tip
        for item in self.queue:
            entry, failed = m.predict_one(self.repo.path, parent, item)
            self.assertIsNone(failed)
            self.entries.append(entry)
            parent = entry["sha"]
        self.last = {"base": self.tip, "entries": self.entries,
                     "batch": {"size": 2, "trees": [
                         e["tree"] for e in self.entries]}}
        self.check = {"results": {"r": {"ok": True,
                                        "tree": self.entries[-1]["tree"]}}}
        self.lines = []
        for name, value in (("RESOLVE_IDENTITY", ("Merger", "m@example")),
                            ("CI_WORKFLOW", ""), ("REPO_SLUG",
                                                  "example/widget")):
            patch = mock.patch.object(m, name, value)
            patch.start()
            self.addCleanup(patch.stop)

    def git(self, args, cwd=None, env=None):
        """git, with a push to GitHub sent to the test's origin."""
        if "push" in args:
            args = [self.repo.origin if a.startswith("https://github.com/")
                    else a for a in args]
        return m.sh(args, cwd, env)

    def merge(self, merged=(5, 6), refuse=None):
        self.github = FakeGitHub({
            SLUG + "/pulls/%d" % n: {"merged": n in merged} for n in (5, 6)},
            refuse)
        return m.train_merge(self.repo.path, "/nonexistent", self.entries,
                             self.queue, self.tip, self.check, self.github,
                             self.git, lambda s: None, self.lines.append)


class TrainMerge(TrainSetup):
    """merge.test_ahead.train_merge (designs/0047, amendment of
    2026-11-11): a green train goes in as one pull request."""

    def test_a_green_train_merges_as_one_pull_request(self):
        self.assertEqual(self.merge(), ([5, 6], []))
        top = run_git(self.repo.origin, "rev-parse", "forge/train-5-6")
        # Every member's head is in the train, and its tree is the one
        # the green top tested.
        for head in (self.approved, self.other):
            run_git(self.repo.path, "fetch", "-q", "origin",
                    "forge/train-5-6")
            self.assertEqual(subprocess.run(
                ["git", "-C", self.repo.path, "merge-base",
                 "--is-ancestor", head, top]).returncode, 0)
        self.assertEqual(run_git(self.repo.path, "rev-parse",
                                 top + "^{tree}"), self.entries[-1]["tree"])
        methods = [(w[0], w[1]) for w in self.github.writes]
        self.assertEqual(methods, [
            ("POST", SLUG + "/statuses/" + top),
            ("POST", SLUG + "/pulls"),
            ("PUT", SLUG + "/pulls/900/merge"),
            ("DELETE", SLUG + "/git/refs/heads/forge/train-5-6")])
        self.assertEqual(self.github.writes[0][2]["context"],
                         m.STATUS_CONTEXT)
        self.assertEqual(self.github.writes[2][2],
                         {"sha": top, "merge_method": "merge",
                          "commit_title": "Merge train #5..#6 (#900)"})
        self.assertIn("* #5 at %s" % self.approved[:10],
                      self.github.writes[1][2]["body"])

    def test_a_member_github_does_not_mark_merged_goes_on_its_own(self):
        self.assertEqual(self.merge(merged=(5,)), ([5], [6]))
        self.assertIn("#6", self.lines[-1])

    def test_a_refused_merge_closes_the_train_and_falls_back(self):
        refuse = lambda method, path: "protected" \
            if path.endswith("/merge") else None
        self.assertEqual(self.merge(refuse=refuse), ([], [5, 6]))
        self.assertIn(("PATCH", SLUG + "/pulls/900"),
                      [(w[0], w[1]) for w in self.github.writes])
        self.assertIn("each member merges on its own", self.lines[-1])

    def test_a_layer_is_retargeted_to_main_first(self):
        self.queue[1]["baseRefName"] = "b5"
        self.merge()
        self.assertIn(("PATCH", SLUG + "/pulls/6", {"base": "main"}),
                      self.github.writes)

    def test_ready_only_while_main_is_the_trains_base(self):
        self.assertEqual(m.train_ready(self.last, self.queue, self.tip,
                                       self.check), self.entries)
        self.assertIsNone(m.train_ready(self.last, self.queue, "f" * 40,
                                        self.check))

    def test_a_train_of_one_is_ready(self):
        # Merged as a train, the default branch lands on the sha the
        # next train was predicted on; an update and a merge of its own
        # would move it elsewhere and that train would be cut again.
        one = dict(self.last, entries=self.entries[:1],
                   batch={"size": 1, "trees": [self.entries[0]["tree"]]})
        green = {"results": {"r": {"ok": True,
                                   "tree": self.entries[0]["tree"]}}}
        self.assertEqual(m.train_ready(one, self.queue, self.tip, green),
                         self.entries[:1])

    def test_ready_only_with_a_green_top(self):
        self.assertIsNone(m.train_ready(self.last, self.queue, self.tip,
                                        {"results": {}}))

    def test_a_member_that_only_merged_main_is_still_ready(self):
        # Its new head, with main as the first parent, gives the tree
        # its prediction tested: the train merges that head, so GitHub
        # marks it merged, and the tree it tested.
        merged = run_git(self.repo.path, "commit-tree",
                         self.other + "^{tree}", "-p", self.tip, "-p",
                         self.other, "-m", "merge main")
        moved = [self.queue[0], dict(self.queue[1], headRefOid=merged)]
        self.assertIsNone(m.train_ready(self.last, moved, self.tip,
                                        self.check))
        ready = m.train_ready(self.last, moved, self.tip, self.check,
                              self.repo.path)
        self.assertEqual([e["head"] for e in ready],
                         [self.approved, merged])
        self.assertEqual([e["tree"] for e in ready],
                         [e["tree"] for e in self.entries])
        self.entries, self.queue = ready, moved
        self.assertEqual(self.merge(), ([5, 6], []))
        top = run_git(self.repo.origin, "rev-parse", "forge/train-5-6")
        run_git(self.repo.path, "fetch", "-q", "origin", "forge/train-5-6")
        self.assertEqual(subprocess.run(
            ["git", "-C", self.repo.path, "merge-base", "--is-ancestor",
             merged, top]).returncode, 0)
        self.assertEqual(run_git(self.repo.path, "rev-parse",
                                 top + "^{tree}"), self.entries[-1]["tree"])

    def test_a_member_with_a_change_of_its_own_is_not_ready(self):
        self.repo.branch("more", self.other)
        more = self.repo.commit("g.txt", "changed\n", "more")
        moved = [self.queue[0], dict(self.queue[1], headRefOid=more)]
        self.assertIsNone(m.train_ready(self.last, moved, self.tip,
                                        self.check, self.repo.path))

    def test_ready_only_while_every_member_is_at_its_predicted_head(self):
        moved = [self.queue[0], dict(self.queue[1], headRefOid="9" * 40)]
        self.assertIsNone(m.train_ready(self.last, moved, self.tip,
                                        self.check))


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

    def test_a_future_stamped_older_review_is_not_said_since(self):
        # #183: a request for changes posted before the approval but
        # stamped after it held the pull request as a remark since.
        old = dict(review("otherwidgetagent", "CHANGES_REQUESTED",
                          at="2026-11-01T09:58:54Z", body="One phrase."),
                   databaseId=100, createdAt="2026-11-01T09:58:54Z")
        new = dict(review(REVIEWER, at="2026-11-01T09:07:28Z"),
                   databaseId=200, createdAt="2026-11-01T09:07:28Z")
        page = graphql_page([pr(7, reviews=[old, new])])
        got = m.fetch_prs(FakeGitHub({"graphql": lambda: page}))[0]
        self.assertEqual(m.approval_of(got)["databaseId"], 200)
        self.assertEqual(m.said_since(got, m.approval_of(got)), [])

    def test_open_issues(self):
        self.assertEqual(m.fetch_open_issues(FakeGitHub({"--paginate":
                                                         [4, 9]})), {4, 9})
        self.assertEqual(m.fetch_open_issues(FakeGitHub({"--paginate": None})),
                         set())

    def test_open_issues_ask_graphql_for_numbers_only(self):
        github = FakeGitHub({"--paginate": [4]})
        m.fetch_open_issues(github)
        args = github.calls[0]
        self.assertEqual(args[:2], ("--paginate", "graphql"))
        query = next(a for a in args if a.startswith("query="))
        self.assertIn("issues(states:OPEN", query)
        self.assertIn("$endCursor", query)
        self.assertNotIn("body", query)


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

    def test_prefetched_paths_are_answered_once_without_asking_again(self):
        asked = []

        def run(args, cwd=None, env=None):
            asked.append(args[-1])
            return subprocess.CompletedProcess(
                args, 0, '{"p": "%s"}' % args[-1], "")
        github = m.GitHub("tok", run)
        github.prefetch(["a", "b", "a"])
        self.assertEqual(sorted(asked), ["a", "b"])
        self.assertEqual(github.api("a"), {"p": "a"})
        self.assertEqual(github.api("b"), {"p": "b"})
        self.assertEqual(sorted(asked), ["a", "b"])
        self.assertEqual(github.api("a"), {"p": "a"})
        self.assertEqual(sorted(asked), ["a", "a", "b"])

    def test_a_failed_prefetch_is_asked_again_and_reported(self):
        def run(args, cwd=None, env=None):
            return subprocess.CompletedProcess(args, 1, "", "nope")
        github = m.GitHub("tok", run)
        github.prefetch(["x"])
        with self.assertRaisesRegex(RuntimeError, "gh api x: nope"):
            github.api("x")

    def test_a_refused_write_is_returned_not_raised(self):
        run = lambda args, cwd=None, env=None: subprocess.CompletedProcess(
            args, 1, "", "HTTP 405")
        self.assertEqual(m.GitHub("tok", run).write("PUT", "p", a="b"),
                         (False, "HTTP 405"))


class Tick(Decide):

    def tick(self, prs, issues=(), conclusions=("success",), live=False,
             refuse=None, minutes=0, send=None, extra=None,
             check_state=None, run=None):
        github = FakeGitHub(dict(answers(conclusions, prs=prs, issues=issues),
                                 **(extra or {})), refuse)
        self.github = github
        lines = []
        state = self.state = os.path.join(self.tmp.name, "state")
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        record = m.tick(self.repo.path, state, github, run or tools_run(),
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

    def test_the_record_names_urgent_and_elevated_pull_requests(self):
        # For forge-flow: labelled by hand, or made urgent by a tool (a
        # red nightly's fix), whether queued or held.
        m.configure(urgent_project())
        self.addCleanup(m.configure, project())
        held = labelled(pr(7, T1, base="b6", reviews=[]), "Priority:urgent")
        prs = [labelled(self.item(), "Priority:urgent"), self.other_pr(),
               held, pr(8, T1, reviews=[])]
        with mock.patch.object(m, "NIGHTLY_FIXES", frozenset({6, 8})):
            record, _ = self.tick(prs)
        self.assertEqual(record["urgent"], [5, 7])
        self.assertEqual(record["elevated"], [6, 8])
        record, _ = self.tick(prs[1:2])
        self.assertEqual((record["urgent"], record["elevated"]), ([], []))

    def test_a_pull_request_waiting_for_an_instance_is_held_and_passed(self):
        # checks.instances.<name>.gate (agent-forge-next-bundle item 5).
        m.configure(gated_project({"paths": ["f.*"]}))
        self.addCleanup(m.configure, project())
        record, _ = self.tick([self.item(), self.other_pr()])
        self.assertEqual(record["held"]["5"], "waiting for vm/mac (absent)")
        # #6 changes no path the instance runs: it is decided as before.
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(6, "MERGE")])

    def test_only_the_heads_an_instance_applies_to_are_prefetched(self):
        # Fetching every candidate's statuses spent the App's hourly API
        # allowance (2026-11-11): only #5 changes a path the instance runs.
        m.configure(gated_project({"paths": ["f.*"]}))
        self.addCleanup(m.configure, project())
        asked = []
        with mock.patch.object(FakeGitHub, "prefetch", autospec=True,
                               side_effect=lambda gh, paths, workers=8:
                               asked.extend(paths)):
            self.tick([self.item(), self.other_pr()])
        self.assertEqual(asked, [m.status_path(self.item()["headRefOid"])])

    def test_a_pull_request_not_yet_green_is_held_and_passed(self):
        # merge.own_head_green: the build check is the gate, even for an
        # approval given before it ran. #5 is pending on its head and
        # its approved commit, so it is no candidate: not merged, and
        # in no batch, train or prediction. #6 is decided as before.
        m.configure(project(own_head_green=True))
        self.addCleanup(m.configure, project())
        pending = [status("pending")]
        record, _ = self.tick([self.item(), self.other_pr()], extra={
            SLUG + "/commits/" + self.head: pending,
            SLUG + "/commits/" + self.approved: pending})
        self.assertEqual(
            record["held"]["5"],
            "vm/build-and-test is pending on %s, and no approved commit "
            "is green; waits for the build check to pass" % self.head[:10])
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(6, "MERGE")])

    def test_green_on_the_approved_commit_is_enough(self):
        # The Merger's own merge of main made a new head, still pending:
        # the approved commit passed, so the pull request is a candidate
        # and waits at the front for its head, as before.
        m.configure(project(own_head_green=True))
        self.addCleanup(m.configure, project())
        record, _ = self.tick([self.item()], extra={
            SLUG + "/commits/" + self.head: [status("pending")],
            SLUG + "/commits/" + self.approved: [status("success")]})
        self.assertNotIn("5", record["held"])
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(5, "WAIT")])

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
        # A later tick closes what it closes, if GitHub left it open.
        self.assertIn("5", m.load_json(os.path.join(
            self.state, m.CLOSING_FILE), {}))

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

    def test_a_new_reason_on_the_same_head_is_not_said_again(self):
        # designs/0044, rule 1: once per approval and head.
        self.tick([self.item()], conclusions=("failure",), live=True)
        record, _ = self.tick([self.item()], conclusions=("error",),
                              live=True)
        self.assertEqual(self.github.writes, [])
        self.assertEqual(record["decisions"][0]["result"], "already said")

    def test_without_once_per_head_a_new_reason_is_said_again(self):
        m.configure(project(hand_back_once_per_head=False))
        self.addCleanup(m.configure, project())
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

    def told(self, before, **decision):
        """carry_out's answer for a hand-back of #5 at head h, approved
        at T1, after `before` in `said`, and the writes it made."""
        github = FakeGitHub({})
        decision = dict({"action": "HAND-BACK", "to": "author", "pr": 5,
                         "head": "h" * 40, "reason": "red"}, **decision)
        result = m.carry_out(decision, review(REVIEWER), github,
                             {"5": before})
        return result, len(github.writes)

    def test_once_per_head_whatever_the_reason(self):
        # designs/0044, rule 1. One project, 2026-11-04: one head was
        # told 8 times in an hour, a conflict with main and a merge that
        # wasn't clean in turn, each replacing the other in `said`.
        conflict = m.hand_back_key(review(REVIEWER), "h" * 40,
                                   m.CONFLICT_REASON)
        self.assertEqual(self.told(dict(conflict), to="reviewer", reason=(
            "abc's tree is not the clean merge of its parents")),
            ("already said", 0))
        self.assertEqual(self.told(dict(conflict, head="g" * 40)),
                         ("done", 1))
        self.assertEqual(self.told(dict(conflict, approved=T0)), ("done", 1))

    def test_a_conflict_after_another_reason_still_reaches_the_author(self):
        # The reviewer was told the author's merge wasn't clean; then main
        # moved and the head conflicts: the author must hear it.
        unclean = m.hand_back_key(
            review(REVIEWER), "h" * 40,
            "abc's tree is not the clean merge of its parents")
        self.assertEqual(self.told(dict(unclean), reason=m.CONFLICT_REASON),
                         ("done", 1))

    def test_a_gate_s_refusal_of_a_description_still_counts_alone(self):
        gate = m.hand_back_key(review(REVIEWER), "h" * 40, "gate", "d1")
        # The same refusal of the same description, in other words: said.
        self.assertEqual(self.told(dict(gate), reason="gate, again",
                                   text="d1"), ("already said", 0))
        # Another description, or anything after it: the author may have
        # fixed the description alone.
        self.assertEqual(self.told(dict(gate), reason="gate", text="d2"),
                         ("done", 1))
        self.assertEqual(self.told(dict(gate), reason=m.CONFLICT_REASON),
                         ("done", 1))

    def test_a_refusal_for_now_does_not_stand_for_another_reason(self):
        now = dict(m.hand_back_key(review(REVIEWER), "h" * 40, "gate", "d1"),
                   retry=True, at=T1)
        self.assertEqual(self.told(dict(now), reason="gate", text="d1",
                                   retry=True, at=T2), ("already said", 0))
        self.assertEqual(self.told(dict(now), reason="other gate",
                                   text="d1"), ("done", 1))

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

    def test_it_lists_the_approved_ones_held_for_another_merge(self):
        # agent-forge #256: the build check starts them last.
        other = self.other_pr()
        other["body"] = "Blocked-by: #5"
        self.tick([self.item(), other], live=True)
        self.assertEqual(self.next_json()["blocked"], [6])

    def test_blocked_names_only_holds_another_merge_clears(self):
        self.assertEqual(m.blocked_prs({
            "6": "Blocked-by: #5", "7": "stacked on #6",
            "8": "stacked on #6, which merged; waiting for: no Reviewer "
                 "approval", "9": "labelled hold", "10": "deferred until x",
            "11": "unparsed Blocked-by line: soon"}), [6, 7, 8])

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
        data["merge"] = {"gate": GATE, "own_head_green": False,
                         "stall_alert": dict(
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
        self.assertIs(m.OWN_HEAD_GREEN, True)

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

    def test_below_is_the_merged_layers_number_or_left_out(self):
        argv = ["forge-closing-keywords", "--pr", "{pr}", "--below",
                "{below}"]
        self.assertEqual(m.gate_args(argv, "/r", 7, "b", "h", 6)[1:],
                         ["--pr", "7", "--below", "6"])
        self.assertEqual(m.gate_args(argv, "/r", 7, "b", "h")[1:],
                         ["--pr", "7", "--below"])

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

    def test_an_overdue_one_that_conflicts_ends_the_list(self):
        # designs/0037: nothing is predicted past an overdue one.
        q = [self.pr_on(1, self.base, {"shared.txt": "ONE\n"}),
             self.pr_on(2, self.base, {"shared.txt": "uno\n"}),
             self.pr_on(3, self.base, {"c.txt": "c\n"})]
        got = m.predict_ahead(self.repo.path, self.base, q, depth=1,
                              overdue={2: 95})
        self.assertEqual([e["pr"] for e in got], [1])

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
             for n in range(1, m.DEFAULT_AHEAD_DEPTH + 4)]
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


class PredictLayerOnItsBase(unittest.TestCase):
    """A layer that changes its lower layer's lines again is predicted
    right behind it: the lower layer's prediction has its head as a
    second parent, so the layer merges from its real merge base (the
    lower head), not from where the stack left the default branch."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repo(tmp.name)
        self.repo.branch("main", "main")
        self.repo.commit("l.txt", "1\n2\n3\n", "main l.txt")
        self.repo.push("main")
        self.repo.branch("lower", "main")
        self.lower = self.repo.commit("l.txt", "1\nA\n3\n", "lower")
        self.repo.publish_pr(5, self.lower)
        self.repo.branch("upper", self.lower)
        self.upper = self.repo.commit("l.txt", "1\nB\n3\n", "upper")
        self.repo.publish_pr(6, self.upper)

    def test_the_layer_boards_behind_its_base(self):
        got = m.predict_ahead(self.repo.path, self.repo.main_tip(),
                              [pr(5, T1, head=self.lower),
                               pr(6, T1, head=self.upper)])
        self.assertEqual([e["pr"] for e in got], [5, 6])
        self.assertEqual(got[1]["tree"],
                         run_git(self.repo.path, "rev-parse",
                                 self.upper + "^{tree}"))
        # The tested commit keeps one parent, so an update of #5's head
        # to the same tree is the same prediction; its graph holds #5.
        def parents(sha):
            return run_git(self.repo.path, "rev-list", "--parents", "-n",
                           "1", sha).split()[1:]
        self.assertEqual(parents(got[0]["sha"]), [self.repo.main_tip()])
        self.assertEqual(parents(got[0]["graph"]),
                         [self.repo.main_tip(), self.lower])
        self.assertEqual(parents(got[1]["sha"]), [got[0]["sha"]])

    def test_a_head_already_in_the_parent_is_not_a_parent_again(self):
        entry, why = m.predict_one(self.repo.path, self.lower,
                                   pr(5, T1, head=self.lower))
        self.assertIsNone(why)
        parents = run_git(self.repo.path, "rev-list", "--parents", "-n",
                          "1", entry["sha"]).split()[1:]
        self.assertEqual(parents, [self.lower])
        self.assertNotIn("graph", entry)


class PredictRestacked(unittest.TestCase):
    """merge.stacks.predict_restacked (designs/0042): a layer whose lower
    layer merged is predicted, and checked early, as the Merger will
    restack it (`decide`'s RESTACK, then STACK-UPDATE), not as its head
    merges plainly."""

    LINES = ["1", "X", "3", "4", "5", "6", "7", "8", "9"]

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.repo = Repo(tmp.name)
        self.base = self.repo.main_tip()

    def lines(self, **at):
        return "".join(at.get("l%d" % n, line) + "\n"
                       for n, line in enumerate(self.LINES, 1))

    def stack(self, later, new=False):
        """main has l.txt (unless `new`); #5 changes it (or adds it), #6
        on it sets line 8 to U, then #5 makes it `later`, and main
        squashes #5's final head. Returns #6's pull request."""
        if not new:
            self.repo.branch("main", "main")
            self.repo.commit("l.txt", self.lines(l2="2"), "main l.txt")
            self.repo.push("main")
        self.repo.branch("lower", "main")
        first = self.repo.commit("l.txt", self.lines(), "lower l.txt")
        self.repo.branch("upper", first)
        self.upper = self.repo.commit("l.txt", self.lines(l8="U"),
                                      "upper l.txt")
        self.repo.publish_pr(6, self.upper)
        self.repo.branch("lower", "lower")
        self.lower = self.repo.commit("l.txt", later, "lower again")
        main = self.repo.main_tip()
        squash = run_git(self.repo.path, "commit-tree",
                         self.lower + "^{tree}", "-p", main, "-m", "#5")
        run_git(self.repo.path, "push", "-q", "origin", squash + ":main")
        run_git(self.repo.path, "fetch", "-q", "origin")
        self.restacks = {6: (5, self.lower)}
        return pr(6, T1, head=self.upper)

    def other(self, number, name):
        self.repo.branch("o%d" % number, self.repo.main_tip())
        sha = self.repo.commit(name, "x\n", "#%d" % number)
        self.repo.publish_pr(number, sha)
        return pr(number, T1, head=sha)

    def restacked_by_hand(self):
        """#6 with #5's final head merged in, as RESTACK makes it."""
        self.repo.branch("restacked", self.upper)
        return self.repo.merge(self.lower)

    def predict(self, queue, restacks=None, **kw):
        return m.predict_ahead(self.repo.path, self.repo.main_tip(), queue,
                               restacks=restacks, **kw)

    def test_the_configuration(self):
        self.addCleanup(m.configure, project())
        m.configure(project(stacks={"predict_restacked": True},
                            test_ahead={}))
        self.assertIs(m.PREDICT_RESTACKED, True)
        m.configure(project(stacks={"predict_restacked": True}))
        self.assertIs(m.PREDICT_RESTACKED, False)
        m.configure(project(stacks={}, test_ahead={}))
        self.assertIs(m.PREDICT_RESTACKED, False)

    def test_a_layer_is_predicted_as_the_merger_restacks_it(self):
        layer = self.stack(self.lines(l3="Y"))
        seven, eight = self.other(7, "g.txt"), self.other(8, "h.txt")
        # Plainly, it conflicts with main.
        self.assertEqual(m.git(self.repo.path, "merge-tree", "--write-tree",
                               self.repo.main_tip(),
                               self.upper).returncode, 1)
        got = self.predict([seven, layer, eight], self.restacks)
        self.assertEqual([e["pr"] for e in got], [7, 6, 8])
        self.assertIs(got[1].get("restacked"), True)
        self.assertNotIn("same_as_head", got[1])
        self.assertNotIn("restacked", got[0])
        self.assertNotIn("restacked", got[2])
        # What STACK-UPDATE would push once #7 has merged: main merged
        # into the restacked head against #5's final head.
        expected = m.based_merge(self.repo.path, got[0]["sha"],
                                 self.restacked_by_hand(), self.lower)
        self.assertIsNotNone(expected)
        self.assertEqual(got[1]["tree"], expected)
        self.assertEqual(run_git(self.repo.path, "rev-parse",
                                 got[1]["sha"] + "^"), got[0]["sha"])
        # The same inputs give the same commits.
        self.assertEqual(self.predict([seven, layer, eight], self.restacks),
                         got)

    def test_a_train_keeps_a_layer_through_its_restack(self):
        # agent-forge #281: RESTACK's head, with STACK-UPDATE still to
        # come, had sent the layer out of its train as a push.
        self.addCleanup(m.configure, project())
        m.configure(urgent_project(test_ahead={
            "batch": 3, "batches": 2, "depth": 5, "trains": "live"}))
        layer = self.stack(self.lines(l3="Y"))
        # main moves on past #5's squash, so the halfway head's own tree
        # is not the predicted one.
        self.repo.branch("onward", self.repo.main_tip())
        onward = self.repo.commit("m.txt", "m\n", "main moves on")
        run_git(self.repo.path, "push", "-q", "origin", onward + ":main")
        run_git(self.repo.path, "fetch", "-q", "origin")
        seven = self.other(7, "g.txt")
        open_numbers = {6, 7}
        entries, sizes, _ = m.cut_trains(
            self.repo.path, self.repo.main_tip(), [[layer], [seven]], None,
            {6: self.upper, 7: seven["headRefOid"]}, open_numbers,
            restacks=self.restacks)
        self.assertEqual(m.train_lists(entries, sizes), [[6, 7]])
        _, _, later = m.train_batches(entries, sizes, lambda t: None)
        last = {"base": self.repo.main_tip(), "trains": True,
                "entries": entries, "later": later,
                "batch": {"size": sizes[0],
                          "trees": [e["tree"] for e in entries]}}
        halfway = self.restacked_by_hand()
        self.repo.publish_pr(6, halfway)
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[pr(6, T1, head=halfway)], [seven]], last,
            {6: halfway, 7: seven["headRefOid"]}, open_numbers,
            restacks=self.restacks)
        self.assertEqual(events, [])
        self.assertEqual(m.train_lists(got, sizes), [[6, 7]])
        self.assertEqual([e["tree"] for e in got],
                         [e["tree"] for e in entries])
        # Any other push still takes it out.
        self.repo.branch("other", self.upper)
        pushed = self.repo.commit("o.txt", "o\n", "a push")
        self.repo.publish_pr(6, pushed)
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[pr(6, T1, head=pushed)], [seven]], last,
            {6: pushed, 7: seven["headRefOid"]}, open_numbers,
            restacks=self.restacks)
        self.assertEqual(events[0], ("left", "LEFT #6: pushed %s mid-train"
                                     % pushed[:10]))

    def boarded(self):
        """#6 (a layer whose lower layer merged) and #7 cut into one
        train, with main moved on past #5's squash: (seven, last)."""
        self.addCleanup(m.configure, project())
        m.configure(project(stacks={"predict_restacked": True}, test_ahead={
            "batch": 3, "batches": 2, "depth": 5, "trains": "live"}))
        layer = self.stack(self.lines(l3="Y"))
        self.repo.branch("onward", self.repo.main_tip())
        onward = self.repo.commit("m.txt", "m\n", "main moves on")
        run_git(self.repo.path, "push", "-q", "origin", onward + ":main")
        run_git(self.repo.path, "fetch", "-q", "origin")
        seven = self.other(7, "g.txt")
        entries, sizes, _ = m.cut_trains(
            self.repo.path, self.repo.main_tip(), [[layer], [seven]], None,
            {6: self.upper, 7: seven["headRefOid"]}, {6, 7},
            restacks=self.restacks)
        self.assertEqual(m.train_lists(entries, sizes), [[6, 7]])
        _, _, later = m.train_batches(entries, sizes, lambda t: None)
        return seven, {"base": self.repo.main_tip(), "trains": True,
                       "entries": entries, "later": later,
                       "batch": {"size": sizes[0],
                                 "trees": [e["tree"] for e in entries]}}

    def updated(self, head):
        """`head` with main merged in, as GitHub's update makes it."""
        self.repo.branch("updated", head)
        return self.repo.merge(self.repo.main_tip())

    def recut(self, seven, last, head, queued=True):
        self.repo.publish_pr(6, head)
        return m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            ([[pr(6, T1, head=head)]] if queued else []) + [[seven]], last,
            {6: head, 7: seven["headRefOid"]}, {6, 7}, restacks=None)

    def test_the_mergers_own_restack_of_another_stack_stays(self):
        # 2026-11-11: the tick acting on another stack had only that
        # stack's restacks, so this layer's RESTACK read as a push and
        # its train and every later one were cut again.
        seven, last = self.boarded()
        got, sizes, events = self.recut(seven, last,
                                        self.restacked_by_hand())
        self.assertEqual(events, [])
        self.assertEqual(m.train_lists(got, sizes), [[6, 7]])

    def test_the_mergers_update_after_its_restack_stays(self):
        seven, last = self.boarded()
        got, sizes, events = self.recut(
            seven, last, self.updated(self.restacked_by_hand()))
        self.assertEqual(events, [])
        self.assertEqual(m.train_lists(got, sizes), [[6, 7]])

    def test_one_out_of_the_queue_still_leaves(self):
        # Out of the queue (its new head's Mac check pending, say), it
        # may not ride: a train holds only what may merge now.
        seven, last = self.boarded()
        head = self.updated(self.restacked_by_hand())
        _, _, events = self.recut(seven, last, head, queued=False)
        self.assertEqual(events[0], ("left", "LEFT #6: pushed %s mid-train"
                                     % head[:10]))

    def test_a_merge_edited_by_hand_is_a_push(self):
        seven, last = self.boarded()
        updated = self.updated(self.restacked_by_hand())
        with open(os.path.join(self.repo.path, "extra.txt"), "w") as f:
            f.write("extra\n")
        run_git(self.repo.path, "add", "extra.txt")
        run_git(self.repo.path, "commit", "-q", "--amend", "--no-edit")
        edited = run_git(self.repo.path, "rev-parse", "HEAD")
        self.assertNotEqual(edited, updated)
        _, _, events = self.recut(seven, last, edited)
        self.assertEqual(events[0], ("left", "LEFT #6: pushed %s mid-train"
                                     % edited[:10]))
        # And so is a commit of the author's on top of an update.
        self.repo.branch("more", updated)
        more = self.repo.commit("n.txt", "n\n", "more")
        _, _, events = self.recut(seven, last, more)
        self.assertEqual(events[0], ("left", "LEFT #6: pushed %s mid-train"
                                     % more[:10]))

    def main_first(self, head):
        """A merge of `head` and main with main as its first parent, as
        an author's `git checkout main && git merge` makes it: no
        `own_push`, whose walk follows first parents."""
        tree = run_git(self.repo.path, "merge-tree", "--write-tree",
                       self.repo.main_tip(), head).split()[0]
        return run_git(self.repo.path, "commit-tree", tree, "-p",
                       self.repo.main_tip(), "-p", head, "-m", "merge")

    def test_a_push_that_only_merges_main_keeps_its_place(self):
        # 2026-11-12: a train member whose author merged main left its
        # train as a push, and every train behind it was cut again.
        seven, last = self.boarded()
        merged = self.main_first(seven["headRefOid"])
        self.assertFalse(m.own_push(self.repo.path, seven["headRefOid"],
                                    merged, self.repo.main_tip()))
        self.repo.publish_pr(7, merged)
        upper = last["entries"][0]["head"]
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[pr(6, T1, head=upper)], [pr(7, T1, head=merged)]], last,
            {6: upper, 7: merged}, {6, 7}, restacks=self.restacks)
        self.assertEqual(events, [("kept", "KEPT #7: pushed %s, same tree "
                                   "in its place" % merged[:10])])
        self.assertEqual(m.train_lists(got, sizes), [[6, 7]])
        # The same predictions, so the top's run still counts.
        self.assertEqual([e["sha"] for e in got],
                         [e["sha"] for e in last["entries"]])
        self.assertEqual(got[1]["head"], merged)
        # A change of the author's on top of it still leaves.
        self.repo.branch("more7", merged)
        more = self.repo.commit("g.txt", "changed\n", "more")
        self.repo.publish_pr(7, more)
        _, _, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[pr(6, T1, head=upper)], [pr(7, T1, head=more)]], last,
            {6: upper, 7: more}, {6, 7}, restacks=self.restacks)
        self.assertEqual(events[0], ("left", "LEFT #7: pushed %s mid-train"
                                     % more[:10]))

    def test_behind_one_that_left_the_boarded_head_is_predicted_again(self):
        # The member ahead left, so the train up to it is new: its new
        # head is compared with its old one predicted there.
        seven, last = self.boarded()
        self.repo.branch("push6", last["entries"][0]["head"])
        pushed = self.repo.commit("o.txt", "o\n", "a push")
        self.repo.publish_pr(6, pushed)
        merged = self.main_first(seven["headRefOid"])
        self.repo.publish_pr(7, merged)
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[pr(6, T1, head=pushed)], [pr(7, T1, head=merged)]], last,
            {6: pushed, 7: merged}, {6, 7}, restacks=self.restacks)
        self.assertEqual([c for c, _ in events][:2], ["left", "kept"])
        self.assertIn(7, m.train_lists(got, sizes)[0])

    def test_a_restack_onto_a_main_that_changed_the_lower_lines(self):
        # 2026-11-12: main changed #5's line 2 again after its squash, so
        # #5's final head no longer merges into main as nothing: the
        # restack is still the Merger's own, its second parent a squash
        # on main.
        self.stack(self.lines(l3="Y"))
        self.repo.branch("main", "origin/main")
        self.repo.commit("l.txt", self.lines(l2="Z", l3="Y"), "main l2")
        self.repo.push("main")
        head = self.restacked_by_hand()
        self.assertTrue(m.own_push(self.repo.path, self.upper, head,
                                   self.repo.main_tip()))
        # A branch main has no squash of is still a push.
        self.repo.branch("side", self.upper)
        self.repo.branch("elsewhere", self.base)
        self.repo.commit("e.txt", "e\n", "elsewhere")
        run_git(self.repo.path, "checkout", "-q", "side")
        merged = self.repo.merge("elsewhere")
        self.assertFalse(m.own_push(self.repo.path, self.upper, merged,
                                    self.repo.main_tip()))

    def test_a_verdict_before_the_restack_counts(self):
        # 2026-11-12: the restacked layer's gated instance had passed on
        # the head before the restack, but the walk back stopped at the
        # restack, so the layer was held and left its train as a push.
        self.addCleanup(m.configure, project())
        self.stack(self.lines(l3="Y"))
        head = self.restacked_by_hand()
        self.repo.publish_pr(6, head)
        mac = [status(context="vm/mac")]
        layer = pr(6, T1, head=head,
                   reviews=[review(REVIEWER, oid="b" * 40)])
        layer["labels"] = {"nodes": []}

        def wait(statuses):
            m.configure(gated_project())
            github = FakeGitHub({SLUG + "/commits/" + sha: value
                                 for sha, value in statuses.items()})
            return m.instance_wait(self.repo.path, layer,
                                   self.repo.main_tip(), github, {})

        self.assertIsNone(wait({head: [], self.upper: mac}))
        # So does the build check's green, for merge.own_head_green.
        github = FakeGitHub({SLUG + "/commits/" + sha: value for sha, value
                             in {head: [], "b" * 40: [],
                                 self.upper: [status()]}.items()})
        self.assertIsNone(m.own_green_wait(
            layer, m.approval_of(layer), github, {}, self.repo.path,
            self.repo.main_tip()))
        # A merge of a branch main lacks stops the walk.
        self.repo.branch("elsewhere", self.base)
        self.repo.commit("e.txt", "e\n", "elsewhere")
        self.repo.branch("side", head)
        other = self.repo.merge("elsewhere")
        layer["headRefOid"] = other
        self.assertEqual(wait({other: [], head: [], self.upper: mac}),
                         "waiting for vm/mac (absent)")

    def test_without_the_option_the_layer_is_left_out(self):
        layer = self.stack(self.lines(l3="Y"))
        seven, eight = self.other(7, "g.txt"), self.other(8, "h.txt")
        self.assertEqual([e["pr"] for e in self.predict([seven, layer,
                                                         eight])], [7, 8])
        self.assertEqual([e["pr"] for e in self.predict(
            [seven, layer, eight], {})], [7, 8])

    def test_a_restack_that_conflicts_is_a_conflict(self):
        # #5 later changes the line #6 changed.
        layer = self.stack(self.lines(l8="V"))
        seven, eight = self.other(7, "g.txt"), self.other(8, "h.txt")
        q = [seven, layer, eight]
        self.assertEqual([e["pr"] for e in self.predict(q, self.restacks)],
                         [7, 8])
        self.assertEqual([e["pr"] for e in self.predict(
            q, self.restacks, overdue={6: 95})], [7])
        # First in the queue, it ends the list, as a plain conflict does.
        self.assertEqual(self.predict([layer, seven], self.restacks), [])

    def test_a_layer_that_holds_the_head_below_is_not_merged_with_it(self):
        # l.txt is new in #5, so main and #6 each add it, and a plain
        # merge conflicts even once #6 holds #5's final head.
        self.stack(self.lines(l3="Y"), new=True)
        head = self.restacked_by_hand()
        self.repo.publish_pr(6, head)
        calls = []

        def run(args, cwd=None, env=None):
            calls.append(args)
            return m.sh(args, cwd=cwd, env=env)

        got = m.predict_ahead(self.repo.path, self.repo.main_tip(),
                              [pr(6, T1, head=head)], run,
                              restacks=self.restacks)
        self.assertEqual([(e["pr"], e.get("restacked")) for e in got],
                         [(6, True)])
        self.assertEqual(got[0]["tree"], m.based_merge(
            self.repo.path, self.repo.main_tip(), head, self.lower))
        self.assertNotIn(["merge-tree", "--write-tree", "--no-messages",
                          head, self.lower],
                         [a[3:] for a in calls])

    def early(self, layer, said, restacks):
        github = FakeGitHub({})
        got = m.early_conflicts(self.repo.path, self.repo.main_tip(),
                                [layer], {6: m.approval_of(layer)}, github,
                                said, True, tools_run(), {6}, restacks)
        return got, github.writes

    def test_a_layer_whose_restack_conflicts_is_handed_back_early(self):
        layer = self.stack(self.lines(l8="V"))
        said = {}
        self.assertEqual(self.early(layer, said, None), ([], []))
        got, writes = self.early(layer, said, self.restacks)
        self.assertEqual([(d["pr"], d["reason"], d["detail"], d["early"])
                          for d in got], [(
                              6, m.RESTACK_REASON % (
                                  m.CONFLICT_REASON, 5, self.lower[:10]),
                              m.RESTACK_DETAIL % (5, self.lower[:10],
                                                  "main"), True)])
        self.assertEqual([w[1] for w in writes],
                         [SLUG + "/issues/6/comments"])
        # Said once per approval and head.
        self.assertEqual(self.early(layer, said, self.restacks), ([], []))

    def test_a_layer_whose_restack_is_clean_is_left_alone(self):
        layer = self.stack(self.lines(l3="Y"))
        self.assertEqual(self.early(layer, {}, self.restacks), ([], []))

    def test_a_layer_main_conflicts_with_after_the_restack(self):
        # STACK-UPDATE's merge conflicts: main changed #6's line 8 too.
        layer = self.stack(self.lines(l3="Y"))
        self.repo.branch("main", "origin/main")
        self.repo.commit("l.txt", self.lines(l3="Y", l8="M"), "main l8")
        self.repo.push("main")
        got, _ = self.early(layer, {}, self.restacks)
        self.assertEqual([(d["pr"], d["reason"]) for d in got],
                         [(6, m.CONFLICT_REASON)])

    def test_a_layer_without_a_restack_entry_is_still_left_out(self):
        layer = self.stack(self.lines(l8="V"))
        self.assertEqual(self.early(layer, {}, {7: (5, self.lower)}),
                         ([], []))


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

    def test_two_members_with_one_tree_keep_the_batch(self):
        # Seen on 2026-10-26: one member added nothing on top of the one
        # before it, so the top's tree came twice. Found at its first
        # place, the batch looked changed, and a new top ran again.
        last = {"trees": list("abcdd")}
        self.assertEqual(self.size(self.entries(*"bcddef"), last),
                         (4, None))
        self.assertEqual(self.size(self.entries(*"ddef"), last), (2, None))

    def test_a_changed_member_starts_a_new_batch(self):
        last = {"trees": list("abcd")}
        self.assertEqual(self.size(self.entries(*"bxdef"), last), (4, None))
        self.assertEqual(self.size(self.entries(*"xyz"), last), (3, None))

    def later(self, entries, size, last=None, red=(), started=()):
        verdict = lambda t: False if t in red else None
        return m.later_batches(entries, size, last, verdict,
                               lambda sha, tree: tree in started)

    def test_later_batches_follow_the_first(self):
        # designs/0035 part 2: up to batches - 1 more, end to end.
        m.configure(project(test_ahead={"batch": 2, "batches": 3,
                                        "depth": 7}))
        entries = self.entries(*"abcdefg")
        self.assertEqual(self.later(entries, 2), [
            {"size": 2, "trees": list("cd")},
            {"size": 2, "trees": list("ef")}])
        self.assertEqual(self.later(entries[:5], 2),
                         [{"size": 2, "trees": list("cd")},
                          {"size": 1, "trees": ["e"]}])
        m.configure(project(test_ahead={"batch": 2}))
        self.assertEqual(self.later(entries, 2), [])

    def test_a_started_batch_is_fixed_and_the_next_grows(self):
        m.configure(project(test_ahead={"batch": 3, "batches": 3,
                                        "depth": 8}))
        # #12's run started when the second batch was only c.
        first = self.later(self.entries(*"abc"), 2, started="c")
        self.assertEqual(first, [{"size": 1, "trees": ["c"], "fixed": True}])
        # d and e arrived since: the fixed one keeps its size, and they
        # make the next.
        self.assertEqual(self.later(self.entries(*"abcde"), 2, first), [
            {"size": 1, "trees": ["c"], "fixed": True},
            {"size": 2, "trees": list("de")}])
        # A change in it unfixes it.
        self.assertEqual(self.later(self.entries(*"abxde"), 2, first), [
            {"size": 3, "trees": list("xde")}])

    def test_nothing_after_a_red_batch(self):
        m.configure(project(test_ahead={"batch": 2, "batches": 3,
                                        "depth": 7}))
        entries = self.entries(*"abcdefg")
        self.assertEqual(self.later(entries, 2, red="b"), [])
        self.assertEqual(self.later(entries, 2, red="d"), [
            {"size": 2, "trees": list("cd")}])
        self.assertEqual(self.later(entries, 0), [])

    def test_a_fixed_later_batch_follows_a_merged_one(self):
        # The first batch merged; the later one, fixed, is the batch now
        # at its own size, so its top's run counts for it.
        later = [{"size": 2, "trees": list("ef"), "fixed": True}]
        self.assertEqual(m.batch_size(self.entries(*"efgh"),
                                      {"trees": list("abcd")},
                                      lambda t: None, later), (2, None))
        # Not one that wasn't fixed: that is a new batch.
        later[0].pop("fixed")
        self.assertEqual(m.batch_size(self.entries(*"efgh"),
                                      {"trees": list("abcd")},
                                      lambda t: None, later), (4, None))

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


class Suspects(unittest.TestCase):
    """designs/0035, second amendment: a red batch keeps its suspects,
    and their prefixes are tested front to back at cut points."""

    def cuts(self, n, green=0, red=None, slots=3):
        trees = [str(k) for k in range(1, n + 1)]
        verdict = lambda t: (True if int(t) <= green else
                             False if red and int(t) >= red else None)
        return m.suspect_cuts(trees, verdict, slots)

    def test_the_example(self):
        # 8 suspects, the 7th red, 3 slots: prefixes 3 and 6 first.
        self.assertEqual(self.cuts(8, red=8), (0, 8, [3, 6]))
        # Both green: 1 to 6 merge, and prefix 7 is run alone.
        self.assertEqual(self.cuts(8, green=6, red=8), (6, 8, [7]))
        # It is red: one longer than the green one, so it is named.
        self.assertEqual(self.cuts(8, green=6, red=7), (6, 7, []))

    def test_one_slot_halves(self):
        self.assertEqual(self.cuts(8, red=8, slots=1), (0, 8, [4]))
        self.assertEqual(self.cuts(8, red=4, slots=1), (0, 4, [2]))
        self.assertEqual(self.cuts(2, red=2, slots=1), (0, 2, [1]))

    def test_without_a_known_red_the_whole_set_is_a_cut(self):
        # The tip moved for another reason: every tree is new.
        self.assertEqual(self.cuts(8, slots=3), (0, None, [4, 8]))
        self.assertEqual(self.cuts(3, green=3), (3, None, []))

    def test_the_batches_of_a_round(self):
        self.assertEqual(m.suspect_batches(0, [3, 6]), (3, [3]))
        self.assertEqual(m.suspect_batches(6, [7]), (6, [1]))
        self.assertEqual(m.suspect_batches(6, []), (6, []))
        self.assertEqual(m.suspect_batches(0, []), (0, []))

    def saved(self, *numbers):
        return {"suspects": [{"pr": n, "head": "h%d" % n} for n in numbers],
                "tree": "t", "merged": [], "left": []}

    def queue(self, *numbers, pushed=()):
        return [{"number": n, "headRefOid": "new" if n in pushed
                 else "h%d" % n} for n in numbers]

    def test_a_suspect_that_changes_leaves(self):
        saved, line = m.suspects_left(
            self.saved(5, 6, 7), self.queue(5, 6, 7, pushed=(6,)),
            {"decisions": []}, {}, {})
        self.assertEqual([e["pr"] for e in saved["suspects"]], [5, 7])
        self.assertEqual(saved["left"], [6])
        self.assertIsNone(line)

    def test_every_suspect_merged_is_a_flake(self):
        saved = self.saved(6)
        saved["merged"] = [5]
        record = {"decisions": [{"pr": 6, "action": "MERGE",
                                 "result": "done"}]}
        saved, line = m.suspects_left(saved, self.queue(), record, {}, {})
        self.assertIsNone(saved)
        self.assertEqual(line, "BATCH-FLAKE #5..#6: every suspect merged "
                         "green; the red top's tree t was a flake")

    def test_a_suspect_only_brought_up_to_date_stays(self):
        # 2026-11-12: an update of the first suspect took it out of the
        # search, which then ended, and every train was cut again.
        moved = lambda head, pr: pr["number"] == 6
        saved, _ = m.suspects_left(
            self.saved(5, 6, 7), self.queue(5, 6, 7, pushed=(6, 7)),
            {"decisions": []}, {}, {}, moved)
        self.assertEqual(saved["suspects"], [
            {"pr": 5, "head": "h5"}, {"pr": 6, "head": "new"}])
        self.assertEqual(saved["left"], [7])

    def test_none_left_after_a_change_is_no_flake(self):
        saved = self.saved(5)
        saved["merged"] = [4]
        saved, line = m.suspects_left(saved, self.queue(5, pushed=(5,)),
                                      {"decisions": []}, {}, {})
        self.assertIsNone(saved)
        self.assertIsNone(line)

    def test_suspects_lead_the_queue(self):
        queue = self.queue(9, 6, 5, pushed=(6,))
        self.assertEqual(m.suspect_place(self.saved(5, 6), queue), {5: 0})

    def entries(self, *trees):
        return [{"pr": 10 + k, "sha": "%d" % k * 40, "tree": t,
                 "head": "h%d" % (10 + k)} for k, t in enumerate(trees)]

    def round(self, saved, entries, red=(), green=(), last=None):
        verdict = lambda t: False if t in red else True if t in green \
            else None
        said, record, lines = {}, {}, []
        with tempfile.TemporaryDirectory() as state_dir:
            going = m.suspect_round(state_dir, saved, entries, verdict, last,
                                    said, record, lines.append)
            kept = m.load_suspects(state_dir)
        return going, said, record, lines, kept

    def test_a_round_writes_the_batch_and_the_later_cuts(self):
        m.configure(project(test_ahead={"batch": 8, "batches": 3,
                                        "depth": 9}))
        self.addCleanup(m.configure, project())
        saved = self.saved(*range(10, 18))
        going, said, record, lines, kept = self.round(
            saved, self.entries(*"abcdefgh"), red="h")
        self.assertTrue(going)
        self.assertEqual(said["batch"], {"size": 3, "trees": list("abc"),
                                         "suspects": True})
        self.assertEqual(said["later"], [{"size": 3, "trees": list("def")}])
        self.assertEqual((record["batch"], record["later"]),
                         ([10, 11, 12], [[13, 14, 15]]))
        self.assertEqual(lines, ["SUSPECTS #10..#17: green to none, red "
                                 "from #17; checking #10..#12, #10..#15"])
        self.assertEqual(len(kept["suspects"]), 8)

    def test_a_shorter_red_stops_the_longer_runs(self):
        m.configure(project(test_ahead={"batch": 8, "batches": 3,
                                        "depth": 9}))
        self.addCleanup(m.configure, project())
        entries = self.entries(*"abcdefgh")
        going, said, record, _, kept = self.round(
            self.saved(*range(10, 18)), entries, red="fh", green="abc")
        self.assertTrue(going)
        # 1 to 3 merge; prefixes 4 and 5 run; 6 bounds it.
        self.assertEqual(said["batch"]["size"], 3)
        self.assertEqual(said["later"], [{"size": 1, "trees": ["d"]},
                                         {"size": 1, "trees": ["e"]}])
        self.assertEqual(said["stop"], sorted(e["sha"] for e in entries[6:]))
        self.assertEqual([e["pr"] for e in kept["suspects"]],
                         [10, 11, 12, 13, 14, 15])

    def test_a_red_suspect_at_the_front_ends_the_search(self):
        m.configure(project(test_ahead={"batch": 4}))
        self.addCleanup(m.configure, project())
        going, said, _, lines, kept = self.round(
            self.saved(10, 11), self.entries(*"ab"), red="ab")
        self.assertFalse(going)
        self.assertEqual(said, {})
        self.assertEqual(lines, ["SUSPECTS #10..#11: #10 is red on its "
                                 "own; the search ends"])
        self.assertIsNone(kept)


class WithLayers(unittest.TestCase):
    """designs/0035: a stack's ready layers go straight after their base,
    and what they push out of the batch is owed a place."""

    def prs(self, *numbers, head="h"):
        return [{"number": n, "headRefOid": "%s%d" % (head, n)}
                for n in numbers]

    def run_it(self, queue, layers, last=None, size=3):
        by = {pr["number"]: pr for pr in queue}
        layers_of = lambda pr: [(l, {}) for l in layers.get(pr["number"], ())]
        out, owed = m.with_layers(queue, layers_of, last or {}, size)
        return [pr["number"] for pr in out], owed

    def test_layers_go_after_their_base(self):
        self.assertEqual(self.run_it(self.prs(1, 2, 3),
                                     {1: self.prs(11, 12)}),
                         ([1, 11, 12, 2, 3], [2, 3]))

    def test_without_layers_nothing_is_owed(self):
        self.assertEqual(self.run_it(self.prs(1, 2, 3), {}), ([1, 2, 3], []))

    def test_an_owed_pull_request_is_not_pushed_out_again(self):
        self.assertEqual(self.run_it(self.prs(1, 2, 3),
                                     {1: self.prs(11, 12)}, {"owed": [2]}),
                         ([1, 11, 2, 3], [3]))

    def test_owed_until_a_batch_includes_it(self):
        self.assertEqual(self.run_it(self.prs(1, 2, 3, 4), {},
                                     {"owed": [4]}), ([1, 2, 3, 4], [4]))
        self.assertEqual(self.run_it(self.prs(4, 5), {}, {"owed": [4]}),
                         ([4, 5], []))

    def test_a_running_batch_takes_no_new_layer(self):
        last = {"entries": [{"pr": n, "head": "h%d" % n} for n in (1, 2, 3)],
                "batch": {"size": 3}}
        self.assertEqual(self.run_it(self.prs(1, 2, 3), {1: self.prs(11)},
                                     last), ([1, 2, 3], []))
        # One it predicted already stays.
        last["entries"].insert(1, {"pr": 11, "head": "h11"})
        self.assertEqual(self.run_it(self.prs(1, 2, 3), {1: self.prs(11)},
                                     last), ([1, 11, 2, 3], [3]))
        # A layer of one outside the running batch joins.
        self.assertEqual(self.run_it(self.prs(1, 2, 3, 4),
                                     {4: self.prs(41)}, last, size=3),
                         ([1, 2, 3, 4, 41], []))


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

    def test_a_green_train_merges_whole_in_one_tick(self):
        # merge.test_ahead.train_merge (designs/0047, amendment of
        # 2026-11-11).
        m.configure(project(test_ahead={"batch": 2, "train_merge": True}))
        self.assertIs(m.TRAIN_MERGE, True)
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        origin = self.repo.origin

        def run(args, cwd=None, env=None):
            if "push" in args:
                args = [origin if a.startswith("https://github.com/")
                        else a for a in args]
            return tools_run()(args, cwd, env)
        with mock.patch.object(m, "RESOLVE_IDENTITY", ("Merger", "m@x")), \
                mock.patch.object(m, "CI_WORKFLOW", ""):
            record, lines = self.tick(
                prs, conclusions=(None,), live=True, minutes=1, run=run,
                check_state={"results": {top["sha"]: {
                    "tree": top["tree"], "ok": True}}},
                extra={SLUG + "/pulls/5": {"merged": True},
                       SLUG + "/pulls/6": {"merged": True}})
        self.assertEqual([(d["pr"], d["action"], d.get("train"))
                          for d in record["decisions"]],
                         [(5, "MERGE", True), (6, "MERGE", True)])
        # The pull request the train merged as, for forge-flow.
        self.assertEqual([d.get("train_pr") for d in record["decisions"]],
                         [900, 900])
        self.assertTrue(any(line.startswith("TRAIN-MERGE #5..#6: merged 2 "
                                            "as #900")
                            for line in lines), lines)
        self.assertTrue(m.moved_a_stack(record))

    def test_one_outside_a_building_first_train_waits_for_its_train(self):
        # 2026-11-12: #10784, pushed out of the first train, merged on
        # its own green 25 minutes into that train's run, and every
        # train was cut again.
        m.configure(project(test_ahead={"batch": 1, "batches": 2,
                                        "trains": "live",
                                        "train_merge": True}))
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        said = self.ahead_json()
        self.assertEqual([e["pr"] for e in said["entries"]], [5, 6])
        # As if #5 had left the first train and boarded the next.
        said["entries"].reverse()
        said["batch"] = {"size": 1, "trees": [said["entries"][0]["tree"]]}
        said["later"] = [{"size": 1, "trees": [said["entries"][1]["tree"]],
                          "fixed": True}]
        with open(os.path.join(self.state, m.AHEAD_FILE), "w") as handle:
            json.dump(said, handle)
        top = said["entries"][0]
        record, _ = self.tick(prs, live=True, minutes=1, check_state={
            "results": {}, "ahead_started": {top["sha"]: "x"}})
        five = [d for d in record["decisions"] if d["pr"] == 5]
        self.assertEqual([(d["action"], d.get("reason")) for d in five],
                         [("WAIT", m.TRAIN_AHEAD_REASON % "#6..#6")])
        self.assertNotIn("/merge", " ".join(w[1] for w in self.github.writes))

    def test_an_urgent_one_heads_the_next_train(self):
        # 2026-11-13: an urgent pin (#11215) went in front of a train
        # whose top was testing, cut every train again and spent that
        # run. An urgent one heads the next train cut instead.
        data = urgent_project(test_ahead={"batch": 1, "batches": 2,
                                          "trains": "live",
                                          "train_merge": True})
        m.configure(data)
        self.addCleanup(m.configure, project())
        five = self.item()
        self.tick([five], conclusions=(None,), live=True)
        self.assertEqual([e["pr"] for e in self.ahead_json()["entries"]],
                         [5])
        top = self.ahead_json()["entries"][0]
        hot = labelled(self.other_pr(), "Priority:urgent")
        record, lines = self.tick([five, hot], live=True, minutes=1,
                                  check_state={"results": {},
                                               "ahead_started": {
                                                   top["sha"]: "x"}})
        self.assertFalse([l for l in lines if "RECUT" in l], lines)
        # It heads the next train, behind #5's.
        self.assertEqual(record["ahead"], [5, 6])
        self.assertEqual(record["trains"], [[5], [6]])

    def behind_main_on_a_train(self, **ahead):
        """#5 and #6 boarded on one train, then main moves on, so #5,
        first in the queue, is behind it: the second tick's record."""
        m.configure(project(test_ahead=dict({"batch": 2, "trains": "live"},
                                            **ahead)))
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        self.assertEqual(self.ahead_json()["batch"]["size"], 2)
        self.advance_main("n.txt")
        record, _ = self.tick(prs, live=True, minutes=1)
        return record

    def test_a_train_that_merges_as_one_is_not_updated_member_by_member(self):
        # Amendment of 2026-11-12: with train_merge, the first member's
        # update only started a build of its new head; the train goes in
        # as one merge of its tested top.
        record = self.behind_main_on_a_train(train_merge=True)
        five = [d for d in record["decisions"] if d["pr"] == 5]
        self.assertEqual([(d["action"], d.get("reason")) for d in five],
                         [("WAIT", m.TRAIN_WAIT_REASON % 2)])
        self.assertNotIn("update-branch",
                         " ".join(w[1] for w in self.github.writes))

    def test_a_train_github_would_not_take_is_updated_as_before(self):
        m.configure(project(test_ahead={"batch": 2, "trains": "live",
                                        "train_merge": True}))
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        self.advance_main("n.txt")
        # The default branch moved to a tree the train predicted (as
        # when the train ahead merged): here, by pretending its base.
        said = self.ahead_json()
        said["base"] = self.repo.main_tip()
        m.save_json(os.path.join(self.state, m.AHEAD_FILE), said)
        with mock.patch.object(m, "CI_WORKFLOW", "ci.yml"):
            record, lines = self.tick(
                prs, live=True, minutes=1, check_state={"results": {
                    top["sha"]: {"tree": top["tree"], "ok": True}}})
        self.assertTrue(any("each member merges on its own" in line
                            for line in lines), lines)
        self.assertEqual([d["action"] for d in record["decisions"]
                          if d["pr"] == 5], ["UPDATE"])

    def test_without_train_merge_the_first_member_is_updated(self):
        record = self.behind_main_on_a_train()
        self.assertEqual([d["action"] for d in record["decisions"]
                          if d["pr"] == 5], ["UPDATE"])

    def unproven(self, board):
        """#5 and #6, approved, their own build checks still pending:
        the first tick's record and lines, with own_green_to_board
        `board`."""
        m.configure(project(own_head_green=True, test_ahead={
            "batch": 2, "trains": "live", "own_green_to_board": board}))
        self.prs = [self.item(), self.other_pr()]
        pending = [status("pending")]
        self.pending = {SLUG + "/commits/" + p["headRefOid"]: pending
                        for p in self.prs}
        self.pending[SLUG + "/commits/" + self.approved] = pending
        return self.tick(self.prs, conclusions=(None,), live=True,
                         extra=self.pending)

    def test_with_own_green_to_board_false_they_board_without_it(self):
        # 2026-11-12: four approved pull requests whose own checks were
        # queued behind a hundred others waited an hour, held, while
        # the one train ran; the train's top tests them anyway.
        self.assertIs(m.OWN_GREEN_TO_BOARD, True)
        record, lines = self.unproven(False)
        self.assertIs(m.OWN_GREEN_TO_BOARD, False)
        self.assertEqual((record["queue"], sorted(record["held"])),
                         ([], ["5", "6"]))
        self.assertEqual(record["trains"], [[5, 6]])
        self.assertIn("TRAIN-CUT 1: #5 #6", lines)
        # Boarded, they are candidates at the heads they boarded with.
        record, _ = self.tick(self.prs, conclusions=(None,), live=True,
                              minutes=1, extra=self.pending)
        self.assertEqual(record["held"], {})
        self.assertEqual(record["queue"], [5, 6])
        self.assertEqual(record["trains"], [[5, 6]])

    def test_by_default_they_wait_for_their_own_green(self):
        record, lines = self.unproven(True)
        self.assertEqual(sorted(record["held"]), ["5", "6"])
        self.assertNotIn("trains", record)
        self.assertFalse([l for l in lines if l.startswith("TRAIN-CUT")])

    def test_one_red_on_its_own_head_does_not_board(self):
        m.configure(project(own_head_green=True, test_ahead={
            "batch": 2, "trains": "live", "own_green_to_board": False}))
        prs = [self.item(), self.other_pr()]
        failed = [status("failure")]
        extra = {SLUG + "/commits/" + p["headRefOid"]: failed for p in prs}
        extra[SLUG + "/commits/" + self.approved] = failed
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              extra=extra, check_state={"results": {
                                  prs[1]["headRefOid"]: {
                                      "ok": False, "failed": ["test"]}}})
        self.assertEqual(record["trains"], [[5]])
        self.assertEqual(record["train_waits"],
                         {"6": "its own head is red"})

    def red_train(self, train_merge=True):
        """#5 and #6 boarded on one train, then main moves on, so #5 is
        behind it, and the train's top is found red: the second tick's
        record."""
        m.configure(project(test_ahead={"batch": 2, "trains": "live",
                                        "train_merge": train_merge}))
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        self.advance_main("n.txt")
        record, lines = self.tick(prs, live=True, minutes=1, check_state={
            "results": {top["sha"]: {"tree": top["tree"], "ok": False}}})
        return record, lines

    def test_a_red_trains_members_are_not_updated(self):
        # 2026-11-12: the tick that found a train's top red updated its
        # first member, a suspect, whose new head then took it out of
        # the search, and every train was cut again.
        record, _ = self.red_train()
        five = [d for d in record["decisions"] if d["pr"] == 5]
        self.assertEqual([(d["action"], d.get("reason")) for d in five],
                         [("WAIT", m.SUSPECT_WAIT_REASON % "#5..#6")])
        self.assertNotIn("update-branch",
                         " ".join(w[1] for w in self.github.writes))

    def test_without_train_merge_a_red_trains_member_is_updated(self):
        record, _ = self.red_train(train_merge=False)
        self.assertEqual([d["action"] for d in record["decisions"]
                          if d["pr"] == 5], ["UPDATE"])

    def test_a_red_train_is_kept_through_its_search(self):
        # 2026-11-12: a red train's search wrote batches of its own, so
        # ahead.json held no trains when it ended, and the trains were
        # cut afresh, with new pull requests in the middle of them.
        m.configure(project(test_ahead={"batch": 2, "batches": 2,
                                        "trains": "live",
                                        "train_merge": True}))
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        red = {"results": {top["sha"]: {"tree": top["tree"], "ok": False}}}
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=1, check_state=red)
        self.assertTrue(any(l.startswith("BATCH #5..#6: top red")
                            for l in lines), lines)
        kept = self.ahead_json()["kept_trains"]
        self.assertEqual([e["pr"] for e in kept["entries"]], [5, 6])
        # The search goes on: the trains are still kept.
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=2, check_state=red)
        self.assertNotIn("trains", self.ahead_json())
        self.assertEqual(self.ahead_json()["kept_trains"], kept)
        # It ends (here, by hand), and a new pull request has come: the
        # train is kept, and the new one goes behind it.
        os.remove(os.path.join(self.state, m.SUSPECTS_FILE))
        seven = self.other_pr(7)
        seven["createdAt"] = "2026-10-07T09:00:00Z"
        record, lines = self.tick(prs + [seven], conclusions=(None,),
                                  live=True, minutes=3, check_state=red)
        self.assertEqual(record["trains"], [[5, 6], [7]])
        self.assertFalse(any(l.startswith("TRAIN-CUT 1") for l in lines),
                         lines)

    def test_trains_before(self):
        trains = {"trains": True, "entries": []}
        kept = {"trains": True, "entries": [{"pr": 5}]}
        self.assertIs(m.trains_before(trains), trains)
        searching = {"entries": [], "kept_trains": kept}
        self.assertIs(m.trains_before(searching), kept)
        self.assertEqual(m.trains_before({}), {})
        self.assertIsNone(m.trains_before(None))
        self.assertEqual(m.kept_trains("m", [{"pr": 5}, {"pr": 6}], [1, 1],
                                       {"urgent_cut": [6]}), {
            "trains": True, "base": "m", "entries": [{"pr": 5}, {"pr": 6}],
            "batch": {"size": 1}, "later": [{"size": 1}],
            "urgent_cut": [6]})

    def test_suspect_wait(self):
        entries = [{"pr": 5, "head": "a", "tree": "t5"},
                   {"pr": 6, "head": "b", "tree": "t6"}]
        last = {"trains": True, "entries": entries,
                "batch": {"size": 2, "trees": ["t5", "t6"]}}
        five = {"number": 5, "headRefOid": "a"}
        red = {"results": {"x": {"tree": "t6", "ok": False}}}
        wait = m.SUSPECT_WAIT_REASON % "#5..#6"
        # The tick a train's top is red, before its search is saved.
        self.assertEqual(m.suspect_wait(None, five, last, red), wait)
        self.assertIsNone(m.suspect_wait(None, five, last, {}))
        self.assertIsNone(m.suspect_wait(
            None, dict(five, headRefOid="c"), last, red))
        with mock.patch.object(m, "tree_base_red", return_value=["build"]):
            self.assertIsNone(m.suspect_wait(None, five, last, red))
        # While the search runs: a suspect at its head waits, unless the
        # search found it green.
        saved = {"suspects": [{"pr": 5, "head": "a"},
                              {"pr": 6, "head": "b"}]}
        searching = {"entries": entries, "batch": {
            "size": 1, "trees": ["t5"], "suspects": True}}
        self.assertEqual(m.suspect_wait(saved, five, searching, red), wait)
        green = {"results": {"y": {"tree": "t5", "ok": True}}}
        self.assertIsNone(m.suspect_wait(saved, five, searching, green))
        self.assertEqual(m.suspect_wait(
            saved, {"number": 6, "headRefOid": "b"}, searching, green), wait)
        self.assertIsNone(m.suspect_wait(
            saved, {"number": 7, "headRefOid": "b"}, searching, green))

    def test_with_suspects(self):
        five, six, layer = ({"number": n} for n in (5, 6, 9))
        saved = {"suspects": [{"pr": 5}, {"pr": 9}, {"pr": 6}]}
        # A layer held as stacked joins the search where it has it.
        self.assertEqual(m.with_suspects([five, six], saved,
                                         [five, six, layer]),
                         [five, layer, six])
        self.assertEqual(m.with_suspects([five, six], None, [layer]),
                         [five, six])
        self.assertEqual(m.with_suspects([], saved, [layer]), [])
        # A layer gone (merged, closed) is left out.
        self.assertEqual(m.with_suspects([five, six], saved, [five, six]),
                         [five, six])

    def test_train_boarded(self):
        entries = [{"pr": 5, "head": "a", "tree": "t5"},
                   {"pr": 6, "head": "b", "tree": "t6"}]
        last = {"trains": True, "entries": entries,
                "batch": {"size": 2, "trees": ["t5", "t6"]}}
        five = {"number": 5, "headRefOid": "a"}
        self.assertEqual(m.train_boarded(last, five, {}), 2)
        # At another head, or not on the first train, it is updated.
        self.assertIsNone(m.train_boarded(last, dict(five, headRefOid="c"),
                                          {}))
        self.assertIsNone(m.train_boarded(last, {"number": 7,
                                                 "headRefOid": "a"}, {}))
        # A train of one waits too: an update would move the default
        # branch off the tree the next train was predicted on. Not
        # batches that aren't trains, nor a train whose top is red.
        one = dict(last, entries=entries[:1],
                   batch={"size": 1, "trees": ["t5"]})
        self.assertEqual(m.train_boarded(one, five, {}), 1)
        self.assertIsNone(m.train_boarded(dict(last, trains=False), five,
                                          {}))
        red = {"results": {"x": {"tree": "t6", "ok": False}}}
        self.assertIsNone(m.train_boarded(last, five, red))

    def test_later_batches_are_written_and_fixed_once_started(self):
        # designs/0035 part 2.
        m.configure(project(test_ahead={"batch": 2, "batches": 2,
                                        "depth": 3}))
        self.repo.branch("third", "origin/main")
        sha = self.repo.commit("t.txt", "third\n", "third")
        self.repo.publish_pr(7, sha)
        self.repo.branch("feature", "feature")
        third = pr(7, T1, head=sha, reviews=[review(REVIEWER, oid=sha)])
        prs = [self.item(), self.other_pr(), third]
        record, _ = self.tick(prs, conclusions=(None,), live=True)
        ahead = self.ahead_json()
        self.assertEqual(record["batch"], [5, 6])
        self.assertEqual(record["later"], [[7]])
        top = ahead["entries"][2]
        self.assertEqual(ahead["later"], [{"size": 1,
                                           "trees": [top["tree"]]}])
        self.tick(prs, conclusions=(None,), live=True, minutes=1,
                  check_state={"results": {},
                               "ahead_started": {top["sha"]: "x"}})
        self.assertEqual(self.ahead_json()["later"], [
            {"size": 1, "trees": [top["tree"]], "fixed": True}])

    def test_fewer_batches_while_re_cuts_are_frequent(self):
        # designs/0044, rule 5: more than 2 re-cuts of a started batch in
        # the last hour, so one batch until fewer.
        m.configure(project(test_ahead={"batch": 2, "batches": 2,
                                        "depth": 3,
                                        "unstable_after_recuts": 2}))
        self.repo.branch("third", "origin/main")
        sha = self.repo.commit("t.txt", "third\n", "third")
        self.repo.publish_pr(7, sha)
        self.repo.branch("feature", "feature")
        third = pr(7, T1, head=sha, reviews=[review(REVIEWER, oid=sha)])
        prs = [self.item(), self.other_pr(), third]
        state = os.path.join(self.tmp.name, "state")
        os.makedirs(state, exist_ok=True)
        with open(os.path.join(state, "merger.jsonl"), "w") as handle:
            for at in ("10:59", "11:10", "11:20", "11:30"):
                handle.write(json.dumps({"at": "2026-10-07T%s:00Z" % at,
                                         "recut": True}) + "\n")
        record, lines = self.tick(prs, conclusions=(None,), live=True)
        self.assertEqual(record["unstable"], 3)
        self.assertEqual(record["batch"], [5, 6])
        self.assertNotIn("later", record)
        self.assertIn("UNSTABLE: 3 re-cuts of a started batch in the last "
                      "hour, over 2; predicting 1 batch until fewer", lines)
        # Said once.
        _, lines = self.tick(prs, conclusions=(None,), live=True, minutes=1)
        self.assertFalse([l for l in lines if l.startswith("UNSTABLE")])
        # An hour on, the re-cuts are older than the window.
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=31)
        self.assertNotIn("unstable", record)
        self.assertEqual(record["later"], [[7]])
        self.assertIn("SETTLED: 2 batches in flight again", lines)

    def test_a_re_cut_is_recorded(self):
        m.configure(project(test_ahead={"batch": 2}))
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        started = {"ahead_started": {top["sha"]: "x"}, "results": {}}
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=1, check_state=started)
        self.assertNotIn("recut", record)
        # #6 is pushed to: the started batch is re-cut.
        six = dict(prs[1], headRefOid=self.head)
        record, _ = self.tick([prs[0], six], conclusions=(None,), live=True,
                              minutes=2, check_state=started)
        self.assertIs(record["recut"], True)

    def test_a_red_top_splits_the_batch(self):
        m.configure(project(test_ahead={"batch": 2}))
        self.tick([self.item(), self.other_pr()], conclusions=(None,),
                  live=True)
        top = self.ahead_json()["entries"][1]
        record, lines = self.tick(
            [self.item(), self.other_pr()], conclusions=(None,), live=True,
            check_state={"results": {top["sha"]: {"tree": top["tree"],
                                                  "ok": False}}})
        # designs/0035, second amendment: its members are the suspects,
        # and with one slot the first half is checked.
        self.assertIn("BATCH #5..#6: top red; its 2 members are the "
                      "suspects", lines)
        self.assertIn("SUSPECTS #5..#6: green to none, red from #6; "
                      "checking #5..#5", lines)
        self.assertEqual(record["batch"], [5])
        self.assertEqual(self.ahead_json()["batch"]["size"], 1)
        saved = m.load_suspects(self.state)
        self.assertEqual([e["pr"] for e in saved["suspects"]], [5, 6])
        self.assertEqual(saved["tree"], top["tree"])

    def test_the_suspects_keep_their_places_and_nothing_joins(self):
        # Seen on 2026-10-26: a red batch's halves regrew to the full
        # batch with the red one still in it, whenever one joined in
        # front.
        m.configure(project(test_ahead={"batch": 3}))
        self.repo.branch("third", "origin/main")
        sha = self.repo.commit("t.txt", "third\n", "third")
        self.repo.publish_pr(4, sha)
        self.repo.branch("feature", "feature")
        early = pr(4, T0, head=sha, reviews=[review(REVIEWER, oid=sha)])
        # Made once: each other_pr() commits again, and a commit made in
        # the next second is a new head, so #6 would leave the suspects
        # by chance (the test failed now and then).
        other = self.other_pr()
        self.tick([self.item(), other], conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        red = {"results": {top["sha"]: {"tree": top["tree"], "ok": False}}}
        self.tick([self.item(), other], conclusions=(None,), live=True,
                  check_state=red)
        # #4, older, would go first: it waits behind the suspects.
        record, _ = self.tick([early, self.item(), other],
                              conclusions=(None,), live=True, minutes=1,
                              check_state=red)
        self.assertEqual(record["ahead"][:2], [5, 6])
        self.assertEqual(record["batch"], [5])
        # #6 is pushed to: it leaves, and the search has #5 alone.
        six = dict(other, headRefOid=self.head)
        self.tick([self.item(), six], conclusions=(None,), live=True,
                  minutes=2, check_state=red)
        self.assertEqual([e["pr"] for e in m.load_suspects(
            self.state)["suspects"]], [5])

    def red_alone(self, rerun_label=None):
        """#5 and #6 a batch whose top is red, then #5 red on its own:
        the ticks' records and lines, and the red check state. With
        `rerun_label`, as labels.rerun."""
        configured = project(test_ahead={"batch": 2})
        if rerun_label:
            configured.data.setdefault("labels", {})["rerun"] = rerun_label
        m.configure(configured)
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        red = {"results": {top["sha"]: {"tree": top["tree"], "ok": False}}}
        self.tick(prs, conclusions=(None,), live=True, check_state=red)
        alone = self.ahead_json()["entries"][0]
        red["results"][alone["sha"]] = {"tree": alone["tree"], "ok": False}
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=1, check_state=red)
        return prs, record, lines, red

    def test_a_suspect_red_on_its_own_is_handed_back_and_kept_out(self):
        # designs/0044, rule 4. One project, 2026-11-04: such a suspect
        # was put back into a batch three more times before it was
        # handed back.
        prs, record, lines, red = self.red_alone()
        self.assertIn("SUSPECTS #5..#6: #5 is red on its own; the search "
                      "ends", lines)
        self.assertIn("SUSPECT-RED #5: red on its own at %s; handed back, "
                      "and out of batches until a new head"
                      % self.head[:10], lines)
        back = [d for d in record["decisions"] if d.get("suspect")]
        self.assertEqual([(d["pr"], d["to"], d["result"]) for d in back],
                         [(5, "author", "done")])
        self.assertEqual(back[0]["reason"], m.RED_SUSPECT_REASON % (
            m.STATUS_CONTEXT, "main"))
        body = self.github.writes[-1][2]["body"]
        self.assertIn("It stays out of the Merger's batches", body)
        self.assertEqual(record["ahead"], [6])
        # Later ticks: handed back once, never updated, merged or
        # predicted at that head.
        for minutes in (2, 3):
            record, lines = self.tick(prs, conclusions=("success",),
                                      live=True, minutes=minutes,
                                      check_state=red)
            for d in record["decisions"]:
                if d["pr"] == 5:
                    self.assertEqual(d, {
                        "action": "HAND-BACK", "to": "author", "pr": 5,
                        "head": self.head, "reason": back[0]["reason"],
                        "result": "already said"})
            self.assertNotIn(5, record["ahead"])
            self.assertFalse([l for l in lines if "SUSPECT-RED" in l])
            self.assertFalse([w for w in self.github.writes
                              if "/issues/5/" in w[1] or "/pulls/5/" in w[1]])
        # A new head is new evidence.
        head = self.repo.commit("f4.txt", "fixed\n", "fixed")
        self.repo.publish_pr(5, head)
        fixed = pr(5, head=head, reviews=[review(REVIEWER, oid=head)])
        record, _ = self.tick([fixed, prs[1]], conclusions=(None,),
                              live=True, minutes=4)
        self.assertIn(5, record["ahead"])
        self.assertEqual(m.red_suspects(self.state, [fixed]), {})

    def rerun(self, red, finished, ok=True):
        """`red` with a verdict on #5's own head that finished at
        `finished`, merged with main's tip."""
        red["results"][self.head] = {"pr": 5, "ok": ok, "finished": finished,
                                     "base": self.repo.main_tip(),
                                     "rerun_of": ["test"]}
        return red

    def test_a_red_suspect_records_when_and_on_what_it_was_found(self):
        _, record, _, _ = self.red_alone()
        saved = m.load_json(os.path.join(self.state, m.RED_SUSPECT_FILE), {})
        self.assertEqual(saved["5"]["at"], record["at"])
        self.assertEqual(saved["5"]["main"], self.repo.main_tip())

    def test_the_hand_back_names_both_ways_out(self):
        self.red_alone()
        self.assertIsNone(m.RERUN_LABEL)
        body = self.github.writes[-1][2]["body"]
        self.assertIn("until this pull request has a new head, or until the "
                      "build check's own run of this head", body)
        self.assertNotIn("label", body)

    def test_a_green_rerun_after_the_red_releases_it(self):
        # designs/0044, rule 4, amendment: on 2026-11-04 a timing test
        # failed under load, and only a new head brought it back.
        prs, record, lines, red = self.red_alone()
        self.rerun(red, "2026-10-07T12:01:30Z")
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=2, check_state=red)
        self.assertIn("SUSPECT-CLEARED #5: green rerun at %s; back in the "
                      "queue" % self.head[:10], lines)
        self.assertEqual(record["suspects_cleared"], [5])
        self.assertFalse([d for d in record["decisions"]
                          if d["pr"] == 5 and d["action"] == "HAND-BACK"])
        self.assertIn(5, record["ahead"])
        notes = [w for w in self.github.writes
                 if w[1] == SLUG + "/issues/5/comments"]
        self.assertEqual(len(notes), 1)
        self.assertIn("finished green at 2026-10-07T12:01:30Z", notes[0][2][
            "body"])
        self.assertTrue(notes[0][2]["body"].endswith(m.SIGNATURE))
        self.assertEqual(m.load_json(os.path.join(
            self.state, m.RED_SUSPECT_FILE), {}), {})
        self.assertNotIn("5", m.load_json(os.path.join(
            self.state, "handed-back.json"), {}))
        # Said once.
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=3, check_state=red)
        self.assertNotIn("suspects_cleared", record)
        self.assertFalse([w for w in self.github.writes
                          if w[1] == SLUG + "/issues/5/comments"])

    def assert_still_out(self, prs, red):
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=2, check_state=red)
        self.assertFalse([l for l in lines if "SUSPECT-CLEARED" in l])
        self.assertNotIn("suspects_cleared", record)
        self.assertEqual([d["result"] for d in record["decisions"]
                          if d["pr"] == 5], ["already said"])
        self.assertNotIn(5, record["ahead"])

    def test_a_green_from_before_the_red_does_not_release_it(self):
        prs, _, _, red = self.red_alone()
        self.assert_still_out(prs, self.rerun(red, "2026-10-07T12:00:30Z"))

    def test_a_red_rerun_does_not_release_it(self):
        prs, _, _, red = self.red_alone()
        self.assert_still_out(prs, self.rerun(red, "2026-10-07T12:01:30Z",
                                              ok=False))

    def test_a_green_on_an_older_main_does_not_release_it(self):
        prs, _, _, red = self.red_alone()
        self.rerun(red, "2026-10-07T12:01:30Z")
        red["results"][self.head]["base"] = run_git(
            self.repo.path, "rev-parse", "origin/main~1")
        self.assert_still_out(prs, red)

    def test_a_red_suspect_kept_without_a_time_waits_for_a_new_head(self):
        prs, _, _, red = self.red_alone()
        path = os.path.join(self.state, m.RED_SUSPECT_FILE)
        saved = m.load_json(path, {})
        del saved["5"]["at"], saved["5"]["main"]
        m.save_json(path, saved)
        self.assert_still_out(prs, self.rerun(red, "2026-10-07T12:01:30Z"))

    def test_the_hand_back_names_the_rerun_label(self):
        self.red_alone(rerun_label="Rerun")
        body = self.github.writes[-1][2]["body"]
        self.assertIn("If you believe the red was a flake, put the `Rerun` "
                      "label on this pull request", body)

    def test_without_the_key_a_red_suspect_waits_to_be_reached(self):
        m.configure(project(test_ahead={"batch": 2,
                                        "hand_back_red_suspect": False}))
        prs = [self.item(), self.other_pr()]
        self.tick(prs, conclusions=(None,), live=True)
        top = self.ahead_json()["entries"][1]
        red = {"results": {top["sha"]: {"tree": top["tree"], "ok": False}}}
        self.tick(prs, conclusions=(None,), live=True, check_state=red)
        alone = self.ahead_json()["entries"][0]
        red["results"][alone["sha"]] = {"tree": alone["tree"], "ok": False}
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=1, check_state=red)
        self.assertFalse([d for d in record["decisions"] if d.get("suspect")])
        self.assertIn(5, record["ahead"])
        self.assertFalse(os.path.exists(os.path.join(
            self.state, m.RED_SUSPECT_FILE)))

    def test_a_suspect_named_red_behind_a_green_prefix_goes_at_once(self):
        # Red one longer than a green prefix: named, and handed back
        # while the green prefix merges.
        m.configure(project(test_ahead={"batch": 4}))
        saved = {"suspects": [{"pr": n, "head": "h%d" % n}
                              for n in (10, 11, 12)],
                 "tree": "t", "merged": [], "left": []}
        entries = [{"pr": 10 + k, "sha": "%d" % k * 40, "tree": t,
                    "head": "h%d" % (10 + k)} for k, t in enumerate("abc")]
        found = []
        with tempfile.TemporaryDirectory() as state_dir:
            going = m.suspect_round(
                state_dir, saved, entries,
                lambda t: True if t == "a" else False, None, {}, {},
                lambda line: None, found)
        self.assertTrue(going)
        self.assertEqual(found, [(entries[1], 1)])

    def test_a_suspect_that_merges_keeps_the_search(self):
        # Seen on 2026-10-26: the first suspect merged on its green, and
        # the same tick, still predicting it first, found no suspect at
        # the front and ended the search unsaid.
        m.configure(project(test_ahead={"batch": 3}))
        self.repo.branch("third", "origin/main")
        sha = self.repo.commit("t.txt", "third\n", "third")
        self.repo.publish_pr(7, sha)
        self.repo.branch("feature", "feature")
        prs = [self.item(), self.other_pr(),
               pr(7, T1, head=sha, reviews=[review(REVIEWER, oid=sha)])]
        self.tick(prs, conclusions=(None,), live=True)
        entries = self.ahead_json()["entries"]
        red = {"results": {entries[2]["sha"]: {"tree": entries[2]["tree"],
                                               "ok": False}}}
        self.tick(prs, conclusions=(None,), live=True, check_state=red)
        # One slot: the first two are checked, and are green.
        half = self.ahead_json()["entries"][1]
        self.assertEqual(self.ahead_json()["batch"]["size"], 2)
        green = {"results": dict(red["results"], **{half["sha"]: {
            "tree": half["tree"], "ok": True}})}
        record, lines = self.tick(prs, extra=self.covered(), live=True,
                                  minutes=1, check_state=green)
        self.assertEqual(record["decisions"][0]["action"], "MERGE")
        saved = m.load_suspects(self.state)
        self.assertIsNotNone(saved, lines)
        self.assertEqual([e["pr"] for e in saved["suspects"]], [6, 7])
        self.assertEqual(saved["merged"], [5])
        self.assertEqual(record["ahead"], [6, 7])
        self.assertFalse([l for l in lines if l.startswith("BATCH ")])

    def gate_refuses(self, number, calls=None):
        """`run` whose closing-keywords gate refuses pull request
        `number`, counting its calls in `calls`."""
        def run(args, cwd=None, env=None):
            if os.path.basename(args[0]) == "closing-keywords":
                if calls is not None:
                    calls.append(args[args.index("--pr") + 1])
                if args[args.index("--pr") + 1] == str(number):
                    return subprocess.CompletedProcess(
                        args, 1, "#9 closes from a commit, undeclared", "")
            return tools_run()(args, cwd, env)
        return run

    def test_a_member_the_gate_refuses_goes_back_before_its_batch(self):
        # designs/0035, fourth amendment. Seen on 2026-10-26: the third
        # member of a green batch of eight was handed back mid-merge
        # for a closing keyword, and every member after it ran again.
        m.configure(project(test_ahead={"batch": 3}))
        self.repo.branch("third", "origin/main")
        sha = self.repo.commit("t.txt", "third\n", "third")
        self.repo.publish_pr(7, sha)
        self.repo.branch("feature", "feature")
        prs = [self.item(), self.other_pr(),
               pr(7, T1, head=sha, reviews=[review(REVIEWER, oid=sha)])]
        calls = []
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  run=self.gate_refuses(6, calls))
        back = [d for d in record["decisions"] if d.get("ahead")]
        self.assertEqual([(d["pr"], d["action"], d["to"]) for d in back],
                         [(6, "HAND-BACK", "author")])
        self.assertEqual(back[0]["result"], "done")
        self.assertIn("CHECKED-AHEAD #6", " ".join(lines))
        self.assertEqual(record["ahead"], [5, 7])
        self.assertEqual(record["batch"], [5, 7])
        # Once per head: the next tick runs the gate for no one again,
        # and says nothing again. Seen on 2026-10-26: the hand-back was
        # never saved, and its comment went out on every tick.
        calls.clear()
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=1, run=self.gate_refuses(6, calls))
        self.assertEqual(calls, [])
        self.assertEqual(record["ahead"], [5, 7])
        self.assertFalse([d for d in record["decisions"] if d.get("ahead")])
        self.assertFalse([w for w in self.github.writes
                          if "/issues/6/comments" in str(w)])

    def test_a_new_description_lets_a_gate_hand_back_rejoin(self):
        # Seen on 2026-10-26: handed back for an undeclared closing
        # keyword, fixed by an edit of the body two minutes later, and
        # still out of every batch an hour on: neither the head nor the
        # approval had changed.
        m.configure(project(test_ahead={"batch": 3}))
        self.repo.branch("third", "origin/main")
        sha = self.repo.commit("t.txt", "third\n", "third")
        self.repo.publish_pr(7, sha)
        self.repo.branch("feature", "feature")
        prs = [self.item(), self.other_pr(),
               pr(7, T1, head=sha, reviews=[review(REVIEWER, oid=sha)])]
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              run=self.gate_refuses(6))
        self.assertEqual(record["ahead"], [5, 7])
        prs[1] = dict(prs[1], body="Closes #9.")
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=1, run=self.gate_refuses(0))
        # Behind the batch already predicted, which it doesn't disturb.
        self.assertEqual(record["ahead"], [5, 7, 6])
        # Refused again for the new description, it is said again: the
        # author has a new answer to read.
        before = len([w for w in self.github.writes
                      if "/issues/6/comments" in str(w)])
        prs[1] = dict(prs[1], body="Closes #8.")
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=2, run=self.gate_refuses(6))
        self.assertEqual(record["ahead"], [5, 7])
        self.assertEqual(len([w for w in self.github.writes
                              if "/issues/6/comments" in str(w)]),
                         before + 1)

    def test_a_new_description_does_not_undo_other_hand_backs(self):
        six = self.other_pr()
        approval = m.approval_of(six)
        said = {"6": m.hand_back_key(approval, six["headRefOid"],
                                     m.CONFLICT_REASON)}
        self.assertTrue(m.handed_back(said, dict(six, body="new"), approval))
        said = {"6": m.hand_back_key(approval, six["headRefOid"], "gate",
                                     m.description(six))}
        self.assertTrue(m.handed_back(said, six, approval))
        self.assertFalse(m.handed_back(said, dict(six, body="new"),
                                       approval))
        self.assertFalse(m.handed_back(said, dict(six, title="new"),
                                       approval))

    def gate_lags(self, number, lagging, calls=None):
        """`run` whose closing-keywords gate refuses pull request
        `number` for now (exit 3, GitHub's field lagging the body)
        while `lagging[0]` is true."""
        def run(args, cwd=None, env=None):
            if os.path.basename(args[0]) == "closing-keywords":
                if calls is not None:
                    calls.append(args[args.index("--pr") + 1])
                if args[args.index("--pr") + 1] == str(number) \
                        and lagging[0]:
                    return subprocess.CompletedProcess(
                        args, 3, "", "#9 is declared by a closing keyword "
                        "in pull request 6's body, but GitHub's "
                        "closingIssuesReferences does not list it yet.")
            return tools_run()(args, cwd, env)
        return run

    def test_a_refusal_for_now_is_asked_again(self):
        # Seen on 2026-10-28: handed back ahead because GitHub had not
        # yet linked an issue the body declared, the pull request was
        # never checked again, though the link appeared minutes later,
        # and sat out of every prediction for over an hour.
        m.configure(project(test_ahead={"batch": 3}))
        self.repo.branch("third", "origin/main")
        sha = self.repo.commit("t.txt", "third\n", "third")
        self.repo.publish_pr(7, sha)
        self.repo.branch("feature", "feature")
        prs = [self.item(), self.other_pr(),
               pr(7, T1, head=sha, reviews=[review(REVIEWER, oid=sha)])]
        lagging, calls = [True], []

        def comments():
            return len([w for w in self.github.writes
                        if "/issues/6/comments" in str(w)])

        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              run=self.gate_lags(6, lagging, calls))
        self.assertEqual(record["ahead"], [5, 7])
        back = [d for d in record["decisions"] if d.get("ahead")]
        self.assertEqual([(d["pr"], d.get("retry")) for d in back],
                         [(6, True)])
        self.assertEqual(comments(), 1)
        # Within GATE_RETRY: not asked again, and nothing said.
        calls.clear()
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=2, run=self.gate_lags(6, lagging, calls))
        self.assertEqual(calls, [])
        self.assertEqual(record["ahead"], [5, 7])
        self.assertEqual(comments(), 0)
        # Due, and still lagging: asked again, said no second time.
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=6, run=self.gate_lags(6, lagging, calls))
        self.assertEqual(calls, ["6"])
        self.assertEqual(record["ahead"], [5, 7])
        self.assertEqual(comments(), 0)
        # The link appears, nothing else changes: it rejoins.
        lagging[0] = False
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=12,
                              run=self.gate_lags(6, lagging, calls))
        self.assertIn(6, record["ahead"])
        self.assertEqual(comments(), 0)

    def test_a_refusal_for_good_is_not_asked_again(self):
        # The control: exit 1 stays handed back at the same head,
        # approval and description, however long it waits.
        m.configure(project(test_ahead={"batch": 3}))
        prs = [self.item(), self.other_pr()]
        calls = []
        self.tick(prs, conclusions=(None,), live=True,
                  run=self.gate_refuses(6, calls))
        calls.clear()
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=60, run=self.gate_refuses(6, calls))
        self.assertEqual(calls, [])
        self.assertNotIn(6, record["ahead"])

    def test_retry_due(self):
        six = self.other_pr()
        approval = m.approval_of(six)
        key = dict(m.hand_back_key(approval, six["headRefOid"], "gate",
                                   m.description(six)),
                   retry=True, at="2026-10-07T12:00:00Z")
        said = {"6": key}
        at = datetime.datetime(2026, 10, 7, 12, 0, 0,
                               tzinfo=datetime.timezone.utc)
        self.assertTrue(m.handed_back(said, six, approval))
        self.assertTrue(m.handed_back(said, six, approval,
                                      at + datetime.timedelta(minutes=4)))
        self.assertFalse(m.handed_back(said, six, approval,
                                       at + m.GATE_RETRY))
        plain = {"6": dict(key, retry=False)}
        self.assertTrue(m.handed_back(plain, six, approval,
                                      at + datetime.timedelta(hours=9)))

    def test_a_member_that_passes_is_checked_once(self):
        m.configure(project(test_ahead={"batch": 2}))
        prs = [self.item(), self.other_pr()]
        calls = []
        self.tick(prs, conclusions=(None,), live=True,
                  run=self.gate_refuses(0, calls))
        self.assertEqual(calls, ["6"])
        calls.clear()
        record, _ = self.tick(prs, conclusions=(None,), live=True,
                              minutes=1, run=self.gate_refuses(0, calls))
        self.assertEqual(calls, [])
        self.assertEqual(record["ahead"], [5, 6])
        # A new description is checked again.
        prs[1] = dict(prs[1], body="Closes #9.")
        self.tick(prs, conclusions=(None,), live=True, minutes=2,
                  run=self.gate_refuses(0, calls))
        self.assertEqual(calls, ["6"])

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
                         (self.repo.main_tip(),
                          checkout.store(self.repo.path), record["at"]))
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
        self.assertEqual(m.IN_FLIGHT, ("UPDATE", "RESOLVE", "UNION",
                                       "RESTACK", "STACK-UPDATE", "WAIT"))
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

    def test_nothing_waits_behind_one_handed_back_at_its_head(self):
        # 2026-11-11: #10570 was handed back (its head not the clean
        # merge of its parents) and held 26 others as "ahead of them in
        # the queue" until its author pushed again.
        five = self.item()
        state = os.path.join(self.tmp.name, "state")
        os.makedirs(state, exist_ok=True)
        m.save_json(os.path.join(state, "handed-back.json"),
                    {"5": {"head": five["headRefOid"], "reason": "red",
                           "approved": five["reviews"][0]["submittedAt"]}})
        record, _ = self.tick([five, self.rival()], live=True)
        self.assertEqual(record["held"], {})

    def test_a_pair_of_commits_is_asked_of_git_once(self):
        # agent-forge #301: the same pairs, next tick, ask git nothing.
        asked = []

        def run(args, cwd=None, env=None):
            if "merge-tree" in args:
                asked.append(args[-2:])
            return m.sh(args, cwd, env)
        queue = [self.item(), self.rival()]
        cache = {}
        first = m.conflicts_ahead(self.repo.path, queue, run,
                                  self.repo.main_tip(), cache)
        self.assertEqual(first, {6: 5})
        self.assertEqual(len(asked), 3)
        kept = dict(cache["used"])
        asked.clear()
        again = m.conflicts_ahead(self.repo.path, queue, run,
                                  self.repo.main_tip(), kept)
        self.assertEqual(again, first)
        self.assertEqual(asked, [])

    def test_a_live_tick_keeps_only_the_pairs_it_asked(self):
        self.tick([self.other_pr(7)], live=True)
        path = os.path.join(self.state, m.PAIRS_FILE)
        m.save_json(path, {"old new": 0})
        self.tick([self.item(), self.rival()], live=True)
        pairs = m.load_json(path, {})
        self.assertNotIn("old new", pairs)
        self.assertIn(1, pairs.values())

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

    def test_one_held_for_an_instance_is_told_too(self):
        # Seen on 2026-11-07: a layer whose base merged conflicted with
        # main and sat, held for an instance that never ran, unseen by
        # its author, who thought the Merger had it.
        six = self.behind()
        m.configure(gated_project({"paths": ["g.*"]}, early_conflicts=True))
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True)
        self.assertEqual(record["held"]["6"], "waiting for vm/mac (absent)")
        self.assertIn((6, "HAND-BACK", True),
                      [(d["pr"], d["action"], d.get("early"))
                       for d in record["decisions"]])

    def test_a_layer_held_for_an_instance_is_left_to_its_restack(self):
        six = self.behind()
        six["baseRefName"] = "lower"
        m.configure(gated_project({"paths": ["g.*"]}, early_conflicts=True))
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True)
        self.assertNotIn(6, [d["pr"] for d in record["decisions"]])

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
        # Told anything else for this approval and head, it isn't told
        # again either (designs/0044, rule 1), unless that is off.
        said["6"]["reason"] = "the Merger's gate failed"
        self.assertEqual(self.early(six, said), ([], []))
        m.configure(project(early_conflicts=True,
                            hand_back_once_per_head=False))
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


class ResolveSlots(RepoCase):
    """merge.resolve_slots (designs/0012, amendment of 2026-11-12): at
    most N pull requests are back with their authors for a conflict
    with main at once; the others that conflict are held, untold, until
    a slot frees."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr
    rival = HoldConflicting.rival
    comments = HoldConflicting.comments
    WAIT = "conflicts with main; waits for a resolve slot (%d out: %s)"

    def setUp(self):
        super().setUp()
        self.configure()
        self.addCleanup(m.configure, project())

    def configure(self, slots=1, make=project, **merge):
        m.configure(make(resolve_slots=slots, **merge))

    def conflicting(self, *numbers):
        """Approved pull requests made on main's g.txt, which main then
        changes: each conflicts with main, the oldest first."""
        made = [self.rival(n, "2026-10-07T1%d:00:00Z" % k,
                           (("g.txt", "rival %d\n" % n),))
                for k, n in enumerate(numbers)]
        self.advance_main("g.txt", "main moved g\n")
        return made

    def told(self):
        """The pull requests handed back this tick."""
        return sorted(int(w.split("/")[-2]) for w, body in self.comments()
                      if body.startswith("Not merging"))

    def acted(self, record):
        return [(d["pr"], d["action"]) for d in record["decisions"]]

    def test_the_configuration(self):
        self.assertEqual(m.RESOLVE_SLOTS, 1)
        m.configure(project())
        self.assertEqual(m.RESOLVE_SLOTS, 0)

    def test_one_is_handed_back_and_the_others_wait_untold(self):
        prs = self.conflicting(6, 7, 8)
        record, lines = self.tick(prs, live=True)
        self.assertEqual(self.acted(record), [(6, "HAND-BACK")])
        self.assertEqual(self.told(), [6])
        self.assertEqual({k: record["held"][k] for k in ("7", "8")},
                         {"7": self.WAIT % (1, "#6"),
                          "8": self.WAIT % (1, "#6")})
        self.assertEqual((record["resolving"], record["resolve_waiting"]),
                         ([6], [7, 8]))
        self.assertEqual(record["queue"], [6])
        self.assertIn("RESOLVE-WAIT #7 #8: conflict with main; 1 of 1 "
                      "slots out (#6)", lines)
        # Nothing is said about the waiting ones in handed-back.json.
        with open(os.path.join(self.state, "handed-back.json")) as handle:
            self.assertEqual(sorted(json.load(handle)), ["6"])
        # The next tick: #6 is still out, so the others still wait, and
        # nobody is told again.
        record, _ = self.tick(prs, live=True, minutes=1)
        self.assertEqual(record["resolve_waiting"], [7, 8])
        self.assertEqual(self.told(), [])

    def test_a_slot_frees_when_the_one_out_goes(self):
        prs = self.conflicting(6, 7, 8)
        self.tick(prs, live=True)
        # #6 closes (or its author pushes: a head it wasn't told at).
        record, _ = self.tick(prs[1:], live=True, minutes=1)
        self.assertEqual(self.acted(record), [(7, "HAND-BACK")])
        self.assertEqual(self.told(), [7])
        self.assertEqual(record["held"]["8"], self.WAIT % (1, "#7"))

    def test_a_new_head_frees_the_slot(self):
        prs = self.conflicting(6, 7)
        self.tick(prs, live=True)
        with open(os.path.join(self.state, "handed-back.json")) as handle:
            said = json.load(handle)
        out = lambda six: m.out_for_resolving(
            said, [six], self.repo.path, self.repo.main_tip(), tools_run())
        self.assertEqual(out(prs[0]), [6])
        self.assertEqual(out(dict(prs[0], headRefOid="9" * 40)), [])
        self.assertEqual(out(dict(prs[0], isDraft=True)), [])

    def test_one_no_longer_conflicting_frees_its_slot(self):
        six, = self.conflicting(6)
        said = {"6": m.hand_back_key(m.approval_of(six), six["headRefOid"],
                                     m.CONFLICT_REASON)}
        self.assertEqual(m.out_for_resolving(
            said, [six], self.repo.path, self.repo.main_tip(), tools_run()),
            [6])
        # Main takes #6's g.txt: it merges cleanly again.
        self.advance_main("g.txt", "rival 6\n")
        self.assertEqual(m.out_for_resolving(
            said, [six], self.repo.path, self.repo.main_tip(), tools_run()),
            [])
        # Told something else, or under another approval, isn't out.
        self.advance_main("g.txt", "main moved again\n")
        other = dict(said["6"], reason="the Merger's gate failed")
        self.assertEqual(m.out_for_resolving(
            {"6": other}, [six], self.repo.path, self.repo.main_tip(),
            tools_run()), [])
        older = dict(said["6"], approved="2026-01-01T00:00:00Z")
        self.assertEqual(m.out_for_resolving(
            {"6": older}, [six], self.repo.path, self.repo.main_tip(),
            tools_run()), [])

    def test_two_slots(self):
        self.configure(2)
        record, _ = self.tick(self.conflicting(6, 7, 8), live=True)
        self.assertEqual(self.told(), [6, 7])
        self.assertEqual(record["held"]["8"], self.WAIT % (2, "#6, #7"))

    def test_without_slots_every_conflict_is_handed_back(self):
        self.configure(0)
        record, _ = self.tick(self.conflicting(6, 7, 8), live=True)
        self.assertEqual(self.told(), [6, 7, 8])
        self.assertNotIn("resolving", record)

    def test_one_labelled_urgent_gets_the_slot_first(self):
        self.configure(make=urgent_project)
        prs = self.conflicting(6, 7, 8)
        labelled(prs[2], "Priority:urgent")
        record, _ = self.tick(prs, live=True)
        self.assertEqual(self.told(), [8])
        self.assertEqual(record["resolve_waiting"], [6, 7])

    def test_those_behind_the_front_wait_too(self):
        # merge.early_conflicts: the hand-backs behind the front are
        # gated the same way, after the front's.
        self.configure(early_conflicts=True)
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        prs = [self.item()] + self.conflicting(6, 7)
        record, _ = self.tick(prs, conclusions=(None,), live=True)
        self.assertEqual(self.told(), [6])
        self.assertEqual([d.get("early") for d in record["decisions"]
                          if d["pr"] == 6], [True])
        self.assertEqual(record["held"]["7"], self.WAIT % (1, "#6"))

    def test_a_dry_tick_says_nothing(self):
        record, _ = self.tick(self.conflicting(6, 7))
        self.assertEqual(self.acted(record), [(6, "HAND-BACK")])
        self.assertEqual(record["resolve_waiting"], [7])

    def test_a_layer_above_one_out_is_not_handed_back_itself(self):
        # Held as stacked, as before: its base's resolution comes first.
        self.configure(early_conflicts=True)
        six, seven = self.conflicting(6, 7)
        layer = pr(9, T2, base="b6", head=seven["headRefOid"],
                   reviews=[review(REVIEWER, oid=seven["headRefOid"])])
        record, _ = self.tick([six, layer], live=True)
        self.assertEqual(self.told(), [6])
        self.assertNotIn("resolve_waiting", record)
        self.assertIn("9", record["held"])
        self.assertNotIn("resolve slot", record["held"]["9"])

    # merge.resolve_slot_minutes: a slot held with no push runs out.

    EXPIRED = ("conflicts with main; its resolve slot ran out after 30 "
               "minutes with no push, and it waits behind the others for "
               "another (%d out: %s)")

    def test_a_slot_held_with_no_push_runs_out(self):
        self.configure(resolve_slot_minutes=30)
        prs = self.conflicting(6, 7, 8)
        self.tick(prs, live=True)
        self.assertEqual(self.told(), [6])
        record, lines = self.tick(prs, live=True, minutes=29)
        self.assertEqual((record["resolving"], self.told()), ([6], []))
        record, lines = self.tick(prs, live=True, minutes=30)
        self.assertIn("RESOLVE-EXPIRED #6: out for resolving 30 minutes "
                      "with no push; its slot goes to the next", lines)
        self.assertEqual(self.told(), [7])
        self.assertEqual(record["resolving"], [7])
        self.assertEqual(record["resolve_expired"], [6])
        self.assertEqual(record["held"]["6"], self.EXPIRED % (1, "#7"))
        self.assertEqual(record["held"]["8"], self.WAIT % (1, "#7"))
        # Said once; #7's slot counts from when it was given.
        record, lines = self.tick(prs, live=True, minutes=59)
        self.assertFalse([l for l in lines if "RESOLVE-EXPIRED" in l])
        self.assertEqual((record["resolving"], self.told()), ([7], []))

    def test_one_whose_slot_ran_out_goes_after_those_waiting(self):
        self.configure(resolve_slot_minutes=30)
        prs = self.conflicting(6, 7, 8)
        self.tick(prs, live=True)
        self.tick(prs, live=True, minutes=30)
        # #7 closes: #8 has waited, so it goes before #6 again.
        record, _ = self.tick([prs[0], prs[2]], live=True, minutes=31)
        self.assertEqual((record["resolving"], self.told()), ([8], [8]))
        # #8 closes too: #6 has its slot back, untold, from now.
        record, _ = self.tick(prs[:1], live=True, minutes=32)
        self.assertEqual((record["resolving"], self.told()), ([6], []))
        record, _ = self.tick(prs[:1], live=True, minutes=61)
        self.assertEqual(record["resolving"], [6])
        record, _ = self.tick(prs[:1], live=True, minutes=62)
        self.assertEqual(record.get("resolve_expired"), [6])

    def test_its_push_frees_one_whose_slot_ran_out(self):
        self.configure(resolve_slot_minutes=30)
        prs = self.conflicting(6, 7)
        self.tick(prs, live=True)
        self.tick(prs, live=True, minutes=30)
        with open(os.path.join(self.state, m.RESOLVE_FILE)) as handle:
            self.assertEqual(sorted(json.load(handle)), ["6", "7"])
        # A head it wasn't told at is not out, whatever its slot did.
        pushed = dict(prs[0], headRefOid="9" * 40)
        kept, expired, slots = m.expire_resolving(
            os.path.join(self.state, m.RESOLVE_FILE), [7], [pushed, prs[1]],
            m.parse_time(T0))
        self.assertEqual((kept, expired, sorted(slots)), ([7], [], ["7"]))

    def test_without_minutes_a_slot_never_runs_out(self):
        prs = self.conflicting(6, 7)
        self.tick(prs, live=True)
        record, _ = self.tick(prs, live=True, minutes=600)
        self.assertEqual(record["resolving"], [6])
        self.assertNotIn("resolve_expired", record)
        self.assertFalse(os.path.exists(os.path.join(self.state,
                                                     m.RESOLVE_FILE)))

    # merge.resolve_slot_on "push" (designs/0053).

    def resolved(self, number, created, approved=False):
        """Pull request `number`'s resolution: a new head on main's tip
        that keeps its own change, so it merges cleanly."""
        self.repo.branch("rival%d" % number, "origin/main")
        sha = self.repo.commit("r%d.txt" % number, "resolved\n",
                               "resolve #%d" % number)
        self.repo.publish_pr(number, sha)
        self.repo.branch("feature", "feature")
        return pr(number, created, head=sha,
                  reviews=[review(REVIEWER, oid=sha)] if approved else [])

    def next_json(self):
        with open(os.path.join(self.state, "next.json")) as handle:
            return json.load(handle)

    def test_with_push_every_conflict_is_told_at_once(self):
        self.configure(resolve_slot_on="push")
        record, lines = self.tick(self.conflicting(6, 7, 8), live=True)
        self.assertEqual(self.told(), [6, 7, 8])
        self.assertNotIn("resolve_waiting", record)
        self.assertFalse([l for l in lines if l.startswith("RESOLVE-WAIT")])
        body = [b for _, b in self.comments() if b.startswith("Not merging")]
        self.assertIn("your push queues for one of 1 resolve slots", body[0])
        self.assertIn("Merge `main` (at %s)" % self.repo.main_tip()[:10],
                      body[0])

    def test_with_push_a_resolution_takes_a_slot_and_the_next_queues(self):
        self.configure(resolve_slot_on="push")
        prs = self.conflicting(6, 7, 8)
        self.tick(prs, live=True)
        six = self.resolved(6, "2026-10-07T10:00:00Z")
        seven = self.resolved(7, "2026-10-07T11:00:00Z")
        record, lines = self.tick([six, seven, prs[2]], live=True,
                                  minutes=5)
        self.assertEqual(record["resolving"], [6])
        self.assertEqual(record["resolve_queue"], [7])
        self.assertIn("RESOLVE-SLOTS: #6 checking; #7 queued", lines)
        self.assertEqual(
            record["held"]["7"],
            "a resolution of a conflict with main; waits for a resolve "
            "slot (1 out: #6)")
        said = self.next_json()
        self.assertEqual(said["resolve_slotted"],
                         [{"pr": 6, "head": six["headRefOid"]}])
        self.assertEqual(said["resolve_queue"],
                         [{"pr": 7, "head": seven["headRefOid"]}])
        self.assertEqual(said["resolve_base"],
                         {"sha": self.repo.main_tip()})
        # #6 keeps its slot while it waits for its review.
        record, _ = self.tick([six, seven, prs[2]], live=True, minutes=10)
        self.assertEqual((record["resolving"], record["resolve_queue"]),
                         ([6], [7]))
        # Approved, it is a candidate again: its slot goes to #7.
        approved = self.resolved(6, "2026-10-07T10:00:00Z", approved=True)
        record, _ = self.tick([approved, seven, prs[2]], live=True,
                              minutes=15)
        self.assertEqual(record["resolving"], [7])
        self.assertNotIn("resolve_queue", record)

    def test_with_push_a_slot_runs_out(self):
        self.configure(resolve_slot_on="push", resolve_slot_minutes=30)
        prs = self.conflicting(6, 7)
        self.tick(prs, live=True)
        six = self.resolved(6, "2026-10-07T10:00:00Z")
        seven = self.resolved(7, "2026-10-07T11:00:00Z")
        self.tick([six, seven], live=True, minutes=1)
        record, lines = self.tick([six, seven], live=True, minutes=31)
        self.assertIn("RESOLVE-EXPIRED #6: its resolution held a slot 30 "
                      "minutes; the slot goes to the next", lines)
        self.assertEqual((record["resolving"], record["resolve_queue"]),
                         ([7], [6]))

    def test_with_push_progress_restarts_a_slots_clock(self):
        # agent-forge #372: a green check on its head is progress, so the
        # slot runs out resolve_slot_minutes after it, not after the push.
        self.configure(resolve_slot_on="push", resolve_slot_minutes=30)
        prs = self.conflicting(6, 7)
        self.tick(prs, live=True)
        six = self.resolved(6, "2026-10-07T10:00:00Z")
        seven = self.resolved(7, "2026-10-07T11:00:00Z")
        self.tick([six, seven], live=True, minutes=1)
        green = {"results": {six["headRefOid"]: {"ok": True}}}
        self.tick([six, seven], live=True, minutes=20, check_state=green)
        record, lines = self.tick([six, seven], live=True, minutes=31,
                                  check_state=green)
        self.assertEqual(record["resolving"], [6])
        self.assertFalse([l for l in lines if "RESOLVE-EXPIRED" in l])
        record, lines = self.tick([six, seven], live=True, minutes=50,
                                  check_state=green)
        self.assertEqual((record["resolving"], record["resolve_queue"]),
                         ([7], [6]))

    def test_with_push_a_head_still_conflicting_is_no_resolution(self):
        self.configure(resolve_slot_on="push")
        six, = self.conflicting(6)
        self.tick([six], live=True)
        with open(os.path.join(self.state, "handed-back.json")) as handle:
            said = json.load(handle)
        # Pushed again, still on the old main: it conflicts as before.
        self.repo.branch("rival6", "rival6")
        again = self.repo.commit("x.txt", "more\n", "more")
        self.repo.publish_pr(6, again)
        self.repo.branch("feature", "feature")
        found = m.resolutions(said, [dict(six, headRefOid=again)], [],
                              self.repo.path, self.repo.main_tip(),
                              tools_run())
        self.assertEqual(found, [])

    def test_with_push_a_resolution_main_moves_under_is_told_again(self):
        # agent-forge #373: unapproved, it isn't a candidate, so only
        # this tells it.
        self.configure(resolve_slot_on="push")
        prs = self.conflicting(6, 7)
        self.tick(prs, live=True)
        six = self.resolved(6, "2026-10-07T10:00:00Z")
        record, _ = self.tick([six, prs[1]], live=True, minutes=5)
        self.assertEqual(record["resolving"], [6])
        self.advance_main("r6.txt", "main moved r6\n")
        record, _ = self.tick([six, prs[1]], live=True, minutes=10)
        self.assertEqual(self.told(), [6])
        self.assertIn((6, "HAND-BACK"), self.acted(record))
        self.assertEqual(record["resolving"], [])
        # Told once for this head.
        record, _ = self.tick([six, prs[1]], live=True, minutes=15)
        self.assertNotIn((6, "HAND-BACK"), self.acted(record))

    def test_the_order_slots_are_given_in(self):
        base = pr(6, "2026-10-07T03:00:00Z")
        layers = [pr(9, base="b6"), pr(10, base="b9", reviews=[])]
        single = pr(7, "2026-10-07T01:00:00Z")
        later = pr(8, "2026-10-07T02:00:00Z")
        queue, everyone = [single, base, later], [single, base, later] + layers
        order = lambda row=None: [p["number"] for p in m.resolve_slot_order(
            [later, base, single], queue, everyone, row)]
        self.assertEqual(order(), [7, 6, 8])
        # Right of way comes first; unless it is idle.
        self.assertEqual(order({"pr": 8}), [8, 7, 6])
        self.assertEqual(order({"pr": 8, "idle": "drafted"}), [7, 6, 8])
        # In batches of two, #7 and #6 would board together: the base
        # with an approved layer above it goes first.
        m.configure(project(resolve_slots=1, test_ahead={"batch": 2}))
        self.assertEqual(order(), [6, 7, 8])
        self.assertEqual(m.approved_above(6, everyone), 1)


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

    def test_it_holds_nobody_while_not_yet_approved_again(self):
        # designs/0044, rule 2: only while it is a candidate at its head.
        unapproved = pr(5, head=self.head, reviews=[])
        rival = self.rival(created=self.OLDER)
        record, _ = self.tick([unapproved, rival], live=True)
        self.assertEqual(record["right_of_way"], dict(
            self.row(), idle="not a candidate at %s" % self.head[:10]))
        self.assertEqual((record["queue"], record["held"]), ([6], {}))
        # Approved again, it holds, on the clock it started.
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              live=True, minutes=1)
        self.assertEqual(record["right_of_way"], self.row())
        self.assertEqual(list(record["held"]), ["6"])

    def test_without_while_eligible_it_holds_while_not_yet_approved(self):
        m.configure(project(right_of_way={"while_eligible": False}))
        unapproved = pr(5, head=self.head, reviews=[])
        record, _ = self.tick([unapproved, self.rival(created=self.OLDER)],
                              live=True)
        self.assertEqual(record["right_of_way"], self.row())
        self.assertEqual(record["queue"], [])
        self.assertEqual(list(record["held"]), ["6"])

    def test_it_holds_nobody_once_handed_back_at_its_head(self):
        # Here to its reviewer, since a commit after the approval isn't
        # a merge: a hand-back to the author for anything else already
        # ended it (designs/0002).
        head = self.repo.commit("f3.txt", "after\n", "after the approval")
        self.repo.publish_pr(5, head)
        five = pr(5, head=head, reviews=[review(REVIEWER, oid=self.head)])
        rival = self.rival(created=self.OLDER)
        record, _ = self.tick([five, rival], conclusions=(None,), live=True)
        self.assertEqual(record["decisions"][0]["action"], "HAND-BACK")
        self.assertEqual(list(record["held"]), ["6"])
        record, _ = self.tick([five, rival], conclusions=(None,), live=True,
                              minutes=1)
        self.assertEqual(record["right_of_way"]["idle"],
                         "handed back at %s" % head[:10])
        self.assertNotIn("6", record["held"])
        self.assertIn(6, record["queue"])

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
                          "since": "2026-10-07T13:01:00Z",
                          "idle": "not a candidate at %s" % head[:10]})

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

    def test_not_while_a_request_for_changes_stands(self):
        # designs/0002, amendment: GitHub refuses its merge until that
        # reviewer's next verdict, so it holds nobody meanwhile, and the
        # clock it started keeps running.
        rival = self.rival(created=self.OLDER)
        approved = review(REVIEWER, oid=self.head)
        asked = review("example-owner", "CHANGES_REQUESTED", T1)
        record, _ = self.tick([pr(5, head=self.head,
                                  reviews=[approved, asked]), rival],
                              live=True)
        self.assertIsNone(record["right_of_way"])
        self.assertNotIn("6", record["held"])
        lifted = review("example-owner", "APPROVED", T2, oid=self.head)
        record, _ = self.tick([pr(5, head=self.head,
                                  reviews=[approved, asked, lifted]), rival],
                              conclusions=(None,), live=True, minutes=10)
        self.assertEqual(record["right_of_way"], self.row())
        self.assertEqual(list(record["held"]), ["6"])

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


class AgeFirst(RepoCase):
    """merge.age_first (designs/0037): a candidate that has waited long
    goes first. #6, opened after #5, goes behind it until it has waited
    `after_minutes` since a tick first saw it as a candidate."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr
    rival = HoldConflicting.rival

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        m.configure(project(age_first={}))
        self.addCleanup(m.configure, project())
        self.state = os.path.join(self.tmp.name, "state")

    def unapproved(self):
        return pr(5, head=self.head, reviews=[])

    def remembered(self, name="dry-run-" + m.AGE_FILE):
        with open(os.path.join(self.state, name)) as handle:
            return json.load(handle)

    def test_the_configuration(self):
        self.assertEqual(m.AGE_FIRST, datetime.timedelta(minutes=90))
        m.configure(project(age_first={"after_minutes": 30}))
        self.assertEqual(m.AGE_FIRST, datetime.timedelta(minutes=30))
        m.configure(project())
        self.assertIsNone(m.AGE_FIRST)

    def test_off_by_default(self):
        m.configure(project())
        other = self.other_pr()
        self.tick([self.unapproved(), other], conclusions=(None,))
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              minutes=200)
        self.assertEqual(record["queue"], [5, 6])
        self.assertNotIn("overdue", record)
        self.assertFalse(os.path.exists(
            os.path.join(self.state, "dry-run-" + m.AGE_FILE)))

    def test_an_overdue_one_goes_first(self):
        other = self.other_pr()
        record, _ = self.tick([self.unapproved(), other],
                              conclusions=(None,))
        self.assertEqual(record["overdue"], {})
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              minutes=89)
        self.assertEqual((record["queue"], record["overdue"]), ([5, 6], {}))
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              minutes=90)
        self.assertEqual(record["queue"], [6, 5])
        self.assertEqual(record["overdue"], {"6": 90})
        self.assertEqual(self.remembered()["6"],
                         {"since": "2026-10-07T12:00:00Z",
                          "seen": "2026-10-07T13:30:00Z"})

    def test_a_green_batch_s_members_keep_their_order(self):
        # 2026-10-31: an overdue member moved up inside a green batch,
        # which changed every tree after it, so the green covered none
        # of them and the batch waited out a new top's run.
        when = datetime.datetime(2026, 10, 31, 0, 29)
        queue = [pr(n) for n in (10, 11, 12, 13)]
        since = {10: when - datetime.timedelta(minutes=30),
                 11: when - datetime.timedelta(minutes=95),
                 13: when - datetime.timedelta(minutes=100)}
        out, overdue = m.overdue_first(queue, since, when, None, {10, 11})
        self.assertEqual([p["number"] for p in out], [10, 11, 13, 12])
        self.assertEqual(overdue, {11: 95, 13: 100})
        out, _ = m.overdue_first(queue, since, when, None, set())
        self.assertEqual([p["number"] for p in out], [13, 11, 10, 12])

    def test_the_longest_waiting_goes_first(self):
        other = self.other_pr()
        self.tick([self.unapproved(), other], conclusions=(None,))
        self.tick([self.item(), other], conclusions=(None,), minutes=10)
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              minutes=100)
        self.assertEqual(record["queue"], [6, 5])
        self.assertEqual(record["overdue"], {"5": 90, "6": 100})

    def test_the_clock_survives_a_hand_back_and_a_new_head(self):
        other = self.other_pr()
        self.tick([self.item()], conclusions=(None,))
        self.tick([self.unapproved(), other], conclusions=(None,),
                  minutes=30)
        self.assertEqual(self.remembered()["5"]["since"],
                         "2026-10-07T12:00:00Z")
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              minutes=90)
        self.assertEqual(record["overdue"], {"5": 90})

    def test_one_long_no_candidate_or_closed_is_forgotten(self):
        other = self.other_pr()
        self.tick([self.item(), other], conclusions=(None,))
        record, _ = self.tick([self.unapproved(), other],
                              conclusions=(None,), minutes=24 * 60 - 1)
        self.assertIn("5", self.remembered())
        self.tick([self.unapproved(), other], conclusions=(None,),
                  minutes=24 * 60)
        self.assertNotIn("5", self.remembered())
        self.tick([other], conclusions=(None,), minutes=24 * 60 + 1)
        self.assertEqual(list(self.remembered()), ["6"])

    def test_a_live_tick_keeps_its_own_clock(self):
        self.tick([self.item()], conclusions=(None,), live=True)
        self.assertIn("5", self.remembered(m.AGE_FILE))
        self.assertFalse(os.path.exists(
            os.path.join(self.state, "dry-run-" + m.AGE_FILE)))

    def test_an_overdue_one_never_yields(self):
        m.configure(project(age_first={}, yield_after_hand_back=1))
        other = self.other_pr()
        self.tick([self.item()], conclusions=(None,))
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "dry-run.jsonl"), "a") as handle:
            handle.write(json.dumps(hand_back(
                5, "2026-10-07T13:00:00Z")) + "\n")
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              minutes=89)
        self.assertEqual(record["queue"], [6, 5])
        record, _ = self.tick([self.item(), other], conclusions=(None,),
                              minutes=95)
        self.assertEqual((record["queue"], record["yielded"]), ([5, 6], {}))

    def test_it_goes_ahead_of_those_predicted(self):
        # #6 was predicted, so with test_ahead it keeps its place ahead
        # of #5 until #5 is overdue; then the prediction is spent.
        m.configure(project(age_first={}, test_ahead={}))
        other = self.other_pr()
        self.tick([self.item()], conclusions=(None,))
        self.tick([self.unapproved(), other], conclusions=(None,),
                  minutes=1)
        for minutes, queue in ((89, [6, 5]), (90, [5, 6])):
            m.save_json(os.path.join(self.state, m.AHEAD_FILE),
                        {"at": "2026-10-07T12:00:00Z", "entries": [
                            {"pr": 6, "head": other["headRefOid"]}]})
            record, _ = self.tick([self.item(), other],
                                  conclusions=(None,), minutes=minutes)
            self.assertEqual(record["queue"][:2], queue)

    def test_right_of_way_does_not_hold_one_that_has_waited_longer(self):
        m.configure(project(age_first={"after_minutes": 30},
                            right_of_way={}))
        os.makedirs(self.state, exist_ok=True)
        with open(os.path.join(self.state, "dry-run.jsonl"), "a") as handle:
            for k in range(2):
                handle.write(json.dumps(hand_back(
                    5, "2026-10-07T11:%02d:00Z" % (10 + k),
                    head=str(k) * 40, reason=m.CONFLICT_REASON)) + "\n")
        rival = self.rival()
        record, _ = self.tick([self.unapproved(), rival],
                              conclusions=(None,))
        # Unapproved, #5 holds nobody yet (designs/0044, rule 2).
        self.assertEqual(list(record["held"]), [])
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              minutes=29)
        self.assertEqual((record["queue"], list(record["held"])),
                         ([5], ["6"]))
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              minutes=30)
        self.assertEqual(record["right_of_way"]["pr"], 5)
        self.assertNotIn("6", record["held"])
        self.assertEqual(record["queue"], [6, 5])


class FixedOrder(unittest.TestCase):
    """designs/0044, rule 3: the batches up to the last whose top's run
    has started keep their order."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        m.configure(project(test_ahead={"batch": 2, "batches": 2,
                                        "depth": 3}))
        self.addCleanup(m.configure, project())

    def ahead(self, numbers, batch=2, later=(), fixed=False):
        entries = [{"pr": n, "head": "h%d" % n, "sha": "s%d" % n,
                    "tree": "t%d" % n} for n in numbers]
        value = {"entries": entries}
        if batch:
            value["batch"] = {"size": batch, "trees": [
                e["tree"] for e in entries[:batch]]}
            at = batch
            value["later"] = []
            for size in later:
                value["later"].append(dict({"size": size, "trees": [
                    e["tree"] for e in entries[at:at + size]]},
                    **({"fixed": True} if fixed else {})))
                at += size
        m.save_json(os.path.join(self.tmp.name, m.AHEAD_FILE), value)

    def fixed(self, queue, started=(), said=None, open_numbers=None,
              trees=None):
        prs = [{"number": n, "headRefOid": h} for n, h in queue]
        approvals = {p["number"]: review(REVIEWER) for p in prs}
        check = {"ahead_started": {s: "x" for s in started}, "results": {}}
        trees = trees or {}

        def run(args, cwd=None, env=None):
            if "rev-parse" in args:
                sha = args[-1].split("^")[0]
                return subprocess.CompletedProcess(
                    args, 0 if sha in trees else 1, trees.get(sha, "") + "\n",
                    "")
            return subprocess.CompletedProcess(args, 0, "", "")
        return m.fixed_order(
            self.tmp.name, "repo", prs,
            {p["number"] for p in prs} if open_numbers is None
            else open_numbers, approvals, said or {}, check, None, run)

    def queue(self, *numbers):
        return [(n, "h%d" % n) for n in numbers]

    def test_nothing_started_fixes_nothing(self):
        self.ahead([5, 6, 7, 8], later=[2])
        self.assertEqual(self.fixed(self.queue(5, 6, 7, 8)), {})

    def test_up_to_the_last_started_top(self):
        self.ahead([5, 6, 7, 8], later=[2])
        self.assertEqual(self.fixed(self.queue(5, 6, 7, 8), ["s6"]),
                         {5: 0, 6: 1})
        # A later top started fixes the batches before it too: its
        # tree has them all.
        self.assertEqual(self.fixed(self.queue(5, 6, 7, 8), ["s8"]),
                         {5: 0, 6: 1, 7: 2, 8: 3})

    def test_a_later_batch_said_fixed_is_started(self):
        self.ahead([5, 6, 7], later=[1], fixed=True)
        self.assertEqual(self.fixed(self.queue(5, 6, 7)),
                         {5: 0, 6: 1, 7: 2})

    def test_without_batches_each_prediction_is_one(self):
        self.ahead([5, 6, 7], batch=0)
        self.assertEqual(self.fixed(self.queue(5, 6, 7), ["s6"]),
                         {5: 0, 6: 1})

    def test_one_that_merged_leaves_the_rest_in_place(self):
        self.ahead([5, 6, 7, 8], later=[2])
        self.assertEqual(self.fixed(self.queue(6, 7, 8), ["s8"],
                                    open_numbers={6, 7, 8}),
                         {6: 0, 7: 1, 8: 2})

    def test_one_that_drops_out_ends_it_from_its_batch(self):
        self.ahead([5, 6, 7, 8], later=[2])
        everyone = {5, 6, 7, 8}
        # #7 lost its approval, or is held: the first batch keeps its
        # order, and the second, whose trees all change, doesn't.
        self.assertEqual(self.fixed(self.queue(5, 6, 8), ["s8"],
                                    open_numbers=everyone), {5: 0, 6: 1})
        # Pushed to.
        self.assertEqual(self.fixed([(5, "h5"), (6, "h6"), (7, "new"),
                                     (8, "h8")], ["s8"]), {5: 0, 6: 1})
        # Handed back at its head.
        said = {"7": m.hand_back_key(review(REVIEWER), "h7", "red")}
        self.assertEqual(self.fixed(self.queue(5, 6, 7, 8), ["s8"],
                                    said=said), {5: 0, 6: 1})

    def test_the_one_updated_keeps_its_place_by_its_tree(self):
        # GitHub's update of the one in flight gives the tree predicted.
        self.ahead([5, 6])
        self.assertEqual(self.fixed([(5, "u5"), (6, "h6")], ["s6"],
                                    trees={"u5": "t5"}), {5: 0, 6: 1})
        self.assertEqual(self.fixed([(5, "u5"), (6, "h6")], ["s6"],
                                    trees={"u5": "other"}), {})

    def test_it_reads_what_it_can(self):
        for junk in (None, [], {"entries": "x"}, {"entries": [1]},
                     {"entries": [{"pr": 5}], "batch": {"size": "2"}}):
            with self.subTest(junk=junk):
                m.save_json(os.path.join(self.tmp.name, m.AHEAD_FILE), junk)
                self.assertEqual(self.fixed(self.queue(5), ["s5"]), {})

    def test_overdue_and_urgent_ones_around_a_fixed_batch(self):
        when = datetime.datetime(2026, 11, 4, 9, 0)
        long_ago = when - datetime.timedelta(minutes=200)
        queue = [pr(n) for n in (6, 7, 8, 5)]
        labelled(queue[2], "Priority:urgent")
        m.configure(urgent_project(age_first={}, test_ahead={"batch": 2}))
        out, overdue = m.overdue_first(queue, {5: long_ago}, when, None,
                                       set(), {6: 0, 7: 1})
        # The urgent one first, as before, then the fixed batch, then
        # the overdue one.
        self.assertEqual([p["number"] for p in out], [8, 6, 7, 5])
        self.assertEqual(overdue, {5: 200})


class Recut(unittest.TestCase):
    """designs/0044, rule 5: what counts as re-cutting a batch whose
    top's run has started."""

    def last(self, sizes=(2,), started=True):
        entries = [{"pr": n, "head": "h%d" % n, "sha": "s%d" % n,
                    "tree": "t%d" % n} for n in (5, 6, 7, 8)]
        sizes = list(sizes)
        value = {"entries": entries, "batch": {"size": sizes[0]},
                 "later": [{"size": k} for k in sizes[1:]]}
        check = {"ahead_started": {"s%d" % (4 + sum(sizes)): "x"}
                 if started else {}, "results": {}}
        return value, check

    def entries(self, *pairs):
        return [{"pr": n, "tree": t} for n, t in pairs]

    def test_growing_and_merging_are_no_re_cut(self):
        last, check = self.last()
        everyone = {5, 6, 7, 8}
        self.assertFalse(m.recut(last, self.entries(
            (5, "t5"), (6, "t6"), (9, "t9")), everyone, set(), check))
        self.assertFalse(m.recut(last, self.entries((6, "t6"), (7, "x")),
                                 everyone, {5}, check))
        self.assertFalse(m.recut(last, self.entries((7, "x")), {7, 8},
                                 set(), check))

    def test_one_in_front_or_one_dropping_out_is(self):
        last, check = self.last()
        everyone = {4, 5, 6, 7, 8}
        self.assertTrue(m.recut(last, self.entries(
            (4, "t4"), (5, "u5"), (6, "u6")), everyone, set(), check))
        self.assertTrue(m.recut(last, self.entries((6, "u6"), (7, "u7")),
                                everyone, set(), check))

    def test_a_batch_not_started_is_not(self):
        last, check = self.last(started=False)
        self.assertFalse(m.recut(last, self.entries((4, "t4")), {4, 5, 6},
                                 set(), check))

    def test_counting(self):
        m.configure(project(test_ahead={"batch": 2, "batches": 2,
                                        "unstable_after_recuts": 2}))
        self.addCleanup(m.configure, project())
        records = [{"recut": True}, {}, {"recut": True}]
        self.assertIsNone(m.unstable(records, False))
        self.assertEqual(m.unstable(records, True), 3)
        m.configure(project(test_ahead={"batch": 2, "batches": 2}))
        self.assertIsNone(m.unstable(records * 9, True))


class FixedOrderTick(RepoCase):
    """Rule 3 in a tick: an overdue candidate and right of way go behind
    a batch whose top's run has started."""

    item = Decide.item
    tick = Tick.tick
    other_pr = Tick.other_pr
    rival = HoldConflicting.rival

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        self.addCleanup(m.configure, project())
        self.state = os.path.join(self.tmp.name, "state")
        os.makedirs(self.state)

    def predicted(self, *prs):
        m.save_json(os.path.join(self.state, m.AHEAD_FILE), {
            "at": "2026-10-07T12:00:00Z", "entries": [
                {"pr": p["number"], "head": p["headRefOid"],
                 "sha": "s%d" % p["number"], "tree": "t%d" % p["number"]}
                for p in prs],
            "batch": {"size": len(prs), "trees": [
                "t%d" % p["number"] for p in prs]}})

    def test_an_overdue_one_goes_behind_a_started_batch(self):
        m.configure(project(age_first={}, test_ahead={"batch": 2}))
        six, seven = self.other_pr(), self.other_pr(7)
        self.tick([self.item()], conclusions=(None,))
        self.tick([pr(5, head=self.head, reviews=[]), six, seven],
                  conclusions=(None,), minutes=1)
        started = {"ahead_started": {"s7": "x"}, "results": {}}
        for check, queue in ((None, [5, 6, 7]), (started, [6, 7, 5])):
            with self.subTest(started=bool(check)):
                self.predicted(six, seven)
                record, _ = self.tick([self.item(), six, seven],
                                      conclusions=(None,), minutes=95,
                                      check_state=check)
                self.assertEqual(record["queue"], queue)
                self.assertEqual(record.get("fixed"),
                                 [6, 7] if check else None)
        # Off, the overdue one goes first as before.
        m.configure(project(age_first={}, test_ahead={
            "batch": 2, "fixed_order": False}))
        self.predicted(six, seven)
        record, _ = self.tick([self.item(), six, seven], conclusions=(None,),
                              minutes=96, check_state=started)
        self.assertEqual(record["queue"], [5, 6, 7])

    def test_right_of_way_holds_no_member_of_a_started_batch(self):
        m.configure(project(right_of_way={}, test_ahead={"batch": 2}))
        RightOfWay.overtaken(self, name="dry-run.jsonl")
        rival = self.rival(created=RightOfWay.OLDER)
        self.predicted(rival)
        record, _ = self.tick([self.item(), rival], conclusions=(None,))
        self.assertEqual(record["held"],
                         {"6": "conflicts with #5, which has right of way"})
        record, _ = self.tick([self.item(), rival], conclusions=(None,),
                              check_state={"ahead_started": {"s6": "x"},
                                           "results": {}})
        self.assertEqual(record["right_of_way"]["pr"], 5)
        self.assertEqual((record["held"], record["queue"]), ({}, [6, 5]))


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

    def test_the_same_owned_lines_are_asked_one_at_a_time(self):
        # agent-forge #264: #7 changes what #6 changes; #8 another file.
        m.configure(project(code_owners=True, code_owner_sequence=True))
        record, _ = self.tick([self.owned_pr(6), self.owned_pr(7),
                               self.owned_pr(8, name="owned/y.json")],
                              live=True)
        self.assertEqual(record["held"], {
            "6": "waits for code owner @TheOwner: owned/x.json",
            "7": "waits for #6: both change owned/x.json lines 0-0, which "
                 "a code owner must approve; one at a time",
            "8": "waits for code owner @TheOwner: owned/y.json"})

    def test_sequencing_is_off_by_default(self):
        record, _ = self.tick([self.owned_pr(6), self.owned_pr(7)],
                              live=True)
        self.assertEqual(record["held"]["7"],
                         "waits for code owner @TheOwner: owned/x.json")

    def test_only_overlapping_lines_wait(self):
        lines = {6: {"c.json": [(10, 12)]}, 7: {"c.json": [(13, 20)]},
                 8: {"c.json": [(12, 12)]}, 9: {"c.json": [(1, 30)]}}
        waits = {n: ("h%d" % n, "waits for code owner") for n in lines}
        waits[10] = ("h10", None)
        with mock.patch.object(m, "owned_lines",
                               lambda repo, pr, *a, **k: lines[pr["number"]]):
            got = m.sequence_owned(None, [{"number": n} for n in waits],
                                   waits, [], "main")
        self.assertEqual(got, {
            8: ("h8", m.SEQUENCE_REASON % (6, "c.json", 12, 12)),
            9: ("h9", m.SEQUENCE_REASON % (6, "c.json", 10, 12))})

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

    def layer(self, base="b5", reviews=None, head=None, body=""):
        return pr(6, T1, base=base, head=head or self.upper, body=body,
                  reviews=[
            review(REVIEWER, oid=self.upper)] if reviews is None else reviews)

    def tick(self, prs, live=True, minutes=0, check_state=None,
             conclusions=("success",)):
        github = FakeGitHub(answers(conclusions, prs=prs))
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
                        live=live, check_state=check_state)
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

    def test_a_retargeting_tick_keeps_the_batch(self):
        # designs/0032: the batch #5 merged from goes on after the tick
        # that retargets #6. Written empty there, it was forgotten and
        # its members waited for a new run.
        m.configure(project(stacks={}, test_ahead={"batch": 2}))
        self.merge_bottom()
        path = os.path.join(self.state, m.AHEAD_FILE)
        before = m.load_json(path, None)
        self.assertIn("batch", before)
        record, _ = self.tick([self.layer()], minutes=1)
        self.assertEqual(record["decisions"][0]["action"], "RETARGET")
        self.assertEqual(m.load_json(path, None), before)

    def test_a_ready_layer_joins_its_bases_batch(self):
        # designs/0035: #6 is predicted straight after #5, so the run of
        # #5's batch covers it too.
        m.configure(project(stacks={},
                            test_ahead={"batch": 2, "stacks": True}))
        record = self.merge_bottom()
        self.assertEqual(record["ahead"], [5, 6])
        ahead = m.load_json(os.path.join(self.state, m.AHEAD_FILE), None)
        self.assertEqual([e["pr"] for e in ahead["entries"]], [5, 6])
        self.assertEqual(ahead["batch"]["size"], 2)

    def test_the_base_merges_on_its_batchs_green_with_the_layer_held(self):
        # #6 is still held as stacked when #5's covered head merges; the
        # batch holds while #6 is ready at the head predicted.
        self.base_merges_on_the_batchs_green(self.layer())

    def test_a_layer_blocked_by_its_base_doesnt_hold_the_base_for_ever(self):
        # A layer that names the layer below as Blocked-by waits only for
        # the stack: the batch hold still finds it ready, so #5 merges.
        self.base_merges_on_the_batchs_green(
            self.layer(body="Blocked-by: #5"))

    def base_merges_on_the_batchs_green(self, layer):
        m.configure(project(stacks={},
                            test_ahead={"batch": 2, "stacks": True}))
        covered = {SLUG + "/commits/": [status(
            description="green: covered by batch #5..#6 (tested as x)")]}
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)

        def tick(check_state=None):
            github = FakeGitHub(dict(answers(prs=[self.bottom(), layer]),
                                     **covered))
            return m.tick(self.repo.path, self.state, github, tools_run(),
                          lambda line: None, now=lambda: start, live=True,
                          check_state=check_state)
        record = tick()
        self.assertEqual(record["decisions"][0]["action"], "WAIT")
        self.assertEqual(record["batch"], [5, 6])
        top = m.load_json(os.path.join(self.state, m.AHEAD_FILE),
                          None)["entries"][1]
        record = tick({"results": {top["sha"]: {"tree": top["tree"],
                                                "ok": True}}})
        self.assertEqual(record["decisions"][0]["action"], "MERGE")

    def test_a_layer_waiting_for_an_instance_stays_out(self):
        # A batch's run is not a gated instance's: a layer still waiting
        # for vm/mac would leave the batch at its merge and spend it.
        m.configure(gated_project({"paths": ["u.*"]}, stacks={},
                                  test_ahead={"batch": 2, "stacks": True}))
        record = self.merge_bottom()
        self.assertEqual(record["ahead"], [5])
        # Once it is green there, it joins as before.
        m.configure(gated_project({"paths": ["nothing.*"]}, stacks={},
                                  test_ahead={"batch": 2, "stacks": True}))
        self.state = os.path.join(self.tmp.name, "ungated")
        record, _ = self.tick([self.bottom(), self.layer()])
        self.assertEqual(record["ahead"], [5, 6])

    def test_a_layer_not_yet_approved_stays_out(self):
        m.configure(project(stacks={},
                            test_ahead={"batch": 2, "stacks": True}))
        record, _ = self.tick([self.bottom(), self.layer(reviews=[])])
        self.assertEqual(record["ahead"], [5])
        # Without the setting, an approved one stays out too.
        m.configure(project(stacks={}, test_ahead={"batch": 2}))
        self.state = os.path.join(self.tmp.name, "without")
        record, _ = self.tick([self.bottom(), self.layer()])
        self.assertEqual(record["ahead"], [5])

    def own_green_tick(self, layers_need_own_green, minutes=0,
                       check_state=None, **ahead):
        """A live tick with merge.own_head_green on, #5 green on its own
        and #6, its layer, with no run of its own yet."""
        m.configure(project(stacks={}, own_head_green=True, test_ahead=dict(
            {"batch": 2, "stacks": True, "trains": "live",
             "layers_need_own_green": layers_need_own_green}, **ahead)))
        table = dict(answers(prs=[self.bottom(), self.layer()]), **{
            SLUG + "/commits/": [],
            SLUG + "/commits/" + self.lower: [status()],
            SLUG + "/pulls/5": {"merged": True},
            SLUG + "/pulls/6": {"merged": True}})
        self.github = FakeGitHub(table)
        base = tools_run()

        def run(args, cwd=None, env=None):
            if "push" in args:
                args = [self.repo.origin if a.startswith("https://github.com/")
                        else a for a in args]
            return base(args, cwd, env)
        lines = []
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        with mock.patch.object(m, "RESOLVE_IDENTITY", ("Merger", "m@x")), \
                mock.patch.object(m, "CI_WORKFLOW", ""):
            record = m.tick(self.repo.path, self.state, self.github, run,
                            lines.append, now=lambda: start
                            + datetime.timedelta(minutes=minutes),
                            live=True, check_state=check_state)
        return record, lines

    def test_a_layer_boards_without_its_own_green_when_told(self):
        # Amendment of 2026-11-12: the train's top tests it.
        record, _ = self.own_green_tick(True)
        self.assertEqual(record["ahead"], [5])
        self.state = os.path.join(self.tmp.name, "without")
        record, _ = self.own_green_tick(False)
        self.assertEqual(record["ahead"], [5, 6])

    def test_its_train_merges_as_one_on_the_tops_green(self):
        # Main moves on, so #5 isn't merged on its own at once.
        self.advance_main("n.txt")
        record, _ = self.own_green_tick(False, train_merge=True)
        self.assertEqual(record["ahead"], [5, 6])
        top = m.load_json(os.path.join(self.state, m.AHEAD_FILE),
                          None)["entries"][1]
        record, lines = self.own_green_tick(
            False, minutes=1, train_merge=True, check_state={"results": {
                top["sha"]: {"tree": top["tree"], "ok": True}}})
        self.assertEqual([(d["pr"], d["action"], d.get("train"))
                          for d in record["decisions"]],
                         [(5, "MERGE", True), (6, "MERGE", True)])

    def test_a_boarded_layer_needs_no_green_of_its_own(self):
        github = FakeGitHub({SLUG + "/commits/": []})
        layer = self.layer()
        approval = m.approval_of(layer)
        self.assertIsNotNone(m.own_green_wait(layer, approval, github, {}))
        self.assertIsNone(m.own_green_wait(layer, approval, github, {},
                                           boarded=self.upper))
        self.assertIsNotNone(m.own_green_wait(layer, approval, github, {},
                                              boarded="f" * 40))

    def test_boarded_layers(self):
        last = {"trains": True, "entries": [
            {"pr": 5, "head": "a", "carriage": 5},
            {"pr": 6, "head": "b", "carriage": 5, "on": 5},
            {"pr": 7, "head": "c", "carriage": 7},
            # Its base merged since: it heads its carriage, still a layer.
            {"pr": 8, "head": "d", "carriage": 8, "on": 4}]}
        self.assertEqual(m.boarded_layers(last), {6: "b", 8: "d"})
        self.assertEqual(m.boarded_layers(dict(last, trains=False)), {})
        self.assertEqual(m.boarded_layers(None), {})

    def test_boarded_members(self):
        last = {"trains": True, "entries": [
            {"pr": 5, "head": "a"}, {"pr": 6, "head": "b"}]}
        self.assertEqual(m.boarded_members(last), {5: "a", 6: "b"})
        self.assertEqual(m.boarded_members(dict(last, trains=False)), {})
        self.assertEqual(m.boarded_members(None), {})
        # A red train's search: its batch and the trains it keeps, or
        # the members flip between held and not every other tick and
        # the search starts over each time (a sim, 2026-11-12).
        searching = {"entries": [{"pr": 5, "head": "a"}],
                     "kept_trains": last}
        self.assertEqual(m.boarded_members(searching), {5: "a", 6: "b"})

    def hand_tick(self, pulls, minutes=1):
        """A live tick with only #6 open, and GitHub answering the
        closed pull requests from b5 with `pulls`."""
        github = FakeGitHub(dict(answers(prs=[self.layer()]), **{
            SLUG + "/pulls?state=closed": pulls}))
        self.github = github
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        return m.tick(self.repo.path, self.state, github, tools_run(),
                      lambda line: None, now=lambda: start
                      + datetime.timedelta(minutes=minutes), live=True), github

    def test_a_base_merged_by_hand_still_retargets_its_layer(self):
        # #5 merged on GitHub, not by the Merger: #6 was held as based on
        # b5 for ever.
        self.squash_bottom_onto_main()
        merged = [{"number": 5, "merged_at": "2026-10-07T11:59:00Z",
                   "base": {"ref": "main"}, "head": {"sha": self.lower}}]
        record, github = self.hand_tick(merged)
        self.assertEqual([(d["pr"], d["action"]) for d in record["decisions"]],
                         [(6, "RETARGET")])
        self.assertEqual(github.writes, [
            ("PATCH", SLUG + "/pulls/6", {"base": "main"})])
        self.assertEqual(self.stack_file()["pending"], [{
            "pr": 6, "after": 5, "branch": "b5", "head": self.lower}])

    def test_a_base_with_no_merged_pull_request_is_asked_hourly(self):
        record, github = self.hand_tick([{"number": 5, "merged_at": None,
                                          "base": {"ref": "main"},
                                          "head": {"sha": self.lower}}])
        self.assertEqual(record["held"], {"6": "based on b5, not main"})
        asked = lambda g: [c for c in g.calls if "state=closed" in c[0]]
        self.assertEqual(len(asked(github)), 1)
        _, github = self.hand_tick([], minutes=30)
        self.assertEqual(asked(github), [])
        _, github = self.hand_tick([], minutes=62)
        self.assertEqual(len(asked(github)), 1)

    def test_a_base_the_merger_merged_is_not_asked_about(self):
        # Its layers were recorded then; one that left the stack stays out.
        self.merge_bottom()
        m.save_stack(self.state, {})
        record, github = self.hand_tick([], minutes=2)
        self.assertEqual([c for c in github.calls if "state=closed" in c[0]],
                         [])
        self.assertEqual(record["held"], {"6": "based on b5, not main"})

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

    def test_a_layer_back_mid_batch_waits_for_the_next(self):
        # #4654: a layer whose base merged went first, into the batch
        # being run, and each one that came back spent that batch's run.
        m.configure(project(stacks={}, test_ahead={"batch": 2}))
        self.merge_bottom()
        older = self.other(4)
        self.repo.branch("other", "origin/main")
        sha = self.repo.commit("p.txt", "oldest\n", "oldest")
        self.repo.publish_pr(3, sha)
        self.repo.branch("feature", "feature")
        oldest = pr(3, "2026-10-07T08:00:00Z", head=sha,
                    reviews=[review(REVIEWER, oid=sha)])

        def queue(predicted):
            m.save_json(os.path.join(self.state, m.AHEAD_FILE), {
                "at": "2026-10-07T12:01:00Z", "entries": [
                    {"pr": p["number"], "head": p["headRefOid"]}
                    for p in predicted]})
            m.save_json(os.path.join(self.state, "next.json"), {
                "at": "2026-10-07T12:01:00Z", "pr": 4, "action": "WAIT"})
            record, _ = self.tick([oldest, older, self.layer("main")],
                                  live=False, minutes=2)
            return record["queue"]
        # Behind #4's batch, then first in the next.
        self.assertEqual(queue([older, oldest]), [4, 3, 6])
        # In the running batch itself, it keeps its place there.
        self.assertEqual(queue([older, self.layer("main"), oldest]),
                         [4, 6, 3])
        # With no batch running, it goes first.
        os.remove(os.path.join(self.state, "next.json"))
        record, _ = self.tick([oldest, older, self.layer("main")],
                              live=False, minutes=2)
        self.assertEqual(record["queue"][0], 6)

    def test_a_layer_back_between_a_green_batchs_merges_waits(self):
        # Seen on 2026-10-26: a batch of 8 went green and its first
        # member merged; three layers whose bases had just merged went
        # in front of the other seven, and the top's green was spent.
        m.configure(project(stacks={}, test_ahead={"batch": 2}))
        self.merge_bottom()
        older = self.other(4)
        self.repo.branch("other", "origin/main")
        sha = self.repo.commit("p.txt", "oldest\n", "oldest")
        self.repo.publish_pr(3, sha)
        self.repo.branch("feature", "feature")
        oldest = pr(3, "2026-10-07T08:00:00Z", head=sha,
                    reviews=[review(REVIEWER, oid=sha)])
        m.save_json(os.path.join(self.state, m.AHEAD_FILE), {
            "at": "2026-10-07T12:01:00Z", "entries": [
                {"pr": 4, "head": older["headRefOid"], "tree": "t4"},
                {"pr": 3, "head": sha, "tree": "t3"}],
            "batch": {"size": 2, "trees": ["t4", "t3"]}})

        def queue(ok):
            record, _ = self.tick(
                [oldest, older, self.layer("main")], live=False, minutes=2,
                check_state={"results": {"x" * 40: {"tree": "t3",
                                                    "ok": ok}}})
            return record["queue"]
        # No merge is in flight, but the batch's top is green.
        self.assertEqual(queue(True), [4, 3, 6])
        # A red top is no batch to wait for.
        self.assertEqual(queue(False)[0], 6)

    def test_a_layer_predicted_past_a_green_batch_still_waits(self):
        # Seen on 2026-10-26: a layer predicted 16th, behind both
        # batches, counted as predicted and went in front of a green
        # batch's six members left to merge.
        m.configure(project(stacks={}, test_ahead={"batch": 2}))
        self.merge_bottom()
        older = self.other(4)
        self.repo.branch("other", "origin/main")
        sha = self.repo.commit("p.txt", "oldest\n", "oldest")
        self.repo.publish_pr(3, sha)
        self.repo.branch("feature", "feature")
        oldest = pr(3, "2026-10-07T08:00:00Z", head=sha,
                    reviews=[review(REVIEWER, oid=sha)])
        layer = self.layer("main")
        m.save_json(os.path.join(self.state, m.AHEAD_FILE), {
            "at": "2026-10-07T12:01:00Z", "entries": [
                {"pr": 4, "head": older["headRefOid"], "tree": "t4"},
                {"pr": 3, "head": sha, "tree": "t3"},
                {"pr": 6, "head": layer["headRefOid"], "tree": "t6"}],
            "batch": {"size": 2, "trees": ["t4", "t3"]}})
        record, _ = self.tick(
            [oldest, older, layer], live=False, minutes=2,
            check_state={"results": {"x" * 40: {"tree": "t3", "ok": True}}})
        self.assertEqual(record["queue"], [4, 3, 6])

    def test_a_layer_waits_for_a_green_later_batch_once_the_batch_merged(self):
        # Seen on 2026-10-26: the batch merged, the one predicted on it
        # was green, and a layer back from below went in front of it.
        m.configure(project(stacks={}, test_ahead={"batch": 2,
                                                   "batches": 2}))
        self.merge_bottom()
        older = self.other(4)
        self.repo.branch("other", "origin/main")
        sha = self.repo.commit("p.txt", "oldest\n", "oldest")
        self.repo.publish_pr(3, sha)
        self.repo.branch("feature", "feature")
        oldest = pr(3, "2026-10-07T08:00:00Z", head=sha,
                    reviews=[review(REVIEWER, oid=sha)])
        m.save_json(os.path.join(self.state, m.AHEAD_FILE), {
            "at": "2026-10-07T12:01:00Z", "entries": [
                {"pr": 9, "head": "9" * 40, "tree": "t9"},
                {"pr": 3, "head": sha, "tree": "t3"},
                {"pr": 4, "head": older["headRefOid"], "tree": "t4"}],
            "batch": {"size": 1, "trees": ["t9"]},
            "later": [{"size": 2, "trees": ["t3", "t4"], "fixed": True}]})

        def queue(ok):
            record, _ = self.tick(
                [oldest, older, self.layer("main")], live=False, minutes=2,
                check_state={"results": {"x" * 40: {"tree": "t4",
                                                    "ok": ok}}})
            return record["queue"]
        self.assertEqual(queue(True), [3, 4, 6])
        # Its top's run has started, so its order is fixed, green or not
        # (designs/0044, rule 3); before that rule the layer went first.
        self.assertEqual(queue(None), [3, 4, 6])
        m.configure(project(stacks={}, test_ahead={
            "batch": 2, "batches": 2, "fixed_order": False}))
        self.assertEqual(queue(None)[0], 6)

    def test_an_urgent_one_goes_ahead_of_a_layer_that_comes_back(self):
        # 2026-10-26: an urgent pin waited behind four layers in turn.
        m.configure(urgent_project(stacks={}, test_ahead={"batch": 2}))
        self.merge_bottom()
        older = labelled(self.other(4), "Priority:urgent")
        record, _ = self.tick([older, self.layer("main")], live=False,
                              minutes=2)
        self.assertEqual(record["queue"], [4, 6])

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

    # designs/0033: with wait_hours, a layer that isn't ready waits.

    WAITING = ("stacked on #5, which merged; waiting for: no Reviewer "
               "approval")

    def wait(self, hours=24):
        m.configure(project(stacks={"wait_hours": hours}))
        self.merge_bottom()
        record, lines = self.tick([self.layer(reviews=[])], minutes=1)
        self.assertEqual(self.github.writes, [])
        self.assertEqual(record["held"], {"6": self.WAITING})
        self.assertEqual(record["stack"], [{"pr": 6, "after": 5,
                                            "waiting": True}])
        return record

    def test_a_layer_that_is_not_eligible_waits(self):
        self.wait()
        self.assertEqual(self.stack_file()["pending"], [{
            "pr": 6, "after": 5, "branch": "b5", "head": self.lower,
            "since": "2026-10-07T12:01:00Z"}])
        # Judged again each tick, waiting since the first.
        record, _ = self.tick([self.layer(reviews=[])], minutes=30)
        self.assertEqual(record["held"], {"6": self.WAITING})
        self.assertEqual(self.stack_file()["pending"][0]["since"],
                         "2026-10-07T12:01:00Z")

    def test_a_waiting_layer_is_retargeted_once_approved(self):
        self.wait()
        record, lines = self.tick([self.layer()], minutes=40)
        self.assertEqual([(d["pr"], d["action"], d.get("result"))
                          for d in record["decisions"]],
                         [(6, "RETARGET", "done")])
        self.assertIn("STACK #6: retargeted to main after #5 merged; "
                      "0 above", lines)
        self.assertEqual(self.stack_file()["pending"], [{
            "pr": 6, "after": 5, "branch": "b5", "head": self.lower,
            "waited": True}])

    def test_a_layer_that_waited_keeps_its_usual_place(self):
        self.wait()
        self.tick([self.layer()], minutes=40)
        older = self.other()
        record, _ = self.tick([older, self.layer("main")], live=False,
                              minutes=41)
        self.assertEqual(record["queue"], [4, 6])
        self.assertEqual(record["stack"], [{"pr": 6, "after": 5}])

    def test_a_waiting_layer_outlives_a_day_old_stack(self):
        self.wait(hours=48)
        record, _ = self.tick([self.layer(reviews=[])], minutes=60 * 25)
        self.assertEqual(record["held"], {"6": self.WAITING})
        record, _ = self.tick([self.layer()], minutes=60 * 26)
        self.assertEqual([(d["pr"], d["action"])
                          for d in record["decisions"]], [(6, "RETARGET")])

    def test_after_wait_hours_it_leaves_the_stack_and_is_told(self):
        self.wait()
        record, _ = self.tick([self.layer(reviews=[])], minutes=60 * 24 + 2)
        self.assertEqual(record["held"], {
            "6": "stacked on #5, which merged, but no Reviewer approval"})
        self.assertNotIn("stack", record)
        self.assertIsNone(self.stack_file())
        self.assertEqual(record["stack_waited"], [
            {"pr": 6, "after": 5, "result": "done"}])
        (method, path, body), = self.github.writes
        self.assertEqual((method, path), ("POST", SLUG + "/issues/6/comments"))
        self.assertTrue(body["body"].startswith("Not-a-review\n\n"))
        self.assertIn("This layer waited 24 hours for its approval after #5 "
                      "merged, and the Merger no longer retargets it. "
                      "Retarget it to `main` and ask for its approvals "
                      "again.", body["body"])
        # Said once: it has left the stack.
        record, _ = self.tick([self.layer(reviews=[])], minutes=60 * 24 + 3)
        self.assertEqual(self.github.writes, [])
        self.assertNotIn("stack_waited", record)

    def test_a_dry_run_says_nothing_of_one_waited_out(self):
        self.wait()
        record, _ = self.tick([self.layer(reviews=[])], live=False,
                              minutes=60 * 24 + 2)
        self.assertEqual(record["stack_waited"], [{"pr": 6, "after": 5}])

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

    def test_a_layer_waiting_for_its_own_green_keeps_its_restack(self):
        # own_green_to_board false: a layer on main held only for its own
        # green may board a train, so the stack keeps it and #5's final
        # head to restack onto (a sim, 2026-11-12: dropped, it was
        # handed back for a conflict the restack would have resolved).
        self.lower_moves_on(self.lines(l8="U"), self.lines(l3="Y"))
        m.configure(project(stacks={}, own_head_green=True, test_ahead={
            "batch": 2, "trains": "live", "own_green_to_board": False}))
        record, _ = self.tick([self.layer("main")], minutes=2,
                              conclusions=(None,))
        self.assertIn("6", record["held"])
        self.assertEqual([(e["pr"], e["head"]) for e in
                          self.stack_file()["pending"]], [(6, self.lower)])

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

    def test_a_body_edited_after_a_comment_hold_decides(self):
        """The body is dated by its last edit: an edit that clears the
        hold, made after a comment set it, is the newer text."""
        item = pr(body="Blocked-by: none",
                  comments=[comment(PEER, at=TA, body="Blocked-by: #2")])
        item["lastEditedAt"] = TB
        self.assertIsNone(self.held(item))
        item["body"] = "Blocked-by: #3"
        self.assertEqual(self.held(item), "Blocked-by: #3")

    def test_an_unedited_body_is_dated_by_its_creation(self):
        item = pr(created=TB, body="Blocked-by: #3",
                  comments=[comment(PEER, at=TA, body="Blocked-by: #2")])
        self.assertEqual(self.held(item), "Blocked-by: #3")
        # Created before the comment: the comment is newer.
        item["createdAt"] = T0
        self.assertEqual(self.held(item), "Blocked-by: #2")

    def test_a_body_edit_older_than_the_comment_does_not_win(self):
        item = pr(body="Blocked-by: none",
                  comments=[comment(PEER, at=TB, body="Blocked-by: #2")])
        item["lastEditedAt"] = TA
        self.assertEqual(self.held(item), "Blocked-by: #2")

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

    def test_the_hold_says_approvals_never_hold_by_default(self):
        text = m.spoken_text(review(REVIEWER, at=T1), (PEER, T2, "comment"))
        self.assertIn("Comments by the author, approvals and comments "
                      "marked `Not-a-review` don't hold", text)
        self.assertNotIn("with text", text)

    def test_the_hold_says_an_approval_with_text_holds_with_approvals(self):
        # An approval with text was said to hold nothing, while the
        # hold it explained was for one.
        m.configure(project(said_since={"approvals": True}))
        text = m.spoken_text(review(REVIEWER, at=T1), (PEER, T2, "review"))
        self.assertIn("Comments by the author, approvals with no text and "
                      "comments marked `Not-a-review` don't hold", text)
        self.assertIn("An approval with text after the one that counts "
                      "does", text)

    def test_the_hold_leaves_the_author_out_with_author(self):
        m.configure(project(said_since={"author": True}))
        text = m.spoken_text(review(REVIEWER, at=T1), (AUTHOR, T2, "comment"))
        self.assertIn("Approvals and comments marked `Not-a-review` don't "
                      "hold", text)
        self.assertNotIn("by the author", text)

    def test_the_authors_remark_holds_with_author(self):
        m.configure(project(said_since={"author": True}))
        self.assertEqual(self.said([comment(AUTHOR, at=T2)]),
                         [(AUTHOR, T2, "comment")])

    def test_a_later_approval_holds_with_approvals(self):
        m.configure(project(said_since={"approvals": True}))
        self.assertEqual(self.said(reviews=[review(PEER, at=T2,
                                                   body="one thing")]),
                         [(PEER, T2, "review")])

    def test_a_bare_later_approval_never_holds(self):
        m.configure(project(said_since={"author": True, "approvals": True}))
        self.assertEqual(self.said(reviews=[review(PEER, at=T2)]), [])
        self.assertEqual(self.said(reviews=[review(PEER, at=T2,
                                                   body="  \n")]), [])

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

    def test_a_pull_ref_left_at_an_old_commit_is_fetched_by_sha(self):
        # GitHub's ref stayed at #5's old head while the API named the
        # new one; decide() then read it as a head that moved, for ever.
        newer = self.repo.commit("n.txt", "newer\n", "newer")
        run_git(self.repo.path, "push", "-q", "origin", "HEAD:refs/heads/b5")
        self.assertEqual(m.fetch_pr(self.repo.path, 5, newer).returncode, 0)
        self.assertEqual(run_git(self.repo.path, "rev-parse", m.PR_REF % 5),
                         newer)

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


class StallFromRecentRuns(unittest.TestCase):
    """merge.stall_run_factor: a wait is a stall only once it is longer
    than the build check's recent runs take, by the factor, with
    stall_minutes as the floor."""

    START = QueuedStall.START

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name
        self.addCleanup(m.configure, project())

    def runs(self, *minutes, **extra):
        results = {}
        for k, took in enumerate(minutes):
            start = self.START - datetime.timedelta(hours=10 - k)
            results["%040d" % k] = dict({
                "started": m.iso(start), "seconds": took * 60,
                "finished": m.iso(start + datetime.timedelta(minutes=took))},
                **extra)
        return {"results": results}

    def stalled(self, minutes, check, action="WAIT"):
        waiting = {"pr": 5, "head": "h" * 40, "action": action}
        m.stalled_for(self.state, waiting, self.START, check)
        return m.stalled_for(
            self.state, waiting,
            self.START + datetime.timedelta(minutes=minutes), check)

    def test_the_limit_follows_the_median_run(self):
        m.configure(project())
        check = self.runs(40, 20, 44, 40, 60)
        self.assertEqual(m.stall_limit(check),
                         datetime.timedelta(minutes=60))
        self.assertIsNone(self.stalled(59, check))
        self.assertEqual(self.stalled(60, check), 60)

    def test_a_batch_tops_limit_follows_the_long_runs(self):
        # A top runs every step its members touch: as long as the
        # longest heads, not the median (2026-11-12: 17-minute median,
        # 40-minute train tops, every train called a stall).
        m.configure(project())
        check = self.runs(*([1] * 4 + [16] * 12 + [41, 47, 44, 40]))
        self.assertEqual(m.stall_limit(check),
                         datetime.timedelta(minutes=30))
        self.assertEqual(m.stall_limit(check, top=True),
                         datetime.timedelta(minutes=44 * 1.5))

    def test_stall_minutes_is_the_floor(self):
        m.configure(project())
        check = self.runs(5, 6, 7)
        self.assertEqual(m.stall_limit(check),
                         datetime.timedelta(minutes=30))
        self.assertEqual(self.stalled(30, check), 30)

    def test_only_the_recent_runs_count(self):
        m.configure(project())
        check = self.runs(*([100] * 5 + [20] * m.STALL_RUNS))
        self.assertEqual(m.stall_limit(check),
                         datetime.timedelta(minutes=30))

    def test_copied_results_and_no_results_are_not_runs(self):
        m.configure(project())
        self.assertEqual(m.stall_limit(self.runs(60, 60, covered=True)),
                         datetime.timedelta(minutes=30))
        self.assertEqual(m.stall_limit(self.runs(60, 60, seconds=0)),
                         datetime.timedelta(minutes=30))
        self.assertEqual(m.stall_limit(None), datetime.timedelta(minutes=30))

    def test_a_refused_write_keeps_stall_minutes(self):
        m.configure(project())
        self.assertEqual(self.stalled(30, self.runs(60, 60, 60),
                                      action="UPDATE"), 30)

    def test_the_factor_and_off(self):
        m.configure(project(stall_run_factor=2))
        self.assertEqual(m.stall_limit(self.runs(40, 40, 40)),
                         datetime.timedelta(minutes=80))
        m.configure(project(stall_run_factor=0))
        self.assertEqual(m.stall_limit(self.runs(40, 40, 40)),
                         datetime.timedelta(minutes=30))

    def test_the_check_state_lists_the_running_slots(self):
        with open(os.path.join(self.state, "state.json"), "w") as out:
            out.write('{"results": {}}')
        held = locks.try_lock(os.path.join(self.state, "worker-2.lock"),
                              "#6 %s 2026-10-07T12:16:00Z pid=1 ahead"
                              % ("s" * 40))
        self.addCleanup(held.close)
        idle = locks.try_lock(os.path.join(self.state, "worker.lock"), "#7 x y")
        idle.close()
        got = m.load_check_state(self.state)
        self.assertEqual(got["running"], [{
            "pr": 6, "sha": "s" * 40, "started": "2026-10-07T12:16:00Z"}])


class BatchTopStall(unittest.TestCase):
    """A head first in the batch waits for its top's prediction: timed
    from when the build check started the top."""

    START = QueuedStall.START
    HEAD = "h" * 40

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.state = tmp.name
        m.configure(project())
        self.addCleanup(m.configure, project())
        self.ahead({"size": 2}, [
            {"pr": 5, "head": self.HEAD, "sha": "p" * 40, "tree": "t1"},
            {"pr": 6, "head": "j" * 40, "sha": "q" * 40, "tree": "t2"}])

    def ahead(self, batch, entries):
        m.save_json(os.path.join(self.state, m.AHEAD_FILE),
                    {"at": "2026-10-07T12:00:00Z", "batch": batch,
                     "entries": entries})

    def stalled(self, minutes, started, pr=5, action="WAIT"):
        waiting = {"pr": pr, "head": self.HEAD, "action": action}
        check = {"waiting": {}, "ahead_started": started}
        m.stalled_for(self.state, waiting, self.START, check)
        return m.stalled_for(
            self.state, waiting,
            self.START + datetime.timedelta(minutes=minutes), check)

    def test_timed_from_the_tops_start(self):
        started = {"q" * 40: "2026-10-07T12:16:00Z"}
        self.assertIsNone(self.stalled(31, started))
        self.assertEqual(self.stalled(46, started), 46)

    def test_a_top_not_started_is_as_before(self):
        self.assertEqual(self.stalled(30, {"p" * 40: "2026-10-07T12:16:00Z"}),
                         30)

    def test_not_first_in_the_batch_is_as_before(self):
        self.assertEqual(self.stalled(
            30, {"q" * 40: "2026-10-07T12:16:00Z"}, pr=6), 30)

    def test_a_top_started_before_the_wait_counts_from_the_wait(self):
        self.assertEqual(self.stalled(
            30, {"q" * 40: "2026-10-07T11:00:00Z"}), 30)

    def test_a_refused_write_is_as_before(self):
        self.assertEqual(self.stalled(
            30, {"q" * 40: "2026-10-07T12:16:00Z"}, action="UPDATE"), 30)

    def test_the_stall_names_the_batch_and_its_top(self):
        # agent-forge #285: "look at the build check" sent agents to a
        # status that still said "queued for a check slot".
        self.ahead({"size": 2, "trees": ["t1", "t2"]}, [
            {"pr": 5, "head": self.HEAD, "sha": "p" * 40, "tree": "t1"},
            {"pr": 6, "head": "j" * 40, "sha": "q" * 40, "tree": "t2"}])
        batch = m.stall_batch(self.state, 5)
        self.assertEqual(batch, ("#5..#6", "q" * 40))
        text = m.stall_text({"pr": 5, "head": self.HEAD, "minutes": 40},
                            batch=batch)
        self.assertIn("it is in the Merger's batch #5..#6, whose top `%s` "
                      "the build check runs for it" % ("q" * 10), text)

    def test_no_batch_for_a_pull_request_outside_it(self):
        self.assertIsNone(m.stall_batch(self.state, 9))
        self.assertNotIn("batch", m.stall_text(
            {"pr": 9, "head": self.HEAD, "minutes": 40}))

    def test_a_later_batch_is_found_too(self):
        m.save_json(os.path.join(self.state, m.AHEAD_FILE), {
            "at": "2026-10-07T12:00:00Z",
            "batch": {"size": 1, "trees": ["t1"]},
            "later": [{"size": 2, "trees": ["t2", "t3"]}],
            "entries": [{"pr": 5, "sha": "p" * 40, "tree": "t1"},
                        {"pr": 6, "sha": "q" * 40, "tree": "t2"},
                        {"pr": 7, "sha": "r" * 40, "tree": "t3"}]})
        self.assertEqual(m.stall_batch(self.state, 6), ("#6..#7", "r" * 40))

    def test_a_run_of_the_tops_tree_at_another_commit_counts(self):
        # 2026-11-12: the top wasn't started because an earlier
        # prediction with the same tree was running, which covers it;
        # the wait was called a stall at 30 minutes.
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        repo = Repo(tmp.name)
        tip = repo.main_tip()
        tree = run_git(repo.path, "rev-parse", tip + "^{tree}")
        top = run_git(repo.path, "commit-tree", tree, "-p", tip, "-m", "top")
        earlier = run_git(repo.path, "commit-tree", tree, "-m", "earlier")
        other = repo.commit("o.txt", "o\n", "another tree")
        self.ahead({"size": 2}, [
            {"pr": 5, "head": self.HEAD, "sha": "p" * 40, "tree": "t1"},
            {"pr": 6, "head": "j" * 40, "sha": top, "tree": tree}])

        def stalled(minutes, sha):
            waiting = {"pr": 5, "head": self.HEAD, "action": "WAIT"}
            check = {"waiting": {}, "ahead_started": {}, "running": [
                {"pr": 6, "sha": sha, "started": "2026-10-07T12:16:00Z"}]}
            m.stalled_for(self.state, waiting, self.START, check, repo.path)
            return m.stalled_for(
                self.state, waiting,
                self.START + datetime.timedelta(minutes=minutes), check,
                repo.path)

        self.assertIsNone(stalled(31, earlier))
        self.assertEqual(stalled(46, earlier), 46)
        # A run of another tree is not the top's.
        self.assertEqual(stalled(30, other), 30)

    def test_no_or_a_bad_ahead_file_is_as_before(self):
        self.ahead(None, None)
        self.assertEqual(self.stalled(
            30, {"q" * 40: "2026-10-07T12:16:00Z"}), 30)
        self.ahead({"size": "x"}, [])
        self.assertEqual(self.stalled(
            30, {"q" * 40: "2026-10-07T12:16:00Z"}), 30)


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


UNION_LIST = "# Changes\n\n* base\n\n## Older\n"


class Union(unittest.TestCase):
    """merge.resolve.union (designs/0046): CHANGES.md lists changes, and
    the feature and main each add a line at the same place."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.repo = Repo(self.tmp.name)
        self.state = os.path.join(self.tmp.name, "state")
        os.mkdir(self.state)
        self.write("CHANGES.md", UNION_LIST)
        self.write("code.txt", "a\nb\nc\n")
        self.commit("list")
        self.repo.push("main")
        self.repo.branch("feature")
        self.write("CHANGES.md", UNION_LIST.replace(
            "* base\n", "* base\n* feature\n"))
        self.head = self.commit("feature")
        self.publish(self.head)
        self.repo.branch("main", "main")
        self.write("CHANGES.md", UNION_LIST.replace(
            "* base\n", "* base\n* main\n"))
        self.commit("main")
        self.repo.push("main")
        self.repo.branch("feature", "feature")
        self.configure()
        self.addCleanup(m.configure, project())
        m.RESOLVE_IDENTITY = IDENTITY
        self.addCleanup(setattr, m, "RESOLVE_IDENTITY", None)

    def configure(self, globs=("*.md",), **merge):
        m.configure(project(resolve={"union": list(globs)}, **merge))

    write = Resolve.write
    commit = Resolve.commit
    git_run = Resolve.git_run

    def publish(self, head):
        self.repo.publish_pr(5, head)
        run_git(self.repo.path, "push", "-q", "-f", "origin",
                head + ":refs/heads/b5")

    def item(self, head=None):
        return pr(5, head=head or self.head,
                  reviews=[review(REVIEWER, oid=self.head)])

    def decide(self):
        item = self.item()
        return m.decide(item, m.approval_of(item), self.repo.path,
                        self.repo.main_tip(), FakeGitHub(answers()), {},
                        self.git_run())

    def push(self, github=None):
        decision = self.decide()
        decision.update(pr=5, head=self.head)
        return m.union_push(self.repo.path, decision, self.repo.main_tip(),
                            github or FakeGitHub({}), self.git_run())

    def ahead(self, skip=(), item=None, github=None):
        item = item or self.item()
        item.setdefault("baseRefName", "main")
        item.setdefault("headRefName", "b5")
        return m.resolve_ahead(
            self.repo.path, self.state, [item], set(skip),
            {5: m.approval_of(item)}, self.repo.main_tip(),
            github or FakeGitHub({}), {}, self.git_run())

    def test_resolved_ahead_of_its_turn_with_trains(self):
        got = self.ahead()
        self.assertEqual([d["action"] for d in got], ["UNION"])
        self.assertEqual(got[0]["result"], "done")
        self.assertTrue(got[0]["early"])
        self.assertEqual(got[0]["files"], {"CHANGES.md": [[1, 1]]})

    def test_a_member_of_a_train_is_left_alone(self):
        self.assertEqual(self.ahead(skip=[5]), [])

    def test_a_layer_is_left_alone(self):
        item = self.item()
        item["baseRefName"] = "lower"
        self.assertEqual(self.ahead(item=item), [])

    def test_one_it_cannot_resolve_waits_for_its_turn(self):
        self.configure(globs=("*.txt",))
        self.assertEqual(self.ahead(), [])

    def test_a_failed_resolve_is_handed_back(self):
        m.RESOLVE_IDENTITY = None
        got = self.ahead()
        self.assertEqual([d["action"] for d in got], ["UNION", "HAND-BACK"])
        self.assertIn("could not resolve it (identity)", got[1]["reason"])

    def hand_merge(self, text):
        """The author's own merge of main, resolved as `text`."""
        got = subprocess.run(["git", "-C", self.repo.path, "merge", "-q",
                              "origin/main"], env=git_env(),
                             capture_output=True, text=True)
        self.assertIn("CONFLICT", got.stdout + got.stderr)
        self.write("CHANGES.md", text)
        return self.commit("merge main")

    def covers(self, head):
        return m.covers_head(self.repo.path, self.head, head,
                             "refs/remotes/origin/main")

    def test_the_configuration(self):
        self.assertEqual(m.UNION, ("*.md",))
        self.assertEqual(m.RESOLVE, ())
        m.configure(project(resolve={"union": ["a"], "regenerate": [RULE]}))
        self.assertEqual((m.UNION, len(m.RESOLVE)), (("a",), 1))
        m.configure(project(resolve=[RULE]))
        self.assertEqual((m.UNION, len(m.RESOLVE)), ((), 1))

    def test_a_pure_addition_conflict_is_resolved(self):
        got = self.decide()
        self.assertEqual(got["action"], "UNION")
        self.assertEqual(got["branch"], "b5")
        self.assertEqual(got["files"],
                         [("CHANGES.md", [(["* feature"], ["* main"])])])

    def test_the_resolution_is_committed_pushed_and_said(self):
        github = FakeGitHub({})
        self.assertEqual(self.push(github), "done")
        new = run_git(self.repo.origin, "rev-parse", "b5")
        self.assertEqual(run_git(self.repo.origin, "rev-list", "--parents",
                                 "-n1", new).split()[1:],
                         [self.head, self.repo.main_tip()])
        self.assertEqual(run_git(self.repo.origin, "show",
                                 new + ":CHANGES.md"),
                         "# Changes\n\n* base\n* feature\n* main\n\n## Older")
        self.assertEqual(run_git(self.repo.origin, "log", "-1",
                                 "--format=%an <%ae>", new),
                         "%s <%s>" % IDENTITY)
        self.assertEqual(self.pushed_env["GIT_CONFIG_KEY_0"],
                         "http.https://github.com/.extraheader")
        (method, where, fields), = github.writes
        self.assertEqual((method, where), ("POST",
                                           SLUG + "/issues/5/comments"))
        body = fields["body"]
        self.assertIn("`CHANGES.md`: 1 line from this branch, then 1 line "
                      "from `main`", body)
        self.assertIn("+ * feature\n+ * main\n", body)
        self.assertTrue(body.endswith("— Merger"))
        # Its tree is the resolution, so the approval covers it.
        self.repo.push("main")
        self.assertEqual(self.covers(run_git(self.repo.path, "rev-parse",
                                             "origin/b5")), (True, None))

    def test_a_moved_head_refuses_the_push(self):
        run_git(self.repo.path, "commit", "-q", "--allow-empty", "-m", "x")
        run_git(self.repo.path, "push", "-q", "-f", "origin", "HEAD:b5")
        self.assertTrue(self.push().startswith("refused: "))
        self.assertNotEqual(run_git(self.repo.origin, "rev-parse", "b5"),
                            self.head)

    def test_without_an_identity_it_fails(self):
        m.RESOLVE_IDENTITY = None
        self.assertTrue(self.push().startswith("failed: identity"))

    def test_a_mixed_conflict_is_handed_back(self):
        self.write("code.txt", "a\nfeature\nc\n")
        self.head = self.commit("feature code")
        self.repo.branch("main", "main")
        self.write("code.txt", "a\nmain\nc\n")
        self.commit("main code")
        self.repo.push("main")
        self.repo.branch("feature", "feature")
        self.publish(self.head)
        got = self.decide()
        self.assertEqual((got["action"], got["reason"]),
                         ("HAND-BACK", m.CONFLICT_REASON))
        self.assertIn("code.txt conflicts, and no glob names it",
                      got["detail"])

    def test_a_matching_file_with_a_changed_line_is_handed_back(self):
        self.write("CHANGES.md", UNION_LIST.replace(
            "* base\n", "* BASE\n* feature\n"))
        self.head = self.commit("feature edits")
        self.publish(self.head)
        got = self.decide()
        self.assertEqual((got["action"], got["reason"]),
                         ("HAND-BACK", m.CONFLICT_REASON))
        self.assertIn("changed or removed", got["detail"])

    def test_without_the_setting_it_is_handed_back(self):
        m.configure(project())
        got = self.decide()
        self.assertEqual(got["action"], "HAND-BACK")
        self.assertNotIn("detail", got)

    def test_an_author_merge_of_the_same_tree_is_covered(self):
        head = self.hand_merge(UNION_LIST.replace(
            "* base\n", "* base\n* feature\n* main\n"))
        self.assertEqual(self.covers(head), (True, None))
        # Without the setting it is a hand-resolved conflict, as before.
        m.configure(project())
        ok, why = self.covers(head)
        self.assertFalse(ok)
        self.assertIn("not the clean merge", why)

    def test_an_author_merge_of_another_tree_is_refused(self):
        head = self.hand_merge(UNION_LIST.replace(
            "* base\n", "* base\n* main\n* feature\n"))
        ok, why = self.covers(head)
        self.assertFalse(ok)
        self.assertIn("not the clean merge", why)
        # So is the resolution with anything more in it.
        run_git(self.repo.path, "reset", "-q", "--hard", self.head)
        head = self.hand_merge(UNION_LIST.replace(
            "* base\n", "* base\n* feature\n* main\n* slipped in\n"))
        self.assertFalse(self.covers(head)[0])

    def tick(self, item=None, minutes=0, conclusions=("success",)):
        item = item or self.item()
        github = FakeGitHub(answers(conclusions, prs=[item]))
        start = datetime.datetime(2026, 10, 7, 12, 0, 0,
                                  tzinfo=datetime.timezone.utc)
        lines = []
        record = m.tick(self.repo.path, self.state, github, self.git_run(),
                        lines.append, now=lambda: start + datetime.timedelta(
                            minutes=minutes), live=True)
        return record, github, lines

    def test_a_tick_resolves_it_then_waits_for_the_build_check(self):
        record, github, lines = self.tick()
        (decision,) = record["decisions"]
        self.assertEqual((decision["action"], decision["result"]),
                         ("UNION", "done"))
        self.assertEqual(decision["files"], {"CHANGES.md": [[1, 1]]})
        self.assertIn("UNION #5: merged main into it, keeping both sides' "
                      "added lines in CHANGES.md", lines)
        self.assertEqual([w[1] for w in github.writes],
                         [SLUG + "/issues/5/comments"])
        self.assertEqual(m.load_json(os.path.join(self.state, "next.json"),
                                     {}).get("pr"), 5)
        new = run_git(self.repo.origin, "rev-parse", "b5")
        self.assertNotEqual(new, self.head)
        self.repo.publish_pr(5, new)
        record, github, _ = self.tick(self.item(new), minutes=1,
                                      conclusions=(None,))
        self.assertEqual([d["action"] for d in record["decisions"]],
                         ["WAIT"])
        self.assertEqual(github.writes, [])

    def test_a_tick_that_cannot_push_hands_it_back_once(self):
        m.RESOLVE_IDENTITY = None
        record, github, _ = self.tick()
        self.assertEqual([(d["action"], d.get("result"))
                          for d in record["decisions"]],
                         [("UNION", "failed: identity: the Merger's commit "
                                    "identity is unknown"),
                          ("HAND-BACK", "done")])
        self.assertIn("could not resolve it (identity)",
                      record["decisions"][1]["reason"])
        record, github, _ = self.tick(minutes=1)
        self.assertEqual([(d["action"], d.get("result"))
                          for d in record["decisions"]],
                         [("UNION", "failed: identity: the Merger's commit "
                                    "identity is unknown"),
                          ("HAND-BACK", "already said")])
        self.assertEqual(github.writes, [])

    def test_early_conflicts_leave_generated_files_to_the_merger(self):
        m.configure(project(early_conflicts=True, resolve=[
            {"paths": ["CHANGES.md"], "command": ["true"],
             "check": ["true"]}]))
        got = m.early_conflicts(
            self.repo.path, self.repo.main_tip(), [self.item()],
            {5: m.approval_of(self.item())}, FakeGitHub({}), {}, True,
            self.git_run())
        self.assertEqual(got, [])

    def test_early_conflicts_leave_a_resolvable_one_to_the_merger(self):
        self.configure(early_conflicts=True)
        item = self.item()
        github = FakeGitHub({})
        got = m.early_conflicts(self.repo.path, self.repo.main_tip(), [item],
                                {5: m.approval_of(item)}, github, {}, True,
                                self.git_run())
        self.assertEqual((got, github.writes), ([], []))
        self.configure(globs=("docs/*",), early_conflicts=True)
        got = m.early_conflicts(self.repo.path, self.repo.main_tip(), [item],
                                {5: m.approval_of(item)}, github, {}, True,
                                self.git_run())
        self.assertEqual([(d["pr"], d["action"]) for d in got],
                         [(5, "HAND-BACK")])
        self.assertIn("no glob names it", got[0]["detail"])


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

    def red_then_rerun(self, finished, ok=True, covered=False):
        """#5 handed back for the build check's red under update_once,
        main moved on, and the build check's verdict on the same head
        finished at `finished`; the next tick's record and log."""
        m.configure(project(update_once=True))
        self.addCleanup(m.configure, project())
        record, _ = self.tick([self.item()], conclusions=("failure",),
                              live=True)
        self.assertEqual(record["decisions"][0]["action"], "HAND-BACK")
        saved = m.load_json(os.path.join(self.state, "handed-back.json"), {})
        self.assertEqual(saved["5"]["at"], "2026-10-07T12:00:00Z")
        self.advance_main("m3.txt")
        verdict = {"pr": 5, "ok": ok, "finished": finished,
                   "base": self.repo.main_tip(), "rerun_of": ["test"]}
        if covered:
            verdict["covered"] = True
        return self.tick([self.item()], live=True, minutes=5,
                         check_state={"results": {self.head: verdict}})

    def test_a_green_rerun_after_the_red_releases_it(self):
        # Seen on 2026-11-04: a poisoned cache's red, re-run green within
        # the hour, kept a pull request out for ten hours, until its
        # reviewer approved the same head again.
        record, lines = self.red_then_rerun("2026-10-07T12:03:00Z")
        self.assertIn("HAND-BACK-CLEARED #5: green rerun at %s since its "
                      "red; back in the queue under its approval"
                      % self.head[:10], lines)
        self.assertEqual(record["decisions"][0]["action"], "UPDATE")
        self.assertNotIn("5", m.load_json(os.path.join(
            self.state, "handed-back.json"), {}))

    def assert_still_skipped(self, record, lines):
        self.assertFalse([l for l in lines if "HAND-BACK-CLEARED" in l])
        self.assertEqual(record["decisions"][0]["action"], "SKIP")

    def test_a_green_from_before_the_red_does_not_release_it(self):
        self.assert_still_skipped(*self.red_then_rerun("2026-10-07T11:59:00Z"))

    def test_a_red_rerun_does_not_release_it(self):
        self.assert_still_skipped(*self.red_then_rerun("2026-10-07T12:03:00Z",
                                                       ok=False))

    def test_a_batch_cover_does_not_release_it(self):
        self.assert_still_skipped(*self.red_then_rerun("2026-10-07T12:03:00Z",
                                                       covered=True))

    def test_a_hand_back_kept_without_a_time_is_left_alone(self):
        said = {"5": {"approved": T1, "head": "a" * 40,
                      "reason": "vm/build-and-test is failure: red"}}
        got = m.clear_green_reruns(
            said, [pr(5, head="a" * 40)],
            {"results": {"a" * 40: {"ok": True, "finished": T3}}},
            lambda line: None)
        self.assertEqual((got, list(said)), ([], ["5"]))

    def test_other_hand_backs_are_left_alone(self):
        said = {"5": {"approved": T1, "head": "a" * 40, "at": T1,
                      "reason": m.CONFLICT_REASON}}
        got = m.clear_green_reruns(
            said, [pr(5, head="a" * 40)],
            {"results": {"a" * 40: {"ok": True, "finished": T3}}},
            lambda line: None)
        self.assertEqual((got, list(said)), ([], ["5"]))

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

    def test_union_gives_a_live_run_the_mergers_identity(self):
        self.run_main({"resolve": {"union": ["*.md"]}}, [])
        self.assertEqual(self.identities, ["Merger"])
        self.assertEqual(self.ticks[0]["identity"], IDENTITY)

    def test_the_check_state_is_read_for_queued_stalls(self):
        check = os.path.join(self.tmp, "check")
        os.mkdir(check)
        with open(os.path.join(check, "state.json"), "w") as f:
            json.dump({"waiting": {"h": {}}}, f)
        self.run_main({"queued_stall_minutes": 90},
                      ["--check-state", check])
        # With the build check's running slots, none here.
        self.assertEqual(self.ticks[0]["check_state"],
                         {"waiting": {"h": {}}, "running": []})
        # Without batches (designs/0050 makes them the default).
        self.run_main({"test_ahead": False}, ["--check-state", check])
        self.assertNotIn("check_state", self.ticks[0])



class RedMain(unittest.TestCase):
    """designs/0011, amendment: a red default branch's fix goes first,
    a red that is the base's names no suspect, and urgent pull requests
    are batched apart."""

    def setUp(self):
        m.configure(project(test_ahead={"batch": 4}))
        self.addCleanup(m.configure, project())
        self.addCleanup(setattr, m, "MAIN_RED_FIXES", frozenset())

    def state(self, **results):
        return {"main_red": {"issue": 9, "fixes": [5, True, "6"]},
                "results": results}

    def entries(self, *prs):
        return [{"pr": n, "sha": "%d" % k * 40, "tree": "t%d" % k,
                 "head": "h%d" % k} for k, n in enumerate(prs)]

    def test_the_fixes_are_read_only_while_the_issue_is_open(self):
        self.assertEqual(m.main_red_fixes(self.state()), frozenset({5}))
        self.assertEqual(m.main_red_fixes({"main_red": {"fixes": [5]}}),
                         frozenset())
        self.assertEqual(m.main_red_fixes(None), frozenset())

    def test_a_fix_is_urgent(self):
        m.MAIN_RED_FIXES = frozenset({5})
        self.assertTrue(m.urgent(pr(5, T3)))
        self.assertFalse(m.urgent(pr(3, T0)))

    def test_a_fix_goes_first_behind_the_one_in_flight_and_a_green_batch(self):
        m.MAIN_RED_FIXES = frozenset({5})
        queue = [pr(3, T0), pr(4, T1), pr(6, T2), pr(5, T3)]
        number = lambda q: [p["number"] for p in q]
        self.assertEqual(number(m.fixes_first(queue, None, set())),
                         [5, 3, 4, 6])
        self.assertEqual(number(m.fixes_first(queue, 3, set())),
                         [3, 5, 4, 6])
        self.assertEqual(number(m.fixes_first(queue, 3, {3, 4})),
                         [3, 4, 5, 6])

    def test_a_fix_in_a_green_batch_keeps_its_place_in_it(self):
        # The batch's green covers its trees in its order only.
        m.MAIN_RED_FIXES = frozenset({5})
        queue = [pr(3, T0), pr(4, T1), pr(5, T3), pr(6, T2)]
        number = lambda q: [p["number"] for p in q]
        self.assertEqual(number(m.fixes_first(queue, None, {3, 4, 5})),
                         [3, 4, 5, 6])

    def test_a_red_only_where_the_base_is_red_is_the_bases(self):
        base = "b" * 40
        state = self.state(**{
            base: {"main": True, "ok": False, "failed": ["web", "doc"]},
            "x": {"tree": "t", "ok": False, "failed": ["web"]},
            "c": {"tree": "t", "ok": True, "covered": "#1..#2"}})
        self.assertEqual(m.tree_base_red(state, "t", base), ["web"])
        state["results"]["y"] = {"tree": "t", "ok": False,
                                 "failed": ["rust"]}
        self.assertIsNone(m.tree_base_red(state, "t", base))

    def test_a_red_with_a_green_base_or_none_is_its_own(self):
        base = "b" * 40
        red = {"x": {"tree": "t", "ok": False, "failed": ["web"]}}
        self.assertIsNone(m.tree_base_red(self.state(**red), "t", base))
        green = dict(red, **{base: {"main": True, "ok": True,
                                    "failed": []}})
        self.assertIsNone(m.tree_base_red(self.state(**green), "t", base))
        self.assertIsNone(m.tree_base_red(self.state(), "t", base))

    def test_a_head_red_only_where_its_tip_was(self):
        base = "b" * 40
        state = self.state(**{
            base: {"main": True, "ok": False, "failed": ["web"]},
            "h": {"ok": False, "failed": ["web"], "base": base,
                  "base_red": True},
            "g": {"ok": False, "failed": ["web"], "base": base}})
        self.assertEqual(m.head_base_red(state, "h"), ["web"])
        self.assertIsNone(m.head_base_red(state, "g"))
        self.assertIsNone(m.head_base_red(state, "missing"))

    def test_urgent_ones_are_a_batch_of_their_own(self):
        entries = self.entries(5, 6, 3, 4, 7)
        none = lambda t: None
        self.assertEqual(m.batch_size(entries, None, none, (), {5, 6}),
                         (2, None))
        self.assertEqual(m.batch_size(entries, None, none, (), {4}),
                         (3, None))
        self.assertEqual(m.batch_size(entries, None, none), (4, None))

    def test_a_later_batch_stops_at_an_urgent_one(self):
        m.configure(project(test_ahead={"batch": 4, "batches": 3}))
        entries = self.entries(3, 4, 5, 6, 7, 8)
        got = m.later_batches(entries, 2, None, lambda t: None,
                              lambda sha, tree: False, {6})
        self.assertEqual([b["size"] for b in got], [1, 1])


SHARED = "".join("line %d\n" % k for k in range(1, 21))


class LeftTopCovers(TrainSetup):
    """merge.test_ahead.left_top_covers (agent-forge #296): when a member
    leaves the first train mid-run, the members below it merge on the
    old top's green instead of waiting for the new top's run."""

    def setUp(self):
        super().setUp()
        self.last = dict(self.last, trains=True)
        # #6 was pushed mid-train: the new first train is #5 and #6's
        # new head, with another top.
        self.moved = dict(self.entries[1], head="f" * 40, sha="e" * 40,
                          tree="d" * 40)
        self.new = [self.entries[0], self.moved]
        self.started = {"results": {}, "ahead_started": {
            self.entries[1]["sha"]: "2026-11-12T21:40:00Z"}}

    def test_the_members_below_a_leaver_are_kept_with_the_old_top(self):
        got = m.left_top(self.last, self.new, [2], self.tip, self.started)
        self.assertEqual(got["tree"], self.entries[1]["tree"])
        self.assertEqual([e["pr"] for e in got["entries"]], [5])

    def test_only_a_started_top_that_is_not_red_covers(self):
        self.assertIsNone(m.left_top(self.last, self.new, [2], self.tip,
                                     {"results": {}}))
        red = dict(self.started, results={"r": {
            "ok": False, "tree": self.entries[1]["tree"]}})
        self.assertIsNone(m.left_top(self.last, self.new, [2], self.tip, red))
        self.assertIsNone(m.left_top(self.last, self.new, [2], "c" * 40,
                                     self.started))

    def test_a_kept_one_lasts_while_its_members_start_the_first_train(self):
        kept = m.left_top(self.last, self.new, [2], self.tip, self.started)
        later = dict(self.last, entries=self.new, left_top=kept,
                     batch={"size": 2, "trees": [e["tree"]
                                                 for e in self.new]})
        self.assertEqual(m.left_top(later, self.new, [2], self.tip,
                                    self.started), kept)
        gone = [dict(self.entries[0], head="a" * 40), self.moved]
        self.assertIsNone(m.left_top(later, gone, [2], self.tip,
                                     self.started))

    def test_nothing_is_kept_when_no_member_is_below_the_leaver(self):
        first = [dict(self.entries[0], head="a" * 40, tree="b" * 40),
                 self.moved]
        self.assertIsNone(m.left_top(self.last, first, [2], self.tip,
                                     self.started))

    def test_switched_off_nothing_is_kept_or_merged(self):
        with mock.patch.object(m, "LEFT_TOP_COVERS", False):
            self.assertIsNone(m.left_top(self.last, self.new, [2], self.tip,
                                         self.started))
            last = dict(self.last, left_top={
                "tree": self.entries[1]["tree"], "entries": self.new[:1]})
            self.assertIsNone(m.left_ready(last, self.queue, self.tip,
                                           self.check))

    def test_the_members_merge_once_the_old_top_is_green(self):
        last = dict(self.last, left_top=m.left_top(
            self.last, self.new, [2], self.tip, self.started))
        got = m.left_ready(last, self.queue, self.tip, self.check)
        self.assertEqual([e["pr"] for e in got], [5])
        self.assertIsNone(m.left_ready(last, self.queue, self.tip,
                                       self.started))
        self.assertIsNone(m.left_ready(last, self.queue, "c" * 40,
                                       self.check))
        pushed = [dict(self.queue[0], headRefOid="a" * 40), self.queue[1]]
        self.assertIsNone(m.left_ready(last, pushed, self.tip, self.check))

    def test_the_train_below_merges_with_the_old_tops_green(self):
        self.github = FakeGitHub({SLUG + "/pulls/5": {"merged": True}})
        got = m.train_merge(self.repo.path, "/nonexistent", self.entries[:1],
                            self.queue, self.tip, self.check, self.github,
                            self.git, lambda s: None, self.lines.append,
                            tested_tree=self.entries[1]["tree"])
        self.assertEqual(got, ([5], []))
        status = self.github.writes[0][2]
        self.assertIn("under a top tested green as r", status["description"])


class TrainInTheWay(TrainSetup):
    """A pull request outside the first train doesn't merge on its own
    under that train's started run (2026-11-12: #10784 did, 25 minutes
    into a train of eight, and every train was cut again)."""

    def setUp(self):
        super().setUp()
        self.last = dict(self.last, trains=True,
                         batch={"size": 1, "trees": [
                             self.entries[0]["tree"]]},
                         later=[{"size": 1, "trees": [
                             self.entries[1]["tree"]]}])
        self.started = {"results": {}, "ahead_started": {
            self.entries[0]["sha"]: "2026-11-12T16:40:00Z"}}

    def test_one_outside_the_started_first_train_waits(self):
        got = m.train_in_the_way(self.last, self.queue[1], self.started)
        self.assertEqual([e["pr"] for e in got], [5])

    def test_a_member_of_the_first_train_is_not_in_its_way(self):
        self.assertIsNone(m.train_in_the_way(self.last, self.queue[0],
                                             self.started))

    def test_a_train_not_started_or_red_is_no_reason_to_wait(self):
        self.assertIsNone(m.train_in_the_way(self.last, self.queue[1],
                                             {"results": {}}))
        red = dict(self.started, results={"r": {
            "ok": False, "tree": self.entries[0]["tree"]}})
        self.assertIsNone(m.train_in_the_way(self.last, self.queue[1], red))
        self.assertIsNone(m.train_in_the_way({}, self.queue[1],
                                             self.started))


class KeepTrains(TrainSetup):
    """labels.keep_trains (amendment of 2026-11-12, tenth): the default
    branch moving only by labelled merges keeps the trains on their
    base, and a green train merges its chain with the default branch
    merged on top."""

    def setUp(self):
        super().setUp()
        self.state = tempfile.mkdtemp(dir=self.tmp.name)
        patch = mock.patch.object(m, "KEEP_LABEL", "Merge:keep-trains")
        patch.start()
        self.addCleanup(patch.stop)
        self.last = dict(self.last, trains=True,
                         batch={"size": 1, "trees": [
                             self.entries[0]["tree"]]},
                         later=[{"size": 1, "trees": [
                             self.entries[1]["tree"]]}])
        self.labelled = {}

    def move_main(self, name="pin.txt", text="pin\n"):
        self.advance_main(name, text)
        return self.repo.main_tip()

    def carried(self, last=None, tip=None):
        with mock.patch.object(m, "kept_by_label",
                               lambda sha, github, cache:
                               self.labelled.get(sha)):
            return m.carried_base(self.repo.path, self.state,
                                  last or self.last, tip, None,
                                  out=self.lines.append)

    def test_a_labelled_merge_keeps_the_trains_on_their_base(self):
        tip = self.move_main()
        self.labelled[tip] = 11009
        self.assertEqual(self.carried(tip=tip), self.tip)

    def test_any_other_commit_cuts_them_again(self):
        tip = self.move_main()
        self.assertEqual(self.carried(tip=tip), tip)
        with mock.patch.object(m, "KEEP_LABEL", None):
            self.labelled[tip] = 11009
            self.assertEqual(self.carried(tip=tip), tip)

    def test_a_kept_commit_that_conflicts_cuts_them_again(self):
        tip = self.move_main("g.txt", "not the other\n")
        self.labelled[tip] = 11009
        self.assertEqual(self.carried(tip=tip), tip)
        self.assertIn("TRAIN-KEEP", self.lines[-1])

    def test_only_what_moved_main_since_is_asked(self):
        first = self.move_main()
        self.labelled[first] = 11009
        tip = self.move_main("pin2.txt", "pin 2\n")
        self.labelled[tip] = 11010
        last = dict(self.last, main=first)
        self.labelled.pop(first)
        self.assertEqual(self.carried(last, tip), self.tip)

    def test_a_kept_train_merges_with_main_on_top(self):
        tip = self.move_main()
        self.entries = self.entries[:1]
        self.github = FakeGitHub({SLUG + "/pulls/5": {"merged": True}})
        got = m.train_merge(self.repo.path, self.state, self.entries,
                            self.queue, self.tip, {"results": {"r": {
                                "ok": True,
                                "tree": self.entries[0]["tree"]}}},
                            self.github, self.git, lambda s: None,
                            self.lines.append, main=tip)
        self.assertEqual(got, ([5], []))
        top = self.github.writes[0][1].rsplit("/", 1)[1]
        run_git(self.repo.path, "fetch", "-q", "origin", "forge/train-5-5")
        self.assertEqual(run_git(self.repo.path, "rev-parse", top + "^2"),
                         tip)
        files = run_git(self.repo.path, "ls-tree", "--name-only", top)
        self.assertIn("pin.txt", files.split())
        self.assertIn("f.txt", files.split())
        self.assertIn("kept commits merged on top",
                      self.github.writes[0][2]["description"])
        # The trains behind it stay on its chain once it has merged.
        merge = run_git(self.repo.path, "commit-tree", top + "^{tree}",
                        "-p", tip, "-p", top, "-m", "Merge train")
        last = dict(self.last, entries=self.last["entries"], main=tip)
        self.assertEqual(self.carried(last, merge),
                         self.last["entries"][0]["sha"])

    def test_a_train_that_conflicts_with_main_goes_on_its_own(self):
        tip = self.move_main("g.txt", "not the other\n")
        self.github = FakeGitHub({})
        got = m.train_merge(self.repo.path, self.state, self.entries,
                            self.queue, self.tip, self.check, self.github,
                            self.git, lambda s: None, self.lines.append,
                            main=tip)
        self.assertEqual(got, ([], [5, 6]))
        self.assertIn("doesn't merge cleanly", self.lines[-1])
        self.assertEqual(self.github.writes, [])

    def test_the_label_is_read_from_the_commits_pull_request(self):
        sha = "a" * 40
        github = mock.Mock()
        github.api.return_value = {"data": {"repository": {"object": {
            "associatedPullRequests": {"nodes": [
                {"number": 3, "merged": True, "mergeCommit": {"oid": "b" * 40},
                 "labels": {"nodes": [{"name": "Merge:keep-trains"}]}},
                {"number": 4, "merged": True, "mergeCommit": {"oid": sha},
                 "labels": {"nodes": [{"name": "Merge:keep-trains"}]}}]}}}}}
        cache = {}
        self.assertEqual(m.kept_by_label(sha, github, cache), 4)
        self.assertEqual(cache, {sha: 4})
        github.api.return_value["data"]["repository"]["object"][
            "associatedPullRequests"]["nodes"][1]["labels"]["nodes"] = []
        self.assertIsNone(m.kept_by_label("c" * 40, github, {}))
        github.api.side_effect = RuntimeError("down")
        cache = {}
        self.assertIsNone(m.kept_by_label("d" * 40, github, cache))
        self.assertEqual(cache, {})


class Trains(RepoCase):
    """designs/0047: trains of carriages that touch different files, cut
    once and kept."""

    item = Decide.item
    tick = Tick.tick

    def setUp(self):
        super().setUp()
        self.advance_main("shared.txt", SHARED)
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        self.configure()
        self.addCleanup(m.configure, project())

    def configure(self, **ahead):
        m.configure(urgent_project(test_ahead=dict(
            {"batch": 3, "batches": 2, "depth": 5, "trains": "live"},
            **ahead)))

    def change(self, number, name, text, created=T1, start="origin/main"):
        """An approved pull request on a branch of its own from `start`
        that writes `text` to `name`."""
        self.repo.branch("b%d" % number, start)
        sha = self.repo.commit(name, text, "#%d" % number)
        self.repo.publish_pr(number, sha)
        self.repo.branch("feature", "feature")
        got = pr(number, created, head=sha,
                 reviews=[review(REVIEWER, oid=sha)])
        got["headRefName"] = "b%d" % number
        return got

    def line(self, number, k, created=T1):
        """One that changes line `k` of shared.txt only."""
        text = SHARED.replace("line %d\n" % k, "line %d by #%d\n"
                              % (k, number))
        return self.change(number, "shared.txt", text, created)

    def fixes(self, number):
        """#`number` fixes a red default branch."""
        patch = mock.patch.object(m, "MAIN_RED_FIXES", frozenset({number}))
        patch.start()
        self.addCleanup(patch.stop)

    def cut(self, carriages, last=None, open_numbers=None, check=None,
            started=None, acting=None):
        heads = {p["number"]: p["headRefOid"]
                 for c in carriages for p in c}
        return m.cut_trains(self.repo.path, self.repo.main_tip(), carriages,
                            last, heads, open_numbers or set(heads),
                            red=check or frozenset(), started=started,
                            acting=acting)

    def said(self, entries, sizes):
        """ahead.json as a tick writes it for these trains."""
        _, _, later = m.train_batches(entries, sizes, lambda t: None)
        return {"base": self.repo.main_tip(), "trains": True,
                "entries": entries, "batch": {"size": sizes[0], "trees": [
                    e["tree"] for e in entries[:sizes[0]]]}, "later": later}

    def numbers(self, entries, sizes):
        return m.train_lists(entries, sizes)

    def test_carriages_share_no_file_and_an_overlap_waits_its_turn(self):
        six, seven = self.line(6, 1), self.line(7, 20)
        eight = self.change(8, "x.txt", "x\n")
        entries, sizes, events = self.cut(
            [[self.item()], [six], [seven], [eight]])
        # #7 shares shared.txt with #6: it waits, and is first in the
        # next train.
        self.assertEqual(self.numbers(entries, sizes), [[5, 6, 8], [7]])
        self.assertEqual([e["carriage"] for e in entries], [5, 6, 8, 7])
        self.assertEqual([e["train"] for e in entries], [0, 0, 0, 1])
        self.assertIn(("cut", "CUT 2: #7"), events)
        # Each prediction stands on the one before, as GitHub would
        # merge them.
        self.assertEqual(m.git(self.repo.path, "rev-parse",
                               entries[3]["sha"] + "^").stdout.strip(),
                         entries[2]["sha"])

    def test_urgent_ones_share_a_train_when_they_merge_cleanly(self):
        # agent-forge #298: two urgent configuration changes to one file
        # board together, not one train each; the train then fills up
        # with what follows (designs/0047, amendment of 2026-11-11).
        six = labelled(self.line(6, 1), "Priority:urgent")
        seven = labelled(self.line(7, 20), "Priority:urgent")
        eight = self.change(8, "x.txt", "x\n")
        entries, sizes, _ = self.cut([[six], [seven], [eight]])
        self.assertEqual(self.numbers(entries, sizes), [[6, 7, 8]])

    def test_urgent_ones_that_conflict_still_go_apart(self):
        six = labelled(self.line(6, 1), "Priority:urgent")
        seven = labelled(self.change(7, "shared.txt", "other\n"),
                         "Priority:urgent")
        entries, sizes, _ = self.cut([[six], [seven]])
        # #7 doesn't merge onto #6, so it doesn't board with it (nor
        # after it, as any conflict behind a train).
        self.assertEqual(self.numbers(entries, sizes), [[6]])

    def test_within_clean_takes_an_overlap_that_merges_cleanly(self):
        self.configure(trains_within="clean")
        six, seven = self.line(6, 1), self.line(7, 20)
        eight = self.change(8, "x.txt", "x\n")
        entries, sizes, _ = self.cut([[self.item()], [six], [seven],
                                      [eight]])
        self.assertEqual(self.numbers(entries, sizes), [[5, 6, 7], [8]])

    def test_a_resolvable_conflict_is_resolved_behind_the_train(self):
        # Clean ones overtook such a pull request for as long as there
        # were any; it now gets a head that boards at the next cut.
        data = urgent_project(
            resolve={"union": ["*.md"]},
            test_ahead={"batch": 3, "batches": 2, "depth": 5,
                        "trains": "live"})
        m.configure(data)
        m.RESOLVE_IDENTITY = IDENTITY
        self.addCleanup(setattr, m, "RESOLVE_IDENTITY", None)
        self.advance_main("base.md", "* base\n")
        six = self.change(6, "x.txt", "x\n")
        seven = self.change(7, "base.md", "* base\n* seven\n")
        run_git(self.repo.path, "push", "-q", "-f", "origin",
                seven["headRefOid"] + ":refs/heads/b7")
        self.advance_main("base.md", "* base\n* main\n")
        record, lines = self.tick([self.item(), six, seven],
                                  conclusions=(None,), live=True,
                                  run=Resolve.git_run(self))
        got = [d for d in record["decisions"] if d.get("early")]
        self.assertEqual([(d["pr"], d["action"], d["result"]) for d in got],
                         [(7, "UNION", "done")], lines)

    def test_one_that_conflicts_with_the_train_ahead_waits(self):
        # designs/0047 §6: not handed back for a conflict with the
        # putative main.
        self.configure(batch=1, batches=2, depth=3)
        six = self.line(6, 1)
        clash = SHARED.replace("line 1\n", "line 1 by #7\n")
        seven = self.change(7, "shared.txt", clash)
        eight = self.change(8, "x.txt", "x\n")
        entries, sizes, _ = self.cut([[six], [seven], [eight]])
        self.assertEqual(self.numbers(entries, sizes), [[6], [8]])

    def test_a_red_only_for_an_old_conflict_boards(self):
        # 2026-11-12: four heads the build check had found conflicting
        # with an older tip of main merged cleanly onto the train's top,
        # but their red kept them out of every train, and only one train
        # was cut for an hour.
        six = self.change(6, "x.txt", "x\n")
        seven = self.change(7, "y.txt", "y\n")
        conflicted = {"ok": False, "failed": ["merge"], "conflicts": ["a"],
                      "base": "0" * 40}
        broken = {"ok": False, "failed": ["test"], "base": "0" * 40}
        check = {"results": {six["headRefOid"]: conflicted,
                             seven["headRefOid"]: broken}}
        self.assertFalse(m.head_red(check, six))
        self.assertTrue(m.head_red(check, seven))
        record, _ = self.tick([self.item(), six, seven], conclusions=(None,),
                              live=True, check_state=check)
        self.assertEqual(record["trains"], [[5, 6]])
        self.assertEqual(record["train_waits"],
                         {"7": "its own head is red"})

    def test_why_each_left_out_waits(self):
        self.configure(batch=3, batches=1)
        six, seven = self.line(6, 1), self.line(7, 20)
        clash = self.change(8, "shared.txt", "other\n")
        nine = self.change(9, "z.txt", "z\n")
        carriages = [[self.item()], [six], [clash], [seven], [nine]]
        heads = {c[0]["number"]: c[0]["headRefOid"] for c in carriages}
        note = {}
        entries, sizes, _ = m.cut_trains(
            self.repo.path, self.repo.main_tip(), carriages, None, heads,
            set(heads), note=note)
        self.assertEqual(self.numbers(entries, sizes), [[5, 6, 9]])
        self.assertEqual(note["waits"], {
            "7": "touches a file train 1 touches",
            "8": "touches a file train 1 touches"})
        self.configure(batch=3, batches=1, trains_within="clean")
        entries, sizes, _ = m.cut_trains(
            self.repo.path, self.repo.main_tip(), carriages, None, heads,
            set(heads), note=note)
        self.assertEqual(self.numbers(entries, sizes), [[5, 6, 7]])
        self.assertEqual(note["waits"], {"8": "conflicts onto train 1",
                                         "9": "every train is full"})

    def test_a_stack_is_one_carriage_and_never_split(self):
        six = self.change(6, "s.txt", "one\n")
        layer = self.change(9, "s2.txt", "two\n", start=six["headRefOid"])
        seven = self.change(7, "y.txt", "y\n")
        eight = self.change(8, "z.txt", "z\n")
        self.configure(batch=2, batches=2, depth=3)
        # The biggest carriage, [#6, #9], has the first train, whole;
        # #7 and #8 the next.
        entries, sizes, _ = self.cut([[seven], [six, layer], [eight]])
        self.assertEqual(self.numbers(entries, sizes), [[6, 9], [7, 8]])
        self.assertEqual([e["carriage"] for e in entries], [6, 6, 7, 8])

    def test_a_layer_that_conflicts_stays_and_the_one_below_boards(self):
        six = self.change(6, "s.txt", "one\n")
        # Written from main, so it conflicts with #6 in s.txt, and #10
        # above it can't board either.
        layer = self.change(9, "s.txt", "other\n")
        top = self.change(10, "t.txt", "top\n", start=layer["headRefOid"])
        seven = self.change(7, "y.txt", "y\n")
        self.configure(batch=3, batches=2, depth=5)
        entries, sizes, events = self.cut([[six, layer, top], [seven]])
        self.assertEqual(self.numbers(entries, sizes), [[6, 7]])
        self.assertEqual([e["carriage"] for e in entries], [6, 7])
        self.assertIn(("left", "LEFT #9: won't board with the layers below "
                       "it, which go without it; also held back: #10"), events)

    def test_a_red_layer_stays_and_the_one_below_boards(self):
        six = self.change(6, "s.txt", "one\n")
        layer = self.change(9, "s2.txt", "two\n", start=six["headRefOid"])
        self.configure(batch=3, batches=2, depth=5)
        entries, sizes, _ = self.cut([[six, layer]], check={9})
        self.assertEqual(self.numbers(entries, sizes), [[6]])

    def test_a_carriage_whose_first_is_red_stays_whole(self):
        six = self.change(6, "s.txt", "one\n")
        layer = self.change(9, "s2.txt", "two\n", start=six["headRefOid"])
        seven = self.change(7, "y.txt", "y\n")
        self.configure(batch=3, batches=2, depth=5)
        entries, sizes, _ = self.cut([[six, layer], [seven]], check={6})
        self.assertEqual(self.numbers(entries, sizes), [[7]])

    def test_a_stack_longer_than_batch_is_a_train_of_its_own(self):
        six = self.change(6, "s.txt", "one\n")
        layer = self.change(9, "s2.txt", "two\n", start=six["headRefOid"])
        top = self.change(10, "s3.txt", "three\n",
                          start=layer["headRefOid"])
        seven = self.change(7, "y.txt", "y\n")
        self.configure(batch=2, batches=2, depth=3)
        entries, sizes, _ = self.cut([[six, layer, top], [seven]])
        self.assertEqual(self.numbers(entries, sizes), [[6, 9, 10], [7]])

    def test_a_cut_train_is_fixed_and_a_newcomer_waits(self):
        six = self.change(6, "y.txt", "y\n")
        entries, sizes, _ = self.cut([[self.item()], [six]])
        self.assertEqual(self.numbers(entries, sizes), [[5, 6]])
        # #4, older and disjoint, comes in front in the queue: it goes
        # into the next train, not into the cut one.
        four = self.change(4, "w.txt", "w\n", created=T0)
        got, sizes, events = self.cut([[four], [self.item()], [six]],
                                      self.said(entries, sizes))
        self.assertEqual(self.numbers(got, sizes), [[5, 6], [4]])
        self.assertEqual([c for c, _ in events], ["cut"])
        self.assertEqual(got[:2], entries)

    def test_a_push_takes_out_only_that_member(self):
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        entries, sizes, _ = self.cut([[self.item()], [six], [seven]])
        self.assertEqual(self.numbers(entries, sizes), [[5, 6, 7]])
        # #6 is pushed to: its new head is no longer approved, so it has
        # left the queue. The train keeps #5 and #7.
        last = self.said(entries, sizes)
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(), [[self.item()], [seven]],
            last, {5: self.head, 6: "c" * 40, 7: seven["headRefOid"]},
            {5, 6, 7})
        self.assertEqual(self.numbers(got, sizes), [[5, 7]])
        self.assertEqual(events, [("left", "LEFT #6: pushed cccccccccc "
                                   "mid-train")])
        # #5's tree is unchanged; only the top's tree is new.
        self.assertEqual(got[0], entries[0])
        self.assertNotEqual(got[1]["tree"], entries[2]["tree"])

    def test_a_layer_above_a_leaver_leaves_with_it(self):
        six = self.change(6, "s.txt", "one\n")
        layer = self.change(9, "s2.txt", "two\n", start=six["headRefOid"])
        seven = self.change(7, "y.txt", "y\n")
        self.configure(batch=4, batches=1)
        entries, sizes, _ = self.cut([[self.item()], [six, layer],
                                      [seven]])
        self.assertEqual(self.numbers(entries, sizes), [[6, 9, 5, 7]])
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[self.item()], [seven]], self.said(entries, sizes),
            {5: self.head, 6: six["headRefOid"], 9: layer["headRefOid"],
             7: seven["headRefOid"]}, {5, 6, 7, 9})
        self.assertEqual(self.numbers(got, sizes), [[5, 7]])
        self.assertEqual(events, [("left", "LEFT #6: left the queue; its "
                                   "stack's layers #9 leave with it")])

    def sibling(self, number, base, name, text="x\n", reviews=True):
        """A layer on #`base`'s branch writing `text` to `name`."""
        got = self.change(number, name, text, start=base["headRefOid"])
        got["baseRefName"] = base["headRefName"]
        if not reviews:
            got["reviews"] = []
        return got

    def siblings(self):
        """#6 on main; #9 and #10 side by side on it, #11 on #9."""
        six = self.change(6, "s.txt", "one\n")
        nine = self.sibling(9, six, "s9.txt")
        ten = self.sibling(10, six, "s10.txt")
        eleven = self.sibling(11, nine, "s11.txt")
        return six, nine, ten, eleven

    def test_layers_side_by_side_are_ready_depth_first(self):
        # Amendment of 2026-11-12: three layers on one base, all
        # approved and green, and none boarded.
        six, nine, ten, eleven = self.siblings()
        twelve = self.sibling(12, six, "s12.txt", reviews=False)
        thirteen = self.sibling(13, twelve, "s13.txt")
        prs = [six, nine, ten, eleven, twelve, thirteen]
        got = m.ready_layers(self.repo.path, six, prs,
                             {p["number"] for p in prs},
                             datetime.datetime.now(datetime.timezone.utc),
                             {})
        # #12 isn't approved: it and #13 on it stay out, the rest board.
        self.assertEqual([p["number"] for p, _ in got], [9, 11, 10])

    def test_a_carriage_that_is_a_tree_boards_whole(self):
        six, nine, ten, eleven = self.siblings()
        self.configure(batch=4, batches=1, depth=3)
        entries, sizes, _ = self.cut([[six, nine, eleven, ten]])
        self.assertEqual(self.numbers(entries, sizes), [[6, 9, 11, 10]])
        self.assertEqual([e["on"] for e in entries], [None, 6, 9, 6])
        # Each is predicted on all before it: #10's holds #9 and #11.
        files = m.git(self.repo.path, "ls-tree", "--name-only",
                      entries[3]["tree"]).stdout.split()
        for name in ("s.txt", "s9.txt", "s10.txt", "s11.txt"):
            self.assertIn(name, files)

    def test_a_red_layer_holds_back_only_its_own_branch(self):
        six, nine, ten, eleven = self.siblings()
        self.configure(batch=4, batches=1, depth=3)
        entries, sizes, events = self.cut([[six, nine, eleven, ten]],
                                          check={9})
        self.assertEqual(self.numbers(entries, sizes), [[6, 10]])

    def test_a_sibling_that_conflicts_stops_only_its_own_branch(self):
        six = self.change(6, "s.txt", "one\n")
        nine = self.sibling(9, six, "c.txt", "nine\n")
        ten = self.sibling(10, six, "c.txt", "ten\n")
        fourteen = self.sibling(14, ten, "s14.txt")
        eleven = self.sibling(11, nine, "s11.txt")
        self.configure(batch=5, batches=1, depth=4)
        entries, sizes, events = self.cut(
            [[six, nine, eleven, ten, fourteen]])
        self.assertEqual(self.numbers(entries, sizes), [[6, 9, 11]])
        self.assertIn(("left", "LEFT #10: won't board with the layers below "
                       "it, which go without it; also held back: #14"),
                      events)

    def test_a_layer_recut_after_its_base_merged_keeps_its_mark(self):
        # layers_need_own_green: the base #6 merged, and #9 heads its
        # carriage in a train cut afresh; it boarded as a layer.
        six, nine, ten, eleven = self.siblings()
        self.configure(batch=4, batches=1, depth=3)
        last = {"entries": [{"pr": 9, "head": nine["headRefOid"], "on": 6}]}
        entries, sizes, _ = self.cut([[nine, eleven]], last=dict(
            last, base="0" * 40))
        self.assertEqual([(e["pr"], e["on"]) for e in entries],
                         [(9, 6), (11, 9)])

    def test_one_that_leaves_takes_only_the_layers_on_it(self):
        six, nine, ten, eleven = self.siblings()
        self.configure(batch=4, batches=1, depth=3)
        entries, sizes, _ = self.cut([[six, nine, eleven, ten]])
        heads = {p["number"]: p["headRefOid"]
                 for p in (six, nine, ten, eleven)}
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(), [[six, ten]],
            self.said(entries, sizes), heads, set(heads))
        self.assertEqual(events, [("left", "LEFT #9: left the queue; its "
                                   "stack's layers #11 leave with it")])
        self.assertEqual(self.numbers(got, sizes), [[6, 10]])

    def test_a_merged_member_just_goes(self):
        six = self.change(6, "y.txt", "y\n")
        entries, sizes, _ = self.cut([[self.item()], [six]])
        # #5 merged as predicted: main's tree is its prediction's.
        self.repo.branch("main")
        run_git(self.repo.path, "merge", "-q", "--ff-only", self.head)
        self.repo.push("main")
        self.repo.branch("feature", "feature")
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(), [[six]],
            self.said(entries, sizes), {6: six["headRefOid"]}, {6})
        self.assertEqual(self.numbers(got, sizes), [[6]])
        self.assertEqual(events, [])
        self.assertEqual(got[0]["tree"], entries[1]["tree"])

    def test_a_force_merge_cuts_every_train_again(self):
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        self.configure(batch=1, batches=3)
        entries, sizes, _ = self.cut([[self.item()], [six], [seven]])
        last = self.said(entries, sizes)
        self.advance_main("forced.txt", "by hand\n")
        got, sizes, events = self.cut([[seven], [self.item()], [six]],
                                      last)
        self.assertEqual(events[0][0], "main moved")
        self.assertEqual(self.numbers(got, sizes), [[7], [5], [6]])

    def test_a_kept_commit_keeps_every_train_and_its_trees(self):
        # labels.keep_trains: the tick cuts on the base `carried_base`
        # gives, the trains' own, though main has moved.
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        self.configure(batch=1, batches=3)
        entries, sizes, _ = self.cut([[self.item()], [six], [seven]])
        last = self.said(entries, sizes)
        base = self.repo.main_tip()
        self.advance_main("pin.txt", "pin\n")
        tip = self.repo.main_tip()
        with mock.patch.object(m, "KEEP_LABEL", "Merge:keep-trains"), \
                mock.patch.object(m, "kept_by_label",
                                  lambda sha, github, cache: 11009):
            carried = m.carried_base(self.repo.path, self.tmp.name, last,
                                     tip, None)
        self.assertEqual(carried, base)
        heads = {p["number"]: p["headRefOid"]
                 for p in (self.item(), six, seven)}
        got, sizes, events = m.cut_trains(
            self.repo.path, carried, [[self.item()], [six], [seven]], last,
            heads, set(heads), red=frozenset())
        self.assertEqual(events, [])
        self.assertEqual([e["tree"] for e in got],
                         [e["tree"] for e in entries])

    def test_a_red_mains_fix_cuts_the_trains_behind_the_first(self):
        # Only a red default branch's fix cuts trains already cut; one
        # labelled urgent heads the next train (2026-11-13).
        self.fixes(8)
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        self.configure(batch=1, batches=3)
        entries, sizes, _ = self.cut([[self.item()], [six], [seven]])
        hot = labelled(self.change(8, "u.txt", "u\n"), "Priority:urgent")
        got, sizes, events = self.cut(
            [[self.item()], [six], [seven], [hot]],
            self.said(entries, sizes))
        self.assertEqual(events[0], ("urgent", "RECUT: #8 goes first; the "
                                     "trains after train 1 are cut "
                                     "again"))
        self.assertEqual(self.numbers(got, sizes), [[5], [8], [6]])

    def test_an_urgent_one_heads_the_next_train_and_cuts_none(self):
        # 2026-11-13: an urgent pin went in front of a train whose top
        # was testing and every train was cut again. One labelled
        # urgent heads the next train cut, behind those already cut,
        # whether or not they have started.
        self.configure(batch=2, batches=3)
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        entries, sizes, _ = self.cut([[self.item()], [six], [seven]])
        self.assertEqual(self.numbers(entries, sizes), [[5, 6], [7]])
        hot = labelled(self.change(9, "u.txt", "u\n"), "Priority:urgent")
        ten = self.change(10, "w.txt", "w\n")
        for queue in ([[hot], [self.item()], [six], [seven], [ten]],
                      [[self.item()], [six], [seven], [ten], [hot]]):
            got, sizes2, events = self.cut(
                queue, self.said(entries, sizes),
                started=lambda sha, tree: False)
            self.assertEqual(events, [("cut", "CUT 3: #9 #10")])
            self.assertEqual(self.numbers(got, sizes2),
                             [[5, 6], [7], [9, 10]])

    def test_others_fill_an_urgent_ones_train(self):
        # designs/0047, amendment of 2026-11-11: an urgent one goes first
        # in its train, which then fills up like any other, rather than
        # leaving with the urgent one alone.
        hot = labelled(self.change(8, "u.txt", "u\n"), "Priority:urgent")
        six = self.change(6, "y.txt", "y\n")
        entries, sizes, _ = self.cut([[six], [hot]])
        self.assertEqual(self.numbers(entries, sizes), [[8, 6]])

    def test_the_one_the_merger_acts_on_stays_ahead_of_an_urgent_one(self):
        # 2026-11-11: the Merger waited on #5's own run while a nightly's
        # fix (#8) headed the train cut behind it. #5 then merged on its
        # own green, out of the train's order, and the green train no
        # longer predicted the default branch. #5 stays first.
        hot = labelled(self.change(8, "u.txt", "u\n"), "Priority:urgent")
        six = self.change(6, "y.txt", "y\n")
        entries, sizes, _ = self.cut([[self.item()], [six], [hot]],
                                     acting=5)
        self.assertEqual(self.numbers(entries, sizes), [[5, 8, 6]])
        # Without one in flight, the urgent one leads as before.
        entries, sizes, _ = self.cut([[self.item()], [six], [hot]])
        self.assertEqual(self.numbers(entries, sizes), [[8, 5, 6]])

    def test_a_red_mains_fix_heads_the_first_train_not_started(self):
        # Only a red default branch's fix cuts trains already cut; one
        # labelled urgent heads the next train (2026-11-13).
        self.fixes(9)
        # Train 2 hasn't started: #9 heads it again, and it fills up
        # with the queue's next ones, not a train of its own.
        self.configure(batch=2, batches=3)
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        ten = self.change(10, "w.txt", "w\n")
        entries, sizes, _ = self.cut([[self.item()], [six], [seven],
                                      [ten]])
        self.assertEqual(self.numbers(entries, sizes), [[5, 6], [7, 10]])
        hot = labelled(self.change(9, "u.txt", "u\n"), "Priority:urgent")
        got, sizes, events = self.cut(
            [[self.item()], [six], [seven], [ten], [hot]],
            self.said(entries, sizes), started=lambda sha, tree: False)
        self.assertEqual(events[0], ("urgent", "RECUT: #9 goes first; the "
                                     "trains after train 1 are cut "
                                     "again"))
        self.assertEqual(self.numbers(got, sizes), [[5, 6], [9, 7], [10]])

    def test_a_train_whose_top_started_is_not_cut_again(self):
        # Train 2's top is testing: an urgent one waits for train 3
        # rather than throwing that run away.
        self.configure(batch=2, batches=3)
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        ten = self.change(10, "w.txt", "w\n")
        entries, sizes, _ = self.cut([[self.item()], [six], [seven],
                                      [ten]])
        top = entries[3]["sha"]
        hot = labelled(self.change(9, "u.txt", "u\n"), "Priority:urgent")
        got, sizes, events = self.cut(
            [[self.item()], [six], [seven], [ten], [hot]],
            self.said(entries, sizes),
            started=lambda sha, tree: sha == top)
        self.assertEqual([c for c, _ in events if c == "urgent"], [])
        self.assertEqual(self.numbers(got, sizes), [[5, 6], [7, 10], [9]])

    def test_a_red_head_cannot_board(self):
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        state = {"results": {six["headRefOid"]: {"ok": False,
                                                 "tree": "t"}}}
        self.assertIs(m.head_red(state, six), True)
        self.assertIs(m.head_red(dict(state, main_red={"issue": 3}), six),
                      False)
        self.assertIs(m.head_red({"results": {six["headRefOid"]: {
            "ok": False, "covered": "batch"}}}, six), False)
        entries, sizes, _ = self.cut([[self.item()], [six], [seven]],
                                     check={6})
        self.assertEqual(self.numbers(entries, sizes), [[5, 7]])

    def test_a_red_train_ends_what_follows_it(self):
        entries = [{"pr": n, "tree": "t%d" % n} for n in (1, 2, 3, 4, 5)]
        red = lambda bad: (lambda tree: False if tree in bad else None)
        self.assertEqual(m.train_batches(entries, [2, 2, 1], red(())), (
            2, None, [{"size": 2, "trees": ["t3", "t4"], "fixed": True},
                      {"size": 1, "trees": ["t5"], "fixed": True}]))
        # A red second train: nothing after it.
        self.assertEqual(m.train_batches(entries, [2, 2, 1],
                                         red({"t4"}))[2],
                         [{"size": 2, "trees": ["t3", "t4"],
                           "fixed": True}])
        # A red first train: its members are the suspects, nothing
        # follows.
        self.assertEqual(m.train_batches(entries, [2, 2, 1], red({"t2"})),
                         (2, 2, []))

    def test_a_later_trains_green_top_folds_the_trains_in_front(self):
        # Amendment of 2026-11-11: its tree contains them all, so they
        # merge at once rather than waiting for their own tops.
        entries = [{"pr": n, "tree": "t%d" % n} for n in (1, 2, 3, 4, 5)]
        says = lambda good, bad=(): (
            lambda tree: True if tree in good else
            False if tree in bad else None)
        self.assertEqual(m.train_batches(entries, [2, 2, 1], says({"t4"})),
                         (4, None, [{"size": 1, "trees": ["t5"],
                                     "fixed": True}]))
        # The last train's green folds all three.
        self.assertEqual(m.train_batches(entries, [2, 2, 1], says({"t5"})),
                         (5, None, []))
        # A red top in front of a green one is overruled, as a flake:
        # no suspects.
        self.assertEqual(m.train_batches(entries, [2, 2, 1],
                                         says({"t4"}, {"t2"})),
                         (4, None, [{"size": 1, "trees": ["t5"],
                                     "fixed": True}]))
        # The first train's own green folds nothing.
        self.assertEqual(m.train_batches(entries, [2, 2, 1], says({"t2"})),
                         (2, None, [{"size": 2, "trees": ["t3", "t4"],
                                     "fixed": True},
                                    {"size": 1, "trees": ["t5"],
                                     "fixed": True}]))

    def test_trains_longer_than_the_entries_left_are_dropped(self):
        # A member handed back after the trains were cut: the sizes run
        # past the entries. That raised IndexError and ended the tick.
        entries = [{"pr": n, "tree": "t%d" % n} for n in (1, 2, 3, 4)]
        none = lambda tree: None
        self.assertEqual(m.train_batches(entries, [2, 3], none),
                         (2, None, []))
        self.assertEqual(m.train_batches(entries[:1], [2, 2], none),
                         (0, None, []))

    def ahead_json(self):
        return m.load_json(os.path.join(self.state, m.AHEAD_FILE), None)

    def test_a_live_tick_tells_who_boarded_once(self):
        # agent-forge #286: merge.test_ahead.tell_boarded.
        self.configure(tell_boarded=True)
        six, seven = self.line(6, 1), self.line(7, 20)
        prs = [self.item(), six, seven]
        record, lines = self.tick(prs, conclusions=(None,), live=True)
        told = [w for w in self.github.writes
                if w[1].endswith("/comments")
                and "In the Merger's train" in w[2].get("body", "")]
        self.assertEqual(sorted(w[1] for w in told), sorted(
            "repos/%s/issues/%d/comments" % (m.REPO_SLUG, n)
            for n in (5, 6, 7)))
        self.assertEqual(sorted(t["pr"] for t in record["told_boarded"]),
                         [5, 6, 7])
        self.assertIn("TOLD-BOARDED #6 %s" % six["headRefOid"][:10], lines)
        # What to do instead of pushing.
        self.assertIn("If it needs a fix, open a new pull request stacked "
                      "on this branch; don't push here.", told[0][2]["body"])
        # The next tick, the same trains: nobody is told again.
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=1)
        self.assertNotIn("told_boarded", record)
        self.assertFalse([l for l in lines if l.startswith("TOLD-BOARDED")])

    def test_off_by_default_nobody_is_told(self):
        six = self.line(6, 1)
        record, _ = self.tick([self.item(), six], conclusions=(None,),
                              live=True)
        self.assertNotIn("told_boarded", record)
        self.assertFalse([w for w in self.github.writes
                          if "In the Merger's train"
                          in w[2].get("body", "")])

    def test_a_live_tick_writes_trains_and_keeps_them(self):
        six, seven = self.line(6, 1), self.line(7, 20)
        prs = [self.item(), six, seven]
        record, lines = self.tick(prs, conclusions=(None,), live=True)
        self.assertEqual(record["trains"], [[5, 6], [7]])
        self.assertEqual((record["batch"], record["later"]), ([5, 6], [[7]]))
        ahead = self.ahead_json()
        self.assertIs(ahead["trains"], True)
        self.assertEqual(ahead["later"][0]["fixed"], True)
        self.assertIn("TRAIN-CUT 1: #5 #6", lines)
        self.assertIn("TRAIN-CUT 2: #7", lines)
        # The next tick changes nothing, and says nothing.
        record, lines = self.tick(prs, conclusions=(None,), live=True,
                                  minutes=1)
        self.assertEqual(record["trains"], [[5, 6], [7]])
        self.assertFalse([l for l in lines if l.startswith("TRAIN-")])
        self.assertNotIn("recut", record)

    def test_a_dry_run_keeps_the_batches_and_logs_the_trains(self):
        self.configure(trains="dry-run")
        six, seven = self.line(6, 1), self.line(7, 20)
        prs = [self.item(), six, seven]
        record, lines = self.tick(prs, conclusions=(None,), live=True)
        # The batch is the queue's first three, as without trains.
        self.assertEqual(record["batch"], [5, 6, 7])
        self.assertEqual(record["trains"], [[5, 6], [7]])
        self.assertIn("TRAIN-DRY 1: #5 #6 | 2: #7", lines)
        self.assertNotIn("trains", self.ahead_json())
        dry = m.load_json(os.path.join(self.state, m.TRAINS_DRY_FILE), {})
        self.assertEqual([e["pr"] for e in dry["entries"]], [5, 6, 7])
        _, lines = self.tick(prs, conclusions=(None,), live=True, minutes=1)
        self.assertNotIn("TRAIN-DRY 1: #5 #6 | 2: #7", lines)

    def test_on_by_default(self):
        # designs/0050: a project that says nothing about testing ahead
        # gets trains, and so does one whose test_ahead leaves trains out.
        data = example()
        data["merge"] = {"gate": GATE, "own_head_green": False}
        m.configure(config.Project(data, None))
        self.addCleanup(m.configure, project())
        self.assertEqual((m.TEST_AHEAD, m.AHEAD_DEPTH, m.AHEAD_BATCH,
                          m.AHEAD_BATCHES, m.TRAINS),
                         (True, 7, 4, 2, "live"))
        data["merge"]["test_ahead"] = {"batch": 3}
        m.configure(config.Project(data, None))
        self.assertEqual(m.TRAINS, "live")
        data["merge"]["test_ahead"] = False
        m.configure(config.Project(data, None))
        self.assertIs(m.TEST_AHEAD, False)

    def test_off_says_nothing_of_trains(self):
        m.configure(project(test_ahead={"batch": 3, "trains": "off"}))
        self.assertIsNone(m.TRAINS)
        six = self.line(6, 1)
        record, lines = self.tick([self.item(), six], conclusions=(None,),
                                  live=True)
        self.assertNotIn("trains", record)
        self.assertFalse(os.path.exists(os.path.join(
            self.state, m.TRAINS_DRY_FILE)))

    def test_a_push_mid_train_in_a_live_tick(self):
        self.configure(batch=3, batches=1)
        six, eight = self.change(6, "y.txt", "y\n"), self.change(
            8, "x.txt", "x\n")
        self.tick([self.item(), six, eight], conclusions=(None,), live=True)
        # #6 is pushed to; its approval is of the old head.
        self.repo.branch("b6", six["headRefOid"])
        pushed = self.repo.commit("y.txt", "y again\n", "#6 again")
        self.repo.publish_pr(6, pushed)
        self.repo.branch("feature", "feature")
        record, lines = self.tick(
            [self.item(), dict(six, headRefOid=pushed), eight],
            conclusions=(None,), live=True, minutes=1)
        self.assertEqual(record["trains"], [[5, 8]])
        self.assertIn("TRAIN-LEFT #6: pushed %s mid-train" % pushed[:10],
                      lines)
        self.assertEqual(record["train_events"], ["left"])
        self.assertIs(record["recut"], True)

    def test_a_red_mains_fix_that_cannot_board_cuts_only_once(self):
        # Only a red default branch's fix cuts trains already cut; one
        # labelled urgent heads the next train (2026-11-13).
        self.fixes(8)
        six, seven = self.change(6, "y.txt", "y\n"), self.change(
            7, "z.txt", "z\n")
        self.configure(batch=1, batches=3)
        entries, sizes, _ = self.cut([[self.item()], [six], [seven]])
        hot = labelled(self.change(8, "u.txt", "u\n"), "Priority:urgent")
        note = {}
        heads = {5: self.head, 6: six["headRefOid"], 7: seven["headRefOid"],
                 8: hot["headRefOid"]}
        # #8's head is red: it can't board, so it gets one cut only.
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[self.item()], [six], [seven], [hot]],
            self.said(entries, sizes), heads, set(heads), red={8},
            note=note)
        self.assertEqual([c for c, _ in events][:1], ["urgent"])
        self.assertEqual(note, {"urgent_cut": [8],
                                "waits": {"8": "its own head is red"}})
        last = dict(self.said(got, sizes), urgent_cut=[8])
        again, sizes2, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(),
            [[self.item()], [six], [seven], [hot]], last, heads, set(heads),
            red={8})
        self.assertEqual(events, [])
        self.assertEqual(again, got)

    def test_a_dry_run_keeps_its_trains_when_main_moves(self):
        six = self.change(6, "y.txt", "y\n")
        entries, sizes, _ = self.cut([[self.item()], [six]])
        last = self.said(entries, sizes)
        self.advance_main("other.txt", "merged by a batch\n")
        heads = {5: self.head, 6: six["headRefOid"]}
        got, sizes, events = m.cut_trains(
            self.repo.path, self.repo.main_tip(), [[six], [self.item()]],
            last, heads, set(heads), force_merges=False)
        self.assertEqual(self.numbers(got, sizes), [[5, 6]])
        self.assertEqual(events, [])

    def test_a_red_mains_fix_the_queue_puts_first_cuts_every_train(self):
        # Only a red default branch's fix cuts trains already cut; one
        # labelled urgent heads the next train (2026-11-13).
        self.fixes(8)
        six = self.change(6, "y.txt", "y\n")
        self.configure(batch=2, batches=2)
        entries, sizes, _ = self.cut([[self.item()], [six]])
        hot = labelled(self.change(8, "u.txt", "u\n"), "Priority:urgent")
        got, sizes, events = self.cut([[hot], [self.item()], [six]],
                                      self.said(entries, sizes))
        self.assertEqual(events[0][0], "urgent")
        # Every train is cut again, whatever has started; #8 goes first
        # and its train fills up.
        self.assertEqual(self.numbers(got, sizes), [[8, 5], [6]])


class ToldBoarded(unittest.TestCase):
    """agent-forge #286: a pull request that boards a live train is told
    once not to push, and again only after it leaves and reboards."""

    def setUp(self):
        self.state = tempfile.mkdtemp()
        self.github = FakeGitHub({})

    def tell(self, numbers, heads, live=True):
        return m.tell_boarded(self.github, self.state,
                              [{"pr": n} for n in numbers], heads, live)

    def test_told_once_while_it_rides(self):
        heads = {5: "a" * 40, 6: "b" * 40}
        told = self.tell([5, 6], heads)
        self.assertEqual([t["pr"] for t in told], [5, 6])
        self.assertEqual(len(self.github.writes), 2)
        method, path, fields = self.github.writes[0]
        self.assertEqual((method, path),
                         ("POST", "repos/%s/issues/5/comments" % m.REPO_SLUG))
        self.assertTrue(fields["body"].startswith("Not-a-review\n"))
        self.assertIn("train at aaaaaaaaaa", fields["body"])
        self.assertIn("handed back", fields["body"])
        # The Merger's own update gives #5 a new head: not told again.
        self.assertEqual(self.tell([5, 6], {5: "c" * 40, 6: "b" * 40}), [])
        self.assertEqual(len(self.github.writes), 2)

    def test_told_again_after_it_leaves_and_reboards(self):
        self.tell([5, 6], {5: "a" * 40, 6: "b" * 40})
        # #6 leaves the trains (a push, a hand-back): forgotten.
        self.tell([5], {5: "a" * 40})
        told = self.tell([5, 6], {5: "a" * 40, 6: "d" * 40})
        self.assertEqual([(t["pr"], t["head"]) for t in told],
                         [(6, "dddddddddd")])

    def test_a_dry_run_posts_and_keeps_nothing(self):
        told = self.tell([5], {5: "a" * 40}, live=False)
        self.assertEqual([t["pr"] for t in told], [5])
        self.assertEqual(self.github.writes, [])
        self.assertEqual(len(self.tell([5], {5: "a" * 40})), 1)

    def test_a_refused_comment_is_tried_again(self):
        self.github.refuse = "403"
        told = self.tell([5], {5: "a" * 40})
        self.assertEqual(told[0]["result"], "refused: 403")
        self.github.refuse = None
        self.assertEqual(self.tell([5], {5: "a" * 40})[0]["result"], "done")

    def test_a_layer_and_its_base_in_one_train_are_each_told_once(self):
        told = self.tell([5, 5, 9], {5: "a" * 40, 9: "e" * 40})
        self.assertEqual([t["pr"] for t in told], [5, 9])


class ReturnedHead(unittest.TestCase):
    """A hand-back for another head is forgotten once the head is back
    at the approved commit itself (agent-forge-next-bundle item 3)."""

    def setUp(self):
        m.configure(project())
        self.addCleanup(m.configure, project())

    def said(self, head):
        return {"5": {"approved": T1, "head": head,
                      "reason": "the approved commit is not in the head's "
                                "history"}}

    def test_back_at_the_approved_commit_clears_it(self):
        said, lines = self.said("b" * 40), []
        item = pr(5, head="a" * 40, reviews=[review(REVIEWER, oid="a" * 40)])
        self.assertEqual(m.clear_returned_heads(said, [item], lines.append),
                         [5])
        self.assertEqual(said, {})
        self.assertIn("HAND-BACK-CLEARED #5: the head is back at the "
                      "approved commit aaaaaaaaaa, not bbbbbbbbbb", lines[0])

    def test_a_hand_back_of_this_very_head_stands(self):
        said = self.said("a" * 40)
        item = pr(5, head="a" * 40, reviews=[review(REVIEWER, oid="a" * 40)])
        self.assertEqual(m.clear_returned_heads(said, [item], print), [])
        self.assertIn("5", said)

    def test_a_head_that_is_not_the_approved_commit_stands(self):
        said = self.said("b" * 40)
        item = pr(5, head="c" * 40, reviews=[review(REVIEWER, oid="a" * 40)])
        self.assertEqual(m.clear_returned_heads(said, [item], print), [])

    def test_one_said_under_another_approval_is_left_alone(self):
        said = self.said("b" * 40)
        item = pr(5, head="a" * 40,
                  reviews=[review(REVIEWER, at=T3, oid="a" * 40)])
        self.assertEqual(m.clear_returned_heads(said, [item], print), [])


class CloseMergedIssues(unittest.TestCase):
    """After a merge, the issues GitHub left open are closed by a later
    tick (agent-forge-next-bundle item 4)."""

    def setUp(self):
        m.configure(project())
        self.addCleanup(m.configure, project())
        self.state = tempfile.mkdtemp()
        self.addCleanup(lambda: subprocess.run(["rm", "-rf", self.state]))
        self.merged = datetime.datetime(2026, 11, 4, 19, 44,
                                        tzinfo=datetime.timezone.utc)
        m.note_closing(self.state, 8200, self.merged)

    def answer(self, *refs, merged=True):
        return {"graphql": {"data": {"repository": {"pullRequest": {
            "merged": merged, "mergeCommit": {"oid": "f" * 40},
            "closingIssuesReferences": {"nodes": list(refs)}}}}}}

    def ref(self, number, state="OPEN", reason=None, repo=None):
        return {"number": number, "state": state, "stateReason": reason,
                "repository": {"nameWithOwner": repo or m.REPO_SLUG}}

    def close(self, github, minutes=3):
        lines = []
        got = m.close_merged_issues(
            self.state, github,
            self.merged + datetime.timedelta(minutes=minutes), lines.append)
        return got, lines

    def test_an_open_issue_is_closed_with_a_comment(self):
        github = FakeGitHub(self.answer(self.ref(7981),
                                        self.ref(7982, "CLOSED")))
        got, lines = self.close(github)
        self.assertEqual(got, [{"pr": 8200, "issue": 7981,
                                "result": "done"}])
        self.assertEqual([(w[0], w[1]) for w in github.writes], [
            ("POST", "%s/issues/7981/comments" % SLUG),
            ("PATCH", "%s/issues/7981" % SLUG)])
        self.assertIn("#8200, which says it closes this issue, merged as "
                      "ffffffffff", github.writes[0][2]["body"])
        self.assertEqual(github.writes[1][2], {"state": "closed",
                                               "state_reason": "completed"})
        self.assertIn("CLOSED-ISSUE #7981: still open after #8200 merged",
                      lines[0])
        # Asked about once: the next tick asks nothing.
        github = FakeGitHub({})
        self.assertEqual(self.close(github, minutes=10)[0], [])
        self.assertEqual(github.calls, [])

    def test_it_waits_for_github_first(self):
        github = FakeGitHub({})
        self.assertEqual(self.close(github, minutes=1)[0], [])
        self.assertEqual(github.calls, [])
        self.assertIn("8200", m.load_json(
            os.path.join(self.state, m.CLOSING_FILE), {}))

    def test_reopened_and_other_repositories_are_left_alone(self):
        github = FakeGitHub(self.answer(
            self.ref(1, reason="REOPENED"), self.ref(2, repo="x/y")))
        self.assertEqual(self.close(github)[0], [])
        self.assertEqual(github.writes, [])

    def test_unreadable_is_kept_until_it_gives_up(self):
        def refuse():
            raise RuntimeError("gh api graphql: boom")
        github = FakeGitHub({"graphql": refuse})
        self.close(github)
        self.assertIn("8200", m.load_json(
            os.path.join(self.state, m.CLOSING_FILE), {}))
        self.close(github, minutes=25 * 60)
        self.assertEqual(m.load_json(
            os.path.join(self.state, m.CLOSING_FILE), {}), {})


def gated_project(scope=None, **merge):
    """The example project with a `mac` instance the Merger waits for."""
    data = example()
    # designs/0050 makes testing ahead the default; these tests say
    # when they want it.
    data["merge"] = dict({"gate": GATE, "own_head_green": False,
                          "test_ahead": False}, **merge)
    data["merge"]["test_ahead"] = old_defaults(data["merge"]["test_ahead"])
    block = {"context": "vm/mac", "steps": [{"name": "t", "run": ["true"]}],
             "poster": "Merger", "gate": True}
    if scope is not None:
        block["pull_requests"] = scope
    data.setdefault("checks", {}).setdefault("instances", {})["mac"] = block
    return config.Project(data, None)


class InstanceGate(RepoCase):
    """The Merger waits for a gated instance's context on the pull
    requests it applies to (agent-forge-next-bundle item 5)."""

    def setUp(self):
        super().setUp()
        self.advance_main()
        self.head = self.repo.merge("origin/main")
        self.repo.publish_pr(5, self.head)
        self.addCleanup(m.configure, project())

    def wait(self, statuses, scope=None, labels=()):
        m.configure(gated_project(scope))
        item = pr(5, head=self.head,
                  reviews=[review(REVIEWER, oid=self.approved)])
        item["labels"] = {"nodes": [{"name": n} for n in labels]}
        self.github = FakeGitHub({SLUG + "/commits/" + sha: value
                                  for sha, value in statuses.items()})
        return m.instance_wait(self.repo.path, item, self.repo.main_tip(),
                               self.github, {})

    def mac(self, state, description=""):
        return [status(state, context="vm/mac", description=description)]

    def test_the_configuration_reads_gated_instances(self):
        m.configure(gated_project({"paths": ["mac/*"], "label": "Check:mac"}))
        self.assertEqual(m.GATED, (("vm/mac", "mergerwidgetagent[bot]",
                                    ("mac/*",), "Check:mac"),))
        m.configure(project())
        self.assertEqual(m.GATED, ())

    def test_green_on_the_head_passes(self):
        self.assertIsNone(self.wait({self.head: self.mac("success")}))

    def test_absent_and_pending_wait(self):
        self.assertEqual(self.wait({self.head: [], self.approved: []}),
                         "waiting for vm/mac (absent)")
        self.assertEqual(self.wait({self.head: self.mac("pending"),
                                    self.approved: []}),
                         "waiting for vm/mac (pending)")

    def test_red_holds_it(self):
        self.assertEqual(
            self.wait({self.head: self.mac("failure", "red(test)")}),
            "vm/mac is failure at %s: red(test)" % self.head[:10])

    def test_a_verdict_before_a_merge_of_main_counts(self):
        # The head is a merge of main into the approved commit: the
        # instance's green on the approved commit is the pull request's.
        self.assertIsNone(self.wait({self.head: self.mac("pending"),
                                     self.approved: self.mac("success")}))

    def test_a_pull_request_out_of_scope_does_not_wait(self):
        self.assertIsNone(self.wait({}, scope={"paths": ["mac/*"]}))
        self.assertEqual(self.github.calls, [])

    def test_a_path_or_the_label_puts_it_in_scope(self):
        self.assertEqual(self.wait({self.head: [], self.approved: []},
                                   scope={"paths": ["f.*"]}),
                         "waiting for vm/mac (absent)")
        self.assertEqual(self.wait({self.head: [], self.approved: []},
                                   scope={"label": "Check:mac"},
                                   labels=("Check:mac",)),
                         "waiting for vm/mac (absent)")

    def test_someone_elses_green_does_not_count(self):
        self.assertEqual(self.wait({self.head: [status(
            "success", context="vm/mac", creator="mallory")],
            self.approved: []}), "waiting for vm/mac (absent)")


class OwnGreenWait(unittest.TestCase):
    """merge.own_head_green: `own_green_wait` on its own."""

    APPROVED, HEAD = "a" * 40, "h" * 40

    def wait(self, statuses, cache=None):
        github = FakeGitHub({SLUG + "/commits/" + sha: value
                             for sha, value in statuses.items()})
        item = pr(5, head=self.HEAD,
                  reviews=[review(REVIEWER, oid=self.APPROVED)])
        got = m.own_green_wait(item, m.approval_of(item), github,
                               {} if cache is None else cache)
        return got, github

    def test_a_green_kept_from_an_earlier_tick_reads_nothing(self):
        with tempfile.TemporaryDirectory() as state:
            first = {}
            self.assertIsNone(self.wait({self.APPROVED: [status()]}, first)[0])
            m.save_greens(state, first)
            kept = m.load_greens(state)
            got, github = self.wait({}, kept)
            self.assertIsNone(got)
            self.assertEqual(github.calls, [])

    def test_only_greens_are_kept(self):
        with tempfile.TemporaryDirectory() as state:
            m.save_greens(state, {"a": "pending", "b": "success", "c": None})
            self.assertEqual(m.load_greens(state), {"b": "success"})

    def test_kept_greens_are_bounded_newest_last(self):
        with tempfile.TemporaryDirectory() as state:
            with mock.patch.object(m, "GREENS_KEEP", 2):
                m.save_greens(state, {s: "success" for s in "abc"})
            self.assertEqual(list(m.load_greens(state)), ["b", "c"])

    def test_a_broken_greens_file_is_empty(self):
        with tempfile.TemporaryDirectory() as state:
            with open(os.path.join(state, m.GREENS_FILE), "w") as handle:
                handle.write("{")
            self.assertEqual(m.load_greens(state), {})

    def test_green_on_either_commit_passes(self):
        self.assertIsNone(self.wait({self.APPROVED: [status()]})[0])
        self.assertIsNone(self.wait({self.APPROVED: [status("pending")],
                                     self.HEAD: [status()]})[0])

    def test_pending_red_and_absent_hold_it(self):
        for state, said in (("pending", "pending"), ("failure", "failure"),
                            (None, "absent")):
            got, _ = self.wait({self.APPROVED: [], self.HEAD:
                                [status(state)] if state else []})
            self.assertEqual(got, m.GREEN_WAIT_REASON % (
                "vm/build-and-test", said, self.HEAD[:10]))

    def test_someone_elses_green_does_not_count(self):
        got, _ = self.wait({self.APPROVED: [], self.HEAD: [
            status(creator="mallory")]})
        self.assertIsNotNone(got)

    def test_each_commit_is_read_once_a_tick(self):
        cache = {}
        _, github = self.wait({self.APPROVED: [], self.HEAD: []}, cache)
        _, again = self.wait({self.APPROVED: [], self.HEAD: []}, cache)
        self.assertEqual(len(github.calls), 2)
        self.assertEqual(again.calls, [])

    def test_off_by_configuration(self):
        m.configure(project(own_head_green=False))
        self.addCleanup(m.configure, project())
        self.assertIs(m.OWN_HEAD_GREEN, False)


class OwnGreenWaitAfterUpdates(RepoCase):
    """merge.own_head_green follows merges of the default branch back:
    a batch member the Merger has just updated again, whose build check
    hasn't posted yet, stays a candidate (and in its batch) when an
    earlier update of it was green, though its approval names a commit
    that never was."""

    def setUp(self):
        super().setUp()
        self.advance_main("m1.txt")
        self.first = self.repo.merge("origin/main")
        self.advance_main("m2.txt")
        self.head = self.repo.merge("origin/main")

    def wait(self, statuses):
        github = FakeGitHub({SLUG + "/commits/" + sha: value
                             for sha, value in statuses.items()})
        item = pr(5, head=self.head,
                  reviews=[review(REVIEWER, oid=self.approved)])
        return m.own_green_wait(item, m.approval_of(item), github, {},
                                self.repo.path, self.repo.main_tip())

    def test_green_before_the_latest_update_counts(self):
        self.assertIsNone(self.wait({self.approved: [], self.head: [],
                                     self.first: [status()]}))

    def test_nothing_green_on_the_way_back_still_holds_it(self):
        self.assertEqual(
            self.wait({self.approved: [], self.head: [],
                       self.first: [status("failure")]}),
            m.GREEN_WAIT_REASON % ("vm/build-and-test", "absent",
                                   self.head[:10]))

    def test_a_commit_of_its_own_stops_the_walk(self):
        # A change after the green update is new work: its green is
        # needed, not the one before it.
        self.head = self.repo.commit("f.txt", "more\n", "more")
        self.assertIsNotNone(self.wait({self.approved: [], self.head: [],
                                        self.first: [status()]}))


class CodeOwnerAsk(unittest.TestCase):
    """agent-forge #153: a pull request held for a code owner opens an
    ask issue, closed when the hold ends at that head."""

    def setUp(self):
        m.configure(project(code_owners=True, code_owner_ask={
            "labels": ["Status:waiting-owner"]}))
        self.addCleanup(m.configure, project())
        self.dir = tempfile.mkdtemp()
        self.lines = []
        self.github = FakeGitHub({})

    def asks(self, waits, open_heads):
        m.code_owner_asks(self.dir, self.github, waits, open_heads,
                          self.lines.append)
        return m.load_json(os.path.join(self.dir, m.CODE_OWNER_ASK_NAME), {})

    def test_a_hold_opens_one_labelled_issue(self):
        why = "waits for code owner @alex: .github/CODEOWNERS"
        asks = self.asks({5: ("a" * 40, why), 6: ("b" * 40, None)},
                         {5: "a" * 40, 6: "b" * 40})
        self.assertEqual(asks, {"5": {"head": "a" * 40, "issue": 900,
                                      "labeled": ["Status:waiting-owner"]}})
        (method, path, fields), label = self.github.writes
        self.assertEqual(fields["title"], "#5 waits for a code owner's approval")
        self.assertIn(why, fields["body"])
        self.assertEqual(label[2], {"labels[]": "Status:waiting-owner"})
        # Once per head: the next tick opens nothing more.
        self.asks({5: ("a" * 40, why)}, {5: "a" * 40})
        self.assertEqual(len(self.github.writes), 2)

    def test_an_unjudged_pr_keeps_its_ask(self):
        self.asks({5: ("a" * 40, "waits")}, {5: "a" * 40})
        self.assertEqual(set(self.asks({}, {5: "a" * 40})), {"5"})

    def test_approval_new_head_or_close_ends_it(self):
        for waits, heads, said in (
                ({5: ("a" * 40, None)}, {5: "a" * 40}, "no longer waits"),
                ({}, {5: "c" * 40}, "has a new head"),
                ({}, {}, "was closed")):
            self.github = FakeGitHub({})
            m.save_json(os.path.join(self.dir, m.CODE_OWNER_ASK_NAME),
                        {"5": {"head": "a" * 40, "issue": 9,
                               "labeled": ["Status:waiting-owner"]}})
            self.assertEqual(self.asks(waits, heads), {})
            comment, close = self.github.writes
            self.assertIn(said, comment[2]["body"])
            self.assertEqual(close[:2], ("PATCH", "repos/example/widget/issues/9"))

    def test_a_refused_create_is_tried_again(self):
        self.github.refuse = "nope"
        self.assertEqual(self.asks({5: ("a" * 40, "waits")}, {5: "a" * 40}),
                         {})
        self.assertIn("CODE-OWNER-ASK refused", self.lines[0])

    def test_config(self):
        data = example()
        data["merge"] = {"code_owner_ask": {"title": "x", "labels": [""]}}
        problems = config.check(data)
        for said in ("needs merge.code_owners", "must name the pull request",
                     "must be a list of label names"):
            self.assertTrue(any(said in p for p in problems), problems)
class InstanceMainRed(unittest.TestCase):
    """agent-forge #154: an instance's own main-red issue puts the pull
    requests that close it first, as the main context's does."""

    def setUp(self):
        data = example()
        data["merge"] = {"gate": GATE, "own_head_green": False,
                         "test_ahead": False}
        data["checks"] = dict(data.get("checks", {}), instances={
            "mac": {"context": "vm/mac", "poster": "Helper",
                    "main_red_issue": {"labels": ["bug"]}},
            "quiet": {"context": "vm/quiet"}})
        self.project = config.Project(data, None)
        m.configure(self.project)
        self.addCleanup(m.configure, project())
        self.addCleanup(setattr, m, "MAIN_RED_FIXES", frozenset())

    def github(self, nodes):
        github = mock.Mock()
        github.api.return_value = {"data": {"repository": {"issues": {
            "nodes": nodes}}}}
        return github

    def test_only_an_instance_with_an_issue_has_a_rule(self):
        (title, labels, poster), = m.INSTANCE_RED
        self.assertEqual(labels, ("bug",))
        self.assertEqual(poster, self.project.bot_login("Helper"))
        self.assertTrue(title.match(
            "The default branch is red for vm/mac at 0123abcdef"))
        self.assertFalse(title.match(
            "The default branch is red for vm/quiet at 0123abcdef"))
        self.assertFalse(title.match(
            "The default branch is red for vm/mac at 0123abcdef!"))

    def test_the_issues_are_the_posters_with_the_title(self):
        login = self.project.slug("Helper")
        github = self.github([
            {"number": 7, "title": "The default branch is red for vm/mac "
             "at 0123abcdef", "author": {"login": login}},
            {"number": 8, "title": "The default branch is red for vm/mac "
             "at 0123abcdef", "author": {"login": "someoneelse"}},
            {"number": 9, "title": "Something else",
             "author": {"login": login}}])
        self.assertEqual(m.instance_red_issues(github), {7})
        args = github.api.call_args[0]
        self.assertIn("labels[]=bug", args)
        self.assertIn("owner=example", args)

    def test_github_unread_means_none(self):
        github = mock.Mock()
        github.api.side_effect = RuntimeError("down")
        self.assertEqual(m.instance_red_issues(github), set())

    def test_a_pr_that_closes_it_outside_code_is_a_fix(self):
        prs = [{"number": 1, "title": "Fix the Mac", "body": "Fixes #7"},
               {"number": 2, "title": "x", "body": "`Fixes #7`"},
               {"number": 3, "title": "Closes #7", "body": None},
               {"number": 4, "title": "y", "body": "Fixes #70"}]
        self.assertEqual(m.red_fixes_of(prs, {7}), frozenset({1, 3}))
        self.assertEqual(m.red_fixes_of(prs, set()), frozenset())


class GreenApproval(unittest.TestCase):
    """merge.approval green (2026-11-13)."""

    def setUp(self):
        m.configure(project(approval="green", own_head_green=True))
        self.addCleanup(m.configure, project())

    def node(self, **kw):
        p = pr(reviews=[], **kw)
        p["commits"] = {"nodes": [{"commit": {"committedDate": T1}}]}
        return p

    def test_the_head_approves_itself(self):
        got = m.approval_of(self.node())
        self.assertEqual((got["state"], got["submittedAt"],
                          got["commit"]["oid"]), ("APPROVED", T1, "a" * 40))
        approval, why = m.eligible(self.node(), [])
        self.assertIsNone(why)
        self.assertEqual(m.approval_of(pr(reviews=[])) ["submittedAt"], T0)

    def test_a_remark_after_the_push_holds_it(self):
        _, why = m.eligible(self.node(comments=[comment(PEER, T2)]), [])
        self.assertIn("after the approval", why)
        # One before the push doesn't.
        _, why = m.eligible(self.node(comments=[comment(PEER, T0)]), [])
        self.assertIsNone(why)

    def test_the_default_still_needs_a_reviewer(self):
        m.configure(project())
        self.assertIsNone(m.approval_of(self.node()))

    def test_the_configuration(self):
        data = example()
        data["merge"] = {"approval": "sometimes"}
        self.assertIn("merge.approval must be reviewer or green",
                      config.check(data))
        data["merge"] = {"approval": "green", "own_head_green": False}
        self.assertIn("merge.approval green needs own_head_green",
                      config.check(data))


class ManualMode(unittest.TestCase):
    """agent-forge #274: merge.mode manual."""

    def setUp(self):
        m.configure(project(mode="manual"))
        self.addCleanup(m.configure, project())
        self.dir = tempfile.mkdtemp()
        self.lines = []

    def test_the_configuration(self):
        self.assertTrue(m.MANUAL)
        m.configure(project())
        self.assertFalse(m.MANUAL)
        data = example()
        data["merge"] = {"mode": "sometimes"}
        self.assertIn("merge.mode must be auto or manual",
                      config.check(data))

    def test_hand_merges_are_recorded_as_merges(self):
        when = datetime.datetime(2026, 10, 7, 10, tzinfo=datetime.timezone.utc)
        github = FakeGitHub({SLUG + "/pulls?state=closed": [
            {"number": 7, "merged_at": "2026-10-07T10:30:00Z",
             "merged_by": {"login": "leaddevwidgetagent"}},
            {"number": 6, "merged_at": "2026-10-07T09:30:00Z"},
            {"number": 5, "merged_at": None},
            {"number": 8, "merged_at": "2026-10-07T10:20:00Z",
             "user": {"login": "someone"}}]})
        # The first run starts the clock.
        self.assertEqual(m.record_hand_merges(self.dir, github,
                                              self.lines.append, when), [])
        got = m.record_hand_merges(self.dir, github, self.lines.append,
                                   when + datetime.timedelta(hours=1))
        self.assertEqual([(r["at"], r["decisions"][0]["pr"],
                           r["decisions"][0]["by"]) for r in got],
                         [("2026-10-07T10:20:00Z", 8, "someone"),
                          ("2026-10-07T10:30:00Z", 7, "leaddevwidgetagent")])
        with open(os.path.join(self.dir, "merger.jsonl")) as handle:
            self.assertEqual(len(handle.readlines()), 2)
        # Each once.
        self.assertEqual(m.record_hand_merges(self.dir, github,
                                              self.lines.append, when), [])
        self.assertIn("2 hand merge(s) recorded: #8, #7", self.lines[1])


    def test_ready_lists_what_holds_it(self):
        m.configure(project(mode="manual", ci_workflow=""))
        head = "a" * 40
        prs = [{"number": 5, "headRefOid": head, "isDraft": False,
                "body": "Blocked-by: #9\n"}, {"number": 9}]
        green = [{"context": m.STATUS_CONTEXT, "state": "success",
                  "creator": {"login": m.STATUS_CREATOR}}]
        github = FakeGitHub({SLUG + "/commits/": green})
        behind = {"answer": 1}

        def git(repo, *args, run=None):
            code = behind["answer"] if args[0] == "merge-base" else 0
            return subprocess.CompletedProcess(args, code, "", "")
        with mock.patch.object(m, "fetch_prs", return_value=prs), \
                mock.patch.object(m, "fetch_open_issues", return_value=set()), \
                mock.patch.object(m, "have_head"), \
                mock.patch.object(m, "git", git):
            self.assertEqual(m.merge_ready("/r", github, 5), [
                "it is not current with main: merge main into it first",
                "Blocked-by #9, still open"])
            prs[0]["body"], behind["answer"] = "", 0
            self.assertEqual(m.merge_ready("/r", github, 5), [])
            self.assertEqual(m.merge_ready("/r", github, 6),
                             ["#6 is not an open pull request"])
class TrainAheadStall(unittest.TestCase):
    """A head waiting for train 1's run is timed from that run's start,
    not from when it began waiting: the wait is the train's."""

    START = datetime.datetime(2026, 10, 7, 12, 0, 0,
                              tzinfo=datetime.timezone.utc)

    def setUp(self):
        self.state = tempfile.mkdtemp()
        m.configure(project(stall_run_factor=0))
        self.addCleanup(m.configure, project())

    def stalled(self, minutes, started):
        waiting = {"pr": 5, "head": "h" * 40, "action": "WAIT",
                   "train_top": "t" * 40}
        check = {"ahead_started": {"t" * 40: started}} if started else {}
        m.stalled_for(self.state, waiting, self.START, check)
        return m.stalled_for(
            self.state, waiting,
            self.START + datetime.timedelta(minutes=minutes), check)

    def test_timed_from_the_trains_run(self):
        self.assertIsNone(self.stalled(45, "2026-10-07T12:20:00Z"))
        self.assertEqual(self.stalled(50, "2026-10-07T12:20:00Z"), 50)

    def test_no_run_start_known_is_timed_as_before(self):
        self.assertEqual(self.stalled(30, None), 30)


class Unblockers(unittest.TestCase):
    """designs/0052: next.json's `unblocks`."""

    def test_what_held_ones_wait_on(self):
        self.assertEqual(m.unblockers({
            "5": "Blocked-by: #9 is open", "6": "stacked on #7",
            "8": "waits for code owner @x: y"}), [7, 9])


class Boarded(unittest.TestCase):
    """A stack layer and a member aboard together (the tick failed with
    `keywords must be strings` once both were)."""

    def test_layers_and_members_join(self):
        with mock.patch.object(m, "LAYERS_OWN_GREEN", False), \
                mock.patch.object(m, "OWN_GREEN_TO_BOARD", False), \
                mock.patch.object(m, "boarded_layers",
                                  return_value={10999: "b" * 40}), \
                mock.patch.object(m, "boarded_members",
                                  return_value={10990: "a" * 40,
                                                10999: "c" * 40}):
            self.assertEqual(m.boarded_now({}), {10990: "a" * 40,
                                             10999: "b" * 40})

