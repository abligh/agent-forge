"""Tests for bin/forge-router.

Each rule is tested against a GraphQL response built in the shape the
router's query returns, passed through `fetch` as a fake `gh api`
would hand it over, so the flattening is exercised with the rules. The
relay is a fake `post`, or a fake opener for the HTTP layer: no test
reaches the network, mints a token or touches the real state.
"""

import datetime
import io
import json
import os
import pathlib
import tempfile
import unittest
import urllib.error
from unittest import mock

from agent_forge import config
from tests.helpers import example, load_tool

r = load_tool("forge-router")
r.configure(config.Project(example(), None))

LEADDEV = "leaddevwidgetagent[bot]"
REVIEWER = "reviewerwidgetagent[bot]"
MERGER = "mergerwidgetagent"  # GraphQL leaves "[bot]" off
OWNER = "example-owner"
HEAD = "5b257c8" + "0" * 33
BACK = "Not merging %s: x.\n\n— Merger" % HEAD[:10]
OLD = "1a2b3c4" + "0" * 33     # a commit before the head
MID = "9d8e7f6" + "0" * 33
T0 = "2026-10-09T10:00:00Z"
T1 = "2026-10-09T11:00:00Z"
T2 = "2026-10-09T12:00:00Z"
T3 = "2026-10-09T13:00:00Z"
NOW = datetime.datetime(2026, 10, 9, 14, 0, tzinfo=datetime.timezone.utc)



def roster(agents, machinery=None):
    """A Roster of the example project with these agents."""
    data = example()
    data["agents"] = agents
    if machinery is not None:
        data["machinery"] = machinery
    return r.Roster(config.Project(data, None))


# The example project, for the tests that pin what it says.
LIVE = r.Roster(config.Project(example(), None))
# The rules are tested against the example's LeadDev, Reviewer and
# Helper, with Reviewer the one reviewer. TWO, below, adds a second.
ROSTER = roster({name: example()["agents"][name]
                 for name in ("LeadDev", "Reviewer", "Helper")})


# --- fixtures in GraphQL's shape --------------------------------------------


def review(who, state, at, rid=900, oid=HEAD):
    return {"databaseId": rid, "author": {"login": who}, "state": state,
            "submittedAt": at, "body": "", "commit": {"oid": oid},
            "comments": {"totalCount": 0}}


def comment(who, at, body="text", cid=700):
    return {"databaseId": cid, "author": {"login": who}, "createdAt": at,
            "body": body}


def status(state, at=T1, creator=MERGER, context="vm/build-and-test"):
    return {"context": context, "state": state, "createdAt": at,
            "creator": {"login": creator}}


def pr_node(number=41, author=LEADDEV, labels=(), draft=False,
            reviews=(), comments=(), statuses=(status("SUCCESS"),),
            commits=((OLD, 1), (HEAD, 1)), head=HEAD, files=()):
    """`commits` are (oid, parent count), oldest first; the last is the
    head. `files` are (path, changed lines)."""
    return {"number": number, "isDraft": draft, "headRefOid": head,
            "author": {"login": author},
            "files": {"nodes": [{"path": p, "additions": n, "deletions": 0}
                                for p, n in files]},
            "labels": {"nodes": [{"name": l} for l in labels]},
            "reviews": {"nodes": list(reviews)},
            "comments": {"nodes": list(comments)},
            "commits": {"nodes": [
                {"commit": {"oid": oid, "parents": {"totalCount": parents}}}
                for oid, parents in commits]},
            "headCommit": {"nodes": [{"commit": {
                "oid": commits[-1][0],
                "status": {"contexts": list(statuses)} if statuses
                else None}}]}}


def issue_node(number=22, author=LEADDEV, labels=(), comments=(), body="",
               created=T0, edited=None):
    return {"number": number, "author": {"login": author}, "body": body,
            "createdAt": created, "lastEditedAt": edited,
            "labels": {"nodes": [{"name": l} for l in labels]},
            "comments": {"nodes": list(comments)}}


def response(prs=(), issues=(), more_prs=None, more_issues=None):
    repo = {}
    if prs is not None:
        repo["pullRequests"] = {
            "pageInfo": {"hasNextPage": bool(more_prs),
                         "endCursor": more_prs},
            "nodes": json.loads(json.dumps(list(prs)))}
    if issues is not None:
        repo["issues"] = {
            "pageInfo": {"hasNextPage": bool(more_issues),
                         "endCursor": more_issues},
            "nodes": json.loads(json.dumps(list(issues)))}
    return {"data": {"repository": repo}}


class FakeGitHub:
    """Answers each `gh api graphql` call with the next page."""

    def __init__(self, *pages):
        self.pages = list(pages)
        self.calls = []

    def api(self, *args):
        self.calls.append(args)
        if args[0] != "graphql":
            raise AssertionError("unexpected gh api %r" % (args,))
        if not self.pages:
            raise AssertionError("no page left for %r" % (args,))
        return self.pages.pop(0)


class FailingGitHub:
    def api(self, *args):
        raise RuntimeError("gh api graphql: HTTP 502")


def decide_pr(node):
    prs, _ = r.fetch(FakeGitHub(response([node], [])))
    return r.route_pr(prs[0], ROSTER)


def decide_issue(node):
    _, issues = r.fetch(FakeGitHub(response([], [node])))
    return r.route_issue(issues[0], ROSTER)


