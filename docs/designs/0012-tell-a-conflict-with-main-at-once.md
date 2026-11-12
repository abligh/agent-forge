# 0012: Tell a conflict with the default branch at once

* Status: approved (Alex, 2026-10-12, at 8a5b2cd,
  <https://github.com/abligh/agent-forge/issues/21#issuecomment-6121146422>)
* Implemented: v0.4.36, off until configured.
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

## Amendment, 2026-11-12: resolve slots

* Status: direction approved by the owner; this text describes it as
  built.

### Problem

On one project over 24 hours, 48 pull requests were handed back 76
times for a conflict with the default branch (33 once, 15 two to six
times). Each hand-back was followed by a resolution by hand, then a
hand-back to the reviewer because the resolving merge wasn't clean
(57 of those), and by more check runs of the pull request's own:
those handed back averaged 2.5 runs against 1.3. A resolution made
while the queue keeps merging is often undone before the pull request
reaches the front, so it is resolved again. Telling every conflict at
once, which this design was for, makes that worse in a busy queue:
everyone resolves, and most resolve twice.

### Design

`merge.resolve_slots: N` (integer, default 0: no limit, as before).
With N above 0, at most N pull requests are *out for resolving* at
once.

1. **Out.** A pull request is out while `handed-back.json` has it
   handed back for a conflict with the default branch (a reason that
   starts with `CONFLICT_REASON`, a restack's included) at the head
   and under the approval it has now, it isn't a draft, and it still
   conflicts (`git merge-tree` against the tip; a restack's conflict
   is with the layer below's final head, so it counts until one of the
   other events). Its slot frees when its author pushes, it gets a new
   approval or loses it, it closes or merges, or the default branch
   moves so that it merges cleanly. The re-review after the fix holds
   no slot: by then its head is new.
2. **Waiting.** Every other conflict a tick finds, at the front, behind
   it (`early_conflicts`), from a failed restack, union or regeneration,
   or from GitHub refusing an update, is collected rather than handed
   back, and the queue moves past it as it would past a hand-back. At
   the tick's end the free slots go to the first of them in the order
   below, and each gets the hand-back it would have had. The rest are
   held with `conflicts with main; waits for a resolve slot (N out:
   #a, #b)`: no comment, nothing in `handed-back.json`, out of the
   record's `queue` and of every prediction. The hand-backs given are
   posted before the holds are set, so their comments' lists of the
   author's other pull requests don't show a waiting one as conflicting.
3. **Order.** Labelled urgent first, then other urgent ones, then the
   one with right of way (unless idle), then those that would board
   soonest: their place in the queue, in groups of `test_ahead.batch`
   when there are batches; within a group a stack's base with more
   approved layers above it first, then the queue's order; last, one
   not in the queue (held for an instance), oldest first.
4. **Stacks.** A layer above a base that is out is held as stacked, as
   before, and isn't handed back separately.
5. **Other rules.** Right of way and `hold_conflicting` act before the
   slots: a pull request either holds isn't in the queue the slots are
   given from. Right of way counts conflict hand-backs, so with slots
   it is earned only by those given. A waiting pull request is still in
   the queue `hold_conflicting` reads, so one that conflicts with it is
   held behind it, as behind any other. The yield counts only the
   hand-backs given.

### Not covered

A conflict found by `resolve_ahead` (a regeneration or union tried for
a train's members that fails) is still handed back at once; it is rare,
and the Merger had already tried to resolve it itself.

### Evidence

The simulator (`tests/merger_sim.py`, `hot`) has pull requests that
mostly touch one region of one file, so each merge makes the others
conflict; an author resolves a hand-back after a delay and the
reviewer approves the fix. Thirty pull requests, 70% of them on the
one file, seeds 1 to 3; conflict hand-backs per merge (each followed
by a resolution and a re-review) and hours to merge all thirty:

| slots | hand-backs per merge | hours |
|---|---|---|
| 0 (no limit) | 1.60, 0.97, 1.23 | 8.33, 7.98, 8.12 |
| 1 | 0.60, 0.53, 0.53 | 8.62, 9.15, 8.48 |
| 2 | 0.93, 0.80, 0.90 | 8.92, 7.73, 8.93 |
| 3 | 1.20, 0.97, 1.07 | 8.28, 7.50, 7.57 |
| 4 | 1.37, 1.27, 1.20 | 8.28, 8.65, 7.95 |

**Choosing N.** Where the changes meet in one place, each merge undoes
every other resolution out at the time, so a slot spends about one
resolution per merge while it is held, and N slots up to N - 1 too
many. Four slots barely help in this case because, without a limit,
four or fewer were out at 86% of the hand-backs and resolutions of
seed 1 (at most seven): the limit rarely bites. One slot spends the fewest resolutions, at some cost in
hours, as each waits for the one before it; two halve the waste for
about the same hours as no limit. The scenarios without conflicts run
exactly as without the key.
