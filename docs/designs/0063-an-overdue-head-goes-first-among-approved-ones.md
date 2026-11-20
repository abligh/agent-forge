# 0063: An overdue head goes first among approved ones

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-20
* Issue: (this pull request)

## Summary

`checks.max_wait_minutes` promises that no head waits on priority for
ever. With `checks.approved_first` it doesn't hold: `next_head` returns
the best approved head before it looks at overdue ones, and among
approved heads it orders by `pr_since`, when the pull request *first*
waited (designs/0052 §1). So an approved pull request with one head,
newer than a set of older ones that keep pushing new heads, waits as
long as they keep pushing. And an unreviewed head runs only when no
approved one waits, while the Router waits for that head's check before
asking for a review, so it can get neither.

This design changes two things:

1. Among approved heads, after the Merger's own and its predictions, a
   head whose **own** wait has reached `max_wait_minutes` goes first,
   longest own wait first.
2. The Router gives the pull request's reviewer the turn when an
   unreviewed head's check has been pending longer than
   `max_wait_minutes`, so a reviewer can arm it and put it among the
   approved ones.

The kept place stays, for heads that aren't overdue.

## The evidence

On one project's host on 2026-11-20:

* `STARTED` lines from 00:00Z to 19:30Z: about 480 starts. Of these, 121 went
  first as approved and 5 as overdue ("it has waited N minutes").
* At 19:30Z, 26 heads waited. Approved heads not held by the Merger,
  with their own wait: 5.5 h, 4.6 h, 3.4 h, 3.1 h and 2.6 h. Meanwhile heads
  pushed at 19:21Z started within a minute, because their pull requests'
  kept places dated from 11-18 and 11-19 and they had restacked several
  times since.
* An unreviewed head, queued at 09:22Z, was still pending at 19:52Z.
  The Router gave its pull request to nobody (rule 5: check pending),
  so no reviewer was asked, and no review meant no place among the
  approved heads. It was reviewed only after a person asked about it.

The 30-minute cap is configured. It just never applies while any
approved head waits, and on a busy host one nearly always does.

## Why a head's own wait, not the pull request's

The kept place exists so that a rebase or a red run doesn't send a pull
request to the back (0052 §1). That stays: ordering among heads that
aren't overdue doesn't change. But a head that was pushed a minute ago
has not been starved. Its pull request's long `pr_since` measures how
long the *work* has been around, not how long this run has waited.
Overdue is about starvation, so it should count the head's own wait
(`queued`). A pull request that restacks often still gets its kept
place each time. It just can't use it to pass a head that has waited
longer than the cap.

## The change

In `next_head`, the approved branch's sort key becomes:

    (pr != first_pr,
     pr not in queued,
     not own_overdue(s),          # new
     pr in blocked,
     -own_wait(s) if own_overdue(s) else order(waiting, s))

`own_overdue` is `when - queued >= MAX_WAIT`, false without
`max_wait_minutes`, for a failed head, or for a default-branch head.
The Merger's own head and its predictions still go first: while those
wait, nothing merges. Heads the Merger holds (`blocked`) stay last,
overdue or not, since their base must change before their run means
anything.

The unapproved branch already uses `max_wait_minutes`, and it doesn't
change.

The Router's new rule goes after rule 5 and before rule 6. When an
unreviewed head's check is pending, has been queued longer than
`max_wait_minutes`, and the reviewer hasn't spoken since the push, the
turn is the pull request's reviewer's: "its check has waited N
minutes; review it now and arm it, so the check can run". The rule is
on with `router.review_waiting_heads`, off by default.

## What it costs

Overdue approved heads go before kept-place heads that aren't overdue.
With a 30-minute cap and a deep queue, most heads eventually become
overdue, and the order then is by own wait, which is FIFO. That is
the cap's stated intent. If FIFO turns out too slow for restacked
pull requests, raising `max_wait_minutes` (say to 120) restores more
of the kept-place order and still bounds starvation.

The Router rule asks for reviews earlier than now, before green.
Reviewers on the project already arm approvals before green
(`labels.armed`), so this only moves when they are asked.

## Tests

* An approved head queued `max_wait_minutes` ago goes before an
  approved head whose pull request has an older `pr_since` but whose
  head was queued a minute ago.
* The Merger's own head and a predicted one still go first.
* A blocked overdue head is still last.
* Without `max_wait_minutes`, the order is as now.
* The Router rule: on, with an unreviewed head pending for longer than
  the cap, gives the reviewer the turn; not when the reviewer has spoken
  since the push; not when it's off.
