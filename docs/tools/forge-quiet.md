# forge-quiet

`forge-quiet` holds a shared host quiet while someone times something on
it, and tells a job whether it must wait. An agent measuring
performance on a host other agents also build on gets noise from their
builds, check runs and deploys. With a **hold**, that heavy work waits
until the hold ends or is released. A hold is one small file that ends
by itself, so a holder that dies blocks nothing for longer than it
asked.

## Usage

    forge-quiet [--dir DIR] hold MINUTES REASON... [--as NAME] [--no-settle]
    forge-quiet [--dir DIR] release [--as NAME]
    forge-quiet [--dir DIR] status [--wait MINUTES]

An agent takes and drops holds by hand, around a measurement:

    forge-quiet hold 45 "baseline timings of the release build"
    ./measure.sh
    forge-quiet release

A job asks before heavy work, and decides what to do if it is still
held:

    forge-quiet status --wait 180 || echo "still held; going ahead"

`forge-pr-check` reads the holds itself and starts no run while one is
live, so it needs no call to this tool.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--dir DIR` | `$FORGE_QUIET_DIR`, else `quiet.dir` | The quiet directory. Given before the subcommand. |
| `hold MINUTES REASON...` | | Take this holder's hold for `MINUTES` (1 to `quiet.max_minutes`), or replace it. `REASON` is one line; several words are joined with spaces. |
| `release` | | Drop this holder's hold. |
| `status` | | List the live holds, soonest to end first. |
| `--as NAME` | `$FORGE_AGENT` | For `hold` and `release`: the holder. Letters, digits, `.`, `_` and `-`, at most 64, starting with a letter or digit. |
| `--no-settle` | off | For `hold`: don't run `quiet.settle`. |
| `--wait MINUTES` | none: answer at once | For `status`: wait up to this long for every hold to end, looking again every 30 seconds. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_QUIET_DIR` | The quiet directory, when `--dir` isn't given. Also read by `forge-pr-check`. Use it on a host where `quiet.dir` can't exist. |
| `FORGE_AGENT` | The holder, when `--as` isn't given. |
| `FORGE_CONFIG`, `FORGE_CRON_CHECKOUT`, `FORGE_PROJECT_ROOT` | Where to find the configuration, in that order ([config.md, Finding it](../config.md#finding-it)). |

The settle command (below) runs with these set:

| Variable | Value |
| --- | --- |
| `FORGE_QUIET_HOLDER` | The holder. |
| `FORGE_QUIET_MINUTES` | The minutes the hold was asked for. |
| `FORGE_QUIET_UNTIL` | When the hold now ends, as `YYYY-MM-DDTHH:MM:SSZ`. |
| `FORGE_QUIET_DIR` | The quiet directory. |

## Configuration

The `quiet` section ([config.md, Keys](../config.md#keys)): `dir`,
`max_minutes` (default 120), `settle` and `settle_minutes` (default
30).

`hold` always reads the configuration, for these limits. `release` and
`status` read it only when neither `--dir` nor `FORGE_QUIET_DIR` names
the directory. With no directory from anywhere, nothing is ever held:
`status` says so and exits 0.

## What it does

**A hold** is the file `<dir>/<holder>`, whose first line is

    until 2026-11-08T14:30:00Z baseline timings of the release build

It is written to a temporary file and renamed into place, so a reader
never sees half of one. A holder has at most one hold; holding again
replaces it. A hold is live until its time. One past its time, or a
file that can't be parsed, holds nothing. The directory must exist:
`hold` doesn't create it.

**Settling.** A hold stops heavy work from starting, not work already
under way. When the configuration has `quiet.settle`, a command (an
argument list), `hold` runs it once the hold is written, and then every
30 seconds until it exits 0, for at most `quiet.settle_minutes`. A
`forge-` name runs from this installation, a relative path from the
configuration's checkout, and the command's working directory is that
checkout. One run may take two minutes.

* Exit 0: settled. It prints `settled; held by <holder> until <time>`.
* Exit 1: busy. It waits and tries again, printing the command's output
  when it changes.
* Anything else is an error, said as one. Three errors in a row end the
  wait. A busy run in between resets the count.

Before each run the hold is taken again for its full minutes, and again
once settled, so settling doesn't spend the hold. If it doesn't settle
in time, or fails three times, the hold is kept and `hold` exits 3.
Whether to wait, go ahead or release is then the holder's call.

**status** prints `quiet: no hold is live`, or one line per live hold,
`held: <holder> until <time>: <reason>`.

## State and logs

| Path | What it is |
| --- | --- |
| `<quiet dir>/<holder>` | One hold. Mode 0664, so other users on the host can read and replace it. |
| `<quiet dir>/.<holder>.<pid>` | A hold being written. |

Output goes to stdout. Nothing else is kept.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | `hold`, `release`: done (also when there was no hold to release). `status`: no hold is live, or none is after `--wait`. |
| 1 | `status`: a hold is live, or still is when `--wait` runs out. |
| 2 | Bad arguments, no holder for `hold` or `release`, no quiet directory for them, a hold too long or a bad holder name, or the directory can't be written. |
| 3 | `hold`: the hold is taken, but the settle command still fails at the end of `settle_minutes`, or failed with an error three times in a row. Its last output is printed. |

## See also

* [config.md, Keys](../config.md#keys) (`quiet`)
* [agent_forge/quiet.py](../../agent_forge/quiet.py), the hold file's
  format, which a project's own jobs may read directly
