# 0065: A head runs the test units its changes reach

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-24
* Issue: #568

## Summary

Some build-check steps are a pile of independent test files: each one
reads a few paths of the repository, and most heads change none of
them. Such a step runs every file on every head today.
[designs/0048](0048-nightly-steps-run-on-the-pull-requests-that-can-break-them.md)
scopes whole steps by path; this design scopes the files inside a step.

1. **`forge-units select`** reads a project's table of test units and
   the paths each reads, takes a base, and prints the units a head's
   changes reach. It fails closed: a run-everything path, a path no
   entry places, or a unit with no entry selects everything.
2. **`forge-units trace`** runs each unit under file-access tracing and
   fails when a unit reads a path its entry doesn't cover. A project
   runs it in its nightly, never on a head, so a stale entry is found
   within a day, not by a red train.
3. **The project keeps the table and calls the tool** from its own
   runner. agent-forge never runs the tests itself on a head; it says
   which.

Trains and the default branch still run every unit, so a wrong entry
costs a red train and a hand-back, never a red default branch.
Everything is off until a project names a table in its configuration.

## Why

One project, 2026-11-20 to 2026-11-24:

* **Its tools-test step runs about a hundred Python test files on
  every head**: a median 289 CPU-s and 40 s of wall on the check host,
  run before the crates' tests, so its wall is on every head's path.
* **A median merged pull request needs about a quarter of it.**
  Replayed over 914 merged pull requests with two agents' hand-traced
  tables of what each file reads, a median one needs 46 of 187 CPU-s
  (measured each file alone on another host; mean 71, p75 65); 190 of
  the 914 change a path that must run everything. By ratio that is
  about 218 CPU-s of the 289 saved on a median head, and about 30 s of
  the 40 s of wall: estimates, not measurements.
* **Selection would have missed almost nothing.** Of 38 heads red in
  that step, 22 changed a run-everything path. Of the other 16, 15
  failed in a file the tables list as reading what the head changed.
  The one it would have passed was a timing assertion whose next head
  was green 44 minutes later: a flake.
* **A hand-kept table can't be proved complete.** The project's own
  proposal said so: running a file with only its listed inputs present
  misses a file that skips when an input is absent (4 of 79 did), and
  one that asserts some text is *absent* (it passes with its input
  removed). One of its widest readers was found only by a trace.

Tracing what each unit opens settles the last point. A test asserting
that text is absent still opens the file to look; a test that skips
on a missing input still asks for it. So the trace sees every read,
whatever the assertions do, and a nightly can hold the table to it.

## The design

### The table

A text file in the project, named by the configuration, one line each:

* `unit UNIT INPUT...`: a unit (a path relative to the root, usually a
  test file) and the paths it reads, as prefixes or globs (`*` crossing
  `/`); `-` for none outside the run-everything paths;
* `placed DIR`: a directory whose paths are read only as the entries
  say. A changed path outside every `placed` directory and every
  run-everything path selects everything;
* `everything GLOB`: a path whose change selects everything (the test
  harness, its configuration, the build files);
* `#` starts a comment.

Units are found by `units.glob` in the configuration (such as
`tools/test_*.py`). A unit the glob finds with no `unit` line is always
selected, and `forge-units check` reports it; an entry naming no file
is an error.

### `forge-units select --units NAME --base REF [--head REF]`

Takes the paths changed between the merge base of `REF` and the head
(default `HEAD`), as `git diff --name-only` gives them, and prints:

* `ALL <why>`, one line, when a path matches `everything`, or no
  `placed` directory and no `everything` glob holds it;
* otherwise each unit with an input that holds a changed path, and
  each unit with no entry, one a line;
* nothing, with exit 0, when no unit's inputs changed. The project's
  runner then passes and says so.

`--why` adds, after each unit, the changed path that selected it, for
the check's log. The tool reads only git and the table; it runs no
test and needs no network.

### `forge-units trace --units NAME -- COMMAND...`

Runs `COMMAND` once for each unit, with `{unit}` replaced by the unit's
path, under `strace -f -e trace=%file,execve`. Each unit runs in its
own process, however short, so each read belongs to one unit.

