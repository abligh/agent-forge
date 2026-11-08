# forge-prune-builds

`forge-prune-builds` clears the build output of agents' worktrees that
nobody is using. Agents work in a git worktree per task, and each builds
its own cache (a Rust `target/` is 10 to 30 GB) that nothing removes
when the task ends, so a host's disk fills. This deletes such caches,
and only them: a directory is a candidate only if it carries a valid
`CACHEDIR.TAG`, which Cargo writes into every `target/`. Source, git
data and anything written by hand are never touched, so a wrong
decision costs one rebuild. Optional passes clear more while the disk
is too full, clear shared target directories, and age out untagged
caches such as Go's.

## Usage

    forge-prune-builds --root DIR [--root DIR ...] [--shared DIR ...]
                       [--merged-hours H] [--idle-hours H]
                       [--high-water PCT --low-water PCT]
                       [--pressure-floor-hours H] [--live-trim-hours H]
                       [--every-minutes N]
                       [--aged-cache DIR ...] [--aged-days D] [--dry-run]

Run it from cron through `forge-cron-run`, so it finds the project's
configuration (for the pull-request state) in the cron checkout. Hourly,
with the defaults:

    0 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-prune-builds \
        --root $HOME/agents

Or every five minutes, scanning in full only hourly unless the disk is
too full:

    */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-prune-builds \
        --root $HOME/agents --shared $HOME/agents \
        --high-water 85 --low-water 70 --live-trim-hours 6 \
        --every-minutes 60 --aged-cache $HOME/.cache/go-build

To see what it would clear, run it by hand with `--dry-run`.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--root DIR` | required | A directory whose direct subdirectories with a `.git` are worktrees, such as a supervisor's agents directory. Repeatable. |
| `--shared DIR` | none | A directory whose tagged subdirectories, down to three levels and outside any worktree, are caches shared between worktrees (a Cargo `CARGO_TARGET_DIR`, or one per build kind beneath a directory of them). Repeatable. May be the same directory as a `--root`, or contain another `--shared`; each cache is judged once. |
| `--merged-hours H` | `6` | Idle hours before a cache is cleared when its worktree's branch heads a merged or closed pull request. |
| `--idle-hours H` | `48` | Idle hours before a cache is cleared otherwise, and for shared caches. Must be at least `--merged-hours`. |
| `--high-water PCT` | off | Used percent of a root's filesystem at which more is cleared after the normal pass. Needs `--low-water`. |
| `--low-water PCT` | off | Used percent to clear down to under pressure. Needs `--high-water`, and must be above 0 and at most `--high-water`. |
| `--pressure-floor-hours H` | `1` | Under pressure, keep a cache used this recently. At least 0. |
| `--live-trim-hours H` | off | If the pressure pass isn't enough, trim the caches in use too, of worktrees and shared ones, taking outputs unused for `H` hours. Needs `--high-water`; above 0. |
| `--every-minutes N` | off | Skip the run when every root is under `--high-water` (or no `--high-water` is given) and the last full scan was less than `N` minutes ago. Above 0. |
| `--aged-cache DIR` | none | An untagged cache, such as `GOCACHE`, whose files are deleted once `--aged-days` old. Repeatable. |
| `--aged-days D` | `5` | Days unused before an `--aged-cache` file is deleted. Above 0. |
| `--dry-run` | off | Report what would be cleared, and delete nothing. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Where to find the configuration ([config.md, Finding it](../config.md#finding-it)). Under `forge-cron-run` it is found in the cron checkout. |
| `FORGE_AGENT`, `GH_TOKEN` | The token the pull-request state is read with: `FORGE_AGENT`'s App token if it mints, else `GH_TOKEN`, else none. |
| `FORGE_GITHUB_API` | The GitHub API base. |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME`, `HOME` | Where the time of the last full scan is kept (below). |

It also reads `CARGO_TARGET_DIR` from other processes' environments,
for `--shared`.

## Configuration

