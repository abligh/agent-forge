# 0047: Fixed trains of pull requests that touch different files

* Status: accepted
* Author: XMPPEngineer
* Date: 2026-11-04

## Summary

With batches (designs/0032, 0035, 0044), one run of a batch's top
covers every merge in the batch. A batch is whatever is next in the
queue, and it is cut again whenever the queue in front of it changes.
On one project most green runs were spent that way, and the queue
drained at about two pull requests per run.

This design replaces batches with **trains**, each off until a project
sets it:

1. **A train's carriages touch different files.** A carriage is a
   stack (designs/0025) or a single pull request. The first carriage
   is the first in the queue's order. Each later one is the next in
   that order that touches no file already in the train, up to
   `batch` pull requests.
2. **A cut train is fixed.** Nothing joins it and nothing in it moves.
   Overdue candidates, right of way and new approvals only order the
   trains not yet cut. A train is abandoned only when the train it is
   built on goes red.
3. **Trains run side by side.** Train k is built on the putative main
   that train k − 1 leaves. A carriage joins it only if it merges
   cleanly onto that main (`git merge-tree`).
4. **The Merger does the updating.** A carriage that won't merge
   cleanly onto the putative main waits for a later train, without a
   hand-back. It is handed back only when it reaches the front and
   conflicts with the real `main`.

The owner asked for this design on 2026-11-04, in XMPPEngineer's
session, as part of the work on one project's merge rate.

## Evidence

One project, 2026-11-04.

* **Batches are cut again and again.** After 11:00Z there were 122
  re-cuts. 67 followed a member leaving the queue (a push, a hand-back,
  a lost approval), and about 120 followed a pull request joining
  ahead of members: 45 at the front, 28 overdue and 49 others. A
  re-cut can have both causes. Only 2 were conflicts.
* **Runs merge about two pull requests each.** Green runs merged 2.1
  to 2.5 pull requests each, in three windows of three hours. The
  build check ran 1.1 to 1.5 batch-top runs per merge, and 3.0 to 3.9
  runs of every kind.
* **Merging is slow after approval.** Approval to merge took a median
  of 45 to 79 minutes, and a 90th percentile of 162 to 180.
* **Reds are rare, but repeat.** 16 of 101 batch-top runs were red.
  10 of those fell in two clusters, probably one broken pull request
  put into batch after batch. A member's own head is red 4.6% of the
  time (15 of 324).
* **Authors merge `main` themselves.** Hand-backs to a reviewer because
  an author's merge after the approval wasn't clean were 4 to 11 per
  window. That is about as many as the conflict hand-backs to authors
  (6 to 10).
* **Four files take most of the overlap.** Of 153 pull requests, 55
  touched one documentation file, 41 another, 39 one source file and
  37 another.

### A replay

XMPPEngineer replayed each three-hour window against this design. Each
replay started cold at the window's start and used the real Merger's
queue and order tick by tick (1435 ticks). Runs took 581 s, the median
batch-top run that day, or 706 s, the 75th percentile. Reds came from
members' own red heads near the run, or at random at 4.6% each. After
a red, the culprit stayed out and the trains behind were re-formed.
Conflicts came from pairwise `git merge-tree` of the final heads: 1093
of the 1932 pairs that share a file conflict. The replay ignored the
second-platform check instances.

| | Today | 1 train | 2 trains | 3 trains | 6 trains |
| --- | --- | --- | --- | --- | --- |
| Merges an hour | 9.0–12.7 | 9.9–12.7 | 10.8–14.7 | 10.9–15.7 | 10.9–16.3 |
| Pull requests per green run | 2.1–2.5 | 2.0–2.3 | 1.6–1.8 | 1.4–1.7 | 1.4–1.6 |
| Runs per merge (gating) | 1.1–1.5 | 0.5–0.6 | 0.7–0.8 | 0.8–0.9 | 0.8–1.0 |
| Approval to merge, median (min) | 45–79 | 20–29 | 15–26 | 14–26 | 14–26 |

