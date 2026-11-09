# forge-cron-run

`forge-cron-run` is the wrapper every scheduled agent-forge job runs
through. It resets a dedicated checkout of the project to the default
branch, so the code that runs is the code that was reviewed. It gives
every job the same `PATH`, whatever started it. It refuses to run an
agent-forge release the project didn't pin. It takes locks so that two
runs of one tool never overlap and no reset happens under a running
job. And it appends each run's output to a log file, since cron has
nowhere useful to put it. A project wants it because a crontab line
that calls a tool directly gets none of this.

## Usage

    FORGE_CRON_CHECKOUT=~/widget-cron forge-cron-run TOOL [ARGS...]

It is run from a crontab, one line per job, and by `forge-receiver` for
each tick a webhook delivery gives work to. A typical crontab:

    */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-router --send
    */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-nightly
    7 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-stats-watch

The cron checkout (`~/widget-cron` here) is a clone of the project kept
only for jobs. Never point it at a person's or an agent's working copy:
every run resets it hard to `origin/<default branch>`.

Do not run `forge-install` through it. `forge-install` exists to end a
pin mismatch, which this wrapper refuses to run anything under.

## Options

It has no options of its own. Everything after `TOOL` is handed to the
tool unchanged.

| Argument | Default | Meaning |
| --- | --- | --- |
| `TOOL` | required | The tool to run. A name starting `forge-` is this installation's own `bin/<TOOL>`. Any other name is the project's `tools/<TOOL>`, in the cron checkout. A name with a `/` or starting `.` is refused. |
| `ARGS...` | none | The tool's own arguments, passed as they are. |

With no `TOOL` it prints `usage: forge-cron-run TOOL [ARGS...]` and
exits 2.

## Environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `FORGE_CRON_CHECKOUT` | required | The project's cron checkout. The job runs with it as its working directory. Unset, it exits 2. |
| `FORGE_CRON_LOGDIR` | `~/cron-logs` | Where each tool's log and the lock files go. |
| `FORGE_CRON_PATH` | none | Extra directories for `PATH`, colon-separated, such as a host's toolchains. They come after this release's `bin` and before `~/.local/bin` and the system directories. |
| `FORGE_CRON_APP` | none | An agent or machinery App the job acts as. Its installation token is minted into `GH_TOKEN`. A mint that fails stops the job; there is no fallback to the host's own `gh` login. |
| `FORGE_CRON_LOCK` | none | The lock file of another wrapper that runs jobs from the same checkout, while a project moves its crontab over. This one then takes that file instead of its own checkout lock. The other wrapper must use `flock`. |
| `FORGE_CRON_LOCK_WAIT_S` | `300` | One budget, in seconds, for all the locks and the sync. Keep it shorter than any job's period. |
| `FORGE_CRON_FETCH_RETRY_S` | `5` | How long to wait before the one retry of a fetch that lost a ref-lock race with another fetch. |
| `HOME` | the user's | The home directory the defaults are under. |

The caller's `PATH` is discarded. The job's `PATH` is this release's
`bin`, then `FORGE_CRON_PATH`, then `~/.local/bin`, `/usr/local/bin`,
`/usr/bin` and `/bin`. The rest of the environment is passed on, so
variables a tool reads (`FORGE_STATE_DIR`, say) can be set on the
crontab line too.

## Configuration

