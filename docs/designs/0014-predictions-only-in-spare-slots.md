# 0014: The Merger's predictions only in spare slots

* Status: approved (Alex, 2026-10-12, https://github.com/abligh/agent-forge/issues/22#issuecomment-6132254794)
* Implemented: v0.4.43, off until configured.
* Author: XMPPEngineer
* Date: 2026-10-12

## Summary

With `merge.test_ahead`, the build check runs the Merger's predictions
before any ordinary pull-request head, except the Merger's own head and
a head that has waited `checks.max_wait_minutes`. Its documentation says
predictions use "free slots", but in practice a prediction takes a slot
that a waiting head would otherwise get.

That is a good bet when most predictions are used. On one project whose
two slots were busy 96% of the day, most were not. Measured with
`forge-stats` over four and a half hours (after the matching fixes of
v0.4.32 and v0.4.33):

| | |
| --- | --- |
| predictions run | 30 |
| used by a later head (posted green without a run) | 8 |
| slot time on predictions | 2.9 of about 8 slot-hours |
| slot time on predictions never used | 2.0 slot-hours |
| a pull-request head's wait for a slot | median 106 s, p90 32 min |

Predicting less deep doesn't help. Of the predictions for the pull
request just behind the front, 5 of 20 were used. Most predictions go
stale because the queue's order changes before the prediction is
reached: other pull requests merge first (yields, right of way, a new
approval), or a merge lands while the prediction runs.

So on a full host, a quarter of the check's capacity went on
predictions nobody used, while real heads waited for a slot.

## Design

`merge.test_ahead.spare_slots_only: true` (default false). A prediction
is started only when no ordinary head is waiting to be picked, that is,
only into a slot that would otherwise stay idle. The Merger's own head
and the default branch keep the priority they have today. They are
ordinary picks, so they now also go before predictions.

Nothing else changes:
- which predictions the Merger makes;
- how they are matched;
- `depth` and `max_age_hours`;
- the fast lane, which never runs predictions.

A running prediction is never stopped for a head that arrives later.
Stopping it would waste the time it has already spent, and a run is a
few minutes long.

## Why it is safe

The verdicts are the same. A prediction only ever lets a head be posted
green without a run when its tree was already tested green. With this
switch, fewer trees are tested ahead, so more heads are run themselves,
exactly as without `test_ahead`.

## Cost

When the slots are full, the Merger's first pull request is checked
only after the one ahead of it merges, as without `test_ahead`. That
costs about one run per merge, but only for a prediction that would
have been used, about a quarter of them in the measurement above. With
spare capacity, for example a host with more slots, predictions run as
today.

## Measuring it

With the switch on for a day, compare `forge-stats` with the day
before:
- a pull-request head's wait for a slot;
- the time from push to verdict;
- approval to merge;
- merges per hour;
- predictions run and used.

## Amendment (2026-11-10): a spent prediction is stopped

Approved by Alex on 2026-11-10 at about 20:58Z, in answer to the proposal in agent-forge #281 ("Shall I go ahead with 2 and 3?" — "Yes").

**What happened.** On one busy evening (7 slots, 84% busy), seven
prediction runs went on for 5 to 22 minutes each after a re-cut had
spent them, about 1.5 slot-hours in 1 h 45 min. No later head or batch
used their trees. A superseded pull-request head is stopped
(`SUPERSEDED`), but a prediction never was: it is no pull request's
head.

**The rule.** With `merge.test_ahead.stop_spent`, each tick of the
build check compares every running prediction with the Merger's
`ahead.json`. One whose commit and tree are in none of its entries,
later batches' included, is stopped as a superseded head is: SIGTERM
to the worker's process group, `STOPPED-SPENT` in the log, no verdict,
and the slot is freed. A prediction whose tree is still among the
entries under another commit keeps running, since a head can still be
matched to it. Nothing is stopped while `ahead.json` is stale,
unreadable or empty, or when git can't name the commit's tree.

**Switch.** `merge.test_ahead.stop_spent`, default `false`.
