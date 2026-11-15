# 0056: Wasting less when a member leaves a train

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-14
* Issue: #393 (research: FleetResearcher)

## Summary

Once the build check stops being the limit, departures from trains
set the merge rate. A pull request that leaves a started train (a new
push, a lost approval, a conflict, its own red) changes the tree of
every prediction at and after its place. Those runs are then wasted.
#393 asks for four things. This design takes each in turn:

1. **Keep what was tested ahead of the leaver.** This already exists
   (`left_top_covers`, #296), but `stop_spent` defeated it. That is now
   fixed (#408). Nothing more is proposed.
2. **Re-form the tail in its place.** With `trains: "live"`, this also
   already exists. The design adds a simulation test that holds it to
   that.
3. **A resolution that only resolves boards at once.** An author's
   resolution of a conflict with the default branch may board a train
   and be tested while the reviewer looks at just the conflicted hunks.
   It still merges only on a new approval. New key
   `merge.resolve.board_unreviewed`, off by default.
4. **The train's length follows what survives.** Like Zuul's window:
   `batch` grows by one each time a train merges whole, and halves when
   a started train loses a member, between a floor and the configured
   `batch`. New key `merge.test_ahead.window`, off by default.
5. **A train whose run hasn't started takes new members.** Added
   after step 1 counted lone merges: half of them went alone because
   a train of one held a train's place. New key
   `merge.test_ahead.grow_unstarted`, off by default.

No new rule about queue order is proposed. Every new key is off
unless set.

## Why

On one project (about 200 pull requests a day, `trains: "live"`,
`batch` 16, `train_merge`):

* trains merged a mean of 2.5 to 2.9 pull requests each;
* in two weeks, 545 pull requests were handed back at least once,
  about a fifth of those merged, and 378 of them twice or more;
* hand-backs split about evenly between "conflicts with the default
  branch" and "the approval no longer covers the head". The second is
  mostly an author's resolution of the first, waiting for review again.
* the Merger logged 609 departures from started trains since 10-10,
  and not one `TRAIN-UNDER-LEFT` merge: item 1's rule never fired.

In replays of a doubled day with a quick head check (designs/0055), so
the slots were only half busy, pull requests left started trains 64
to 95 times. 119 to 196 of 451 pull requests were still unmerged at
the end of the day.

### Today's figures on the library's days

Replayed at main (v0.4.206) with `test_ahead` `{batch: 16, batches:
2, trains: "live", train_merge: true, stop_spent: true, later_first:
1}`, on the library's made-up days (#414, `make replay-compare`):

| | ordinary | heavy | bad |
| --- | --- | --- | --- |
| Pull requests | 120 | 240 | 120 |
| Merged | 111 | 150 | 94 |
| Approval to merge, median | 44 min | 92.5 min | 75 min |
| Approval to merge, p90 | 129 min | 341 min | 251 min |
| Hand-backs | 76 | 92 | 90 |
| Build runs a merge | 2.31 | 1.99 | 2.98 |
| Slots busy | 77% | 91% | 83% |
| Rule breaks | 0 | 0 | 0 |

The replay's report doesn't yet count what this design is about.
`left_trains` counts only members that left and never merged at their
tree, and `recuts` only re-cuts of started trains. Both are 0 on all
three days, even though the bad day has 40 pushes after approval. So
step 1 adds departures from started trains (by place) and runs spent
on a lone pull request to the report, and every later step is judged
on those.

Each step below is replayed on the same days, before and after, in
its own pull request (CLAUDE.md's rule, #395).

## The design

### 1. Keep what was tested ahead of the leaver: done

When member k of a started train leaves, the Merger keeps the old top
in ahead.json as `left_top`. Members 1..k-1 then merge on its green
when it is green on a tree they are all in
(`merge.test_ahead.left_top_covers`, #296, on by default).
`forge-pr-check`'s `stop_spent` stopped that top as spent, because the
re-cut no longer predicted its tree, so the rule never fired. #408
keeps it running. The simulation test `LeftTop` shows #1..#3 merging on
the old top's green at 08:08, where without the fix they waited for a
new run, until 08:13.

A spare run at a long train's midpoint, which #393 suggests, is not
proposed. With `batches: 2` the second train's top is already
running beside the first. A midpoint run costs a whole slot for each
train, and it pays only when the leaver is in the train's second half.
Measure departures by place (below) before spending a slot on it.

### 2. Re-form the tail in its place: exists, now tested

With `trains: "live"`, `cut_trains` keeps the trains the last tick cut
in their order while what is left of each still merges cleanly. A
member that leaves goes alone, with the layers of its stack above it
(`TRAIN-LEFT`). The others keep their places (rule 1). New pull
requests are cut into new trains only from what is left (rule 3), so
none goes in front of the tail. `fixed_order` ends its protection from
the leaver's batch on, but that only orders the queue, not trains
already cut.

What is added is a simulation test, `TailKeepsItsPlace`. A train of
five starts, #2 is pushed mid-train, and a newer approved pull
request arrives that same minute. The train continues as #1, #3, #4,
#5, and the newcomer joins a later train or the end of this one,
never in front of #3.

### 3. A resolution that only resolves boards at once

Today an approval covers a head when every commit since it is a clean
merge of the default branch, or a `resolve.union` resolution
(`covers_head`). An author's resolution of a real conflict is not
covered. It waits for a reviewer before it may even board, and it
then waits for a train and a full run. #393 proposes that such an
approval should keep covering the head when, outside the conflicted
hunks, the merge's tree equals the automatic merge.

**That is not safe as proposed.** The conflicted hunks are exactly
the lines nobody reviewed: the author wrote them when resolving. With
the approval kept, they would merge on the check alone. That breaks
the rule that the Merger merges what reviewers approved and nothing
else (CLAUDE.md). A check catches a broken resolution, not a wrong
one, such as a resolution that keeps the wrong side of a changed
condition.

**The narrowest safe version takes away the wait without removing the
review.** The review and the full run then happen side by side, not
one after the other:

* With `merge.resolve.board_unreviewed: true`, a head whose only
  commits since its approval are merges of the default branch counts
  as **provisionally covered** when, outside the hunks git reports as
  conflicted for each merge (`git merge-tree --write-tree
  --name-only` and the conflict markers of a `zdiff3` merge), its
  tree equals the automatic merge's.
* A provisionally covered pull request may board a train and be
  predicted and tested as usual.
* It does not merge until a reviewer approves its head. A train whose
  top is green but holds one waits for that approval. Pull requests
  ahead of it merge on a prediction that covers them (`left_top`,
  today's prefix rules).
* The Merger asks the reviewer once per head (`RESOLUTION-REVIEW
  #n`), naming the conflicted hunks only, with their lines. Reviewing
  the hunks is enough, since everything else is the approved head
  plus the default branch.
* A provisionally covered head that changes again is judged afresh.
  One that fails the test is handed back as today.

**What it saves:** the time from the resolution to the approval,
which today passes before the train and the full run can even start.
**What it costs:** a train holding an unapproved resolution can wait
on its reviewer. So such a pull request boards only as the **last**
member of its train, and the members ahead of it merge on a prefix's
green, which the check runs anyway.

The **document-only** variant from #393 (keep the approval outright
when every conflicted hunk is in `docs.paths`) is left for Alex to
decide on. It still merges unreviewed text, but text that can't break
the product. If he wants it, it is a second key,
`merge.resolve.keep_approval_paths`, a list of globs, off by default.

### 4. The train's length follows what survives

`merge.test_ahead.window` is `{"floor": n, "grow": 1, "shrink": 0.5}`
(default none: `batch` as configured). With it, the Merger keeps a
current length `w` in ahead.json, starting at `batch`:

* a train that merges whole: `w = min(batch, w + grow)`;
* a started train that loses a member (`TRAIN-LEFT`), or whose top is
  red: `w = max(floor, ceil(w * shrink))`;
* new trains are cut at `w`; trains already cut keep their length.

This is Zuul's gate window: it grows by one on success and halves on
failure (Zuul's documentation, "Project Gating", `window` and
`window-decrease-factor`). When departures are frequent, a shorter
train has fewer members to lose, and a departure invalidates fewer
predictions. When they are rare, trains grow back to `batch`. The
record carries `window` each tick, and `forge-flow` shows it beside
the trains.

### 5. A train whose run hasn't started takes new members

*Added 2026-11-15, from step 1's figures (#417).*

On the library's ordinary day, with the settings above, 52 of 111
merges went alone, as trains of one. At those merges the Merger
usually had two to four other pull requests eligible. Its record says
why they stayed out: `every train is full`, 304 times in the day, and
`conflicts onto train 2` 120 times. Departures from started trains
happened 3 times.

The cause is rule 3 of `cut_trains`. With `trains: "live"`, the trains
the last tick cut are kept as cut (rule 1), and new pull requests are
cut only into **new** trains, while there are fewer than `batches`.
So a train of one, cut when it was the only candidate, keeps one of
the two trains' places until it merges, even while its top's run
hasn't started. Pull requests approved a minute later wait for it.

New key `merge.test_ahead.grow_unstarted` (default false). With it:

* the **last** kept train whose top's run the build check hasn't
  started (`started(sha, tree)`, as rule 2 already asks) takes new
  members after its last one, by rule 3's usual test (merges cleanly,
  touches no file the train touches, within `batch`);
* trains whose runs have started never change: no run is thrown away;
* only the last unstarted train grows, so no newcomer goes in front of
  a member already cut (rule 1 holds);
* the grown train's new top is a new prediction, so the check runs it
  in place of the old top, which hadn't started.

What it costs: a top that hadn't started is predicted again, which is
cheap. A top that the check was about to start may wait one tick
longer for its new prediction.

## Measuring

`forge-flow` and the replay report already give departures from
started trains (`left_trains`) and full runs per merge. Two more are
added:

* **departures by place**: whether the leaver was the first, a middle
  or the last member. That is the figure that would justify a
  midpoint run (item 1);
* **runs spent on a lone pull request** at the head of the queue, not
  in a train. #393's target is a fall in this figure.

## Steps

Each step is its own pull request, inert until its key is set, and
carries `make replay-compare`'s table.

1. The simulation test for item 2 (`TailKeepsItsPlace`, in this pull
   request), and departures by place plus lone runs in the replay
   report and `forge-flow`. Until those are counted, the library's days
   can't show what items 3 and 4 change.
2. `merge.test_ahead.grow_unstarted` (item 5), judged on the lone
   merges first.
3. `merge.test_ahead.window`.
4. `merge.resolve.board_unreviewed`, with the `RESOLUTION-REVIEW` ask.
5. Only if Alex asks: `merge.resolve.keep_approval_paths`.

## Acceptance

* With the new keys absent, behaviour and tests are unchanged.
* Simulation tests:
  * the members ahead of a leaver merge on the old top's green (exists:
    `LeftTop`);
  * the tail keeps its place after a departure;
  * the window halves on a departure and grows on a whole train's
    merge;
  * a train of one whose run hasn't started takes a newcomer; one
    whose run has started doesn't;
  * a resolution that meets the rule boards and merges only after its
    new approval; one that changes a line outside the conflicted hunks
    doesn't board.
* No pull request merges without a green full run covering its tree,
  and none on a resolution nobody approved, in any test.

## Not in this design

* New queue-order rules (#393: "Don't add another queue-order rule").
* A midpoint run for long trains, until departures by place justify
  it.
