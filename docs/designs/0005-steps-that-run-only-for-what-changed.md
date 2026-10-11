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

This proposes two things for `forge-pr-check`:

* a step can name the paths it depends on (`paths`), and runs only when
  the head changes one of them;
* every step is told what the head is compared with, so a project's own
  step can narrow further, for example to the packages that changed and
  the packages that depend on them.

It also proposes a full run of every step on each new head of `main`.
That way a selection that was too narrow is caught within one merge,
not when it next matters.

## Goals

* **A change is checked by the steps it can affect**, and quickly when
  that is few of them.
* **Selection fails closed.** If the changed paths can't be listed, or a
  path matches no step's patterns, every step runs.
* **Nothing narrower than a full run reaches `main` unnoticed.** Each
  new head of `main` gets every step, and the result is posted on that
  commit.
* **A reused verdict is never broader than the run it reuses.** A
  matched tree counts only if that run skipped nothing this head would
  run.

## Non-goals

* **Working out a project's dependency graph.** agent-forge doesn't
  know what a crate, a package or a module is. A project that wants
  finer selection writes a step that reads the comparison point and
  decides for itself.
* **Changing what the Merger requires.** The status context and its
  meaning stay the same: green for what the head changes.

## Design

### Which paths changed

Before the steps run, the worker lists the paths that differ between
the checked-out tree and the merge base of the head with the default
branch it fetched (`git diff --name-only <base> HEAD`). For a
`test_ahead` prediction, the comparison is with the default branch the
prediction was built on. Renames count as both paths.

### `paths` on a step

A step may carry `"paths": [pattern, ...]` in gitignore syntax (the
syntax `merge.code_owners` already reads). A step with `paths` runs
only when a changed path matches one of its patterns. A step without
`paths` always runs, as today. So the existing configurations don't
change.

`checks.unmatched` sets what happens to a changed path that no step's
`paths` matches. It is `all` by default: every step runs, because the
project hasn't said what checks that path. `none` runs only the steps
without `paths`.

The changed paths can be unavailable: the merge base is missing, or the
diff fails. Then every step runs, and the run's summary says so.

### The comparison point, for steps that narrow further

Every step's environment gains `CHECK_BASE`, the commit the changed
paths were listed against. It doesn't start with `FORGE_`, so it isn't
one of the settings dropped from a step's environment. A project's own
step can use it to run, for example, `cargo test` for the packages that
changed and the packages that depend on them. When the changed paths
are unavailable, `CHECK_BASE` is unset, and a step that reads it must
then test everything.

### What is posted and recorded

The verdict records the steps that ran and the steps skipped by
selection. The posted description says so:
`green on <host>: 2 of 6 steps, for what changed`. A run of every step
reads as today.

### Matching a tree tested before

With `test_ahead`, a head whose tree is green here is not run again. A
selective earlier run counts only if every step this head would run
also ran then, and if the earlier run's `CHECK_BASE` is the same as this
head's. A narrowed step's result depends on both, so either can make
the earlier run too narrow.

### A full run on each new head of `main`

`checks.full_on_main: true`. When the default branch's head moves, the
worker queues that head, like a pull request's, and runs every step on
it, ignoring `paths` and without `CHECK_BASE`. It posts the status on
that commit and logs `MAIN-GREEN` or `MAIN-RED`. A pull request's head
is started before a waiting head of `main`, except the newest `main`
head, which goes ahead of it once it has waited
`full_on_main_minutes` (default 30). Red on `main` means a selection
was too narrow or a test is flaky. It is posted, so the project's
existing alerting (`merge.stall_alert`, or a person) sees it.

## Config and file-format shape

```json
"checks": {
  "full_on_main": true,
  "unmatched": "all",
  "steps": [
    {"name": "docs", "run": ["make", "doc-check"]},
    {"name": "rust", "run": ["make", "test-rust-affected"],
     "paths": ["*.rs", "Cargo.*", "crates/", ".cargo/"]},
    {"name": "web", "run": ["make", "test-web"], "paths": ["web/"]}
  ]
}
```

`docs/config.md` defines `paths`, `unmatched`, `full_on_main`,
`full_on_main_minutes` and `CHECK_BASE`.

## Trade-offs and failure modes

* **A pattern list that is too narrow lets a break merge.** The full run
  on `main` catches it at the next merge, and `unmatched: all` keeps a
  path nobody listed from going untested. Projects should list what a
  step depends on generously: the build files, the toolchain files, the
  step's own scripts.
* **A step whose result depends on files outside its patterns**, such as
  a lockfile or a shared configuration file, must list them. This is
  the same discipline a monorepo's CI path filters need.
* **Main gets more runs:** one per merge. With depth-2 test-ahead, most
  merged trees are already green and match, so the extra run is often a
  match, not a build.
* **The step digest covers `paths`.** Changing a step's patterns
  re-runs, rather than reusing, verdicts from before the change.

## Testing strategy

agent-forge's tests cover:

* which steps run for given changed paths: matched, unmatched under
  `all` and `none`, and paths unavailable;
* `CHECK_BASE` in each step's environment, and unset when the paths are
  unavailable;
* the description and the verdict record for a selective run;
* a tree match refused because the earlier run skipped a needed step,
  and one refused because its comparison point differs;
* `full_on_main` queueing, its ordering against pull requests, the
  status posted on `main`, and the log lines.

In a project, a week of verdicts compared with what a full run would
have said.

## Implementation plan

1. `paths`, `unmatched` and `CHECK_BASE`, off unless a step has
   `paths`, in one release.
2. `full_on_main`, off by default, in the same release.
3. A project turns both on in its configuration, in a pull request its
   owner merges.

## Open questions

* **Default for `full_on_main_minutes`.** 30 minutes suggested.
