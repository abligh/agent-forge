# 0049: The router tells a person when an agent can't be reached

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-11-05

## Summary

`forge-router` wakes an agent when an item becomes her turn, and
reminds her while it stays her turn. Nothing tells a person when that
isn't working: when an agent is reminded again and again and doesn't
act, or when her wakes can't be delivered at all because no session of
hers is in the room. Both look like an agent who is busy. They are
found only when a person notices that something has sat for a day.

This design adds `router.escalate` to the router engine, off until a
project sets it. With it, a sending run opens one issue per agent,
labelled for the project's owner, when either:

* **(a) a turn is reminded and not taken up.** A turn decision has
  had `after_reminders` delivered reminders and is still decided; or
* **(b) the agent can't be reached.** Every wake to her has gone
  undelivered for `undelivered_minutes`.

A later trigger for the same agent comments on her open issue rather
than opening another. The issue closes itself, with a comment saying
why, as soon as the agent takes a wake, or the turn that triggered it
stops being decided, whichever comes first. A relay that answers
nobody is one fault, not one per agent, and gets one issue of its own.

## Evidence

One project's `wakes.jsonl` and `state.json`, over nine days:

* **Turns only, `after_reminders` 4:** one turn would have escalated:
  a claimed issue that had 35 reminders without being taken up.
* **Counting relooks as well:** two more items would have escalated,
  with 9 reminders each. Both were parked items that a relook rule
  re-checks daily by design, and whose wait was already on the owner's
  label. So only turn decisions count (Design §1).
* **Undelivered wakes:** a session restart left one agent without a
  session in the room for 23 minutes. That is ordinary and must not
  escalate, hence the advice of at least 60 minutes (Configuration).

Today the router records only delivered wakes in `state.json`: each
turn's `woken_at` and `reminders`. A wake the relay refuses stays due
and is retried every run, and leaves only a `not sent:` line in
`wakes.jsonl`. So (b) needs new state; (a) can be judged from what is
recorded.

## Design

### 1. Trigger (a): a turn reminded and not taken up

On a sending run, for each live decision of kind `turn` whose
recorded entry has `reminders` at or above `after_reminders`, the
turn is escalated for its agent. Notices and relooks never count:
a relook re-checks a parked item on purpose, and its wait is already
someone else's.

`reminders` counts delivered reminders only (`reminders_sent`), so
(a) means the agent was told N more times and the turn is still hers.
A reminder taken by any of her sessions counts, her twins' included:
a twin shares her App and may be the session holding the item.
A turn whose decision has no reminder (`router.ONCE`) never reaches
(a). The count is the engine's, so (a) works the same under any rules
module.

### 2. Trigger (b): an agent who can't be reached

`escalate.json` in the router's state directory records, per agent,
`undelivered_since`: the time of the first run in which a wake to her
was due and no session of hers took it (the relay's `404 unknown-name`
for every session, or any other refusal except the relay being
unreachable). The next run that delivers a wake to her clears it.

When `undelivered_since` is older than `undelivered_minutes`, she is
escalated. An agent with `away` set (her stand-in works for her) is
never escalated under (b): her absence is already known.

### 3. A relay that answers nobody

When a run finds the relay unreachable (`relay unreachable`, which
already stops the run posting), or every agent with a wake due was
refused, the fault is the relay's or the room's, not each agent's.
`escalate.json` records `relay_down_since` instead, and per-agent
`undelivered_since` is not set by that run. After
`undelivered_minutes`, one issue is opened for the relay. It closes
when a run delivers any wake.

### 4. The issue

* **One per agent**, titled `forge-router: <agent> can't be reached`
  for (b) and `forge-router: <agent> hasn't taken up <item>` for (a);
  the first trigger decides the title. It is opened as the Merger's
  App, which the router already reads GitHub as, labelled `label` (by
  default `labels.waiting_on_owner`) and `<labels.owner_prefix><agent>`.
  That label is the owner's list, so by default the issue mentions and
  assigns nobody: a mention would add an email on top. With `to` set,
  the body mentions that login, and still assigns nobody. It says what
  triggered it: the item and its reminders for (a), or since
  when and the relay's answer for (b), and what a person might do
  (look at her session, restart it, or give the item to someone else).
