# 0007: Check the pull request merged with the default branch

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-11

## Summary

`forge-pr-check` runs the steps of the default branch's configuration
on the pull request's head as its author pushed it. When the head is
behind the default branch, those are two different snapshots of the
project. On one project, a change renamed a step and added the
Makefile targets the new step runs, both in the same pull request.
Once it merged, every open pull request based before it failed the
renamed step with `No rule to make target`: twenty pull requests, five
red verdicts in the first half hour, each after a seven-minute run,
none of them wrong.

The head alone is also not what will merge. The Merger brings a head
up to date before it merges it, and the build check then runs again
on that update, so a pull request is built at least twice.

This proposes `checks.merge_default`: the worker checks the head
merged with the default branch's tip, the tree the Merger's update
would make, and posts the verdict on the head as now.

## Goals

* **The steps and the tree they run on come from one snapshot.** A
  change to the steps can't strand pull requests based before it.
* **The check tests what will merge**, as far as the default branch
  has got.
* **The update before a merge needs no second run** when the default
  branch hasn't moved since.
* **Nothing the Merger requires changes.** The status context, its
  meaning for the head, and the hand-backs stay the same.

## Non-goals

* **Running again whenever the default branch moves.** That would
  multiply the queue by the merge rate. A verdict stays what it is;
  the Merger's update and its predictions (`merge.test_ahead`) catch
  what a later merge breaks, as now.
* **Writing anything to the pull request.** The merge is local and is
  never pushed.

## Design

### The commit that is checked

When a worker prepares a head, it fetches the default branch as well
and computes `git merge-tree --write-tree <tip> <head>`. If the head
already contains the tip, it checks the head out as today. Otherwise
it makes a commit of that tree whose parents are the tip and the head,
with a fixed author, committer and date and no signature, so the same
inputs always give the same commit, and runs the steps there. This is
how the Merger already makes its predictions, so the two agree on the
tree.

GitHub's own merge ref (`refs/pull/N/merge`) isn't used: GitHub
computes it lazily, so it can be missing or stale when the worker
looks.

### What is recorded and posted

The verdict is still keyed by the head and posted on the head. It
gains `base`, the tip it was merged with, and `tree` is the merged
tree. The description says so: `green on <host> with main at
<short sha>`.

With `merge.test_ahead`, the merged tree is exactly what GitHub's
`update-branch` makes while the tip is unchanged. So when the Merger
updates the head, the update's tree matches this verdict, and the new
head is posted green at once (`MATCHED`), with no second run. Today
that happens only for the pull requests the Merger has predicted.

### When the merge conflicts

A head that conflicts with the tip can't be checked. The worker
records a red verdict, with no steps run, and posts `failure` with
`conflicts with main at <short sha>` and the paths in conflict. That
is the same outcome the Merger reaches, but sooner: the author needs
to merge the default branch either way.

### Dependencies and the fast lane

With `checks.depends` (design 0005), the changed paths are those
between the tip and the merged commit, which is exactly the pull
request's change. `CHECK_BASE` is the tip. The record for the tip's
own tree, written by the tip's traced run, then applies directly,
without falling back to an ancestor's record. The fast lane (design
0006) estimates from the same paths.

### What stays the same

Predictions (`ahead`) are merges already and are checked as they are.
Runs of the default branch's own heads check the tip itself.

## Config and file-format shape

```json
"checks": {"merge_default": true}
```

`docs/config.md` defines `merge_default` and the verdict's `base`.

## Trade-offs and failure modes

* **A verdict is against the tip at the time.** A pull request green
  against an older tip may be red against a newer one. That is true
  today too, and the Merger's update and predictions are what catch
  it.
* **A red default branch makes every pull request red.** Today a
  pull request based before the breakage stays green. With `depends`,
  the default branch's own run logs `MAIN-RED`, and the description
  of a red verdict names that run when the tip is red, so authors
  aren't sent to fix what isn't theirs.
* **More conflict verdicts, sooner.** A pull request that conflicts
  with the default branch fails its check instead of waiting for the
  Merger to hand it back. Its author must merge the default branch
  either way.
* **One more fetch per run.** It is of the default branch, which the
  worktree mostly has already.

## Testing strategy

agent-forge's tests cover:

* a head behind the tip checked as the merge, with fixed metadata and
  a repeatable commit;
* a head that contains the tip, checked as itself;
* a conflict giving a red verdict with no steps run, and naming the
  paths in conflict;
* `base` and `tree` in the verdict, and the description;
* a later update of the head matched to the verdict, with no run;
* `CHECK_BASE` and the changed paths under `depends`;
* a red tip named in a red verdict's description;
* predictions and default-branch runs unchanged.

## Implementation plan

1. `merge_default`, off by default, in one release.
2. A project turns it on in its configuration, in a pull request its
   owner merges.

## Open questions

* **Whether it should become the default** once a project has run it
  for a while. It makes `test_ahead` matches far more frequent, and
  there is little reason to check a tree that will never merge.