For each unit it collects every path under the repository root that
was opened, listed or stat'ed, ignoring the unit's own file, the
`everything` paths, `.git/` and the paths in `units.trace_ignore`
(caches, temporary output). A directory listing counts as reading the
directory. A read no input of the unit covers is a finding.

**git is a reader too.** A unit that runs `git ls-files`, `git grep`,
`git diff` or `git show` reads through `.git/`, which the trace sees
only as the index. Each such `execve` is a finding unless its
pathspecs, read from its arguments, are covered by the unit's inputs.
One with no pathspec needs `*` as an input, which selects the unit for
every change.

It prints a line per finding, `UNIT reads PATH (not in its entry)`,
then the `unit` lines that would cover them, and exits 1 if there were
any. A unit that fails its own test is reported, not counted as a
finding: the trace judges the table, not the code.

### `forge-units check --units NAME`

Every unit the glob finds has an entry, and every entry a unit; every
`placed` and `everything` line parses. Exit 1 otherwise. Cheap, so a
project can run it in the check itself.

### Configuration

`units`, a map of names to:

* `table`, the table's path from the root (required);
* `glob`, the units (required);
* `trace_ignore`, globs the trace ignores (default none).

Inert until present. The check host needs `strace` for `trace` only.

### Statistics

`select` prints a last line, `selected N of M (P%)`, that a project's
runner can leave in its step's log; `forge-stats` reads it from the
check-head log when present, as `units_selected`, so the saving is
measured, not estimated.

## What a project does to use it

1. Write the table, and add `units.<name>` to its configuration.
2. Make its step's runner take a base and call `forge-units select`,
   running what it prints; with no base, everything, as now.
3. Pass the base from the head check only. Trains, the default branch
   and the nightly run everything.
4. Add a nightly step: `forge-units trace --units NAME -- <runner>
   {unit}`. Its first runs will name entries to fix.
5. Optionally run `forge-units check` in the check.

## Trade-offs and failure modes

* **A stale entry between nightlies** still lets a head pass a unit it
  should have run. A train then goes red and the pull request is handed
  back. That costs a train, and is what the nightly trace bounds to a
  day.
* **A unit whose reads depend on what it finds** (it walks a directory
  and opens what is there) is traced on the tree of that night only. A
  later head that adds a file in a directory the unit lists is still
  covered, since the listing put the directory in its entry.
* **Reads outside the repository** (the network, `/tmp`, the home
  directory) are not judged. A unit that reads a sibling checkout or a
  downloaded fixture belongs in `everything` or needs its own rule.
* **strace slows a unit** by a factor of about two to five for
  file-heavy tests. Run once a night, that costs a few CPU-minutes.
* **strace under a container** needs ptrace of its own children, which
  the usual container runtimes allow. On a host where it can't,
  `trace` exits 2 with the reason, and the nightly step is red until
  that is fixed.
* **Rejected: listing what to leave out.** It fails open for a path
  nobody thought of.
* **Rejected: tracing every head** and reusing a unit's earlier result
  when nothing it read changed. It costs the trace on every head,
  which is what was being saved.
* **Rejected: Python audit hooks instead of strace.** They see a
  Python test's own opens but not its subprocesses' (git, compilers),
  and the units are not all Python.

## Steps

1. `forge-units select` and `check`, with the configuration key and
   tests: every kind of path; an unplaced path; a unit with no entry;
   no changes; `--why`.
2. `forge-units trace`, with tests over small fixture units that read
   a listed path, an unlisted one, a directory, through git with and
   without a pathspec, and one that asserts a file's text is absent.
3. `forge-stats` reads `units_selected`.
4. docs/config.md and a release.

## Acceptance

* A project's head check runs the units `select` prints, and its
  trains everything.
* Its nightly trace is green for a week, and any finding it made
  before that was fixed by one table line.
* `units_selected` shows the saving on real heads.

## Not in this design

* Selecting a project's compiled tests by crate or package. Projects do
  that themselves today, from their build tool's own graph.
* Changing which build-check steps run (designs/0048 does that).
