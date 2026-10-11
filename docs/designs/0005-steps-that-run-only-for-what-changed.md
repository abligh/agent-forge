# 0005: Steps that run only for what changed

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-11

## Summary

A project's build check runs every step on every head: formatting,
lint, the whole test suite, the docs check. A pull request that changes
one design document waits five minutes, mostly for tests of code it
didn't touch. On one project the median check took 241 seconds, of
which the docs check needed three. The project's owner wants the median
under ten seconds, and most pull requests change one area.

This proposes that `forge-pr-check` learns what each step depends on,
instead of being told:

* **Each full run on the default branch records the files each step
  read**, by tracing the step's file opens. That covers the build's
  inputs and the files the tests read at run time, whatever the
  language or runtime.
* **A pull request runs the steps that read a file it changes**, and
  every step whenever the record can't answer.
* **Every step is told what the head is compared with** (`CHECK_BASE`),
  and where the record is (`CHECK_DEPENDS`). So a project's own test
  runner can make the same choice per test binary.

A side effect is wanted: the record shows couplings nobody meant, such
as code or tests that read design documents. A change to a design then
runs those tests, and the check says which step read which document, so
the project can remove the coupling instead of living with it.

## Goals

* **No hand-kept lists.** What a step depends on is measured, so it
  can't fall out of date.
* **Selection fails closed.** A change the record can't vouch for runs
  every step.
* **Couplings are visible.** The record can be read, and the posted
  description names what made a step run.
* **A reused verdict is never broader than the run it reuses.**

## Non-goals

* **Knowing a project's languages.** agent-forge sees only files
  opened. Finer selection (per crate, per test binary) is the
  project's own runner's job, from the same record.
* **Changing what the Merger requires.** The status context and its
  meaning stay the same.

## Design

### Recording what each step reads

`checks.depends: true`. When the default branch's head moves, the
worker queues that head, like a pull request's, and runs every step
under `strace -f -e trace=open,openat,openat2,execve,stat,newfstatat,`
`statx` (more of the same family as Linux adds them). From each step's
trace it keeps the paths inside the worktree that the step opened,
executed or looked for, including those it looked for and didn't find.
A file that appears later can then change what the step does.
Build output (the step's cache directories, `checks.caches`) is left
out.

The record is written to the state directory as
`depends/<tree>.json`: `{step: [path, ...]}`, plus the steps' digest
and the time. The worker keeps the newest records for the last 14 days.
It also posts the head's status on that commit and logs `MAIN-GREEN` or
`MAIN-RED`. A red full run records nothing, so the last green record
stays in use.

A traced step runs more slowly. Only these runs on the default branch
are traced, and one traced run serves every pull request based on it
or later. A head of the default branch is started after waiting pull
requests, except that the newest one goes first once it has waited
`depends_minutes` (default 30).

### Choosing steps for a pull request

The worker lists the paths that differ between the head and its merge
base with the default branch. It finds the newest record whose tree is
that merge base's, or failing that the newest record from a commit that
is an ancestor of the merge base, with every path changed in between
added to the changed list. A step runs if:

* it read, executed or looked for any changed path, or any path under
  a changed directory;
* the change adds a path in a directory the step listed or read; or
* `always: true` is set on it, for steps whose cost is negligible or
  whose result depends on more than files.

Every step runs, and the description says why, when:

* there is no usable record, or it has another steps digest;
* the diff can't be computed;
* `strace` isn't installed on the host, which the worker logs once per
  run as `NO-TRACE`.

### The comparison point, for runners that narrow further

Every step's environment gains `CHECK_BASE`, the merge base, and
`CHECK_DEPENDS`, the record's path. Neither starts with `FORGE_`, so
neither is dropped. A project's test runner can record its own
per-binary dependencies the same way: compiled inputs from the build
tool's dependency files, run-time reads from its own trace on the
default branch. It can then skip binaries a change can't reach. When
there is no record, both variables are unset and the step must test
everything.

### What is posted and recorded

The verdict records the steps that ran, the steps skipped, and for each
step that ran the first changed path that made it run. The description
reads, for example, `green on <host>: 2 of 6 steps (doc-check:
designs/0042.md; rust: designs/0042.md via include_str)`. When a step
ran only because of a path that it reads and that isn't source code,
the reader can see the coupling. The `via` part is added when the trace
shows it.

### Matching a tree tested before

With `test_ahead`, a head whose tree is green here is not run again. A
selective earlier run counts only if every step this head would run
also ran then, under the same `CHECK_BASE`.

## Config and file-format shape

```json
"checks": {
  "depends": true,
  "depends_minutes": 30,
  "steps": [
    {"name": "fmt-check", "run": ["make", "fmt-check"], "always": true},
    {"name": "test", "run": ["make", "test"]}
  ]
}
```

`docs/config.md` defines `depends`, `depends_minutes`, `always`, the
record's format, `CHECK_BASE` and `CHECK_DEPENDS`.

## Trade-offs and failure modes

* **A step that depends on something it doesn't open**, such as the
  network, the clock or the toolchain on the host, isn't selected by a
  change to it. Toolchain upgrades already make verdicts age out
  (`test_ahead.max_age_hours`), and `always` covers the rest.
* **A step that reads nearly everything**, such as a formatter walking
  the tree, runs on every change. That is correct, and such steps are
  usually cheap. The trace shows which ones they are.
* **A record lags the default branch by one run.** A change based on a
  head that has no record yet uses an older record, plus the files
  changed since, so it errs towards running more.
* **The default branch gets more runs**, one per merge. With test-ahead,
  many merged trees have already been tested green, but they still
  need a traced run, because a match records no reads.
* **`strace` needs `ptrace` on the step's processes.** That is allowed
  for a user's own children under the usual Yama setting (1); hosts
  with stricter settings fall back to running everything.

## Testing strategy

agent-forge's tests cover:

* the trace parser: open, exec and failed lookups, paths outside the
  worktree, cache directories left out;
* record writing and retention, and red runs that don't overwrite;
* selection: a matched path, a new file in a read directory, `always`,
  the fallback to an ancestor's record plus the changes since, and
  every fall-back-to-all case;
* `CHECK_BASE` and `CHECK_DEPENDS` in the steps' environment;
* the description and the verdict record, and tree matches refused for
  a narrower earlier run;
* ordering the default branch's heads against pull requests.

In a project, a week of selections compared with what full runs said:
every pull request that a skipped step would have failed is a bug in
the record.

## Implementation plan

1. Recording on the default branch (`depends`), with nothing selected
   yet. The records are compared with what the project expects.
2. Selection by step, `always`, the two variables and the description.
3. A project turns it on in its configuration, in a pull request its
   owner merges, and installs `strace` on the host.
4. Optionally, a project's own runner selects per test binary from the
   same record.

## Open questions

* **Default for `depends_minutes`.** 30 minutes suggested.
* **Retention.** Fourteen days of records suggested.
