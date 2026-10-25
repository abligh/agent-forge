"""What `forge-router`'s engine and a rules module share.

The engine fetches, locks, keeps state, delivers and journals; a rules
module says whose turn each item is. docs/router.md is the design. This
module is the contract between them: the `Decision` a module returns,
the `Lookup` it declares, the `Context` it is handed, and how a module
is found from `router.rules` in the configuration. Besides them, the
`Action`s a decision may carry (`AddLabel`) and the `Reminder` policy
it may give.

A rules module is a Python module with:

    graphql      {"PullRequest": fields, "Issue": fields}: what the one
                 query asks for besides `number`, which the engine needs;
                 and "Merged": fields the merged search asks for besides
                 `number title mergedAt author`, for `ctx.merged` (absent:
                 none)
    flatten      the connections, by field name, that arrive as lists
                 rather than {"nodes": [...]}, in open items and in
                 `ctx.merged` alike
    lookups      the `Lookup`s the engine runs after the query
    Roster       Roster(project): who is who. The engine asks it only
                 `is_agent(name)`, `session_name(name)` and, when it
                 has one, `sessions(name)`: every session that works as
                 the agent, hers first (absent: her session name alone)
    configure(project, merger)
                 set the module up for a project; `merger` is
                 bin/forge-merger, so a rule can use its tests
    author(node, ctx)   who wrote a node, as a name
    decide(item, ctx)   [Decision] for one open item
    sweep(ctx)          [Decision] once a run, for what no one item's
                        decide says: about no item, or about items
                        (`ctx.only`, when set, names those it may decide
                        about) (may be absent)
    recheck(item, ctx)  decide again just before delivery (absent: decide)
    summary(ctx)        a phrase for the run's last line, or "" (may be
                        absent)
    each_run(ctx)       [Action] done once a sending run that isn't
                        `--only`'s, after delivery (may be absent)
    wants_directory     true when the rules read the relay's directory,
                        `ctx.directory` (absent: false). Read after
                        `configure`, so it may depend on the project
    merged_window       minutes: when above 0, the pull requests merged
                        in the last that many are read, as
                        `ctx.merged` (absent: 0, none read). Read after
                        `configure`, as `wants_directory` is
    wants_previous      true when the rules read the previous run's
                        decisions, `ctx.previous` (absent: false). Read
                        after `configure`, as `wants_directory` is
"""

import dataclasses
import datetime
import importlib
import importlib.machinery
import importlib.util
import math
import os
import zlib
from typing import Any, Callable

# What a decision may be. A turn is the agent's to act on; a notice and
# a relook are sent in their own sections, marked not the agent's turn;
# a surface is printed and journalled, and never sent.
KINDS = ("turn", "notice", "relook", "surface")

DEFAULT_RULES = "default"
# What `router.rules: "default"` names: the rules shipped here.
BUILT_IN = {DEFAULT_RULES: "agent_forge.rules_default"}


class RulesError(Exception):
    """The rules module named can't be loaded or isn't one."""


class Action:
    """Something a decision does on GitHub before its wake goes out, as
    the Merger's App. `run(github, repo)` returns (True, "") when GitHub
    took it, else (False, why), and never raises; `describe()` says what
    it does, for the output and the journal."""

    def describe(self):
        raise NotImplementedError

    def run(self, github, repo):
        raise NotImplementedError


@dataclasses.dataclass(frozen=True)
class AddLabel(Action):
    """Put `label` on issue or pull request `item`. Adding a label it
    already has changes nothing, so doing it again is harmless."""
    item: int
    label: str

    def describe(self):
        return "add label %s to #%d" % (self.label, self.item)

    def run(self, github, repo):
        return github.write("POST", "repos/%s/issues/%d/labels" % (
            repo, self.item), **{"labels[]": self.label})


@dataclasses.dataclass(frozen=True)
class Reminder:
    """When a turn already woken is woken again: `first_s` after the
    first wake, then each interval double the last, up to `ceiling_s`.
    Each wait is lengthened by up to `jitter` times its interval, by an
    amount fixed by a hash of the turn's key and the reminder's number,
    so agents woken together aren't reminded together. Jitter only
    delays: no reminder comes before its interval."""
    first_s: float
    ceiling_s: float
    jitter: float = 0.5

    def __post_init__(self):
        if not (self.first_s > 0 and self.ceiling_s >= self.first_s
                and self.jitter >= 0):
            raise ValueError("Reminder needs 0 < first_s <= ceiling_s and "
                             "jitter >= 0, not %r" % (self,))

    def interval(self, sent):
        """The interval before reminder number `sent` + 1, `sent` having
        gone already."""
        return min(self.first_s * 2 ** min(max(sent, 0), 64), self.ceiling_s)

    def wait(self, key, sent):
        """Seconds after the last wake that the next reminder is due:
        the interval, plus its jitter for this `key` (a string)."""
        interval = self.interval(sent)
        spread = zlib.crc32(("%s\n%d" % (key, sent)).encode()) / 2 ** 32
        return interval * (1 + self.jitter * spread)


