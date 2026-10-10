# agent-forge: design

Tooling for a software project developed by a team of AI agents on
GitHub: each agent a long-lived Claude Code session with its own GitHub
App, a human owner who approves designs and decisions, and machinery
that reviews nothing but moves work along. agent-forge is that
machinery, and the small tools around it, shared by every project that
runs this way.

## Why

Two projects have grown this tooling independently. The second began as
a port of the first and the two have diverged since, each gaining fixes
the other lacks: one has the safer webhook receiver and the merge gate
that trusts only its own build status, the other has the faster
build-check queue, cron locking that can't starve, and a router that
covers more cases. Every fix has to be found twice, and both copies
hard-code their project's repository, Apps, labels and paths.

agent-forge holds one copy, with everything project-specific moved to
configuration, so a fix made once reaches every project, and a new
project starts with the whole pipeline.

## Goals

* **One copy of every generic tool**, each improvement merged from
  whichever project had it.
* **Nothing project-specific in the code**: a project is described by
  one configuration file in its own repository.
* **Installed per host** at a pinned version, so a project upgrades
  deliberately.
* **The projects' own guarantees kept**: every tool tested, standard
  library Python only, no tool that can merge or approve on its own
  judgment.

## Non-goals

* **A project's rules of work.** Which labels mean what, who reviews
  which paths and what a design must contain belong to the project. The
  tools read them from configuration.
* **Product tooling**: deployment, image builds, simulators, format
  converters. Those stay in their projects.
* **The agent supervisor** (claude-agent-supervisor) and the XMPP
  channel (xmpp-mcp), which are separate repositories already.

## Shape

### A project is a configuration file

`.agent-forge/config.json` in the project's repository:

* `repo`, `owner` (the human's login), `default_branch`;
* `agents`: each agent's App (`app_id`, `installation_id`, `slug` where
  it isn't the default), `kind` (`developer` or `reviewer`),
  `session_name`, and `areas`: path globs, matched against changed lines
  to choose a reviewer, and used to offer work to a developer;
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
files. Today each tool copies these or loads another tool's file as a
module.

### Tools

Names drop the project prefix: `forge-merger`, `forge-build-check`,
`forge-receiver`, `forge-router`, and so on. A project can install
prefixed shims if it wants its old names.

## What moves, and what each side contributes

**Merge pipeline**

* **Merger.** From one side: only statuses posted by the build-check
  App count; approvers come from the roster; an empty code-owner
  approval isn't "someone spoke"; a re-review hand-back lists the paths
  to look at; a conflict is handed back once, never retried. From the
  other: a stalled queue alerts the owner; the Merger tells the build
  check which head to build first; a hook for files that are
  regenerated rather than merged.
* **Build check.** Parallel slots with their own worktrees and caches;
  steps, conditional steps and a step timeout from configuration; heads
  that moved are dropped. Plus: the Merger's head first; flake
  detection; a timing report; an optional hook to check the default
  branch first.
* **Receiver.** A body cap checked before reading, path and
  secret-file checks, and a configurable event-to-tool table (a push or
  an approval ticks the Merger, a new head ticks the build check). Plus:
  de-duplication that survives a restart, and a sweep that asks GitHub
  to redeliver what was missed. Project-specific labelling stays a
  plug-in.
* **Cron runner.** Per-tool locks plus a shared checkout lock that is
  taken exclusively only to reset, so frequent ticks can't starve the
  reset.

**Routing and wakes**

The router decides whose turn every open item is and wakes that agent
through the relay. It becomes an engine (fetch, state, lock, cooldown,
delivery) with each project's rules as a module:

* from one side: turns read from commits, never clocks (a review is at
  the head by SHA); a cooldown keyed on agent and event, so one event
  wakes once whichever rule matches it; reviewers chosen by changed
  lines in their areas; one process under one lock; a dry-run journal;
* from the other: a conflicted pull request goes to its owner; required
  checks read from branch protection; `Defer-until:` and `Blocked-by:`;
  a wake when a merge unblocks someone; reminders that back off with
  jitter; delivery that retries, is idempotent and tells a missing
  agent from a missing relay.

**Identity and sessions**: App tokens with slug overrides and
not-yet-created Apps; worktree identity; the git credential helper;
signed comments and bodies; approval scope; the owner's decisions; the
session-start hook (role brief, open items, handoff note); handoff
notes; a `gh` wrapper that refuses to run as the human's account; idle
detection.

**Small tools**: documentation checks (a reflower and a linter, line
width, links and anchors, spelling, stale citations), guards against
force-pushes that strand reviewed commits and against branches that
revert their base, a discussion snapshot, temp and orphan-process
cleanup, memory sampling, token usage, the tool index and test runner,
and the meta-tests every tool directory runs.

## Order of work

1. Skeleton: configuration loader and schema, the library, the test
   runner and meta-tests, the neutrality check.
2. The tools that are already identical on both sides or need no
   configuration.
3. The merge pipeline.
4. The router engine, with the smaller project's rules first; the
   larger project's rules last, as they carry the most risk.
5. Switch-over, one project at a time, the smaller first; within a
   project, one tool at a time, each through that project's own
   review.

## Staying neutral

`tools/check-neutral` refuses any tracked file that contains a word
from a list kept outside the repository (`$FORGE_FORBIDDEN_WORDS`, a
file of one word per line), so the list of names that mustn't appear is
never published itself. It runs in the test suite when the list is
present, and as a pre-commit hook.
