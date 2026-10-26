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

## Amendment (2026-10-21)

An addition, separate from the label: `checks.host_fault_text`, an
optional list of strings in the project's configuration. A red whose
failed step's tail contains one of them counts as the host's fault, as
a full disk already does: no verdict is recorded, the head is tried
again later, and after three such faults it is posted `error`. The
strings are added to the built-in `No space left on device` and
`ENOSPC`, and the `HOST-FAULT` line quotes the one that matched. A
project can then name an outside service's outage, such as a package
registry's error, without agent-forge knowing that service. It needs
no label, and it settles some reds before anyone would want to run
them again.

## Amendment (2026-10-26): a stale build cache is cleared, and the head run again

* Status: approved (Alex Bligh, 2026-10-26, https://github.com/abligh/agent-forge/issues/126#issuecomment-6415958707)

**Why.** A slot's build cache can go stale in a way the build tool
doesn't notice. A rerun of the same head reuses the same stale cache,
so it is red again, and so is every other head the slot runs that
needs the same artifact. On one project:

* **2026-10-20.** A pull request was red twice, on two heads, at a
  WebAssembly link. The linker reported undefined symbols in one
  crate's own codegen units. The cache's object files were left over
  from an earlier compilation, and the build tool kept reusing them.
  Clearing that crate's incremental state by hand fixed it.
* **2026-10-26.** A pull request that changed only documentation was
  red twice in the fast lane: first on its head, then on its one
  rerun. Each failure was the same link failure, with undefined
  symbols for generic code the lane's release build had compiled
  earlier, against a different standard library build. Heads checked
  in other slots against the same default branch were green, and so
  was the branch itself. Moving the lane's cache aside by hand fixed
  it.

Neither red was the change's. Each cost the author a rerun or a push,
and a reviewer's queued approval waited behind it.

**The change.** `checks.cache_fault_text`, an optional list. Each
entry is a list of strings. A red whose failed step's tail contains
every string of some entry is a **stale cache**:

1. **No verdict.** The red isn't recorded, as for a host fault. The
   log says `CACHE-FAULT`, with the head, the slot, the step and the
   entry that matched.
2. **The slot's incremental state is cleared.** Every directory
   directly in an `incremental` directory under the slot's caches
   (`checks.caches`, and the step's own `caches`) is removed. That is
   the unit the cache pruning already removes whole, never a single
   file. The build's other output stays, so the next run rebuilds the
   project's own code without incremental reuse, not its dependencies.
   This happens while the slot's lock is still held, so no other run
   is using the cache.
3. **The head runs again** at once, at the front of the queue, in the
   same slot. It isn't the rerun `labels.rerun` gives, and it doesn't
   use that rerun up.
4. **Once per head.** If the same head matches again after its cache
   was cleared, the red stands, recorded as any red is, and the log
   says the cache was already cleared. The cause is then not the cache,
   or not only the cache.
5. **Nothing else counts.** A cache fault isn't a host fault, so it
   doesn't count toward MAX_ERRORS' `error`.

A project names its own build tool's symptom. For a Rust project that
might be `[["rust-lld: error:", "undefined symbol"]]`. agent-forge
knows no build tool.

**Cost.** One run of the head, and one slower build in that slot,
since its own code rebuilds without incremental reuse. Against that,
the head isn't red, and the slot stops turning every head that needs
the same artifact red.

| Key | Default | What it is |
| --- | --- | --- |
| `checks.cache_fault_text` | none | A list of lists of strings. A red whose failed step's tail has every string of one list is a stale cache: no verdict, the slot's incremental state is cleared, and the head is run again once. |
