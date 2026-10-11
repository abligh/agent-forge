# 0004: Pull requests nobody is woken for

* Status: approved (Alex, 2026-10-11, at 8be7570,
  <https://github.com/abligh/agent-forge/issues/4#issuecomment-6105751563>)
* Implemented: v0.4.10 (7f1c89a), each key off until configured
* Author: XMPPEngineer
* Date: 2026-10-11

## Summary

One morning, a project had 24 open pull requests, several of them
hours old. Some were waiting for good reasons: a question to the owner,
a review, a build. But several were stuck in ways no rule noticed, so
nobody was woken for them:

* **A pull request whose base had already merged.** Its author stacked
  it on another pull request's branch. That pull request merged, but the
  branch stayed, and the Merger held the stacked one ("based on X, not
  main") for three and a half hours after its approval. The router
  woke nobody, because an approved pull request is the Merger's.
* **A pull request blocked by the issue it closes.** Its body said
  `Blocked-by: #N`, where #N was the issue it fixed. #N stays open until
  the pull request merges, so the Merger would never have taken it.
* **Drafts with no recorded wait.** Six drafts in one stack had waited
  five hours on a blocker that had merged. Their author kept them as
  drafts for a reason that no longer applied, and nothing on them said
  what they were waiting for. Rule 1 says a draft is nobody's turn, so
  nobody asked.

This proposes three rules for the default rules module, each off until
configured: `router.stuck.retarget`, `router.stuck.self_blocked` and
`router.stuck.draft_hours`.

## Goals

* **A stuck pull request is someone's turn**: its author's, with what to
  do.
* **A wait that is recorded is respected.** A draft that says what it
  waits for is left alone.
* **Each is said once per state**, not on every run.

## Non-goals

* **Fixing them automatically.** The Merger doesn't retarget a pull
  request or edit its body. Its author does.
* **Changing what the Merger merges.**

## Design

The three rules come before rule 1, so they apply to drafts too. The
first that matches decides. Each makes the turn the pull request
author's.

### Retarget

`router.stuck.retarget: true`. A pull request whose base is not the
default branch, when no open pull request has that base as its head
branch: either the pull request it was stacked on has merged or
closed, or there never was one. The message names the base and says to
retarget the pull request to the default branch, merging the default
branch in first if needed. The event is keyed on the pull request and
its base, so it is said once until the base changes.

### Blocked by what it closes

`router.stuck.self_blocked: true`. A pull request with a
`Blocked-by: #N` line, in the Merger's notation, where #N is also one
of its closing references: GitHub's `closingIssuesReferences`, which
come from `Closes #N`, `Fixes #N` and the like. The message says the
Merger can never take it, and to replace the line with `Closes #N`.
The event is keyed on the pull request and the body's last edit.

### A draft with no recorded wait

`router.stuck.draft_hours: H`. A draft that has had no new commit, no
body edit and no comment by its author for H hours, and records no
wait. A wait is recorded when the draft has a `Blocked-by:` line naming
an open item, a `Defer-until:` time still to come, or a
`Status:waiting-*` label. The message says how long it has sat, and
asks the author to un-draft it, or to record what it waits for. The
event is keyed on the pull request and its last activity, so a draft
is asked once per idle stretch.

## Config and file-format shape

In a project's `.agent-forge/config.json`, beside the existing keys:

```json
"router": {"stuck": {"retarget": true, "self_blocked": true,
                     "draft_hours": 2}}
```

`docs/config.md` defines each key. The router's pull request query
gains each pull request's base and head branch names and its closing
references, if it doesn't have them already.

## Trade-offs and failure modes

* **A stack of drafts asks once per draft.** A stacked draft whose
  base is still an open pull request isn't asked to retarget. Only the
  idle-draft rule applies to it, and a `Blocked-by:` line on the draft
  below it satisfies that.
* **An author who wants a draft parked must say so**, with a line or a
  label. That is the point: an unrecorded wait looks the same as a
  forgotten one.
* **A pull request based on a long-lived branch with no pull request**
  (a release branch, say) would be asked to retarget. A project that
  works that way leaves `retarget` off.

## Testing strategy

* **agent-forge's tests** for each rule: when it matches and when it
  doesn't (a recorded wait, an open base pull request, a `Blocked-by:`
  that isn't a closing reference), and that each event is said once.
* **In a project, a dry run of the router** with the keys set, compared
  with the open pull requests by hand.

## Implementation plan

1. The three rules, off by default, in one release.
2. Each project turns on what it wants in its own configuration.

## Open questions

* **H.** Default suggestion: 2 hours.