class Mapping(unittest.TestCase):

    def test_bot_logins_map_to_roles_with_or_without_the_suffix(self):
        for login in (LEADDEV, "leaddevwidgetagent", "LeadDevWidgetAgent"):
            self.assertEqual(ROSTER.who(login), "LeadDev")
        self.assertEqual(ROSTER.who(REVIEWER), "Reviewer")
        self.assertEqual(ROSTER.who("helperbot[bot]"), "Helper")

    def test_the_merger_is_known_but_not_an_agent(self):
        self.assertEqual(ROSTER.who(MERGER + "[bot]"), "Merger")
        self.assertFalse(ROSTER.is_agent("Merger"))
        self.assertTrue(ROSTER.is_agent("LeadDev"))

    def test_anyone_else_is_their_login(self):
        self.assertEqual(ROSTER.who(OWNER), OWNER)
        self.assertFalse(ROSTER.is_agent(OWNER))
        self.assertEqual(ROSTER.who(None), "ghost")

    def test_session_names(self):
        self.assertEqual(ROSTER.session_name("LeadDev"), "WidgetLeadDev")
        self.assertEqual(ROSTER.session_name("Reviewer"), "WidgetReviewer")
        two = roster({"Painter": {"session_name": "Brush"}, "Plain": {}})
        self.assertEqual(two.session_name("Painter"), "Brush")
        self.assertEqual(two.session_name("Plain"), "WidgetPlain")

    def test_a_configured_session_name(self):
        self.assertEqual(ROSTER.session_name("Helper"), "Helper")

    def test_signatures_cover_agents_and_the_merger(self):
        self.assertEqual(LIVE.signatures(), {
            "— LeadDev", "— Reviewer", "— UIDev", "— UIReviewer",
            "— Helper", "— Merger"})

    def test_every_agent_wakes_by_its_session_name(self):
        for name in ("LeadDev", "UIDev", "UIReviewer"):
            self.assertEqual(LIVE.session_name(name), "Widget" + name)
            self.assertTrue(LIVE.is_agent(name))


class PullRequestRules(unittest.TestCase):

    def assertTurn(self, decision, who, rule, event=None):
        self.assertEqual((decision["who"], decision["rule"]), (who, rule),
                         decision)
        if event is not None:
            self.assertEqual(decision["event"], event)

    def assertNobody(self, decision, rule):
        self.assertIsNone(decision["who"], decision)
        self.assertEqual(decision["rule"], rule, decision)

    # Rule 1.
    def test_1_a_draft_is_nobodys(self):
        self.assertNobody(decide_pr(pr_node(draft=True)), 1)

    def test_1_waiting_labels_are_nobodys(self):
        for label in ("Status:waiting-owner", "Status:waiting-external"):
            self.assertNobody(decide_pr(pr_node(labels=[label])), 1)

    # Rule 2.
    def test_2_a_plain_hand_back_is_the_authors(self):
        body = "Not merging 5b257c8: it conflicts.\n\n— Merger"
        d = decide_pr(pr_node(reviews=[review(REVIEWER, "APPROVED", T1)],
                              comments=[comment(MERGER, T2, body, 61)]))
        self.assertTurn(d, "LeadDev", 2, T2)
        self.assertEqual(d["line"],
                         "#41 the Merger handed it back (comment 61)")

    def test_2_a_not_a_review_hand_back_is_reviewers(self):
        body = "Not-a-review\n\nNot merging 5b257c8: x.\n\n— Merger"
        d = decide_pr(pr_node(reviews=[review(REVIEWER, "APPROVED", T1)],
                              comments=[comment(MERGER + "[bot]", T2, body)]))
        self.assertTurn(d, "Reviewer", 2, T2)

    def test_2_a_hand_back_of_an_older_head_has_gone_stale(self):
        # The author pushed a fix: a push is neither comment nor review,
        # so only the head the hand-back names can tell.
        for body in ("Not merging %s: it conflicts.\n\n— Merger" % OLD[:10],
                     "Not-a-review\n\nNot merging %s: x.\n\n— Merger"
                     % OLD[:10]):
            d = decide_pr(pr_node(comments=[comment(MERGER, T2, body)]))
            self.assertTurn(d, "Reviewer", 8)

    def test_2_a_merger_comment_naming_no_head_is_not_a_hand_back(self):
        d = decide_pr(pr_node(comments=[comment(MERGER, T2, "Not merging")]))
        self.assertNotEqual(d["rule"], 2)

    def test_2_does_not_hold_once_the_author_has_replied(self):
        d = decide_pr(pr_node(
            reviews=[review(REVIEWER, "APPROVED", T1)],
            comments=[comment(MERGER, T2, BACK),
                      comment(LEADDEV, T3, "fixed")]))
        self.assertNotEqual(d["rule"], 2)

    def test_2_does_not_hold_once_a_review_is_newer(self):
        d = decide_pr(pr_node(
            reviews=[review(REVIEWER, "APPROVED", T3)],
            comments=[comment(MERGER, T2, BACK)]))
        self.assertNotEqual(d["rule"], 2)

    def test_2_a_third_partys_later_comment_does_not_end_it(self):
        d = decide_pr(pr_node(comments=[comment(MERGER, T2, BACK),
                                        comment("someone", T3)]))
        self.assertTurn(d, "LeadDev", 2, T2)

    # Rule 3.
    def test_3_changes_requested_since_the_push_is_the_authors(self):
        d = decide_pr(pr_node(reviews=[review(REVIEWER, "CHANGES_REQUESTED",
                                              T1, 55)]))
        self.assertTurn(d, "LeadDev", 3, T1)
        self.assertEqual(d["line"],
                         "#41 Reviewer asked for changes (review 55)")

    def test_3_a_commented_review_is_the_authors(self):
        d = decide_pr(pr_node(reviews=[review(REVIEWER, "COMMENTED", T1)]))
        self.assertTurn(d, "LeadDev", 3)
        self.assertIn("Reviewer commented", d["line"])

    def test_3_not_once_the_author_has_answered(self):
        d = decide_pr(pr_node(reviews=[review(REVIEWER, "COMMENTED", T1)],
                              comments=[comment(LEADDEV, T2)]))
        self.assertTurn(d, "Reviewer", 8)

    def test_3_not_for_a_review_of_an_older_commit(self):
        d = decide_pr(pr_node(reviews=[
            review(REVIEWER, "CHANGES_REQUESTED", T1, oid=OLD)]))
        self.assertTurn(d, "Reviewer", 8)

    def test_3_only_reviewers_review_counts(self):
        d = decide_pr(pr_node(reviews=[review("someone", "CHANGES_REQUESTED",
                                              T1)]))
        self.assertTurn(d, "Reviewer", 8)

    # Rule 4.
    def test_4_a_red_check_is_the_authors(self):
        for state in ("FAILURE", "ERROR"):
            d = decide_pr(pr_node(statuses=[status(state, T1)]))
            self.assertTurn(d, "LeadDev", 4, T1)
            self.assertEqual(d["line"], "#41 vm/build-and-test is %s at "
                             "5b257c8" % state.lower())

    def test_4_only_the_mergers_status_counts(self):
        d = decide_pr(pr_node(statuses=[status("FAILURE", creator=OWNER)]))
        self.assertNobody(d, 5)

    def test_4_the_newest_of_the_mergers_statuses_counts(self):
        d = decide_pr(pr_node(statuses=[status("FAILURE", T1),
                                        status("SUCCESS", T2)]))
        self.assertTurn(d, "Reviewer", 8)

    # Rule 5.
    def test_5_pending_or_absent_is_nobodys(self):
        self.assertNobody(decide_pr(pr_node(statuses=[status("PENDING")])), 5)
        self.assertNobody(decide_pr(pr_node(statuses=())), 5)

    def test_5_a_status_on_another_commit_is_absent(self):
        node = pr_node()
        node["headCommit"]["nodes"][0]["commit"]["oid"] = "f" * 40
        self.assertNobody(decide_pr(node), 5)

    # Rule 6.
    def test_6_approved_since_the_push_and_quiet_is_the_mergers(self):
        self.assertNobody(decide_pr(pr_node(
            reviews=[review(REVIEWER, "APPROVED", T1)])), 6)

    def test_6_not_when_something_was_said_since(self):
        d = decide_pr(pr_node(reviews=[review(REVIEWER, "APPROVED", T1)],
                              comments=[comment(LEADDEV, T2, "thanks")]))
        self.assertTurn(d, "Reviewer", 8, T2)

    def test_6_an_approval_covers_the_head_through_merges(self):
        # The Merger's update-branch merges of main after the approval.
        commits = ((OLD, 1), (MID, 2), (HEAD, 2))
        self.assertNobody(decide_pr(pr_node(commits=commits, reviews=[
            review(REVIEWER, "APPROVED", T1, oid=OLD)])), 6)

    def test_8_an_approval_then_a_non_merge_commit_needs_review(self):
        commits = ((OLD, 1), (MID, 2), (HEAD, 1))
        self.assertTurn(decide_pr(pr_node(commits=commits, reviews=[
            review(REVIEWER, "APPROVED", T1, oid=OLD)])), "Reviewer", 8)

    def test_8_an_approval_of_a_commit_no_longer_on_the_pr(self):
        # After a force-push the approved commit is gone.
        self.assertTurn(decide_pr(pr_node(reviews=[
            review(REVIEWER, "APPROVED", T1, oid="e" * 40)])), "Reviewer", 8)

    def test_8_a_review_of_an_older_commit_is_not_a_review_of_the_head(self):
        # The head was committed before the review was written, but
        # reached the pull request after it: the clock says reviewed,
        # the commit says not.
        d = decide_pr(pr_node(reviews=[
            review(REVIEWER, "APPROVED", T3, oid=OLD)],
            statuses=[status("SUCCESS", T1)]))
        self.assertTurn(d, "Reviewer", 8)
        d = decide_pr(pr_node(reviews=[
            review(REVIEWER, "COMMENTED", T3, oid=OLD)]))
        self.assertTurn(d, "Reviewer", 8)

    # Rule 7.
    def test_7_reviewers_own_green_pull_request_is_nobodys(self):
        self.assertNobody(decide_pr(pr_node(author=REVIEWER)), 7)

    # Rule 8.
    def test_8_green_and_unreviewed_is_reviewers(self):
        d = decide_pr(pr_node())
        self.assertTurn(d, "Reviewer", 8, T1)
        self.assertEqual(d["line"], "#41 needs your review (green at 5b257c8)")

    def test_8_the_authors_later_comment_is_the_event(self):
        d = decide_pr(pr_node(comments=[comment(LEADDEV, T3)]))
        self.assertTurn(d, "Reviewer", 8, T3)

    def test_8_falls_to_someone_not_an_agent(self):
        d = decide_pr(pr_node(author=OWNER))
        self.assertTurn(d, "Reviewer", 8)
        d = decide_pr(pr_node(author=OWNER,
                              statuses=[status("FAILURE")]))
        self.assertTurn(d, OWNER, 4)

    def test_no_rule(self):
        # Green, Reviewer approved, then someone else spoke after it: not
        # quiet (6), but Reviewer has reviewed since the push (8).
        d = decide_pr(pr_node(reviews=[review(REVIEWER, "APPROVED", T1)],
                              comments=[comment("someone", T2)]))
        self.assertNobody(d, None)


