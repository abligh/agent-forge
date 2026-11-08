# forge-memory-trim

`forge-memory-trim` shortens index lines in `MEMORY.md` by removing
detail that is already in the topic file each line points at. Index
lines grow one way: someone learns a refinement and appends
`**Added <date>**: ...` to the one-line hook. When
[forge-memory-check](forge-memory-check.md) says the index is near its
limit, the ad-hoc repair is a character cap, which cuts lines
mid-sentence and loses the operative half. This tool never truncates.
It removes an appended clause only when its substance is already in
the topic file, refuses where it isn't, and leaves the choice of which
lines to shorten to a person. The narrative is in
[memory-sync.md, Keeping the index loadable](../memory-sync.md#keeping-the-index-loadable).

## Usage

    forge-memory-trim [--dir DIR] [--check]
    forge-memory-trim [--dir DIR] --apply --entry FILE [--entry FILE ...]

Run by hand. First list the candidates, read each line, then name the
ones to shorten:

    forge-memory-trim
    forge-memory-trim --apply --entry build-cache.md --entry flaky-tests.md

`--check` suits a scheduled job or a pre-commit hook that should fail
while any line could be shortened. There is no bulk mode.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--dir DIR` | `$FORGE_MEMORY_DIR`, else `memory.dir` | The memory directory. |
| `--check` | off | Also exit 1 when a line could be shortened and hasn't been. |
| `--apply` | off | Rewrite `MEMORY.md`, shortening only the lines `--entry` names. Needs at least one `--entry`. |
| `--entry FILE` | none | A topic file, by its name as the index links it, whose index line may be shortened. Repeatable. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_MEMORY_DIR` | The memory directory, when `--dir` isn't given. |
| `FORGE_CONFIG`, `FORGE_PROJECT_ROOT` | Where to find the configuration ([config.md, Finding it](../config.md#finding-it)). A missing or broken configuration is not itself an error. |

## Configuration

Only `memory.dir` ([config.md, Keys](../config.md#keys)).

## What it does

**Which lines.** Only index lines of the form
`- [Title](file.md) — hook`, with an em dash. The clause it may remove
runs from `**Added` or `**20<yy>-` (or `**20<yy>,`) to the end of the
line. A trailing `;` before it goes too.

**Verify before removing.** For each line with such a clause, it reads
the topic file and checks that every word of six letters or more in the
clause (except `added`) is in the file, ignoring case. One missing word
is enough to refuse. A clause with no such words can't be verified, so
it is refused too. So is a line whose topic file can't be read.

**What it prints.** On stdout, one line per candidate:

    CANDIDATE  build-cache.md: 143 bytes, safe to lose from the index

with `<-- AND LOOKS LIKE IT SUPERSEDES THE REST OF THE LINE` added when
the clause uses words such as "replaces", "instead", "no longer" or
"rather than". That case deserves the most care: the removed clause may
be the newest truth. On stderr:

* `REFUSED <file>: <why>` and the clause, for each refusal, with the
  missing words;
* `TRUNCATED <file>` for a line an earlier cap cut (it ends in `…`,
  perhaps with a full stop). The text is gone; rewrite the hook from
  the topic file. The tool won't guess;
* a summary count, and a reminder that a candidate is not a
  recommendation: it is only known that nothing would be lost.

**`--apply`** replaces each named candidate's line with
`- [Title](file.md) — <hook without the clause>`, and rewrites
`MEMORY.md`. Every other line is left as it was. An `--entry` with
nothing removable is named on stderr. **No topic file is ever written**:
this tool deletes, having checked the text survives elsewhere. Append
a refused clause to its topic file first, then run it again.

## State and logs

It reads `MEMORY.md` and the topic files, and with `--apply` writes
`MEMORY.md` only. It keeps no state and no backup; the memory
repository's history (see [forge-memory-sync](forge-memory-sync.md))
is the record.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Nothing refused or already cut (and with `--check`, no candidate either); or `--apply` shortened every named line, with nothing refused or cut. |
| 1 | A line couldn't be shortened safely, or was already cut; with `--check`, also a candidate not yet applied; with `--apply`, also an `--entry` with nothing removable. |
| 2 | No directory named, no `MEMORY.md` in it, `--apply` without `--entry`, or `--apply` with no named entry it could shorten. |

## See also

* [memory-sync.md](../memory-sync.md)
* [forge-memory-check](forge-memory-check.md), which says when to run
  this
