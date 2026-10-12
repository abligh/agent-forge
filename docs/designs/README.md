# Designs

A change to what agent-forge's tools decide (a new rule, a new kind of
hold, a new message) is designed here, before or alongside its code, so
that a project turning it on can point at one document instead of
writing its own.

## Where a design lives, and who approves it

* **Here, numbered** `NNNN-short-title.md`, with a status line.
* **The owner approves it**, on an issue in this repository titled
  `NNNN: ready for approval`, by a comment of their own. The status line
  is then amended to `approved (<name>, <date>, <link to the comment>)`.
* **Code may land before approval, off by default.** Every rule a
  design adds has a configuration switch, and is inert until a project
  sets it. Approval is what lets a project set it.

## What a project does

A project turns an approved feature on with one change to its own
configuration, under its own review rules, linking the design here.
Whether that change needs more than the project's usual review is the
project's rule, not agent-forge's.

## Lifecycle

0. **Draft**: an idea written down to be examined or measured
   against. It is not up for approval, and nothing is built from it.
1. **Proposed**: written, with the evidence that motivates it.
2. **Approved**: the owner's comment on the approval issue, cited in
   the status line.
3. **Implemented**: the release that has it, in an `Implemented:` line.
4. **Superseded or abandoned**: by a later design, or with the reason.

## Staying neutral

A design names no project. Evidence from a project is described by what
happened ("on one day, 61 hand-backs of 34 pull requests"), not by whose
repository it happened in, so that `tools/check-neutral` passes.
