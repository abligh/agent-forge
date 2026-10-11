# 0008: Every issue has an owner

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-11

## Summary

The router decides whose turn an issue is from its `Owner:<Name>`
label. On one project, many open issues had no such label: an agent
had filed a batch of bugs it meant to fix itself, and the owner later
labelled them by hand. Until then:

* **Answers woke nobody.** Rule 12 ("someone commented since the
  owner last did") needs the label. A reviewer's question on an
  unlabelled issue sat until its author happened to look.
* **The idle offer passed them over.** Rule 15 offers an idle agent
  only issues labelled with its name, so an agent with nothing to do
  wasn't offered its own bugs.
* **Issues nobody had claimed stayed unclaimed.** An issue the owner
  filed without a label belongs to nobody, and nothing says so.

Some rules already fall back to the author when there is no label
(rules 10, 13 and 14). Rule 12 and the idle offer don't.

This proposes `router.owners`: every rule reads an issue's owner the
same way, falling back to the author, and an issue that still has no
owner is the planner's to assign.

## Goals

* **One reading of "the owner" in every rule.** The label, else the
  author when the author is an agent.
* **An issue with no owner is someone's turn.** Whoever plans the work
  is asked to label it, once.
* **Labels stay the owner's tool.** A label always wins over the
  fallback, so reassigning work is still one label change.

## Non-goals

* **Pull requests.** Their turns already go to the author, or to a
  reviewer, never by label. The label on a pull request stays a
  convenience for people.
* **Choosing the owner.** The router asks the planner; it doesn't
  guess from paths or history.

## Design

### Whose issue it is

An issue's owner is:

1. the agent its first `Owner:<Name>` label names, when that agent is
   in the configuration;
2. else its author, when the author is an agent in the configuration
   (machinery Apps excepted);
3. else nobody: the issue is **unowned**.

A label naming nobody in the configuration (a typo, or an agent since
removed) is ignored, so the issue falls to step 2 or 3, and the turn
under rule 18 below says why. Rules 10, 12, 13, 14 and 15 all use this
reading.

### Rule 18: an unowned issue

An open issue that is unowned and is not waiting on someone outside
(`Status:waiting-external`) is `owners.triage`'s turn: "has no owner:
label it `Owner:<Name>`", or "its `Owner:` label names nobody". The
event is when the issue was created or last became unowned (the last
label change), so it is said once and reminded on the usual schedule.

Without `owners.triage`, the issue is surfaced (printed on every run,
as rule 7 is) instead of being anyone's turn.

### What stays the same

Pull request rules, the waiting labels and the engine are unchanged.
An issue labelled for an agent behaves exactly as now.

## Config shape

```json
"router": {"owners": {"triage": "LeadDev"}}
```

`docs/config.md` defines `owners` and `triage` (an agent in the
configuration). `"owners": {}` turns on the fallback alone, with
unowned issues surfaced rather than routed.

## Trade-offs and failure modes

* **More turns for authors.** Comments on an agent's unlabelled issue
  now wake the agent, as they would have done if the issue had been
  labelled. That is the point, but a project with many stale
  agent-filed issues will see a burst of them on the first run.
* **One turn per unowned issue for the planner.** On the first run,
  that is every unowned issue at once. The same burst is the evidence
  that it was needed; after it, only new issues arrive.
* **An agent that files an issue for someone else must label it.**
  Otherwise the fallback makes it the filer's. This is the same as
  what an unlabelled issue means to a person reading it.

## Testing strategy

agent-forge's tests cover:

* each step of the owner reading, including a label naming nobody and
  a machinery App as author;
* rule 12 and the idle offer for an agent-filed issue with no label;
* rule 18 to `triage`, its event, and a label change re-raising it;
* rule 18 surfaced without `triage`;
* everything unchanged without `router.owners`.

## Implementation plan

1. `router.owners`, off by default, in one release.
2. A project turns it on in its configuration, in a pull request its
   owner merges.

## Open questions

* **Writing the label.** The router could add `Owner:<author>` to an
  agent-filed issue itself, so the label people see matches what the
  router reads. It would be the router's first write to GitHub, so it
  is left out unless the owner wants it.
