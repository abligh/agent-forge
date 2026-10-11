# 0010: Fresh dependency records

* Status: approved (Alex, 2026-10-11, at 2c73757,
  <https://github.com/abligh/agent-forge/issues/14#issuecomment-6111010104>)
* Implemented: v0.4.21, off until configured. A step is chosen from a
  record whose tree differs from the base's in nothing it read, an
  ancestor or not, since a `merge_default` merge is never one; the
  base's tree's records are tried first, then the newest 20. Runs of
  predictions write records too. A head waits only while the default
  branch's head waits for its traced run or is running it, and only
  when it was estimated against the tip, so without `merge_default`
  only a head based on the tip waits; the Merger's head and a cheap
  head never do. A step no record has makes the default branch's head
  traced, but no head wait. A step reused under design 0009 gives the
  new record nothing, and a default-branch head that needs no step
  traced gets no record of its own.
* Author: XMPPEngineer
* Date: 2026-10-11

## Summary

With `checks.depends` (design 0005), a pull request runs only the
steps that read what changed. "What changed" is measured from the
dependency record, which only the default branch's own traced runs
write. So it includes everything the default branch changed since its
last traced run. Once the default branch has moved, every step that
reads what it changed is uncertain and runs.

On one project one afternoon, the default branch's traced run waited
30 minutes behind pull requests (`depends_minutes`). Meanwhile four
merges changed code. A one-line change to a design document then ran
all nine steps, 7 minutes behind a queue it should have skipped, and
so did every other pull request checked before the next traced run.

Changing `depends_minutes` only moves the cost:
* **Waiting less** means a full traced run of the default branch
  nearly every time it moves.
* **Waiting more** means more full runs of pull requests.

Minutes are the wrong measure anyway. A record goes stale when the
default branch changes something a step reads, not when time passes.

This proposes three changes, under `checks.fresh`:

1. **Every green run writes a record**, not only the default branch's.
2. **The default branch is traced when its record is stale for a step
   a waiting head needs**, not after a fixed wait.
3. **A head that needs a step only because the record is stale waits
   for a fresh one**, then runs with its own selection, in the fast
   lane when that is cheap.

## Goals

* **A pull request runs the steps its own change needs.** It doesn't
  also run the steps the default branch's recent changes made
  uncertain.
* **No more full runs than now.** Freshness should come mostly from
  runs that happen anyway.
* **Selection stays exactly as sound as now.** A step is skipped only
  when its recorded reads are known to hold for the tree checked.

## Non-goals

* **Reading what a step reads without running it.** Static analysis of
  build graphs is the project's business, not this tool's.

## Design

### Records from every run

Each pull request's run is traced as the default branch's runs are
now. With `merge_default` (design 0007), the tree checked is the head
merged with the default branch's tip. That is usually exactly the tree
the default branch gets when the Merger merges it.

When the run is green, it writes a record for the tree it checked,
step by step:

* **A step it ran** contributes the reads its trace recorded.
* **A step it skipped** contributes the reads it had in the record the
  selection used. Those still hold, because a skipped step read
  nothing that changed between that record's tree and this one, and
  a step reads what its inputs lead it to.
* **A step that reads `CHECK_BASE`** (design 0009's `reads_base`,
  default true) contributes nothing. Its run is narrowed against the
  base, so its trace is only part of what a full run reads. The new
  record then lacks that step, and the step's reads come from the
  newest default-branch run that traced it in full.

A record is keyed by its tree, as now, so the default branch's tip
usually finds one for its own tree, written by the run that checked
the pull request it merged.

### Choosing the record

For a head, each step is chosen from the newest record that has that
step and whose tree is the head's merge base, or an ancestor of it.
Steps may then come from different records. The changes counted for a
step are those since its own record, so most steps count none at all.

### Tracing the default branch when it matters

`depends_minutes` is replaced. The default branch's newest head is
traced first, ahead of pull requests, when some waiting head has a
step that runs only because that step's record is stale. Stale means
the default branch changed one of the step's reads since the record.
A default-branch head whose changes since the last record touch no
step's reads is never traced. Its record is still exact.

`depends_minutes` remains as an upper bound: a stale record is
refreshed within that long even if no head is waiting on it.

### Waiting for the fresh record

When a head's selection has a step that runs only because its record
is stale, the head waits for the default branch's traced run, or for a
green run of another head on the same tip that writes the record,
instead of running everything now. Once the fresh record is written,
the head's steps are chosen again, and it goes to the fast lane if
they are cheap. A head waits at most `wait_minutes` (default 15) and
then runs as now.

## Config shape

```json
"checks": {"depends": true,
           "fresh": {"wait_minutes": 15}}
```

`fresh` is refused without `depends`. `depends_minutes` keeps its
meaning as the upper bound.

## Trade-offs and failure modes

* **Tracing costs a little on every run.** `LD_PRELOAD` tracing is
  cheap but not free, and each record is about a megabyte. Records are
  pruned to the newest few per step, as now.
* **A step whose reads depend on more than its inputs** would carry a
  wrong read set forward from a skip: the environment, the time, the
  network. That is already true of selection under design 0005. Such a
  step should be `always`.
* **Waiting trades latency for work.** A head that could have run
  everything at once waits up to `wait_minutes` for a record. With the
  queue as it is, that wait is usually shorter than the queue itself.

## Testing strategy

agent-forge's tests cover:

* records from green pull request runs: ran steps traced, skipped steps
  carried over, `reads_base` steps left out;
* a red run writing no record;
* each step chosen from its own newest usable record;
* the default branch traced only when a waiting head needs a step its
  changes made stale;
* a head waiting for its fresh record, choosing again, and going to the
  fast lane;
* `wait_minutes` running a head as now;
* the configuration refused without `depends`;
* nothing changed without `fresh`.

## Implementation plan

1. `checks.fresh`, off by default, in one release.
2. A project turns it on in its configuration, in a pull request its
   owner merges.
