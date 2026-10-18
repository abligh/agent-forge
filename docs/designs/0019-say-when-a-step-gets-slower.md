# 0019: Say when a check step gets slower

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-18

## Summary

A step of the build check can get several times slower and nobody is
told. forge-pr-check records each step's time in every verdict
(`step_seconds`), but nothing reads it except the fast lane's costs.
This design has forge-pr-check open an issue when a step's recent
median has grown well past its usual one, naming the step, the two
medians and the default-branch commits in which the time jumped, so the
project's owner of the check can act the day it happens.

## Evidence

On one project, the browser step's median, from the verdicts:

| Days | Median |
|---|---|
| Three days before | 62–65 s |
| The next day | 71 s |
| The day after | 362 s, reaching about 870 s by evening |
| The fourth day | 1339 s, on an overloaded host |

Three pull requests in two days each added a whole-corpus comparison to
the same step. A full check went from about 7 minutes to 25–30. Merges
are serial behind a check, so they fell from 6–7 an hour to 1–3. Every
one of those runs was logged, but the change was found two days later,
by someone asking why merges were slow.

## Design

A new `checks.step_alert`, off unless configured:

```json
"step_alert": {"growth": 1.5, "min_seconds": 30, "window_days": 7,
               "labels": ["bug"], "owner": "BuildEngineer"}
```

**What counts as a step's time.** Only runs of the step itself count.
A verdict that matched or reused the step (designs/0009, 0018), or
skipped it by selection (designs/0005), says nothing about its cost. The
default branch's own runs are the steadiest sample, since they run every
step. A pull request's runs are counted too, because on a project that
checks main only now and then they are most of the sample.

**When it fires.** After each verdict, for each step with at least
`min_runs` (default 10) runs in both periods:

* `recent`: the median of the step's runs in the last 24 hours;
* `usual`: the median over the `window_days` before that.

It fires when `recent > growth × usual` and
`recent − usual ≥ min_seconds`. The absolute floor keeps a 4 s step
taking 7 s from raising an issue.

**What it says.** One issue per step is open at a time, written by
`checks.poster` as `main_red_issue` is, with `labels`, plus
`Owner:<owner>` when `owner` is set. It gives:

* the two medians and the run counts;
* the first default-branch run where the step was over the threshold,
  and the last one under it;
* the commits merged between those two, with their pull requests. That
  is where to look.

A step that stays slow comments again at most once a day, with fresh
medians. When `recent` falls back under the threshold, it comments and
closes the issue. The new level is then the usual one: a step a project
chose to make slower isn't reported for ever.

**What it doesn't do.** It holds nothing: the gate and the Merger are
unchanged. It doesn't blame a host: on a host under load every step
slows together, so when most steps (more than half) cross the
threshold at once, it opens one issue about the host instead, naming
the load and every step's ratio.

## Cost

One pass over the verdicts kept, after each verdict: the same data
`step_costs` already reads. No runs, and nothing written unless it
fires.

## Alternatives

* **A fixed budget per step** (`max_seconds`). That's simpler, but it
  needs a number per step and someone to keep it up to date as the
  project grows, and nobody does. Growth against the step's own history
  needs no upkeep.
* **A weekly report.** That's too late: the evidence above cost two days
  of merges.
