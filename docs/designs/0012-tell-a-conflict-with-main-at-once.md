# 0012: Tell a conflict with the default branch at once

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-12

## Summary

The Merger decides the candidates in queue order and stops at the
first one it acts on (an update, a wait for the check, a merge). Every
candidate behind it waits unexamined. So a pull request that a merge
has left conflicting with the default branch hears nothing until it
reaches the front, and only then is it handed back to its author.

On one project, a pull request third in the queue became conflicting at
03:49 when the one ahead of it merged. The Merger told its author at
07:02, after fifteen other merges, and the author fixed it within the
hour. The three hours between were spent waiting for news nobody
needed to wait for.

## Design

`merge.early_conflicts: true` (`forge-merger`, default false). After
the tick stops, it checks every candidate it didn't reach. A head that
doesn't contain the default branch's tip, and that `git merge-tree`
says conflicts with it, is handed back to its author now, exactly as it
would be at the front:
- the same `CONFLICT_REASON` and comment;
- said once per approval and head (`handed-back.json`);
- recorded as a `HAND-BACK` decision with `"early": true`.

A head git can't compare is left to be decided when it is reached, as
before.

Holds come first. A candidate held behind another
(`hold_conflicting`), or held by a right of way, isn't in the queue the
tick walks, so it isn't checked early. Its hold already says one rebase
will cover both.

## Interactions

* **The yield** (`yield_after_hand_back`) counts these hand-backs like
  any other, unless `yield_ignores_conflicts` is set. For a conflict it
  should be set, since a conflict is mostly what merged after the
  approval.
* **Right of way** counts conflict hand-backs. An early one is earned
  sooner, by the same rule.
* **Predictions** (`test_ahead`) skip a pull request already handed
  back, so none is spent on a head that can't merge.

## Cost

One `merge-tree` per unreached candidate per tick, on heads the
checkout mostly has already (`hold_conflicting` fetches them too). That
is milliseconds against minutes.

## Evidence

The case above. The Merger's record shows the candidate third in every
tick's `queue` from 03:50 to 07:02, and the hand-back at 07:02.
