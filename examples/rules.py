"""An example of a project's own router rules: the default rules, and
one more.

A project names its rules in `.agent-forge/config.json`:

    "router": {"rules": "tools/router_rules.py"}

The file is read from the cron checkout, so the rules are reviewed
like the rest of the project's code. A module needs `graphql`, `Roster`,
`configure` and `decide`; everything else has a default
(docs/router.md).

The rule added here: **a pull request that conflicts with the default
branch is its author's turn**, since nobody else can resolve it. It
needs a field the default rules don't fetch, `mergeable`, so it adds
that to the query, and it gives way to the default rules' waiting
labels and drafts. Everything else is the default rules' decision.
"""
from agent_forge import rules_default as base
from agent_forge.router import Decision

graphql = dict(base.graphql,
               PullRequest=base.graphql["PullRequest"] + "\nmergeable")
flatten = base.flatten
lookups = base.lookups
Roster = base.Roster
author = base.author
sweep = base.sweep
summary = base.summary


def configure(project, merger):
    base.configure(project, merger)


def conflicted(item):
    """Whether an open, non-draft pull request that nobody is waiting
    on conflicts with the default branch."""
    waiting = {base.WAITING_OWNER, base.WAITING_EXTERNAL}
    return (item.get("__typename") == "PullRequest"
            and not item.get("isDraft")
            and not waiting & set(base.labels_of(item))
            and item.get("mergeable") == "CONFLICTING")


def decide(item, ctx):
    if conflicted(item):
        number = item["number"]
        # The head is the event: a push that resolves the conflict, or
        # one that leaves it, is a new turn.
        return [Decision(number, ctx.roster.who(base.author_of(item)),
                         "conflict", item.get("headRefOid") or "",
                         "#%d conflicts with the default branch" % number)]
    return base.decide(item, ctx)
