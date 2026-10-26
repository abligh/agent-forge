# 0035: Batches take stacks, run side by side, and size themselves

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-26

## Summary

With batches (designs/0032), one run of the batch's top covers every
merge in the batch. Three limits remain:

1. **A stack's layers can't share a batch.** A layer is held until the
   layer below has merged, then retargeted on a tick of its own
   (designs/0025, 0033). Its turn comes in a later batch, behind the
   pull requests that were queued meanwhile. Yet a layer is the pull
   request most certain to merge cleanly on top of its base: it already
   contains it.
2. **Batches run one at a time.** The next batch's prediction exists
   while the current one runs, but its top isn't run until the current
   batch has merged. The host's other slots sit idle meanwhile.

3. **The batch size is a guess.** A project picks `batch` by hand,
   with nothing to tell it whether a larger or smaller one would merge
   more per run.

This design makes three changes, each off unless set:

1. **A stack joins its base's batch.** When a batch takes a stack's
   bottom layer, the layers above it that are ready go into the same
   batch, straight after it. Fairness is restored in the next batch:
   any pull request a batch passed over goes first in the next one.
2. **The next batches run in spare slots.** The tops of the next
   batches, each predicted on the batch before, run while the current
   one runs. When the batches below a top all go green and merge, its
   verdict is already in.
3. **Batches report how they went, and can size themselves.** Each
   batch's outcome is recorded: the size tried, the size that merged,
   and the runs it took. A report estimates the pull requests' red
   rate from them and gives the best size, a power of two. Later, the
   Merger can set the size itself.

## Evidence

One project, the 24 hours to 05:30Z on 26 October, `merge.stacks`,
`test_ahead` (depth 4) and, from 02:28Z, `batch: 3`.

* **The queue drains at one batch per run.** A prediction run took a
  mean of 705 s (263 runs). The Merger starts the next batch's run
  only once the current batch has merged, so at most one batch merges
  per 12 minutes or so. At 05:11Z, with 34 pull requests queued, 2 of
  the host's 6 slots were busy: the batch's top and the default
  branch's tip.
* **Stacks are most of what's held.** 22 of the 28 held pull requests
  were stacked layers.
* **A layer waits for a batch of its own.** 24 layers were retargeted
  after the layer below merged. The 21 of those that have merged took
  a median of 44 minutes from the layer below merging (75th percentile
  97 minutes, the longest 169). The Merger's own part, retarget to
  merge, is a few minutes when the check is already green: 3 to 9
  minutes for the fastest four.
* **Most batches aren't red.** 237 of the 263 prediction runs were
  green (90%). A text conflict is skipped while predicting, so it never
  reaches a run. A batch therefore mostly turns red because one pull
  request is red on its own, or flaky, not because two of its members
  clash.

## The change

### 1. A stack joins its base's batch: `merge.test_ahead.stacks: true`

Needs `merge.stacks` and a `batch` above 1.

1. **Who joins.** While predicting, after pull request Q, the Merger
   takes each layer stacked on Q that would be eligible but for its
   base: an approval covers its head, nothing said since, no blocker
   or deferral (as designs/0033 judges a waiting layer). Then the
   layers above that one, the same way, up the stack. The first layer
   that isn't ready ends the climb.
2. **The prediction.** A layer's prediction is its head merged onto the
   commit before it: Q's prediction, or the layer below's. That is the
   tree GitHub's update gives once Q has merged and the layer is
   retargeted to the default branch, the same rule as any other
   prediction. A layer that conflicts is left out, and the climb ends
   there.
3. **Where it goes.** Straight after Q, inside the batch, before the
   next pull request in the queue. The batch keeps its size, so a
   layer takes the place of a queued pull request.
4. **Merging it.** Nothing new: Q merges, the next tick retargets the
   layer (which no longer forgets the batch, v0.4.115), the update
   gives the predicted tree, it is covered by the batch's green top,
   and it merges. The retarget costs one tick, not a run.
5. **Fairness, made up in the next batch.** A pull request a layer
   displaced is **owed**. `ahead.json` records it. The next batch
   starts with the owed pull requests in their queue order, before any
   layer. A layer may never displace a pull request that is owed, so
   nobody is passed over twice in a row.

### 2. The next batches run side by side: `merge.test_ahead.batches: K`

Default 1, as today. With K above 1, and `depth` + 1 at least
K × `batch`:

1. **More tops.** The Merger predicts K batches end to end: batch k
   starts from batch k − 1's top. `ahead.json` lists each batch with its
   trees and top.