Ranges are across the three windows and both ways of placing the
reds. In each window, "2 trains" and up give a strict and a lenient
figure. Strict means a later train's carriages also share no file
with the trains ahead. Lenient means any carriage merges cleanly onto
the trains ahead. The rule here, `git merge-tree` clean, lies between
them. Strict gave 0.5 to 1.5 more merges an hour than 1 train, and
lenient 2 to 3 more.

What it shows:

* **The gain is mostly latency, not throughput.** One train is about
  today's merge rate. Several trains add 1 to 4 merges an hour.
  Approval to merge falls from about an hour to about 20 minutes, and
  runs per merge halve, because a fixed train's run is never spent.
* **Trains are short.** Steady trains had 1.4 to 2.3 pull requests. A
  maximum of 8 never bound. What limits them is file overlap, mostly
  the four files above.

### What the replay holds fixed

The replay used the real arrivals: the pull requests approved in each
window, at the times they were. The owner's point, on reading it:

> I suspect your simulator has held the supply of PRs constant. I
> suspect that won't be the case because anecdotally (through looking
> around) agents are often "Waiting for X to merge", which they will
> do less of. They are also rebasing things and then "Waiting for Y to
> gate" - they will still have to wait for Y to gate (we're not
> improving things that run on the agent host) but if they get fewer
> handbacks, they will do it less often, so (a) there's less agent
> time spent on that (though that would be mitigated by subagents
> which are often used), and (b) more relevantly, there would be less
> load on the agent host. I admit the above is speculative rather than
> measured, but I can't see how it would make the situation worse.

So the replay's merge rate is a floor. A merge 40 minutes sooner
releases work stacked on it, or waiting for it, 40 minutes sooner.
Fewer hand-backs mean fewer pushes, gates and re-reviews. "Measuring
it" below says how to tell.

## The design

### 1. Cutting a train

The queue is ordered as today: urgent, red-default-branch fixes, right
of way, overdue, `age_first`, then approval order.

1. **Carriages.** A stack with `merge.test_ahead.stacks` is one
   carriage: its base and its ready layers, in order, with the union
   of their files. Anything else is a carriage of one pull request.
2. **The first carriage** is the first in the queue that can go. It
   must merge cleanly onto the train's base, which is `main`'s tip for
   the first train and the previous train's top for the others.
3. **Each later carriage** is the next in the queue that touches no
   file the train already touches and merges cleanly onto the train so
   far. A carriage skipped for overlap keeps its place in the queue
   and is first in line for the next train.
4. **A train is full** at `batch` pull requests. A stack is never
   split; one that doesn't fit waits for the next train. The first
   carriage is taken even when its stack is longer than `batch`.
5. **Who may not board.** As today, a pull request held, handed back
   under its approval and head, or a red suspect (designs/0044 rule 4)
   can't board. A pull request whose own head has a red verdict can't
   board either. The red-suspect rule of designs/0044, with its green
   rerun amendment, applies to a train's culprit too. So a broken pull
   request is put in one train, not ten.

Within a train, file-disjointness is the rule rather than a clean
merge. Carriages that share no file give the same tree in any order
and whichever of them merge. A dropped carriage then changes nothing
in the others' trees. The cost is shorter trains. The dry run (below)
logs what a clean-merge rule would have cut too, so the two can be
compared before anyone changes `within`.

### 2. A cut train is fixed

A train is cut when it is written to `ahead.json`. From then:

* nothing joins it, and its carriages keep their order;
* overdue candidates, right of way's holder, newly approved pull
  requests and returning stack layers go into the first train not yet
  cut;
* right of way holds none of its members.

This extends designs/0044 rule 3, "fixed once its top's run has
started", to "fixed once cut". With trains in flight in spare slots,
a train's run usually starts at once anyway.

Two things still go in front of a cut train, as today. One is a
red-default-branch fix (designs/0011): every train built on a red
`main` is red too. The other is a pull request labelled
`labels.urgent`, which a person sets. Each cuts the trains behind the
one being merged, and the log says why.

### 3. Trains in flight

`merge.test_ahead.batches` (K) trains are cut end to end. Train k is
built on train k − 1's top. `forge-pr-check` runs the first train's
top first, and the others in spare slots, as for batches today
(designs/0035 part 2). A later train's green verdict counts once every
train below it has merged.

