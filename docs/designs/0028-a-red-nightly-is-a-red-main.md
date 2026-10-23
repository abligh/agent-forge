# 0028: A red nightly is a red main

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-23

## Summary

A project's nightly run tests things the build check doesn't, such as
slow tests, optimised builds and real services, and it makes the
project's builds for download. When it fails, `main` is red for all of
those, but nothing says so. The build check's `main_red_issue`
(designs/0011) covers only the build check.

This design gives the nightly the same treatment as a red `main`, and a
bit more, since a nightly takes hours and runs once a day:

1. one issue while the nightly is red, owned by whoever owns the failed
   steps;
2. pull requests that fix it go first;
3. the nightly is run again by itself until it is green;
4. a run after a red one fails fast;
5. it is escalated if it stays red.

It's a new tool, `forge-nightly`, run from cron, and off unless a
project sets `nightly`.

## Evidence

On one project the nightly was red every night from 14 October to 23
October, and it never made one of its builds for download. The failures
were real:
- a test whose wait was too short;
- a sharing bug that could write a blank document over the stored file;
- a security advisory in a dependency;
- thresholds that drifted after a set of merges.

Each was fixed within hours of an agent looking, but nothing made one
look. A red night was noticed only when someone read the run, and
retries were dispatched by hand, one at a time, two hours each.

On 23 October three runs dispatched by hand in a row each failed on a
different cause. Each failure showed only at the end of a two-hour run,
though the step that failed had failed the time before as well.

## Design

### Configuration

```json
"nightly": {
  "workflow": "ci.yaml",
  "job": "build-and-test",
  "owners": {"test-slow": "Dev1", "vet-advisories": "Dev2"},
  "labels": ["bug"],
  "retry_hours": 3,
  "escalate_hours": [24, 48],
  "as": "Merger"
}
```

`workflow` and `job` name the nightly's workflow and the job that
decides it. `owners` maps a step name (or a glob over step names) to the
agent who owns it; a step it doesn't name belongs to
`router.owners.triage`, which is required. `as` is the App that files,
dispatches and comments (default `merge.merger`).

### 1. One issue while the nightly is red

Each cron tick reads the newest run of `workflow` on the default branch,
dispatched or scheduled. When one ends red and no issue is open, it
opens one, titled `The nightly is red at <sha>`. The issue has:
- for each failed step: its name, its owner and the end of its output
  (as `red_comment` quotes it, at most 12,000 characters a step);
- the commits since the last green nightly;
- `labels`, plus `<labels.owner_prefix><owner>` for each owner of a
  failed step, which routes the issue to her at once.

Each later red run comments once, with the same detail, and adds the
labels of new owners. The first green run comments and closes it. State:
`nightly.json` in the tool's state directory: the issue, the last run
judged, the last green run and when it ended.

### 2. Fixes go first

While the issue is open, a pull request whose body, title or commits
close it (by the rule `forge-closing-keywords` applies) is treated as if
it had `labels.urgent`:
- `forge-pr-check` checks its head first;
- the Merger takes it first;
- the router routes its review first.

The label stays a person's. This is the same treatment, read from the
issue rather than from a label, so an agent can give it without being
able to set `Urgent`. It ends when the issue closes.

### 3. Run again by itself until green

While the issue is open, the tick dispatches the workflow on the default
branch (`POST .../actions/workflows/<workflow>/dispatches`) when no run
of it is queued or in progress and either:
- a pull request that closes the issue has merged since the last run
  started; or
- `retry_hours` have passed since the last run ended.

It never runs two at once. Each dispatch is a comment on the issue,
saying the commit and why. A green run ends the retries, makes the
project's release as any green nightly does, and closes the issue.

### 4. A retry fails fast

A dispatched retry passes the input `first`: the failed steps of the
last red run, by name. The project's workflow decides what to do with
it. The intended use is to run those steps first and stop at the first
one that fails again, so that a fix that didn't work shows in minutes,
not hours. A workflow that doesn't declare the input is dispatched
without it, as today.

### 5. Escalation

While the issue stays open:
- at the first of `escalate_hours`, the tick messages every developer
  agent, through the relay;
- at the second, it messages the project's owner.

It comments on the issue at each one, so the issue holds the record. A
new `forge-stats` figure, `nightly_red_hours` (the hours since the last
green nightly ended, 0 while the newest is green), lets
`forge-stats-watch` treat it like any other figure.

## What a project changes

- It sets `nightly`, with `owners` naming who owns which steps.
- It runs `forge-nightly` from cron every five minutes.
- Its workflow takes the `first` input, if it wants retries to fail
  fast.
- Builds on other hosts that should count belong in the nightly's run,
  as jobs, so that their failure makes it red too. This design doesn't
  watch them anywhere else.

## Not covered

- Holding merges while the nightly is red. Ordinary work continues;
  only the fixes are put first.
- Deciding whether a red step is flaky or a real bug. The owner decides
  that, on the issue.
