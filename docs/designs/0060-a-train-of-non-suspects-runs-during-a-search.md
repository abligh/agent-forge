# 0060: A train of non-suspects runs during a search

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-18
* Issue: #485

## Summary

When a train's top is red, forge-merger searches its members for the
culprit (`SUSPECTS`, `suspect_round`) and keeps the trains behind it
(`kept_trains`) until the search ends. Nothing else merges meanwhile,
however many approved pull requests wait and however many slots are
free. This design lets one train made only of pull requests that are
not suspects, predicted on the default branch's tip without the
suspects, run during the search and merge when green. The setting is
`merge.test_ahead.bypass_search`, off by default.

## The evidence

On one project's host on 2026-11-18 (00:00–15:12Z, four slots):

* 70 trains merged 103 pull requests. 5 tops went red, sized 4, 4, 4,
  5 and 7, and 4 culprits were found and handed back.
* The searches took 15 to 56 minutes each. In the longest (about
  14:28–15:24Z), a 9-PR train of approved pull requests, cut at about
  14:52Z, waited the whole time, and the merger's stall notice fired
  twice.
* Full runs took 532–1202 s. A search of a 7-member train ran three
  prefixes at once, then one suspect, then another: three rounds of
  full runs in sequence.

## Why it can't simply run the next kept train

A later train's tree holds every train in front of it (designs/0047,
amendment of 2026-11-11), so the kept second train contains the red
train's members, the culprit among them, and is red too. The bypass
train has to be a different tree: the default branch's tip plus the
non-suspects only.

## What changes

1. While a search runs, and `bypass_search` is on, each tick cuts at
   most one bypass train from the boarding pull requests that are not
   suspects and are not stacked on one (`with_suspects`). It is cut as
   a first train is (`cut_trains`), on the default branch's tip, not on
   the search's base (`train_base`), and said as `TRAIN-BYPASS: #a #b`.
2. It takes a slot only when the search's own runs leave one free
   (`suspect_cuts` sizes the search's runs to the slots; the bypass
   train takes what is left, never more than one).
3. Green, it merges as a train merges (`train_merge`), and the default
   branch moves. The search goes on, on its kept base, as it already
   does when the default branch moves under `labels.keep_trains`
   (`SEARCH-KEPT`).
4. When the search ends, the kept trains are cut again on the new tip
   (as `TRAIN-RECUT` does), without the culprit and without what the
   bypass train merged.

## The cost, and the choice it leaves

A green prefix the search found can merge only on the base it was
tested on. Once a bypass train has merged, those members need one more
run on the new tip before they merge. On the longest search above that
would have cost one more full run (about 15 minutes) for the suspects
found green, against the 30 minutes and more that the 9 approved pull
requests waited.

Two choices for the owner:

* **(a)** The bypass train merges as soon as it is green, and the
  search's green members are re-run afterwards. Most throughput, one
  extra run per search.
* **(b)** The bypass train's top runs during the search, but it merges
  only when the search ends, in front of the kept trains. Nothing is
  run twice, but the bypass train still waits for the search. It saves
  only the bypass train's run time, about 15 minutes, not the whole
  search.

The default proposed is (a), behind the setting, so a project can
measure it.

## Not in this design

* Stopping a search's remaining runs once its culprit is known.
* More than one bypass train at a time.

## Testing

* A unit test of the cut: suspects, and layers stacked on them, never
  board the bypass train. Its base is the tip, not `train_base`.
* A merger-sim scenario (docs/merger-sim.md): a red 7-member train and
  9 approved pull requests behind it. Measure the minutes to the 9th
  merge with the setting off, (a) and (b).