### 4. How a train merges

The train's branch already exists: the Merger's predictions under
`refs/agent-forge-merger/ahead/<k>` are each carriage merged in order
onto the one before, from `main`'s tip. The build check tests the
train's top tree. On green, the Merger merges the members one at a
time, as covered batches merge today. Each member gets `update-branch`
and then a squash merge, both pinned to the head that was judged.
After member k − 1's squash lands, `main`'s tree is the tree predicted
for it, so the update gives member k exactly the tree predicted. That
is what "covered by" checks before posting green.

Pushing the train's branch to `main` was considered and rejected:

* **Each pull request keeps its own squash commit** and shows as
  merged. GitHub closes its issues (`Fixes #n`) only when the pull
  request itself merges.
* **The approval still covers what merges.** GitHub's update is a
  clean merge of `main`, which the approval check already accepts. A
  rebase made by the Merger would be new commits that no approval
  covers.
* **Branch protection is unchanged.** No App needs to bypass it.

This needs nothing new from GitHub, and a train of n costs about 2n
ticks. Ticks follow each other at once while they merge
(`TICKS_PER_RUN`).

A force merge by a person bypasses all of this. It changes `main`
under every train, which are then cut again from the new tip.

### 5. A push to a member mid-train

GitHub merges only a pull request's current head, so a member pushed
to after its train was cut can't be merged at the head the run
tested.

* **Its update still gives the predicted tree.** This is the case when
  the author merged `main` cleanly. Nothing changes, as today.
* **Otherwise the member leaves the train, and only it.** The new
  commits aren't covered by its approval anyway, so it couldn't merge
  until reviewed again. The train is not abandoned. It keeps its other
  members and their order. Since their files are disjoint from the
  leaver's, they still merge cleanly without it and their trees are
  unchanged; only the top's tree changes. That tree needs its own run,
  which goes first. The trains behind are rebuilt on the new top and
  keep their members where those still merge cleanly. The log says
  `TRAIN-LEFT #n: pushed <sha> mid-train`.
* **In a stack carriage, the layers above the leaver leave with it.**
  They are built on its old head, so they can't merge without it.
  The layers below it stay: they don't depend on it. The layers that
  left go into the first train not yet cut, as one carriage again
  once the pushed layer is approved at its new head.

Merging the remaining members on the old run without a new one would
save that run, but would put an untested tree on `main`. The members
share no file with the leaver, which makes it likely to be green, not
certain. It is under "Not yet".

To make pushes rarer, the build check's pending description on a
member says `in train <top>: a push now takes it out`. A project's
guidance for authors should say that the Merger updates approved pull
requests itself, so an author need not merge `main` into one.

### 6. No hand-back for a conflict with the putative main

A carriage that won't merge cleanly onto a train's base, but does onto
the real `main`, waits for a later train. It isn't handed back for
that: the conflict is only predicted, and goes away if the train ahead
crashes or loses the member it conflicts with. Today's
`merge.hold_conflicting` and the predictions already leave such a pull
request out; trains make that the rule.

It is handed back as soon as it conflicts with the real `main`, on the
tick the conflicting pull request merges, wherever it is in the queue.
That is `merge.early_conflicts` (designs/0012), unchanged, which tries
`merge.resolve.union` (designs/0046) first and hands back only what
that can't resolve. A conflict with the real `main` doesn't get better
by waiting, so waiting would only delay the author's fix. So the
longest a pull request goes unhanded-back is one train: the time from
the prediction to the merge that makes it real. Without
`early_conflicts`, it is handed back when it reaches the front, as
today; trains mode recommends turning `early_conflicts` on.

### 7. A red train

The red train's search for its first red member runs as today
(designs/0035 part 3, designs/0044 rule 4). The culprit is handed back
and kept out until it has a new head or a green rerun. The trains
behind it are abandoned and cut again without the train's unresolved
members. Members found green merge as their prefix goes green, as
today.

## Configuration

All under `merge.test_ahead`, which they need. Off by default.

