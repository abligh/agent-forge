# 0051: A red nightly has one lead and an issue per step

* Status: approved (Alex Bligh, 2026-11-10; https://github.com/abligh/agent-forge/issues/268)
* Author: XMPPEngineer
* Date: 2026-11-10

## Summary

`forge-nightly` keeps one issue open while the nightly is red
(designs/0028), labelled with the owner of every step that has failed
on it. This design makes three changes, each off until a project
sets it:

1. **One lead.** `nightly.lead` names one agent who owns getting the
   whole nightly green. The red-nightly issue carries her Owner label
   and no other.
2. **An issue per red step.** Each failed step gets a step issue of its
   own, labelled with that step's owner. The step issue closes itself
   when the step next passes, and the red-nightly issue lists the step
   issues.
3. **Everyone is offered the fix.** While the nightly is red, the
   router's idle offer gives every idle developer, not only the step's
   owner, the open step issues before anything else. One notice goes to
   the project's room when the nightly turns red.

Merging carries on as before. Nothing here holds a pull request.

## Evidence

On one project, a red-nightly issue stayed open for 40 hours across 12
red runs:

* **Owners piled up, and nobody owned the whole.** The issue gained
  three Owner labels, one for each night's new failing step, and lost
  none. Each step owner fixed their own step and wrote that their part
  was done. Nobody was answerable for the nightly going green.
* **One step was attributed to the wrong agent.** `nightly.owners`
  mapped it to one agent, but the tests that failed were another's.
  The two of them sorted it out in comments.
* **One failing check had no owner.** It was filed by hand without an
  Owner label, and it waited until a person noticed.
* **Reports, not progress.** The issue reached 48 comments: 20 from
  the tool, and the rest status reports from four agents.

`red_first` (designs/0039) did make the issue its owners' turn. But
with three owners and one issue, "the owner" meant whichever step each
agent read as theirs.

## Design

### 1. The lead

With `nightly.lead` set, an agent of `agents`:

* the red-nightly issue is opened with `<owner_prefix><lead>` and
  `nightly.labels`, never with step owners' labels. A later red run adds
  no Owner labels to it.
* the issue's body says who leads and what that means: the lead drives
  the nightly to green, chases step owners, takes any step without an
  owner or attributed to the wrong agent, and says when it is green.
* `red_first` (rule 20) makes the red-nightly issue the lead's turn,
  as it makes it the owner's today. Its fallback (rule 21) applies to
  step issues instead (§3).
* the escalations at `escalate_hours` are unchanged, and their text
  names the lead.

Without `nightly.lead`, the red-nightly issue is labelled as today.

### 2. Step issues

With `nightly.step_issues` set to `true` (it needs `lead`), each red
run, for each failed step of `nightly.job`:

* **No open step issue for that step name:** one is opened, titled
  `The nightly's <step> is red at <sha>`, labelled `nightly.labels`
  and `<owner_prefix><owner>`, where the owner is `nightly.owners`'
  agent for the step, or the lead when no entry matches. It carries the
  step's output, as the red-nightly issue does today, plus a
  `Nightly-step: <workflow> <step>` line, by which it is found again,
  and a link to the red-nightly issue. The red-nightly issue then
  quotes only a list: each red step, its owner and its issue.
* **An open step issue:** one comment, `Red again at <sha>`, with the
  output, as the red-nightly issue gets today.

A step issue closes when a later run of `nightly.job` shows that step
succeeding (`conclusion: success`), with a comment naming the run.
A step that was skipped or did not run proves nothing, and its issue
stays open. This matters for a retry with `first`, which may stop after
the first failure. The first green run of the whole job closes every
step issue and the red-nightly issue, as now.

A step issue whose owner is wrong is fixed by hand: its Owner label is
changed, and a later red run of the step doesn't add the configured
owner back. Only a newly opened step issue takes `nightly.owners`.

### 3. The router

* **Step issues are red builds.** forge-nightly writes the open step
  issues to nightly.json (`steps: [{name, issue, owner}]`), and
  `agent_forge/nightly.py` reads them for the router. Under
  `red_first`, each step issue is its owner's turn, as the red-nightly
  issue is today, with the same release by a referencing pull request
  or an owner's comment.
* **Everyone is offered them.** With `router.red_first.everyone` set to
  `true`, the idle offer (rule 15) offers every idle developer the open
  step issues, oldest first, before her own issues, whoever owns them.
  It doesn't wait for `fallback_hours`, and skips a step issue that an
  open pull request already references. Without it, the fallback works
  as today, but per step issue.
* **Fixes.** `nightly.fixes_named` counts a pull request that closes or
  names a step issue as a fix of the nightly, so it goes first and its
  merge brings the early rerun forward (designs/0048 §4). A step issue
  is linked with the red-nightly issue by construction.
* **`nightly_wake`** (designs/0040) is unchanged. It wakes issues
  labelled as waiting for a nightly, and step issues aren't.

### 4. The room notice

With `nightly.announce`, a name or room JID the relay resolves, the
run that opens the red-nightly issue sends one message there: `The
nightly is red at <sha>: <step> (<owner>), …. <lead> leads: #N.
Merging continues; idle developers, take a red step first.` A
later red run sends nothing, and a green run that closes the issue
sends `The nightly is green again at <sha>.` A message the relay
refuses is retried on the next tick, as escalations are, and is then
dropped after an hour.

### 5. Reruns

Reruns are unchanged. A rerun is dispatched after a fix merges, or
after `retry_hours`, with `first` set to the red steps. Which jobs a
rerun runs is the workflow's own business: a workflow may skip
everything but `nightly.job` when `first` is set. Its per-step results
then close or comment on step issues as in §2.

## Configuration

All new keys are optional, and each is inert without the ones it needs.

| Key | Default | What it is |
| --- | --- | --- |
| `nightly.lead` | none | The agent, one of `agents`, who owns getting the nightly green. The red-nightly issue carries her Owner label alone. |
| `nightly.step_issues` | `false` | `true` opens one issue per failed step, labelled for the step's owner (the lead if none), and closes it when the step next succeeds. Needs `lead`. |
| `nightly.announce` | none | Where the relay posts one notice when the nightly turns red and one when it is green again: a name or room JID it resolves. |
| `router.red_first.everyone` | `false` | `true` makes the idle offer give every idle developer the open step issues first, without waiting for `fallback_hours`. Needs `nightly.step_issues`. |

## Trade-offs and failure modes

* **More issues.** A night with five red steps opens six issues
  instead of one. They close themselves, and each has one owner, which
  is the point. A step that stays red keeps one issue, commented on
  once a run.
* **Step names must be stable.** A renamed step looks like a new red
  step, and its old issue stays open until the whole job is green. The
  lead closes it by hand.
* **Everyone offered may mean two agents on one step.** The offer
  skips a step issue that an open pull request references. Before a
  pull request exists, the first to comment takes it, as the idle offer
  does today.
* **No merge stop.** Merging continues. A project that wants to stop
  merges while the nightly is red needs a design of its own.

## Testing

* A red run with `lead` set opens the red-nightly issue with the
  lead's Owner label alone. A second red run with another failing step
  adds no label.
* With `step_issues`, a red run opens one step issue per failed step,
  labelled for its owner, and the lead for a step `owners` doesn't
  name. A second red run of the same step comments and doesn't open.
* A later run in which the step succeeds closes its issue. One in
  which it is skipped does not. A green run closes all of them.
* A changed Owner label on a step issue survives a later red run.
* nightly.json's `steps` lists the open step issues. Under
  `red_first`, each is its owner's turn, and with `everyone` an idle
  developer who owns none is offered one first.
* `fixes_named` counts a pull request naming a step issue.
* `announce` sends once on red and once on green, and retries a
  refused send.
* Config validation for the four keys and their dependencies.
  `--dry-run` writes nothing.

## Implementation plan

1. Config schema and validation. docs/config.md.
2. forge-nightly: the lead, step issues, nightly.json's `steps`, and
   the announce message, with tests.
3. `agent_forge/nightly.py` and rules 15, 20 and 21 for step issues and
   `everyone`, with tests. docs/router.md.
4. A release. A project turns it on with one config change.
