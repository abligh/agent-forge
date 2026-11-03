# 0043: Tell every check instance which pull request is first

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-03

## Summary

With `checks.merger_first`, `forge-pr-check` runs the Merger's pull
request before the others. It learns that pull request from
`next.json` in the Merger's state directory, so only an instance on
the Merger's own host can do it. An instance on another host
(designs/0021), such as a macOS check, finds no `next.json` and runs
its queue oldest first, however long the Merger waits for its verdict.

This design has the Merger put a label, `labels.merger_next`, on the
pull request `next.json` names, and take it off the one before.
Every instance already reads the open pull requests' labels each tick,
and a labelled pull request is treated as the Merger's when there is
no fresh `next.json`. Off by default.

## Evidence

On one day, a pull request was given right of way (designs/0037) at
12:57Z, holding 11 approved pull requests that conflicted with it. It
needed its build check and a second instance's macOS check. Its head
arrived last in the macOS queue, behind 10 others, at about 30
minutes a run. Three pull requests merged in the hour after 12:10Z,
against 8–12 an hour that morning. The project's merge-time and
turnaround watches fired. The head was put first by hand at 13:18Z.

## Design

### The Merger

With `labels.merger_next` set, each live tick that writes `next.json`
with a pull request makes sure that pull request, and no other open
one, carries the label:

* it adds the label to the pull request named, if it lacks it;
* it takes it off any other open pull request that has it.

When `next.json` names no pull request, the label comes off wherever
it is. The Merger reads labels with the pull requests it already
fetches, so a tick whose front hasn't changed makes no calls. A dry
run changes no labels. GitHub creates the label the first time it
is added.

### The check

`merger_first` reads `next.json` as today. When there is none, or it
is older than `MERGER_FRESH`, the open pull request carrying
`labels.merger_next` is the Merger's pull request, if exactly one
does. The pulls API that each tick already reads gives the labels, so
this costs no call. Everything that follows from the Merger's pull
request (its head going first, `merger_head_waits`) is unchanged.

`ahead.json`'s predictions stay local: they name commits in the
Merger's checkout, which another host doesn't have.

### Configuration

`labels.merger_next`, a label name, default unset. Unset, neither
tool changes. The configuration check refuses it without
`checks.merger_first` (or `merge.test_ahead`, which implies it).

## Trade-offs

* **A stale label.** If the Merger stops, the label stays where it
  was, and that pull request keeps going first on other hosts. It is
  one pull request's place in a queue, never a merge, and the
  Merger's stall alert already reports a stopped Merger.
* **Label events.** Each change of front adds and removes a label:
  two calls, and two timeline entries on the pull requests. A
  project whose workflows run on `labeled` should leave this off or
  filter the label.
* **Alternatives not chosen.**
  * A commit status on the front head would need a call per waiting
    head on every tick to read, and shows in each pull request's
    checks.
  * A repository variable is one call to read, but needs new App
    permissions on both sides.
  * Copying `next.json` to the other host would need a path between
    hosts that the check doesn't otherwise have.

## Non-goals

* Sending `ahead.json`'s predictions to other hosts.
* Changing the order within an instance's queue beyond the Merger's
  pull request.