| Key | Default | What it is |
| --- | --- | --- |
| `trains` | none: off | `"live"` cuts trains instead of batches. `"dry-run"` keeps batches and also logs, each tick, the trains it would have cut (`TRAIN-DRY`, and `trains` in the record). |
| `trains_within` | `"files"` | What carriages in one train must be: `"files"`, sharing no file, or `"clean"`, merging cleanly in order. |
| `batch` | as today | A train's maximum length in pull requests. |
| `batches` | as today | How many trains are in flight. |
| `stacks` | as today | With `merge.stacks`, a stack is one carriage. |

`trains` needs `fixed_order` and refuses it `false`. `depth` + 1 must
still be at least `batch` × `batches`.

**Moving from batches.** A project with `batch` and `batches` set
first runs `trains: "dry-run"` for a day, then sets `"live"` with the
same `batch` and `batches`. To go back, it removes `trains`; the next
tick predicts batches from wherever the queue stands.

## Measuring it

`forge-flow` and the Merger's records, over a day before and a day
after:

* **Re-cuts.** On one day, 122 after 11:00Z. With trains, they
  should be about 0, apart from reds, pushes and force merges, each
  logged with its cause.
* **Pull requests per green run, and runs per merge.** The replay
  expects fewer pull requests per run, about 1.5 to 2, and about half
  the runs per merge.
* **Approval to merge, median and 90th percentile.** The replay
  expects about 20 minutes median.
* **Hand-backs per merge, by cause.** Conflict hand-backs should not
  rise. Reviewer hand-backs for an author's own merge of `main` should
  fall as authors stop doing it.
* **The owner's hypothesis.** Approvals an hour (supply), pushes per
  merged pull request, and the agent hosts' load. The replay can't
  show these.
* **Merges an hour,** against the project's bar.

The dry run gives the first two from the same day's queue. It logs
each train it would cut, and which of today's re-cuts it would not
have made.

## Order of work

1. `forge-merger`:
   * cutting trains, the boarding rules, fixed-at-cut, the push rule
     and the red rule;
   * `TRAIN-DRY`, and `trains` in the record;
   * the pending description;
   * tests for each rule, including a stack as a carriage, a stack
     longer than `batch`, an overlap skipped and taken next train, a
     push mid-train, a red train behind a green one, an urgent cut and
     a force merge.
2. `forge-flow`: re-cuts by cause, pull requests per green train, and
   approvals an hour.
3. `agent_forge/config.py` and `docs/config.md`; one release.
4. A project pins it, runs the dry run for a day, then turns it on.

`forge-pr-check` needs nothing new: a train is a batch to it, with a
top to run and members to cover.

## Not yet

* **Merging what's left of a train after a push, without a new run.**
  The remaining members share no file with the leaver, so their tree
  is likely, not certain, to be green. Measure how often a re-run of
  such a train goes red first.
* **Searching a red train carriage by carriage.** Disjoint carriages
  could each be run alone on the train's base in spare slots, rather
  than halving. Only worth it if red trains stay common after the
  boarding rule.
* **The files that make trains short.** Splitting the four files most
  pull requests touch is the project's work, not agent-forge's. It
  would lengthen trains more than any rule here.
* **Second-platform check instances.** The replay ignored them. On
  that day one gated the front of the queue for 93 to 142 minutes of
  each 180. Trains don't make them faster.
* **Telling authors when their train will land,** so they can stop
  waiting on it sooner.
* **Sizing `batch` and `batches` from the records** (designs/0035
  part 3), now that runs are rarely spent.

## What it costs

* **On the build host:** fewer runs per merge. A train that is cut
  again after a push or a red costs a run, as a re-cut batch does
  today.
* **For the Merger:** a file list per carriage, from `git diff
  --name-only`, and one `merge-tree` per
  carriage per train. Writes to GitHub are those of batches today.
* **For an overdue pull request:** it waits for the trains already cut
  (at most `batches` runs) before it goes first, instead of cutting
  them. designs/0044 rule 3 already makes that trade once a run has
  started.
* **Risk:** carriages that share no file can still break each other,
  for example a renamed function and a new caller of it. The train's
  run tests them together, as a batch's does today.
