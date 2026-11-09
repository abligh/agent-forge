# forge-approval-scope

`forge-approval-scope` says whether a branch adds text to an already
approved design document without saying it is an amendment. A design's
`Status: approved (<person>, <date>)` line carries no scope, so text
merged after the approval silently inherits it, and the merged document
reads as approved in full. The tool looks only at the commits a branch
adds, which is where the mistake happens and can still be fixed. It
doesn't judge whether an amendment is right, only that it is declared;
the remedy is one clause.

## Usage

    forge-approval-scope                     origin/<default branch>..HEAD
    forge-approval-scope BASE..HEAD          any range
    forge-approval-scope --history FILE      every post-approval commit

An agent runs it before pushing a branch that touches a design. A
project can also run it in CI or as a check step, on the range a pull
request adds:

```sh
forge-approval-scope origin/main..HEAD
```

It takes a range the way `forge-commit-identity` does, so it can also be
a Merger gate, an entry in `merge.gate`:

```json
"merge": {"gate": [["forge-approval-scope", "{base}..{head}"],
                   ["forge-commit-identity", "{base}..{head}"]]}
```

It needs a full clone; a shallow one is refused.

## Options

| Option | Default | Meaning |
| --- | --- | --- |
| `range` | `origin/<default_branch>..HEAD` | The commits to judge, as `git rev-list` reads them. |
| `--history FILE` | none | Instead of judging a range, list every commit that touched `FILE` after its approval line landed. `FILE` is a path in the repository. |
| `-h`, `--help` | | Print the usage and exit. |

## Environment

| Variable | Meaning |
| --- | --- |
| `FORGE_CONFIG` | The configuration file itself. |
| `FORGE_PROJECT_ROOT` | The project's checkout, used when `FORGE_CONFIG` is not set. |

With neither set, the configuration is found from the git checkout
around the working directory. The configuration is optional: if it
can't be loaded, the defaults below are used.

## Configuration

It reads [`default_branch`](../config.md#keys) (default `main`) for the
default range, and [`docs.designs_dir`](../config.md#keys) (default
`designs`) for where designs are. A design is any `.md` file under
`designs_dir`, a path from the top of the repository. A project whose
designs are in `docs/designs` sets `"docs": {"designs_dir":
"docs/designs"}`.

## What it does

**What counts as approved.** A design is approved at a commit when, at
that commit, a line of it matches `Status: approved` or `Status:
implemented`, case insensitive. The key and the word may be emphasized
(`* **Status**: approved`, `Status: **approved**`). An implemented
design counts as approved. Other statuses, such as `draft`, don't
count.

**What it judges.** For each commit in the range, oldest first, and
each design file it touches that is approved *at that commit*, it takes
the lines the commit adds. A document approved later does not make its
own earlier drafting suspect. Then it leaves out:

* the `Status:` field (its line and its indented continuation lines)
  on the commit that changes the status, to approved or to implemented.
  The approval itself is not an amendment. Other text that commit adds
  is still judged;
* the `Implemented:` field and its continuation lines, whenever they are
  written: recording what shipped is allowed;
* blank lines.

If anything remains, the commit adds text to an approved design.

**What counts as saying so.** The commit's message, or one of the added
lines, contains a word `amend`, `amended`, `amendment`, `amending` or
`amends`, in any case, such as "Amended 2026-10-09, after approval".

Each unmarked finding goes to stderr:

    docs/designs/0007-retry.md: 1a2b3c4d5e6f adds 4 line(s) to an approved design without naming it as an amendment

and a summary to stdout, with the size of the range so an empty range
can't pass for a clean one. The middle number counts commit and design
pairs:

    forge-approval-scope: 5 commit(s) examined, 1 touch an approved design, 1 unmarked

**`--history FILE`** finds the first commit at which `FILE` reads as
approved, and lists every later commit that touched it, up to `HEAD`,
as `<sha> <commit date> <subject>`:

    approved by 9f8e7d6c5b4a
      <full sha> 2026-10-12T10:00:00+00:00 Amend the retry limit
    forge-approval-scope: 1 commit(s) after approval

**A shallow clone is refused**, as is a repository where
`git rev-parse --is-shallow-repository` can't say whether it is
shallow. Against a grafted history every commit below the graft reads
as added on the branch, so the answer would be invented rather than
merely empty.

## Exit status

| Status | Meaning |
| --- | --- |
| 0 | Nothing unmarked (or, with `--history`, the history was listed). |
| 1 | A commit adds text to an approved design without marking it. |
| 2 | The range could not be read or counted, the clone is shallow (or git couldn't say), `--history` found no approval line in the file, or a usage error. |

## See also

* [Configuration: `docs`](../config.md#keys): `designs_dir`.
* [forge-decision](forge-decision.md): whether the owner decided
  something.
