# forge-memory-check

`forge-memory-check` asserts that the agents' memory index, `MEMORY.md`,
still loads in full, and that every topic file can be reached from it.
The index is read into every session at start. Past the harness's limit
it is truncated, not refused, and the newest entries are the ones
dropped, silently. A topic file no index entry leads to is written and
never read. Either way sessions simply stop knowing things, and nobody
sees a failure. This check makes it visible. The narrative is in
[memory-sync.md, Keeping the index loadable](../memory-sync.md#keeping-the-index-loadable).

## Usage

    forge-memory-check [--dir DIR] [--limit BYTES] [--long-line BYTES]

`MEMORY.md` lives on the host, not in the project's repository, so this
runs from cron on each host that has one, not in CI:

    0 * * * * FORGE_CRON_CHECKOUT=$HOME/widget-cron \
        $HOME/.local/bin/forge-cron-run forge-memory-check

By hand, on any directory:

    forge-memory-check --dir ~/.claude/projects/-home-agent-widget/memory

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--dir DIR` | `$FORGE_MEMORY_DIR`, else `memory.dir` | The memory directory. When given, the configuration isn't read at all. |
| `--limit BYTES` | `memory.limit`, else 24985 | The bytes of `MEMORY.md` the harness loads. |
| `--long-line BYTES` | `memory.long_line`, else 200 | The bytes past which one index line is reported. |

With `--dir`, `memory.limit` and `memory.long_line` are not read, so the
built-in defaults apply unless `--limit` and `--long-line` are given
too.

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_MEMORY_DIR` | The memory directory, when `--dir` isn't given. With it set, a missing or broken configuration is not an error. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Where to find the configuration ([config.md, Finding it](../config.md#finding-it)). |

## Configuration

From the `memory` section ([config.md, Keys](../config.md#keys)): `dir`,
`limit` and `long_line`. It reads no other key.

## What it does

It reads `MEMORY.md` and every other `*.md` file directly in the
directory, and prints its findings on stdout.

**Failures** (exit 1):

* `MEMORY.md` is over the limit, in bytes: it is being truncated.
* An index entry, a `(file.md)` link in `MEMORY.md`, points at no file.
* A topic file is in no index entry. A file counts as indexed when it
  is reachable from `MEMORY.md`, transitively, within the directory:
  through `(file.md)` links, and through `[[name]]` links, which match
  a file's front-matter `name:` or its base name without `.md`. So an
  index can be layered through index pages, and topic files can lead
  to each other. A `[[name]]` that matches nothing is not an error.
* `MEMORY.md` is missing from a directory that still has topic files:
  every memory in it is unreachable.

**Warnings** (no change to the status):

* `MEMORY.md` is over 85% of the limit.
* An index line is over `--long-line` bytes. Up to ten are listed, as
  `L<line> <bytes> <topic file>`, then a count of the rest. The topic
  file is the line's first link, where its detail belongs.

**Headroom.** Every report ends with the room left, in entries: how many
more index lines of the current mean length fit under the limit. A
clean run prints one line with the size, the limit, the number of topic
files and that room.

**A missing directory is not a failure.** A check that can't see its
subject must not report on it, so it says
`no memory directory on this host` on stdout and exits 0. So does a
directory with no `.md` files at all.

The repair for a long index is editorial: move detail out of index
lines into the topic files.
[forge-memory-trim](forge-memory-trim.md) helps with that.

## State and logs

It reads only; it writes nothing. Under `forge-cron-run`, the output
goes to `~/cron-logs/forge-memory-check.log`.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Under the limit and every file reachable, perhaps with warnings; or no memory directory on this host. |
| 1 | Over the limit, an entry pointing at no file, a file in no entry, or the index gone while topic files remain. |
| 2 | No directory named: no `--dir`, no `FORGE_MEMORY_DIR`, and no configuration or no `memory.dir` in it. |

## See also

* [memory-sync.md](../memory-sync.md)
* [forge-memory-trim](forge-memory-trim.md), the repair
* [forge-memory-sync](forge-memory-sync.md)