def blockers_page(**found):
    """The answer to the Blocked-by query: n<number> -> its state."""
    return {"data": {"repository": {
        k: ({"state": v[0], "closedAt": v[1]} if v else None)
        for k, v in found.items()}}}


class GitHubFailingOn:
    """FakeGitHub, but a Blocked-by query naming `bad` fails, as GitHub
    fails a query naming a number that doesn't exist."""

    def __init__(self, bad, *pages):
        self.bad, self.fake = bad, FakeGitHub(*pages)

    def api(self, *args):
        if any("n%d:" % self.bad in a for a in args):
            raise RuntimeError("gh api graphql: Could not resolve")
        return self.fake.api(*args)


class UnblockedRule(unittest.TestCase):
    """Rule 14: an issue whose Blocked-by items have all closed."""

    BODY = "Next step.\n\nBlocked-by: #1\nBlocked-by: #2\n"

    def decide(self, *pages, issues=None, github=None):
        node = issue_node(number=30, labels=["Owner:LeadDev"], body=self.BODY)
        github = github or FakeGitHub(response([], issues or [node]), *pages)
        _, got = r.fetch(github)
        return r.route_issue(got[0], ROSTER), github

    def test_one_still_open_is_nobodys(self):
        d, _ = self.decide(blockers_page(n1=("MERGED", T2)),
                           issues=[issue_node(number=30, labels=["Owner:LeadDev"],
                                              body=self.BODY),
                                   issue_node(number=2)])
        self.assertIsNone(d["who"])

    def test_all_closed_is_the_owners_once_with_the_last_close(self):
        d, github = self.decide(blockers_page(n1=("MERGED", T1),
                                              n2=("CLOSED", T2)))
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("LeadDev", 14, T2))
        self.assertEqual(d["line"], "#30 unblocked: #1, #2 closed")
        self.assertEqual(len(github.calls), 2)

    def test_a_line_written_after_the_close_does_not_wake(self):
        node = issue_node(number=30, labels=["Owner:LeadDev"], body=self.BODY,
                          edited=T3)
        d, _ = self.decide(blockers_page(n1=("MERGED", T1),
                                         n2=("CLOSED", T2)), issues=[node])
        self.assertIsNone(d["who"])

    def test_the_owner_speaking_after_the_close_ends_it(self):
        node = issue_node(number=30, labels=["Owner:LeadDev"], body=self.BODY,
                          comments=[comment(LEADDEV, T3)])
        d, _ = self.decide(blockers_page(n1=("MERGED", T1),
                                         n2=("CLOSED", T2)), issues=[node])
        self.assertIsNone(d["who"])

    def test_no_owner_label_goes_to_the_author(self):
        node = issue_node(number=30, author=REVIEWER, body=self.BODY)
        d, _ = self.decide(blockers_page(n1=("MERGED", T1),
                                         n2=("CLOSED", T2)), issues=[node])
        self.assertEqual((d["who"], d["rule"]), ("Reviewer", 14))

    def test_a_line_in_code_does_not_count_and_no_query_is_made(self):
        node = issue_node(number=30, labels=["Owner:LeadDev"],
                          body="```\nBlocked-by: #1\n```\n")
        github = FakeGitHub(response([], [node]))
        d, _ = self.decide(github=github)
        self.assertIsNone(d["who"])
        self.assertEqual(len(github.calls), 1)

    def test_a_number_naming_nothing_is_left_out(self):
        github = GitHubFailingOn(
            2, response([], [issue_node(number=30, labels=["Owner:LeadDev"],
                                        body=self.BODY)]),
            blockers_page(n1=("MERGED", T1)))
        d, _ = self.decide(github=github)
        self.assertEqual((d["who"], d["rule"], d["event"]),
                         ("LeadDev", 14, T1))
        self.assertEqual(d["line"], "#30 unblocked: #1 closed")


