# 0032: Merge in batches

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-25

## Summary

With `merge.test_ahead`, the Merger merges one pull request per
`forge-pr-check` run: each pull request's predicted commit is checked in
its own run, and the queue can drain no faster than those runs finish.
When pull requests arrive faster than that, the queue only grows.

This design lets one run cover several merges. The Merger predicts the
next `batch` pull requests as today, but the build check runs only the
last of those predictions, the batch's top. If the top is green, every
commit below it in the batch counts as green too, and the Merger merges
the whole batch, one pull request per tick, with no run in between. If
the top is red, the batch is split, and its halves are checked the same
way until the pull request that turned it red is found and handed back.

Off until `merge.test_ahead.batch` is set above 1.

## Evidence

One project, one working day, `test_ahead` at depth 3:

| Hours (UTC) | Pull requests opened | Merged | Mean queue |
| --- | --- | --- | --- |
| 08–12 | 59 | 49 | 3–5 |
| 13–16 | 42 | 30 | 7–15 |
| 17–19 | 41 | 36 | 16–19 |

Arrivals ran at about 12 an hour and merges at about 10, so the queue
quadrupled in the afternoon: about 90 minutes from approval to merge.

Where the time goes, over the 41 merges after 17:00:

* **The Merger is fast when a prediction is ready.** 30 of the 41
  (73%) merged on a head whose tree had been tested as a prediction.
  Each merged within a minute of its status being posted.
* **The runs are the limit.** A prediction is a full run of about 10 to
  15 minutes (designs/0031 measured 924 s), and each merge needs its
  own. With depth 3, four runs are in flight for four merges. They share
  the host's slots with every other head: 12 to 30 were waiting all
  afternoon.
* **Predictions are thrown away.** The other 11 merges were checked
  afresh after a hand-back, a push or a reorder spent the prediction
  for them, and every prediction behind it.

So halving the runs a merge needs roughly doubles how fast the queue
can drain, whatever else the host is doing.

## The change

`merge.test_ahead.batch: N` (default 1, as today). With N above 1:

1. **The batch.** The Merger predicts as today: commit k is the merge
   of pull request k onto commit k − 1, from the default branch's tip.
   The first N predictions are the batch. `ahead.json` marks the Nth
   as the batch's `top` and the ones below it as its `members`, each
   with its tree.
2. **One run.** `forge-pr-check` runs only the top, ahead of every head
   but the default branch's (as predictions go first today). The
   members get no run of their own.
3. **Green covers the batch.** When the top is green, a head whose tree
   is a member's tree is posted `success` with the description
   `green: covered by batch <top> (tested as <sha>)`, as long as
   `ahead.json` still names that batch, with the same trees, and is no
   older than `MERGER_FRESH`. The Merger then updates and merges each
   member in order, then the top: one tick each, no wait.
4. **Red splits it.** When the top is red, the next prediction is the
   lower half's top (`batch` halved, rounding up), and so on down. A
   batch of 4 finds its one bad pull request in at most two more runs,
   then that pull request goes back to its author as a red does today.
   If the halves are all green, the red was in the top pull request
   itself, or flaky. That is decided by its own run, as today.
5. **A broken batch.** If anything in the batch changes before it has
   merged (a push, a hand-back, a new approval order), the batch is
   predicted again from where the queue now stands. A member's
   `covered` status already posted stays posted, but the Merger merges
   a member only while the batch's top tree is still the top of the
   current `ahead.json`.

## What it guarantees, and what it doesn't

* **The tip is always tested.** After the whole batch merges, the
  default branch's tip has exactly the top's tree, which a real run
  turned green. That is what `test_ahead` guarantees today, per merge.
* **The commits in between aren't run.** A member's tree is never run
  on its own. If a later member fixes something an earlier one broke,
  the default branch is broken between those two merges, for a minute
  or two. GitHub's merge queue makes the same trade when it batches.
  The default branch's own check (`main_traced`, designs/0028's red
  default branch) still runs on its tip as today.
* **A batch that stops halfway.** If a member is handed back after
  earlier members merged, the tip is a member's tree, which wasn't
  run. Rule 5 makes this rare: a member merges only while its batch's
  top is current. When it happens, the default branch's own check is
  the backstop, as for any red tip.
* **Nothing new on GitHub.** Branch protection is unchanged: every
  head is still up to date and has a `success` status when it merges.
  The status's description says which run it rests on.

## Cost

* **Fewer runs.** N merges cost one run when the top is green, plus
  ⌈log₂ N⌉ more when one member is red.
* **More thrown away when a batch breaks.** A push to a member spends
  the batch's run. With N = 3 to 4 and today's 27% of predictions
  thrown away, the runs saved still outnumber the runs lost.
* **A slower red.** The bad pull request in a red batch is found after
  up to ⌈log₂ N⌉ more runs. Its author hears later, but nobody else
  waits behind it: the other members merge as soon as their half is
  green.

## Configuration

| Key | Default | What it is |
| --- | --- | --- |
| `merge.test_ahead.batch` | `1` | How many pull requests one prediction run covers. `1` is today's behaviour. 3 or 4 suits a queue that grows by a few an hour; more than `depth` + 1 is refused. |

## Order of work

One pull request per tool, each off until `batch` is set:

1. `forge-merger`: `batch` and `members` in `ahead.json`; merge a
   member only while its batch's top is current; the red split
   and the printed lines (`BATCH #a..#b: top <sha> green, merging N`
   and `BATCH #a..#b: top red, checking #a..#m`); tests.
2. `forge-pr-check`: run only a batch's top; post a member's tree green
   when the top is green; the description; tests.
3. `docs/config.md`'s `merge.test_ahead`, and a release.

A project then sets `batch` in its own configuration.
