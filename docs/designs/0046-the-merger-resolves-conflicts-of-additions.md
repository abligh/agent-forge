# 0046: The Merger resolves conflicts of additions itself

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-04

## Summary

When `main` conflicts with an approved pull request, the Merger hands
it back. The author merges `main`, the build check runs, and a
reviewer approves the resolution, at least one round trip through two
agents. Many of these conflicts are two pull requests adding lines at
the same place in a list. Git can't tell what order they go in, but
any order is right. This design lets the Merger make that merge
itself, by rules a project names, push it to the pull request's
branch, and have the approval cover it, as it covers a clean merge
today.

The owner asked for this design on 2026-11-04, in XMPPEngineer's
session, as part of the work on one project's merge rate.

## Evidence

One project, 11-03 17:00Z to 11-04 07:40Z, with 238 conflicting merges
re-run by `git merge-tree`:

* **Hand-backs per merge rose to 1.40**, against 0.36 on 11-02, and to
  2.0 the next morning. Conflicts with `main` were 0.91 a merged pull
  request, and resolutions that voided their approval 0.59.
* **13% of the conflicts were pure additions on both sides**: every
  conflicting hunk had an empty base. A union of the two sides
  resolves them.
* **48% were docs-only or at most 20 lines.** Some of those are edits,
  not additions, and need a person, but many are lists.
* **Builds per merged pull request went from 3.0 to 5.9** on that
  pull request's own path. Each hand-back costs a check of the
  author's merge and usually a review.

## Design

### What the Merger may resolve

`merge.resolve.union`: a list of path globs. In a file matching one,
a conflicting hunk whose base is empty on both sides (both added
lines, neither changed or removed any) is resolved as the head's lines
then `main`'s. Any other conflicting hunk in any file, including a
non-empty base in a matching file, means the merge can't be resolved,
and the pull request is handed back as today. So one real conflict
anywhere stops it.

A project that sets `merge=union` in `.gitattributes` gets the same
result from git for those files, but GitHub's `update-branch` may not
honour it. This rule doesn't depend on that.

### What it does

When `update-branch` would fail because of a conflict, the Merger:

1. computes the merge as `git merge-tree --write-tree` would, then
   applies the rule above to each conflicting hunk;
2. if every conflict is resolved, writes the merge commit (parents:
   the head, then `main`'s tip) and pushes it to the pull request's
   branch with its own App, pinned to the head it judged;
3. comments, signed `— Merger`, listing each file and the lines it
   added from each side, so the author and the reviewers can see it;
4. waits for the build check on the new head, as after `UPDATE`.

It doesn't hand back. If the build check goes red, it hands back as
for any red head.

### Why the approval still covers it

Today an approval covers a merge of `main` after it when the merge's
tree equals `git merge-tree` of its parents: the merge is git's own,
so nobody wrote anything to review. This design widens that to "equals
the Merger's resolution of its parents under `merge.resolve`". The
check recomputes the resolution from the two parents and the
configured globs, so it holds whoever made the merge: the Merger, or
an author who ran the same tool. Nothing anyone typed is in the
resolution, and the comment shows it.

### What it never does

* It never resolves a hunk where either side changed or removed lines.
* It never resolves in a file that no glob names.
* It never pushes over a head it didn't judge.

## Configuration

`merge.resolve.union`: a list of globs, default empty (off). A project
names only files whose concurrent edits are additions: lists, tables of
contents, changelogs. The project's own review of that configuration
is what makes it safe.

## Steps

1. `forge-merger`: the resolution, the push, the comment, the wider
   approval check, and tests:
   * a pure-addition conflict resolved and pushed;
   * a mixed conflict handed back;
   * a matching file with a changed line handed back;
   * the resolution recomputed and accepted after an author pushes the
     same tree;
   * a different tree refused.
2. A small `forge-resolve` that an author can run to get the same merge
   locally.
3. docs/config.md.

## What it costs

* **On the build host:** one `merge-tree` and one push per resolved
  conflict.
* **For the Merger's App:** write access to pull request branches,
  which it already uses for `update-branch`.
* **Risk:** a wrong glob unions a file whose edits aren't additions.
  The empty-base condition limits that to cases where both sides only
  added lines.
