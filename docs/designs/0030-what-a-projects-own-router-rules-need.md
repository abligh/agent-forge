# 0030: What a project's own router rules need

* Status: proposed
* Author: XMPPEngineer
* Date: 2026-10-24

## Summary

docs/router.md split `forge-router` into an engine and a rules module,
so that a project whose routing differs can bring its own rules. Its
last step, a project writing its module and switching to it, is under
way. That project's router has 111 rules. Checked one by one against
the engine and the default rules, 25 are covered already, 65 belong in
the project's module, and 11 are no longer needed. The other 10 can't
be written in a module today. They need five engine hooks and three
settings, and switching to the module needs a sixth hook, a first run
that wakes nobody. This design adds all nine. Each is off, or changes
nothing, until a module or a configuration uses it.

Engine hooks:

1. **Merged pull requests carry the module's fields** (`graphql["Merged"]`).
2. **The previous run's decisions** in `ctx.previous`.
3. **Actions once a run** (`each_run`), not tied to a turn.
4. **A turn already woken** by someone else, recorded without a wake
   (`Decision.woken`).
5. **A grace period before a turn is forgotten** (`router.forget_minutes`).
6. **`--seed`**: a first run that records turns without waking anyone.

Settings:

7. **Per-agent `areas` and `offer`.**
8. **`router.quiet_labels`.**
9. **`router.merge_notice.skip_label` and `merge_notice.to`.**

## Evidence

The project's router is three tools and an hourly idle check. Every
rule was listed with its source, and each was marked against the engine
and the default rules at 9a1489a. The rules that need something new:

* **A merge notice for the item's owner, not its author.** The project
  tells the agent named by the `Owner:` label when a pull request it
  owns merges, and skips pull requests labelled not to follow up.
  `ctx.merged` has `number`, `title`, `mergedAt` and `author` only, so
  a module can't see labels on a merged pull request.
* **Only pull requests that were someone's turn.** The same notice
  goes out only for a pull request that was someone's turn when it was
  last routed. A drafted or parked pull request that merges tells
  nobody. No module can know what an earlier run decided.
* **A red default branch that recovers.** A workflow whose three newest
  runs on the default branch failed is reported once. When it passes
  again, that is reported once too. A module can key the first report
  on the first red run. It can't tell that a workflow it reported
  last run is green now without a record of what it reported.
* **A heartbeat.** After every run that read the queue, the project
  rewrites a status block in one issue's body: who waits on what, and
  which workflows are red. It also posts a status on each open pull
  request's head saying whose turn it is. Actions run only for a due
  decision, so a heartbeat would need a new turn key on every run.
* **"Woken by hand."** When someone writes `Woke: <Agent>` on an item,
  that agent was told some other way. The item stays her turn, but she
  isn't woken for it again. A decision can't say that: a new key is
  always woken.
* **A flapping item.** An item that is decided on one run, not on the
  next, then decided again with the same event is woken twice. The
  engine forgets a turn as soon as a run doesn't decide it. The
  project's router had a six-hour cooldown per agent and item, which
  survived the flap.
* **Areas and offers.** The project offers idle agents pool items by
  area, first their own, then unowned, then other agents'. It keeps the
  areas (paths and labels) and an `"offer": false` flag in a file of
  its own, because the configuration's agent entries can't hold them.
  docs/design.md already promises per-agent areas, and docs/config.md
  has none.
* **Labels that silence an item.** Some labels make an item nobody's
  turn whoever owns it: a parking label, a venue label, a liveness
  marker. The default rules know the waiting-on-the-owner and
  waiting-on-someone-outside labels, but no others.

## Design

### 1. Merged pull requests carry the module's fields

A module's `graphql` may have a `Merged` entry: fields added to the
merged search's `PullRequest` fragment, as `PullRequest` and `Issue`
are for open items. Connections it names in `flatten` are flattened
there too. So `"Merged": "labels(first:20){nodes{name}}"` gives each
`ctx.merged` entry a `labels` list. Without the entry, the search
fetches what it does today.

The default rules add `labels` when `merge_notice.skip_label` or
`merge_notice.to: "owner_label"` is set (9, below).

### 2. The previous run's decisions

Each sending run that read the queue writes `previous.json` in the
state directory: by item key, the decisions it made (agent, rule,
event, kind, and whether it was suppressed), with the run's time. An
`--only` run replaces only the keys of the items it routed. A dry run
writes nothing.

`ctx.previous` hands it to the module as read at the start of the run:
`{"at": time, "items": {key: [ {agent, rule, event, kind, suppressed},
… ]}}`. It is None on the first run, or when the file can't be read.
A module must take None to mean unknown, not that nothing was decided
before. Like `merged` and `directory`, it is read only for a module
that asks: `wants_previous = True`.

This is the record docs/router.md expects a later step to add. It
covers the notice that goes out only for a pull request that was
someone's turn: was it in `previous` with an agent and not
suppressed? It also covers the red workflow that recovers: did the
last run have a `"-workflow-red"` decision for it?

### 3. Actions once a run

