# 0013: A branch of its own for each agent

* Status: draft. Not for approval: it is here to be measured against
  (see "What to measure first") before anything is proposed.
* Author: XMPPEngineer
* Date: 2026-10-12

## Summary

Every pull request goes into the default branch, one at a time, through
one merge queue. An agent whose change is approved often can't build
on it until it merges. While she waits, she either starts something
unrelated or sits idle. The queue keeps getting shorter (designs 0001,
0002, 0012), but its length isn't what matters most. What matters is
the rate at which agents' work lands, and how much of an agent's day is
spent waiting for her own work to land.

This draft sets out three ways to let an agent keep building on her own
approved work before it reaches the default branch:

* **A. Feature branches.** Each agent works on long-lived branches of
  her own. Small reviewed pull requests go into her branch, and the
  whole branch is merged into the default branch when a feature is done.
* **B. Stacked pull requests.** An agent bases a pull request on her own
  unmerged one. The Merger moves it onto the default branch when the
  one below merges.
* **C. One growing draft per feature.** An agent keeps adding commits to
  one draft pull request. Reviewers review it on demand, each time from
  where they last stopped.

A is the most capable and needs the most machinery. B and C need
little. Which of them, if any, is worth building depends on the
measurements below.

## What to measure first

On a project with the queue as it is now, over at least a full day:

1. **Approval to merge**, per pull request: the median, the 90th
   percentile, and how it varies with the length of the queue.
2. **Waiting on her own work**: for each agent, the time she held an
   approved, unmerged pull request and had no other work she could do.
   Also, how often her next pull request was based on, or needed, one
   still in the queue.
3. **Merges per hour**, against how busy the build check was. If the
   check slots are full most of the day, check capacity is the limit,
   and A adds checks (syncs and promotions) without adding capacity.
4. **Conflicts**: how often a queued pull request is handed back for a
   conflict with the default branch, and between which agents. A
   assumes agents mostly touch different code.

If (2) is small, none of this is worth building. If (2) is large and
(4) is small, A pays. If (2) is large, but mostly one pull request
waiting on the one before it, B or C may be enough.

## A. Feature branches

### Branches and owners

A feature branch is named `<prefix><agent>/<name>`, e.g.
`feature/agent-a/importer` (`branches.prefix`, default `feature/`). The
agent in its name owns it. An agent may have as many as she likes:
apart from checks, a branch costs nothing to run.

### Into a feature branch

A pull request into a feature branch is reviewed as today. Reviewers
review what goes into feature branches, so problems are caught while
they are small. It is checked by forge-pr-check against its base (the
merge with the feature branch's tip, as design 0007 does with the
default branch). It is merged by the Merger with a queue of its own,
one queue per base branch.

Usually only the owner writes to a branch, so its queue is short and
rarely conflicts. The check of a pull request's merge with its base is
usually already done when it is approved, because nothing else moved
the base meanwhile.

Into a feature branch the Merger may squash: one commit per reviewed
pull request is the granularity worth keeping.

### Keeping up with the default branch

The default branch is merged into a feature branch by machine, at
these times:
- just before promotion;
- after a merge into the feature branch, but only if at least
  `sync.minutes` have passed, or `sync.commits` have landed on the
  default branch, since the branch last took it in.

A branch nobody is merging into is left alone. There is no timer, which
would merge into idle branches for nothing.

A sync that merges cleanly is pushed straight to the branch; the
branch's check then runs on it as on any new head. A sync that
conflicts becomes a pull request into the feature branch, from a
`<branch>-sync` branch holding the conflicted merge, plus an issue for
the owner. She resolves it on that branch, and it is reviewed like
anything else. So every change a person wrote reaches the branch
through a review.

### Promotion

A promotion is a pull request from the feature branch into the default
branch, opened by the owner when the feature is done, or by the Merger
when `promote.hours` have passed since the last one.

The Merger merges it with a merge commit, never a squash: a squash
would throw away each reviewed pull request's commit, and leave the
branch in conflict with its own work at its next sync.

A promotion is not reviewed again by a person. A tool checks instead
that every commit it adds to the default branch came through a
reviewed pull request into the branch, or is a clean machine sync. It
is checked like any pull request, against the default branch.

After it merges, the branch carries on from the merge, or is deleted
if the owner is done with it.

### Work that two branches need

Sometimes agent A needs a change that is on agent B's branch, or B's
branch duplicates A's work. Then:
- A asks B, over the usual channels, to carry a particular change.
- To offer one, A opens a pull request into B's branch (from a branch
  off B's), which B's reviewers review like any other.
- Once it is on B's branch, A can take it by merging B's branch, or
  the commit, into her own, through a pull request.

Reviewers, who see every pull request into every branch, are the ones
placed to notice two branches doing the same thing. That becomes a
duty of their role.

### Issues and closing keywords

GitHub closes an issue from a pull request's closing keywords only
when the pull request merges into the default branch. So an issue
fixed on a feature branch stays open until promotion.

The router and the Merger read `closingIssuesReferences`, and must
treat an issue whose fix has merged into a feature branch as fixed
but not yet released. It is nobody's turn, and it is not a reason to
hold a pull request (today's rule 0b). It closes when the promotion
merges.

### What changes in agent-forge

Every tool has to know about more than one base branch:
- **forge-merger**: a queue per base branch; yields and holds per
  queue; the merge method per base branch (squash into a feature
  branch, merge commit into the default branch); syncs and promotions.
- **forge-pr-check**: checks against any base branch. Dependency
  records and main checks stay for the default branch only.
- **The receiver and router**: events and turns for pull requests into
  any branch; the rule for closing keywords above.
- **A promotion checker**: the "only reviewed commits" proof.

Repository settings: merge commits allowed, and branch protection or a
ruleset for `<prefix>*`.

## B. Stacked pull requests

An agent opens a pull request whose base is her own unmerged branch.
It is reviewed and checked against that base as usual.

When the pull request below it merges, the Merger:
- retargets the stacked pull request to the default branch;
- rebases it onto the merge, dropping the commits the squash already
  carried (`git rebase --onto`).

A clean rebase is pushed. A conflicting one is handed back to the
author.

This needs:
- a retarget-and-rebase step in the Merger;
- forge-pr-check checking against a base other than the default branch;
- a rule that a stacked pull request can't merge before the one below
  it.

No long-lived branches, no syncs, no promotions.

## C. One growing draft per feature

An agent keeps one draft pull request per feature and keeps pushing to
it. She asks for a review when she wants one (a label, or a message).
The reviewer reviews only the commits since her last review. It is
marked ready, and joins the queue, when the feature is done.

This needs:
- an on-demand review turn in the router;
- a "since the last review" view for reviewers;
- merging it with a merge commit, to keep the commits.

It integrates as late as A, but with no extra branches to look after.

## Open questions

* Can a feature branch go stale by being left unpromoted for days? A
  limit (`promote.hours`) forces it out, but a half-finished feature
  then lands in the default branch. Is that acceptable?
* Is a build check of each sync worth it, or only of the promotion?
* Should the per-branch queues get check slots of their own, so that a
  busy branch can't starve the default branch's queue?
* How much does history with merge commits cost the tools that walk
  it (dependency tracing, `git log` in issue texts)?
