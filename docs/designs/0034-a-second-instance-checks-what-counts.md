# 0034: A second instance checks what counts

* Status: approved (Alex Bligh, 2026-10-26, https://github.com/abligh/agent-forge/pull/99#issuecomment-6406712511)
* Author: XMPPEngineer
* Date: 2026-10-26

## Summary

designs/0021 added a second instance of the build check, on another
platform's host, with its own commit-status context. The Merger doesn't
require that context, so the new platform's check could prove itself
first. A later design was to decide whether it gates.

Meanwhile, the instance runs every pull request's head, in full. The
tracer that lets the first host skip unaffected steps is Linux-only.
Its verdicts gate nothing, and they arrive about an hour after the head
was pushed, often after the pull request has merged. The only verdicts
that matter are those that show the default branch has broken on that
platform. Those come late too, mixed in with dozens of others.

This design points the instance at what counts:

1. **The default branch's tip, newest first.** The instance checks
   the default branch's newest tip as soon as its slot is free,
   skipping tips that were overtaken while they waited. A red tip
   opens an issue, as the first instance's red default branch does.
2. **Pull requests only where asked.** A pull request's head is run
   only if it changes a path the block lists, or carries a label.
3. **A gate later**, once the instance keeps up. This design says
   what that needs, and leaves the decision to a later one.

Each part is off unless the block sets it.

## Evidence

One project, the 24 hours to 02:05Z on 26 October. The second
instance ran on one macOS host with one slot.

* **187 verdicts, 15.2 hours of runs.** A run took a median of 314 s
  (90th percentile 421 s). The slot was busy most of the day.
* **The wait was the cost.** A head waited a median of 58 minutes for
  its run (90th percentile 2 hours). An agent asked that evening
  whether the queue was moving.
* **Most reds weren't the pull request's.** 81 of the 187 were red.
  71 were the same step, which had been failing on the default branch
  itself since about 22:25Z. Each of those heads waited an hour to
  report a red it had inherited.
* **The red that mattered was found by people.** The default branch's
  break showed up first as red pull request heads that agents
  recognised. An issue was opened by hand, and two fixes went into
  the queue. The instance itself said nothing about the default
  branch.
* **More than a hundred merges a day.** Running every tip would cost
  as much as today. Running only the newest tip whenever the slot is
  free costs at most one run per slot-time, about 11 an hour here,
  and each reports within one run of the merge.

## The change

New keys in a `checks.instances` block. Each is inert unless set.

### 1. The default branch's newest tip: `main: "newest"`

`main` already exists for an instance (designs/0011: `needed` or
`every`). It gains `newest`: when the slot is free, the instance checks
the default branch's current tip, unless a verdict for that tree is
already recorded. A tip that is overtaken while it waits is never
run.

The default-branch tip goes before any pull request's head, as the
first instance's traced run does.

### 2. A red tip opens an issue: `main_red_issue` in a block

Today `main_red_issue` is refused in a block, because it belongs to
the first instance. In a block it now means the same thing for that
instance:
- while the instance's newest default-branch verdict is red, one
  issue is open about it;
- the issue names the steps, the end of their output, and the merges
  since the last green tip on this instance;
- a green tip closes it.

Its title and labels are the block's own, so the two instances'
issues never merge.

### 3. Which pull requests run: `pull_requests`

`pull_requests: {"paths": [glob, ...], "label": "<name>"}`.
A head is run only when:
- its changes since the merge base touch a path matching one of the
  `paths`; or
- the pull request carries `label`.

Any other head gets no status from this instance. That is safe while
the Merger doesn't require the context, and it's why a block that sets
`pull_requests` must not also be required (see 4). Without the key,
every head is run, as today.

`paths` is a project's own judgement of what can break the platform:
its platform code, its build files, its test harness. It is kept by
hand, so it can miss something. Part 1 is the backstop: anything
`paths` misses shows up within one run after it merges, not an hour
later.

### 4. A gate later

Requiring the instance's context is still left to a later design. It
needs three things this design doesn't provide:
- **The instance keeping up with the queue.** Every candidate's head
  would need a verdict within minutes, as the first instance's
  predictions give. That takes more slots or hosts, or step skipping
  on the platform: a tracer for that OS, or `paths` per step.
- **A run of every candidate, so `pull_requests` can't apply.**
- **A record of how often it is red for reasons of its own.** Parts 1
  and 2 produce that record.

## Cost

* **Fewer runs.** At most one default-branch run per free slot-time,
  plus the pull requests that match `paths` or carry `label`.
* **A break is found after it merges, not before.** That is true
  today too: nothing gates on the instance. What changes is that the
  break is reported within one run of the merge, by an issue, rather
  than by people noticing red heads an hour later.
* **Authors of platform code still hear before merging**, through
  `paths`. Anyone else who wants a platform run can ask for one with
  the label.

## Configuration

| Key (in a block) | Default | What it is |
| --- | --- | --- |
| `main` | `needed` | Gains `newest`: check the default branch's current tip whenever the slot is free, skipping tips overtaken while waiting. |
| `main_red_issue` | none | Allowed in a block: the instance's own red-default-branch issue, as `checks.main_red_issue`. |
| `pull_requests` | none | `{"paths": [...], "label": ...}`: run only heads that change a matching path or carry the label. None runs every head. |

## Order of work

1. `forge-pr-check`: `main: "newest"` for an instance, and
   `main_red_issue` in a block; tests.
2. `forge-pr-check`: `pull_requests`; tests.
3. `docs/config.md`, and a release.

A project then sets the keys in its own block.