A module may define `each_run(ctx) -> Sequence[Action]`. A sending run
that read the queue, and isn't `--only`'s, runs those actions once,
after delivery and recording, under the router's lock, through the
engine's GitHub client. The actions are the same `Action` type a
decision carries, so a module can write its own (rewrite a block in
an issue body, post a commit status) with `describe` and `run`. A dry
run prints each (`WOULD …`). A failed one is printed (`ACTION FAILED:
…`) and journalled as a `run` record. Like a failed decision action,
it doesn't change the exit status: the next run tries again, since
the actions run on every run.

A module that wants the heartbeat only every N minutes compares
`ctx.when` with what it wrote last in `state_dir`, and an `Action`
records the time.

### 4. A turn already woken

`Decision.woken`, a string saying why (`"Woke: comment 6390…"`), or
None. A due decision with `woken` set isn't sent. It is recorded as
woken at this run's time and journalled as `marked woken: <why>`. Its
actions are done, as for a sent wake. From then on it is an ordinary
recorded turn: its reminders run from that time, and a new event is a
new turn. On a turn already recorded, `woken` has no effect.

### 5. A grace period before a turn is forgotten

`router.forget_minutes` (default 0, which is today's behaviour). Each
recorded turn keeps `decided_at`, the last run that decided it. A run
that doesn't decide a turn forgets it only when `decided_at` is more
than `forget_minutes` old. A turn recorded before this change has no
`decided_at`, and its `woken_at` is used. A turn kept this way isn't
reminded while it is undecided, since only decisions are due. If it is
decided again with the same event, it is the same turn: it isn't woken
again, and its reminders count on. An `--only` run forgets nothing, as
before.

### 6. `--seed`

A project switching from its own router starts with empty engine
state, so the first sending run would wake every open turn at once,
though the old router has already woken most of them. `--seed` is a
run that decides as a sending run does, then records every due turn
as woken at this run's time, sends nothing, does no actions and no
`each_run`, and journals each as `seeded`. It writes `previous.json`
too. A run after it wakes only turns that began since. It refuses to
run on a state directory that already records a turn, so it can't
silence a live router by mistake.

The project runs it once, just after the old router's last run. A
turn the old router hadn't delivered by then (a failed send) is
recorded too, and waits for its first reminder; the project checks
the old router's last log for those and wakes them by hand.

### 7. Per-agent `areas` and `offer`

Two new fields on an agent's entry, both checked at load:

* `areas`: `{"paths": [globs], "labels": [names]}`. An item is in an
  agent's area when its title or body names one of her paths, or it has
  one of her labels. A module reads it as `roster.areas(name)`, which
  is None for an agent without the field.
* `offer`: `false` means she is never offered idle work. The default
  rules' rule 15 skips her and rule 16 never reports her, as for a
  reviewer. `roster.offered(name)` reads it.

The default rules don't use `areas` yet. The offer order by area is
the module's. `reviews` is still what chooses a reviewer. docs/design.md's
mention of `areas` for reviewer routing is corrected to say `reviews`.

### 8. `router.quiet_labels`

A list of labels. An open item with any of them is nobody's turn under
every default rule, suppressed `quiet: <label>`, with no reminder and
no notice. A module reads the list through `configure`'s project, so it
can use the same list.

### 9. Who the merge notice goes to

Two keys on `router.merge_notice`:

* `skip_label`: a pull request merged with this label tells nobody.
* `to`: `"author"` (the default, as today) or `"owner_label"`, which
  tells the agent named by the pull request's owner label
  (`labels.owner_prefix`). A pull request with no owner label falls
  back to its author.

The notice still goes only to an agent in the roster. A name that
isn't an agent is withheld and journalled, as every wake is.

## Configuration

```json
"router": {
  "rules": "tools/router_rules.py",
  "forget_minutes": 360,
  "quiet_labels": ["Status:waiting-backlog", "Status:venue"],
  "merge_notice": {"to": "owner_label", "skip_label": "No-followup"}
},
"agents": {
  "Builder": {"areas": {"paths": ["build/"], "labels": ["Area:build"]}},
  "Planner": {"offer": false}
}
```

## Not covered

* **An event log for subscriptions.** The project's router also tells
  an agent about any activity on an item she subscribed to with a
  `Notify:` label, from the webhook receiver's log of deliveries.
  `forge-receiver` keeps no such log. A module can derive the same
  notice from the label and the item's newest activity (a comment,
  review or commit time, as the event), which needs nothing new. A log
  can be added if that proves too coarse.
* **A looser recheck.** The project's router woke the first agent named
  even if the turn had moved since. The engine's recheck withdraws a
  wake whose agent or event changed, which is safer. No switch.
* **The host's session registry**, which the project's idle check uses
  to recognise a forked session. Host-specific delivery stays out of
  the engine (docs/router.md). The project can drop the check.
* **Two reminder clocks.** The project reminded on one schedule from
  its watcher and another from its waker. A decision has one
  `Reminder`. The project chooses one.

## Order of work

1. 1 and 2, with tests against the small rules module in
   `tests/router_rules.py`.
2. 3, 4, 5 and 6.
3. 7, 8 and 9 in the configuration and the default rules.

Each lands with its docs (docs/router.md, docs/config.md) and changes
no decision of the default rules while unset. The project then pins
the release and writes its module.
