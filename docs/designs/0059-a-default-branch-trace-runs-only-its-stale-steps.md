# 0059: A default-branch trace runs only its stale steps

* Status: approved (Alex Bligh, 2026-11-17, to XMPPEngineer: "agent-forge #479 - approved")
* Author: XMPPEngineer
* Date: 2026-11-17
* Issue: #479

## Summary

designs/0036 part B, now with the measurement it waited for. When
`trace_matched` (designs/0018 part A, with 0036's
`match_main_unrecorded`) decides that a default-branch head runs traced
in place of a match, the run executes every applicable step, although
`main_needs` has already named the few whose records are stale. This
design has that run execute only the steps `main_needs` names, and
compose the head's record from those steps' new records and the
existing records of the rest, as a pull request's `checks.fresh` run
already does (designs/0010, `compose`).

## The evidence

On one project's host over one night (00:00–06:10Z, about ten merges an
hour, four slots):

* 39 default-branch heads were checked; 29 matched their prediction and
  10 ran traced, about one every 35 minutes (00:29, 01:08, 01:42, 02:15,
  02:47, 03:26, 04:00, 04:50, 05:27Z), as `depends_minutes` = 30 allows.
* Every one of the 10 logged the same reason: `MAIN-TRACE … the records
  of test-rust, test-web, vet are stale or missing`. Three steps.
* Each ran all 15 applicable steps. One measured run took 564 s
  (04:50:24–05:00:00Z). Its step logs finish in sequence, and the three
  named steps account for about 4 minutes of the 9.6 (vet 34 s, test-web
  138 s, test-rust 73 s).
* The traced run held a slot while merge-time re-checks queued: a
  `merger_weight` quick run waited 710 s with all four slots on
  full-tier work, one of them this trace.

## What changes

1. When `trace_matched` returns true, the worker passes the steps
   `main_needs` returned to the run, as `selection["ran"]` does for a
   pull request. Steps not named are neither run nor reused: their
   existing records stand.
2. The run's record is composed per step, exactly as for a `checks.fresh`
   pull-request run: each run step writes its own record, and the
   head's record points at those plus the unchanged ones.
3. `main_needs` returning `None` (no record answers at all) still runs
   every step, as today. So does a head that `trace_matched` didn't
   choose for tracing and that no prediction matched.
4. The verdict posted on the head is unchanged: its tree was tested
   green by its prediction (`tested(..., ok=True)`), which is why it
   would otherwise have been matched. The traced steps only refresh
   records.

## What it costs

* **The other steps are not re-run on the default branch at that
  head.** They were already run green on the same tree by the
  prediction, so nothing is checked less than with a plain match, which
  0036 accepted. What is given up is the full run that, today, happens
  to re-test them every half hour. A selection fault that skips a test
  is then caught by the next run that does select it, not by the
  half-hourly trace.
* **Records of the steps not named keep their age.** That is what
  `main_needs` already judged: they are fresh.

## Expected saving

About 5 minutes of one slot per trace on the measured project: roughly
8–9 slot-minutes an hour, about 3–4% of four slots, landing mostly on
merge-time re-checks at the hours when they queue.

## Rollout

Off by default, behind `reuse.trace_stale_only`, which needs
`trace_matched_main`. Measured after a day: `MAIN-TRACE` run times,
`merger_weight` quick waits, and that no default-branch head goes red
that a full trace would have caught earlier.
