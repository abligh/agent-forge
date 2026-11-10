# forge-pr-check

`forge-pr-check` is the build check. It builds and tests each open
pull request's head on this host, under a memory limit, and posts the
verdict as the commit status the default branch's protection requires
(`checks.context`, say `vm/build-and-test`). The Merger merges only on
that status, posted by the App it trusts. A project wants it so that
every head is tested with the project's own steps on real hardware,
not only on the author's machine or on GitHub Actions, and so that
agents can push without running the whole check locally. It also
tests the Merger's predicted commits, so that a merge doesn't wait for
a run.

## Usage

    forge-pr-check [--post] [--instance NAME] [--checkout DIR]
                   [--state-dir DIR] [--merger-dir DIR]
    forge-pr-check --worker SHA --pr N [--slot K] [--post]
                   [--ahead | --main [--warm]] [...]

Without `--worker` it is a **tick**: it queues the open heads, posts
`pending` on new ones, and starts a detached worker for each head it
can, then returns at once. A **worker** is the check itself. Ticks
start workers; a worker can also be run by hand.

A tick runs from cron, through [forge-cron-run](forge-cron-run.md), on
the build-check host:

    * * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-pr-check --post

and on `pull_request` events, by [forge-receiver](forge-receiver.md):

    "ticks": {"pull_request": [["forge-pr-check", "--post"]]}

A posting worker starts the next tick through `forge-cron-run` when it
lets its slot go, so a head that arrived meanwhile doesn't wait for
cron. A tick with nothing to start ends that chain.

A second instance on another host (a macOS host, say) runs with its
own name:

    * * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-pr-check --post --instance mac

By hand, to check one head without posting:

    forge-pr-check --checkout ~/widget-cron --worker <sha> --pr 7

