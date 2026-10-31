"""The default rules for `forge-router`: whose turn each open item is.

`router.rules: "default"` in the configuration chooses these, and so
does leaving it out. The engine, bin/forge-router, fetches, delivers
and keeps state; this says whose turn an item is, from GitHub alone.
docs/router.md is the interface between the two.

The labels are the configuration's `labels`: `owner_prefix` (default
`Owner:`), `status_prefix` (default `Status:`), `waiting_on_owner`
(default `Status:waiting-owner`) and `waiting_external` (default
`Status:waiting-external`). "The owner" is the person whose login is
the configuration's `owner`.

**Whose turn it is.** The first rule that matches decides; none means
nobody's.

With `router.quiet_labels`, an open item with one of those labels is
nobody's under every rule below, suppressed `quiet: <label>` (the
first of the list it has): no turn, no reminder, no offer of it (rule
15) and no notice of it (rule 19). Off without the key.

Pull requests:

With `router.stuck` in the configuration, three rules come first, so
they apply to drafts and waiting pull requests too, each off until its
key is set (designs/0004). Each is the author's turn, and is said once
for its event, never reminded:

0a. `retarget`: its base is not the default branch (the Merger's), and
    no open pull request has that base as its head branch: what it was
    stacked on has merged or closed, or never was. "Retarget it". The
    event is the base, so it is said once until the base changes. A
    base whose open pull requests couldn't be read wakes nobody;
0b. `self_blocked`: a `Blocked-by: #N` line (the Merger's notation, as
    for rule 14) names one of its closing references
    (`closingIssuesReferences`: `Closes #N` and the like). #N stays
    open until it merges, so the Merger can never take it. "Replace the
    line with `Closes #N`". The event is the body's last edit;
0c. `draft_hours: H`: a draft with no new commit (the head's commit
    date), no body edit and no comment or review by its author for H
    hours, that records no wait: no `Blocked-by:` line naming an open
    item (a number naming nothing is left out, as for rule 14), no
    newest `Defer-until:` (as the Merger reads it) whose time is still
    to come (one that is not a time is no wait here), and no label
    starting with the labels' `status_prefix` and `waiting-`
    (`Status:waiting-*`). "Un-draft it, or record what it waits for".
    The event is that last activity, so it is asked once per idle
    stretch.

Then:

1. a draft, or waiting on the owner or on someone outside: nobody's;
2. a Merger hand-back of the current head is the newest thing said (a
   comment by the Merger, "Not merging <head>:", newer than every review
   and every author comment): the approving reviewer's if it starts
   `Not-a-review`, otherwise the author's;
3. a request for changes stands (a reviewer's or the owner's newest
   verdict, `APPROVED`, `CHANGES_REQUESTED` or `DISMISSED`, is
   `CHANGES_REQUESTED`, as `forge-merger` reads it) and the author has
   not answered it: no author comment after it, and only merges since
   its commit (it covers the head). Or a reviewer's newest review is
   `COMMENTED`, at the head, with no author comment after it. Either is
   the author's. A request stands until its reviewer's next verdict,
   so neither the Merger's merges of main nor another reviewer's
   approval lift it;
4. the build check's status (`checks.context`) is `failure` or `error`
   on the head: the author's;
5. it is `pending` or absent on the head: nobody's;
6. no request for changes stands, a reviewer's newest review is
   `APPROVED`, covers the head, and nothing has been said after it (by
   `forge-merger`'s own test): nobody's, it is the Merger's;
7. green and authored by the only reviewer: nobody's, and surfaced
   (printed on every run, sending or not: it is a problem in the
   configuration, not anyone's turn);
8. green, and a request for changes stands that the author has
   answered: the reviewer's who asked, to review again (nobody's when
   the owner asked). Or green, a reviewer's newest review is an
   approval covering the head, and someone other than the author said
   something after it that holds it (`forge-merger`'s test again): the
   approver's, to approve again or answer it. Otherwise green, and no
   reviewer has reviewed it: no reviewer's review at the head (or
   approval covering it), or an author comment after the newest one:
   the pull request's reviewer's.

**A reviewer** is an agent the configuration marks `"kind":
"reviewer"`. **A pull request's reviewer** is the one, other than its
author, whose `reviews` path globs match the most changed lines; a tie,
and lines nobody's globs match, go to the reviewer listed first.

Issues:

9. waiting on someone outside: nobody's;
10. waiting on the owner, and a comment by the owner newer than the
    item owner's last comment: the item owner's (`Owner:<Name>`, else
    the issue's author), because the owner has answered. A comment by
    the owner's login whose last non-empty line is `— <Name>`, for any
    agent or machinery App in the configuration, is an agent writing
    under the owner's login, not the owner;
11. waiting on the owner otherwise: nobody's;
12. `Owner:<Name>` and a comment by anyone but Name newer than Name's
    last comment: Name's;
13. no `Owner:` label, and a comment by the owner newer than the issue
    author's last comment: the author's, with rule 10's exception. The
    owner usually takes the waiting label off when answering, which
    ends rule 10;
14. its body has `Blocked-by: #N` lines (one number a line, the
    Merger's notation, outside code), every item they name is closed or
    merged, and the last of those closed after the body was last
    written and after the item owner's last comment: the item owner's
    (`Owner:<Name>`, else the author), "unblocked". The event is that
    last close, so it is woken once. A merged pull request is nobody's
    turn, so without this a step waiting on others' work waits until
    someone notices. A line written after its item closed doesn't
    wake, and a number that names nothing is left out. Pull requests
    don't need it: the Merger holds a blocked one and takes it up
    itself. What the named items are now is the `blockers` lookup.

With `router.red_first` (designs/0039), a red-build issue is one a
build tool on this host keeps open while a build is red: forge-nightly's
(`issue` in its nightly.json, while `issue_open` and the file is
fresh) and forge-pr-check's (`main_red.issue` in its state.json). Its
**red event** is when its build was last reported red: for the
nightly, the end of its newest red run, else when the issue was
opened; for the default branch, the issue's newest comment by the
build check's poster, else its creation. It is **in hand** when an
open pull request, by anyone, references it (as rule 15 reads a
reference). It is **parked** when it has a quiet label or a label
starting `Status:waiting-` (the labels' `status_prefix`). The
`red builds` lookup reads the tools' state. Off without the key:

20. after rules 9-11 and 18, and before rule 12: an open red-build
    issue with an owner (the item owner), not parked, not in hand, on
    which the owner hasn't commented since its red event: the owner's,
    "red builds go first", reminded as any turn. The event is the red
    event, so a new red run wakes her again; her comment ends it, and
    the rules below then apply.

With `router.idle_offer` in the configuration, a run also sweeps for
agents with nothing to do (off without it):

15. an agent the relay's directory shows idle (status `idle`, every
    entry of its session name, for at least `idle_minutes`, default 10;
    with `shell_minutes` set, an entry `shell` for at least that long
    counts too: its turn has ended and only a background task it started
    still runs), with no turn from rules 0a-14, which owns an actionable issue: the
    agent's, offered the oldest such issue by creation. An issue is
    actionable when it is labelled `Owner:<Name>`, is waiting neither
    on the owner nor on someone outside, every item its `Blocked-by:`
    lines name is closed or merged (a number naming nothing is left
    out, as for rule 14), and no open pull request by the agent
    references it (a closing reference, `#N` or its URL in the body).
    With `router.red_first`, red-build issues come first (oldest first
    among themselves), and the offer says "Red builds go first".
    The event is when the idle period began, and it is about no item,
    so it is offered once per idle period, never reminded, and the
    next idle period offers again. An agent idle through `shell` is
    also told since when its background task has been running;
16. an idle developer (not a reviewer or a collaborator) with no such
    turn and nothing actionable: `idle_offer.report_to`'s, so whoever
    plans the work knows, once per idle period. A collaborator works
    on the project only when given something: idle with nothing of
    hers to do is where she is meant to be. Not for `report_to` itself, and not
    without `report_to`. With `idle_offer.hand_over_above` (designs/0038),
    the report also names the agent owning the most unstarted issues,
    when that is more than `hand_over_above`, and asks for one of them
    to be handed to the idle developer: an issue is unstarted when it is
    actionable, as above, and isn't labelled `in-progress` (after the
    labels' `status_prefix`). The most is the larger count, then the
    owner of the oldest such issue; the list is oldest first, at most
    ten.

An agent whose entry says `"offer": false` is never offered work by
rule 15 nor reported by rule 16.

With `router.red_first` as well (designs/0039; off without either):

21. an open red-build issue, not parked and not in hand, whose red
    event is at least `red_first.fallback_hours` old and whose owner
    hasn't commented on it for as long: the idle developer (as rule 16
    reads one) idle longest, the name breaking a tie, other than its
    owner or her stand-in and with no turn from rules 0a-14 and 18-20,
    is offered it, "take it". One issue a developer, and she is then
    offered nothing by rules 15 and 16 that run. The event is the issue
    and its red event, so it is said once to her for each red event,
    never reminded. With no such developer nothing is said.

A directory that couldn't be read, or isn't complete, makes nobody
idle: unknown is never idle.

With `router.merge_notice`, a run also tells authors of their merges
(off without it):

17. a pull request by an agent merged in the last `window_minutes`
    (default 30): its author's, "merged", naming any open issue it
    leaves with every `Blocked-by:` item closed (whose owners rule 14
    wakes in their own right) and the author's oldest actionable issue
    (as rule 15 reads it), so an author learns their work landed and
    what to do next. The event is the pull request and when it merged,
    so each merge is told once and never reminded. Nothing is said
    when the merges couldn't be read: unknown is never "nothing
    merged". With `merge_notice.skip_label`, a pull request merged with
    that label tells nobody. With `merge_notice.to: "owner_label"`, the
    notice goes to the agent its `Owner:<Name>` label names, else, with
    none, to its author; a name that isn't an agent is withheld (and
    journalled) by the engine, as every wake is. Either adds `labels`
    to the merged search.

With `router.owners` (designs/0008), "the item owner" in rules 10, 12,
14 and 15 is the agent an `Owner:<Name>` label names, else the author
when an agent, else nobody; a label naming no agent is passed over.
Rule 13 then never applies, and an issue with no owner, past rule 9 and
not waiting on the owner, is rule 18's:

18. an unowned issue: `owners.triage`'s turn, "label it", or surfaced
    without `triage`. The event is the later of its creation and its
    `Owner:` labels' last change, so it is said once and said again if
    its label is taken off.

With `router.unreviewed` (designs/0027), which needs `owners.triage`,
a run that isn't `--only`'s also sweeps for paths no reviewer covers
(off without it):

19. an open pull request, not a draft, changes paths that match no
    reviewer's `reviews` glob (by the reviewer choice's
    `fnmatchcase`, which still gives their lines to the first
    reviewer) and no `unreviewed.ignore` glob. They are grouped by
    area: a path's first segment, or its first two under a directory
    `unreviewed.depth2` lists, and `/` for a file at the top. For an
    area with no open issue (the one `unreviewed.json` records, else
    one titled for it), `triage`'s turn, "give it a reviewer", whose
    action opens one, titled `No reviewer covers <area> (first seen on
    #N)`, listing the pull requests and their paths and labelled
    `Owner:<triage>`. With one open, the pull requests not yet on it
    are added to it in a comment, surfaced. Either way each pull
    request is told once, in a `Not-a-review` comment naming the
    issue. While open pull requests still change the area, the issue
    is reminded of them in a comment once a day, surfaced, on a day
    nothing else was written to it. `unreviewed.json`, in the router's
    state directory, records each area's issue, the pull requests told
    and the day it last wrote, as each write is done. The event has
    the day in it, so a write GitHub refused is tried again the next
    day.

**Who wrote something is read from its login**, never its text (but
for rule 10's exception): an App's bot login (`<slug>[bot]`) maps to
its name through the configuration, the Merger's under `machinery`,
and the `owner` login is the owner. What a review saw is read from the
commit it was made at, never a clock: a review is at the head when its
commit is the head, and an approval covers the head when its commit is
the head or every commit after it on the pull request is a merge (two
parents). An author's comment is an issue comment or a review by the
author's login. Only a status the poster's App posted counts, as for
the Merger.

**The event** that keys a turn is the time of what started it (a
comment, a review, a status, the last blocker's close), so rules 10, 12
and 13, which all mean "you were answered", are one turn for one
comment.

Each rule is written as `route_pr` or `route_issue` returning the dict
`turn` or `nobody` builds; `decide` hands the engine that as a
`Decision`.
"""

