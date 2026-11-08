# 0051: Work waiting for its nightly blocks nothing

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-08

## Summary

An issue whose pull requests have merged often stays open until its
owner has checked it on a nightly build. While it waits, its owner
labels it with `router.nightly_wake`'s label (designs/0040). The issue
is still open, so every `Blocked-by:` line that names it still holds.
The router then finds nothing actionable in the issues that build on
it: the idle offer, the hand-over count and rule 14 all count them as
blocked. While the nightly is red, nothing waiting for it can close.
So one red nightly stops the work that depends on any of it.

The code the blocker stands for is already on the default branch.
Whatever builds on it builds on that branch. Only the blocker's check
on a nightly build is still to come, and the dependent issue doesn't
wait for that.

So, with `router.nightly_wake` configured, **an open issue carrying its
label no longer blocks** for the router: a `Blocked-by:` line naming it
counts as satisfied, as one naming a closed issue does. `"unblocks":
false` keeps today's behaviour.

## What happened

The owner of one project asked why its agents were nearly idle with
266 issues open. At 22:05 that day the issues stood like this:

| State | Issues |
|---|---|
| Labelled as waiting for the nightly | 109 |
| Blocked by an open `Blocked-by:` | 100 |
| Deferred by their owner (`Defer-until:`) | 18 |
| Parked by the owner (a waiting label of his) | 20 |
| Waiting on someone outside | 8 |
| Actionable | 11 |

Of the 100 blocked issues, 47 led only to issues waiting for the
nightly, when each `Blocked-by:` was followed to its end. A further 9
led to both those and other open work. The nightly had been red since
the previous night. Each of its failures was fixed within the hour,
but each rerun takes about two hours. Meanwhile three of the
project's developer agents were idle, and the router's idle reports said
each had "nothing it can act on (6 issues blocked, 19 issues
waiting)".

Two examples:

* An issue for importing one more file type was blocked by the step
  of its design that reads the format. That step had merged and only
  waited for the nightly.
* An issue for a scale test named four steps of another design. All
  four had merged and waited for the nightly.

## Design

### 1. What counts as satisfied

A `Blocked-by:` line names a number. For the router's default rules,
that number no longer blocks when it is:

* closed or merged (as today); or
* **an open issue carrying `router.nightly_wake.label`**, and no
  label that parks it: a quiet label, or another label starting with
  the project's waiting prefix (`Status:waiting-`).

A pull request never counts under the second case. An open pull
request's work isn't on the default branch.

The parking exception keeps a blocker its owner has also put on hold
(for example, waiting on the project's owner) blocking. A blocker
whose nightly check has failed loses the label when its owner takes it
off to fix it (designs/0040 already asks her to). From then on it
blocks again. Nothing that was started on it is undone. The dependent
built on the default branch, which had the blocker's code all along.

### 2. Where the router uses it

* **`blocked(issue)`**, used by the idle offer (rules 15 and 16) and
  the hand-over count: an issue whose blockers are all satisfied is
  actionable.
* **Rule 14** ("unblocked: #B closed"): the owner's turn now also
  comes when the last unsatisfied blocker gains the label, not only
  when it closes. The event's time is when the label was last put on
  (`labelled_at`, as rule 22 reads it). The line says why: "unblocked:
  #B has merged and waits only for its nightly".
* **The idle offer's line** names such a blocker the same way, so
  that an owner who wrote the `Blocked-by:` line herself sees why it
  no longer holds.

The router's listing already has each open issue's labels. Each entry
of the `blockedBy` map that `attach_blockers` builds gains `labels`
for an open issue, and no new query is needed.

### 3. What it doesn't change

* **`forge-merger`.** A pull request's `Blocked-by:` decides when it
  may merge, and nearly always names another pull request. That is
  left as it is. A project that wants the same rule there can ask for
  it in a later design.
* **The blocker itself.** It still waits for its nightly, and rule 22
  still wakes its owner when a build with its fixes is published.
* **The issue's text.** Nobody edits `Blocked-by:` lines. A project's
  guidance should tell its agents that a blocker waiting only for its
  nightly no longer holds, so that what they write and what the router
  does agree.

## Configuration

`router.nightly_wake` gains one key:

| Key | Default | What it is |
|---|---|---|
| `unblocks` | `true` | Whether an open issue carrying `label`, and not parked, counts as satisfied where a `Blocked-by:` line names it (§1). `false`: it blocks until it closes, as before. |

Without `nightly_wake`, nothing changes.

## Tests

In `tests/test_router_rules.py` and the config tests:

* An issue whose only blocker is an open issue with the label is
  actionable for the idle offer. With `unblocks: false`, it is blocked.
* A blocker with the label and `Status:waiting-<anything>`, or a quiet
  label, still blocks.
* An open pull request named by `Blocked-by:` still blocks, whatever
  its labels.
* Rule 14 fires once when the last blocker gains the label, with the
  label's time as its event. It doesn't fire again on the same label,
  and it fires as today when the blocker later closes after the owner
  has spoken.
* Two blockers, one closed and one labelled, together unblock; with
  one of them still plain open, they don't.
* The hand-over count includes such issues.
* `unblocks` must be a boolean.

## Documentation

`docs/config.md`'s `router.nightly_wake` section gains the key, and
`docs/router.md`'s rule 14 and idle-offer text say what counts as
satisfied.
