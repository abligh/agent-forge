# 0022: Run a red head again

* Status: approved (Alex Bligh, 2026-10-20, https://github.com/abligh/agent-forge/issues/35#issuecomment-6295595950)
* Author: XMPPEngineer
* Date: 2026-10-20

## Summary

A verdict belongs to a head. Once a head is red, `forge-pr-check`
never runs it again. When the red came from the host or from a known
flaky test rather than from the change, the author's only way back to
green today is a new head: a rebase or an "Update branch". That costs:
- a full wait in the queue;
- every approval, which reviewers must give again for a diff they've
  already read;
- often a push the author would rather not make.

This design adds a label, `labels.rerun`. Put on a pull request whose
head is red, it makes `forge-pr-check`:
1. drop that head's red verdict, keeping it in the log;
2. queue the head again;
3. take the label off.

Each head can be run again once. The second verdict stands, whatever
it is.

## Evidence

On one project, within a day:
- **A container that never started.** A red came from the container
  runtime failing to start a step's browser, after 1.7 seconds, with
  nothing compared. The author pushed a new head to get past it.
- **A known flaky timing test.** Two reds came from a test with an
  open flake issue, on changes that touch nothing it tests. One author
  had three approvals waiting on green, and the new head that was the
  only way forward would have reset them all.
- **Clearing state by hand.** Twice, an operator cleared a verdict out
  of the check's state file by hand to queue a head again. That's
  error-prone, it needs shell access to the check host, and no log
  records it.

## Design

**The label.** `labels.rerun` names it, for example `Check:rerun`.
Unset, the feature is off. `forge-pr-check` already reads each open
pull request's labels on every tick, for `labels.urgent`.

**What it does.** On a tick, for each open pull request with the
label:
- **Head red, not yet run again:** the head's verdict moves out of
  `results` into the head's entry in `reruns` in the state file, with
  who set the label and when. The head is queued as if new, the label
  is removed, and the log says
  `RERUN #<pr> <sha>: was red(<steps>); queued by <login>`. The
  posted status goes back to pending: "queued again after a red, by
  <login>".
- **Head already run again:** the label is removed, and a comment says
  `<sha> has already been run again once; push a new head to run it
  a third time`.
- **Head green, pending or unrun:** the label is removed. Nothing else
  happens, because there's nothing to run again.

**Once per head, and why.** A rerun is for a red that isn't the
change's fault. A second rerun of the same head would be fishing for a
green from a flaky test, which hides the flake instead of fixing it. A
new head gets a rerun of its own.

**The second verdict stands.** Its posted description says
`(run again after red(<steps>))`, so a reviewer can see that the head
was red once. The first red stays in the log and in `reruns`, so flake
counts still see it.

**Reuse and traces.** The rerun is a fresh run of every step. It
doesn't reuse the red run's steps: the point is to find out whether
they fail again. Steps that the red run passed may still be reused
under `reuse`, as for any head.

**Who may set it.** Anyone GitHub lets label a pull request: triage
access, which the agents' Apps have. That's deliberate. The author is
the one who knows the red was a flake, and once per head bounds what
the label can cost.

**Permissions.** Removing the label and commenting need
`issues: write` for `checks.poster`'s App. A poster that already
comments on red runs (`red_comment`) has it.

## Limits

- **It doesn't decide what a flake is.** Telling a flaky red from a
  real one is still the author's job. The label only saves the push
  and the approvals.
- **Automatic retries are a separate question.** Retrying a step on
  its own when, say, its container never starts is a further design,
  once reruns show which failures recur.
- **The Merger is unchanged.** It merges on the head's current
  verdict, as now.

## Testing

**Unit tests:**
- A red head with the label is queued again, its verdict is kept in
  `reruns`, and the label is removed.
- A second label on the same head is refused with the comment, and
  nothing runs.
- A green, pending or unrun head just loses the label.
- The second verdict's description names the first red.
- With `labels.rerun` unset, a label of that name does nothing.

**One real run** against a throwaway pull request whose only step
fails the first time and passes the second.

## Rollout

1. Implement in an agent-forge release. It's off until a project sets
   `labels.rerun`.
2. A project creates the label and sets it in its configuration.
   Authors are told: once per head, and only for a red that isn't the
   change's fault, naming the flake issue in the pull request if there
   is one.
