# forge-stats

`forge-stats` reports how fast work lands, over a window of time: how
long build checks wait and run and how busy their slots are, how fast
the merge queue merges and why it hands pull requests back, how long
each merged pull request took from approval to merge, and how long each
author's approved work waited. It reads what `forge-pr-check`,
`forge-merger`, `forge-router` and `forge-nightly` already record on
the host, asks GitHub only for approvals (and, with `--actions`, for
Actions runs), and changes nothing. A project wants it to answer "are
we slower than last week, and where?" with numbers rather than a
feeling.

## Usage

    forge-stats [--since TIME] [--until TIME] [--hours H] [--json]
                [--no-github] [--actions] [--checkout DIR]

Run it by hand, or by an agent asked how things are going, on the host
that runs the build check and the Merger (their records are there):

    FORGE_CRON_CHECKOUT=~/widget-cron forge-stats --hours 6
    forge-stats --since 2026-10-12T09:00Z --until 2026-10-13T09:00Z --json

`forge-stats-watch` runs it hourly from cron, in its own process, to
judge the figures. There is no need to schedule `forge-stats` itself.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--since TIME` | `--until` less `--hours` | The start of the window. TIME is ISO 8601 in UTC, such as `2026-10-12T09:00Z`; an offset is honoured, and a time with none is UTC. |
| `--until TIME` | now | The end of the window. |
| `--hours H` | `24` | The window's length when `--since` is not given. May be a fraction. |
| `--json` | off | Print the figures as JSON rather than the text report. |
| `--no-github` | off | Ask GitHub nothing: leave out approval to merge, the authors and the Actions minutes. |
| `--actions` | off | Add the GitHub Actions minutes of the workflow runs created in the window. Ignored with `--no-github`. |
| `--checkout DIR` | `$FORGE_CRON_CHECKOUT`, else the current directory | Where the configuration is found. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--checkout`. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Find the configuration ([config.md, Finding it](../config.md#finding-it)). |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the root of the state directories it reads ([config.md, Where state lives](../config.md#where-state-lives)). |
| `FORGE_AGENT` | The App whose token reads GitHub. When it doesn't mint, `GH_TOKEN` is used. |
| `GH_TOKEN` | The token to read GitHub with when `FORGE_AGENT` gives none. With neither, it reads anonymously, which a public repository allows at a low rate. |
| `FORGE_GITHUB_API` | GitHub's API base (default `https://api.github.com`). |

## Configuration

It reads no section of its own. From the configuration it uses the
project's name (for the state directories), `repo`, `agents` (to name
an author by login or session), `checks.slots` (default 2; the fast
lane's slot is not counted) and whether there is a `nightly` section
([config.md, Keys](../config.md#keys)).

## What it does

**The build check**, from `pr-check/verdicts.log`:

* runs by kind: pull-request heads, the Merger's predictions (`AHEAD`)
  and the default branch; verdicts posted without a run (`MATCHED`);
  reds by step; runs red at once for a conflict;
* the predictions: how many a later head was matched to, and the
  slot-hours of those never used;
* the wait for a slot, the run time, and a pull-request head's wait
  and run together;
* the slots busy: the run time of runs that finished in the window,
  over `checks.slots` times the window. Near 100% means heads queue;
* the default branch's CPU seconds per run, and per step, from the
  check's `state.json` (`main_cpu`);
* the scoped steps' time, when some ran (`scoped_seconds`).

**The merge queue**, from `merger/merger.jsonl`:

* merges in the window and per hour, and the rate over only the hours
  the queue held an approved pull request, so a quiet spell can be told
  from a slow queue;
* for each merge, the time since its head last joined the queue;
* hand-backs by cause (conflict, red, approval, closing keywords),
  each pull request, head and cause counted once.

**Approval to merge**, from GitHub: for each merged pull request, from
the latest approval before its merge to the merge.

**Each author**: merges, approval to merge, and the hours in the window
she had at least one approved pull request not yet merged. Where
`forge-router` has recorded presence (`router/presence.jsonl`), also
the part of those hours she was idle.

**The nightly**, with a `nightly` section: `nightly_red_hours`, the
hours since the last green nightly ended while the newest is red, and 0
while it is green. It is read from `forge-nightly`'s state now, so it
is the figure now even for a window that ended earlier.

**GitHub Actions minutes** (`--actions`): every job of every attempt of
each workflow run created in the window, from the runs and jobs APIs;
the billing API is never read. A job's minutes are its run time rounded
up to a whole minute, as GitHub bills. A job that never started counts
zero, and one still running is counted apart. Self-hosted runners are
counted but not billed. The minutes are broken down by workflow, job,
event, conclusion, attempt, runner OS and who triggered the run. A
private repository also gets a total weighted by GitHub's macOS and
Windows rates.

The text report starts `forge-stats: <project>, <since> to <until>
(<hours> h)`, then sections `Build check`, `Merge queue`,
`Approval to merge`, `Authors`, `Nightly` and the Actions minutes,
each only when it has figures. With `--json`, the same figures are one
JSON object with keys `project`, `since`, `until`, `check`, `queue`,
`approval_to_merge`, `authors`, `actions` and `nightly`.

## State and logs

It writes no file (it may create the empty `pr-check/`, `merger/` and
`router/` directories when they are missing). It reads, under
`~/.local/state/agent-forge/<project>/`:

| File | Written by |
| --- | --- |
| `pr-check/verdicts.log`, `pr-check/state.json` | `forge-pr-check` |
| `merger/merger.jsonl` | `forge-merger` |
| `router/presence.jsonl` | `forge-router` |
| `nightly/nightly.json` | `forge-nightly` |

## Exit status

* **0**: the report was printed.
* **2**: no configuration (`NO-CONFIG: …`), or no records: neither
  `verdicts.log` nor `merger.jsonl` has anything, and `--actions` wasn't
  asked for (`NO-RECORDS: nothing in <dirs>`).

## See also

* [forge-stats-watch](forge-stats-watch.md), which judges these figures
  hourly.
* [forge-flow](forge-flow.md), a page of why merging is slow.
* [designs/0024](../designs/0024-a-watch-on-how-fast-work-lands.md).
