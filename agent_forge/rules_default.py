"""The default rules for `forge-router`: whose turn each open item is.

`router.rules: "default"` in the configuration chooses these, and so
does leaving it out. The engine, bin/forge-router, fetches, delivers
and keeps state; this says whose turn an item is, from GitHub alone.
docs/router.md is the interface between the two.

The labels are the configuration's `labels`: `owner_prefix` (default
`Owner:`), `waiting_on_owner` (default `Status:waiting-owner`) and
`waiting_external` (default `Status:waiting-external`). "The owner" is
the person whose login is the configuration's `owner`.

**Whose turn it is.** The first rule that matches decides; none means
nobody's.

Pull requests:

1. a draft, or waiting on the owner or on someone outside: nobody's;
2. a Merger hand-back of the current head is the newest thing said (a
   comment by the Merger, "Not merging <head>:", newer than every review
   and every author comment): the approving reviewer's if it starts
   `Not-a-review`, otherwise the author's;
3. a reviewer's newest review is at the head, is `CHANGES_REQUESTED` or
   `COMMENTED`, and has no author comment after it: the author's;
4. the build check's status (`checks.context`) is `failure` or `error`
   on the head: the author's;
5. it is `pending` or absent on the head: nobody's;
6. a reviewer's newest review is `APPROVED`, covers the head, and
   nothing has been said after it (by `forge-merger`'s own test):
   nobody's, it is the Merger's;
7. green and authored by the only reviewer: nobody's, and surfaced
   (printed on every run, sending or not: it is a problem in the
   configuration, not anyone's turn);
8. green, and no reviewer has reviewed it: no reviewer's review at the
   head (or approval covering it), or an author comment after the
   newest one: the pull request's reviewer's.

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

With `router.idle_offer` in the configuration, a run also sweeps for
agents with nothing to do (off without it):

15. an agent the relay's directory shows idle (status `idle`, every
    entry of its session name, for at least `idle_minutes`, default 10),
    with no turn from rules 1-14, which owns an actionable issue: the
    agent's, offered the oldest such issue by creation. An issue is
    actionable when it is labelled `Owner:<Name>`, is waiting neither
    on the owner nor on someone outside, every item its `Blocked-by:`
    lines name is closed or merged (a number naming nothing is left
    out, as for rule 14), and no open pull request by the agent
    references it (a closing reference, `#N` or its URL in the body).
    The event is when the idle period began, and it is about no item,
    so it is offered once per idle period, never reminded, and the
    next idle period offers again;
16. an idle developer (not a reviewer) with no such turn and nothing
    actionable: `idle_offer.report_to`'s, so whoever plans the work
    knows, once per idle period. Not for `report_to` itself, and not
    without `report_to`.

A directory that couldn't be read, or isn't complete, makes nobody
idle: unknown is never idle.

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

import datetime
import fnmatch
import re

from agent_forge.router import ONCE, Decision, Lookup

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
DEFAULT_IDLE_MINUTES = 10
# The engine reads the relay's directory only for the idle offer.
wants_directory = False

# What the rules read of each open item, besides its number.
graphql = {
    "PullRequest": """isDraft headRefOid author { login } body
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
          nodes { commit { oid
            status { contexts { context state createdAt
                                creator { login } } } } }
        }""",
    "Issue": """title author { login } body createdAt lastEditedAt
        labels(first:30) { nodes { name } }
        comments(last:100) {
          nodes { databaseId author { login } createdAt body }
        }""",
}
# Read as lists; `commits` and `headCommit` keep their nodes.
flatten = ("labels", "reviews", "comments", "files")


def configure(project, merger_tool):
    """Set the rules' settings from `project`. `merger_tool`, already
    configured for it, is bin/forge-merger."""
    global merger, MERGER, OWNER, WAITING_OWNER, WAITING_EXTERNAL
    global OWNER_PREFIX, STATUS_CONTEXT, IDLE_OFFER, wants_directory
    merger = merger_tool
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
        "report_to": offer.get("report_to")}
    wants_directory = IDLE_OFFER is not None


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

    def reviewers(self):
        """Every agent of kind `reviewer`, in the configuration's order."""
        return self.project.reviewers()

    def reviewer_logins(self):
        return {self.slug(n) for n in self.reviewers()}

    def reviewer_for(self, pr):
        """The reviewer whose `reviews` globs match the most changed
        lines of `pr`, leaving out its author. A tie, and lines no
        reviewer's globs match, go to the first listed. None when no
        reviewer but the author exists."""
        author = self.who(author_of(pr))
        names = [n for n in self.reviewers() if n != author]
        if not names:
            return None
        score = dict.fromkeys(names, 0)
        for f in pr.get("files") or []:
            lines = (f.get("additions") or 0) + (f.get("deletions") or 0)
            path = f.get("path") or ""
            owner = next((n for n in names if any(
                fnmatch.fnmatchcase(path, glob)
                for glob in self.agents[n].get("reviews") or [])), names[0])
            score[owner] += lines
        return max(names, key=lambda n: (score[n], -names.index(n)))


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


