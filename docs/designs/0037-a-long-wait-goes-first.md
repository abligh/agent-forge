# 0037: A pull request that has waited long goes first

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-27

## Summary

The Merger takes candidates oldest first, but three of its other rules
can pass an old candidate again and again, and nothing in them weighs
how long it has waited. With `merge.age_first`, a candidate that has
waited `after_minutes` in the queue is *overdue*. An overdue one goes
ahead of every candidate but the one in flight, nobody newer passes
it, and it never yields.

## What happened

On one project, a pull request (P) became a candidate at 09:50 and was
still waiting six hours later, with two more stacked on it. By 15:12,
45 others had merged, 38 of them queued after it. Until then P was
never predicted, never held and never yielded. It
sat 4th to 15th in the queue, behind newer ones, because:

1. **A skipped prediction loses its place.** `predict_ahead` skips a
   candidate that conflicts with one predicted ahead of it, so that
   those behind it are still tested. The next tick's `locked_order`
   then keeps every predicted candidate ahead of the skipped one. P
   conflicted with an older candidate (Q) that was itself handed back
   three times. Each newly approved candidate that didn't conflict was
   predicted, locked, and went ahead of P. There was always a new one.
2. **Right of way ignores age.** From 15:12, P was held for two newer
   candidates in turn, each of which had earned right of way
   (`merge.right_of_way`) with conflict hand-backs of its own.
3. **Yielding ignores age.** `merge.yield_after_hand_back` moves a
   candidate back two places per hand-back, however long it has waited.

Across the last day on that project, from first entering the queue to
merging: median 55 minutes, p75 122, p90 211. 47 of 186 merges
waited over two hours, and 14 over four.

## Design

A new setting, `merge.age_first: {after_minutes: N}` (off by default).

**Waiting time.** A tick records, in `waiting-since.json` in the
state directory, when it first saw each pull request as a candidate.
The entry survives a hand-back or a new head, since a hand-back for a
conflict is mostly what merged after the approval, not the pull
request's fault. It is dropped when the pull request merges or closes,
or when it hasn't been a candidate for 24 hours. A pull request whose
time since then is at least `after_minutes` is overdue. The record
gains `overdue: {pr: minutes}`.

**A. Order.** `order` puts overdue candidates first, after only urgent
ones (`labels.urgent`, which a person set) and the one a `test_ahead`
run is in flight for. Among overdue ones, the longest waiting goes
first. `locked_order` no longer keeps a predicted candidate ahead of an
overdue one: its prediction is spent, as an urgent label would spend it
today. Blockers and right of way come after overdue ones.

**B. Predictions.** `predict_ahead` never skips an overdue candidate.
If one conflicts with a candidate predicted ahead of it, the list ends
at the overdue one, so nothing behind it is predicted past it. Since
overdue ones are at the front, this mostly happens only behind the one
in flight.

**C. Right of way.** A candidate with right of way doesn't hold an
overdue one that has waited longer. The holder is held instead, behind
it, by `hold_conflicting`'s rule.

**D. Yield.** An overdue candidate yields no places.

A batch being merged (designs/0035) keeps its members' places; an
overdue candidate goes next after it.

## What it costs

- **Predictions are spent.** An overdue candidate taking the front
  spends the runs already started for those it passes. With a 90-minute
  threshold, 68 of the last day's 186 merges waited longer. Most
  of those were at the front anyway; the spend comes only when an
  overdue one passes a locked prediction.
- **A conflicting pair goes in order of age, not of readiness.** When
  an overdue candidate and a newer one conflict, the newer one waits,
  and goes back to its author if it then conflicts with the default
  branch. That is the point: today the old one always loses.

## Setting

90 minutes for a project that merges about ten an hour (well under the
p75 above, so it touches the tail and not the middle).

## Rollout

Off by default. A project turns it on with `merge.age_first` in its
configuration, once the release with it is pinned. Measured after a
day: p90 and maximum queue-to-merge time, merges per hour, and how
many predictions an overdue candidate spent.