* **Later triggers comment** on the open issue: another turn reaching
  (a), or (b) after (a). A trigger is commented on once; the issue
  isn't re-told on every run.
* **It closes itself** with a comment saying why, when the agent takes
  a wake (any delivered wake, which ends (b) and shows she is there),
  or when every turn that triggered (a) stops being decided, whichever
  comes first. The label comes off as it closes.
* `escalate.json` holds each open issue's number and what it was
  opened and commented for. A person closing the issue by hand is
  read on the next run and treated as answered: the same trigger
  doesn't reopen it, and a new one opens a new issue.

### 5. Runs

Only a sending run that isn't `--only`'s escalates, after delivery and
recording and before `each_run`, under the lock, as the Merger's App.
A failed GitHub write is printed and journalled in `wakes.jsonl` as a
`run` record and tried again next run; it never changes the exit
status. A dry run prints `WOULD ESCALATE <agent>: <why>` and writes
nothing. `--seed` never escalates.

## Configuration

```json
"router": {
  "escalate": {
    "after_reminders": 4,
    "undelivered_minutes": 120,
    "label": "Status:waiting-owner"
  }
}
```

| Key | Default | What it is |
| --- | --- | --- |
| `after_reminders` | none: (a) is off | Delivered reminders of one turn after which it escalates. At least 1. |
| `undelivered_minutes` | none: (b) and §3 are off | How long an agent's wakes may all go undelivered before she escalates. At least 30; 60 or more is advised, since a session restart leaves an agent out of the room for some minutes. |
| `label` | `labels.waiting_on_owner` | The label the issue is opened with. |
| `to` | none: nobody is mentioned | A GitHub login the issue's body mentions. Never assigned. |

At least one of `after_reminders` and `undelivered_minutes` must be
set. `forge-config check` validates the section: whole numbers in
range, non-empty strings, no other keys. With a reminder interval of
six hours, `after_reminders` 4 is about a day unanswered.

## Trade-offs and failure modes

* **An agent who is busy, not gone.** (a) can fire on an agent who has
  the turn in hand but hasn't changed anything the rules read. That is
  what the issue is for: a person asks. A project that finds it noisy
  raises `after_reminders`.
* **An agent stopped on purpose.** With wakes due, she escalates under
  (b) unless she has `away` set. That is the setting that says so.
* **Noise from a flapping relay** is bounded by `undelivered_minutes`:
  only an unbroken stretch of refusals counts, and one delivery
  resets it.
* **Two routers.** The issue is the engine's, keyed by agent, so a
  project with one router per host still opens one issue per agent
  only if they share a state directory. Projects run one router.

## Testing

Unit tests with a faked relay and GitHub:

* (a) fires at `after_reminders` for a turn, never for a notice or a
  relook, and not for a `ONCE` turn;
* (b) sets `undelivered_since` on the first refused run, clears it on
  delivery, fires after `undelivered_minutes`, and skips an agent with
  `away`;
* an unreachable relay sets `relay_down_since`, not per-agent times,
  and opens one relay issue;
* a reminder taken by a twin session counts toward (a);
* a second trigger comments, not opens; the issue closes on a
  delivered wake, and on the triggering turn ceasing to be decided;
* without `to` the body mentions nobody and no one is assigned; with
  it, the body mentions that login and no one is assigned;
* a dry run and `--seed` write nothing; config validation.

## Implementation plan

1. Config schema and validation for `router.escalate`; docs/config.md.
2. `escalate.json` and trigger bookkeeping in `bin/forge-router`,
   with the tests above; README and docs/router.md.
3. A release. A project turns it on with one config change.

## Settled questions

1. **Twins count.** A reminder delivered to any of the agent's
   sessions, twins included, counts toward (a): a twin shares the App
   and holds the item.
2. **No default `to`, and no assignment.** The `labels.waiting_on_owner`
   label is the owner's list; a mention on top would only add email.
   `to`, when set, is a login mentioned in the body, never assigned.