class IssueRules(unittest.TestCase):

    def assertTurn(self, decision, who, rule, event=None):
        self.assertEqual((decision["who"], decision["rule"]), (who, rule),
                         decision)
        if event is not None:
            self.assertEqual(decision["event"], event)

    def assertNobody(self, decision, rule):
        self.assertIsNone(decision["who"], decision)
        self.assertEqual(decision["rule"], rule, decision)

    # Rule 9.
    def test_9_waiting_external_is_nobodys(self):
        self.assertNobody(decide_issue(issue_node(
            labels=["Status:waiting-external", "Owner:LeadDev"],
            comments=[comment(OWNER, T2)])), 9)

    # Rule 10.
    def test_10_issue_22_the_owner_answered_goes_to_the_author(self):
        # #22 had no Owner: label; LeadDev opened it and asked.
        d = decide_issue(issue_node(
            number=22, labels=["Status:waiting-owner", "task"],
            comments=[comment(LEADDEV, T1, "Parking this on you"),
                      comment(OWNER, T2, "Yes, do it.", 6088707208)]))
        self.assertTurn(d, "LeadDev", 10, T2)
        self.assertEqual(d["line"], "#22 example-owner answered (comment 6088707208)")

    def test_10_the_owner_label_wins_over_the_author(self):
        d = decide_issue(issue_node(
            author=REVIEWER, labels=["Status:waiting-owner", "Owner:LeadDev"],
            comments=[comment(OWNER, T2)]))
        self.assertTurn(d, "LeadDev", 10)

    def test_10_the_newest_answer_is_the_event(self):
        d = decide_issue(issue_node(
            labels=["Status:waiting-owner"],
            comments=[comment(LEADDEV, T0), comment(OWNER, T1, cid=1),
                      comment(OWNER, T3, cid=3)]))
        self.assertTurn(d, "LeadDev", 10, T3)

    def test_10_an_agent_signing_under_owners_login_is_not_the_owner(self):
        for sig in ("— LeadDev", "— Reviewer", "— Merger"):
            d = decide_issue(issue_node(
                labels=["Status:waiting-owner"],
                comments=[comment(LEADDEV, T1),
                          comment(OWNER, T2, "Done.\n\n%s\n\n" % sig)]))
            self.assertNobody(d, 11)

    def test_10_a_signature_not_on_the_last_line_is_still_the_owner(self):
        d = decide_issue(issue_node(
            labels=["Status:waiting-owner"],
            comments=[comment(OWNER, T2, "> — LeadDev\n\nAgreed.")]))
        self.assertTurn(d, "LeadDev", 10)

    # Rule 11.
    def test_11_waiting_on_the_owner_with_no_answer_is_nobodys(self):
        self.assertNobody(decide_issue(issue_node(
            labels=["Status:waiting-owner"],
            comments=[comment(OWNER, T1), comment(LEADDEV, T2)])), 11)

    # Rule 12.
    def test_12_someone_elses_newer_comment_is_the_owners(self):
        d = decide_issue(issue_node(
            labels=["Owner:LeadDev"],
            comments=[comment(LEADDEV, T1), comment(REVIEWER, T2, cid=8)]))
        self.assertTurn(d, "LeadDev", 12, T2)
        self.assertEqual(d["line"],
                         "#22 has a comment by Reviewer (comment 8)")

    def test_12_not_when_the_owner_spoke_last(self):
        self.assertNobody(decide_issue(issue_node(
            labels=["Owner:LeadDev"],
            comments=[comment(REVIEWER, T1), comment(LEADDEV, T2)])), None)

    def test_12_an_owner_who_never_commented(self):
        d = decide_issue(issue_node(labels=["Owner:Reviewer"],
                                    comments=[comment(LEADDEV, T1)]))
        self.assertTurn(d, "Reviewer", 12)

    # Rule 13.
    def test_13_issue_78_the_owner_answered_and_took_the_label_off(self):
        # #78: LeadDev asked, no Owner:; the owner approved and removed
        # Status:waiting-owner in the same minute.
        d = decide_issue(issue_node(
            number=78, comments=[comment(LEADDEV, T1, "Ready."),
                                 comment(OWNER, T2, "Approved.", 6094)]))
        self.assertTurn(d, "LeadDev", 13, T2)
        self.assertEqual(d["line"], "#78 example-owner answered (comment 6094)")

    def test_13_not_when_the_author_spoke_last(self):
        self.assertNobody(decide_issue(issue_node(
            comments=[comment(OWNER, T1), comment(LEADDEV, T2)])), None)

    def test_13_an_agent_signing_under_owners_login_is_not_the_owner(self):
        self.assertNobody(decide_issue(issue_node(
            comments=[comment(OWNER, T2, "Done.\n\n— Reviewer")])), None)

    def test_13_an_owner_label_leaves_it_to_rule_12(self):
        d = decide_issue(issue_node(
            labels=["Owner:Reviewer"],
            comments=[comment(REVIEWER, T1), comment(OWNER, T2)]))
        self.assertTurn(d, "Reviewer", 12)

    def test_no_owner_and_no_word_from_the_owner_is_nobodys(self):
        self.assertNobody(decide_issue(issue_node(
            comments=[comment(LEADDEV, T1), comment(REVIEWER, T2)])), None)


