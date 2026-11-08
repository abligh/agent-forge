# forge-memory-sync

`forge-memory-sync` shares the agents' memory directory between hosts
through a private git repository. Claude Code keeps a session's
memories in one directory per host. When a project's agents run on
several hosts, each would otherwise learn things the others never see.
Each run commits what changed on this host, rebases onto what the other
hosts pushed, and pushes. `MEMORY.md` merges as a union; any other
conflict stops this host's sync until someone resolves it.
[memory-sync.md](../memory-sync.md) tells the whole story: how a sync
works, authentication, seeding, and resolving a stop. This page is the
reference.

## Usage

    forge-memory-sync [--seed] [--file]

From cron, through `forge-cron-run`, on every host that shares the
memory, with the minute staggered between hosts:

    */10 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        FORGE_CRON_APP=Merger \
        $HOME/.local/bin/forge-cron-run forge-memory-sync --file

`FORGE_CRON_APP` names a machinery App whose token `forge-cron-run`
mints into `GH_TOKEN`. That token serves both an HTTPS remote and the
issue `--file` writes. Leave it out on a host that pushes over SSH and
doesn't file.

Once per host, by hand, from the cron checkout:

    cd ~/widget-cron && forge-memory-sync --seed

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| (none) | | One sync: commit, rebase onto `origin`, push. |
| `--seed` | off | Set this host up once ([memory-sync.md, Seeding a host](../memory-sync.md#seeding-a-host)). |
| `--file` | off | Report a run that exits 1 on a GitHub issue in the project's repository, and forget the report once a run exits 0 ([memory-sync.md, The issue](../memory-sync.md#the-issue)). |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_MEMORY_DIR` | Overrides `memory.dir`. |
| `FORGE_MEMORY_REMOTE` | Overrides `memory.remote`. |
| `FORGE_MEMORY_GIT_DIR` | Overrides `memory.git_dir`. |
| `FORGE_MEMORY_SEED_DIR` | Overrides `memory.seed_dir`. |
| `FORGE_MEMORY_SSH_KEY` | Overrides `memory.ssh_key`. |
| `FORGE_MEMORY_HOST` | This host's name in commit messages, the committer email and the issue title. Default: the hostname up to its first dot. |
| `GH_TOKEN` | Read by git's credential helper over HTTPS, and the token `--file` files with. |
| `FORGE_AGENT` | With `--file`: an agent whose App token is used for the issue in preference to `GH_TOKEN`, when it mints. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Where to find the configuration ([config.md, Finding it](../config.md#finding-it)). |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the state directory ([config.md, Where state lives](../config.md#where-state-lives)). |
| `FORGE_GITHUB_API` | The GitHub API base, for `--file`. |

## Configuration

The `memory` section ([config.md, Keys](../config.md#keys)): `dir` and
`remote` (required, unless the variables above give them), `git_dir`,
`seed_dir`, `ssh_key`, `branch` and `labels`. Also `project` (for
default paths and the state directory) and `repo` (where `--file`
files).

## What it does

See [memory-sync.md, How a sync works](../memory-sync.md#how-a-sync-works).
In short: every git command runs against `memory.git_dir` with the
memory directory as its work tree, with the committer
`forge-memory-sync <forge-memory-sync@<host>>` passed with `-c`, and
is bounded to two minutes. The run prints one line,
`forge-memory-sync: <what happened>`, on stdout when it exits 0 and on
stderr otherwise. With `--file`, a second line says what it did on
GitHub, or that it couldn't report. A failure to report doesn't change
the exit status.

## State and logs

See [memory-sync.md, On-disk state](../memory-sync.md#on-disk-state).
Under `forge-cron-run`, the output goes to
`~/cron-logs/forge-memory-sync.log`.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Synced, or nothing to do. |
| 1 | Something needs a person: a conflict, a push refused twice, a rebase already in progress, a seed that set files aside, or a seed whose push another host beat (the next sync pushes it). |
| 2 | The run couldn't ask its question: no configuration, no memory directory or no `MEMORY.md` in it, a host not seeded (or, with `--seed`, one already seeded: its `memory.git_dir` exists), git or the network failing, or an unexpected error. |

## See also

* [memory-sync.md](../memory-sync.md), the narrative
* [forge-memory-check](forge-memory-check.md) and
  [forge-memory-trim](forge-memory-trim.md)
