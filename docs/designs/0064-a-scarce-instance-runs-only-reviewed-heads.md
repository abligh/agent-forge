# 0064: A scarce instance runs only reviewed heads

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-23

## Summary

An instance of the check (`checks.instances`, designs/0021 and 0034)
runs every head in its scope as soon as it is pushed. On a scarce
machine, such as one macOS host with one slot, most of those runs are
of heads that are later replaced. Only the head that merges needs the
instance's green.

This design adds an opt-in key to an instance's block,
`reviewed_heads`. With it, the instance runs a head only once the
pull request is **released**:

1. a reviewer agent has approved it, at any commit, or
2. it carries `labels.armed`: a reviewer has reviewed it and will
   approve once the quick check is green, or
3. its author asked for an early run with the block's `early_label`.

A head that isn't released gets a pending status that says why, and
no run. The Merger is unchanged: it still holds a gated pull request
until the instance is green on its head (`instance_wait`). Without the
key, nothing changes.

## Why

One project has a gated instance on a single macOS host, with one
slot. In the last week:

* **It ran 364 heads for 163 pull requests,** 2.2 a pull request.
  Each head in scope was run when pushed: before review, after each
  fix a reviewer asked for, and after each update.
* **The host was busy about 89% of the day.** In one day the
  instance's runs took 1,059 minutes (125 runs, of heads and the
  default branch). Agents' own turns on the host took 224 minutes,
  and the nightly about 30.
* **A busy host makes every gated pull request wait.** The Merger
  holds a gated pull request until the instance's green
  (designs/0034, amendment). designs/0057 measured a 90th percentile
  wait of 237 minutes for a head on the same kind of host.

A reviewed pull request is near merging. A head pushed before review
is likely to be replaced: the reviewer asks for changes, or the author
fixes the build check's red. Running once a pull request is released
should bring heads run toward one a pull request. That frees about half
of the instance's time on the host, which then waits less for the
heads that do need it.

## What exists

* **Scope** (`in_scope`, forge-pr-check): an instance runs a head
  only when its pull request carries the block's `pull_requests.label`
  or changes a path in `pull_requests.paths`. Other heads get no
  status from it.
* **Approvals, for order** (`approved_prs`): with `approved_first`, a
  head whose pull request a reviewer agent has approved, at any
  commit, goes first. Answers are kept in `approved.json` for
  APPROVED_FRESH (5 minutes), and at most APPROVED_MAX_CALLS (30)
  pull requests are asked a tick.
* **Armed approvals** (`labels.armed`, `armed_prs`): a reviewer agent
  puts the label on a pull request it has reviewed and will approve
  once its check is green. It takes the label off when the approval
  posts or the head changes. With `approved_first` it counts as
  approved. It is read from the open pull requests' listing, which
  every tick already makes, so it costs no call.
* **The gate** (`instance_wait`, forge-merger): a pull request a gated
  instance applies to is held until the instance's context is
  `success` on its head, or on the commit before each merge of the
  default branch into it (`instance_verdict`). It is checked again at
  boarding, so a train never takes a member without it.
* **Trains.** The instance doesn't run train tops. A train's members
  each carry their own green, and the train's run is the build
  check's. designs/0057 (proposed) would move the gate to the tops.

## The rule

With `reviewed_heads` set in an instance's block, `forge-pr-check
--instance <name>` filters the heads `in_scope` keeps. A head is
queued only when its pull request is released (above). Drafts, and
heads already judged, are kept as today.

**Approvals are read every tick for the heads that wait.** A head in
scope with no verdict and no release is asked about on every tick, not
every APPROVED_FRESH. They are few: the instance's scope, less those
already released. Once a pull request is released it stays released in
`approved.json` until it closes, and isn't asked again. A head that
can't be read waits for the next tick.

**A head is run as soon as it is released.** The instance has no
webhook: it runs on its host's schedule (designs/0021). So a release
is seen on the instance's next tick. With a one-minute interval, a run
starts within about a minute of the approval or the label. The
armed label usually comes before the approval, so the instance's run
often finishes before the reviewer's approval posts.

**A new head of a released pull request is run.** An approval counts
at any commit, as for `approved_first`. The Merger needs the instance's
green on the head it merges, so a head pushed after approval needs its
own run. Starting it at once means the verdict is ready when the
reviewer approves again. A merge of the default branch into a head the
instance has judged is not run: `instance_verdict` already accepts the
earlier verdict for it, so the run would gate nothing.

**What a held head shows.** The instance posts its context `pending`
on the head, once, with `runs once a reviewer approves or arms it`.
The verdicts log says `UNREVIEWED #<pr> <sha>` once. The Merger's hold
then reads `waiting for <context> (pending: runs once a reviewer
approves or arms it)`, so an author knows why. When the head is
released, the log says `RELEASED #<pr> <sha>: approved by <login>`,
`armed` or `early, asked by <login>`, and the head is queued as any
new head. Its place in the queue is its pull request's, as today, and
with `approved_first` it goes before unreleased work anyway.

