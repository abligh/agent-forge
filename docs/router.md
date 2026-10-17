# The router: an engine and a project's rules

`forge-router` decides whose turn every open issue and pull request is,
and wakes that agent. Its rules used to be written into it. Two projects
route very differently, so this splits it into an **engine** that every
project shares and a **rules module** that each project chooses.

The engine is the part that has been found twice and should be found
once: fetching, locking, state, the once-per-turn key, reminders,
delivery and the journal. The rules are the part that differs: what a
turn is, and who it falls to.

## What the two routers do differently

The rules shipped here (the "default" rules, 0a–14 in
`agent_forge/rules_default.py`) read everything from GitHub: who wrote
something by login, what a review saw by commit. A turn is a decision
about one open item, edge triggered: a new event starts it, and it is
woken once.

The larger project's router does more, and some of it doesn't fit that
shape:

* **Several recipients for one item.** An item can be one agent's turn
  while another subscribed to it (a `Notify:` label), and its owner gets
  a periodic re-look while it is parked.
* **Wakes not tied to an open item.** A pull request that merged wakes
  its owner (it is no longer open). A red workflow on the default
  branch, or a stalled queue, is reported with no item.
* **Level-triggered turns.** An owned issue is its owner's turn until
  it is settled, with reminders that back off with jitter, rather than
  once per event.
* **Write side effects.** Choosing a reviewer writes a `Reviewer:`
  label before the wake, so a later run reads the choice back.
* **Identity from signatures.** Its agents share one login, so who
  wrote something is read from the `— Name` line.