2. **They run in spare slots.** `forge-pr-check` runs the current
   batch's top first, as today. The tops of the later batches run in
   slots no ordinary head is waiting for (`spare_slots_only`'s test),
   in order. A later top never delays an agent's own head.
3. **A green later batch.** It counts only once every batch below it
   has merged. Its members are then covered and merge as today, with
   no run of their own.
4. **A red earlier batch.** Every later batch's tree contains the red
   one. So its runs are thrown away, even if they're green. The red
   batch splits as designs/0032 says. While it splits, its members
   that haven't merged are **unresolved**: one of them is red, and
   which isn't known yet. The later batches are predicted again
   without them, from the queue behind them, so a spare slot isn't
   spent on a tree that is probably red. Once the split ends (the
   culprit handed back, or a half merged), the unresolved members go
   back to the front of the queue, in their old order, and the next
   new batch starts with them.
5. **A red later batch, while the earlier one is green.** It splits as
   designs/0032 says, when it becomes the current batch. The halves'
   runs can start early, in spare slots.
6. **Anything that changes a batch** (a push, a hand-back, a new
   approval order) re-predicts from that batch up, as designs/0032's
   rule 5 does for one batch. The batches below it are untouched.

### 3. Batches size themselves: an outcome record, a report, and `batch: "auto"`

1. **Each batch's outcome.** When a batch is done (merged, or split
   down to its culprit), `forge-merger` appends one line to
   `batches.jsonl` in its state directory: the size tried, the size
   that merged, the runs it took, the culprit if one was handed back,
   and whether the default branch's own check was red at the time.
2. **The red rate.** If each pull request is red on its own with
   probability q, a batch of N is green with probability (1 − q)^N.
   `forge-merger --batch-report` fits q to the outcomes of the last
   `hours` (default 24) by maximum likelihood. It leaves out batches run
   while the default branch was red, or while a flaky test was logged
   (`FLAKE`): then every pull request looks bad at once, and q would
   come out far too high.
3. **The best size.** From q, the report gives the expected merges per
   run for each size, and the best. For example:

   | q | 4 | 5 | 8 | 9 | 16 | 17 |
   | --- | --- | --- | --- | --- | --- | --- |
   | 1% | 3.67 | 4.41 | 6.67 | 7.17 | 11.20 | 11.35 |
   | 3% | 3.14 | 3.53 | 4.90 | 4.96 | 6.61 | 6.39 |
   | 5% | 2.71 | 2.90 | 3.79 | 3.69 | 4.43 | 4.19 |
   | 10% | 1.97 | 1.94 | 2.26 | 2.08 | 2.13 | 1.96 |

4. **Powers of two.** Sizes 5 to 8 all take up to four runs to find a
   bad pull request, so a size just above a power of two pays for an
   extra halving and gains little: at q = 10%, 5 merges fewer per run
   than 4. The report recommends only powers of two. A batch whose size
   isn't one first splits at the largest power of two below it
   (12 → 8 → 4 → 2 → 1, not 12 → 6 → 3 → 2 → 1), so every split
   after the first is a clean half.
5. **`batch: "auto"`, later.** With `"auto"` and `batch_max`, the
   Merger starts each new batch at the report's best power of two
   (at most `batch_max`, at least 2) for the last `hours`. It is
   built only after the report has shown its estimate is steady from
   one day to the next. Until then, a project reads the report and sets
   `batch` itself.

## What it doesn't change

* **What a merge requires.** Every head is up to date and has a green
  status on exactly that head when it merges. Every tip of the default
  branch after a whole batch has merged is a tree a run turned green.
* **The queue's order outside a batch.** Part 1 lets a stack's layers
  go ahead within one batch, and puts what they displaced first in the
  next. Part 2 changes nothing about order.
* **A host with no spare slot.** Part 2 then runs nothing extra, and
  the queue drains as today.

## Cost

* **Part 1:** a pull request is passed over by at most one batch,
  about one run, 12 minutes here. A stack of N ready layers merges on
  the run that merges its bottom, instead of up to N batches later.
* **Part 2:** runs thrown away when an earlier batch goes red. With 90%
  of runs green and K = 2, about one later run in ten is spent. Each
  is a slot that would otherwise have been idle.
* **Part 3:** one line per batch, and git-free arithmetic for the
  report.
* **Throughput.** With parts 1 and 2, batch 8 and K = 2, two runs in
  flight cover up to sixteen merges, against three today. At the
  observed red rates that is several times the arrivals of about 12 an
  hour.

## Configuration

| Key | Default | What it is |
| --- | --- | --- |
| `merge.test_ahead.stacks` | `false` | A stack's ready layers join their base's batch, straight after it; anything they displaced goes first in the next batch. Needs `merge.stacks` and `batch` above 1. |
| `merge.test_ahead.batches` | `1` | How many batches are predicted and run at once. Later tops run only in spare slots. `depth` + 1 must be at least `batches` × `batch`. |
| `merge.test_ahead.batch` | `1` | Gains `"auto"` (part 3, built last): the report's best power of two, at most `batch_max`. |
| `merge.test_ahead.batch_max` | none | With `batch: "auto"`, the largest size it may choose. `depth` + 1 must be at least `batches` × `batch_max`. |

## Order of work

1. `forge-merger`: layers in the prediction, `owed` in `ahead.json`,
   the fairness rule; tests. `docs/config.md`. A release.
2. `forge-merger`: `batches.jsonl`, `--batch-report`, and the split at
   the largest power of two; tests. A release.
3. `forge-merger` and `forge-pr-check`: K batches in `ahead.json`,
   later tops in spare slots, unresolved members left out, discarding
   on an earlier red; tests. `docs/config.md`. A release.
4. `batch: "auto"`, once a few days of reports agree.

A project then sets each key in its own configuration. Parts 1 and 3's
record and report are cheap and go first.
