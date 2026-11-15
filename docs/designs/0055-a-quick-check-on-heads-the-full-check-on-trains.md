# 0055: A quick check on heads, the full check on trains

* Status: approved (Alex Bligh, 2026-11-15, on #407: "Approved")
* Author: XMPPEngineer
* Date: 2026-11-14
* Issue: #392 (research: FleetResearcher)

## Summary

The build check gets two tiers:

1. **A quick check on every pull request's head.** It runs the steps a
   project marks for heads and posts its own context,
   `checks.quick.context` (default `<checks.context>/quick`). It is the
   reviewers' signal, and what the Merger requires of a head to board a
   train.
2. **The full check only where something may reach the default
   branch.** That means the top of each train (a lone pull request is a
   train of one) and the default branch itself (`checks.main`). It posts
   `checks.context` as today, and is what the Merger requires to merge.

The split is off unless the project sets `checks.quick` and
`merge.gate: "train"`. With neither set, nothing changes.

## Why

Today `forge-pr-check` runs the same steps on a head as on a train's
top, and the Merger needs `checks.context` on both
(`merge.own_head_green`). Every change is tested in full at least
twice, and once more for each new head: a merge of the default branch,
a conflict resolution, a fix.

On one project (a large Rust workspace, about 200 pull requests a day,
seven slots), on its worst recent day:

| Where the check's slot time went | Share |
| --- | --- |
| First run of a pull request's head | 26% |
| Later runs of the same pull request's heads | 43% |
| Train and prediction runs | 20% |
| Default-branch runs | 7% |
| Stopped before a verdict | 4% |

So 69% of the capacity went on heads, and the run that gates the default
branch got 20%. The slots were 86% busy, and a new head waited a median
3.2 hours for one. Reviewers approve on the head's green, so everything
before the Merger waited on that queue. A merged pull request was small
(median 286 changed lines in 6 files) and cost 2.1 full runs.

The head's run answers "can a reviewer look at this?". The train's run
answers "may this reach the default branch?". Only the second needs the
whole suite. That is the usual shape elsewhere: the Rust project runs a
small subset after each push, and the complete suite on the merge
commit before it lands.

Smaller changes don't remove the double test:

* **Fewer slots, or faster runs.** That project's tests grew about 5%
  a day, so a faster run is absorbed within a fortnight.
* **`own_green_to_board: false`, `layers_need_own_green: false`.** A
  pull request may board without its own green, but its head still gets
  a full run, because the reviewers read the same context.
* **Path-scoped steps (`steps[].pull_requests`, `depends`, `reuse`).**
  In a compiled workspace nearly every change selects nearly every step:
  the median pull request recompiled 61% of the workspace.

### The replay

FleetResearcher replayed real days through the Merger simulation. Each
head got a 4-minute quick check, full runs went only to trains, and
approvals followed the simulated check's green, not the trace's times.
On one day (192 pull requests; full runs 22.6 minutes on a head and
23.6 on a train; seven slots):

| | Merged | Open to merge, median | Slots busy | Full runs a merge |
| --- | --- | --- | --- | --- |
| One tier, as now | 119 | 537 min | 92% | 3.3 |
| Two tiers | 181 | 61 min | 33% | 1.1 |
| Two tiers, every red the trace holds missed by the quick check | 178 | 87 min | 33% | 1.2 |

On two days overlaid (451 pull requests), one tier merged 5: the queue
never drained. Two tiers merged 255 to 332 with the slots half idle,
depending on the train run's length. With every red missed by the quick
check at that load, merges fell to 175, and the queue seized in the
second half of the day.

Two conclusions follow for the design:

* **The quick check must catch build breaks.** It should build what
  the change touches and what depends on it. The project chooses its
  steps, but the documentation says so, with this evidence.
* **Reds that reach a train are cheap at an ordinary load and
  expensive at twice that.** The figure a project watches is red train
  tops per merge (`forge-stats`, below).

## The design

### Steps: which tier

`checks.steps[].tier` is one of `"head"`, `"train"` or `"both"`
(default `"both"`, so an existing step list is unchanged). A quick run
runs the steps whose tier is `"head"` or `"both"`. A full run runs the
steps whose tier is `"train"` or `"both"`. `pull_requests` (designs/0048)
and `depends` then narrow each run's steps as today. A `"train"` step
never runs on a pull request's head.

### `checks.quick`

`{"context": name, "slots": n}`, default none (off).

* `context`: the status a quick run posts, by default
  `<checks.context>/quick`. Posted by `checks.poster`, like
  `checks.context`.
* `slots`: how many quick runs may run beside the full ones (default
  1). A quick slot works like the fast lane (designs/0006): one more
  slot, counted in each run's `CHECK_CPUS` share, so a quick run never
  takes a whole full slot's CPUs. A quick run may also take a free full
  slot that no full run wants, which keeps an idle host busy.

With `checks.quick` set:

* **A head gets a quick run and no full run of its own,** unless the
  Merger asks for one, as it asks today for its predictions
  (`merge.test_ahead`).
* **A full green covers the quick context.** A tree with a green full
  verdict here posts the quick context green ("covered by the full
  check"). A quick green never covers the full context.
* **Reuse is per tier.** Matching a head to an earlier verdict of its
  tree (`tested`) only takes a verdict of the same tier, or a full one
  for a quick run. `reuse`, `depends`, `merge_default`, the caches and
  `scoped_minutes` apply to both tiers, on their own steps.
* **The record says the tier.** `verdicts.log` lines and `state.json`
  results carry `tier` (`"quick"` or `"full"`), so `forge-stats` can
  report each tier.
* **The default branch's runs are full runs,** as `checks.main` says.

### `merge.gate`

`"head"` (default, today's behaviour) or `"train"`. `"train"` needs
`checks.quick`, `merge.own_head_green`, `test_ahead` and
`test_ahead.train_merge`. The configuration check refuses it without
them.

With `"train"`:

* **Candidates.** A pull request is a candidate when the quick context
  is green on its head. The same walk-back `own_head_green` uses for
  merges of the default branch applies, so a head brought up to date
  keeps its green. Every rule that reads the head's green today reads
  the quick context instead: `own_green_to_board`,
  `layers_need_own_green`, `merge.approval: "green"`, and
  `checks.approved_first`.
* **Merges are trains only.** A pull request merges only inside a train
  pull request (`train_merge`), on the full context green on the
  train's top, covering every member's tree as today. A lone candidate
  is a train of one: its prediction (the default branch with it merged)
  gets a full run, and it goes in as a train pull request. Nothing ever
  merges on the quick context alone. This also keeps branch protection
  simple: the protected branch still requires `checks.context`, and
  only a train pull request carries it.
* **Reds.** A red full run on a train names its suspects and searches
  them as now (`hand_back_red_suspect`, `retry_red`). The hand-back
  names the step that failed, and says when the head's quick check
  doesn't run that step (its tier is `"train"`). The author then knows
  to run it locally.

### Reporting

* **`forge-stats`** reports the two tiers apart: runs, slot time and
  waits for each. It adds `red_train_tops_per_merge`, the figure that
  says which side of the load a project is on. `stats_watch` can watch
  it.
* **`forge-flow` and `forge-queue`** show both contexts for each pull
  request: quick on the head, full on its train.
* **The Merger simulation** (`tests/merger_sim.py`) models the tiers:
  quick runs of `quick_minutes` in `quick_slots`, full runs on tops
  only, and the Merger's `gate`. A replay test with two tiers is part
  of the test suite.

## What a project does to use it

1. Mark its steps' tiers. The quick tier must build what a change
   touches and what depends on it, or reds reach trains (see the
   replay).
2. Set `checks.quick` and `merge.gate: "train"`, with
   `test_ahead.train_merge`.
3. Point its reviewers at the quick context: `merge.approval`, and the
   reviewer agents' prompts.
4. Leave branch protection requiring `checks.context`.
5. Watch `red_train_tops_per_merge`. When it climbs, move a step from
   `"train"` to `"both"`.

## Steps

Each step is its own pull request. Each is inert until the keys are
set.

1. **`forge-pr-check`:** `steps[].tier`, `checks.quick` (context,
   slots, covering, reuse per tier), and the tier in the records.
2. **`forge-merger`:** `merge.gate: "train"`: candidates on the quick
   context, trains only (a lone pull request as a train of one), and
   the red hand-back naming the step.
3. **Reporting:** `forge-stats` per tier, `red_train_tops_per_merge`,
   and `forge-flow` and `forge-queue` showing both contexts.
4. **The simulation:** the tiers, and a replay test with two tiers.

## Acceptance

* With the new keys absent, behaviour and tests are unchanged.
* With them set, in the simulation:
  * a head gets a quick run and no full run;
  * a train's top gets a full run;
  * no pull request merges without a green full run covering its tree;
  * a red train hands back its suspect with the step named.
* A replay test with two tiers is part of the test suite.
* `docs/config.md` documents the keys and links here.

## Not in this design

* **Trains losing members.** With the slots half idle, the limit moves
  to trains that lose members. That is a separate issue, to follow.
* **A quick step list chosen per change.** `pull_requests` and
  `depends` already narrow each tier. Nothing more is proposed.
