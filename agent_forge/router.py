"""What `forge-router`'s engine and a rules module share.

The engine fetches, locks, keeps state, delivers and journals; a rules
module says whose turn each item is. docs/router.md is the design. This
module is the contract between them: the `Decision` a module returns,
the `Lookup` it declares, the `Context` it is handed, and how a module
is found from `router.rules` in the configuration.

A rules module is a Python module with:

    graphql      {"PullRequest": fields, "Issue": fields}: what the one
                 query asks for besides `number`, which the engine needs
    flatten      the connections, by field name, that arrive as lists
                 rather than {"nodes": [...]}
    lookups      the `Lookup`s the engine runs after the query
    Roster       Roster(project): who is who. The engine asks it only
                 `is_agent(name)` and `session_name(name)`
    configure(project, merger)
                 set the module up for a project; `merger` is
                 bin/forge-merger, so a rule can use its tests
    author(node, ctx)   who wrote a node, as a name
    decide(item, ctx)   [Decision] for one open item
    sweep(ctx)          [Decision] not tied to one item (may be absent)
    recheck(item, ctx)  decide again just before delivery (absent: decide)
    summary(ctx)        a phrase for the run's last line, or "" (may be
                        absent)
"""

import dataclasses
import datetime
import importlib
import importlib.machinery
import importlib.util
import os
from typing import Any, Callable

DEFAULT_RULES = "default"
# What `router.rules: "default"` names: the rules shipped here.
BUILT_IN = {DEFAULT_RULES: "agent_forge.rules_default"}


class RulesError(Exception):
    """The rules module named can't be loaded or isn't one."""


@dataclasses.dataclass(frozen=True)
class Decision:
    """Whose turn one item is, and why.

    `agent` None is nobody's turn, and `suppressed` then says why; it is
    printed, not sent. The engine keys a turn on (agent, event): `rule`
    is recorded beside them but not compared, since rules that mean the
    same thing ("you were answered") can hand one turn between them."""
    item: int | None
    agent: str | None
    rule: Any
    event: str | None
    line: str | None
    kind: str = "turn"
    suppressed: str | None = None
    actions: tuple = ()
    reminder: Any = None


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
    every open item, so it can look at another (a blocker, say)."""
    roster: Any
    when: datetime.datetime
    prs: list
    issues: list

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