import dataclasses
import datetime
import fnmatch
import os
import re

from agent_forge import locks, nightly, state
from agent_forge.router import ONCE, Action, Decision, Lookup

# The Merger's hand-back names the head it judged, in both its forms.
HAND_BACK_RE = re.compile(r"^Not merging ([0-9a-f]{7,40}):", re.MULTILINE)

# bin/forge-merger, given by `configure`: its own test of "nothing said
# since the approval" and its Blocked-by reading, so the two never
# disagree.
merger = None

# Set from the project's configuration by `configure`.
MERGER = "Merger"
OWNER = ""
WAITING_OWNER = "Status:waiting-owner"
WAITING_EXTERNAL = "Status:waiting-external"
OWNER_PREFIX = "Owner:"
STATUS_CONTEXT = "vm/build-and-test"
# `router.idle_offer`, with its defaults filled in, or None: off.
IDLE_OFFER = None
# A label saying its issue is being worked on (designs/0038).
IN_PROGRESS = "Status:in-progress"
# How many unstarted issues rule 16 names at most (designs/0038).
HAND_OVER_LIST = 10
DEFAULT_IDLE_MINUTES = 10
# The engine reads the relay's directory only for the idle offer.
wants_directory = False
# `router.merge_notice`, with its defaults filled in, or None: off.
MERGE_NOTICE = None
DEFAULT_MERGE_WINDOW = 30
# `router.quiet_labels`: labels that make an open item nobody's.
QUIET_LABELS = ()
# How far back, in minutes, the engine reads merges: 0, none.
merged_window = 0
# `router.stuck`: rules 0a, 0b and 0c, each off until set.
RETARGET = False
SELF_BLOCKED = False
# Hours a draft sits before it is asked about, or None: off.
DRAFT_HOURS = None
# A label recording what a draft waits for starts with this.
WAITING_PREFIX = "Status:waiting-"
# `router.owners` (designs/0008), or None: off. `triage` is the agent
# an unowned issue goes to, or None: surfaced.
OWNERS = None
# `router.unreviewed` (designs/0027), {"ignore", "depth2"}, or None: off.
UNREVIEWED = None
# `router.red_first` (designs/0039), {"fallback_hours"}, or None: off.
RED_FIRST = None
DEFAULT_FALLBACK_HOURS = 2
# The project, from `configure`: whose tools' state the red-build lookup
# reads.
PROJECT = None
# The environment that lookup finds those state directories by; None:
# the process's.
ENVIRON = None

# What the rules read of each open item, besides its number.
graphql = {
    "PullRequest": """isDraft headRefOid author { login } body
        baseRefName headRefName createdAt lastEditedAt
        closingIssuesReferences(first:20) { nodes { number } }
        labels(first:30) { nodes { name } }
        files(first:100) { nodes { path additions deletions } }
        reviews(last:100) {
          nodes { databaseId author { login } state submittedAt body
                  commit { oid } comments { totalCount } }
        }
        comments(last:100) {
          nodes { databaseId author { login } createdAt body }
        }
        commits(last:100) {
          nodes { commit { oid parents { totalCount } } }
        }
        headCommit: commits(last:1) {
          nodes { commit { oid committedDate
            status { contexts { context state createdAt
                                creator { login } } } } }
        }""",
    "Issue": """title author { login } body createdAt lastEditedAt
        labels(first:30) { nodes { name } }
        comments(last:100) {
          nodes { databaseId author { login } createdAt body }
        }""",
}
ISSUE_FIELDS = graphql["Issue"]
# With `merge_notice.skip_label` or `to: "owner_label"`: a merged pull
# request's labels.
MERGED_LABELS = "labels(first:30) { nodes { name } }"
# With `router.owners`: when labels went on and off, for rule 18's event.
OWNER_LABEL_EVENTS = """
        ownerLabels: timelineItems(last:30,
                                   itemTypes:[LABELED_EVENT, UNLABELED_EVENT]) {
          nodes { ... on LabeledEvent { createdAt label { name } }
                  ... on UnlabeledEvent { createdAt label { name } } }
        }"""
# Read as lists; `commits` and `headCommit` keep their nodes.
flatten = ("labels", "reviews", "comments", "files")


def configure(project, merger_tool):
    """Set the rules' settings from `project`. `merger_tool`, already
    configured for it, is bin/forge-merger."""
    global merger, MERGER, OWNER, WAITING_OWNER, WAITING_EXTERNAL
    global OWNER_PREFIX, STATUS_CONTEXT, IDLE_OFFER, wants_directory
    global MERGE_NOTICE, merged_window
    global RETARGET, SELF_BLOCKED, DRAFT_HOURS, WAITING_PREFIX, OWNERS
    global UNREVIEWED, QUIET_LABELS, IN_PROGRESS, RED_FIRST, PROJECT
    merger = merger_tool
    PROJECT = project
    labels = project.section("labels")
    MERGER = merger.ACT_AS
    OWNER = project.owner.lower()
    WAITING_OWNER = labels.get("waiting_on_owner", "Status:waiting-owner")
    WAITING_EXTERNAL = labels.get("waiting_external",
                                  "Status:waiting-external")
    OWNER_PREFIX = labels.get("owner_prefix", "Owner:")
    STATUS_CONTEXT = merger.STATUS_CONTEXT
    offer = project.section("router").get("idle_offer")
    IDLE_OFFER = None if offer is None else {
        "idle_minutes": offer.get("idle_minutes", DEFAULT_IDLE_MINUTES),
        "shell_minutes": offer.get("shell_minutes"),
        "report_to": offer.get("report_to"),
        "hand_over_above": offer.get("hand_over_above")}
    IN_PROGRESS = labels.get("status_prefix", "Status:") + "in-progress"
    wants_directory = IDLE_OFFER is not None
    notice = project.section("router").get("merge_notice")
    MERGE_NOTICE = None if notice is None else {
        "window_minutes": notice.get("window_minutes",
                                     DEFAULT_MERGE_WINDOW),
        "skip_label": notice.get("skip_label"),
        "to": notice.get("to", "author")}
    merged_window = MERGE_NOTICE["window_minutes"] if MERGE_NOTICE else 0
    if MERGE_NOTICE and (MERGE_NOTICE["skip_label"]
                         or MERGE_NOTICE["to"] == "owner_label"):
        graphql["Merged"] = MERGED_LABELS
    else:
        graphql.pop("Merged", None)
    QUIET_LABELS = tuple(project.section("router").get("quiet_labels")
                         or ())
    stuck = project.section("router").get("stuck") or {}
    RETARGET = stuck.get("retarget") is True
    SELF_BLOCKED = stuck.get("self_blocked") is True
    DRAFT_HOURS = stuck.get("draft_hours")
    WAITING_PREFIX = labels.get("status_prefix", "Status:") + "waiting-"
    owners = project.section("router").get("owners")
    OWNERS = None if owners is None else {"triage": owners.get("triage")}
    gaps = project.section("router").get("unreviewed")
    UNREVIEWED = None if gaps is None else {
        "ignore": list(gaps.get("ignore") or []),
        "depth2": list(gaps.get("depth2") or [])}
    graphql["Issue"] = ISSUE_FIELDS + (OWNER_LABEL_EVENTS if OWNERS else "")
    red = project.section("router").get("red_first")
    RED_FIRST = None if red is None else {
        "fallback_hours": red.get("fallback_hours", DEFAULT_FALLBACK_HOURS)}