* **More data.** Merge state (a conflicted pull request goes to its
  owner), branch protection's required checks, Actions runs, the
  timeline (when a label was applied), closing references, and local
  files (holds, areas, the receiver's events).

## The engine

The engine runs one pass:

1. **Fetch.** One GraphQL query for open pull requests and issues, with
   the fields every rules module needs plus the fields the chosen module
   declares. Then each lookup the module declares, batched: aliases for
   numbered items, REST calls per item or per run, local files. A
   lookup that fails yields `UNKNOWN` for what it covers, never an
   empty answer, and the module decides what `UNKNOWN` means.
2. **Decide.** The module's `decide` for each item, and its `sweep` once,
   each returning zero or more decisions. The engine checks each one
   and refuses (exit 2, nothing sent) a decision it can't carry out: an
   unknown kind, actions that aren't `Action`s, a reminder that isn't a
   `Reminder`, a wake with no line, or actions on nobody's turn, which
   has no key to do them once for.
3. **Recheck.** On a sending run, when the module has a `recheck` of its
   own, the items about to be woken for are fetched again and
   re-decided (below).
4. **Act.** The due decisions' actions (label writes), as the Merger's
   App, before delivery, so a failed send leaves them in place.
5. **Deliver.** Due decisions batched into one message per agent, in
   sections by kind, through the relay, with the confirmed-delivery
   rules `forge-router` already has.
6. **Record.** State only for what was delivered (and for what was
   withheld or surfaced, once); the journal for every wake sent,
   failed, withheld, withdrawn or surfaced.

**Several decisions per item.** `decide` may return any number for
one item: one agent's turn, a notice to another, a relook to its
owner. Each is its own turn. A decision about no item (`item` None,
usually from `sweep`) is keyed on its rule in the item's place, as
`"-"` and the rule (`"-stall"`), so a module keeps that rule's name
stable from run to run; two such decisions with one rule are told
apart by agent and event. A second decision with the key of one
before it in the same run is the same turn, and is dropped.

**Once per turn.** A decision's key is (agent, item, event). The event
is whatever the rules say started the turn: a comment's time, a head
SHA, a delivery GUID. A level-triggered rule sets it to the time its
turn began, so a restarted turn is a new key.

**State.** `state.json` keeps `items`, by item key (the number, or
`"-"` and the rule), holding each turn recorded for it: one turn as an
object, as it always has been, and several as a list of them. So a
state file from before several turns per item reads as it did, and
what it records is still already woken: upgrading doesn't wake
everything again. A turn's entry has its agent, rule and event, and
`woken_at`, `reminders` (how many reminders it has had, left out while
none), or `withheld` or `surfaced` with `at`. A run forgets the turns
of an item key that it decided but no longer gave, and every item key
it didn't decide at all, unless it was `--only`; an item key whose
wake failed is left as it was, as it always has been.

**Kinds.** A `turn` is the agent's to act on. A `notice` (for its
information) and a `relook` (worth looking at again) are sent with the
turns, one message per agent: the turns first, under the heading a
wake has always had (`Your turn:`, so a message of turns alone is the
text it always was), then `For your information (not your turn):` and
`Worth another look (not your turn):`, each only when it has lines. A
`surface` is sent to nobody: it is printed on every run, sending or
not, as nobody's turn is printed (`#9 nobody's (rule 7: …)`), and
journalled once per key. The default rules' rule 7, a pull request
nobody can review, is a surface.

**Actions** are `Action`s on the decision, done before delivery as the
Merger's App, through the engine's own GitHub client, when the
decision is due: about to be sent (a new turn or a reminder), or
withheld or surfaced for the first time. A decision already woken does
nothing again. The first is `AddLabel(item, label)`. A failed action
(GitHub refused it, or it raised) is printed (`ACTION FAILED: …`) and
journalled with the wake, and the decision still goes out. A dry run
prints each (`WOULD add label …`) and does nothing.

**Reminders.** A key already woken is woken again by its reminder
policy, which the module gives per decision. None is the fixed
interval, `relay.remind_seconds` (`$FORGE_ROUTER_REMIND`), as the
default rules have it. `Reminder(first_s, ceiling_s, jitter=0.5)` is
a first reminder `first_s` after the wake, then each interval double
the last, up to `ceiling_s`. Each wait is lengthened by up to `jitter`
times its interval, by a share fixed by a hash (crc32) of the key and
the reminder's number, so a fleet woken together isn't reminded
together. Jitter only delays, never brings a wake forward. Each
interval runs from the last wake, and the state counts the reminders.
`ONCE` is never reminded: a turn whose key changes when it should be
woken again, such as the idle offer, keyed on when the idle period
began, or the merge notice, keyed on the merge.

**Check before send.** When the module defines a `recheck` of its own
(not the default, which is `decide` itself), a sending run fetches each
item it is about to wake for again, in one query naming those numbers
(`issueOrPullRequest`, with the module's fields), runs the lookups over
them, and calls `recheck(item, ctx)` with a context holding the fresh
items. A wake holds when the fresh decisions give its item a decision
with its agent and event; one that doesn't, or whose item has closed,
isn't sent, isn't recorded, and is journalled as withdrawn on recheck.
A recheck whose fetch fails sends none of the wakes it covers, and
they stay due. A wake about no item is sent as decided. For the
default rules nothing is fetched again: a run makes the GitHub calls
it always did. A dry run never rechecks.

**`--only`** narrows a run to items an event named, under the same lock
and state as cron's runs. A `sweep` is told which items it may decide
about (`ctx.only`, the numbers, None on a full run), and the engine
drops every decision about another item, or about none: those are
cron's runs' to make.

**Exit status.** 0 when nothing was due or everything was delivered; 1
when some was delivered and the rest stays due; 2 when the queue
couldn't be read, or something was due and none of it was delivered.
The last was 1 before: a router that delivered nothing hasn't done its
job, and 2 is what a scheduler reports as broken. A wake that stays due
is one the relay didn't take or a recheck couldn't fetch; one withheld
or withdrawn isn't due.

## The interface

A rules module is Python, named by `router.rules` in the configuration:
`"default"` (shipped here), or a path to a file in the project's own
repository, read from the cron checkout, so it is reviewed there.

`agent_forge/router.py` holds what the engine and a module share.

```python
class Rules(Protocol):                # a module, not a class
    graphql: Mapping[str, str]    # fields besides `number`, by type:
                                  #   PullRequest, Issue
    flatten: Sequence[str]        # connections handed over as lists
    lookups: Sequence[Lookup]     # what to fetch besides
    Roster: type                  # Roster(project): who is who
    def configure(project, merger) -> None: ...
    def author(node, ctx) -> str: ...        # who wrote this
    def decide(item, ctx) -> Sequence[Decision]: ...
    def sweep(ctx) -> Sequence[Decision]: ...         # optional
    def recheck(item, ctx) -> Sequence[Decision]: ... # optional
    def summary(ctx) -> str: ...                      # optional
    wants_directory: bool         # optional: read the relay's directory

@dataclass(frozen=True)
class Lookup:
    name: str                     # "blockers"
    run: Callable                 # run(github, repo, prs, issues)

@dataclass(frozen=True)
class Reminder:
    first_s: float                # the first reminder, after the wake
    ceiling_s: float              # intervals double up to this
    jitter: float = 0.5           # delay by at most this x the interval

class Action:                     # done before delivery
    def describe(self) -> str: ...
    def run(self, github, repo) -> tuple[bool, str]: ... # no raise

@dataclass(frozen=True)
class AddLabel(Action):
    item: int
    label: str

