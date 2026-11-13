# 0053: A resolve slot is for a push ready to check

* Status: approved (Alex, 2026-11-13), with one change: a resolution
  may merge a train's head as well as the default branch (§1, §2)
* Author: XMPPEngineer
* Date: 2026-11-13

## Summary

`merge.resolve_slots` (designs/0012, amendment of 2026-11-12) caps how
many pull requests are back with their authors for a conflict with the
default branch at once. A slot is taken when the Merger hands the
conflict back, and it is held while the author works. Meanwhile the
others that conflict are held untold: their authors don't know there
is anything to do.

Most of a slot's time is therefore spent waiting for an author, and
none of it is spent where the slot costs anything, which is the build
check host. This design moves the slot to the scarce thing:

* **Every conflict is told at once**, as it was before slots.
* **The author's push is the request for a slot.** A resolution waits
  in a queue until a slot is free, and the slot then covers its check,
  its re-review and its boarding.

Resolving is cheap: a merge of the default branch, or of a head
already in a train, into the branch, without the full test. The test is the expensive part, and the slot is
what pays for it.

## Evidence

One project, `resolve_slots: 2`, from 2026-11-12 14:02Z to 2026-11-13
10:51Z (1391 Merger ticks):

* Both slots were out, with others waiting untold, on 605 ticks (43%).
* The 23 slots held: median 61 minutes, the longest over 10 hours.
  Seven were held over 100 minutes.
* The 24 pull requests that waited for a slot: median 84 minutes,
  eight over 3 hours, the longest over 10 hours. All were approved,
  and their authors were not told.
* One slot was held 2 hours, and counting, by an author who had
  resolved twice. The first resolution passed a local full test run
  after an hour, by which time the default branch had moved and
  conflicted again in the same import list. The second was into
  another hour-long local run.

The slot was meant to stop several authors resolving against a branch
that would move again before their turn. It does that, but by keeping
the queue idle while one author tests.

## Design

### 1. Every conflict is told at once

With `merge.resolve_slot_on: "push"`, a conflict with the default
branch is handed back as soon as it is found, at the front or behind
it (`early_conflicts`), exactly as with no `resolve_slots`. Nothing is
held untold. The hand-back says that a slot is needed for the
resolution's check, and that a merge, not a full local test run, is
all the author needs to do before pushing.

**What to merge.** The hand-back lists the viable heads, and so does
`next.json` as `resolve_bases`: the default branch's tip, and the head
of each train now being checked or about to merge (a train's own pull
request, top first). Trains merge far more often than not, so a
resolution on a train's head builds on what is about to be the
default branch and is less likely to conflict again. Each listed head
is one the author can fetch: a train's pull request branch.

### 2. A resolution's push queues for a slot

A **resolution** is a new head of a pull request that was handed back
for a conflict with the default branch and that now merges cleanly
with it, or that has one of the listed train heads as an ancestor and
merges cleanly with that train's head. One on a train is stacked on
it: it boards only after that train merges, and if the train fails
and is cut, it is handed back again at once with the new list. The
Merger keeps the resolutions in its record:

* `resolve_slots` of them hold a slot, given in `resolve_slot_order`
  (urgent first, then right of way, then the soonest to board).
* The rest wait in `resolve_queue`, in the same order.

`forge-pr-check` reads both from `next.json`:

* A resolution holding a slot runs first, after the Merger's own pull
  request and its predictions, in `approved_first`'s tier, even though
  its approval is now stale.
* A resolution waiting for a slot is not run at all. It shows
  `HELD #n: a resolution, waiting for a resolve slot (2 out: #a, #b)`.
  When it is still not run, the build check host has spent nothing on
  it.

Re-review runs in parallel with the check, as now.

### 3. When a slot frees

A slot frees when its pull request boards a train or merges, closes,
or conflicts again. It also frees after `resolve_slot_minutes` with no
progress, where progress is a green check or a new approval.

A resolution that conflicts again, in the queue or in a slot, is told
again at once. Re-merging the default branch is cheap, and the new
push queues again, keeping the place in `resolve_slot_order` that the
pull request had.

### 4. What stays

* `resolve_slots` keeps its meaning as a count. With `resolve_slot_on`
  unset (or `"hand-back"`), slots work as they do today, with
  `resolve_slot_minutes` (agent-forge #362).
* Stack layers above a base that is out are held as stacked, as now.
* Right of way and `hold_conflicting` are unchanged.

## Configuration

| Key | Default | Meaning |
|---|---|---|
| `merge.resolve_slot_on` | `"hand-back"` | `"push"`: tell every conflict at once, and give a slot to a resolution's push (this design). |

`resolve_slots` must be set. A project switches with one config change.

## Trade-offs and failure modes

* **A resolution on a train carries the train's other pull requests**
  until the train merges. Its diff on GitHub shows them, and if the
  train fails, the author resolves again. Trains merge far more often
  than they fail, so this is a good trade.
* **More resolving.** Every conflict is told, so more authors resolve,
  and some resolutions are undone by a later merge before they get a
  slot. That costs agent time, which is cheap, rather than build check
  time, which is not.
* **A resolution may wait for its check.** That is the point: the
  check host's time goes to the resolutions next in line.
* **Authors must not gate locally for an hour.** That is a project's
  rule, not agent-forge's, but the hand-back text says it.
* **Two writers of priority.** `forge-pr-check` already reads the
  Merger's `next.json` (designs/0052); this adds two lists to it. When
  `next.json` is stale, resolutions run in the ordinary order, as
  today.

## Related, not in this design

* designs/0012's amendment of 2026-11-12, the slots this changes.
* designs/0052, the build check's order, which this extends.

## Testing

* The Merger tells every conflict at once with `resolve_slot_on:
  "push"`, and puts resolutions in `resolving` or `resolve_queue` in
  `resolve_slot_order`.
* A slot frees on boarding, merging, closing, a fresh conflict, and
  `resolve_slot_minutes` with no progress.
* `forge-pr-check` runs a slotted resolution in the approved tier, and
  holds a queued one untouched.
* A stale `next.json` falls back to today's order.

## Implementation plan

1. forge-merger: tell at once with the viable heads (`resolve_bases`),
   track resolutions, a resolution on a train boarding after it,
   `resolve_queue`, slot freeing.
2. forge-pr-check: read the slotted and queued resolutions from
   `next.json`, and run or hold them.
3. Docs, release, and a project's config change.
