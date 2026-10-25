# 0025: Merge a stack as a unit

* Status: approved (Alex Bligh, 2026-10-22, https://github.com/abligh/agent-forge/issues/42#issuecomment-6351073967)
* Implemented: v0.4.102
* Author: XMPPEngineer
* Date: 2026-10-22

## Summary

A stack is a chain of pull requests, each based on the branch of the
one below it, with the bottom one based on `main`. Today `forge-merger`
skips every pull request whose base isn't `main`. So a stack merges one
layer at a time, and the author drives every step: retarget the next
layer to `main`, mark it ready, get it approved again, wait its turn in
the queue. Each layer costs a round trip through the author and the
reviewers, even when every layer was reviewed and approved hours
before.

This design makes the Merger see stacks. Once it merges a stack's
bottom pull request, it carries on up the stack in the same run,
before taking anything else:
1. retarget the next layer to `main` itself;
2. bring it up to date with `main`, which is a clean merge;
3. merge it as soon as its check is green.

It stops at the first layer that isn't ready. No approval is lost on
the way, and usually no check has to run again.

It's off unless a project sets `merge.stacks`.

## Evidence

On one project, from 18 to 22 October:
- **58 of 523 merged pull requests were stacked.** The median stacked
  one took 4.9 hours from opening to merge, against 1.7 hours for the
  rest.
- **Review came early, merging late.** In a chain of seven, upper
  layers were reviewed up to 16 hours before they merged. They waited
  because the layer below hadn't merged. When the bottom one did
  merge, the other six took 3 hours 50 minutes more to follow, one at
  a time. Each step was the same: the author retargeted the layer by
  hand, marked it ready, a reviewer approved it again, and it merged.
  The Merger's own part, from ready to merged, took 6 to 11 minutes
  per layer. The rest was waiting for the author to get to the next
  layer.
- **The longest wait was on the author.** One stack's bottom layer
  merged, and the author started rebuilding the layer above on `main`
  4 hours 20 minutes later. That layer was over a day old by then.
- **Stacked pull requests are kept as drafts**, because the Merger
  can't take them anyway. So each one needs a step from its author
  before the Merger will look at it at all.

## Design

### Which pull requests form a stack

Pull request P is **stacked on** Q when P's base branch is Q's head
branch, in the same repository, and Q is open. A **stack** is the
chain that reaches `main`, from the bottom layer (based on `main`) to
the top. A pull request based on a branch that no open pull request
heads is not part of a stack. The Merger holds it as today, saying
`based on <branch>, which no open pull request heads`.

Stacks are found from the open pull requests the Merger already reads
(`baseRefName`, `headRefName`). It doesn't need GitHub's stack API.

A fork in the chain (two pull requests stacked on the same Q) is
allowed. Each branch of it continues on its own: after Q, the Merger
takes the older one first.

### Eligibility while stacked

A stacked pull request is judged as it is today: approval, nothing
said since, no blocker or deferral, and an approval that covers the
head. The only difference is that being stacked holds it, with the
reason `stacked on #Q`. That replaces `based on <branch>, not main`.
So a stacked pull request can be reviewed and approved while the
layers below are still waiting, and it doesn't have to be a draft.

**Covering the head across a change below.** When a lower layer
changes after the layer above was approved, the author merges the
lower layer's branch into the upper one. Today that commit doesn't
cover the head, because no parent is on `main`. It now covers it
when:
- one parent is the head of the pull request it's stacked on;
- that head is itself approved and covered;
- the merge is clean, by the same `merge-tree` test.

A hand-resolved merge still needs a new approval.

### Merging a stack

When the Merger merges a pull request that another one is stacked on,
the next tick of the same run takes the layer above first, before any
other candidate. It does that when that layer is eligible apart from
its base: approved, covered, and with nothing said since. The steps,
one write per tick as today:

1. **Retarget.** It sets the layer's base to `main`
   (`PATCH .../pulls/N` with `base`). The head doesn't change, so the
   approval still covers it.
2. **Update.** The head is behind `main`, which now holds the layer
   below as one squash commit. Merging `main` into the head is a
   clean merge: both sides make the same changes. That's `UPDATE`,
   with the usual `merge-tree` test first. The new head is a clean
   merge of `main`, so the approval covers it.
3. **Check.** The new head's tree is the tree the build check already
   tested when it judged the old head merged with `main`, unless
   `main` has gained another change in between. The build check posts
   that verdict at once, by its existing exact-tree match, so nothing
   runs. If `main` did move, the check runs as for any update.
4. **Merge**, as today. Then the next layer up, from step 1.

**It stops at the first layer that isn't ready**: not approved, red,
in conflict, blocked, deferred, or a draft. That layer is held or
handed back as usual, and the queue goes on with everything else. The
layers above it keep their places and wait for it.

The Merger never deletes a branch another open pull request is based
on, and it retargets before the merge that would leave the base
branch behind.

**Order in the queue.** A stack enters the queue by its bottom layer,
in that layer's usual place. Once it's in progress it isn't
interrupted, so the Merger doesn't leave a stack half merged.
`merge.stacks.max_run` (default 0, meaning no limit) caps how many
layers in a row it takes before letting the queue's next candidate
have a turn. A project uses that if long stacks start to starve
everything else.

**Predictions.** With `merge.test_ahead`, a layer the Merger will take
next is a prediction like any other. Its prediction is the head merged
with what `main` will be after the layers below, which is the tree
step 3 matches.

**The record.** Each step prints a line, for example
`STACK #N: retargeted to main after #Q merged; 3 above`. The tick's
JSON gains `stack: [Q, N, ...]` for the stack being merged.

### Native stacks

GitHub's own stacks were tried on a scratch repository with two Apps
(one as author, one as reviewer and merger):
- **The good parts:** an App can create a stack with one call, and one
  call merges any run of layers from the bottom as separate squash
  commits.
- **The problem:** GitHub then rebases the layers above. That gives
  each one a new head: unsigned, committed by the merging App, and
  with no status. Its approval was dropped when "dismiss stale
  approvals" was on, contrary to the documentation.
- **Why that blocks us:** a rebased head isn't a clean merge, so the
  Merger's coverage test would reject it, and every layer would need
  its approval again.

This design therefore keeps each head and merges `main` into it, which
the coverage test already accepts. Native stacks stay possible for
authors who want GitHub's stack view: the Merger reads the base
branches either way, and never asks GitHub to merge a native stack.

## Configuration

```json
"merge": {
  "stacks": {"max_run": 0}
}
```

Unset, stacked pull requests are held as `based on <branch>, not
main`, as today.

## What a project changes

- **Stop making stacked pull requests drafts.** The Merger now holds
  them as `stacked on #Q`, so review can start at once, and the
  approval is waiting when the layer below merges.
- **The router wakes reviewers for stacked pull requests**, as for any
  other. For a stacked one, the diff GitHub shows against its base
  branch is that layer's own change.
- **Authors no longer retarget by hand after a merge below.** They
  still fix a layer that conflicts or goes red. The Merger hands that
  layer back as for any pull request.

## Not covered

- Rebasing. The Merger only merges `main` into heads, never rewrites
  them.
- Stacks across forks, or onto a branch no pull request heads.
- Merging several layers in one GitHub call. One squash per layer per
  tick keeps each layer's own commit, check and hand-back.

## Amendment (2026-10-25): the Merger restacks a layer itself

* Status: approved (Alex Bligh, 2026-10-25, https://github.com/abligh/agent-forge/issues/80#issuecomment-6400818866)

**What went wrong.** Step 2 above assumed that once a layer is
retargeted, merging `main` into it is clean, because both sides make
the same changes. It usually isn't. The layer was approved on an older
head of the layer below. That layer then gained commits before it
merged (the Merger's own updates from `main`, and fixes), and `main`
holds all of them as one squash commit. So `main` and the layer differ
wherever those later commits touch lines the layer also changed.

In one project, from 2026-10-24 to 2026-10-25, 8 layers were
retargeted. Six conflicted with `main` at once and went back to their
authors. Each author then did by hand what the Merger could have done:
merge the final head of the layer below, then `main`. Several rebased
instead, which loses the approval ("the approved commit is not in the
head's history"). Every one then needed a review again. One layer took
2 hours 35 minutes from its retarget to its merge, and authors began
avoiding stacks.

**The change.** `stacks.json` already records the head each layer below
had when it merged, and a clean merge of that head already covers a
layer's approval (`covers_head`'s `below`). So, in step 2, when
merging `main` into a retargeted layer isn't clean:

1. **`RESTACK`.** If merging the recorded head of the layer below into
   the layer is clean (`git merge-tree`), the Merger makes that merge
   on the layer's branch through GitHub's merges API (`POST
   .../merges`, with base the layer's branch and head that commit), as
   its one write for the tick. The approval covers the new head, as
   for a merge of `main`. It posts the same note as after an `UPDATE`:
   no review is needed again.
2. **`UPDATE`**, on a later tick, as before. The layer now holds every
   commit of the layer below, so its merge with `main` is clean unless
   `main` has gained a change that conflicts with the layer's own.
3. If either merge isn't clean, the layer goes back to its author as a
   conflict, as today. The hand-back says which merge conflicted, so
   the author does only that one, and says to merge, not rebase.

A layer that is still based on its stack's branch below, because it
waits to be retargeted, is never restacked: the Merger restacks only
the layer it is about to update. A restack is tried once per approval
and head, as `resolve` is (`restacked.json`).

**What a project changes.** Nothing in its configuration: this is part
of `merge.stacks`. Authors are told that the Merger restacks a layer
itself after the layer below merges, so they don't merge or rebase the
layer by hand unless it is handed back.

**Order of work.** One pull request: `RESTACK` in `decide` and
`carry_out`, the record and the printed line (`STACK #N: merged #Q's
head <sha> into it`), the hand-back's wording, tests with a stack whose
lower layer gained a commit after the upper one was approved, and
docs/config.md's `merge.stacks`.