**A release taken back doesn't stop a run.** If the armed label comes
off without an approval, a run already started finishes. A head that
hasn't started waits again.

### The early label

`reviewed_heads.early_label` (default none) names a label for a pull
request whose author needs the instance's result before review, such
as a fix only that platform shows.

* On a posting tick, a head whose pull request carries it is
  released, and the label is taken off, as `labels.rerun` is.
  `RELEASED ... early, asked by <login>` names who put it on
  (`labelled_by`). The poster's App needs `issues: write`, as for
  `rerun`.
* It releases that head only. A later push needs the label again, so a
  label left on doesn't bring back a run on every push.
* Anyone with triage access can set it. Agents may use it for work
  that is about the instance's platform. The verdicts log counts the
  early runs, so a project can see if it is overused.

The block's `pull_requests.label` keeps its meaning: it puts a pull
request in scope. It doesn't release one.

## Trains and the default branch

* **Train tops: not run.** The instance runs no train top today, and
  this design doesn't add one. Each member is held until the instance
  is green on its own reviewed head, before it boards. The train's
  run is the build check's, as today.
* **designs/0057.** If it is approved, a `tier: "train"` instance
  already runs heads only on request, and gates the top instead. The
  two keys don't combine, and `forge-config check` refuses both in one
  block. This design is the smaller step: it keeps the gate on heads,
  needs no published train branches, and keeps the culprit of a red
  known without a search.
* **The default branch.** Unchanged. The instance runs it under
  `main` and `main_paths` as today, the backstop for anything
  `paths` misses.

## Key

```json
"checks": {
  "instances": {
    "mac": {
      "reviewed_heads": {"early_label": "run-early"}
    }
  }
}
```

`true` is the same as `{}`: no early label. Left out, or `false`,
every head in scope is run, as today.

| Key | Default | What it is |
| --- | --- | --- |
| `checks.instances.<name>.reviewed_heads` | none: off | Run a head only once its pull request is approved by a reviewer agent, armed, or asked for early. |
| `checks.instances.<name>.reviewed_heads.early_label` | none | A label that releases the head it is on, once, and is taken off. |

`forge-config check` refuses it:

* in `checks` itself: the Merger requires that context on every head,
  and the reviewers read it;
* with `merge.approval: "green"`: there is no reviewer to release a
  head;
* with no agent of kind `reviewer`;
* with `tier: "train"` (designs/0057), if that lands first.

It warns when `labels.armed` is unset, since then only an approval
releases a head, and the run starts later.

## Cost

* **Merges wait for one run after review.** A pull request approved
  without arming waits for the instance's run before it merges. The
  run starts at the head of the queue (`approved_first`), on a host
  half as busy. Today it often waits behind runs of unreviewed heads.
* **Authors see the platform's result later.** Before review, only by
  asking with the early label. Reviewers read the build check, as for
  pull requests out of scope.
* **More approval reads.** One reviews call per waiting head a tick,
  on the instance's host. With a few heads waiting, that is well inside
  an App's hourly allowance.

## Alternatives

* **Release only on an approval that covers the head.** It would skip
  runs of pushes after approval that a reviewer then doesn't approve.
  But `covers_head` needs the Merger's merge-tree work on the
  instance's host, and the run would start only after the second
  approval, adding a run to each such merge.
* **Gate train tops instead** (designs/0057). Fewer runs still, but it
  needs published train branches, carriage ordering, a search on red,
  and a timeout for a late host. This design changes only which heads
  the instance takes.
* **Fewer slots for heads, by priority.** `approved_first` already
  puts approved heads first. It doesn't stop the host spending its
  idle minutes on heads that will be replaced, and with one slot a run
  that has started is never preempted.

## Order of work

0. **Measure first:** for the last week of the instance's
   `verdicts.log`, split its run time into heads and default-branch
   runs, and count the heads of each merged pull request run before
   its first approval or armed label. That bounds the saving.
1. **`forge-pr-check`:** `reviewed_heads` after `in_scope`; approvals
   read every tick for waiting heads; the pending status and the log
   lines; no run for a merge of the default branch into a judged head.
   Inert unless set.
2. **`forge-pr-check`:** `early_label`, taken off like `rerun`.
3. **`agent_forge/config.py`:** the key, instance-only, and its
   refusals and warning. `docs/config.md`'s rows.
4. **`forge-merger`:** `instance_wait` names a pending status's
   description in the hold.

## Acceptance

* With the key set, a head in scope with no approval, no armed label
  and no early label gets a pending status and no run.
* An approval, or the armed label, queues it on the next tick, and the
  run starts in a free slot then.
* A push after approval is run at once. A merge of the default branch
  into a judged head is not.
* The early label runs one head and is taken off.
* The Merger still merges a gated pull request only on the instance's
  green at its head, or before the default branch's merges into it.
* Without the key, the instance's decisions are unchanged.