# --- who is who -------------------------------------------------------------


class Roster:
    """The configuration's Apps: names to logins and session names, and
    back. Only `agents` are woken; machinery (the Merger) is known by
    login and signature but is never anyone's turn."""

    def __init__(self, project):
        self.project = project
        self.agents = project.agents
        self.machinery = project.machinery
        self.by_login = {self.slug(n): n
                         for n in list(self.agents) + list(self.machinery)}

    def slug(self, name):
        return self.project.slug(name)

    def who(self, login):
        """The name a login maps to, or the login itself."""
        login = strip_bot(login)
        return self.by_login.get(login, login or "ghost")

    def login_of(self, who):
        """The login, without "[bot]", of a name or of a plain login."""
        if who in self.agents or who in self.machinery:
            return self.slug(who)
        return (who or "").lower()

    def is_agent(self, who):
        return who in self.agents

    def signatures(self):
        """Every sign-off line an agent or the Merger ends a comment
        with."""
        return {"— " + n for n in list(self.agents) + list(self.machinery)}

    def session_name(self, who):
        return self.project.session_name(who)

    def sessions(self, who):
        """Where her turns go: her session, then her twins', or her
        stand-in's when she is `away`."""
        return self.project.sessions(self.stand_in(who))

    def stand_in(self, who):
        """Who works for her: her `away` stand-in, else herself."""
        return self.project.stand_in(who) if self.is_agent(who) else who

    def reviewers(self):
        """Every agent of kind `reviewer`, in the configuration's order."""
        return self.project.reviewers()

    def areas(self, who):
        """Her `areas`, {"paths": [globs], "labels": [names]}, or None
        without them. The default rules don't read them: a module's
        offer by area does."""
        return self.project.areas(who)

    def offered(self, who):
        """Whether she may be offered idle work (rules 15 and 16): not
        with `"offer": false`."""
        return self.project.offered(who)

    def reviewer_logins(self):
        return {self.slug(n) for n in self.reviewers()}

    def reviewer_for(self, pr):
        """The reviewer whose `reviews` globs match the most changed
        lines of `pr`, leaving out its author. A tie, and lines no
        reviewer's globs match, go to the first listed. Reviewers with
        the same globs share that work: the pull request goes to one of
        them by its number, so each takes every other one and a pull
        request keeps its reviewer. A reviewer who is `away` is left
        out, and her stand-in reviews her globs too. None when no
        reviewer but the author exists."""
        author = self.who(author_of(pr))
        names = [n for n in self.reviewers()
                 if n != author and self.stand_in(n) == n]
        if not names:
            return None

        def globs_of(name):
            return sorted({g for n in self.reviewers()
                           if self.stand_in(n) == name
                           for g in self.agents[n].get("reviews") or []})
        globs_by = {n: globs_of(n) for n in names}
        score = dict.fromkeys(names, 0)
        for f in pr.get("files") or []:
            lines = (f.get("additions") or 0) + (f.get("deletions") or 0)
            path = f.get("path") or ""
            owner = next((n for n in names if any(
                fnmatch.fnmatchcase(path, glob) for glob in globs_by[n])),
                names[0])
            score[owner] += lines
        best = max(names, key=lambda n: (score[n], -names.index(n)))
        globs = globs_by[best]
        peers = [n for n in names if globs and globs_by[n] == globs]
        if not peers:
            return best
        return peers[(pr.get("number") or 0) % len(peers)]


def strip_bot(login):
    """A login lowercased, without the "[bot]" REST adds and GraphQL
    leaves off."""
    login = (login or "").lower()
    return login[:-len("[bot]")] if login.endswith("[bot]") else login


def author_of(node):
    return strip_bot(((node or {}).get("author") or {}).get("login"))


# --- the rules --------------------------------------------------------------


def labels_of(item):
    return [l.get("name") or "" for l in item.get("labels") or []]


def quiet(item):
    """The first of `router.quiet_labels` the item has, or None."""
    labels = labels_of(item)
    return next((l for l in QUIET_LABELS if l in labels), None)


def owner_of(item):
    """The Name of the first `Owner:<Name>` label, by name, or None."""
    for name in sorted(labels_of(item)):
        if name.startswith(OWNER_PREFIX) and name[len(OWNER_PREFIX):].strip():
            return name[len(OWNER_PREFIX):].strip()
    return None


def issue_owner(issue, roster):
    """Whose issue it is. Without `router.owners`, the `Owner:` label's
    name, whoever it names (or None). With it (designs/0008): the agent
    the label names, else the author when an agent, else None: unowned.
    A label naming no agent is passed over."""
    labelled = owner_of(issue)
    if OWNERS is None:
        return labelled
    if labelled and roster.is_agent(labelled):
        return labelled
    author = roster.who(author_of(issue))
    return author if roster.is_agent(author) else None


def unowned(issue, roster):
    """Rule 18: an unowned issue is `owners.triage`'s turn, or surfaced
    without one. The event is when it was created or its `Owner:`
    labels last changed, so a label taken off asks again."""
    n = issue["number"]
    labelled = owner_of(issue)
    why = ("its %s label names nobody" % (OWNER_PREFIX + labelled)
           if labelled else "has no owner")
    triage = OWNERS["triage"]
    if not triage:
        return nobody(n, 18, why, kind="surface")
    changed = [e.get("createdAt") or "" for e in (
        (issue.get("ownerLabels") or {}).get("nodes") or [])
        if ((e or {}).get("label") or {}).get("name", "").startswith(
            OWNER_PREFIX)]
    event = max([issue.get("createdAt") or ""] + changed)
    return turn(n, triage, 18, event, "%s: label it %s<Name>" % (
        why, OWNER_PREFIX))


def last_by(items, logins, stamp="createdAt"):
    """The newest `stamp` of the `items` written by one of `logins`, or
    "" when there is none. GitHub's UTC times compare as strings."""
    return max((i.get(stamp) or "" for i in items
                if author_of(i) in logins), default="")


def status_of(pr):
    """The newest Merger-posted vm/build-and-test context on the head
    commit, or None."""
    nodes = (pr.get("headCommit") or {}).get("nodes") or []
    commit = (nodes[-1] or {}).get("commit") if nodes else None
    if not commit or commit.get("oid") != pr.get("headRefOid"):
        return None
    status = None
    for ctx in ((commit.get("status") or {}).get("contexts")) or []:
        creator = strip_bot((ctx.get("creator") or {}).get("login"))
        if (ctx.get("context") == STATUS_CONTEXT
                and creator == strip_bot(merger.STATUS_CREATOR)
                and (status is None or (ctx.get("createdAt") or "")
                     > (status.get("createdAt") or ""))):
            status = ctx
    return status


def commit_of(review):
    return (review.get("commit") or {}).get("oid") or ""


def at_head(review, pr):
    """True when `review` was made at the pull request's head commit."""
    return commit_of(review) == pr.get("headRefOid")


def covers_head(review, pr):
    """True when `review` was made at the head, or every commit after
    its own on the pull request is a merge (two parents): the Merger's
    update-branch merges of main. A commit not one of the pull request's
    (last 100) commits covers nothing."""
    if at_head(review, pr):
        return True
    oids = [((n or {}).get("commit") or {})
            for n in (pr.get("commits") or {}).get("nodes") or []]
    at = [i for i, c in enumerate(oids) if c.get("oid") == commit_of(review)]
    if not at or not oids or oids[-1].get("oid") != pr.get("headRefOid"):
        return False
    return all(((c.get("parents") or {}).get("totalCount") or 0) == 2
               for c in oids[at[-1] + 1:])


def signed_by_agent(comment, roster):
    """True when the comment's last non-empty line is an agent's or the
    Merger's sign-off."""
    lines = [l.strip() for l in (comment.get("body") or "").splitlines()
             if l.strip()]
    return bool(lines) and lines[-1] in roster.signatures()


def turn(number, who, rule, event, line, reminder=None):
    return {"item": number, "who": who, "rule": rule, "event": event,
            "line": "#%d %s" % (number, line), "reminder": reminder}


def nobody(number, rule, why, kind="turn"):
    return {"item": number, "who": None, "rule": rule, "why": why,
            "kind": kind}


