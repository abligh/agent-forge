# 0057: A second instance checks trains, not heads

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-16

## Summary

A gated instance (designs/0034, amendment of 2026-11-05) holds each
pull request in its scope until the instance's context is green on
that pull request's head. On a host with one slot and a slow check,
those head runs fill the slot. Every in-scope pull request then waits
in that one queue before it can board a train.

This design lets an instance check **the Merger's trains** instead,
as the first instance's full check does under `merge.gate_tier:
"train"` (designs/0055). One new key in the block, `tier: "train"`,
turns it on:

1. **The Merger publishes each train's top** as a branch, whenever a
   member is in the instance's scope. An instance on another host can
   then fetch the top and post its status on it.
2. **The instance runs those tops,** the first train's first. It
   starts as soon as the train is cut, alongside the build check, not
   after it.
3. **The train merges only once the instance is green on its top.**
   In-scope pull requests are no longer held for a green on their own
   heads.
4. **A train is ordered with its out-of-scope carriages first,** so
   those carriages merge on the build check's green while the slower
   instance runs.
5. **A red ejects only the culprit.** Only in-scope members are
   suspects. With one suspect, the culprit is known without a run;
   with more, the red-train search (designs/0035) runs on the
   instance.
6. **Heads run only on request:** the block's `label`. That run tells
   the author and gates nothing.

Without `tier: "train"`, nothing changes.

## Why

One project, with a macOS instance on one host and one slot:

* **About 9.5 minutes a run.** It ran on every head that changed an
  in-scope path.
* **The wait was the cost.** On one busy day, the 90th percentile wait
  was 120 minutes over all runs, and 237 minutes for heads. A gated
  pull request was held for all of that. Reviewers had approved it,
  and the build check had passed it, hours earlier.
* **Most runs were spent on heads that were later replaced.** A head
  is replaced on each push, and on each update the Merger makes. The
  instance's catch-up rule (`instance_verdict`) accepts a verdict from
  before the Merger's updates, but not from before the author's push.
* **Every reaching merge is still to be checked.** The instance was
  made a gate (designs/0034, amendment) after an in-scope pull request
  merged untested and broke the default branch on that platform for
  nearly three hours. Making the gate cheaper must not take it away.

A train of up to 16 pull requests is one run. On that project, the
default branch takes 10 to 40 merges an hour, mostly in trains of
several pull requests, and at most a train every few minutes has an
in-scope member. So about 6 instance runs an hour would cover every
in-scope merge. That is less than the one slot gives. Today's runs on heads
are many times that.

## The design

### 1. `tier: "train"` in a block

A block's `tier` is `"head"` (the default, as today) or `"train"`.
`"train"` needs `gate: true` and the Merger's trains (`merge.test_ahead`
with `trains` not `"off"`). It is refused otherwise.

The scope stays as today, `pull_requests`. A pull request is in scope
when it changes a path in `paths`, or carries `label`. A block without
`pull_requests` has every pull request in scope.

### 2. The Merger publishes the tops: `train_branch`

Today a train's top is a commit in the Merger's own checkout. An
instance on another host can't see it, and a status can't be posted
on it.

With a `tier: "train"` instance, each live tick does two things:

* For each train in `ahead.json` with an in-scope member, it pushes
  the top to `<train_branch><n>` with a lease, where n is the train's
  place: 0 for the first. `merge.train_branch` defaults to
  `forge-train/`.
* It deletes any such branch whose train is gone.

The top's commit message lists the members by number and head, and
the instances whose scope they're in.

The project's CI workflow must not run on these branches. The
documentation says to add the prefix to `branches-ignore`, and
`forge-config check` warns if `ci_workflow` doesn't ignore it.

A remote instance already fetches from GitHub, so a fetch of
`refs/heads/forge-train/*` is all it needs. A status posted on the
top is a commit status like any other, and the Merger reads it like
any other.

### 3. What the instance runs, and in what order

With `tier: "train"`, `forge-pr-check` for that instance takes work
in this order whenever its slot is free:

1. **Train 0's top,** unless the instance already has a verdict for
   that tree.
2. **The default branch's tip,** under `main` (`every`, `newest`;
   `main_paths` as today). This run is usually free: once a train
   merges whole, the default branch's tree is its top's tree, and
   the instance already has a verdict for that tree (designs/0034,
   part 1). So `main` stays the backstop for anything `paths` misses,
   at almost no cost.
3. **Train 1's top.**
4. **Heads that carry the block's `label`** (`approved_first` orders
   them, as today).

A run of a top that is no longer a train's top stops at its next
step, as a superseded head's run does. A train is no longer a top
when it is re-cut, when a member leaves it, or when the default branch
moves under it. The instance keys verdicts by tree, so a train re-cut
to the same tree keeps its verdict.

An instance's lock (`lock`) still applies. Someone else's turn on the
host now delays a train, not a head. See "Cost".

### 4. The gate moves from heads to the train's top

For a pull request in a `tier: "train"` instance's scope:

* **`instance_wait` no longer holds it.** It boards a train on what it
  boards on today.
* **The train merges only on the instance's green.** A train that
  contains it merges only once the instance's context is `success` on
  the train's top, as well as `checks.context`. The instance's context
  must be one its `poster` created, as today. The green may be on any
  commit the Merger published with the same tree.
* **A merge of part of a train needs the instance's green too,** but
  only if that part contains an in-scope member. The build check's
  green on the top covers the prefixes (designs/0032). That is how
  section 5 lets the out-of-scope carriages merge first.

The instance's green is what the Merger reads, so `merge.gate_tier`
and the instance's tier are independent. With `gate_tier: "head"`,
the train still needs the instance's green on its top. Each pull
request still needs `checks.context` on its own head.

### 5. Out-of-scope carriages go first

When the Merger cuts a train (designs/0047), it chooses its carriages
as today, by queue order and touching disjoint files. It then orders
them so that every carriage with no in-scope member comes before
every carriage with one. Within each group they keep the queue's
order.

The top's tree doesn't depend on the order, because the carriages
touch disjoint files. So the order costs no run. The gain is in how
the train merges. Once the build check is green, the out-of-scope
prefix merges, one member a tick as today, while the instance is
still running. The train then waits at its first in-scope member.

### 6. A red ejects only the culprit

When the instance's context is red on a train's top:

1. **Retry once,** with `merge.test_ahead.retry_red`: the instance
   runs the red steps again on the same tree, as the build check does.
2. **Leave an inherited red alone.** It may be the default branch's
   red. If the instance's newest default-branch verdict is red, and
   it failed the same steps, nobody is ejected: the train's in-scope
   members are held, like `instance_wait` today ("<context> is red on
   the default branch"). The out-of-scope prefix still merges. The
   fix for the default branch is itself in scope, and goes green on
   its own train.
3. **One in-scope member: it is the culprit.** It needs no run. It is
   handed back with the instance's red, as a red suspect is today
   (`hand_back_red_suspect`), and the rest of the train goes on.
4. **More than one: search.** The red-train search (designs/0035,
   second amendment) runs over the in-scope members only. The
   out-of-scope prefix has already merged, or is known green. The
   search's prefix trees are published (section 2) as
   `<train_branch>s<k>`, and the instance runs them. With one slot the
   search halves: about log2(k) runs for k in-scope members. Only the
   culprit is handed back, and the members the search clears merge
   on the instance's green for their prefix.

The build check's own search and the instance's are separate searches
in `suspects.json`, each with its own context. A train that is red on
both is searched by the build check first. Its culprit leaves, and the
train is then cut again.

### 7. When the instance is late

The instance may be down, its host locked by a long turn, or its
queue behind. A train it hasn't answered stops every train behind
it.

So, after `train_wait_minutes` (in the block; default 60) with no
final verdict on the first train's top, the Merger takes that train's
in-scope members out of it. They are held as today ("waiting for
<context>"), with no head run asked for. The rest of the train is
re-cut and merges on the build check alone. The stall alert
(`merge.stall_alert`) says which instance it waited on.

The held pull requests board again once the instance answers
anything: a green or red verdict on any top or tip. That ensures that
a dead host holds only its own scope, never the default branch.

## What doesn't change

* **designs/0034**: `main`, `main_paths`, `main_red_issue` and
  `pull_requests` keep their meanings. Only what gates changes.
* **designs/0043**: `labels.merger_next` still marks the Merger's next
  pull request. A `tier: "train"` instance doesn't need the label,
  since the branch it fetches says what is next.
* **designs/0047, 0050**: trains are chosen as today. Section 5 only
  orders carriages within a cut train.
* **designs/0055**: a project may run both. The instance's tier is
  like the full check's: run where a change may reach the default
  branch.
* **designs/0056**: a member leaving a train re-cuts the tail. The
  instance's run on the old top is wasted, as the build check's is.
  `left_top_covers` covers the build check only: the members below a
  leaver that are in scope still need the instance's green on their
  new top.

## Cost

* **In-scope pull requests aren't checked on the platform before
  review.** Their authors can ask for a head run with the label.
  Reviewers read the build check, as for every other pull request.
* **A train with an in-scope member takes as long as the slower
  check.** It starts when the train is cut, so it adds the difference
  between the instance's run and the build check's, not their sum.
  On the project above that is a few minutes, against hours of holds
  today.
* **A turn on the instance's host now delays trains.** While someone
  holds the host's lock, the in-scope trains wait, for up to
  `lock.wait_minutes` and then `train_wait_minutes`. A project with
  turns on that host should keep them short, or out of busy hours.
* **Pushed branches.** Up to two `forge-train/` branches, plus a
  search's prefixes, exist at a time. Each is deleted when its train
  goes.

## Configuration

| Key | Default | What it is |
| --- | --- | --- |
| `checks.instances.<name>.tier` | `"head"` | `"train"`: the instance runs the Merger's train tops (and labelled heads), and gates the train, not each head. Needs `gate: true` and trains. |
| `checks.instances.<name>.train_wait_minutes` | `60` | With `tier: "train"`: how long the first train waits for the instance's verdict on its top before its in-scope members are held and the rest goes on. |
| `merge.train_branch` | `"forge-train/"` | The branch prefix the Merger pushes train tops and search prefixes to, when a `tier: "train"` instance applies. |

## Order of work

0. **Measure first:** for the last week, the share of merged pull
   requests in the instance's scope, and the instance's run time.
   That shows whether one slot keeps up with the trains (section 3).
1. **`forge-merger`:** publish and delete the tops (`train_branch`),
   and order the carriages (section 5). Inert unless an instance is
   `tier: "train"`. Tests with the simulation.
2. **`forge-pr-check`:** the instance's run order, stopping superseded
   tops, and verdicts by tree (section 3).
3. **`forge-merger`:** the gate on the top, a partial merge's gate,
   and the red handling: inherited reds, one suspect, the search
   (sections 4 and 6).
4. **`forge-merger`:** `train_wait_minutes` (section 7), and
   `forge-config check`'s warnings.
5. **`docs/config.md`, and a release.** The project then sets `tier:
   "train"` in its block. It lowers the block's `paths`-driven head
   runs to the label, and adds `forge-train/` to its CI workflow's
   `branches-ignore`.
