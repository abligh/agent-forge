# forge-stats-watch

`forge-stats-watch` watches how fast work lands and files a GitHub
issue when a figure goes out of line
([designs/0024](../designs/0024-a-watch-on-how-fast-work-lands.md)).
Each hour it takes `forge-stats`' figures (and three of `forge-flow`'s)
over the last few hours, compares each with a fixed limit or with the
week before, and opens one issue per figure that is out, for an agent
to look into. It comments while the figure stays out, and closes the
issue once the figure has been back in line for a whole window. It
only reports: it changes no slot, setting or queue. A project wants it
because a build check that is slowly getting slower, or a queue that
has quietly stopped merging, is noticed by nobody until it hurts.

## Usage

    forge-stats-watch [--dry-run]

Run it hourly from cron, through `forge-cron-run`, on the host that runs
the build check, a few minutes past the hour:

    7 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-stats-watch

It mints its own token, so the job needs no `FORGE_CRON_APP`. Run it by
hand with `--dry-run` to see each figure's verdict and what it would
file.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--dry-run` | off | Print each verdict and what it would do, and change nothing, on GitHub or in its state. Lines that would change something start `WOULD`. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The checkout whose configuration it reads. Without it, the configuration is found as every tool finds it. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Find the configuration ([config.md, Finding it](../config.md#finding-it)). |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the root of the state directories it reads and writes ([config.md, Where state lives](../config.md#where-state-lives)). |
| `FORGE_GITHUB_API` | GitHub's API base (default `https://api.github.com`). |

## Configuration

It reads the `stats_watch` section ([config.md, Keys](../config.md#keys)
lists every key, figure and default). Without it, it prints
`forge-stats-watch: no stats_watch section; nothing is watched` and
exits 0. A figure left out of `figures` is not watched. An example:

    "stats_watch": {
      "owner": "LeadDev",
      "figures": {
        "check_run": {"limit": 40, "ratio": 1.5},
        "merge_rate": {"limit": 2, "short_hours": 3},
        "main_red": {},
        "nightly_red_hours": {"limit": 12, "owner": "Tester"}
      },
      "notify": ["Tester"],
      "notify_rooms": ["ops@conference.example.org"]
    }

It also reads `labels.owner_prefix` (the `Owner:` label), `merge.merger`
(the default for `stats_watch.as`), `checks.slots` and the reviewers
among `agents`, for the `forge-flow` figures, the `relay` section, for
`notify` and `notify_rooms`, and what `forge-stats` reads.

## What it does

**The figures.** It runs `forge-stats --json --no-github` in its own
process twice: over the last `window_hours` (default 6), and over the
`baseline_days` (default 7) before that window. When a `forge-flow`
figure is watched, it reads those from the same state directory too.
No watched figure comes from GitHub.

| Figure | Out of line when |
| --- | --- |
| `check_run` | The build check's run time, median, in minutes, is over `limit` or over `ratio` times the baseline's. |
| `check_wait` | The wait for a slot, p90, in minutes: the same. |
| `pr_turnaround` | A pull-request head's wait and run, median, in minutes: the same. |
| `merge_time` | Joined the queue to merged, median, in minutes: the same. |
| `merge_rate` | Merges an hour while the queue held an approved pull request are under `limit`, or under the baseline's over `ratio`. Judged only when the queue held one for an hour or more. |
| `slots_busy` | The slots busy, in percent, are over `limit`. |
| `main_red` | Any red build check of the default branch. |
| `nightly_red_hours` | The hours since the last green nightly ended are over `limit`, or, without one, above 0. |
| `main_cpu` | A default-branch run's CPU seconds, median (or one `step`'s), are over `limit` or over `ratio` times the baseline's. |
| `hand_backs_per_merge` | Hand-backs per merge are over `limit` or over `ratio` times the baseline's. |
| `prs_per_slot` | Pull requests merged per green run are under `limit`, or under the baseline's over `ratio`. |
| `reviewer_busy` | The busiest reviewer's share of the window busy, in percent, is over `limit`. |

**A shorter window as well.** A figure with `short_hours` (from 1 to
under `window_hours`) is also judged over its last `short_hours`,
against the same baseline, and is out of line when either window is,
so a dip of a few hours shows before it moves the whole window. Its
why then starts `over the last <n> h`. `merge_rate`'s hours are still
only those the queue held work, so a quiet night is not a dip. The
issue still closes only after a whole `window_hours` in line.

A figure from fewer than `min_samples` (default 5) samples is not
judged, nor a `ratio` against a baseline that thin. `main_red`,
`nightly_red_hours` and `reviewer_busy` don't use `min_samples`. For
`merge_rate` the samples are the approved pull requests that waited.
Each verdict is printed: `OUT <figure>: …`, `IN <figure>: …` or
`UNJUDGED <figure>: …`.

**The issues.** One open issue per figure:

* **Out, no issue:** it opens one, titled
  `forge-stats-watch: <figure> <why>`, labelled `stats_watch.label`
  (default `stats-watch`) and `Owner:<owner>` (the figure's own
  `owner`, else the watch's). It gives the window's and the baseline's
  figures, the rule that tripped, and the `forge-stats` command to see
  more (`OPEN <figure>: …`). Then it tells each of `notify` and
  `notify_rooms` through the relay, in one line naming the figure, the
  why and the issue (`NOTIFIED <kind> <address> of #N`). One the relay
  doesn't take is reported on standard error and tried again at the
  next run while the issue stays open.
* **Out, issue open:** a comment with the new figures every
  `remind_hours` (default 6) (`REMIND #N <figure>: …`).
* **In line, issue open:** once the figure has been in line at every
  run for a whole window, a comment saying so, and the issue is closed
  (`CLOSE #N …`). Until then, `IN LINE #N <figure> since <time>`. A run
  that couldn't judge the figure restarts that count.

**Context for slow checks.** For `check_run`, `check_wait`,
`pr_turnaround` and `main_cpu`, the issue and its reminders also say
how busy the build check's slots were over the window, and the host's
15-minute load average over it (median and highest of the samples
this watch takes at each run) beside its CPUs. A check that is slow
because the host is busy then reads as contention, not heavier tests.

An issue is found again by its label and its `Figure: <name>` line, so
one an earlier run filed, or one filed by hand, is found even without
the state file. Everything is written as `stats_watch.as` (default
`merge.merger`, else `Merger`).

A failure to reach GitHub skips the hour and says so on standard error
(`forge-stats-watch: skipping this hour: …`); nothing is saved, so the
next hour tries again. A refused write is reported as
`forge-stats-watch: <method> <path> refused: HTTP <status> …`.

## State and logs

`~/.local/state/agent-forge/<project>/stats-watch/state.json` holds,
for each figure, its issue, when it was opened and last commented on,
since when it has been in line, and who of `notify` is still to be
told; and the load average sampled at each run over the last window.
A figure no longer watched is dropped from it. `--dry-run` writes nothing.

It reads the state directories `forge-stats` and `forge-flow` read
(`pr-check/`, `merger/`, `router/`, `nightly/`). From cron, its output
is in `~/cron-logs/forge-stats-watch.log`.

## Exit status

* **0**: done, including when figures are out of line (the issues say
  so), and without a `stats_watch` section.
* **2**: no configuration; `forge-stats` or `forge-flow` failed; no
  token; or GitHub could not be read or refused a write.

## See also

* [forge-stats](forge-stats.md) and [forge-flow](forge-flow.md), where
  the figures come from.
* [forge-nightly](forge-nightly.md), for `nightly_red_hours`.
* [designs/0024](../designs/0024-a-watch-on-how-fast-work-lands.md).