From `.agent-forge/config.json` it reads only `default_branch`
([config.md, Keys](../config.md#keys)), from the checkout before the
sync. So a change of default branch takes effect one run later. When
the file can't be read, it uses `main`.

It also reads `.agent-forge/pin`, after the sync
([config.md, The pin](../config.md#the-pin)). With `FORGE_CRON_APP` set,
it reads that App's entry (under `agents` or `machinery`) and `key_dir`
to mint the token.

## What it does

Each run, in order:

1. Takes `.<TOOL>.lock` in the log directory, exclusively: one run of
   each tool at a time.
2. Takes the checkout's lock, shared, so different jobs run side by
   side. It takes it exclusively instead when a `sync-wanted` marker is
   there (see below).
3. Writes the header, `=== <time> <TOOL> <ARGS> ===`. The time is when
   the job was launched, before any lock.
4. Checks the checkout is a git checkout (`NO-CHECKOUT` otherwise), and
   fetches the default branch from `origin`. A fetch that fails with
   `cannot lock ref` is retried once (`RETRY: …`). Any other failure is
   `SYNC-FAILED: fetch`.
5. If the checkout isn't at `origin/<branch>`, or has an edited tracked
   file, it leaves a `sync-wanted` marker, waits for the checkout's
   lock exclusively, and runs `git reset --hard origin/<branch>`. The
   marker makes later jobs queue for the exclusive lock rather than
   slip past it, so a steady stream of short jobs can't starve the
   reset. Whoever resets removes the marker. Then it goes back to a
   shared lock for the run.
6. Logs `at <short sha>`, the commit the job runs at.
7. Refuses to start if `forge-install` has left
   `<install>.install-wanted` (`INSTALL-PENDING`). Otherwise it takes
   the installation's lock, shared. That lock is handed to the tool,
   and on to anything the tool starts, so `forge-install` never moves
   the installation under a running job.
8. Checks the pin. Without `.agent-forge/pin`, nothing is checked. With
   it, the installation's `HEAD` must be the pinned commit, or the run
   stops with `PIN-MISMATCH`. A pin file that doesn't start with a full
   40-character SHA is `BAD-PIN`.
9. Finds the tool (`NO-SUCH-TOOL` if it isn't there and executable).
10. With `FORGE_CRON_APP`, mints the token (`APP-TOKEN-FAILED` on
    failure) and logs `as <App>`.
11. Logs `waited=<s>`, runs the tool in the checkout with its standard
    input closed and its output in the log, then logs
    `ended <time> ran=<s>` and `exit=<status>`.

When a lock isn't had within `FORGE_CRON_LOCK_WAIT_S`, it logs
`LOCK-TIMEOUT: <which lock> for <s>s; giving up` and `exit=2`, and
says the same on standard error. A waiter so gives up before its
successor is launched, rather than piling up behind a hung job.

Every line that stops a run before the tool starts is written to the
log, followed by `exit=2`: `NO-CHECKOUT`, `SYNC-FAILED`,
`INSTALL-PENDING`, `PIN-MISMATCH`, `BAD-PIN`, `NO-SUCH-TOOL`,
`APP-TOKEN-FAILED`.

## State and logs

All in the log directory (`FORGE_CRON_LOGDIR`, default `~/cron-logs`):

| File | What it is |
| --- | --- |
| `<TOOL>.log` | Every run of the tool, appended: header, `at`, `waited=`, the tool's output, `ended`, `exit=`. |
| `.<TOOL>.lock` | One run of each tool at a time. |
| `.forge-cron.<checkout>.lock` | The checkout's lock. `<checkout>` is its real path with `/` and other odd characters made `_`. `FORGE_CRON_LOCK` replaces it. |
| `.forge-cron.<checkout>.sync-wanted` | Left while a job waits to reset the checkout. |

Beside the installation it uses `<install>.lock` (shared for each job)
and reads `<install>.install-wanted`, both `forge-install`'s.

How long a job held the checkout can be read from its log: the
header's time, `waited=` and `ran=`.

## Exit status

The tool's own status, unchanged. A tool killed by a signal is reported
as 128 plus the signal, as a shell does.

2 when the tool did not run: no `TOOL`, no `FORGE_CRON_CHECKOUT`, no
checkout, a failed sync, an install pending, an installation not at the
pin, a bad pin file, no such tool, no lock in time, or no App token.

## See also

* [forge-install](forge-install.md), which moves the installation to the
  pin.
* [forge-receiver](forge-receiver.md), which ticks tools through this
  wrapper.
* [config.md, The pin](../config.md#the-pin) and
  [Where state lives](../config.md#where-state-lives).
