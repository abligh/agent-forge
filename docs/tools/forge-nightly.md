# forge-nightly

`forge-nightly` treats a red nightly workflow run as a red default
branch ([designs/0028](../designs/0028-a-red-nightly-is-a-red-main.md)).
While the nightly is red it keeps one GitHub issue open, owned by
whoever owns the failed steps. It tells the build check, the Merger and
the router which open pull requests fix it, so they go first. It runs
the nightly again by itself, after a fix merges or after a few hours,
until it is green. And if it stays red, it messages the developers and
then the project's owner. A project wants it because a nightly that
fails in the small hours is otherwise noticed late, by nobody in
particular, and fixed slowly.

## Usage

    forge-nightly [--dry-run]

Run it every five minutes from cron, through `forge-cron-run`, on the
host that runs the build check, the Merger and the router (they read its
state on that host):

    */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-nightly

It mints its own token, so the job needs no `FORGE_CRON_APP`. Run it by
hand with `--dry-run` to see what a tick would do.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--dry-run` | off | Print what it would do and change nothing: nothing on GitHub, in its state or on the relay. Lines that would change something start `WOULD`. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The checkout whose configuration it reads. Without it, the configuration is found as every tool finds it. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Find the configuration ([config.md, Finding it](../config.md#finding-it)). |
| `FORGE_ROUTER_RELAY` | The relay's base URL for escalations, overriding `relay.url`. |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the root of the state directory ([config.md, Where state lives](../config.md#where-state-lives)). |
| `FORGE_GITHUB_API` | GitHub's API base (default `https://api.github.com`). |

## Configuration

It reads the `nightly` section ([config.md, Keys](../config.md#keys)
lists every key and default). Without the section it prints
`forge-nightly: no nightly section; nothing is watched` and exits 0. A
minimal one:

    "nightly": {
      "workflow": "nightly.yaml",
      "job": "build",
      "owners": {"test-*": "LeadDev", "docs": "Writer"}
    }

`workflow` (the workflow's file name) and `job` (the job that decides
green or red, or a matrix's `job (...)` jobs) are required. A step the
`owners` names and globs don't cover is owned by `router.owners.triage`
([router.owners](../config.md#routerowners)), which must be set.

It also reads `repo`, `default_branch`, `owner`, `agents`,
`labels.owner_prefix`, `merge.merger` (the default for `nightly.as`)
and the `relay` section (`url`, `token_file`), for escalations.

The App it acts as (`nightly.as`) needs Actions write (to dispatch the
workflow), Issues and Pull requests.

## What it does

Each tick, under its own lock:

**Judges the newest run.** It reads the newest runs of
`nightly.workflow` on the default branch that were started by
`schedule` or `workflow_dispatch`, and judges the newest completed one
not yet judged, by its `nightly.job` jobs:

* **green** when each succeeded (`GREEN run <id> at <sha>`);
* **red** when one failed, timed out or failed to start
  (`RED run <id> at <sha>: <failed steps>`);
* **neither** otherwise, such as cancelled (`NEITHER run …`).

A run in which every `nightly.release_jobs` job succeeded published its
builds, red or green, and is recorded as `published`
(`PUBLISHED run …` when red). Without `release_jobs`, only a green run
counts. The router's `router.nightly_wake` reads this
([router.nightly_wake](../config.md#routernightly_wake)).

**Keeps one issue.** A red run with no issue open opens one, titled
`The nightly is red at <sha>` (`OPEN …`). It is labelled
`nightly.labels` and `Owner:<agent>` for each owner of a failed step.
For each failed step it gives the name, the owner and the end of its
output (colour codes removed, at most 12,000 characters a step), then
the commits since the last green nightly. Each later red run comments
once with the same detail and adds any new owners' labels
(`COMMENT #N: red again`). If the issue was closed meanwhile, which a
fix's merge does, it is reopened (`REOPEN #N`). The first green run
comments and closes it (`CLOSE #N: green at <sha>`). The issue carries
a `Nightly: <workflow>` line, by which it is found again among the open
issues its App opened, even without the state file.

**Puts fixes first.** While the issue is open, each tick lists the open
pull requests that close it (`FIXES #N: #12, #14`) in its state. A fix
is one with a closing keyword for the issue in its title, body or a
commit message, or the issue in its closing references. With
`nightly.fixes_named`, a pull request that merely names the issue, or
closes an issue linked with it, counts too. `forge-pr-check`,
`forge-merger` and `forge-router` read the list and treat those pull
requests as if they carried `labels.urgent`. Only the 100 oldest open
pull requests are read.

**Runs it again.** While the nightly is red and no run is queued or in
progress (`RUNNING run …`), it dispatches the workflow on the default
branch when a fix merged after the newest run started, or when
`nightly.retry_hours` (default 3) have passed since that run ended
(`DISPATCH …`). Each dispatch is a comment on the issue saying the
commit and why. A dispatch counts as running until a newer run is
seen, or for 30 minutes (`DISPATCHED at …; its run isn't seen yet`).

When the workflow file on the default branch declares a
`workflow_dispatch` input named `first`, the dispatch passes the last
red run's failed steps there, by name, comma-separated, so a retry can
run them first. Otherwise it is dispatched without inputs.

With `nightly.defer_until`, each dispatch comment ends with a
`Defer-until:` line at the run's expected end: the median length of
the last five completed runs, rounded up to the quarter hour. The
router's red-build rules hold the issue until then, or until a red run
ends.

**Escalates.** While the nightly stays red, at the first of
`nightly.escalate_hours` (default 24) after the issue opened it
messages every developer agent, and at the second (default 48) the
project's owner, at `nightly.tell_owner`. Messages go through the
relay as the Merger's do (`ESCALATE #N after 24 h: …`). Each is also a
comment on the issue, the owner's mentioning her. A message nobody
took is tried again next tick (`NOT SENT escalation …`) and not
commented on.

Other lines: `BUSY: another tick holds the lock` (exit 0), and on
standard error `NO-CONFIG: …`, `forge-nightly: skipping this tick: …`
and `forge-nightly: <method> <path> refused: HTTP <status> …`.

## State and logs

`~/.local/state/agent-forge/<project>/nightly/nightly.json` holds the
issue and whether it is open, the last run judged, the last green and
red runs (each with its start and end), the newest verdict, when the
nightly went red, the last run that published its builds, the fixes,
the dispatch and the escalations done. `lock` beside it is the tick's
lock. `--dry-run` writes nothing.

Other tools read the file on the same host: `forge-pr-check`,
`forge-merger` and `forge-router` for the fixes; `forge-router` for
`published`; `forge-stats` for `nightly_red_hours`. The fixes are
ignored once the file is stale.

From cron, its output is in `~/cron-logs/forge-nightly.log`.

## Exit status

* **0**: the tick is done, including while the nightly is red (the issue
  says so), when another tick holds the lock, and without a `nightly`
  section.
* **2**: no configuration; no token; or GitHub could not be read or
  refused a write.

## See also

* [designs/0028](../designs/0028-a-red-nightly-is-a-red-main.md) and
  [designs/0048](../designs/0048-nightly-steps-run-on-the-pull-requests-that-can-break-them.md).
* [forge-router](forge-router.md), [forge-stats](forge-stats.md) and
  [forge-stats-watch](forge-stats-watch.md) (`nightly_red_hours`).
* [forge-cron-run](forge-cron-run.md).
