# 0052: The build check runs first what can merge first

* Status: approved (Alex, 2026-11-13)
* Author: XMPPEngineer
* Date: 2026-11-13

## Summary

When its queue is deep, `forge-pr-check` orders the heads nobody has
approved yet by **when each head was pushed**. With
`max_wait_minutes`, every head is soon overdue, so in practice the
order is oldest head first. That order ignores what the Merger
knows:

* A rebase makes a new head. The pull request goes to the back of a
  queue it had nearly reached the front of.
* A pull request that others wait on (a lower stack layer, an item
  named in someone's `Blocked-by:`) waits behind ones nothing depends
  on.
* A pull request the Merger couldn't merge even if it were green
  (`Blocked-by:` an open item, stacked on an unmerged layer) takes a
  slot ahead of ones it could merge.

This design orders those heads by how soon each pull request could
merge, using what the Merger already writes. It adds no capacity. It
cuts the time from a pull request's first push to its merge by
spending slots on the pull requests next in line.

## Evidence

One project's build check over 24 hours (2026-11-12/13), 7 slots on a
96-core host at load 84:

* 411 runs of pull-request heads, about 2 per merge. The median green
  run took 17.7 minutes.
* The queue held 30 to 100 heads all day. The median head waited
  176 minutes to start, and the slowest tenth over 10 hours.
* Of the 36 heads waiting at 01:15Z, 33 were of pull requests
  nobody had approved. `approved_first` already runs approved heads
  first, so the Merger was not starved. The slow part was the first
  green, which review waits for.
* 105 runs (a quarter) ended red at the `merge` step: by the time
  they ran, the head no longer merged with the default branch. Each
  such pull request came back as a new head, at the back of the queue.

## Design

### 1. A pull request's place, not a head's

The check keeps `first_waiting`, in its state, per pull request: when
any head of it first started waiting. A new head keeps it. It is
forgotten when the pull request closes, or when no head of it has
waited for 24 hours. A head's age for ordering, and for
`max_wait_minutes`, is its pull request's `first_waiting`, not the
head's own `since`. A rebase then keeps its place.

### 2. Merge order for the rest

Among the heads that go after the existing tiers (urgent, the
default branch's traced run, a red default branch's fixes, the
Merger's own head, approved heads, predictions), the order becomes:

1. **Unblockers**: the pull request is a lower layer of a stack with a
   layer above it open, or is named in an open item's `Blocked-by:`.
   Unblocking one lets others move. The facts come from the Merger's
   `next.json` and from `blocked`, which it already writes.
2. **Everything else**, oldest `first_waiting` first.
3. **Held**: the Merger holds it until another item merges
   (`Blocked-by:` an open item, or stacked on an unmerged layer, as
   `next.json`'s `blocked` lists today). These go last, as approved
   ones held this way already do.

`max_wait_minutes` still puts an overdue head ahead of everything below
the Merger's own head. Overdue is now counted from `first_waiting`, so
the cap bounds how long a pull request waits, not a head.

### 3. The STARTED line says why

`, first: it unblocks #N` or `, first: waiting since <time> (pull
request)`, as the existing tiers say theirs.

## Configuration

`checks.order: "merge"` turns on §2. Without it, only §1 changes how
age is counted, and §1 is always on: a rebase losing its place is a
fault in any order. Default `"age"`, today's order. Each project picks
its own setting.

## Trade-offs and failure modes

* **Unblockers could starve the rest.** That is bounded by
  `max_wait_minutes`, as today.
* **A pull request that pushes often keeps its old place.** That is
  intended: the Merger cares about the pull request, not the commit.
  `max_wait_minutes` treats each pull request the same way.
* **Stale Merger files.** A `next.json` older than MERGER_FRESH names
  nothing, as today, and the order falls back to `first_waiting`.

## Related, not in this design

The same day, 64 of the Merger's train runs were stopped part-way
(`stop_spent`), about 21 slot-hours. Most were caused by pin and
config merges made before `labels.keep_trains` existed. Since then
those keep the trains. The rest come from members leaving a train
mid-run (pushed, left the queue, layers that won't board). That is
the Merger's to reduce, and is left for a later design once a few
days' figures without the pin churn are in.

## Testing

* A new head of a waiting pull request keeps its `first_waiting`. A
  closed pull request's entry is forgotten.
* With `order: "merge"`, a lower stack layer and a `Blocked-by:` target
  go before an older ordinary head, and a held head goes last.
* `max_wait_minutes` counts from `first_waiting`.
* The STARTED reasons; config validation.

## Implementation plan

1. §1 and its tests: always on, no config.
2. §2, `checks.order`, docs/config.md and the forge-pr-check docstring.
3. A release, then one config line in a project to turn on §2.
