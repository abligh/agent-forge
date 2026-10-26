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
3. **Batches size themselves.** Each batch's outcome is recorded. A
   report estimates the pull requests' red rate from the outcomes,
   simulates the queue, and gives each size's runs per pull request,
   retests included, and how long a pull request waits. Later, the
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

### 3. A red batch searches for its first red member; batches size themselves

1. **The search already exists.** designs/0032's split is already a
   search for a red batch's first red member. A red verdict is kept by
   tree, and a new batch ends at a tree already red. So once a red
   batch's green half has merged, the next batch is the rest of the old
   batch up to its red top, halved again, rather than a full batch
   refilled from the queue. A batch of 8 with only its 8th member red
   tests 8, then 1–4 (green, merged), then 5–6 (green, merged), then
   7 (green, merged), and the 8th is left to its own run. The search
   starts again only when the predictions change under it: a push, a
   hand-back, a new approval order or a retarget gives the red tree a
   new one, which has no verdict. Nothing is built for this part.
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
4. **What the report measures.** For each size, from a simulation of
   the queue under the project's own rules (a formula per batch misses
   the retests of a red batch's remaining members):
   * **runs per pull request** leaving the queue (merged or handed
     back), retests included: what the hosts spend;
   * **the wait**, in runs, from a pull request's first run to its
     merge or hand-back, mean and 90th percentile: what its author
     sees;
   * **how many runs each pull request was in**, since a flaky test
     catches a pull request that is tested more often.

   Simulated for 100,000 pull requests:

   | q | | 4 | 5 | 8 | 16 | 32 |
   | --- | --- | --- | --- | --- | --- | --- |
   | 2% | runs per pull request | 0.30 | 0.26 | 0.20 | 0.15 | 0.14 |
   | | wait, mean (p90) | 1.2 (1) | 1.3 (1) | 1.6 (4) | 2.5 (6) | 4.6 (12) |
   | 5% | runs per pull request | 0.37 | 0.34 | 0.30 | 0.29 | 0.31 |
   | | wait, mean (p90) | 1.5 (3) | 1.7 (4) | 2.4 (5) | 4.6 (10) | 10.0 (18) |
   | 10% | runs per pull request | 0.49 | 0.48 | 0.47 | 0.52 | 0.60 |
   | | wait, mean (p90) | 2.0 (4) | 2.4 (4) | 3.8 (8) | 8.3 (15) | 19.1 (30) |

   8 costs fewer runs than 5 at every rate up to 10%. Beyond 8 the
   gain depends on the red rate and is small, while the wait doubles
   with each doubling of the size.
5. **The best size.** The report gives the size with the fewest runs
   per pull request for the fitted q whose 90th-percentile wait is
   within `wait_runs` (default 6), and that size's figures.
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
* **Part 3:** one line per batch, and a simulation for the report.
* **Throughput.** With parts 1 and 2, batch 8 and K = 2, two runs in
  flight cover up to sixteen merges, against three today. At the
  observed red rates that is several times the arrivals of about 12 an
  hour.

## Configuration

| Key | Default | What it is |
| --- | --- | --- |
| `merge.test_ahead.stacks` | `false` | A stack's ready layers join their base's batch, straight after it; anything they displaced goes first in the next batch. Needs `merge.stacks` and `batch` above 1. |
| `merge.test_ahead.batches` | `1` | How many batches are predicted and run at once. Later tops run only in spare slots. `depth` + 1 must be at least `batches` × `batch`. |
| `merge.test_ahead.batch` | `1` | Gains `"auto"` (part 3, built last): the report's best size, at most `batch_max`. |
| `merge.test_ahead.batch_max` | none | With `batch: "auto"`, the largest size it may choose. `depth` + 1 must be at least `batches` × `batch_max`. |
| `merge.test_ahead.wait_runs` | `6` | The report's and `"auto"`'s limit on a pull request's 90th-percentile wait, in runs. |

## Order of work

1. `forge-merger`: layers in the prediction, `owed` in `ahead.json`,
   the fairness rule; tests. `docs/config.md`. A release.
2. `forge-merger`: `batches.jsonl` and `--batch-report`; tests. A
   release.
3. `forge-merger` and `forge-pr-check`: K batches in `ahead.json`,
   later tops in spare slots, unresolved members left out, discarding
   on an earlier red; tests. `docs/config.md`. A release.
4. `batch: "auto"`, once a few days of reports agree.

A project then sets each key in its own configuration. Parts 1 and 3
(less the auto mode) go first: they cost no extra runs.

## Amendment (2026-10-26): batches grow at the end, `Blocked-by:` queues, part 2 first