def route_pr(pr, roster, when=None):
    """The turn for one pull request, as `turn` or `nobody` builds it.
    `when` is the run's clock, for rule 0c; it defaults to now."""
    n, head = pr["number"], pr.get("headRefOid") or ""
    labels = labels_of(pr)
    # `router.quiet_labels`, before every rule.
    hushed = quiet(pr)
    if hushed:
        return nobody(n, None, "quiet: " + hushed)
    # Rules 0a, 0b and 0c, before rule 1: a draft can be stuck too.
    stuck = stuck_pr(pr, roster, when or merger.now_utc())
    if stuck:
        return stuck
    # Rule 1.
    if pr.get("isDraft"):
        return nobody(n, 1, "draft")
    for label in (WAITING_OWNER, WAITING_EXTERNAL):
        if label in labels:
            return nobody(n, 1, label)
    author_login = author_of(pr)
    author = roster.who(author_login)
    reviewer_logins = roster.reviewer_logins() - {strip_bot(author_login)}
    status = status_of(pr)
    reviews = [r for r in pr.get("reviews") or [] if r.get("submittedAt")]
    comments = pr.get("comments") or []
    spoke = max(last_by(comments, {author_login}),
                last_by(reviews, {author_login}, "submittedAt"))

    # Rule 2. A hand-back names the head it judged ("Not merging
    # <head[:10]>:"), and holds only while that is still the head: a push
    # is neither a comment nor a review, so without this a fixed and
    # green head would stay the author's for ever.
    handed = [c for c in comments
              if author_of(c) == roster.login_of(MERGER)]
    if handed:
        back = max(handed, key=lambda c: c.get("createdAt") or "")
        when = back.get("createdAt") or ""
        newest_review = max((r["submittedAt"] for r in reviews), default="")
        judged = HAND_BACK_RE.search(back.get("body") or "")
        if (judged and head.startswith(judged.group(1))
                and when > newest_review and when > spoke):
            to = author
            if (back.get("body") or "").lstrip().startswith("Not-a-review"):
                approval = merger.approval_of(dict(pr, reviews=reviews),
                                              roster.reviewer_logins())
                to = (roster.who(author_of(approval)) if approval
                      else roster.reviewer_for(pr) or roster.reviewers()[0])
            return turn(n, to, 2, when, "the Merger handed it back "
                        "(comment %s)" % back.get("databaseId"))
    # Rule 3. A request for changes stands until its reviewer's next
    # verdict, whatever else is said or pushed: the Merger's merges of
    # main after it, and another reviewer's approval, don't lift it.
    theirs = [r for r in reviews
              if strip_bot(author_of(r)) in reviewer_logins]
    newest = max(theirs, key=lambda r: r["submittedAt"]) if theirs else None
    standing = merger.changes_requested(
        dict(pr, reviews=reviews),
        reviewer_logins | ({OWNER} if OWNER else set()))
    answered = [r for r in standing
                if spoke > r["submittedAt"] or not covers_head(r, pr)]
    for request in standing:
        if request not in answered:
            return turn(n, author, 3, request["submittedAt"],
                        "%s asked for changes (review %s)" % (
                            roster.who(author_of(request)),
                            request.get("databaseId")))
    if (newest is not None and at_head(newest, pr)
            and newest.get("state") == "COMMENTED"
            and spoke <= newest["submittedAt"]):
        return turn(n, author, 3, newest["submittedAt"],
                    "%s commented (review %s)" % (
                        roster.who(author_of(newest)),
                        newest.get("databaseId")))
    state = ((status or {}).get("state") or "").lower()
    # Rule 4.
    if state in ("failure", "error"):
        return turn(n, author, 4, status.get("createdAt") or head,
                    "%s is %s at %s" % (STATUS_CONTEXT, state, head[:7]))
    # Rule 5.
    if state != "success":
        return nobody(n, 5, "%s is %s" % (STATUS_CONTEXT, state or "absent"))
    # Rule 6.
    if (not standing and newest is not None
            and newest.get("state") == "APPROVED"
            and covers_head(newest, pr)
            and not merger.said_since(dict(pr, reviews=reviews,
                                           comments=comments), newest)):
        return nobody(n, 6, "the Merger's: approved, nothing said since")
    # Rule 7.
    reviewer = roster.reviewer_for(pr)
    if reviewer is None:
        return nobody(n, 7, "green, and %s's own: no second reviewer"
                      % author, kind="surface")
    # Rule 8. A standing request the author has answered (a comment, or
    # a commit that isn't a merge) is its reviewer's to review again.
    if standing:
        request = standing[0]
        who = roster.who(author_of(request))
        if strip_bot(author_of(request)) not in reviewer_logins:
            return nobody(n, 8, "answered %s's request for changes" % who)
        return turn(n, who, 8, max(status.get("createdAt") or "", spoke,
                                   request["submittedAt"]),
                    "needs your review again: you asked for changes "
                    "(review %s), green at %s" % (request.get("databaseId"),
                                                  head[:7]))
    # Someone other than the author spoke after the approval: the Merger
    # holds it until a newer approval, so it is the approver's to read.
    if (newest is not None and newest.get("state") == "APPROVED"
            and covers_head(newest, pr)):
        said = merger.said_since(dict(pr, reviews=reviews,
                                      comments=comments), newest)
        if said:
            who, at, kind = said[-1]
            return turn(n, roster.who(author_of(newest)), 8, at,
                        "%s's %s came after your approval, so the Merger "
                        "holds it: approve again if nothing needs to change"
                        % (roster.who(who), kind))
    seen = [r for r in theirs if at_head(r, pr) or (
        r.get("state") == "APPROVED" and covers_head(r, pr))]
    last = max((r["submittedAt"] for r in seen), default=None)
    if last is None or spoke > last:
        # The owner's review is an event too: a reviewer who waits for
        # the owner's approval on a code-owned path is woken by it.
        owned = last_by(reviews, {OWNER}, "submittedAt") if OWNER else ""
        return turn(n, reviewer, 8, max(status.get("createdAt") or "", spoke,
                                        owned),
                    "needs your review (green at %s)" % head[:7])
    return nobody(n, None, "no rule matched")


# --- stuck pull requests (rules 0a, 0b and 0c) ------------------------------


