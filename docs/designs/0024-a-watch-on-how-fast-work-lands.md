# 0024: A watch on how fast work lands

* Status: approved (Alex Bligh, 2026-10-21, in the terminal; https://github.com/abligh/agent-forge/issues/40)
* Author: XMPPEngineer
* Date: 2026-10-21

## Summary

`forge-stats` says how fast work lands: how long the build check runs
and waits for a slot, how long a pull request takes from joining the
merge queue to merging, and how many merge an hour. Nobody reads it
unless the owner asks, so a slowdown is found by the owner noticing
idle agents. The owner's words, after one such morning:

> We should monitor these things and file bugs if median merge time or
> CI rate deviate. This should not be on me to monitor all the time.

This design adds `forge-stats-watch`. It runs from cron every hour, and
compares the last few hours' figures with the project's own recent
past and with fixed limits from the configuration. When a figure is out
of line, it opens an issue in the project's repository, assigned to a
named agent, with the figures that tripped it. It comments on that
issue while the figure stays out, and closes it once the figure is back
in line for a whole window.

## Evidence

On 2026-10-21 the build check ran in a median of 7.3 minutes, with the
slots 73% busy. Agents were nonetheless idle for most of a morning.
Each waited on its own gate runs on the shared build host, and those
ran one at a time with five-minute gaps between them, while the host's
load was 5 to 9 on 32 threads. The owner found it by looking at the
agents, not at a figure. The same day, the build check was made the
gate before merge in place of agents' full local gate runs. That makes
the check's capacity matter more, and that capacity is exactly what
this watch measures.

## Design

### What it measures

Each hour, `forge-stats-watch` takes `forge-stats`' figures over the
last `window_hours` (default 6) and over the `baseline_days` (default
7) before that window. These are the watched figures:

| Figure | From `forge-stats` | Out of line when |
|---|---|---|
| `check_run` | build check run time, median | over `limit` minutes, or over `ratio` × the baseline's median |
| `check_wait` | wait for a slot, p90 | over `limit` minutes, or over `ratio` × baseline |
| `pr_turnaround` | pull-request head, waiting and run, median | over `limit` minutes, or over `ratio` × baseline |
| `merge_time` | joined the queue to merged, median | over `limit` minutes, or over `ratio` × baseline |
| `merge_rate` | merges an hour | under `ratio`⁻¹ × baseline, when the window had approved pull requests waiting |
| `slots_busy` | slots busy | over `limit` percent |
| `main_red` | red build checks of `main` | any |

A figure computed from fewer than `min_samples` (default 5) samples is
not judged, so a quiet night does not trip `merge_rate` or anything
else. `merge_rate` is judged only when pull requests were approved and
waiting during the window, since a low rate with nothing to merge is
not a fault.

### Filing and closing

There is one open issue per figure at most, titled `forge-stats-watch:
<figure> <out of line text>` and labelled with `label` (default
`stats-watch`). It is found again by that label and the figure's name
in its body. When a figure goes out of line, the issue is opened and
assigned to `owner`. Its body gives the window's and the baseline's
figures, the limit that tripped, and the `forge-stats` command to see
more. While the figure stays out, the issue gets one comment a
`remind_hours` (default 6) with the new figures. After a whole window
back in line, it is closed with a comment saying so. The issue is
filed with the App of the agent named by `as` (default the Merger's),
through `forge-app-token`, so it needs no personal token.

The watch only reports. It changes no slot, setting or queue: deciding
what a slowdown means is the owner's work, and `owner` is who does it.

### Running

`forge-stats-watch [--dry-run]` prints what it would file and changes
nothing. It keeps its state (open issue numbers, the last comment's
time) in `<state>/stats-watch/state.json`. A failure to reach GitHub
skips that hour and says so on stderr. It runs from the host's cron
through `forge-cron-run`, like the Merger, on the host that runs the
build check, whose logs `forge-stats` reads.

## Configuration

A new optional section, inert until present:

```json
"stats_watch": {
  "owner": "Engineer",
  "window_hours": 6,
  "baseline_days": 7,
  "figures": {
    "check_run":     {"limit": 12, "ratio": 1.5},
    "check_wait":    {"limit": 20, "ratio": 2},
    "pr_turnaround": {"limit": 20, "ratio": 1.5},
    "merge_time":    {"limit": 45, "ratio": 2},
    "merge_rate":    {"ratio": 2},
    "slots_busy":    {"limit": 90},
    "main_red":      {}
  }
}
```

A figure left out of `figures` is not watched. `limit` and `ratio` are
each optional, and a figure with neither trips only on its own rule
(`main_red`). `owner` must be one of `agents`. Also allowed: `label`,
`remind_hours`, `min_samples` and `as`.

## Limits

- It judges medians and p90s over hours, so a single slow run never
  trips it, and a slowdown shows within about an hour of becoming
  typical, not at once.
- The baseline is the project's own recent past. A slow week becomes
  the norm for `ratio`, which is why each figure that has a natural
  ceiling also takes a fixed `limit`.
- It measures the build check and the merge queue, not an agent's own
  checks on its own host. Those are the agent's to run at once, and
  they show here only as pull requests that arrive late.

## Testing

Unit tests drive it with `forge-stats`' JSON for chosen windows and a
fake GitHub. They cover each figure tripping on its limit and on its
ratio, too few samples, `merge_rate` with nothing waiting, opening,
reminding, closing after a clean window, finding an issue filed by an
earlier run, a GitHub failure, and `--dry-run`. The configuration's
checks have tests of their own.

## Rollout

1. Implement and release it.
2. A project pins that release and adds `stats_watch` to its
   configuration (its code owner's review), plus the cron line on the
   build-check host.
3. Run `--dry-run` against the last week first, so that the first live
   issue is not an old one.

## Amendment (2026-10-26): a figure's own owner, and a floor on merges an hour

* Status: approved (Alex Bligh, 2026-10-26, in the terminal: "#134 approved"; https://github.com/abligh/agent-forge/issues/135)

**Why.** On 26 October the queue-to-merge figure was out of line all
day, but every issue went to the watch's one owner. That owner looks
after the build check, not the Merger, and read the issues as load.
Merges an hour stayed at 5.2 to 6.2 all afternoon, against about 7 the
night before. With `ratio` 2 against the baseline, that counted as in
line.

**Change.**
1. Any figure may name its own `owner`, one of `agents`. Its issue is
   labelled for that agent and says it's theirs to decide. A figure
   without one keeps the watch's `owner`.
2. `merge_rate` takes a `limit`, a floor: it's out of line under it,
   judged with or without a baseline, and still only when approved
   pull requests waited.

## Second amendment (2026-10-30): merges an hour while there is work to merge

* Status: approved (Alex Bligh, 2026-10-30, in the terminal: "agent-forge #172 - yes!"; https://github.com/abligh/agent-forge/issues/172)

**Why.** `merge_rate` is judged only when approved pull requests
waited, so that a low rate with nothing to merge is not reported as a
slow queue. But `forge-stats` counts as waiting every pull request
that was in the queue at any tick of the window, and every merged one
was, so any merge made the rate judged. On 30 October a 6 h window had
32 merges, 10, 11 and 7 an hour in its first three hours, then 3, 3
and 0 as authors went idle, the queue empty from the fifth hour. The
watch reported 5.3 an hour, under the limit of 6. The queue had held
approved work for 2.7 h of the 6, and merged 11.8 an hour while it
did.

**Change.**
1. `forge-stats` also reports, for the merge queue, how long the
   queue held at least one approved pull request in the window
   (`held_seconds`): from each tick of the Merger to the next, the
   queue is as the first saw it, and the last tick's state lasts to
   the window's end, so a Merger that stops ticking while work waits
   counts as slow, not idle. The report gives merges an hour over
   that time beside merges an hour over the window.
2. `merge_rate` is merges over those hours, for the window and the
   baseline alike, and is not judged when the queue held work for less
   than an hour. `limit` and `ratio` apply to it as before.

## Third amendment (2026-10-31): what a run costs in CPU

* Status: approved (Alex Bligh, 2026-10-31, in the terminal: "yes please"; https://github.com/abligh/agent-forge/issues/175)

**Why.** The figures the watch judges are all times and counts, and
every one of them is moved by how busy the host is. None says what a
run costs. A project's tests can grow until every check pays several
times what it did, and nothing reports it until the checks are slow
for everyone, by which time the cost is spread over dozens of tests.
In one project a default-branch run's CPU time went from a median of
1,964 s to 6,151 s in eight days, and its slowest step from 1,430 s
to 3,857 s, while the check-time figures blamed the host's load. The
check already records each step's CPU seconds (`step_cpu`, beside
`step_seconds`, in its state's `results`); nothing read them.

**Change.**
1. `forge-stats` reports the CPU time of the default branch's runs
   that finished in the window (`check.main_cpu` in the JSON): each
   run's total over its steps, and each step's, as `{n, median, p90,
   max}`, read from the check's `state.json`. The default branch's
   runs are the steady series: a pull request's run may test only
   what it changed, so its CPU varies with the change. The report
   gives the median, the p90 and the heaviest steps.
2. A new figure, `main_cpu`: a default-branch run's CPU seconds,
   median, over all its steps or, with `step`, that step's. It is
   out of line over `limit` (CPU seconds) or over `ratio` times the
   baseline's, with `min_samples` as for the other figures. CPU time
   is less moved by the host's load than run time, if not unmoved:
   cache and memory contention still inflate it, which is why the
   rule is a ratio over a week's baseline and not a fine threshold.
3. The issue names the step in its rules.

**What it doesn't do.** It says that the cost grew, not which test
grew it; a project's own per-test ledger, if it has one, says that.
It changes no gate: like every figure, it only reports.
