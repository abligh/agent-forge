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
`selected N of M (P%)`, to stderr, for the check's log. The tool reads
only git and the table, and needs no network.

A project passes a base from its head check only: its trains, default
branch and nightly run every unit, so a wrong entry costs a red train,
never a red default branch.

## `check`

Holds the working tree's table to its units: every unit the glob finds
has an entry, every entry names a unit, every line parses. Cheap, so a
project can run it in the check itself.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `--units NAME` | | The table, by its name under `units`. |
| `--base REF` | | `select`: the branch the head will merge into, such as `origin/main`. |
| `--head REF` | `HEAD` | `select`: the head to judge. |
| `--why` | off | `select`: say what selected each unit, on stderr. |

## Exit status

0 done (for `select`, `ALL` included); 1 `check` found a fault; 2 the
configuration has no such table or can't be read. `select` still
prints `ALL` then, so a runner that reads stdout runs every unit.
