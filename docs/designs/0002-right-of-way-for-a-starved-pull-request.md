# 0002: Right of way for a starved pull request

* Status: proposed
* Implemented: v0.4.7 (f7027d7), each key off until configured
* Author: XMPPEngineer
* Date: 2026-10-10

## Summary

One pull request was handed back to its author five times in three
hours, each time because its head conflicted with `main`. Each time,
she resolved the conflict, and the pull request was approved again and
built again. By then, other pull requests had merged, the head conflicted
with `main` again, and the round started over. Each round (resolve,
approve, build) took longer than the gap between merges of the
pull requests it overlapped with, so it could never land.

Two parts of the merge queue make this worse, and neither of them helps:

* **`merge.hold_conflicting` does not help.** It holds a pull request
  that conflicts with one *ahead of it in the queue*. A pull request
  being fixed is not in the queue: its new head has no approval yet.
  So nothing is held behind it, and whatever conflicts with it merges
  first.
* **`merge.yield_after_hand_back` makes it worse.** A conflict with
  `main` counts as the pull request's own failure, so each round also
  moves it further back. The pull request that was overtaken is the
  one that gets penalised.

This proposes two keys, each off until a project sets it:

* `merge.yield_ignores_conflicts` stops a conflict with `main` from
  counting toward the yield.
* `merge.right_of_way` holds back the pull requests that conflict with
  one that has been overtaken repeatedly. It holds them while that pull
  request's fixed head is approved again and built, for a bounded time.

## Goals

* **A pull request that keeps being overtaken lands**, after at most a
  few rounds and with no one coordinating by hand.
* **Nothing else stops.** Pull requests that don't conflict with it
  keep merging.
* **The hold is bounded.** A fix that is never approved, or a build
  that never finishes, can't hold up the others for long.
* **No rule about what merges changes.** Approvals, the build check,
  the gate and what an approval covers stay as they are.

## Non-goals

* **Resolving conflicts automatically.**
* **Reordering work or reassigning it.** Who works on what, and what
  the reviewers review first, is still up to the people and agents.
* **Changing yield or holds for other kinds of hand-back.**

## Design

### Don't count a conflict with `main` toward the yield

`merge.yield_ignores_conflicts: true` (`forge-merger`). A hand-back
whose reason is a conflict with `main` (`it conflicts with main, so it
can't be updated`) is not counted by `merge.yield_after_hand_back`.
Hand-backs for other reasons (a red check or a failed gate) count as
before. The default, false, keeps the behaviour of design 0001.

A conflict with `main` is mostly caused by what merged after the pull
request was approved. Unlike a red build, it says little about the pull
request itself.

### Give right of way to a pull request that keeps being overtaken

`merge.right_of_way: {"after_conflicts": N, "minutes": M}`
(`forge-merger`).

A pull request P **earns** right of way when it has been handed back to
its author for a conflict with `main` N times in the last 24 hours
(distinct heads, counted like the yield's hand-backs). It **holds**
right of way for a head H when all of these are true:

* P is open, and H is its head.
* H does not conflict with `main`. It can't be merged while it does, so
  holding others for it would only delay them.
* No more than M minutes have passed since the Merger first saw H with
  right of way earned. `right-of-way.json` in the state directory
  records that moment, per pull request and head, so a restart does
  not reset the clock.
* P has not been handed back at H for any other reason.

While P holds right of way:

* **Every other candidate whose head conflicts with H is held.** A held
  one is neither updated, merged, handed back nor predicted. The `held`
  reason reads `conflicts with #P, which has right of way`. As with
  `hold_conflicting`, a live tick says so on each held pull request once
  per head and P, and it keeps its approval. The comparison is `git
  merge-tree --write-tree` of the two heads, as for `hold_conflicting`.
  Heads that git can't compare are not held.
* **Once P is a candidate (approved and covered), it goes first.**
  Only the pull request a `test_ahead` run is in flight for stays ahead
  of it, so that run is not wasted. P never yields while it holds right
  of way.
* **Pull requests that don't conflict with H go on merging.**

When P merges, or M minutes pass, or P stops qualifying, the holds lift
at the next tick. A pull request held this way is then judged as usual.

If P loses right of way without merging and is later handed back for a
conflict again, it can earn right of way again. Each period is bounded
by M, so the others are never held for more than M minutes at a time.

The record of each tick gets a `right_of_way` key, `{pr, head, since}`
or null. The Merger posts nothing on P itself. P's author and reviewer
are already told about the hand-back and the re-review it needs, and
the hold comments on the others name P.

## Config and file-format shape

In a project's `.agent-forge/config.json`, beside the existing keys:

```json
"merge": {"yield_ignores_conflicts": true,
          "right_of_way": {"after_conflicts": 2, "minutes": 60}}
```

`docs/config.md` defines both keys. `right-of-way.json`, in the
Merger's state directory, maps each pull request number to
`{head, since}`. Entries for pull requests that are no longer open are
dropped.

## Trade-offs and failure modes

* **The others wait up to M minutes.** That is the price, and M bounds
  it. Only pull requests that conflict with P wait. They would have
  needed rebasing after P merged anyway.
* **A clean pairwise merge does not guarantee a clean three-way
  result.** A pull request that merges cleanly with both `main` and H
  can still leave `main` conflicting with H. This is rare, and if it
  happens P is handed back once more and keeps the right of way it has
  earned.
* **Two pull requests could both earn right of way.** The older one
  (by the queue's order) has it. The other is held behind it if they
  conflict, and otherwise both proceed.
* **It costs git work every tick:** one `merge-tree` of H against each
  other open approved head, as `hold_conflicting` already does.
* **Only candidates are held.** A pull request not yet approved is
  left alone, and its author can keep working on it. It is held only
  once it would otherwise merge.

## Testing strategy

* **agent-forge's tests** for each key: counting, earning, the clock
  and its persistence, expiry, holds, the order, and the in-flight
  exception.
* **In a project, a dry run of the Merger** with the keys set, run
  beside the live one and compared against its log: which holds it
  would have made, and when.
* **Afterwards, a day's numbers:** repeat conflict hand-backs of one
  pull request, and the longest any pull request waited.

## Implementation plan

1. Both keys in one release, off by default.
2. Each project turns on what it wants in its own configuration.

## Open questions

* **N.** Default suggestion: 2. A pull request overtaken twice has shown
  it is in a race it is losing.
* **M.** Default suggestion: 60 minutes. That covers a re-review and a
  build at the times observed, with room to spare.