def owner_of(item):
    """The Name of the first `Owner:<Name>` label, by name, or None."""
    for name in sorted(labels_of(item)):
        if name.startswith(OWNER_PREFIX) and name[len(OWNER_PREFIX):].strip():
            return name[len(OWNER_PREFIX):].strip()
    return None


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


def turn(number, who, rule, event, line):
    return {"item": number, "who": who, "rule": rule, "event": event,
            "line": "#%d %s" % (number, line)}


def nobody(number, rule, why, kind="turn"):
    return {"item": number, "who": None, "rule": rule, "why": why,
            "kind": kind}


def route_pr(pr, roster):
    """The turn for one pull request, as `turn` or `nobody` builds it."""
    n, head = pr["number"], pr.get("headRefOid") or ""
    labels = labels_of(pr)
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
    # Rule 3.
    theirs = [r for r in reviews
              if strip_bot(author_of(r)) in reviewer_logins]
    newest = max(theirs, key=lambda r: r["submittedAt"]) if theirs else None
    if (newest is not None and at_head(newest, pr)
            and newest.get("state") in ("CHANGES_REQUESTED", "COMMENTED")
            and spoke <= newest["submittedAt"]):
        said = ("asked for changes" if newest["state"] == "CHANGES_REQUESTED"
                else "commented")
        return turn(n, author, 3, newest["submittedAt"],
                    "%s %s (review %s)" % (roster.who(author_of(newest)), said,
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
    if (newest is not None and newest.get("state") == "APPROVED"
            and covers_head(newest, pr)
            and not merger.said_since(dict(pr, reviews=reviews,
                                           comments=comments), newest)):
        return nobody(n, 6, "the Merger's: approved, nothing said since")
    # Rule 7.
    reviewer = roster.reviewer_for(pr)
    if reviewer is None:
        return nobody(n, 7, "green, and %s's own: no second reviewer"
                      % author, kind="surface")
    # Rule 8.
    seen = [r for r in theirs if at_head(r, pr) or (
        r.get("state") == "APPROVED" and covers_head(r, pr))]
    last = max((r["submittedAt"] for r in seen), default=None)
    if last is None or spoke > last:
        return turn(n, reviewer, 8, max(status.get("createdAt") or "", spoke),
                    "needs your review (green at %s)" % head[:7])
    return nobody(n, None, "no rule matched")


def route_issue(issue, roster):
    """The turn for one issue, as `turn` or `nobody` builds it."""
    n = issue["number"]
    labels = labels_of(issue)
    comments = issue.get("comments") or []
    owner = owner_of(issue)
    # Rule 9.
    if WAITING_EXTERNAL in labels:
        return nobody(n, 9, WAITING_EXTERNAL)
    if WAITING_OWNER in labels:
        # Rule 10.
        answered = owner_answered(n, issue, owner, comments, roster, 10)
        # Rule 11.
        return answered or nobody(n, 11, WAITING_OWNER)
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
    return Decision(d["item"], d["who"], d["rule"], d["event"], d["line"])


def decide(item, ctx):
    """The one decision for an open item."""
    route = route_pr if item.get("__typename") == "PullRequest" \
        else route_issue
    return [as_decision(route(item, ctx.roster))]


# A decision rests on nothing a later fetch could change but GitHub.
recheck = decide


def sweep(ctx):
    """Rules 15 and 16, the idle offer, when it is configured; nothing
    on a run started by an event (`ctx.only`), whose items are all it
    may decide about."""
    if IDLE_OFFER is None or ctx.only is not None:
        return []
    return idle_offers(ctx)


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
    each agent the directory shows idle for at least `idle_minutes`. An
    agent is matched by its session name; one listed more than once is
    idle only when every entry is, since the latest of them. Nobody is
    idle when the directory is unknown."""
    listing = ctx.directory
    if not isinstance(listing, dict) or listing.get("complete") is not True \
            or not isinstance(listing.get("agents"), list):
        return {}
    sessions = {ctx.roster.session_name(a): a for a in ctx.roster.agents}
    seen = {}
    for entry in listing["agents"]:
        if not isinstance(entry, dict):
            continue
        agent = sessions.get(entry.get("name"))
        if agent is None:
            continue
        since = parse_stamp(entry.get("idle_since")) \
            if entry.get("status") == "idle" else None
        seen.setdefault(agent, []).append((since, entry.get("idle_since")))
    cutoff = ctx.when - datetime.timedelta(minutes=IDLE_OFFER["idle_minutes"])
    idle = {}
    for agent, entries in seen.items():
        if any(since is None for since, _ in entries):
            continue
        since, stamp = max(entries)
        if since <= cutoff:
            idle[agent] = stamp
    return idle


# A pull request body's reference to an issue: `#N`, or its URL.
REFERENCE_RE = re.compile(
    r"(?<![\w/&])#(\d+)\b|/issues/(\d+)\b")


def referenced_by(pr):
    """The issue numbers a pull request references: GitHub's closing
    references, and every `#N` or issue URL in its body."""
    refs = pr.get("closingIssuesReferences") or {}
    nodes = refs.get("nodes") if isinstance(refs, dict) else refs
    numbers = {n.get("number") for n in nodes or [] if isinstance(n, dict)}
    for m in REFERENCE_RE.finditer(pr.get("body") or ""):
        numbers.add(int(m.group(1) or m.group(2)))
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
    something they name is open, or they wait on someone, or its own
    open pull request already references them."""
    mine = ctx.roster.login_of(agent)
    in_hand = set()
    for pr in ctx.prs:
        if author_of(pr) == mine:
            in_hand |= referenced_by(pr)
    actionable, held, waiting = [], 0, 0
    for issue in ctx.issues:
        if owner_of(issue) != agent:
            continue
        labels = labels_of(issue)
        if WAITING_OWNER in labels or WAITING_EXTERNAL in labels:
            waiting += 1
        elif blocked(issue):
            held += 1
        elif issue["number"] not in in_hand:
            actionable.append(issue)
    actionable.sort(key=lambda i: (i.get("createdAt") or "", i["number"]))
    return actionable, held, waiting


def has_turn(agent, ctx):
    """Whether rules 1-14 make any open item the agent's turn."""
    for item in ctx.prs + ctx.issues:
        route = route_pr if item.get("__typename") == "PullRequest" \
            else route_issue
        if route(item, ctx.roster)["who"] == agent:
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


def idle_offers(ctx):
    """Rules 15 and 16: an offer to each idle agent with an actionable
    issue and no turn, and a report to `report_to` of each idle
    developer with none."""
    report_to = IDLE_OFFER["report_to"]
    decisions = []
    for agent, stamp in sorted(idle_agents(ctx).items()):
        if not ctx.roster.is_agent(agent) or has_turn(agent, ctx):
            continue
        actionable, held, waiting = owned_issues(agent, ctx)
        if actionable:
            issue = actionable[0]
            title = (issue.get("title") or "").strip()
            decisions.append(Decision(
                None, agent, 15, stamp,
                "#%d%s is yours and nothing blocks it: start it, or say on "
                "the issue why not (a %s or %s label, or a Blocked-by: "
                "line). You have been idle since %s." % (
                    issue["number"], ' "%s"' % title if title else "",
                    WAITING_OWNER, WAITING_EXTERNAL,
                    since_text(stamp, ctx.when)),
                reminder=ONCE))
            continue
        if not report_to or agent == report_to or \
                ctx.roster.project.kind(agent) == "reviewer":
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
            "%s has been idle since %s with nothing it can act on (%s)." % (
                agent, since_text(stamp, ctx.when), why),
            reminder=ONCE))
    return decisions


# --- lookups ----------------------------------------------------------------


# What each number named in an open issue's `Blocked-by:` lines is now,
# for those that aren't open issues or pull requests themselves.
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
    "closedAt"} or None}, for the numbers its `Blocked-by:` lines name.
    An open item is known from the listing; the rest are asked for in
    one more query, only when there are any. None: names nothing."""
    owner, name = repo.split("/")
    open_now = {i["number"] for i in prs + issues}
    named = {i["number"]: merger.blockers_of(i) for i in issues}
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
    for issue in issues:
        issue["blockedBy"] = {
            b: {"state": "OPEN", "closedAt": None} if b in open_now
            else found.get(b) for b in named[issue["number"]]}



lookups = (Lookup("blockers", attach_blockers),)
