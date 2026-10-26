# 0035: Batches take stacks, run side by side, and size themselves

* Status: approved (Alex Bligh, 2026-10-26, https://github.com/abligh/agent-forge/issues/108#issuecomment-6409275365)
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
3. **A red batch searches for its first red member, and batches size
   themselves.** A red batch is bisected for the first member that
   turns it red: everything before that member merges, and that member
   is handed back. The batch isn't refilled until then, so its other
   members aren't retested. Each batch's outcome is recorded. A report
   estimates the pull requests' red rate from the outcomes, simulates
   the queue, and gives the size that costs the fewest runs per pull
   request, retests included. Later, the Merger can set the size
   itself.

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

### 3. A red batch searches for its first red member; batches size themselves

1. **The search, not a restart.** Today (designs/0032) a red top halves
   the batch until a prefix is green, merges that prefix, and then
   starts a new full batch from the queue. The rest of the old batch,
   with the bad pull request still in it, goes into that new batch. It
   turns red again and is halved again, so the bad pull request's
   batchmates are tested over and over. With `search: true`, a red
   batch is instead searched for its **first red member**: bisect over
   its prefixes, each already predicted, until prefix k is green and
   prefix k + 1 is red. Then members 1 to k merge, covered by prefix k's
   run, and member k + 1 is handed back: it is red on top of the ones
   before it, and its hand-back says so. The members after it are
   unresolved, and they start the next new batch.
2. **Each batch's outcome.** When a batch is done, `forge-merger`
   appends one line to `batches.jsonl` in its state directory: the size
   tried, the number merged, the runs it took, the pull request handed
   back if any, and whether the default branch's own check was red at
   the time.
3. **The red rate.** If each pull request is red with probability q,
   independently, a batch of N is green with probability (1 − q)^N.
   `forge-merger --batch-report` fits q to the last `hours` (default 24)
   of outcomes by maximum likelihood. It leaves out batches run while
   the default branch was red, or while a flaky test was logged
   (`FLAKE`): then every pull request looks bad at once, and q would
   come out far too high.
4. **What the report measures: runs per pull request.** Runs are what
   the hosts spend, so the report's measure is runs per pull request
   leaving the queue (merged or handed back), retests included. It
   also gives how many runs each pull request was in, since a flaky
   test catches a pull request that is tested more often. Both come
   from a simulation of the queue under the project's own rules, not
   from a formula per batch: a formula per batch misses the retests of
   a red batch's remaining members. Simulated, for 200,000 pull
   requests:

   | q | rule | 1 | 2 | 4 | 5 | 8 | 16 |
   | --- | --- | --- | --- | --- | --- | --- | --- |
   | 2% | halve and restart | 1.00 | 0.54 | 0.35 | 0.33 | 0.28 | 0.29 |
   | 2% | search | 1.00 | 0.52 | 0.30 | 0.26 | 0.19 | 0.15 |
   | 5% | halve and restart | 1.00 | 0.61 | 0.49 | 0.51 | 0.51 | 0.61 |
   | 5% | search | 1.00 | 0.56 | 0.37 | 0.34 | 0.30 | 0.29 |
   | 10% | halve and restart | 1.00 | 0.72 | 0.72 | 0.82 | 0.87 | 1.11 |
   | 10% | search | 1.00 | 0.63 | 0.49 | 0.48 | 0.47 | 0.52 |

   Runs per pull request; lower is better. Under halve-and-restart,
   anything above 4 costs more at q = 10%, and 16 costs more than no
   batch at all. With the search, 8 is as good as or better than every
   smaller size up to q = 10%, and each pull request is in 2.5 runs at
   most. A pull request in a batch of 16 at q = 10% is in 4.6 runs.
5. **The best size.** The report gives the size with the fewest runs
   per pull request for the fitted q, under the rule in force, and how
   many runs each pull request was in at that size.
6. **`batch: "auto"`, later.** With `"auto"` and `batch_max`, the
   Merger starts each new batch at the report's best size (at least 2,
   at most `batch_max`) for the last `hours`. It is built only after the
   report has shown its estimate is steady from one day to the next.
   Until then, a project reads the report and sets `batch` itself.

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
* **Part 3:** the search uses predictions that already exist, so it
  costs no extra runs: it saves the retests. A report simulates the
  queue from one line per batch.
* **Throughput.** With parts 1 and 2, batch 8 and K = 2, two runs in
  flight cover up to sixteen merges, against three today. At the
  observed red rates that is several times the arrivals of about 12 an
  hour.

## Configuration

| Key | Default | What it is |
| --- | --- | --- |
| `merge.test_ahead.stacks` | `false` | A stack's ready layers join their base's batch, straight after it; anything they displaced goes first in the next batch. Needs `merge.stacks` and `batch` above 1. |
| `merge.test_ahead.batches` | `1` | How many batches are predicted and run at once. Later tops run only in spare slots. `depth` + 1 must be at least `batches` × `batch`. |
| `merge.test_ahead.search` | `false` | A red batch is bisected for its first red member; those before it merge, it is handed back, and the batch isn't refilled until then (part 3). |
| `merge.test_ahead.batch` | `1` | Gains `"auto"` (part 3, built last): the report's best size, at most `batch_max`. |
| `merge.test_ahead.batch_max` | none | With `batch: "auto"`, the largest size it may choose. `depth` + 1 must be at least `batches` × `batch_max`. |

## Order of work

1. `forge-merger`: layers in the prediction, `owed` in `ahead.json`,
   the fairness rule; tests. `docs/config.md`. A release.
2. `forge-merger`: `search`, `batches.jsonl` and `--batch-report`;
   tests. A release.
3. `forge-merger` and `forge-pr-check`: K batches in `ahead.json`,
   later tops in spare slots, unresolved members left out, discarding
   on an earlier red; tests. `docs/config.md`. A release.
4. `batch: "auto"`, once a few days of reports agree.

A project then sets each key in its own configuration. Parts 1 and 3
(less the auto mode) go first: they cost no extra runs.