@dataclass(frozen=True)
class Decision:
    item: int | None              # None: not about one item
    agent: str | None             # None: nobody's
    rule: Any                     # 8, "pr.3", "issue.unblocked"
    event: str | None             # the turn key's event
    line: str | None              # what the message says
    kind: str = "turn"            # turn, notice, relook, surface
    suppressed: str | None = None # decided, not sent, and why
    actions: tuple = ()           # Actions, done before delivery
    reminder: Reminder | None = None   # None: relay.remind_seconds

ONCE: Reminder                    # woken once, never reminded
```

A module, because the rules are configured like every other tool
here: `configure` sets them up for the project, and is given
`forge-merger`, so a rule can use the Merger's own tests (what an
approval is, whether anything was said since, what `Blocked-by:` names)
rather than a copy that could drift. `sweep` defaults to deciding
nothing, `recheck` to `decide`, and `summary`, a phrase for the run's
last line ("1 waiting on the owner"), to none.

**`item`** is the GraphQL node with the `flatten` connections as lists
and `__typename` set, so `decide` can tell a pull request from an
issue. **`rule`** is whatever the module names its rules: the default
rules number them, and the number is recorded in `state.json` and the
journal as it is. **Nobody's** turn has no event or line, and says why
in `suppressed`.

**A lookup attaches what it finds to the items it covers**, as the
default rules' `blockers` gives each issue a `blockedBy` map, and their
`bases` tells a stacked pull request whether an open one has its base
as its head (`baseOpen`, for `router.stuck`), rather than into
`ctx`: a rule reads it with the item, and the item can be routed on
its own (`--only`) without a second place to look.

**The roster** is the module's, since what makes a login a name is a
rule. The engine asks it whether a name is an agent (only agents are
woken), its session name, and, when the roster has `sessions`, every
session that works as the agent: hers, then her twins', or her
stand-in's while she is `away`. A wake goes to each of them, and counts
as delivered when any one takes it. A stand-in's wake for an agent who
is away says so on its second line ("For Reviewer, who is away;
Reviewer2 stands in.").

`ctx` carries the clock, the roster, every fetched item (so a rule
can read other items: a blocker's state, a reviewer's load),
`only`, the numbers an `--only` run routes, else None, and
`directory`, the relay's `GET /directory` answer (who is in the
agents' room, busy, idle or `shell` (its turn over, a background task
still running) since when). The engine reads the directory
once a run, sending or not, only for a module whose `wants_directory`
is true after `configure`; one that can't be read, or isn't complete,
is printed (`NO-DIRECTORY: …`) and handed over as None, which a module
must read as unknown, not as nobody. The default rules want it only
for `router.idle_offer` (rules 15 and 16). `merged`, likewise, is the
pull requests merged in the last `merged_window` minutes (each with
`number`, `title`, `mergedAt` and `author`), read by one GitHub search
on a run that isn't `--only`'s, only for a module whose
`merged_window` is above 0; a search that fails is printed
(`NO-MERGED: …`) and handed over as None, which a module must read as
unknown, not as nothing merged. The default rules want it only for
`router.merge_notice` (rule 17). A later step
may add the previous run's decisions, a namespaced store the module may
write to, written only when the run's delivery is confirmed, and the
configuration; until then a module keeps what it needs from
`configure`.

**Who wrote something** is the module's `author`. The default rules
read the login. A module whose agents share a login reads the
signature. The engine never assumes either.

## What stays in a project

A project's own rules module holds what only it means: its label
vocabulary, its pool and areas, its hold modes, how it reads CI when
GitHub's own rollup won't do, and its tests. agent-forge holds the
engine and the default rules, and tests the engine against both.

Host-specific delivery, such as redirecting a wake to a renamed session
found in the host's own session registry, stays out of the engine: the
relay already answers `unknown-name`, and that is reported.

## Order of work

1. Done. Move `forge-router`'s rules behind this interface as the
   `default` module. No decision changes: every existing test passes
   unchanged, and its dry-run journal is compared before and after on
   the same input.
2. Done. Engine features the default rules don't use yet: several
   decisions per item and decisions about no item, `sweep` (told
   `ctx.only`), kinds and sections, actions (`AddLabel`), the reminder
   policy (`Reminder`), recheck, and the exit status above. Rule 7,
   which the engine used to print by number, is now the default rules'
   `surface`, printed as before. Each is tested against a small rules
   module in the tests (`tests/router_rules.py`), and no decision of
   the default rules changed. What the engine still refuses is the
   malformed decision (Decide, above).
3. Done. `router.rules` pointing at a project file, loaded from the
   checkout by `load_rules` in `agent_forge/router.py`.
   [examples/rules.py](../examples/rules.py) is a starting point: the
   default rules, plus one that makes a conflicted pull request its
   author's turn, adding the field it needs to the query; its tests
   load it as the engine does.
4. The larger project writes its rules module in its own repository and
   compares its journal with its current router's before switching.