RENDER = "renderreviewerwidgetagent[bot]"
TWO = roster({
    "LeadDev": {},
    "Reviewer": {"kind": "reviewer",
                 "reviews": ["designs/**", "crates/doc/**"]},
    "RenderReviewer": {"kind": "reviewer",
                       "reviews": ["crates/raster/**", "web/**"]},
}, {"Merger": {"app_id": 1, "installation_id": 2}})


def decide_two(node):
    prs, _ = r.fetch(FakeGitHub(response([node], [])))
    return r.route_pr(prs[0], TWO)


class TwoReviewers(unittest.TestCase):
    """Reviewers have areas."""

    def who(self, files, author=LEADDEV):
        prs, _ = r.fetch(FakeGitHub(response(
            [pr_node(author=author, files=files)], [])))
        return TWO.reviewer_for(prs[0])

    def test_the_area_with_the_most_changed_lines(self):
        self.assertEqual(self.who([("crates/raster/src/a.rs", 300),
                                   ("designs/0004-x.md", 20)]),
                         "RenderReviewer")
        self.assertEqual(self.who([("crates/raster/src/a.rs", 20),
                                   ("crates/doc/src/b.rs", 300)]),
                         "Reviewer")

    def test_a_tie_and_unmatched_paths_go_to_the_first_listed(self):
        self.assertEqual(self.who([("web/a.ts", 10), ("designs/a.md", 10)]),
                         "Reviewer")
        self.assertEqual(self.who([("Makefile", 50), ("web/a.ts", 40)]),
                         "Reviewer")
        self.assertEqual(self.who([]), "Reviewer")

    def test_a_reviewers_own_goes_to_the_other(self):
        self.assertEqual(self.who([("designs/a.md", 10)], author=REVIEWER),
                         "RenderReviewer")
        self.assertEqual(self.who([("web/a.ts", 10)], author=RENDER),
                         "Reviewer")

    def test_8_a_raster_pull_request_wakes_render_reviewer(self):
        d = decide_two(pr_node(files=[("crates/raster/src/a.rs",
                                       100)]))
        self.assertEqual((d["who"], d["rule"]), ("RenderReviewer", 8))

    def test_3_either_reviewers_changes_go_to_the_author(self):
        d = decide_two(pr_node(reviews=[review(RENDER, "CHANGES_REQUESTED",
                                               T2)]))
        self.assertEqual((d["who"], d["rule"]), ("LeadDev", 3))
        self.assertIn("RenderReviewer asked for changes", d["line"])

    def test_6_either_reviewers_approval_is_the_mergers(self):
        d = decide_two(pr_node(reviews=[review(RENDER, "APPROVED", T2)]))
        self.assertEqual((d["who"], d["rule"]), (None, 6))

    def test_2_not_a_review_goes_to_the_approver(self):
        d = decide_two(pr_node(
            files=[("designs/a.md", 100)],
            reviews=[review(RENDER, "APPROVED", T1)],
            comments=[comment(MERGER, T2, "Not-a-review\n\n" + BACK)]))
        self.assertEqual((d["who"], d["rule"]), ("RenderReviewer", 2))

    def test_7_with_one_reviewer_its_own_is_nobodys(self):
        one = roster({"LeadDev": {}, "Reviewer": {"kind": "reviewer"}})
        prs, _ = r.fetch(FakeGitHub(response([pr_node(author=REVIEWER)],
                                             [])))
        self.assertEqual(r.route_pr(prs[0], one)["rule"], 7)

    def test_the_example_has_two_reviewers_reviewer_first(self):
        # Reviewer first: unmatched lines go to the reviewer listed first.
        self.assertEqual(LIVE.reviewers(), ["Reviewer", "UIReviewer"])

    def test_the_example_areas(self):
        def who(path):
            prs, _ = r.fetch(FakeGitHub(response(
                [pr_node(author=LEADDEV, files=[(path, 10)])], [])))
            return LIVE.reviewer_for(prs[0])
        for path, reviewer in [
                ("docs/a.md", "Reviewer"), ("tools/x", "Reviewer"),
                ("src/core/a.py", "Reviewer"),
                ("web/src/main.ts", "UIReviewer"), ("Makefile", "Reviewer")]:
            self.assertEqual(who(path), reviewer, path)


class Fetch(unittest.TestCase):

    def test_pages_each_connection_until_done(self):
        github = FakeGitHub(
            response([pr_node(1)], [issue_node(2)], more_prs="P1",
                     more_issues="I1"),
            response([pr_node(3)], [issue_node(4)], more_issues="I2"),
            response(None, [issue_node(5)]))
        prs, issues = r.fetch(github)
        self.assertEqual([p["number"] for p in prs], [1, 3])
        self.assertEqual([i["number"] for i in issues], [2, 4, 5])
        first, second, third = github.calls
        self.assertIn("prs=true", first)
        self.assertIn("prCursor=P1", second)
        self.assertIn("issueCursor=I1", second)
        self.assertIn("prs=false", third)
        self.assertIn("issueCursor=I2", third)


