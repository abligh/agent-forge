# 0039: Red builds go first for their owner, and pass on if untouched

* Status: proposed
* Implemented: next release, off until configured.
* Author: XMPPEngineer
* Date: 2026-10-31

## Summary

designs/0011 and designs/0028 open one issue while the default branch
or the nightly is red, owned by whoever owns the failed steps, and put
the pull requests that fix it first. Nothing makes anyone start such a
pull request. The router treats the issue like any other: its owner is
offered it only when idle, and then only if it is her oldest
actionable issue.

With `router.red_first`, the default rules:

1. make an open red-build issue its owner's turn at once, busy or not,
   until a pull request references it or the owner says on the issue
   what is happening (rule 20);
2. put red-build issues before every other issue in rule 15's offer;
3. after `fallback_hours` with no pull request referencing it and
   nothing from its owner, offer it to an idle developer (rule 21).

## What happened

On one project the nightly went red twice in a day. The issue saying
so was opened at once, owned by the lead, who was busy with other work
all day. Nobody looked at it for hours: the router never woke the
owner for it, since she was never idle, and nothing offered it to
anyone else. A person noticed, reassigned it, and asked why nothing
made agents look at a red nightly before everything else.

When it was reassigned, the lead had in fact opened a fix an hour
before, and the new owner opened a second one, duplicating it. A
fallback must not hand over an issue a pull request already
references.

## Design

A new setting, `router.red_first`, an object; off without it.
`fallback_hours` (a number above 0, default 2) is how long a red-build
issue may go untouched before rule 21 offers it to someone else.

### Which issues are red-build issues

The router reads what the tools that opened them keep, on the same
host:

- the nightly's issue: `issue` in forge-nightly's `nightly.json`,
  while `issue_open` is true and the file is fresh (as
  `agent_forge.nightly.fixes` reads it);
- the default branch's issue: `main_red.issue` in forge-pr-check's
  `state.json`.

A lookup, `red builds`, reads both once a fetch and marks the issue
(`redBuild`), as `blockers` marks what an issue's `Blocked-by:` lines
name.

The **red event** of such an issue is when its build was last
reported red: for the nightly, the end of its newest red run
(`red.ended`), else `opened`; for the default branch, the issue's
newest comment by the poster (`checks.poster`), else its creation. A new red run is a
new event, so a fix that didn't work wakes the owner again.

An issue is **in hand** when an open pull request, by anyone,
references it (the router's `referenced_by`: closing references,
`#N`, its URL or "issue N" in the body).

An issue is **parked** when it has a quiet label or any label starting
`Status:waiting-` (not only the two rules 9 and 11 read): a project's
own waiting label parks it too, as it does for rule 15.

### Rule 20: the owner's turn at once

After the quiet, waiting and unowned rules (so a person can still park
it) and before rule 12: when an open red-build issue has an owner, is
not parked or in hand, and the owner hasn't commented on it since its
red event, it is the owner's turn:

> #N says the nightly is red (since 05:40Z), and no pull request
> references it. Red builds go first: put other work down and fix it,
> or say on the issue who is fixing it.

("the default branch" for the other.) The engine reminds her every
`relay.remind_seconds` as for any turn. It ends when a pull request
references the issue, or when she comments; after a comment the
ordinary rules apply.

### Rule 15: red first

In the idle offer, an agent's actionable red-build issues come before
her other actionable issues, oldest first among themselves, and the
offer says "Red builds go first".

### Rule 21: someone else, if the owner doesn't

Needs `router.idle_offer` (it is what knows who is idle). On a sweep,
for each open red-build issue that is not parked or in hand, whose red
event is at least `fallback_hours` old, and whose owner hasn't
commented on it in the last `fallback_hours`: the idle developer idle
longest (the name breaks a tie), other than the owner or her stand-in,
with no turn of her own and of a kind rule 16 reports, is offered it,
once for that red event:

> #N says the nightly is red since 05:40Z, and for 2 h no pull request
> has referenced it and its owner, Lead, hasn't commented. You are idle:
> take it (move its owner label to Owner:Dev and say so on the issue)
> and fix it first, unless a pull request is about to reference it.

("and nobody owns it" for an issue with no owner, which rule 20 can't
wake anyone for.) A developer is offered one red-build issue a run, and
nothing by rules 15 and 16 in that run, so she gets one message.

The engine keys a turn on its agent too, so "once for that red event"
is once to each developer: if the one offered it goes busy without
taking it, a later run offers it to the developer idle longest then,
once. That is bounded by the number of developers, and is what is
wanted when the first one didn't take it.

Nothing is reassigned automatically; the idle developer takes it, as
in designs/0038. With no idle developer, nothing is sent; designs/0028's
escalation still applies at its own hours.

## What it costs

- A busy owner is interrupted. That is the point: a red build blocks
  releases and hides further breakage, so it outranks her current work.
- A comment on the issue ends rule 20 for that red event, so an owner
  can answer "I'm on it" and keep working; rule 21 catches it if
  nothing follows for `fallback_hours`.
- An instance's red issue (designs/0034) lives in that instance's
  state, on its own host, and isn't seen here.

## Rollout

Off by default. A project sets `"red_first": {}` (or with
`fallback_hours`) in `router`, after a release with this is installed.
Measured after a week: hours from a red-build issue opening to the
first pull request referencing it, before and after.