It needs `git` (2.38 or later), `gh`, `openssl`, `systemd-run --user`
for the memory limit and CPU weight, and, with
[`checks.depends`](../config.md#checks), a C compiler (`preload`) or
`strace`.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--post` | off | Post each verdict as the commit status, as `checks.poster`'s App. Without it nothing is written to GitHub. Only a posting tick acts on `labels.rerun`, `checks.step_alert`, `checks.main_red_issue` and `checks.red_comment`. |
| `--worker SHA` | none: a tick | Check SHA now, in this process. Needs `--pr`. |
| `--pr N` | none | With `--worker`: the pull request SHA heads. `0` for the default branch's head (the default with `--main`). |
| `--slot K` | `1` | With `--worker`: the slot to run in. `0` is the fast lane. |
| `--ahead` | off | With `--worker`: SHA is the Merger's predicted commit for pull request `--pr` (`merge.test_ahead`), checked and recorded but never posted. |
| `--main` | off | With `--worker`: SHA is the default branch's head, run with every step (under the tracer with `checks.depends`, writing its dependency record). |
| `--warm` | off | With `--worker`; implies `--main`. Build SHA again in `--slot` only to bring the slot's caches up to it (`checks.warm`): untraced, posted nowhere, kept apart from its verdict. |
| `--state-dir DIR` | `~/.local/state/agent-forge/<project>/pr-check` (`pr-check-<name>` for an instance) | Where the queue, the verdicts and the logs live. Passed on to workers and chained ticks. |
| `--merger-dir DIR` | `~/.local/state/agent-forge/<project>/merger` | The Merger's state directory: its `next.json` names the pull request to check first and its `ahead.json` lists its predictions. Read with `checks.merger_first`, `merge.test_ahead`, `checks.merger_cpu_weight` or `checks.docker_slices`. |
| `--instance NAME` | `$FORGE_PR_CHECK_INSTANCE`, else none | Run `checks.instances.NAME`, with its own context, steps, host and state directory. An unknown name is refused (`NO-INSTANCE`, exit 2). |
| `--checkout DIR` | `$FORGE_CRON_CHECKOUT` | The cron checkout, kept at the default branch. The steps and configuration come from it. With neither, `NO-REPO`, exit 2. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--checkout`, and passed to chained ticks. |
| `FORGE_PR_CHECK_INSTANCE` | The default for `--instance`. |
| `FORGE_PR_CHECK_SLOTS` | Overrides `checks.slots` (a whole number, at least 1). |
| `FORGE_PR_CHECK_MEMORY_MAX` | Overrides `checks.memory_max`; `off` or empty for no limit. |
| `FORGE_QUIET_DIR` | Overrides `quiet.dir`, for a host where it can't exist. |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Where state directories live ([Where state lives](../config.md#where-state-lives)). |
| `FORGE_GITHUB_API` | The GitHub API base used to mint the poster's token. |
| `FORGE_INSTALL_LOCK_FD` | Set by `forge-cron-run`: the installation's lock, handed on to each worker so a run never spans two releases. |

A step gets the host's environment less `GH_TOKEN`, `GITHUB_TOKEN`,
every `FORGE_` variable and `checks.drop_env`, plus the variables in
[What every step gets](../config.md#what-every-step-gets) and
[What `depends` records and passes on](../config.md#what-depends-records-and-passes-on).

## Configuration

* **[`checks`](../config.md#checks)**: every key in its table, and
  [`checks.instances`](../config.md#checksinstances) for `--instance`.
  The longer topics have sections of their own:
  [The fast lane](../config.md#the-fast-lane),
  [Pruning the build caches](../config.md#pruning-the-build-caches),
  [Warming a slot's caches](../config.md#warming-a-slots-caches),
  [Checking the merge with the default branch](../config.md#checking-the-merge-with-the-default-branch),
  [Reusing a step's verdict](../config.md#reusing-a-steps-verdict) and
  [Keeping the records fresh](../config.md#keeping-the-records-fresh).
* **`merge`**: `merger` (the default poster) and
  [`merge.test_ahead`](../config.md#mergetest_ahead)
  (`max_age_hours`, `spare_slots_only`; it turns on `merger_first`).
* **Keys**: `repo`, `default_branch`, `host` (named in descriptions),
  `agents` (reviewers, for `approved_first`; names for
  `first_authors`), `machinery`, `key_dir` ([Keys](../config.md#keys)).
* **`labels`**: `urgent`, `rerun` and `owner_prefix`.
* **`quiet`**: `dir`, read for live holds.
* **`nightly`**: through `forge-nightly`'s state, which pull requests
  fix a red nightly.

The poster's App needs *Commit statuses: write*, and *Issues: write*
for `labels.rerun`, `main_red_issue`, `step_alert` and `red_comment`.

## What it does

### Each tick

1. Lists the open pull requests as the poster's App. Drafts are not
   checked, and heads from another repository are refused (`REFUSED`,
   posted `error`): the steps run the pull request's code with this
   user's access to the host. An instance with `pull_requests` drops
   the pull requests it doesn't apply to (`OUT-OF-SCOPE`).
2. Under the state lock: handles `labels.rerun`, queues new heads
   (posting `pending`, `queued at HH:MMZ for a check slot; N other
   heads waiting`), queues the default branch's head when it needs a
   run (`QUEUED main <sha>`), posts again any verdict that never
   reached GitHub, and prunes old verdicts, results and logs.
3. Stops any run of a head that is no longer its pull request's
   (`SUPERSEDED`: SIGTERM to the worker's process group), a run its
   batch's green top now covers (`STOPPED-COVERED`), and a prediction
   longer than a red one (`STOPPED-AHEAD`). Prints `RUNNING` or `BUSY`
   for each held slot.
4. Matches, without a slot, each head whose tree already has a green
   verdict here that ran every step it would (`MATCHED`, `COVERED`).
5. Starts nothing while a quiet hold is live (`QUIET:`).
6. Otherwise starts heads in the free slots, in priority order, until
   no slot is free or nothing can start (`STARTED`, `STARTED-AHEAD`,
   `STARTING`, `IDLE`).
7. With `step_alert` and `--post`, judges whether any step got slower.

### The order heads start in

A head waits from when a tick first sees it. With no settings, heads
start oldest first. The full order, as each setting adds to it:

1. the default branch's traced run when it is due
   (`depends_minutes`, or `main: "newest"`, or a stale record with
   `fresh`);
2. urgent heads: `labels.urgent`, or a fix of a red nightly or of the
   red default branch (`main_red_issue`);
3. with `approved_first`, heads of pull requests a reviewer agent has
   approved: the Merger's own first, then those in its `ahead.json`,
   then the rest, and last those its `next.json` lists as `blocked`
   (held until another pull request merges);
4. with `max_wait_minutes`, heads that have waited that long;
5. with `first_authors`, those agents' heads;
6. with `merger_first` (or `test_ahead`), a head of the pull request in
   the Merger's `next.json`;
7. with `test_ahead`, the Merger's predictions in `ahead.json`, then
   later batches' tops in spare slots;
8. the rest, oldest first.

While a head of the Merger's own pull request waits, overdue heads and
`first_authors` go behind it, and no prediction goes before it.
`spare_slots_only` runs predictions only when no ordinary head waits.
A head that failed to prepare always goes behind. It only orders: a
free slot is never kept empty. `STARTED` lines end with why a head
went first (`, first: the Merger waits on it`, `, first: it is
approved`, `, first: it has waited N minutes`). The
[`checks`](../config.md#checks) table gives each key's detail.

### A run

A worker takes its slot's lock (`worker.lock`, `worker-N.lock`,
`worker-lane.lock`), whose one line names the pull request, head,
start and pid. It prepares the slot's own worktree (`wt`, `wt-N`,
`wt-lane`): a clean, detached checkout of the head (`git clean -ffdx`,
less `keep_ignored`), merged with the default branch's tip under
`merge_default`. Then it runs the steps:

* The steps are the cron checkout's `checks.steps`, so a pull request
  can't change what it is judged by. A step with `only_if` runs only
  when the head has that file. Each step runs even if an earlier one
  failed; none is retried.
* Each step runs in its own `systemd-run --user --scope` with
  `MemoryMax` (`memory_max`, default `16G`), no swap,
  `OOMPolicy=continue` and `CPUWeight` (`cpu_weight`, default 1000;
  `merger_cpu_weight` for the run the next merge waits on). A worker
  that can't start a scope logs `UNCAPPED` and runs without it.
  With `cpu_quota`, a slot's scopes share one user slice whose
  `CPUQuota` is that many CPUs, or the run's `CHECK_CPUS` share with
  `"share"`, set as each step starts; `UNCAPPED-CPU` if systemd refuses.
* A step is stopped after `step_timeout_minutes` (default 30).
* A step with `env_file` gets that file's `KEY=VALUE` lines in its
  environment: secrets such as a test account's tokens, which the
  check can't take from CI. The file must be the check user's and mode
  0600; otherwise the step is red, not run, and says why. A missing
  file is logged and the step runs without it. No value is written to
  a log, comment or state.
* Each slot has its own build caches (`caches`), and its own `TMPDIR`
  (`tmp`, `tmp-N`), emptied as a run starts and removed when it ends.
* With `depends`, only the steps that read what the head changes run;
  with `reuse`, a step whose inputs a green run here already passed is
  reused; with `side_by_side`, steps run in parallel; with scoped
  steps (`steps[].pull_requests`), slow steps run only on the pull
  requests that can break them, last and within `scoped_minutes`.
* With `cache_limit`, the slot's caches are pruned as the run ends
  (`CACHE-PRUNED`, `CACHE-OVER-LIMIT`).

The verdict is recorded in `state.json`, logged in `verdicts.log` as
`VERDICT #N <sha>: green` or `red(<steps>)` (`MAIN-GREEN`/`MAIN-RED`
for the default branch, `VERDICT AHEAD` for a prediction), and posted:
`success`, or `failure` naming the first failed step. The description
says `green on <host>`, how many steps ran and why, what was matched,
reused or covered, and `with <branch> at <sha>` under
`merge_default`. A green on a tree an earlier verdict here called red
logs `FLAKE`.

### When a run is not a verdict

* **A host fault.** A step that exits 126 or 127, says `not found` or
  `Error 127`, can't start a container, or says `No space left on
  device`, `ENOSPC` or a `host_fault_text` string, failed for the host.
  No verdict is recorded (`HOST-FAULT`); the head keeps its place and
  is tried on cron's next tick.
* **A stale cache.** A red matching `cache_fault_text` clears the
  slot's `incremental` directories and runs the head again at once
  (`CACHE-FAULT`); a second match stands.
* **A head that can't be checked out** goes behind every other.
* After 3 failures to prepare, or 3 host faults, a head is given up on
  and posted `error`, until `labels.rerun` tries it again.
* **A superseded head** is stopped by the next tick (`STOPPED`): no
  verdict, nothing posted, and the new head is queued.
* A head started less than 60 seconds ago is not started again, so a
  tick in that window doesn't run it twice.

### Predictions, matches and batches

With [`merge.test_ahead`](../config.md#mergetest_ahead), it runs the
Merger's predicted commits from `ahead.json`, from the object store
the Merger's checkout shares, so both must use the same checkout. A
prediction is recorded with `ahead` set and posted nowhere. Each
verdict records its `tree`, `host` and step digest. A head whose tree
has a green verdict here with the same steps, at most `max_age_hours`
old, is posted `success` unrun: `green on <host>: tree tested as <sha>
(<whose>)`. A red verdict is never reused. Nothing after a red
prediction is started. With `batch` above 1 only a batch's top runs,
and a member's head is posted `green on <host>: covered by batch
#a..#b (tested as <sha>)`; a head whose tree is in a batch waits for
its top (`HELD`). The steps must depend on the tree alone: a step that
reads history belongs in `merge.gate` instead.

### Reports and labels

* **[`labels.rerun`](../config.md#keys)** runs a head once more: a red
  head, a green one (forced, matching and reusing nothing), or one
  given up on (`RERUN`). The label comes off.
* **`main_red_issue`** keeps one issue open while the default branch is
  red, comments on each further red head, closes it on the first green
  head, and reopens it when closed too early (`MAIN-RED-REOPENED`). Its
  fixes go first here and in the Merger (`main_red.fixes` in
  `state.json`).
* **`red_comment`** comments once on a pull request whose head is red,
  with the end of each failed step's output.
* **`step_alert`** opens an issue when a step gets slower (`FILED`),
  and closes it when it is back (`CLOSED`). A refused write is
  `REPORT-FAILED`, tried again next time.

Its comments on a pull request (`red_comment`, and the refusal of a
second `labels.rerun` on one head) start with a `Not-a-review` line, so
the Merger's `merge.said_since` doesn't hold an approved pull request
on one posted after the approval.

A post GitHub refuses changes neither the verdict nor the exit status
(`POST-FAILED`); each tick posts again what never arrived.

### Instances

`--instance NAME` runs a block of
[`checks.instances`](../config.md#checksinstances): its own context,
steps, host, poster, state directory (`pr-check-<name>`) and lines
that carry its name (`GREEN mac #7 ...`). With `pull_requests` it runs
only the heads it applies to (`scope.json`; `SCOPE-UNKNOWN` while the
files can't be read). With `gate`, the Merger waits for its context.
With `lock`, a run takes the host's shared lock directory first.

## State and logs

In the state directory (`--state-dir`):

| Path | What it holds |
| --- | --- |
| `state.json` | The queue (`waiting`), each verdict (`results`, kept 21 days), `reruns`, `main_red`, `warm`, `step_alert`, `held`. |
| `state.lock` | Held around every write of `state.json`. |
| `verdicts.log` | One line per verdict, refusal, give-up and report. |
| `worker.log` | Each worker's output: every step's exit status and time, and under a failed step its last 40 lines, failure lines first, each prefixed `    \| `. |
| `worker.lock`, `worker-N.lock`, `worker-lane.lock` | Each slot's lock and its holder. |
| `wt`, `wt-N`, `wt-lane` | Each slot's worktree. |
| `tmp`, `tmp-N` | Each slot's `TMPDIR`. |
| cache directories | Each slot's `checks.caches` (`<name>`, `<name>-N`, `<name>-lane`). |
| `logs/<head>/<step>.log.gz` | Each step's whole output, a time on every line; kept 14 days. |
| `results/<head>/<step>` | Each step's `CHECK_RESULTS`. |
| `depends/<tree>.json` | Dependency records (`checks.depends`). |
| `tracer` | The `preload` tracer, built here. |
| `approved.json` | Reviews read for `approved_first`. |
| `scope.json` | An instance's files per pull request. |

It reads the Merger's `next.json` and `ahead.json` (`--merger-dir`),
`forge-nightly`'s state and the quiet holds. A tick's own output goes
to `~/cron-logs/forge-pr-check.log` through `forge-cron-run`.

## Exit status

* 0: the tick ran, another worker held the slot (`BUSY`), or the check
  was green.
* 1: the check was red.
* 2: it could not ask: no checkout, configuration, instance or token,
  GitHub couldn't list the pull requests, the head couldn't be
  checked out, or a host fault.
* 3: a worker stopped because its head was superseded.

## See also

* [`checks`](../config.md#checks) and
  [`checks.instances`](../config.md#checksinstances) in config.md.
* [forge-merger](forge-merger.md), which merges on this status.
* [forge-quiet](forge-quiet.md), which holds the host quiet.
* [designs/0005](../designs/0005-steps-that-run-only-for-what-changed.md),
  [designs/0021](../designs/0021-a-second-check-instance.md),
  [designs/0029](../designs/0029-a-heads-steps-run-side-by-side.md) and
  [designs/0032](../designs/0032-merge-in-batches.md).