class StateCase(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "router")
        self.lines = []
        self.posts = []
        self.refuse = None

    def post(self, session, text):
        self.posts.append((session, text))
        return self.refuse

    def run_router(self, prs=(), issues=(), send=True, when=NOW, only=None,
                   remind_s=r.DEFAULT_REMIND_S):
        return r.run(FakeGitHub(response(list(prs), list(issues))), ROSTER,
                     self.dir, send, self.post, only, self.lines.append,
                     when=when, remind_s=remind_s)

    def state(self):
        with open(os.path.join(self.dir, "state.json")) as handle:
            return json.load(handle)

    def wakes(self):
        with open(os.path.join(self.dir, "wakes.jsonl")) as handle:
            return [json.loads(line) for line in handle]


class Wakes(StateCase):

    def test_one_message_per_agent_listing_its_items(self):
        prs = [pr_node(41), pr_node(43),
               pr_node(44, statuses=[status("FAILURE")])]
        issues = [issue_node(22, labels=["Status:waiting-owner"],
                             comments=[comment(OWNER, T2, cid=6088707208)])]
        self.assertEqual(self.run_router(prs, issues), 0)
        self.assertEqual(self.posts, [
            ("WidgetLeadDev", "From: Router\nYour turn:\n"
             "#22 example-owner answered (comment 6088707208)\n"
             "#44 vm/build-and-test is failure at 5b257c8\n"),
            ("WidgetReviewer", "From: Router\nYour turn:\n"
             "#41 needs your review (green at 5b257c8)\n"
             "#43 needs your review (green at 5b257c8)\n")])
        self.assertEqual(self.state()["items"]["41"], {
            "agent": "Reviewer", "rule": 8, "event": T1,
            "woken_at": "2026-10-09T14:00:00Z"})
        self.assertEqual([(w["item"], w["outcome"]) for w in self.wakes()],
                         [(22, "sent"), (44, "sent"), (41, "sent"),
                          (43, "sent")])
        self.assertIn("ROUTED 4 open, 4 turn(s) to wake, 1 waiting on example-owner",
                      self.lines[-1])

    def test_once_per_turn(self):
        self.run_router([pr_node(41)])
        self.run_router([pr_node(41)], when=NOW + datetime.timedelta(
            minutes=2))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(len(self.wakes()), 1)

    def test_a_new_event_is_a_new_turn(self):
        self.run_router([pr_node(41)])
        self.run_router([pr_node(41, comments=[comment(LEADDEV, T3)])],
                        when=NOW + datetime.timedelta(minutes=2))
        self.assertEqual(len(self.posts), 2)
        self.assertNotIn("(reminder)", self.posts[1][1])

    def test_a_changed_agent_is_a_new_turn(self):
        self.run_router([pr_node(41, statuses=[status("FAILURE", T1)])])
        self.run_router([pr_node(41, statuses=[status("SUCCESS", T1)])],
                        when=NOW + datetime.timedelta(minutes=2))
        self.assertEqual([p[0] for p in self.posts],
                         ["WidgetLeadDev", "WidgetReviewer"])

    def test_another_rule_for_the_same_event_is_the_same_turn(self):
        # The owner answers on a waiting-owner issue (rule 10), then
        # takes the label off, so rule 12 matches the same comment.
        answer = comment(OWNER, T2, cid=6088707208)
        self.run_router(issues=[issue_node(
            22, labels=["Status:waiting-owner", "Owner:LeadDev"],
            comments=[answer])])
        self.run_router(issues=[issue_node(
            22, labels=["Owner:LeadDev"], comments=[answer])],
            when=NOW + datetime.timedelta(minutes=2))
        self.assertEqual(len(self.posts), 1)
        self.assertEqual(self.state()["items"]["22"]["rule"], 10)

    def test_a_reminder_after_the_interval(self):
        self.run_router([pr_node(41)], remind_s=600)
        self.run_router([pr_node(41)], remind_s=600,
                        when=NOW + datetime.timedelta(seconds=599))
        self.assertEqual(len(self.posts), 1)
        later = NOW + datetime.timedelta(seconds=600)
        self.run_router([pr_node(41)], remind_s=600, when=later)
        self.assertEqual(len(self.posts), 2)
        self.assertIn("#41 needs your review (green at 5b257c8) (reminder)",
                      self.posts[1][1])
        self.assertEqual(self.state()["items"]["41"]["woken_at"],
                         "2026-10-09T14:10:00Z")

    def test_the_default_reminder_is_two_hours(self):
        self.assertEqual(r.DEFAULT_REMIND_S, 7200)

    def test_a_turn_for_someone_not_an_agent_is_withheld_once(self):
        prs = [pr_node(5, author=OWNER, statuses=[status("FAILURE")])]
        self.run_router(prs)
        self.run_router(prs, when=NOW + datetime.timedelta(hours=3))
        self.assertEqual(self.posts, [])
        self.assertEqual(
            [(w["item"], w["agent"], w["outcome"]) for w in self.wakes()],
            [(5, OWNER, "withheld: not an agent")])
        self.assertTrue(self.state()["items"]["5"]["withheld"])

    def test_rule_7_is_printed_even_when_sending(self):
        self.run_router([pr_node(9, author=REVIEWER)])
        self.assertIn("#9 nobody's (rule 7: green, and Reviewer's own: "
                      "no second reviewer)", self.lines)

    def test_a_turn_that_ends_is_forgotten(self):
        self.run_router([pr_node(41)])
        self.run_router([pr_node(41, draft=True)],
                        when=NOW + datetime.timedelta(minutes=2))
        self.assertNotIn("41", self.state()["items"])

    def test_a_closed_item_is_forgotten(self):
        self.run_router([pr_node(41)])
        self.run_router([], when=NOW + datetime.timedelta(minutes=2))
        self.assertEqual(self.state()["items"], {})


class FailedRelay(StateCase):

    def test_a_refused_wake_stays_due_and_nothing_is_recorded_as_woken(self):
        self.refuse = "HTTP 404 unknown-name"
        self.assertEqual(self.run_router([pr_node(41)]), 1)
        self.assertEqual(self.state()["items"], {})
        (wake,) = self.wakes()
        self.assertEqual(wake["outcome"], "not sent: HTTP 404 unknown-name")
        self.assertIn("NOT SENT to WidgetReviewer: HTTP 404 unknown-name; "
                      "#41 stay due", self.lines)
        self.refuse = None
        self.assertEqual(self.run_router(
            [pr_node(41)], when=NOW + datetime.timedelta(minutes=2)), 0)
        self.assertEqual(len(self.posts), 2)
        self.assertNotIn("(reminder)", self.posts[1][1])
        self.assertIn("woken_at", self.state()["items"]["41"])

    def test_one_agents_failure_does_not_hold_anothers(self):
        def post(session, text):
            self.posts.append(session)
            return "HTTP 503 queue-full" if session == "WidgetLeadDev" \
                else None
        self.post = post
        prs = [pr_node(41), pr_node(44, statuses=[status("FAILURE")])]
        self.assertEqual(self.run_router(prs), 1)
        self.assertEqual(set(self.state()["items"]), {"41"})