Only `repo`, and through it the read token
([config.md, Keys](../config.md#keys)). Without a configuration, it
says so on stderr and judges every worktree by `--idle-hours`. It has
no section of its own: everything else is on the command line.
`checks.cache_limit`, which prunes a build check's own slots, is a
different mechanism
([config.md, Pruning the build caches](../config.md#pruning-the-build-caches)).

## What it does

### The normal pass

For each worktree under each `--root`, it finds the tagged caches,
down to three levels below the worktree (`target/`,
`desktop/target/`, `crates/x/target/`). It doesn't look inside a cache
for more, and skips `.git`. A tag is valid when `CACHEDIR.TAG` starts
with the specification's signature line,
`Signature: 8a477f597d28d172789f06886806bc55`. A cache is cleared only
when **all** of these hold:

1. No process has its working directory, or a file open, inside the
   worktree (read from `/proc`).
2. Every Cargo build lock in it (`.cargo-lock`, `.cargo-build-lock`,
   `.cargo-artifact-lock`, up to two levels down) can be taken without
   waiting.
3. No **file** in it was read or written for `N` hours: the newest
   atime or mtime of its files. Directories' times, and the tag file's,
   are not evidence, since scans like this one update them. `N` is
   `--merged-hours` when the worktree's branch heads a merged or closed
   pull request and none open, and `--idle-hours` otherwise, including
   when the state can't be read (detached head, no configuration, an
   API failure). GitHub is asked only when the answer depends on it.

The two thresholds guard each other. A merged pull request doesn't
prove a worktree is finished, since agents reuse them, and with
`relatime` a file's atime may be up to a day stale, which the longer
threshold allows for.

A cache is cleared by renaming it to `<name>.forge-pruning` while its
locks are held, then deleting that. A tool that starts meanwhile finds
no cache rather than half of one. A `.forge-pruning` left by an
interrupted run is deleted on the next.

### Shared caches (`--shared`)

A shared cache is any directory under `--shared` that carries a valid
`CACHEDIR.TAG`, down to three levels: `.tgt-x` itself, or
`.tgt-x/wasm32-wasip1` beneath an untagged `.tgt-x`. A tagged directory
is one cache and isn't searched further. A directory with a `.git` is a
worktree, and nothing inside it is a shared cache.

Each is judged by rules 1 to 3 with two differences: no pull request
decides it, so only `--idle-hours` applies; and it is also in use while
any process this user can read names it, a directory inside it, or a
directory above it as `CARGO_TARGET_DIR`. One in use is trimmed by
`--live-trim-hours` like a live worktree's cache.

### Under pressure (`--high-water`, `--low-water`)

After the normal pass, for each root (and shared directory) whose
filesystem is at least `--high-water` percent used, as `df` counts it,
it clears the caches the normal pass kept that pass rules 1 and 2,
whatever their idle time, least recently used first, until the
filesystem is under `--low-water`. A cache used in the last
`--pressure-floor-hours` is kept.

### Live caches (`--live-trim-hours`)

If a root is still at or over `--high-water`, the caches in use, of
worktrees and shared ones, are trimmed, the one with most to give
first, until it is under `--low-water`. Rule 1 keeps a whole cache from
a live session, which is exactly when it grows: each re-link leaves
another copy of a test binary. So this takes only what a build no
longer needs: under each `deps` directory, the files, and under each
`incremental` directory, whole sessions (`<crate>/<session>`), neither
read nor written for `H` hours and not open. Rule 2 still holds, and the locks are held while it
trims, so a running build is never touched.

### Aged caches (`--aged-cache`)

For a cache that keeps entries rather than a build per directory, and
has no tag (Go's `GOCACHE`), every file neither read nor written for
`--aged-days` is deleted, whatever the disk use. Directories stay,
since the cache's owner creates its layout once. The walk never follows
a symbolic link, and a link is judged by its own times, so nothing
outside the directory is touched. A link, `/` or the home directory as
the cache itself is refused (exit 2).

### Output

One line per cache cleared or kept, with the reason (`busy`,
`locked (a build is running)`, `used 3h ago`,
`pull request finished, idle 9h`), lines for the pressure passes, one
per aged cache, and a total:

    forge-prune-builds: cleared 3, kept 7, 41.2 GB

A dry run says `would clear`, and under pressure estimates the usage it
would reach from the sizes.

## State and logs

| Path | What it is |
| --- | --- |
| `~/.local/state/agent-forge/forge-prune-builds/last-scan-<hash>` | With `--every-minutes`: the time the last full scan of these roots started. `<hash>` is of the sorted `--root` and `--shared` paths. Not written by a dry run. Note it is not under a project's directory. |
| `<cache>.forge-pruning` | A cache being deleted. |

Under `forge-cron-run`, the output goes to
`~/cron-logs/forge-prune-builds.log`.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | The scan finished, whatever it cleared or kept, or it was skipped by `--every-minutes`. |
| 2 | Bad arguments (thresholds out of order, flags missing their partner), a `--root` or `--shared` that isn't a directory, or an `--aged-cache` that isn't a cache directory. |

## See also

* [designs/0016](../designs/0016-clear-build-output-of-idle-worktrees.md),
  clearing idle worktrees
* [designs/0041](../designs/0041-clear-build-output-before-the-disk-fills.md),
  pressure, shared caches and live trimming
* [forge-host-settings](forge-host-settings.md), to track the cron line
