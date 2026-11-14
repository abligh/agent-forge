# 0054: Urgent issues and bugs go first

* Status: approved (Alex Bligh, 2026-11-14, in XMPPEngineer's session: "Please could you make that change?")
* Author: XMPPEngineer
* Date: 2026-11-14

## Summary

The router lists an agent's items, and offers an idle agent its issues,
in four tiers, oldest first within each:

1. **Urgent.** An issue carrying `labels.urgent` that the project's
   `owner` put on; a pull request carrying it, or closing such an
   issue. The wake's line ends `(urgent: first)`.
2. **A red nightly's fixes** (designs/0028), as before, and with
   `router.red_first` the red-build issues (designs/0039).
3. **Bugs.** An item carrying `labels.bug`, or a pull request closing
   an open issue carrying it.
4. **The rest.**

The urgent label carries into the fix. Each sending run puts
`labels.urgent` on every open pull request that closes an issue the
owner marked urgent, so the build check and the Merger, which already
take an urgent pull request first, take the fix first too.

## Why

Until now the owner had no way to say "this issue before everything
else". The router listed items by number, a red nightly's fixes aside,
and offered an idle agent its oldest issue (red builds first with
`red_first`). Nothing put bugs before other work, although agents were
expected to.

## Who may make an issue urgent

Only the owner. Anyone with triage access can add a label, so the
rules read the issue's timeline: the label counts only when the last
time it went on, the owner put it on. Put on by anyone else, it is
ignored: the issue is ranked as if it were not there, and nothing is
carried to its fixes. A pull request's own urgent label is read as
before (the Merger and the build check count it whoever put it on), and
the label the router carries onto a fix is put on by the Merger's App.

## Configuration

* `labels.urgent`, already used for pull requests, now also turns on
  the urgent tier for issues and the carrying to fixes. A project
  without it is unchanged.
* `labels.bug` (new, default none: off) names the bug label, such as
  `bug`.
* Rules modules other than the default can rank items with a `rank(item,
  ctx)` function returning the tier and end lines with
  `first_mark(number, ctx)`; without them the router keeps only the red
  nightly's fixes first.

## Not done

* The carried label is not taken off a pull request when the owner
  takes it off the issue: whoever takes it off the issue takes it off
  the fix too, by hand.
* Nothing ranks the Merger's or the build check's own queues by bug: a
  bug fix's pull request goes first in the router's wakes, not in the
  merge queue.