class Only(StateCase):

    def test_only_routes_the_named_items_and_keeps_the_rest(self):
        self.run_router([pr_node(41)])
        prs = [pr_node(41, comments=[comment(LEADDEV, T3)]), pr_node(43),
               pr_node(44, statuses=[status("FAILURE")])]
        self.run_router(prs, only={43},
                        when=NOW + datetime.timedelta(minutes=1))
        self.assertEqual(self.posts[1], (
            "WidgetReviewer", "From: Router\nYour turn:\n"
            "#43 needs your review (green at 5b257c8)\n"))
        self.assertEqual(len(self.posts), 2)
        self.assertEqual(set(self.state()["items"]), {"41", "43"})
        self.assertEqual(self.state()["items"]["41"]["event"], T1)

    def test_parse_only(self):
        self.assertEqual(r.parse_only("41"), {41})
        self.assertEqual(r.parse_only("41,22, 7"), {41, 22, 7})
        for bad in ("", "x", "4,y", "0", "-3"):
            with self.assertRaises(Exception, msg=bad):
                r.parse_only(bad)


class DryRun(StateCase):

    def dry(self):
        path = os.path.join(self.dir, "dry-run.jsonl")
        if not os.path.exists(path):
            return []
        with open(path) as handle:
            return [json.loads(line) for line in handle]

    def test_a_dry_run_sends_nothing_and_leaves_state_alone(self):
        prs = [pr_node(41), pr_node(5, draft=True)]
        self.assertEqual(self.run_router(prs, send=False), 0)
        self.assertEqual(self.posts, [])
        self.assertEqual(sorted(os.listdir(self.dir)),
                         ["dry-run.jsonl", "dry-state.json"])
        self.assertIn("#5 nobody's (rule 1: draft)", self.lines)
        self.assertIn("WOULD SEND to WidgetReviewer:\nFrom: Router\n"
                      "Your turn:\n#41 needs your review (green at 5b257c8)",
                      self.lines)
        self.assertTrue(self.lines[-1].startswith("DRY-RUN 2 open"))

    def test_a_dry_run_records_each_turn_once_per_key(self):
        prs = [pr_node(41), pr_node(5, author=OWNER,
                                    statuses=[status("FAILURE")])]
        self.run_router(prs, send=False)
        self.run_router(prs, send=False,
                        when=NOW + datetime.timedelta(hours=3))
        self.assertEqual(
            [(w["item"], w["agent"], w["rule"], w["outcome"], w["at"])
             for w in self.dry()],
            [(5, OWNER, 4, "withheld: not an agent", "2026-10-09T14:00:00Z"),
             (41, "Reviewer", 8, "would send to WidgetReviewer",
              "2026-10-09T14:00:00Z")])
        self.assertEqual(self.dry()[1]["line"],
                         "#41 needs your review (green at 5b257c8)")
        # A new event is a new key, and is recorded again.
        self.run_router([pr_node(41, comments=[comment(LEADDEV, T3)])],
                        send=False, when=NOW + datetime.timedelta(hours=4))
        self.assertEqual(len(self.dry()), 3)
        self.assertEqual(self.dry()[2]["event"], T3)

    def test_a_dry_run_forgets_a_turn_that_ended(self):
        self.run_router([pr_node(41)], send=False)
        self.run_router([pr_node(41, draft=True)], send=False)
        self.run_router([pr_node(41)], send=False)
        self.assertEqual(len(self.dry()), 2)

    def test_a_dry_run_records_what_is_already_woken(self):
        self.run_router([pr_node(41)])
        self.run_router([pr_node(41)], send=False)
        self.assertEqual([w["outcome"] for w in self.dry()],
                         ["withheld: already woken"])

    def test_a_dry_run_reads_the_state_but_does_not_change_it(self):
        self.run_router([pr_node(41)])
        before = pathlib.Path(self.dir, "state.json").read_text()
        self.run_router([pr_node(41), pr_node(43)], send=False)
        self.assertIn("#41 Reviewer's, rule 8: already woken", self.lines)
        self.assertEqual(pathlib.Path(self.dir, "state.json").read_text(),
                         before)
        self.assertEqual(len(self.wakes()), 1)
        self.assertFalse(os.path.exists(os.path.join(self.dir, "lock")))


class Poster(unittest.TestCase):

    class Response:
        def __init__(self, code):
            self.status = code

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    def post(self, answer, token="s3cret", relay="http://relay:8788/"):
        seen = []

        def opener(req, timeout):
            seen.append(req)
            if isinstance(answer, Exception):
                raise answer
            return self.Response(answer)
        got = r.make_poster(relay, token, opener)("WidgetLeadDev", "From: x")
        return got, seen

    def http_error(self, code, reason):
        body = json.dumps({"error": "x", "reason": reason}).encode()
        return urllib.error.HTTPError("u", code, "m", {}, io.BytesIO(body))

    def test_the_request_is_the_relays_verbatim_agent_post(self):
        got, (req,) = self.post(200)
        self.assertIsNone(got)
        self.assertEqual(req.full_url, "http://relay:8788/agent/WidgetLeadDev")
        self.assertEqual(req.get_method(), "POST")
        self.assertEqual(req.data, b"From: x")
        headers = {k.lower(): v for k, v in req.header_items()}
        self.assertEqual(headers["x-webhook-token"], "s3cret")
        self.assertEqual(headers["x-xmpp-verbatim"], "1")
        self.assertTrue(headers["content-type"].startswith("text/plain"))

    def test_only_200_counts(self):
        self.assertEqual(self.post(202)[0], "HTTP 202")
        self.assertEqual(self.post(self.http_error(404, "unknown-name"))[0],
                         "HTTP 404 unknown-name")
        self.assertEqual(self.post(self.http_error(409, "ambiguous-name"))[0],
                         "HTTP 409 ambiguous-name")
        self.assertEqual(self.post(self.http_error(503, "queue-full"))[0],
                         "HTTP 503 queue-full")

    def test_no_connection(self):
        got, _ = self.post(urllib.error.URLError("refused"))
        self.assertEqual(got, "relay unreachable: refused")

    def test_no_token_sends_nothing(self):
        got, seen = self.post(200, token=None)
        self.assertIn("no relay token", got)
        self.assertEqual(seen, [])

    def test_the_token_never_reaches_a_reason(self):
        for answer in (500, self.http_error(401, None),
                       urllib.error.URLError("s3cret? no")):
            got, _ = self.post(answer, token="s3cret")
            self.assertNotIn("s3cret", got.replace("s3cret? no", ""))

    def test_relay_token_reads_the_environment_file(self):
        with tempfile.NamedTemporaryFile("w", delete=False) as handle:
            handle.write("# relay\nWEBHOOK_HOST=x\nWEBHOOK_TOKEN='abc'\n")
        self.addCleanup(os.unlink, handle.name)
        self.assertEqual(r.relay_token(handle.name), "abc")
        self.assertIsNone(r.relay_token("/nonexistent/token"))


