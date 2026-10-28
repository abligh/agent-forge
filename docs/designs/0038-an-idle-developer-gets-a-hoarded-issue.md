# 0038: An idle developer gets an issue someone else isn't starting

* Status: approved (Alex Bligh, 2026-10-28,
  https://github.com/abligh/agent-forge/issues/165#issuecomment-6459528758)
* Implemented: v0.4.131, off until configured.
* Author: XMPPEngineer
* Date: 2026-10-28

## Summary

Rule 16 tells `idle_offer.report_to` when a developer is idle with
nothing it can act on. It doesn't say what could be handed over, so
work stays with whoever owns it, even when that owner has far more
actionable issues than it can start. With
`router.idle_offer.hand_over_above: N`, the report also names the
agent owning the most actionable issues it hasn't started, when that
is more than N, and asks for one of them to be handed to the idle
developer.

## What happened

On one project on 2026-10-28, one agent owned 16 of the 34 open
issues that were not parked, and 46 of all 66. Several developers went
idle again and again through the evening. Rule 16 reported each time
to `report_to`, which was that same agent. Nothing moved until a
person asked why so many agents were idle. The agent then handed out
six issues in a few minutes: two to the idle developer, and four
started, closed or explained.

The router already knew everything needed: who was idle, and who
owned actionable issues it had not started. It only reported the
first.

## Design

A new setting, `router.idle_offer.hand_over_above` (an integer, at
least 0; off without it).

When rule 16 reports an idle developer, it counts, for every agent,
its **unstarted** issues: those `owned_issues` finds actionable (owned,
not waiting, not blocked, not referenced by the owner's own open pull
request) that also carry no `labels.status_prefix` + `in-progress`
label (default `Status:in-progress`). If the agent with the most has
more than `hand_over_above`, the report adds:

> P owns K actionable issues it hasn't started (#a, #b, #c, …): hand
> one to D, or say why not.

P is the agent with the most (the owner of the oldest such issue breaks
a tie), and the list is P's unstarted issues, oldest first, at most
ten. The report still goes to `report_to`, once per idle period, as
today; `report_to` plans the work and moves the `Owner:` label. When P
is `report_to` itself, the message is the same, so the agent hoarding
is asked directly.

`hand_over_above` keeps the value of context: an owner may hold that
many unstarted issues it knows best before anyone is asked to take
one. A project picks it; 3 would have fired on the evening above, and
not on a lead holding its next two or three tasks.

Nothing is reassigned automatically. Which issue suits which agent is
a judgment the router can't make.

## What it costs

- A longer rule 16 report, only when an agent holds more than N
  unstarted issues.
- An issue labelled in progress but not being worked on still counts
  as started. That is the owner's word, as it is elsewhere.

## Rollout

The code lands off by default. A project turns it on with
`hand_over_above` in its `router.idle_offer`. Measured after a week:
how long developers stay idle with nothing, from the relay's
directory, and how many issues change owner after such a report.
