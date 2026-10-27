# 0036: Match the default branch to its prediction, with depends on

* Status: approved (Alex Bligh, 2026-10-27,
  https://github.com/abligh/agent-forge/issues/160#issuecomment-6442838940)
* Implemented: v0.4.129, off until configured.
* Author: XMPPEngineer
* Date: 2026-10-27

## Summary

With `checks.main: "every"` (designs/0011), each new default-branch head
gets a full run, unless a green run here already passed its tree. That
run is usually the Merger's prediction (`merge.test_ahead`), so 0011
expected the extra cost to be close to nothing.

With `checks.depends` on, that match never happens in practice. The
worker matches a default-branch head only once its tree already has a
dependency record (`records/<tree>.json`). A prediction is a selected
run, not a traced one, so it writes no record, and the tree that merges
never has one. Every merge therefore pays a full traced run of every
step.

On one project over 48 hours, this came to 176 full runs, a median of
704 s each: 19.3% of all the check's CPU. No main head was matched in
the last 24 hours (217 queued, 128 started, 0 `MAIN-GREEN … matched`).
In the same window, 946 pull-request heads were matched.

designs/0018 part A (`reuse.trace_matched_main`) was meant to keep the
records fresh by tracing a head that *would be matched*, at most once
every `depends_minutes` per slot. It never gets the chance: the record
rule stops the match before 0018 is asked.

## Design

### A. The record rule gives way to 0018

A new switch, `checks.reuse.match_main_unrecorded: true` (refused
unless `trace_matched_main` is on). With it, a default-branch head
whose tree a green run here passed is matched without its tree having a
record. 0018's rule then decides whether it runs traced after all:
- if `main_needs` finds a step whose record is missing or stale at that
  tree;
- and no slot has run a traced default-branch run in place of a match
  in the last `depends_minutes`.

That last limit is host-wide with this switch, where 0018's is per
slot. Per slot, six slots could trace twelve times an hour, which is no
saving at a project's merge rate.

Without the switch, nothing changes: the record rule still applies, as
it does today.

The records stay as fresh as `depends_minutes` allows, which is what
0018 decided was enough: at most one traced run every half hour,
against one per merge today.

### B. Only the stale steps, when a head runs traced in place of a match

Not proposed now. A traced run in place of a match could run only the
steps `main_needs` names, as `checks.fresh` does. A's saving comes
first, so B waits for a measurement after A.

## What it costs

- **A merge is no longer checked in full on its own.** The merged tree
  has been checked by its prediction, which ran the steps and tests its
  changes selected, not every test. A test that selection wrongly
  skipped is then caught at the next traced default-branch run, at most
  `depends_minutes` later per slot, not at the merge that broke it.
  `main_red_issue` still reports that run's red, as 0011 does. This is
  the same trade 0011 made for a matched head without `depends`.
- **Records lag the default branch by up to `depends_minutes`.** A
  pull request's selection reads the newest record; a step whose inputs
  moved since then counts as stale and runs (0009), so a late record
  costs runs, not correctness.

## Expected saving

Most of the 19.3%. A project that merges about ten times an hour keeps
at most two traced runs an hour, so at least four in five of these runs
go: about 15% of check CPU or more. B would save part of the rest.

## Rollout

The code lands off by default. A project turns it on with
`match_main_unrecorded: true` in its own configuration.

Measured after a day: the share of check CPU on default-branch runs,
`MAIN-GREEN … matched` and `MAIN-TRACE` counts, and merge time.
