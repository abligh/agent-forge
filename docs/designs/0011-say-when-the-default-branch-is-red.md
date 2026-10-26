# 0011: Check every default-branch head, and say when it is red

* Status: approved (Alex, 2026-10-12, at f3bd070,
  <https://github.com/abligh/agent-forge/issues/20#issuecomment-6120521112>)
* Implemented: v0.4.37, off until configured. A matched head is
  logged `MAIN-GREEN … matched`; the issue's state is `main_red` and
  `main_green` in state.json; issues are written only by a posting
  (`--post`) run.
* Author: XMPPEngineer
* Date: 2026-10-12

## Summary

forge-pr-check checks the default branch's head only for
`checks.depends`: main's tip is queued for a traced run when a
dependency record needs one, and not otherwise. With `depends` off, the
default branch is never checked at all. With `fresh` on, it is checked
only when a record is stale, and then only the stale steps. Either
way, when a run is red, it logs `MAIN-RED` and tells nobody.

A project moving to forge-pr-check from its own main check found both
of these. Its main check ran every step on each new tip and filed an
issue while the tip was red. Under forge-pr-check it would lose both,
and nothing would say so.

The default branch is what every pull request is checked against. A
red tip makes every pull request's verdict suspect (`base_red` already
marks them). It should be found, and someone should be told.

## Design

Two keys in `checks`, both off by default.

### `checks.main: "every"`

Every new head of the default branch is queued for a full run of every
step, whatever `depends` and `fresh` would ask for. With `depends`,
that run is traced and writes its record as it does today. Without it,
the run is untraced. The rest of today's rules for main's run stay:
- it goes first when due (`depends_minutes`, renamed in the docs to
  mean "the default branch's head" rather than "the traced run");
- an older default-branch head that isn't running is dropped in
  favour of the newest;
- it is never overdue under `max_wait_minutes`.

**A head already proven green isn't run again.** If a green verdict
here has the same tree, it stands for the head and is logged
`MAIN-GREEN … matched`. That verdict can be the Merger's prediction
under `merge.test_ahead`, or a pull request's head that was
fast-forwarded. This is the common case with `test_ahead`, so the
extra cost is close to nothing there.

The default, `"needed"`, is today's behaviour.

### `checks.main_red_issue: {"label": "...", "title": "..."}`

While the default branch is red, there is one open issue about it,
written by `poster`:
- **The first red head opens it.** The issue names the head, each
  failed step with the tail of its output, and the commits since the
  last green head (`git log green..red`). It gets `label`, so whoever
  that label wakes is told. `title` is a template with `{sha}`.
- **Each further red head adds one comment.** It names the head and its
  failed steps. A tick never comments, only a new verdict does.
- **The first green head closes it** with a comment naming the head,
  or the pull request whose matched verdict proved it.

The issue's number is kept in state.json as `main_red_issue`. A post
that fails is logged (`REPORT-FAILED`) and retried on the next verdict.
It is never dropped silently.

A flaky red, followed by a green on the next head, still leaves an
issue that opened and closed: a record of the flake.

`main_red_issue` needs a default-branch run to report on, so it is
refused with `main: "needed"` unless `depends` is on. Even then the
validator warns that only stale steps are run.

## Why not …

* **Post a commit status on the default branch's head and let GitHub
  notify.** Nobody watches commit statuses on main; agents are woken
  by issues and labels.
* **Let the router notice `MAIN-RED` in the log.** The router reads
  GitHub, not this host's logs, and the issue is the durable record
  anyway.

## Cost

A full run per default-branch head that no green verdict matches. With
`test_ahead`, most heads are matched. Without it, there is one run per
merge, which is what the main check being replaced already cost.

## Evidence

The second project's main check has filed an issue on each red tip
since its own design. Its operators rely on it: it is how a flaky test
or a bad merge gets an owner.

## Amendment (2026-10-26): its fix goes first, and a red it caused names no suspect

* Status: proposed
* Author: XMPPEngineer

### Why

Two pull requests passed alone and together turned the default
branch red. The fix was approved and green within twenty minutes, but
it went to the back of a 36-long queue: nothing put it first. Until it
merged, two things went wrong:
- every run that reached the broken step failed, each with "(itself
  red here)";
- a batch whose top contained that step would go red. Under
  designs/0035 its search would then end by naming a member that had
  nothing to do with it and handing it back.

A red nightly's fix already goes first (designs/0028). A red default
branch is the same problem.

### 1. A pull request that closes the open issue goes first

While the `main_red_issue` issue is open, each open pull request that
closes it is treated as if it carried `labels.urgent`, both by
forge-pr-check (it is run first) and by the Merger (it is merged and
predicted first). "Closes" means what designs/0028 means:
- GitHub's closing references;
- `Closes`/`Fixes`/`Resolves #n` in the title, the body (outside code)
  or a commit message.

forge-pr-check works out the numbers each tick, from the pull requests
it already lists, and keeps them as `main_red.fixes` in state.json.
The Merger reads them there, as it reads the nightly's from
forge-nightly's state. Once the issue is closed the list is empty, and
the pull requests go back to their ordinary places. A person's
`labels.urgent` is unchanged.

### 2. A batch red only where its base is red is not searched

The Merger looks at the red verdict on a batch's top tree. If every
step it failed also failed in the default branch's own run of the
batch's base, the red is the base's. Then:
- the top is treated as not yet judged, not as red;
- no suspects are taken and nothing is handed back;
- `BATCH-BASE-RED #a..#b: red only in <steps>, as <base> is` is
  logged once.

Once the base changes, because a fix merged, the batch is predicted
again on the new base. If the top fails a step the base passed, the
red is the batch's own, and designs/0035's search runs as now.

The same test covers a pull request handed back as red: a head whose
merged run is red only where the base is red ("itself red here") is
held as `base red`, not handed back.

### Cost

One list over the pull requests already fetched, a tick. While the
default branch is red and nobody has fixed it, nothing whose red is
the base's merges. With part 1 the fix is first in the next batch, so
that is one batch's run.

### Steps

1. forge-pr-check: `main_red.fixes`, taken as urgent; tests.
2. forge-merger: those as urgent; `BATCH-BASE-RED`, and the base-red
   hold for a single pull request; tests.
