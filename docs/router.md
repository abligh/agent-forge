# The router: an engine and a project's rules

`forge-router` decides whose turn every open issue and pull request is,
and wakes that agent. Today the rules are written into it. Two projects
route very differently, so this splits it into an **engine** that every
project shares and a **rules module** that each project chooses.

The engine is the part that has been found twice and should be found
once: fetching, locking, state, the once-per-turn key, reminders,
delivery and the journal. The rules are the part that differs: what a
turn is, and who it falls to.

## What the two routers do differently

The rules shipped here (the "default" rules, 1–14 in
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
   each returning zero or more decisions.
3. **Act.** The decisions' actions (label writes), as the Merger's App,
   before delivery, so a failed send leaves them in place.
4. **Deliver.** Due decisions batched into one message per agent, in
   sections by kind, through the relay, with the confirmed-delivery
   rules `forge-router` already has.
5. **Record.** State only for what was delivered; the journal for
   everything decided, including what was suppressed and why.

**Once per turn.** A decision's key is (agent, item, event). The event
is whatever the rules say started the turn: a comment's time, a head
SHA, a delivery GUID. A level-triggered rule sets it to the time its
turn began, so a restarted turn is a new key.

**Reminders.** A key already woken is woken again by its reminder
policy, which the module gives per decision: a fixed interval (the
default rules' `remind_seconds`), or a first interval, doubling to a
ceiling, with deterministic jitter from a hash of the key, so a fleet
isn't woken in step. Jitter only delays, never brings a wake forward.

**Check before send.** With `recheck`, the module re-decides each item
on a fresh fetch just before delivery, and a decision that no longer
holds isn't sent.

**`--only`** narrows a run to items an event named, under the same lock
and state as cron's runs. A `sweep` is told which items it may decide
about.

**Exit status.** 0 when nothing was due or everything was delivered; 1
when some was delivered and the rest stays due; 2 when the queue
couldn't be read, or something was due and none of it was delivered.
The last is a change from `forge-router` today, which says 1: a router
that delivered nothing hasn't done its job, and 2 is what a scheduler
reports as broken.

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

@dataclass(frozen=True)
class Lookup:
    name: str                     # "blockers"
    run: Callable                 # run(github, repo, prs, issues)

@dataclass(frozen=True)
class Decision:
    item: int | None              # None: not about one item
    agent: str | None             # None: nobody's
    rule: Any                     # 8, "pr.3", "issue.unblocked"
    event: str | None             # the turn key's event
    line: str | None              # what the message says
    kind: str = "turn"            # turn, notice, relook, surface
    suppressed: str | None = None # decided, not sent, and why
    actions: tuple = ()           # done before delivery
    reminder: Reminder | None = None   # None: relay.remind_seconds
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
default rules' `blockers` gives each issue a `blockedBy` map, rather
than into `ctx`: a rule reads it with the item, and the item can be
routed on its own (`--only`) without a second place to look.

**The roster** is the module's, since what makes a login a name is a
rule. The engine asks it two things: whether a name is an agent (only
agents are woken) and its session name.

`ctx` carries the clock, the roster and every fetched item (so a rule
can read other items: a blocker's state, a reviewer's load). Step 2
adds the previous run's decisions, a namespaced store the module may
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

1. Move `forge-router`'s rules behind this interface as the `default`
   module. No decision changes: every existing test passes unchanged,
   and its dry-run journal is compared before and after on the same
   input. The engine refuses what it can't carry out yet (several
   decisions for one item, one about no item, another kind, actions, a
   reminder policy) rather than half-doing it. One rule still leaks
   into it: the default rules' rule 7, nobody's because nobody can
   review it, is printed even when sending, by number. Step 2's
   `surface` kind replaces that.
2. Engine features the default rules don't use yet: several decisions
   per item, `sweep`, kinds and sections, actions, the reminder policy,
   recheck, the exit status above.
3. `router.rules` pointing at a project file, with an example module in
   the tests. Loading one already works (`load_rules` in
   `agent_forge/router.py`); what remains is the example and its tests.
4. The larger project writes its rules module in its own repository and
   compares its journal with its current router's before switching.