def stuck_pr(pr, roster, when):
    """Rules 0a, 0b and 0c, each when `router.stuck` turns it on: the
    author's turn for a pull request nothing else would wake anyone
    for, said once for its event, else None."""
    n = pr["number"]
    author = roster.who(author_of(pr))
    # Rule 0a. `baseOpen` is the `bases` lookup's: False only when
    # GitHub said no open pull request has the base as its head.
    base = pr.get("baseRefName") or ""
    if (RETARGET and base and base != merger.MAIN
            and pr.get("baseOpen") is False):
        return turn(n, author, "0a", "base " + base,
                    "is based on %s, and no open pull request has %s as "
                    "its head: what it was stacked on has merged or "
                    "closed, or never was. Retarget it to %s, merging %s "
                    "into it first "
                    "if needed." % (base, base, merger.MAIN, merger.MAIN),
                    reminder=ONCE)
    # Rule 0b.
    if SELF_BLOCKED:
        both = sorted(merger.blockers_of(pr) & closing_of(pr))
        if both:
            k = both[0]
            return turn(n, author, "0b",
                        pr.get("lastEditedAt") or pr.get("createdAt") or "",
                        "says Blocked-by: #%d, which it closes: #%d stays "
                        "open until it merges, so the Merger can never take "
                        "it. Replace the line with `Closes #%d`." % (k, k, k),
                        reminder=ONCE)
    # Rule 0c. A last activity that can't be read is never idle.
    if DRAFT_HOURS and pr.get("isDraft"):
        last = last_activity(pr)
        since = parse_stamp(last)
        if since is None or waits(pr, when):
            return None
        hours = int((when - since).total_seconds() // 3600)
        if hours >= DRAFT_HOURS:
            return turn(n, author, "0c", last,
                        "has been a draft with nothing new for %s (since "
                        "%s), and records no wait. Un-draft it, or record "
                        "what it waits for: a Blocked-by: line, a "
                        "Defer-until: line or a %s* label." % (
                            counted(hours, "hour", "hours"),
                            since_text(last, when), WAITING_PREFIX),
                        reminder=ONCE)
    return None


def closing_of(pr):
    """The issue numbers GitHub says the pull request closes."""
    refs = pr.get("closingIssuesReferences") or {}
    nodes = refs.get("nodes") if isinstance(refs, dict) else refs
    return {n.get("number") for n in nodes or [] if isinstance(n, dict)}


def last_activity(pr):
    """The newest of the head's commit date, the body's last edit (or
    the pull request's creation) and anything its author said."""
    nodes = (pr.get("headCommit") or {}).get("nodes") or []
    commit = ((nodes[-1] or {}).get("commit") or {}) if nodes else {}
    mine = {author_of(pr)}
    reviews = [r for r in pr.get("reviews") or [] if r.get("submittedAt")]
    return max(commit.get("committedDate") or "",
               pr.get("lastEditedAt") or pr.get("createdAt") or "",
               last_by(pr.get("comments") or [], mine),
               last_by(reviews, mine, "submittedAt"))


def waits(pr, when):
    """Whether a pull request records what it waits for at `when`: a
    `Blocked-by:` item still open (as `blocked` reads it), a
    `Defer-until:` still to come, or a `Status:waiting-*` label."""
    if any(l.startswith(WAITING_PREFIX) for l in labels_of(pr)):
        return True
    if blocked(pr):
        return True
    until = deferred_until(pr)
    return until is not None and until > when


def deferred_until(pr):
    """The time of the newest `Defer-until:` line on the pull request,
    found as the Merger finds it (the body, then comments and reviews
    by time), or None when there is none or it is not a time."""
    texts = [("", pr.get("body"))]
    texts += [(c.get("createdAt") or "", c.get("body"))
              for c in pr.get("comments") or []]
    texts += [(r.get("submittedAt") or "", r.get("body"))
              for r in pr.get("reviews") or [] if r.get("submittedAt")]
    latest = None
    for _, text in sorted(texts, key=lambda t: t[0]):
        found = merger.DEFER_ANY_RE.findall(merger.markers_of(text))
        if found:
            latest = found[-1]
    return None if latest is None else merger.parse_defer(latest)


# --- issues -----------------------------------------------------------------


def route_issue(issue, roster, ctx=None):
    """The turn for one issue, as `turn` or `nobody` builds it. `ctx`,
    the run's, is what rule 20 reads the open pull requests and the
    clock from; without it rule 20 never applies."""
    n = issue["number"]
    labels = labels_of(issue)
    comments = issue.get("comments") or []
    owner = issue_owner(issue, roster)
    # `router.quiet_labels`, before every rule.
    hushed = quiet(issue)
    if hushed:
        return nobody(n, None, "quiet: " + hushed)
    # Rule 9.
    if WAITING_EXTERNAL in labels:
        return nobody(n, 9, WAITING_EXTERNAL)
    if WAITING_OWNER in labels:
        # Rule 10.
        answered = owner_answered(n, issue, owner, comments, roster, 10)
        # Rule 11.
        return answered or nobody(n, 11, WAITING_OWNER)
    # Rule 18, with `router.owners`: nothing below can wake anyone.
    if OWNERS is not None and owner is None:
        return unowned(issue, roster)
    # Rule 20, with `router.red_first`.
    red = red_turn(issue, owner, comments, roster, ctx)
    if red:
        return red
    # Rule 12.
    if owner:
        mine = {roster.login_of(owner)}
        theirs = last_by(comments, mine)
        others = [c for c in comments if author_of(c) not in mine
                  and (c.get("createdAt") or "") > theirs]
        if others:
            newest = max(others, key=lambda c: c.get("createdAt") or "")
            return turn(n, owner, 12, newest["createdAt"],
                        "has a comment by %s (comment %s)" % (
                            roster.who(author_of(newest)),
                            newest.get("databaseId")))
    # Rule 13: the owner usually takes the waiting label off when
    # answering, which ends rule 10.
    if not owner:
        answered = owner_answered(n, issue, None, comments, roster, 13)
        if answered:
            return answered
    # Rule 14.
    unblocked = unblocked_by(issue, owner, comments, roster)
    if unblocked:
        return unblocked
    return nobody(n, None, "no rule matched")


def unblocked_by(issue, owner, comments, roster):
    """Rule 14: the item owner's turn when the last open item its
    `Blocked-by:` lines name has closed since the body was written and
    since the item owner last spoke, else None."""
    named = merger.blockers_of(issue)
    known = {b: info for b, info in (issue.get("blockedBy") or {}).items()
             if b in named and info}
    if not known or any(i.get("state") == "OPEN" for i in known.values()):
        return None
    event = max((i.get("closedAt") or "" for i in known.values()),
                default="")
    who = owner or roster.who(author_of(issue))
    written = max(issue.get("lastEditedAt") or "",
                  issue.get("createdAt") or "",
                  last_by(comments, {roster.login_of(who)}))
    if not event or event <= written:
        return None
    return turn(issue["number"], who, 14, event, "unblocked: %s closed" % (
        ", ".join("#%d" % b for b in sorted(known))))


def owner_answered(n, issue, owner, comments, roster, rule):
    """The item owner's turn (`Owner:<Name>`, else the issue's author)
    when a comment by the project's owner is newer than their last,
    else None."""
    who = owner or roster.who(author_of(issue))
    theirs = last_by(comments, {roster.login_of(who)})
    answers = [c for c in comments if author_of(c) == OWNER
               and (c.get("createdAt") or "") > theirs
               and not signed_by_agent(c, roster)]
    if not answers:
        return None
    newest = max(answers, key=lambda c: c.get("createdAt") or "")
    return turn(n, who, rule, newest["createdAt"],
                "%s answered (comment %s)" % (OWNER, newest.get("databaseId")))

# --- the interface ----------------------------------------------------------


def as_decision(d):
    """A `turn` or `nobody` dict as the engine's Decision: nobody's
    reason goes in `suppressed`, and rule 7 is a surface."""
    if d["who"] is None:
        return Decision(d["item"], None, d["rule"], None, None,
                        kind=d["kind"], suppressed=d["why"])
    return Decision(d["item"], d["who"], d["rule"], d["event"], d["line"],
                    reminder=d.get("reminder"))


def decide(item, ctx):
    """The one decision for an open item."""
    if item.get("__typename") == "PullRequest":
        return [as_decision(route_pr(item, ctx.roster, ctx.when))]
    return [as_decision(route_issue(item, ctx.roster, ctx))]


# A decision rests on nothing a later fetch could change but GitHub.
recheck = decide


def sweep(ctx):
    """Rules 15 and 16, the idle offer, rule 21, a red build's fallback
    (which needs the idle offer too), rule 17, the merge notice, and
    rule 19, the paths no reviewer covers, each when it is configured;
    nothing on a run started by an event
    (`ctx.only`), whose items are all it may decide about. An agent
    rule 21 offers a red build to is offered nothing else that run."""
    if ctx.only is not None:
        return []
    decisions = []
    if IDLE_OFFER is not None:
        taken = set()
        if RED_FIRST is not None:
            fallbacks, taken = red_fallbacks(ctx)
            decisions += fallbacks
        decisions += idle_offers(ctx, skip=taken)
    if MERGE_NOTICE is not None:
        decisions += merge_notices(ctx)
    if UNREVIEWED is not None:
        decisions += unreviewed(ctx)
    return decisions


def author(node, ctx):
    """Who wrote `node`, by login: the configuration's name for an App,
    else the login."""
    return ctx.roster.who(author_of(node))


def summary(ctx):
    """The run's tally of issues waiting on the owner."""
    waiting = sum(1 for i in ctx.issues if WAITING_OWNER in labels_of(i))
    return "%d waiting on %s" % (waiting, OWNER)


# --- the idle offer (rules 15 and 16) -----------------------------------------


def parse_stamp(stamp):
    """A directory time ("2026-10-10T14:10:00Z") as an aware datetime,
    or None."""
    try:
        return datetime.datetime.strptime(
            stamp, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=datetime.timezone.utc)
    except (TypeError, ValueError):
        return None


def idle_agents(ctx):
    """{agent: when its idle period began (the directory's string)} for
    each agent the directory shows idle: see `idle_sweep`."""
    return {agent: stamp for agent, (stamp, _) in idle_sweep(ctx).items()}


def idle_sweep(ctx):
    """{agent: (when its idle period began, the directory's string;
    whether a background task still runs)} for each agent the directory
    shows idle: `idle` for at least `idle_minutes`, or, with
    `shell_minutes` set, `shell` (its turn over, a background task it
    started still running) for at least that. A session is matched by
    its name; one listed more than once is idle only when every entry
    is, since the latest of them. An agent with twins is idle when any
    of her sessions is, since the longest idle of them: an offer then
    reaches the one with nothing to do. Nobody is idle when the
    directory is unknown."""
    listing = ctx.directory
    if not isinstance(listing, dict) or listing.get("complete") is not True \
            or not isinstance(listing.get("agents"), list):
        return {}
    # An agent who is away isn't offered work: her stand-in is.
    sessions = {s: a for a in ctx.roster.agents
                if ctx.roster.stand_in(a) == a
                for s in ctx.roster.sessions(a)}
    cutoffs = {"idle": IDLE_OFFER["idle_minutes"]}
    if IDLE_OFFER.get("shell_minutes"):
        cutoffs["shell"] = IDLE_OFFER["shell_minutes"]
    cutoffs = {status: ctx.when - datetime.timedelta(minutes=minutes)
               for status, minutes in cutoffs.items()}
    seen = {}
    for entry in listing["agents"]:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name not in sessions:
            continue
        status = entry.get("status")
        since = parse_stamp(entry.get("idle_since")) \
            if status in cutoffs else None
        # Long enough in its status, or None: not idle.
        if since is not None and since > cutoffs[status]:
            since = None
        seen.setdefault(name, []).append(
            (since, entry.get("idle_since"), status == "shell"))
    idle = {}
    for name, entries in seen.items():
        if any(since is None for since, _, _ in entries):
            continue
        since, stamp, _ = max(entries)
        shell = any(in_shell for _, _, in_shell in entries)
        agent = sessions[name]
        if agent not in idle or since < idle[agent][0]:
            idle[agent] = (since, stamp, shell)
    return {agent: (stamp, shell)
            for agent, (_, stamp, shell) in idle.items()}


# A pull request body's reference to an issue: `#N`, or its URL.
REFERENCE_RE = re.compile(
    r"(?<![\w/&])#(\d+)\b|/issues/(\d+)\b")
# "issue 12", "issues 12 and 13", "issues 12, 13 and 14": how commit
# messages, and so many pull request bodies, name an issue.
WORDED_RE = re.compile(
    r"\bissues?[ \t]+(\d+(?:(?:[ \t]*,[ \t]*(?:and[ \t]+)?|[ \t]+and[ \t]+)"
    r"\d+)*)\b", re.IGNORECASE)


def referenced_by(pr):
    """The issue numbers a pull request references: GitHub's closing
    references, and every `#N`, issue URL or "issue N" in its body."""
    refs = pr.get("closingIssuesReferences") or {}
    nodes = refs.get("nodes") if isinstance(refs, dict) else refs
    numbers = {n.get("number") for n in nodes or [] if isinstance(n, dict)}
    body = pr.get("body") or ""
    for m in REFERENCE_RE.finditer(body):
        numbers.add(int(m.group(1) or m.group(2)))
    for m in WORDED_RE.finditer(body):
        numbers |= {int(n) for n in re.findall(r"\d+", m.group(1))}
    return numbers


def blocked(issue):
    """Whether an item an issue's `Blocked-by:` lines name is still open,
    or what they name is unknown. A number naming nothing is left out,
    as rule 14 leaves it out."""
    named = merger.blockers_of(issue)
    if not named:
        return False
    known = issue.get("blockedBy")
    if not isinstance(known, dict):
        return True
    return any((known.get(b) or {}).get("state") == "OPEN" for b in named)


def owned_issues(agent, ctx):
    """(actionable, blocked, waiting): the agent's issues (`Owner:<Name>`)
    that it can act on, oldest first, and how many it can't, because
    something they name is open, or they wait on someone or until a
    `Defer-until:` time still to come, or its own
    open pull request already references them."""
    mine = ctx.roster.login_of(agent)
    in_hand = set()
    for pr in ctx.prs:
        if author_of(pr) == mine:
            in_hand |= referenced_by(pr)
    actionable, held, waiting = [], 0, 0
    for issue in ctx.issues:
        if issue_owner(issue, ctx.roster) != agent:
            continue
        labels = labels_of(issue)
        # Any waiting label, not only the two named in the offer: a
        # project's own, such as one for the backlog, is a wait too.
        until = deferred_until(issue)
        if WAITING_OWNER in labels or WAITING_EXTERNAL in labels \
                or any(l.startswith(WAITING_PREFIX) for l in labels) \
                or quiet(issue) or (until is not None and until > ctx.when):
            waiting += 1
        elif blocked(issue):
            held += 1
        elif issue["number"] not in in_hand:
            actionable.append(issue)
    # With `router.red_first`, red-build issues first (designs/0039).
    actionable.sort(key=lambda i: (red_build(i) is None,
                                   i.get("createdAt") or "", i["number"]))
    return actionable, held, waiting


def has_turn(agent, ctx):
    """Whether rules 0a-14 make any open item the agent's turn."""
    for item in ctx.prs + ctx.issues:
        decided = route_pr(item, ctx.roster, ctx.when) \
            if item.get("__typename") == "PullRequest" \
            else route_issue(item, ctx.roster, ctx)
        if decided["who"] == agent:
            return True
    return False


def since_text(stamp, when):
    """When an idle period began, for a message: "14:10Z" today, else
    with its date."""
    since = parse_stamp(stamp)
    if since.date() == when.date():
        return since.strftime("%H:%MZ")
    return since.strftime("%Y-%m-%d %H:%MZ")


def counted(n, one, many):
    return "%d %s" % (n, one if n == 1 else many)


# Kinds never reported idle with nothing to do (rule 16): a reviewer's
# work comes to her, and a collaborator works here only when given some.
NOT_REPORTED = ("reviewer", "collaborator")


def idle_offers(ctx, skip=()):
    """Rules 15 and 16: an offer to each idle agent with an actionable
    issue and no turn, and a report to `report_to` of each idle
    developer with none. Agents in `skip` (rule 21's) are left out."""
    report_to = IDLE_OFFER["report_to"]
    decisions = []
    for agent, (stamp, shell) in sorted(idle_sweep(ctx).items()):
        if agent in skip or not ctx.roster.is_agent(agent) \
                or not ctx.roster.offered(agent) or has_turn(agent, ctx):
            continue
        actionable, held, waiting = owned_issues(agent, ctx)
        if actionable:
            issue = actionable[0]
            title = (issue.get("title") or "").strip()
            red = red_build(issue)
            decisions.append(Decision(
                None, agent, 15, stamp,
                "#%d%s is yours and %s: start it, or say on "
                "the issue why not (a %s or %s label, or a Blocked-by: "
                "line). You have been idle since %s.%s" % (
                    issue["number"], ' "%s"' % title if title else "",
                    "says %s is red. Red builds go first" % red["what"]
                    if red else "nothing blocks it",
                    WAITING_OWNER, WAITING_EXTERNAL,
                    since_text(stamp, ctx.when),
                    " A background task of yours has been running since "
                    "%s. Stop it if it is stale." % since_text(
                        stamp, ctx.when) if shell else ""),
                reminder=ONCE))
            continue
        if not report_to or agent == ctx.roster.stand_in(report_to) or \
                ctx.roster.project.kind(agent) in NOT_REPORTED:
            continue
        mine = ctx.roster.login_of(agent)
        prs = sum(1 for pr in ctx.prs if author_of(pr) == mine)
        why = ", ".join(part for n, part in (
            (prs, counted(prs, "open pull request", "open pull requests")),
            (held, counted(held, "issue blocked", "issues blocked")),
            (waiting, counted(waiting, "issue waiting", "issues waiting")))
            if n) or "no open issues of its own"
        decisions.append(Decision(
            None, report_to, 16, "%s %s" % (agent, stamp),
            "%s has been idle since %s with nothing it can act on (%s).%s"
            % (agent, since_text(stamp, ctx.when), why,
               hand_over(agent, ctx)),
            reminder=ONCE))
    return decisions


def hand_over(idle, ctx):
    """Rule 16's ask for an issue to hand to `idle` (designs/0038): " P
    owns K actionable issues it hasn't started (#a, ...): hand one to
    D, or say why not.", or "" without `hand_over_above`, or when no
    agent owns more unstarted issues than that."""
    above = IDLE_OFFER.get("hand_over_above")
    if above is None:
        return ""
    best = None
    for agent in sorted(ctx.roster.agents):
        if agent == idle:
            continue
        unstarted = [i for i in owned_issues(agent, ctx)[0]
                     if IN_PROGRESS not in labels_of(i)]
        if len(unstarted) <= above:
            continue
        key = (-len(unstarted), unstarted[0].get("createdAt") or "",
               unstarted[0]["number"])
        if best is None or key < best[0]:
            best = (key, agent, unstarted)
    if best is None:
        return ""
    _, agent, unstarted = best
    listed = ", ".join("#%d" % i["number"]
                       for i in unstarted[:HAND_OVER_LIST])
    return (" %s owns %d actionable issues it hasn't started (%s%s): hand "
            "one to %s, or say why not." % (
                agent, len(unstarted), listed,
                ", ..." if len(unstarted) > HAND_OVER_LIST else "", idle))


# --- red builds first (rules 20 and 21) ----------------------------------------


# Where forge-pr-check keeps the default branch's red issue.
CHECK_STATE = "state.json"
STAMP = "%Y-%m-%dT%H:%M:%SZ"


def as_stamp(text):
    """A time as GitHub writes one ("2026-10-31T05:40:00Z"), from any
    ISO 8601 time with a zone, or None."""
    if not isinstance(text, str) or not text:
        return None
    try:
        return nightly.parse_time(text).strftime(STAMP)
    except (TypeError, ValueError):
        return None


def red_builds(project, environ=None, now=None):
    """{issue number: (what is red, the red event or None)} for the
    issues the build tools keep open while a build is red: forge-nightly's
    (`issue` in nightly.json, while `issue_open` and the file is fresh,
    as `nightly.current` reads it; its event is when its newest red run
    ended, else when the issue was opened) and forge-pr-check's
    (`main_red.issue` in its state.json; its event is the issue's to
    read, so None here). Nothing that can't be read is red."""
    found = {}
    data = nightly.current(project, environ, now)
    number = data.get("issue")
    if data.get("issue_open") and isinstance(number, int) \
            and not isinstance(number, bool):
        red = data.get("red") if isinstance(data.get("red"), dict) else {}
        found[number] = ("the nightly", as_stamp(red.get("ended"))
                         or as_stamp(data.get("opened")))
    check = state.load(os.path.join(locks.state_dir(
        project.name, "pr-check", environ, create=False), CHECK_STATE))
    main = check.get("main_red")
    number = main.get("issue") if isinstance(main, dict) else None
    if isinstance(number, int) and not isinstance(number, bool) \
            and number not in found:
        found[number] = ("the default branch", None)
    return found


def red_event(issue, event):
    """The red event of the default branch's issue: its newest comment
    by the build check's poster (each red head after the first says so
    there), else its creation. `event`, when known already, wins."""
    if event:
        return event
    poster = strip_bot(merger.STATUS_CREATOR)
    said = last_by(issue.get("comments") or [], {poster}) if poster else ""
    return as_stamp(said or issue.get("createdAt"))


def attach_red_builds(github, repo, prs, issues):
    """Rules 20 and 21's lookup, with `router.red_first`: give each open
    issue a build tool keeps open while a build is red `redBuild`,
    {"what": "the nightly" or "the default branch", "event": when it
    was last reported red}. It reads those tools' state on this host,
    not GitHub."""
    if RED_FIRST is None or PROJECT is None:
        return
    found = red_builds(PROJECT, ENVIRON)
    for issue in issues:
        if issue["number"] not in found:
            continue
        what, event = found[issue["number"]]
        event = red_event(issue, event)
        if event:
            issue["redBuild"] = {"what": what, "event": event}


def red_build(issue):
    """The issue's `redBuild`, with `router.red_first`, or None."""
    red = issue.get("redBuild") if RED_FIRST is not None else None
    return red if isinstance(red, dict) and red.get("event") else None


def in_hand(number, ctx):
    """Whether an open pull request, by anyone, references the issue."""
    return any(number in referenced_by(pr) for pr in ctx.prs)


def parked(issue):
    """Whether a person has parked the issue: a quiet label, or a label
    saying what it waits for."""
    labels = labels_of(issue)
    return bool(quiet(issue)) or WAITING_OWNER in labels \
        or WAITING_EXTERNAL in labels \
        or any(l.startswith(WAITING_PREFIX) for l in labels)


def red_turn(issue, owner, comments, roster, ctx):
    """Rule 20: an open red-build issue the owner hasn't commented on
    since its red event, and that no open pull request references, is
    the owner's turn, reminded as any turn is. None otherwise."""
    red = red_build(issue)
    if red is None or ctx is None or not owner or parked(issue):
        return None
    n = issue["number"]
    if in_hand(n, ctx) or \
            last_by(comments, {roster.login_of(owner)}) >= red["event"]:
        return None
    return turn(n, owner, 20, red["event"],
                "says %s is red (since %s), and no pull request references "
                "it. Red builds go first: put other work down and fix it, "
                "or say on the issue who is fixing it." % (
                    red["what"], since_text(red["event"], ctx.when)))


def red_fallbacks(ctx):
    """Rule 21: ([Decision], {agent offered}): each open red-build issue
    not parked and not in hand, whose red event is `fallback_hours` old
    and whose owner hasn't commented on it for as long, offered to the
    idle developer idle longest (the name breaks a tie), other than its
    owner (or her stand-in), with no turn of her own and of a kind rule
    16 reports; one issue each. Said once for each red event."""
    hours = RED_FIRST["fallback_hours"]
    cutoff = (ctx.when - datetime.timedelta(hours=hours)).strftime(STAMP)
    pool = sorted(
        (parse_stamp(stamp), agent)
        for agent, (stamp, _) in idle_sweep(ctx).items()
        if ctx.roster.is_agent(agent) and ctx.roster.offered(agent)
        and ctx.roster.project.kind(agent) not in NOT_REPORTED
        and not has_turn(agent, ctx))
    decisions, taken = [], set()
    for issue in sorted(ctx.issues, key=lambda i: i["number"]):
        red = red_build(issue)
        n = issue["number"]
        if red is None or parked(issue) or in_hand(n, ctx) \
                or red["event"] > cutoff:
            continue
        owner = issue_owner(issue, ctx.roster)
        if owner and last_by(issue.get("comments") or [],
                             {ctx.roster.login_of(owner)}) >= cutoff:
            continue
        away = {owner, ctx.roster.stand_in(owner)} if owner else set()
        pick = next((a for _, a in pool
                     if a not in away and a not in taken), None)
        if pick is None:
            continue
        taken.add(pick)
        decisions.append(Decision(
            None, pick, 21, "#%d %s" % (n, red["event"]),
            "#%d says %s is red since %s, and for %s h no pull request has "
            "referenced it and %s. You are idle: take it (move its owner "
            "label to %s and say so on the issue) and fix it first, unless a pull "
            "request is about to reference it." % (
                n, red["what"], since_text(red["event"], ctx.when),
                "%g" % hours,
                "its owner, %s, hasn't commented" % owner if owner
                else "nobody owns it", OWNER_PREFIX + pick),
            reminder=ONCE))
    return decisions, taken


# --- the merge notice (rule 17) -----------------------------------------------


def unblocked_by_merge(number, ctx):
    """"#N (Owner)" for each open issue whose `Blocked-by:` lines name
    `number` and whose every named item is now closed or merged."""
    out = []
    for issue in sorted(ctx.issues, key=lambda i: i["number"]):
        if number in merger.blockers_of(issue) and not blocked(issue):
            out.append("#%d (%s)" % (issue["number"],
                                     issue_owner(issue, ctx.roster)
                                     or ctx.roster.who(author_of(issue))))
    return out


def merge_notices(ctx):
    """Rule 17: a word to the author of each pull request an agent wrote
    that merged in the window, with what it unblocks and the author's
    next item; or, with `to: "owner_label"`, to the agent its owner
    label names. None for one labelled `skip_label`. Nothing when the
    merges are unknown."""
    if not isinstance(ctx.merged, list):
        return []
    cutoff = ctx.when - datetime.timedelta(
        minutes=MERGE_NOTICE["window_minutes"])
    decisions = []
    for pr in sorted(ctx.merged, key=lambda p: (p.get("mergedAt") or "",
                                                p.get("number") or 0)):
        at = parse_stamp(pr.get("mergedAt"))
        if at is None or not cutoff <= at <= ctx.when:
            continue
        if MERGE_NOTICE.get("skip_label") and \
                MERGE_NOTICE["skip_label"] in labels_of(pr):
            continue
        # A labelled owner who isn't an agent is the engine's to
        # withhold and journal; an author who isn't is passed over.
        agent = owner_of(pr) \
            if MERGE_NOTICE.get("to") == "owner_label" else None
        if agent is None:
            agent = ctx.roster.who(author_of(pr))
            if not ctx.roster.is_agent(agent):
                continue
        title = (pr.get("title") or "").strip()
        text = "#%d%s merged at %s." % (
            pr["number"], ' "%s"' % title if title else "",
            since_text(pr["mergedAt"], ctx.when))
        freed = unblocked_by_merge(pr["number"], ctx)
        if freed:
            text += " It unblocks %s." % ", ".join(freed)
        actionable, _, _ = owned_issues(agent, ctx)
        if actionable:
            nxt = actionable[0]
            nxt_title = (nxt.get("title") or "").strip()
            text += " Your oldest item nothing blocks: #%d%s." % (
                nxt["number"], ' "%s"' % nxt_title if nxt_title else "")
        decisions.append(Decision(
            None, agent, 17, "#%d %s" % (pr["number"], pr["mergedAt"]),
            text, reminder=ONCE))
    return decisions


# --- paths no reviewer covers (rule 19) ---------------------------------------


# Rule 19's record, in the engine's state directory: {area: {"issue",
# "prs" (told so far), "said" (the day it last wrote to the issue)}}.
UNREVIEWED_FILE = "unreviewed.json"
# The most paths a comment names for one pull request.
MAX_PATHS = 20


def area_of(path):
    """A path's area, with its trailing slash: its first segment, or its
    first two under a `depth2` directory; "/" for a file at the top."""
    parts = path.split("/")
    if len(parts) == 1:
        return "/"
    if parts[0] in UNREVIEWED["depth2"] and len(parts) > 2:
        return "%s/%s/" % (parts[0], parts[1])
    return parts[0] + "/"


def uncovered(ctx):
    """{area: {number: [path]}}: each open pull request's changed paths,
    drafts left out, that match no reviewer's `reviews` glob (by the
    reviewer choice's `fnmatchcase`) and no `ignore` glob."""
    globs = [g for n in ctx.roster.reviewers()
             for g in ctx.roster.agents[n].get("reviews") or []]
    globs += UNREVIEWED["ignore"]
    found = {}
    for pr in ctx.prs:
        if pr.get("isDraft") or quiet(pr):
            continue
        for f in pr.get("files") or []:
            path = f.get("path") or ""
            if path and not any(fnmatch.fnmatchcase(path, g) for g in globs):
                found.setdefault(area_of(path), {}).setdefault(
                    pr["number"], []).append(path)
    return found


def load_unreviewed(state_dir):
    state = merger.load_json(os.path.join(state_dir, UNREVIEWED_FILE), {})
    return state if isinstance(state, dict) else {}


def area_issue(area, known, ctx):
    """The open issue about `area`: the one `unreviewed.json` records,
    else the oldest titled for it; or None."""
    if known.get("issue") in {i["number"] for i in ctx.issues}:
        return known["issue"]
    prefix = "No reviewer covers %s (" % area
    titled = sorted(i["number"] for i in ctx.issues
                    if (i.get("title") or "").startswith(prefix))
    return titled[0] if titled else None


def path_list(paths):
    shown = ", ".join("`%s`" % p for p in sorted(paths)[:MAX_PATHS])
    more = len(paths) - MAX_PATHS
    return shown + (" and %d more" % more if more > 0 else "")


def pr_lines(prs):
    """One line a pull request, for an issue: its number and paths."""
    return "".join("- #%d: %s\n" % (n, path_list(paths))
                   for n, paths in prs)


def numbered(prs):
    return ", ".join("#%d" % n for n, _ in prs)


class Unreviewed(Action):
    """Rule 19's writes about one area, recording each in
    `unreviewed.json` as it is done, so no later run does it again."""

    def path(self):
        return os.path.join(self.state_dir, UNREVIEWED_FILE)

    def save(self, entry):
        state = load_unreviewed(self.state_dir)
        state[self.area] = entry
        os.makedirs(self.state_dir, exist_ok=True)
        merger.save_json(self.path(), state)


@dataclasses.dataclass(frozen=True)
class ReportUnreviewed(Unreviewed):
    """Open the area's issue naming `prs`, or, with `issue`, the open
    one, add them to it; then say so once on each pull request, in a
    `Not-a-review` comment. `prs` is ((number, (path, ...)), ...),
    those not yet told."""
    state_dir: str
    area: str
    issue: int | None
    prs: tuple
    label: str
    day: str

    def describe(self):
        if self.issue is None:
            return "open an issue for %s naming %s, and say so on each" % (
                self.area, numbered(self.prs))
        return "add %s to #%d, and say so on each" % (numbered(self.prs),
                                                      self.issue)

    def run(self, github, repo):
        if self.issue is None:
            issue, why = github.create(
                "repos/%s/issues" % repo,
                title="No reviewer covers %s (first seen on #%d)" % (
                    self.area, self.prs[0][0]),
                body="No reviewer's `reviews` globs cover `%s`, so its lines "
                "go to the first reviewer listed. Give it a reviewer in the "
                "configuration, then close this issue.\n\nPull requests "
                "that change it:\n%s\n%s" % (
                    self.area, pr_lines(self.prs), merger.SIGNATURE),
                **{"labels[]": self.label})
            if issue is None:
                return False, why
            entry = {"issue": issue, "prs": []}
        else:
            issue = self.issue
            entry = load_unreviewed(self.state_dir).get(self.area)
            if not isinstance(entry, dict) or entry.get("issue") != issue:
                entry = {"issue": issue, "prs": []}
            ok, why = github.write(
                "POST", "repos/%s/issues/%d/comments" % (repo, issue),
                body="Also:\n%s\n%s" % (pr_lines(self.prs), merger.SIGNATURE))
            if not ok:
                return False, why
        entry["said"] = self.day
        self.save(entry)
        failed = []
        for n, paths in self.prs:
            ok, why = github.write(
                "POST", "repos/%s/issues/%d/comments" % (repo, n),
                body="Not-a-review: no reviewer's `reviews` globs cover `%s` "
                "(%s), so #%d asks for one. Its lines still go to the first "
                "reviewer listed meanwhile.\n\n%s" % (
                    self.area, path_list(paths), issue, merger.SIGNATURE))
            if not ok:
                failed.append("#%d: %s" % (n, why))
                continue
            entry["prs"] = sorted(set(entry["prs"]) | {n})
            self.save(entry)
        return (False, "; ".join(failed)) if failed else (True, "")


@dataclasses.dataclass(frozen=True)
class RemindUnreviewed(Unreviewed):
    """Comment on the area's open `issue` that `prs` still change it."""
    state_dir: str
    area: str
    issue: int
    prs: tuple
    day: str

    def describe(self):
        return "remind #%d that %s still change %s" % (
            self.issue, numbered(self.prs), self.area)

    def run(self, github, repo):
        ok, why = github.write(
            "POST", "repos/%s/issues/%d/comments" % (repo, self.issue),
            body="Open pull requests still change `%s`, which no "
            "reviewer's `reviews` globs cover:\n%s\n%s" % (
                self.area, pr_lines(self.prs), merger.SIGNATURE))
        if not ok:
            return False, why
        entry = load_unreviewed(self.state_dir).get(self.area)
        if not isinstance(entry, dict) or entry.get("issue") != self.issue:
            entry = {"issue": self.issue, "prs": []}
        entry["said"] = self.day
        self.save(entry)
        return True, ""


def unreviewed(ctx):
    """Rule 19: for each area open pull requests change that no reviewer
    covers, its issue opened (the triage owner's turn) or the new pull
    requests added to it, each told once; and, on a day nothing else
    was written to it, a reminder on the open issue. Nothing without
    the engine's state directory to record it in."""
    state_dir = getattr(ctx, "state_dir", None)
    if not state_dir:
        return []
    state = load_unreviewed(state_dir)
    day = ctx.when.strftime("%Y-%m-%d")
    triage = OWNERS["triage"]
    label = OWNER_PREFIX + triage
    decisions = []
    for area, found in sorted(uncovered(ctx).items()):
        known = state.get(area) if isinstance(state.get(area), dict) else {}
        issue = area_issue(area, known, ctx)
        told = set(known.get("prs") or []) \
            if issue is not None and known.get("issue") == issue else set()
        prs = tuple((n, tuple(sorted(found[n]))) for n in sorted(found))
        new = tuple(p for p in prs if p[0] not in told)
        if new and issue is None:
            decisions.append(Decision(
                None, triage, 19, "%s %s %s" % (area, numbered(new), day),
                "No reviewer covers %s: %s change it. Give it a reviewer in "
                "the configuration; its issue is labelled %s." % (
                    area, numbered(new), label),
                actions=(ReportUnreviewed(state_dir, area, None, new, label,
                                          day),),
                reminder=ONCE))
        elif new:
            decisions.append(Decision(
                None, None, 19, "%s %s %s" % (area, numbered(new), day), None,
                kind="surface", suppressed="%s change %s, which no reviewer "
                "covers (#%d)" % (numbered(new), area, issue),
                actions=(ReportUnreviewed(state_dir, area, issue, new, label,
                                          day),)))
        elif (known.get("said") or "") < day:
            decisions.append(Decision(
                None, None, 19, "%s %s" % (area, day), None, kind="surface",
                suppressed="#%d: %s still change %s, which no reviewer "
                "covers" % (issue, numbered(prs), area),
                actions=(RemindUnreviewed(state_dir, area, issue, prs, day),)))
    return decisions


# --- lookups ----------------------------------------------------------------


# What each number named in an open issue's `Blocked-by:` lines (or a
# draft's, for rule 0c) is now, for those that aren't open issues or
# pull requests themselves.
BLOCKER_QUERY = """
query($owner:String!, $name:String!) {
  repository(owner:$owner, name:$name) {
%s
  }
}"""
BLOCKER_FIELD = ("    n%d: issueOrPullRequest(number:%d) {\n"
                 "      ... on Issue { state closedAt }\n"
                 "      ... on PullRequest { state closedAt }\n"
                 "    }")


def attach_blockers(github, repo, prs, issues):
    """Give each open issue a `blockedBy` map, {number: {"state",
    "closedAt"} or None}, for the numbers its `Blocked-by:` lines name,
    and each draft too when rule 0c is on. An open item is known from
    the listing; the rest are asked for in one more query, only when
    there are any. None: names nothing."""
    owner, name = repo.split("/")
    open_now = {i["number"] for i in prs + issues}
    covered = issues + [p for p in prs if DRAFT_HOURS and p.get("isDraft")]
    named = {i["number"]: merger.blockers_of(i) for i in covered}
    ask = sorted(set().union(*named.values()) - open_now) if named else []
    def look_up(numbers):
        query = BLOCKER_QUERY % "\n".join(BLOCKER_FIELD % (b, b)
                                           for b in numbers)
        repo = github.api("graphql", "-f", "query=" + query,
                          "-F", "owner=" + owner, "-F", "name=" + name
                          )["data"]["repository"]
        return {b: repo.get("n%d" % b) or None for b in numbers}
    found = {}
    if ask:
        # GitHub fails the whole query when one number names nothing, so
        # a mistyped line mustn't stop every run: ask one at a time then,
        # and leave out what still fails.
        try:
            found = look_up(ask)
        except Exception:  # noqa: BLE001 - gh's failures vary
            for b in ask:
                try:
                    found.update(look_up([b]))
                except Exception:  # noqa: BLE001
                    found[b] = None
    for item in covered:
        item["blockedBy"] = {
            b: {"state": "OPEN", "closedAt": None} if b in open_now
            else found.get(b) for b in named[item["number"]]}


# Whether any open pull request has each base as its head branch, for
# those no pull request in the listing has: one count per base, the
# names passed as variables.
BASE_QUERY = """
query($owner:String!, $name:String!%s) {
  repository(owner:$owner, name:$name) {
%s
  }
}"""
BASE_FIELD = ("    b%d: pullRequests(states:OPEN, headRefName:$b%d, "
              "first:1) { totalCount }")


def attach_bases(github, repo, prs, issues):
    """Rule 0a's lookup, when it is on: give each pull request based on
    another branch than the default `baseOpen`, whether an open pull
    request has that branch as its head. One in the listing answers
    it; the rest are asked for in one more query, only when there are
    any. None: GitHub couldn't say, which wakes nobody."""
    if not RETARGET:
        return
    stacked = [p for p in prs
               if p.get("baseRefName") not in (None, "", merger.MAIN)]
    heads = {p.get("headRefName") for p in prs}
    ask = sorted({p["baseRefName"] for p in stacked} - heads)
    found = {}
    if ask:
        owner, name = repo.split("/")
        query = BASE_QUERY % (
            "".join(", $b%d:String!" % i for i in range(len(ask))),
            "\n".join(BASE_FIELD % (i, i) for i in range(len(ask))))
        args = ["graphql", "-f", "query=" + query,
                "-F", "owner=" + owner, "-F", "name=" + name]
        for i, base in enumerate(ask):
            args += ["-f", "b%d=%s" % (i, base)]
        try:
            answer = github.api(*args)["data"]["repository"]
            found = {base: ((answer.get("b%d" % i) or {}).get("totalCount")
                            or 0) > 0 for i, base in enumerate(ask)}
        except Exception:  # noqa: BLE001 - gh's failures vary
            pass  # unknown: `baseOpen` None
    for pr in stacked:
        base = pr["baseRefName"]
        pr["baseOpen"] = True if base in heads else found.get(base)


lookups = (Lookup("blockers", attach_blockers),
           Lookup("bases", attach_bases),
           Lookup("red builds", attach_red_builds))
