# 0062: A main trace waits for the train top

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-19
* Issue: #499

## Summary

The default branch's traced run (the "main trace", which records what
each step read so later heads can reuse it) gives way to predictions
only until it is due: `checks.depends_minutes` (default 30) after the
merge. From then on it goes before later tops. With
`checks.main_cpu_weight` it runs at the same CPU weight as the train
top, so in busy hours, when every slot has a prediction to run, the
trace starts at that bound beside a top that is already running, and
the next merge waits on the top. This design lets a due main trace
wait longer while a train top runs, with a second bound. The setting
is `checks.main_trace_yields`, off by default.

**Correction (2026-11-19, 12:25Z).** The first version said the trace
starts "within a minute of a merge". That's true only when slots are
free. Of 48 main runs from 11-18 to 11-19 on the host below, the median
waited 165 s, but the top quarter waited 31 to 37 minutes: the
existing bound. So in busy hours this design is close to raising
`depends_minutes`, which is configuration and can be measured first.
See "A simpler first step".

## The evidence

On one project's host (4 check slots, 96 CPUs), 2026-11-18 12:00Z to
2026-11-19 10:45Z: 90 full-tier train-top runs (`VERDICT AHEAD`) and 46
main traces (`MAIN-GREEN` / `MAIN-RED`), each about 16 minutes. A top
counts as "with a trace" when a trace overlapped at least 30% of it.

| train top | runs | median | p75 |
| --- | --- | --- | --- |
| at the Merger's weight, no trace | 19 | 733 s | 1093 s |
| at the Merger's weight, with a trace | 22 | 880 s | 1074 s |
| at the default weight, no trace | 29 | 749 s | 1121 s |
| at the default weight, with a trace | 20 | 1176 s | 1433 s |

Traces start right after merges, and merges come in busy hours, so
part of the gap is load in general. Even so, a trace beside a top adds
2.5 to 7 minutes to the run the next merge waits on. The project gave
main's runs the Merger's weight (8000) on purpose: at the default
weight, main's browser steps timed out under load and the default
branch went red. So lowering the weight again is not the answer.

## What changes

1. **Waiting.** With `checks.main_trace_yields`, a due main trace is not
   started while any prediction is running (`VERDICT AHEAD` runs: a
   batch top, a later batch's top, a bypass top or a search's run),
   nor while the Merger's batch top waits unstarted for a slot. It is
   logged `MAIN-YIELD main <sha>: <n> train run(s) in progress` once
   per tip.
2. **The bound.** It waits at most `main_trace_yield_minutes` (default
   30) from when it became due. After that it starts as it does today.
   The bound keeps the default branch's green answer (and the
   main-red check) from waiting behind a host that is never idle.
3. **A newer tip replaces it.** If the default branch moves while the
   trace waits, the waiting trace is for the old tip and is replaced by
   the new one, as now. So a burst of merges costs one trace, not one
   per merge.
4. **Unchanged:** the trace's weight (`main_cpu_weight`), what it
   records, `trace_stale_only`, and the default branch's red check
   when the tip is not traced.

## The cost

Heads checked between the merge and the trace's verdict can't reuse
the new tip's records for up to `main_trace_yield_minutes` longer. They
can still reuse the previous tip's matched records. On the evidence
above, about two traces an hour, each overlapping a train top for most
of its run.

## A simpler first step

Raise `checks.depends_minutes` from 30 to 60 for a day and compare the
table above. In busy hours it has the same effect as this design. When
slots are free it costs nothing, since the trace starts at once. It also
delays the default branch's red check by up to 30 more minutes, which
is the cost this design's bound was meant to limit. The same setting
also caps traces at one every `depends_minutes` (`trace_matched_main`,
`match_main_unrecorded`), so 60 halves the traces too: less
contention, but the records later heads reuse are refreshed half as
often. If those costs are acceptable, this design isn't needed.

## Not in this design

* Leaving a slot free for a bypass train during a search (designs/0060).
  That is configuration (`merge.test_ahead.search_cuts`), measured
  separately.

## Testing

* Unit tests of the pick: a due trace waits while a prediction runs;
  starts after the bound; is replaced by a newer tip while it waits;
  starts at once with the setting off.
* On the project's host, compare the same table for a day with the
  setting on.
