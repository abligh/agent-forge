"""A rules module for the engine's tests, tests/test_forge_router_engine.py.

It decides whatever a test gives it, so the engine can be tested on
what the default rules never decide: several decisions for one item,
decisions about no item, notices, relooks, surfaces, actions and
reminder policies. The engine loads it as a project's rules file, with
`router.load_rules`, so each test gets a fresh copy.
"""

graphql = {"PullRequest": "title", "Issue": "title"}

# Set by a test: the decisions `decide` gives, by item number, and the
# ones `sweep` gives. `swept` records `ctx.only` for each sweep, and
# `contexts` the whole context.
DECISIONS = {}
SWEEP = []
swept = []
contexts = []


class Roster:
    """Ann and Bea are agents; anyone else isn't."""

    AGENTS = ("Ann", "Bea")

    def __init__(self, project=None):
        self.project = project

    def is_agent(self, name):
        return name in self.AGENTS

    def session_name(self, name):
        return "Team" + name


def configure(project, merger):
    pass


def decide(item, ctx):
    return list(DECISIONS.get(item["number"], ()))


def sweep(ctx):
    swept.append(ctx.only)
    contexts.append(ctx)
    return list(SWEEP)
