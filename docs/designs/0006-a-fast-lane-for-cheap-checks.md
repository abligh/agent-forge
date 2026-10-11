# 0006: A fast lane for cheap checks

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-11

## Summary

With `checks.depends` (design 0005), a pull request runs only the steps
that read what it changes. A change to one design document can then
need a few seconds of checking. It still waits in the same queue as a
change that rebuilds everything, behind a dozen seven-minute runs. On
one project the queue was an hour deep, so a three-second check took
an hour.

This proposes `checks.fast_lane`: a slot of its own for heads whose
chosen steps are all cheap, so they start at the next tick and wait
for nothing.

## Goals

* **A cheap check waits for nothing.** It doesn't queue behind
  expensive ones.
* **Expensive checks lose nothing.** The fast lane is an extra slot;
  the ordinary slots keep their work and their order.
* **Cheap is measured, not declared.** It comes from the steps a head
  would run and how long those steps have taken here.

## Non-goals

* **Ordering by expected cost in general** (shortest job first across
  the ordinary slots). Long checks could then wait for ever behind a
  stream of medium ones.
* **A lane without `depends`.** Without it every head runs every step,
  and nothing is cheap.

## Design

### Which heads are cheap

For each waiting head, each tick, the build check works out the steps
it would run, as the worker does (design 0005): the merge base, the
newest usable dependency record, and the paths changed. To do that
before checkout, it fetches the head into the checkout it keeps for
the Merger. The fetch is cheap, and only heads without an estimate
need one.

A step's cost is the median of its last 20 runs' durations here, from
the verdict log, which already records each step's seconds. A step
with fewer than five runs counts as expensive. A head is cheap when
the sum of its chosen steps' costs is at most `max_seconds`.

When the steps can't be worked out (no record, or a fetch or diff
that fails), the head is not cheap, and it waits in the ordinary queue
as today.

### The lane

`checks.fast_lane: {"max_seconds": 60}` adds one slot that runs only
cheap heads, oldest first, under the same memory limit and CPU weight
as the others. It has its own lock and its own build cache, like any
slot. An ordinary slot that is free may also take a cheap head when no
expensive head waits, so the lane never makes a cheap head wait longer
than today.

A run in the lane that exceeds `max_seconds` × 3 is not stopped:
estimates are estimates. It is logged as `SLOW-LANE` with the steps
that ran and their times, so a bad estimate can be seen.

### What is posted

Nothing new. The verdict and status are the same as for any run. The
worker log names the lane, so a lane run can be told from a slot run.

## Config and file-format shape

```json
"checks": {"depends": true, "fast_lane": {"max_seconds": 60}}
```

`docs/config.md` defines `fast_lane` and `max_seconds`. It is refused
without `depends`.

## Trade-offs and failure modes

* **An extra run at a time.** Cheap heads need little CPU by
  definition, so the lane costs the ordinary slots little. On a host
  short of memory, the lane's own memory limit applies like any slot's.
* **An estimate can be wrong.** A step can be slow for a reason its
  history doesn't show. The cost is one long run in the lane while
  other cheap heads wait, logged as `SLOW-LANE`.
* **One more build cache.** A lane mostly runs steps that compile
  nothing, so its cache stays small.
* **Each tick works out the steps for waiting heads.** That is one
  fetch and two diffs per head without an estimate, bounded by the
  queue's length.

## Testing strategy

agent-forge's tests cover:

* step costs from the verdict log: the median, too few runs, unknown
  steps;
* which heads are cheap, and which aren't when the steps can't be
  worked out;
* the lane taking only cheap heads, oldest first;
* an ordinary slot taking a cheap head only when no expensive one
  waits;
* `SLOW-LANE`;
* the configuration refused without `depends`.

## Implementation plan

1. `fast_lane`, off by default, in one release.
2. A project turns it on in its configuration, in a pull request its
   owner merges.

## Open questions

* **`max_seconds`.** 60 suggested: the text checks and dependency
  installs, but no compile.
