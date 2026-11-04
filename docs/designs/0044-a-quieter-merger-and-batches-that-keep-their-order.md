# 0044: A quieter Merger, and batches that keep their order

* Status: approved (Alex, 2026-11-04, in XMPPEngineer's session)
* Author: XMPPEngineer
* Date: 2026-11-04

## Summary

A review of one project's Merger logs found four rules working against
each other, and one hand-back saying the same thing again and again.
This design changes five things in `forge-merger`:

1. **A hand-back is said once per head**, whatever the reason, until
   the head or its approval changes.
2. **Right of way holds others only while its holder is a candidate**
   at its head, and not handed back there.
3. **A batch's order is fixed once its top's run has started.** What
   would have gone in front of it goes behind it instead.
4. **A suspect found red on its own is handed back at once**, and stays
   out of batches until it has a new head.
5. **Fewer batches are in flight while re-cuts are frequent**, if a
   project sets a limit.

Rules 1, 2 and 4 fix behaviour nobody wanted, so they are on by
default. Rule 3 is on wherever `merge.test_ahead` is. Rule 5 is off
unless configured. Each has a switch in [docs/config.md](../config.md).

## Evidence

All of it from one project, on 2026-11-04.

* **Right of way** (designs/0002) was active in 70 to 90% of the
  Merger's ticks and held 3 to 6 pull requests, also while its holder
  was back with its author.
* **Batches were re-cut about 13 times an hour.** Overdue candidates
  (designs/0037), right of way and three batches in flight
  (designs/0035 part 2) went in front of batches whose runs had
  started. 46% of the green batch-top runs merged nothing.
* **32 of 84 hand-backs in one morning repeated one already told** for
  the same head. One head was told 8 times in an hour, alternating
  between "it conflicts with main, so it can't be updated" and
  "<sha>'s tree is not the clean merge of its parents".
* **A suspect found red on its own** in a red batch's search
  (designs/0035, second amendment) was put into a batch three more
  times before it was handed back.

## 1. A hand-back is said once per head

**Why it repeated.** `handed-back.json` keeps one hand-back per pull
request: its approval, head and reason. A hand-back counted as already
said only when all three matched. The same head can be handed back for
two reasons by two paths. `merge.early_conflicts` tells its author of
a conflict with `main` without asking whether the approval covers the
head. At the front, `decide` asks that first, and hands the head to
its reviewer because a merge after the approval isn't clean. Each
hand-back replaced the other in `handed-back.json`, so each was new
every time.

**The rule.** Once a pull request has been told of a hand-back under
an approval at a head, nothing more is posted on it for that approval
and head, whatever the reason or the recipient. A new head or a new
approval is a new question, as before. The record still lists the
decision, with `already said`. Two hand-backs still count as new:

* one after a `merge.gate` tool's refusal, unless it is the same
  refusal of the same title and body: the author may fix the
  description alone, and a gate's hand-back is keyed by it already;
* one after a gate's refusal for now (`retry`), for another reason,
  since that refusal wasn't the author's to fix.

`merge.early_conflicts` skips a pull request already told anything for
its approval and head, so it does no git work for it either.

**Switch:** `merge.hand_back_once_per_head`, default `true`. With
`false`, each new reason for the same head is said again.

## 2. Right of way only while its holder is a candidate

**Why.** designs/0002 lets a pull request P hold right of way for its
head H while H is clean against `main`, wasn't handed back to its
author for anything else, and has no request for changes standing. It
doesn't ask whether P can merge. So P held others while its new head
waited for a review, and while it was back with its author.

**The rule.** P holds others only while it is a candidate at H
(approved there, not drafted, nothing open blocking it, not waiting
for a code owner) and was not handed back at H under its approval,
to anyone. Otherwise it holds nobody, and the tick's record says why,
under `right_of_way.idle`. The clock that bounds right of way keeps
running, as for a request for changes (designs/0002, amendment).

**Switch:** `merge.right_of_way.while_eligible`, default `true`. With
`false`, P holds others from the moment it has right of way, as
before.

## 3. A batch's order is fixed once its top's run has started

**Why.** The Merger already keeps the members of the batch being
merged in their order (designs/0035, third amendment; designs/0037).
A later batch whose top's run had started, or the first batch while
its top ran, had no such protection. An overdue candidate, one with
right of way, or a returning stack layer went in front of it, and
right of way held its members. Every tree behind that point changed,
so the run was spent.

designs/0035's first amendment already says that a batch whose top's
run has started is fixed: "Nothing joins it, and nothing in it moves."
This rule makes the rest of the Merger's ordering honour that.

**The rule.** The batches in `ahead.json`, up to and including the last
whose top's run has started, keep their members' order. A batch's
top's run has started when `ahead.json` says it is `fixed`, or when the
build check has started it or has a verdict on its tree. A later
batch's tree holds every batch before it, so a run started there fixes
those too. Without `batch`, each prediction counts as a batch of one.

* Overdue candidates, right of way's holder, newly approved pull
  requests and stack layers coming back go behind them.
* Right of way holds none of their members.
* As before, an urgent one (`labels.urgent`) goes in front of
  everything but the batch being merged, and a red default branch's
  fixes (designs/0011, amendment) go first.
* A member that drops out ends it from its batch on, since every tree
  from there changes anyway: a new head whose tree is not the one
  predicted, a hand-back, a lost approval or a hold. GitHub's update of
  the one in flight gives the predicted tree, so it stays. One that
  merged has left the front, and the rest keep their places.

The tick's record lists the fixed members under `fixed`.

**Switch:** `merge.test_ahead.fixed_order`, default `true`, so it is
on wherever `merge.test_ahead` is. With `false`, the others may go in
front, as before.

**What it costs.** An overdue candidate or one with right of way waits
for the batches already running, at most `batches` runs, before it
goes first. Those runs merge their members instead of being spent.

## 4. A suspect red on its own is handed back, and stays out

**Why.** A red batch's search ends when a suspect is red on top of
every suspect before it. The suspect was then left to the queue, "as a
red is today". Its tree on the new tip had no verdict, so it was
predicted again, put in a new batch, and turned that batch's top red,
until its own check at the front handed it back.

**The rule.** When the search finds a suspect red on top of the
suspects before it (the first red prefix is one longer than the
longest green one), the Merger:

* hands it back to its author at once, once, with the reason `a red
  batch's search found it red on its own: <context> is failure on it
  merged with main`, and the tested commit in the comment;
* keeps it in `red-suspects.json` in the state directory, with its
  head;
* leaves it out of every prediction, and neither updates nor merges
  it, until it has a new head. A new approval of the same head is not
  enough: the evidence is the head's.

If the search ended on it at the front, the predictions are made
again without it in the same tick. The tick logs `SUSPECT-RED #N` once.

**Switch:** `merge.test_ahead.hand_back_red_suspect`, default `true`.
With `false`, it goes back into the queue when the search ends, as
before.

**What it costs.** A flaky red can hand back a pull request that would
have passed. The search has already run it twice by then (in the red
top and in its own prefix), and its author can push again.

## 5. Fewer batches while re-cuts are frequent

**Why.** With K batches in flight, a drop-out in the first spends the
runs of all K. Rule 3 stops the re-cuts that came from reordering, but
not those from drop-outs: a member pushed to, unapproved or handed
back. So rule 3 does not make this rule moot. When drop-outs are
frequent, fewer batches in flight spend fewer runs.

**The rule.** A tick re-cuts a batch whose top's run had started when
what is left of that batch, without the pull requests that merged or
closed, is no longer the same pull requests with the same trees in
the new predictions. Its record then says `"recut": true`. When more
than N ticks in the last hour did so, this one included, the Merger
predicts only `batches_when_unstable` batches. The record says
`unstable` with the count. The tick logs `UNSTABLE` when it starts and
`SETTLED` when the count is no longer above N.

**Switches:** `merge.test_ahead.unstable_after_recuts` (N, default
none: off) and `merge.test_ahead.batches_when_unstable` (default 1, at
most `batches`).

**Why off.** How many re-cuts are left once rule 3 is on is not known
yet. A project measures `recut` in its records for a day, then sets N
if it needs it.

## Order of work

1. `forge-merger`: rules 1 to 5, each with its switch and tests;
   `agent_forge/config.py`; `docs/config.md`; one release.
2. A project pins the release. Rules 1 to 4 apply at once. It measures
   `recut` for a day before setting rule 5.

## Measuring it

On a project with `merge.test_ahead`, over a day before and after:
repeated hand-backs per head, ticks with right of way holding someone
whose holder was not a candidate, ticks with `recut`, green batch tops
that merged nothing, and merges per hour.
