# forge-units

`forge-units` says which of a step's test units a head's changes reach
([designs/0065](../designs/0065-a-head-runs-the-test-units-its-changes-reach.md)).
Some build-check steps are a pile of independent test files, each
reading a few paths of the repository, and most heads change none of
them. A project keeps a table of what each file reads; its step's
runner asks `forge-units select` which to run, and runs those.
agent-forge never runs the tests itself.

## Usage

    forge-units select --units NAME --base REF [--head REF] [--why]
    forge-units check --units NAME
    forge-units trace --units NAME -- COMMAND...

`NAME` is a table under the configuration's `units` section
([config.md](../config.md#units)).

## The table

A text file in the project, one line each; `#` starts a comment.

| Line | Meaning |
| --- | --- |
| `unit UNIT INPUT...` | A unit (a path from the root, usually a test file) and the paths it reads, each a prefix of whole path components or a glob whose `*` crosses `/`; `-` alone for none. |
| `placed DIR` | A directory whose paths are read only as the entries say. |
| `everything GLOB` | A path whose change selects every unit: the test harness, its configuration, the build files. |

For example:

```text
everything tools/harness.py
placed tools
placed docs
unit tools/test_links.py docs/ README.md
unit tools/test_parse.py tools/parse.py tools/grammar/*.txt
unit tools/test_version.py -
```

## `select`

Takes the paths changed between the merge base of `--base` and the
head (`--head`, default `HEAD`), as `git diff --name-only --no-renames`
gives them, so a renamed or deleted path counts by its old name as well
as its new one. It reads the table and the units from the head's tree,
and prints on stdout, one a line and nothing else:

* one line `ALL <why>` when a changed path matches an `everything`
  glob, or lies in no `placed` directory (a unit's own file excepted);
  also when no merge base can be found (a shallow clone, an unrelated
  base) or the table can't be read or parsed. It fails closed;
* otherwise each unit with an input holding a changed path, each unit
  whose own file changed, and each unit the glob finds with no entry;
* nothing when no unit is reached. The runner then passes and says so.

`--why` writes what selected each unit, and a last line
`selected N of M (P%)` (or `selected all: <why>`), to stderr, for the
check's log. `forge-stats` reads that line from the step's log as
`units_selected`, so a runner that wants the saving measured leaves
`select`'s stderr in it. The tool reads only git and the table, and
needs no network.

A project passes a base from its head check only: its trains, default
branch and nightly run every unit, so a wrong entry costs a red train,
never a red default branch.

## `check`

Holds the working tree's table to its units: every unit the glob finds
has an entry, every entry names a unit, every line parses. Cheap, so a
project can run it in the check itself.

## `trace`

Runs `COMMAND` once for each unit, with `{unit}` replaced by the unit's
path, under `strace -f -e trace=%file,%process`, each unit in its own
process so each read belongs to one unit. A project runs it in its
nightly, never on a head, so a stale entry is found within a day:

```sh
forge-units trace --units tools -- python3 -m unittest {unit}
```

For each unit it collects every path under the root that was opened,
stat'ed, listed, or asked for and not found (a test that skips when an
input is missing still asks for it; one that asserts some text is
absent still opens the file), and prints a line for each its inputs
don't cover, `UNIT reads PATH (not in its entry)`, then the `unit` line
that would cover them.

Not judged:

* the unit's own file, the `everything` paths, `.git/`, and the
  configuration's `trace_ignore` (an entry matches at any depth, so
  `__pycache__/` covers every such directory);
* a stat that finds a directory, or a readlink that finds no link:
  they resolve a path and read nothing in it;
* the unit's own directory and those above it, and a listing of a
  directory an input lies in;
* paths outside the root.

**git is judged by its command line.** A git process's own reads are
not judged; a reading subcommand (`ls-files`, `grep`, `diff`, `show`,
`log`, `ls-tree`, `cat-file`, `blame`, `archive`) must name pathspecs
the unit's inputs cover, resolved against the process's directory and
any `-C`. One with no pathspec needs the input `*`, which selects the
unit for every change. A git whose directory (after `-C`, or its
`--work-tree`) is outside the root works on another repository, such
as a test's scratch one, and is not judged at all. A git reached
through a helper script is still seen, since strace follows children.

A unit whose own command fails is reported on stderr, not counted: the
trace judges the table, not the code. The host needs `strace`, and
ptrace of its own children; where it can't trace, the tool exits 2
with the reason.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--units NAME` | | The table, by its name under `units`. |
| `--base REF` | | `select`: the branch the head will merge into, such as `origin/main`. |
| `--head REF` | `HEAD` | `select`: the head to judge. |
| `--why` | off | `select`: say what selected each unit, on stderr. |
| `COMMAND...` | | `trace`: the unit's runner, after `--`, with `{unit}` for its path. |

## Exit status

0 done (for `select`, `ALL` included); 1 `check` or `trace` found a
fault; 2 the configuration has no such table or can't be read, or
`trace` has no `{unit}` in its command, no strace, or can't trace.
`select` still prints `ALL` then, so a runner that reads stdout runs
every unit.
