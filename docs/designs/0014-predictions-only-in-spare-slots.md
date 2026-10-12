# 0014: The Merger's predictions only in spare slots

* Status: proposed
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
