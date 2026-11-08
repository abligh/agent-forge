# forge-host-settings

`forge-host-settings` compares a host's agent settings and crontab with
copies the project tracks in its own repository, or installs those
copies. Every agent session on a host loads the same files, such as
Claude Code's `~/.claude/settings.json` and the hooks it names, and
each host runs a crontab. Edited by hand on one host, they drift
silently from the others, and a crontab installed on the wrong host
runs its jobs twice. With the copies tracked, they change by pull
request like anything else, and `--apply` from cron installs each
approved change on every host.

## Usage

    forge-host-settings --check   [--tree DIR] [--host NAME]
    forge-host-settings --install [--tree DIR] [--host NAME]
    forge-host-settings --apply   [--tree DIR] [--host NAME]

Exactly one of `--check`, `--install` and `--apply` is required.

From each host's crontab, through `forge-cron-run`, which first resets
the cron checkout to the default branch:

    */5 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-host-settings --apply

By hand, to see what differs, or to install what a person reviewed:

    FORGE_CRON_CHECKOUT=~/widget-cron forge-host-settings --check
    FORGE_CRON_CHECKOUT=~/widget-cron forge-host-settings --install

A project that wants a person to install every change leaves `--apply`
out of its crontabs and runs `--install` by hand.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--check` | | Report the differences as a unified diff on stdout. Change nothing. |
| `--install` | | Write the tracked copies over the host's, and install the host's crontab. Says what it did, or that nothing differs. |
| `--apply` | | `--install`, for cron: prints nothing when nothing differs, and won't install a crontab that drops its own `--apply` line (see below). |
| `--tree DIR` | `$FORGE_CRON_CHECKOUT` | The checkout the copies and the configuration are read from. With neither, the tool exits 2. |
| `--host NAME` | `$FORGE_HOST_NAME`, else the hostname up to its first dot | This host's short name, which picks its crontab. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CRON_CHECKOUT` | The default for `--tree`. |
| `FORGE_HOST_NAME` | The default for `--host`. |
| `HOME` | Where each file's `to` path is installed under. |
| `FORGE_STATE_DIR`, `XDG_STATE_HOME` | Move the state directory, where backups go ([config.md, Where state lives](../config.md#where-state-lives)). |

## Configuration

It reads `.agent-forge/config.json` from `--tree`, never from the
working directory, and the `host_settings` section of it
([config.md, Keys](../config.md#keys)):

* `files`: each `{"from": ..., "to": ..., "mode": ..., "json": ...}`.
  `from` is a path in the project, `to` a path under the home
  directory. `mode` is an octal string, default `"0644"`. `json: true`
  compares the file as JSON.
* `host_keys`: keys of a JSON file that belong to the host, such as a
  choice Claude Code records when someone answers it in its interface.
* `crontabs`: `{"<host>": path in the project}`. The key is the host's
  short name: the output of `hostname -s`, unless `--host` or
  `FORGE_HOST_NAME` says otherwise.

A configuration with neither `files` nor `crontabs` is an error
(exit 2). Either alone is enough.

## What it does

**The copies come from the cron checkout**, which `forge-cron-run`
keeps at the default branch, and not from the working tree the tool
runs in. Installing a branch by mistake would otherwise be one `cd`
away. `--tree` reads another checkout, on purpose.

**Installing follows approval.** The settings decide what agents may do
without asking, and the crontab what runs unattended. So the tracked
copies should be code-owned: a change reaches the default branch only
with the owner's approving review. Once it has, `--apply` installs it.
Running sessions pick new settings up only when they next start.

**It validates first.** Every tracked file is read, and every `json`
file parsed, before anything is written. A missing or invalid tracked
copy, or a `crontab -l` that fails for any reason but "no crontab",
exits 2 with nothing written.

**What counts as a difference.**

* A file not marked `json` compares byte for byte.
* A file marked `json` compares as the JSON object it holds, so key
  order and layout don't count. A reordered list does. The keys in
  `host_keys` are ignored by the comparison. A host file that isn't a
  JSON object (`[]`, `null`, invalid JSON) differs, and installing
  repairs it.
* A file absent from the host differs.
* The crontab compares as text.

**Which crontab.** A host installs only the entry for its own short
name, never another host's. A host with no entry keeps whatever crontab
it has. `--check` and `--install` say so; `--apply` says nothing.

**`--install` and `--apply`.**

* It writes only the files that differ, each through a temporary file
  and a rename, with the tracked `mode`, so a failure leaves the old
  file whole. The file's directory is created if needed.
* For a `json` file, it keeps the values the host's file has for the
  `host_keys`, so an install doesn't undo a choice made on the host.
  The file is then written as JSON with two-space indent. With no host
  key to keep, it writes the tracked bytes.
* Each replaced file is kept as a backup (see below), never beside the
  file: every file in a hooks directory is taken for a hook.
* The crontab is installed with `crontab -`, after keeping the old one.
  If `crontab` refuses the tracked copy, the old crontab stays and the
  tool exits 1.
* `--apply` won't install a crontab that lacks the line
  `forge-host-settings --apply` when the host's current crontab has
  one, since that would turn the installing off silently. It says so
  and exits 1. `--install`, run by a person, will.
* A second run finds nothing to do.

## State and logs

| Path | What it is |
| --- | --- |
| `~/.local/state/agent-forge/<project>/host-settings/backups/<name>.<UTC time>` | A file it replaced, by its base name, such as `settings.json.20261108T120000Z`. |
| `~/.local/state/agent-forge/<project>/host-settings/backups/crontab.<UTC time>` | A crontab it replaced. |

Backups are never removed. Under `forge-cron-run`, the output goes to
`~/cron-logs/forge-host-settings.log`.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | `--check`: the host matches. `--install`, `--apply`: done, or nothing to do. |
| 1 | `--check`: something differs (the diff is on stdout, a summary on stderr). `--install`, `--apply`: the crontab was refused by `crontab`, or `--apply` refused to drop its own line. |
| 2 | No `--tree` or `$FORGE_CRON_CHECKOUT`, a bad configuration, nothing listed in `host_settings`, or a tracked copy or the crontab couldn't be read. Nothing was written. |

## See also

* [config.md, Keys](../config.md#keys) (`host_settings`)
* [forge-session-start](forge-session-start.md), the hook such files
  usually install
* [forge-install](forge-install.md)
