# 0058: The Merger waits for each area's reviewer

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-16
* Issue: #460

## Summary

`agents.<name>.reviews` chooses who reviews a pull request. It never
decides what merges. `approval_of` takes the newest `APPROVED` review
by any reviewer App, so a pull request that touches a specialist's
paths merges on the general reviewer's approval alone. A project found
this when such a pull request merged before its specialist had looked.
Until now the specialist's area was held only by a practice: the
general reviewer approves last.

This design adds an opt-in hold, `merge.area_reviewers`. With it, a
candidate that changes a path some reviewer's `reviews` globs match is
held until one reviewer of that area has approved at a commit that
covers the head. It is the same rule `merge.code_owners` already
applies to CODEOWNERS, with the roster's globs in place of `@login`s,
which an App can't be.

## What exists

* **Reviewer choice** (`forge-router`): a pull request's changed paths
  are matched (`fnmatch`, `*` crossing `/`) against each reviewer's
  `reviews`. Reviewers with the same globs share an area, and a pull
  request goes to one of them by its number. Paths no glob matches go
  to the first reviewer listed.
* **Approval** (`approval_of`): the newest `APPROVED` review by an
  `APPROVERS` App other than the author. A review's body is never
  read, so `Not-a-review` on an approval doesn't stop it counting.
  `Not-a-review` only keeps a remark from holding (`said_since`).
* **Code owners** (`code_owner_wait`, `merge.code_owners`): for each
  path changed since `main` that CODEOWNERS gives owners, one owner
  must be the author or have, as their newest verdict, an `APPROVED`
  review at a commit that `covers_head` accepts. Otherwise the pull
  request is held with `waits for code owner @x: path`. It is a hold,
  not a hand-back.

## The rule

With `merge.area_reviewers` set, `held` gains a reason when, for some
path the pull request changes since `main`:

1. at least one agent of kind `reviewer` has `reviews` globs that
   match it, and
2. no agent of that path's **area** has, as her newest verdict, an
   `APPROVED` review at a commit that covers the head.

A path's **area** is the set of reviewers whose globs match it,
grouped as the reviewer choice groups them. Reviewers with identical
globs are one area, and any one of them is enough, as with the
routing. A path two different areas match needs one approval from
each, since both claimed it.

**Which approvals count.** The same as for code owners:

* the reviewer's **newest** `APPROVED`, `CHANGES_REQUESTED` or
  `DISMISSED` review, which must be `APPROVED`;
* at a commit `covers_head` accepts, so a merge of `main` or a
  regenerated file doesn't cost a fresh approval, and a real change
  does;
* an author never satisfies her own area, unlike a code owner. An
  agent doesn't review her own work, and the general approval already
  excludes the author.

**What isn't held.**

* A path no glob matches: the general approval covers it, as today.
* A path under `merge.area_reviewers.ignore`, globs matched as
  `reviews` are (e.g. `[".agent-forge/**"]`, which a code owner
  reviews).
* A reviewer who is `away`: her stand-in's approval counts for her
  area, as the routing already sends her pull requests to the
  stand-in.

**The hold line.** `waits for UIReviewer (crates/x/reports/**):
crates/x/reports/templates/a.typ and 3 more`. That names the area's
first reviewer, the glob that matched and the first path, as
`code_owner_wait` names its owner and path. With a shared area:
`waits for Reviewer2 or Reviewer3 (...)`. Nothing is said to the
author, since there's nothing for her to do.

**Telling the reviewer.** The routing has already sent the pull request
to the general reviewer. With `merge.area_reviewers.tell` (default
true), the Merger asks the router to wake each waiting area's reviewer
once per pull request and head, as the stall notice does. It does not
open an issue: a reviewer works from her wakes, not from a list of
asks.

## Key

```json
"merge": {
  "area_reviewers": {
    "ignore": [".agent-forge/**"],
    "tell": true
  }
}
```

`true` is the same as `{}`. Left out, or `false`, nothing changes:
`reviews` only routes, as today. `forge-config check` refuses it
without a reviewer that has `reviews`, and refuses an `ignore` entry
that isn't a non-empty string.

## How it meets the rest

* **The general approval is still needed.** This adds a hold; it never
  replaces `approval_of`. A pull request touching only a specialist's
  paths needs her approval, which is also an approval by an
  `APPROVERS` App, so one review can satisfy both.
* **`said_since`.** Unchanged. A specialist's later comment holds the
  pull request as any remark after the approval does.
* **Code owners.** Independent. A path can wait for both its owner and
  its area. The hold lists the first reason, as `held` does today.
* **Trains** (#459). The hold is checked on each member before it
  boards, as `code_owner_wait` is (`boarding_wait`), so a held member
  never boards. The train's own pull request carries no reviews, and
  isn't checked.
* **Stacks.** A layer's area is judged on its own diff from its base,
  as code owners are judged for layers today.
* **`approved_first`** (forge-pr-check). Unchanged: any reviewer's
  approval still moves a head forward. A head approved by the general
  reviewer but waiting for its area is near merging too.
* **`merge.approval` "green".** Area holds still apply. A green head
  approves itself for the general rule only.

## What `forge-flow` shows

The hold is a `held` reason like any other, so it is already listed
with its pull request. `forge-flow` counts `area` holds in its holds
table, beside `code owner`, with the median time they stood. That
shows whether a specialist is the slow step.

## Cost

One `git diff --name-only` per candidate, already run for code owners
(shared when both are on), and an `fnmatch` per path and reviewer.
The reviews are already fetched.

## Alternatives

* **Generate CODEOWNERS from `reviews`.** GitHub's code owners must be
  users or teams, not Apps, so GitHub couldn't enforce it, and
  `code_owner_wait` would wait for good on names it can't check.
* **Keep the practice** (the general reviewer approves last). It
  leaked once already: an approval before the specialist's counts, and
  a review's text can't stop it.
* **Require every reviewer the router *would* choose.** Only one of a
  shared area is chosen, by number. Requiring the chosen one would make
  the hold depend on routing order, and would refuse an approval from
  her equal.

## Steps

1. `area_wait(repo, pr, areas, main_ref)` beside `code_owner_wait`;
   the hold in `held`, `boarding_wait` and the layer check; the key,
   its `forge-config check`, and docs/config.md's row. Inert until set.
2. `tell`: the wake through the router, once per pull request and
   head.
3. `forge-flow`'s `area` row.

## Acceptance

* With the key set, a pull request changing a path in a reviewer's
  `reviews`, approved only by another reviewer, is held with the line
  above, and merges once a reviewer of that area approves at a covering
  commit.
* An approval at an older head that `covers_head` accepts (main merged
  in) still counts. One before a real change doesn't.
* Two reviewers with identical globs: either's approval clears it.
* A path in `ignore`, or matched by no glob, holds nothing.
* A train never boards a held member.
* Without the key, the Merger's decisions are unchanged (`make
  replay-compare` shows no difference).
