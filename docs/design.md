# agent-forge: design

Tooling for a software project developed by a team of AI agents on
GitHub: each agent a long-lived Claude Code session with its own GitHub
App, a human owner who approves designs and decisions, and machinery
that reviews nothing but moves work along. agent-forge is that
machinery, and the small tools around it, shared by every project that
runs this way.

## What it is for

Agents that each work in their own session need something to move
work between them: to build and test what they push, merge what a
reviewer approved, hand back what can't merge, and tell each agent
when it is its turn. That machinery is the same for every project run
this way; only the names, labels, paths and build steps differ.
agent-forge is that machinery, with everything project-specific in one
configuration file, so a fix made once reaches every project that uses
it, and a new project starts with the whole pipeline.

## Goals

* **One copy of every generic tool**, shared by every project that
  uses it.
* **Nothing project-specific in the code**: a project is described by
  one configuration file in its own repository.
* **Installed per host** at a pinned version, so a project upgrades
  deliberately.
* **Guarantees every tool keeps**: every tool tested, standard library
  Python only, no tool that can merge or approve on its own judgment.

## Non-goals

* **A project's rules of work.** Which labels mean what, who reviews
  which paths and what a design must contain belong to the project. The
  tools read them from configuration.
* **Product tooling**: deployment, image builds, simulators, format
  converters. Those stay in their projects.
* **The agent supervisor** (claude-agent-supervisor) and the XMPP
  channel (xmpp-mcp), which are separate repositories.

## Shape

### A project is a configuration file

`.agent-forge/config.json` in the project's repository:

* `repo`, `owner` (the human's login), `default_branch`;
* `agents`: each agent's App (`app_id`, `installation_id`, `slug` where
  it isn't the default), `kind` (`developer` or `reviewer`),
  `session_name`, a reviewer's `reviews`: path globs, matched against
  changed lines to choose a reviewer, and `areas`, used to offer work
  to a developer;
* `machinery`: the Apps acting for scheduled tools (the Merger);
* `labels`: the owner, waiting and type labels the tools read;
* `checks`: the required status context, the App that posts it, and the
  build steps the build check runs;
* `docs`: the documentation globs and line width;
* `relay`: where wakes are posted, and the room agents share.

Tools find it from the consumer's checkout (`git rev-parse
--show-toplevel`), never from their own location. Environment variables
use the `FORGE_` prefix and only override configuration, for tests and
host differences. State lives under
`~/.local/state/agent-forge/<project>/<tool>/`.

### A small shared library

`agent_forge/`, standard library only: GitHub REST and GraphQL calls,
App token minting, the roster, the markers tools agree on
(`Not-a-review`, `Blocked-by:`, `Defer-until:`), locking and state
files.

### Tools

Each is `bin/forge-<name>`, with a page under [tools/](tools/); the
[README](../README.md#tools) lists them by what they do.

## What it does

**The merge pipeline.**

* **The Merger** (`forge-merger`) merges what a reviewer agent
  approved, at a head the approval covers. It counts only the status
  the build check's App posted, and the approvers the roster names. It
  tests the queue ahead in trains and hands back, once and with the
  reason, what can't merge. A stalled queue is reported.
* **The build check** (`forge-pr-check`) runs each head's steps in
  parallel slots, each with its own worktree, caches and memory limit.
  It skips heads that moved, and runs the Merger's head first.
* **The receiver** (`forge-receiver`) takes GitHub's webhooks, with
  the body size capped and the secret checked. It runs the tools a
  configurable table names for each event, and de-duplicates
  deliveries across restarts.
* **The cron runner** (`forge-cron-run`) runs each job against a
  checkout reset to the default branch, under per-tool locks and at
  the pinned release.

**Routing and wakes.** The router (`forge-router`) decides whose turn
every open item is and wakes that agent through the relay. It is an
engine (fetching, state, locking, once-per-turn keys, reminders,
delivery, a journal) with each project's rules as a module
([router.md](router.md)):

* turns are read from commits, never clocks (a review is at the head
  by SHA);
* one event wakes once, whichever rule matches it;
* reviewers are chosen by the changed lines their `reviews` globs
  match;
* `Blocked-by:` and `Defer-until:` lines are honoured;
* delivery retries, is idempotent, and tells a missing agent from a
  missing relay.

**Identity and sessions.** Every agent acts through its own GitHub
App:
* App tokens;
* a worktree's git identity, and the git credential helper that goes
  with it;
* signed comments and bodies;
* checks that a design's approval still covers it, and that the
  owner's decisions were made after they were asked;
* the session-start brief.

**The tools around them.**
* documentation checks: format, line width, links and anchors;
* host settings and crontabs installed from the repository;
* shared agent memory;
* quiet holds and a job queue;
* build-output pruning;
* statistics and a watch on them;
* the flow page;
* the nightly watch.

## Staying neutral

`tools/check-neutral` refuses any tracked file that contains a word
from a list kept outside the repository (`$FORGE_FORBIDDEN_WORDS`, a
file of one word per line), so the list of names that mustn't appear is
never published itself. It runs in the test suite when the list is
present, and as a pre-commit hook.