class Lock(unittest.TestCase):

    def test_gives_up_after_the_wait(self):
        import fcntl
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "lock")
            with open(path, "a") as held, open(path, "a") as other:
                fcntl.flock(held, fcntl.LOCK_EX)
                clock = iter(range(0, 1000, 30))
                self.assertFalse(r.take_lock(other, 60, lambda s: None,
                                             lambda: next(clock)))
                fcntl.flock(held, fcntl.LOCK_UN)
                self.assertTrue(r.take_lock(other, 60, lambda s: None))


class Main(unittest.TestCase):

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = os.path.join(tmp.name, "router")
        self.checkout = os.path.join(tmp.name, "cron")
        os.makedirs(os.path.join(self.checkout, ".agent-forge"))
        self.write_config(example())
        self.environ = {"HOME": tmp.name,
                        "FORGE_CRON_CHECKOUT": self.checkout}
        self.lines = []
        self.addCleanup(r.configure, config.Project(example(), None))

    def write_config(self, data):
        with open(os.path.join(self.checkout, config.CONFIG_NAME), "w") as f:
            json.dump(data, f)

    def mint(self, token="token"):
        def mint(project, agent):
            self.assertEqual((project.name, agent), ("widget", "Merger"))
            return token
        return mint

    def main(self, argv, mint="token", run=None, environ=None, state=True):
        calls = []

        def fake_run(github, roster, state_dir, send, post, only, out,
                     remind_s):
            calls.append((github.env["GH_TOKEN"], state_dir, send, only,
                          remind_s))
            return run() if run else 0
        with mock.patch.object(r, "run", fake_run):
            rc = r.main(argv + (["--state-dir", self.dir] if state else []),
                        None, self.lines.append, post=lambda s, t: None,
                        roster=ROSTER,
                        environ=dict(self.environ, **(environ or {})),
                        mint=self.mint(mint))
        return rc, calls

    def test_a_dry_run_by_default_takes_no_lock(self):
        rc, calls = self.main([])
        self.assertEqual((rc, calls),
                         (0, [("token", self.dir, False, None, 7200)]))
        self.assertFalse(os.path.exists(self.dir))

    def test_send_and_only(self):
        rc, calls = self.main(["--send", "--only", "41,22"])
        self.assertEqual(calls, [("token", self.dir, True, {41, 22}, 7200)])
        self.assertTrue(os.path.exists(os.path.join(self.dir, "lock")))

    def test_the_reminder_comes_from_the_environment(self):
        self.assertEqual(self.main([], environ={
            "FORGE_ROUTER_REMIND": "60"})[1][0][4], 60)
        self.assertEqual(self.main([], environ={
            "FORGE_ROUTER_REMIND": "soon"}), (2, []))

    def test_the_reminder_and_relay_come_from_the_configuration(self):
        data = example()
        data["relay"] = {"url": "http://relay:1", "remind_seconds": 99,
                         "token_file": "~/t"}
        self.write_config(data)
        self.assertEqual(self.main([])[1][0][4], 99)
        self.assertEqual((r.RELAY, r.TOKEN_FILE),
                         ("http://relay:1", os.path.expanduser("~/t")))

    def test_the_owner_and_labels_come_from_the_configuration(self):
        self.main([])
        self.assertEqual((r.OWNER, r.WAITING_OWNER, r.WAITING_EXTERNAL,
                          r.OWNER_PREFIX),
                         ("example-owner", "Status:waiting-owner",
                          "Status:waiting-external", "Owner:"))

    def test_no_token(self):
        self.assertEqual(self.main(["--send"], mint=None), (2, []))
        self.assertEqual(self.lines, ["NO-TOKEN: could not mint Merger's token"])

    def test_no_configuration(self):
        rc = r.main([], None, self.lines.append, environ={})
        self.assertEqual(rc, 2)
        self.assertIn("NO-CONFIG", self.lines[0])

    def test_a_failed_wake_exits_one(self):
        self.assertEqual(self.main(["--send"], run=lambda: 1)[0], 1)

    def test_github_failing_exits_two(self):
        with mock.patch.object(r.merger, "GitHub",
                               lambda token, sh: FailingGitHub()):
            rc = r.main(["--state-dir", self.dir], None, self.lines.append,
                        roster=ROSTER, environ=self.environ,
                        mint=self.mint())
        self.assertEqual(rc, 2)
        self.assertTrue(self.lines[-1].startswith("FAILED: gh api graphql"))

    def test_busy_exits_zero(self):
        with mock.patch.object(r, "take_lock", lambda handle: False):
            rc, calls = self.main(["--send"])
        self.assertEqual((rc, calls), (0, []))
        self.assertTrue(self.lines[0].startswith("BUSY: "))

    def test_the_state_directory_defaults_under_the_home(self):
        _, calls = self.main([], state=False)
        self.assertEqual(calls[0][1], os.path.join(
            self.environ["HOME"], ".local", "state", "agent-forge", "widget",
            "router"))


if __name__ == "__main__":
    unittest.main()