* Status: approved (Alex Bligh, 2026-10-26, https://github.com/abligh/agent-forge/issues/118#issuecomment-6411078124)

**Why.** A pull request that becomes ready while a batch runs waits for
that batch to merge before anything tests it, even when slots are free.
On one project at 07:55Z on 26 October, the Merger predicted only the
running batch (8 pull requests), and 3 of the host's 6 slots were
busy. A pull request whose `Blocked-by:` names another that is approved
and queued is held until that one merges, and is first tested only
then. Neither wait is needed. The Merger knows the exact tree each
member will put on the default branch before any run reports, so it can
test what comes next on top of it at once.

### 1. A running batch is fixed; the next one grows at its end

Part 2 as approved predicts K batches end to end and runs the later
tops in spare slots. This amendment says how a batch is filled:

1. **A batch whose top's run has started is fixed.** Nothing joins it,
   and nothing in it moves (designs/0025's third amendment already
   keeps a returning stack layer out of it).
2. **The first batch whose run hasn't started grows at its end.** A
   pull request that becomes ready goes there, in its queue order,
   until that batch reaches `batch` members. Then a new batch starts
   behind it.
3. **A later batch starts its run as soon as a slot is spare**, with
   the members it has then, one or more. It doesn't wait to fill up:
   a run started now with 3 members merges them sooner than one
   started later with 8. From then on, rule 1 applies to it.
4. **`batches` becomes a cap.** At most K batches are in flight at
   once, the current one included. With no spare slot, nothing extra
   runs, as approved.

A batch that grows at its end changes no tree below its new members,
so it never spends a run that has started.

### 2. `Blocked-by:` queues behind its blocker: `merge.test_ahead.blocked_by: true`

Today a pull request whose `Blocked-by:` names an open one is not
eligible until that one merges. With this key:

1. **Who is queued.** A pull request that would be eligible but for its
   `Blocked-by:`, where every open number it names is a pull request in
   the queue, approved at its head. A blocker that is an issue, held,
   handed back or not approved keeps it held, as today.
2. **Where it goes.** Straight after the last of its blockers, as part 1
   puts a stack's layer after its base: in the same batch if that batch
   hasn't started and has room, with the same owed fairness, or else
   first in the next one.
3. **The prediction** is its head merged onto the commit before it, as
   for any member. One that conflicts is left out, as a layer is.
4. **Merging it.** It stays held as blocked until its blockers have
   merged. Then it is updated, covered by its batch's green top and
   merged like any other member, with no run of its own.
5. **If a blocker leaves the queue** (a push, a hand-back, a new
   `Blocked-by:`), the pull requests it blocks leave the prediction
   with it and are held as today.

An author can then file the next piece of work as soon as the one it
needs is approved, either as a stacked branch (part 1) or based on the
default branch with a `Blocked-by:` line, and nobody waits for the
first to merge before the second is tested.

### 3. Order of work, changed

Part 2 now comes before part 3, since that is where the time goes:

1. Done: part 1 (v0.4.116), and the third amendment of designs/0025
   (v0.4.117).
2. `forge-merger` and `forge-pr-check`: K batches in `ahead.json`, the
   fixed and growing rules above, later tops in spare slots, unresolved
   members left out, discarding on an earlier red; tests;
   `docs/config.md`; a release.
3. `forge-merger`: `blocked_by`; tests; `docs/config.md`; a release.
4. Part 3: `batches.jsonl` and `--batch-report`, then `batch: "auto"`.

| Key | Default | What it is |
| --- | --- | --- |
| `merge.test_ahead.batches` | `1` | Now the most batches in flight at once. A later batch runs as soon as a slot is spare, with the members it has. |
| `merge.test_ahead.blocked_by` | `false` | A pull request blocked only by queued, approved pull requests is predicted straight after its last blocker. |

## Amendment (2026-10-26, second): a red batch keeps its suspects, and a covered member needs no estimate

* Status: approved (Alex Bligh, 2026-10-26, https://github.com/abligh/agent-forge/issues/124#issuecomment-6415961308)

**Why.** On one project, 09:00 to 11:30Z on 26 October, 17 of 21 batch
tops were red and 12 pull requests merged, against 59 in the nine
hours before. 14 of the 17 reds held one pull request that was red with
the default branch since another merged at 07:17Z. Its batches were red
from 08:33Z. It was handed back at 11:17Z, when it had reached the
front of the queue and was tested alone. Two things went wrong.

* **Part 3's search didn't happen.** Part 3 says the search for a red
  batch's first red member needs nothing built: a red verdict is kept
  by tree, and a new batch ends at a tree already red. But that tree
  changes whenever anything joins the queue in front of the red member.
  That happened over and over: a hand-back's return, a stack layer, a
  pull request owed a place. Each change gave the red tree a new one,
  which had no verdict, and the batch grew back to `batch` with the red
  member still in it. Each cycle merged the green half, about 4 pull
  requests, for one red run and one green one, about 25 minutes.
* **Covered members ran anyway.** A member is covered by its batch's
  green top only when its own estimate of the steps it would run
  (`lane_steps`) was worked out against the default branch's current
  tip. Every merge moves the tip. So the next member, updated a moment
  later, has no estimate yet, and it waits for a slot and runs. After
  one batch's top went green at 10:21Z, its members ran for 49 s and
  161 s, and the batch took 9 minutes to merge.

### 1. A red batch's members are its suspects until the red one is found

1. **Suspects.** When a batch's top is red, its members that haven't
   merged become its **suspects**, in their order, with their heads.
   `forge-merger` keeps them in `suspects.json` in its state directory,
   with the red top's tree.
2. **Only the suspects are tested.** While there are suspects, the
   current batch is made of suspects only, and nothing joins in front
   of them or among them. A pull request that becomes ready, a
   returning stack layer, or one owed a place goes behind the suspects,
   in the next batch. Later batches (part 2) are not predicted on top
   of suspects, as part 2's rule 4 says for unresolved members.
3. **A suspect that changes leaves.** A suspect that is pushed to,
   handed back, unapproved or closed is no longer a suspect. Its new
   head is new evidence, and it is queued again as any pull request is.
   A tip that moves for another reason, such as a hand merge, doesn't
   end the search: the suspects are predicted again on the new tip.
4. **The search ends** when one suspect is shown red on its own, on top
   of every suspect before it, and is handed back as a red is today. It
   also ends when no suspects are left. If every suspect merges green,
   the first red was flaky, and the Merger logs `BATCH-FLAKE` with the
   red top's tree. The batch then grows again from the queue.

### 2. The search runs front to back, in spare slots at once

The suspects' prefixes are nested: the first one, the first two, and
so on, each a prediction on the one before. When a prefix is green,
every shorter prefix is covered by it. The search therefore runs as a
front-to-back search that merges as it goes, with several cut points
tested at once:

1. **Cut points.** With m slots free for it (the current batch's slot,
   plus spare slots as part 2 counts them), the Merger tests the
   prefixes at m evenly spaced cut points of the suspects. The last cut
   point, the whole set, is already known red and isn't run again.
2. **The verdicts.** The longest green prefix merges, its members
   covered as any batch's. The shortest red prefix bounds the search.
   The suspects become the members after the green prefix, up to and
   including the last member of that red prefix. A red prefix one
   member longer than a green one names its last member as red: it is
   handed back. Runs of prefixes longer than a red one are stopped,
   which frees their slots.
3. **Examples.** 8 suspects, only the 7th red, 3 slots: prefixes 3 and
   6 run together. Both are green, so 1 to 6 merge, and the suspects
   are 7 and 8. Prefix 7 then runs alone and is red, so the 7th is
   handed back and the 8th joins the next batch. That is 2 rounds after
   the first red, where halving takes 3, and none of them regrows the
   batch. With 1 slot this is designs/0032's halving, with the
   suspects kept.

### 3. A covered member needs no estimate of its own

`forge-pr-check` covers a waiting head whose tree is a member of the
Merger's batch, and which contains the default branch's tip, with the
top's green when the top's run selected the steps the head would run.
Today that needs the head's own estimate against the current tip.
Instead, the top's own selection is enough when the top's base is an
ancestor of the head. The member's changes against that base are a
subset of the top's, so every step chosen for the member is one the
top ran. With designs/0010's dependency records, the records the top's
selection read are the ones the member's would read, or older ones.
The head is then `COVERED` at once, on the tick that sees it. The
estimate is still used when it exists and is newer.

### 4. Order of work, changed

Part 3's first item, "nothing is built for this part", is withdrawn.
This amendment's sections go first, since they are where today's time
went:

1. `forge-pr-check`: section 3; tests; a release.
2. `forge-merger` and `forge-pr-check`: sections 1 and 2:
   `suspects.json`, the prefixes at cut points, stopping longer runs,
   `BATCH-FLAKE`; tests; `docs/config.md`; a release.
3. Then the first amendment's `blocked_by`, and part 3's report.

No new key: both changes apply wherever `batch` is above 1.

## Amendment (2026-10-26, third): a green batch finishes merging first

* Status: proposed

**Why.** At 14:12Z on 26 October a batch of 8 went green, and its
first member merged. Between that merge and the next, no merge was in
flight, so the three stack layers whose bases had just merged counted
as "no batch running" and went first. Part 1 lets a returning layer go
first only then. Every member behind them got a new tree, the top's
green covered none of them, and the seven waited for a new run.

**Change.** A batch whose top is green, with a member still in the
queue at the head it was predicted at, counts as running until it has
merged. A layer that comes back meanwhile goes first in the next
batch, as it does while a merge is in flight. No new key.