# A turn woken once and never reminded: its key changes when it should
# be woken again (an idle offer, keyed on when the idle period began).
ONCE = Reminder(math.inf, math.inf, 0)


@dataclasses.dataclass(frozen=True)
class Decision:
    """Whose turn one item is, and why.

    `agent` None is nobody's turn, and `suppressed` then says why; it is
    printed, not sent. The engine keys a turn on (agent, item, event):
    `rule` is recorded beside them but not compared, since rules that
    mean the same thing ("you were answered") can hand one turn between
    them. A decision about no item (`item` None) is keyed on its rule in
    the item's place. `kind` is one of KINDS; `actions` are `Action`s
    done before the wake; `reminder` is a `Reminder`, or None for the
    configuration's fixed `relay.remind_seconds`. `woken` says why the
    agent already knows (someone woke her some other way): a new turn
    with it is recorded as woken, not sent; on a turn already recorded
    it changes nothing."""
    item: int | None
    agent: str | None
    rule: Any
    event: str | None
    line: str | None
    kind: str = "turn"
    suppressed: str | None = None
    actions: tuple = ()
    reminder: Any = None
    woken: str | None = None


@dataclasses.dataclass(frozen=True)
class Lookup:
    """Something fetched besides the query: `run(github, repo, prs,
    issues)` attaches what it finds to the items it covers, so a rule
    reads it with the item. `repo` is "owner/name"."""
    name: str
    run: Callable[..., None]


@dataclasses.dataclass
class Context:
    """What a rule may read besides its item: the clock, the roster and
    every open item, so it can look at another (a blocker, say). `only`
    is the numbers a run started by an event routes (`--only`), else
    None: a sweep decides about those items alone, and the engine drops
    any other decision it makes. `directory` is the relay's
    `GET /directory` answer, read only for rules whose `wants_directory`
    is true, and None when it wasn't read or couldn't be: a rule reads
    None as "unknown", never as "nobody is there". `merged` is the pull
    requests merged in the rules' `merged_window` (each with `number`,
    `title`, `mergedAt` and `author`), read only when that is above 0
    and the run isn't `only`'s, and None when it wasn't read or
    couldn't be: unknown, never "nothing merged". `state_dir` is the
    engine's state directory, where a module may keep files of its own:
    read them when deciding, and write them only from an `Action`, so
    only a sending run changes them. `previous` is the decisions the
    last sending run made, `{"at": time, "items": {item key:
    [{"agent", "rule", "event", "kind", "suppressed"}, ...]}}`, read
    only for rules whose `wants_previous` is true, and None when it
    wasn't read, there was no run before, or it couldn't be read: a rule
    reads None as "unknown", never as "nothing was decided"."""
    roster: Any
    when: datetime.datetime
    prs: list
    issues: list
    only: frozenset | None = None
    directory: Any = None
    merged: Any = None
    state_dir: str | None = None
    previous: Any = None

    @property
    def items(self):
        """Every open item by number."""
        return {i["number"]: i for i in self.prs + self.issues}


def load_rules(name, root=None):
    """The rules module `name` names: a built-in one, or a file relative
    to `root`, the project's checkout. Optional parts are filled in, so
    the engine needn't ask whether each is there."""
    if not isinstance(name, str) or not name:
        raise RulesError("router.rules must name a rules module")
    if name in BUILT_IN:
        module = importlib.import_module(BUILT_IN[name])
    else:
        module = _load_file(name, root)
    for part in ("graphql", "Roster", "configure", "decide"):
        if not hasattr(module, part):
            raise RulesError("rules %r has no %s" % (name, part))
    defaults = {"flatten": (), "lookups": (), "sweep": lambda ctx: [],
                "wants_directory": False, "merged_window": 0,
                "wants_previous": False, "each_run": lambda ctx: (),
                "recheck": module.decide, "summary": lambda ctx: ""}
    for part, value in defaults.items():
        if not hasattr(module, part):
            setattr(module, part, value)
    return module


def _load_file(name, root):
    """A project's own rules, from its checkout: read from where the
    configuration was, so the rules are reviewed with it."""
    if os.path.isabs(name):
        raise RulesError("router.rules %r must be relative to the project"
                         % name)
    if not root:
        raise RulesError("router.rules %r: the project's checkout is "
                         "unknown" % name)
    path = os.path.join(root, name)
    if not os.path.isfile(path):
        raise RulesError("router.rules %r: no such file in %s" % (name, root))
    loader = importlib.machinery.SourceFileLoader("forge_router_rules", path)
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    try:
        loader.exec_module(module)
    except Exception as err:  # noqa: BLE001 - whatever the file raises
        raise RulesError("router.rules %r: %s" % (name, err)) from err
    return module
