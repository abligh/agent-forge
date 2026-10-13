# agent-forge

Tooling for a software project developed by a team of AI agents on
GitHub: a Merger that merges what reviewers approved, a build check
that tests every pull request's head, a webhook receiver, a router that
wakes whichever agent's turn it is, per-agent App identities, and the
small tools around them.

Status: **every tool is ported; no project uses them yet.** Each was
ported from the copy in the smaller of the two projects that had one,
with all its tests; the larger project's additions come next.
[docs/design.md](docs/design.md) says what moves, what each copy
contributes, and in what order.

* Python 3.11+, standard library only.
* A project describes itself in one file, `.agent-forge/config.json`,
  in its own repository; nothing project-specific lives here.
* Installed per host, at a pinned version.

Companions: [claude-agent-supervisor](https://github.com/abligh/claude-agent-supervisor)
keeps each agent's Claude Code session running, and
[xmpp-mcp](https://github.com/abligh/xmpp-mcp) is the chat channel the
router wakes agents through.

## Layout

    agent_forge/   the shared library: config, proc, locks, state, markers
    bin/           the tools, each forge-<name>
    docs/          the design and the configuration reference
    examples/      an example project configuration
    tests/         one test file per tool and module, and meta-tests
    tools/         this repository's own checks

## Tools

The merge pipeline, which runs from cron and on webhooks:

| Tool | What it does |
| --- | --- |
| `forge-merger` | Merges each reviewer-approved pull request in turn, oldest first. |
| `forge-pr-check` | Builds and tests each open pull request's head on the host, under a memory limit, and posts the status the Merger requires. |
| `forge-router` | Wakes each agent whose turn an open issue or pull request is, through the XMPP relay. |
| `forge-receiver` | Takes GitHub's webhook deliveries and ticks the tools an event gives work. |
| `forge-cron-run` | Runs a project's tool from cron against the reviewed checkout, under locks. |

Identity, signing and decisions, which agents run:

| Tool | What it does |
| --- | --- |
| `forge-app-token` | Mints an installation token for one agent's GitHub App. |
| `forge-git-credential` | Gives git the stamped agent's App token as its push credential. |
| `forge-worktree-identity` | Stamps a worktree's git identity as an agent's bot, and verifies it. |
| `forge-commit-identity` | Refuses commits made under the host's unconfigured identity. |
| `forge-new-body`, `forge-gh-signed` | Write a signed body file, and run `gh` with it as the agent's App. |
| `forge-decision` | Says whether the owner decided something, on this item, after it was asked. |
| `forge-approval-scope` | Says whether a branch adds text to an approved design without saying so. |
| `forge-closing-keywords` | Reports every issue a squash merge will close, refusing a negated one. |

Documentation and hosts:

| Tool | What it does |
| --- | --- |
| `forge-doc-format`, `forge-doc-width`, `forge-doc-links` | Check Markdown: whitespace and headings, line width, links and anchors. |
| `forge-install` | Moves this installation to the commit the project pins, between jobs. |
| `forge-stats` | Reports how fast work lands: check waits and slot use, the merge queue, approval to merge per author. |
| `forge-host-settings` | Compares a host's agent settings with the project's tracked copy, or installs it. |
| `forge-session-start` | A SessionStart hook: tells a restarted session what died and what it owns. |
| `forge-memory-sync`, `forge-memory-check`, `forge-memory-trim` | Share the agents' memory between hosts through a git repository; check the index still loads in full; shorten its lines safely ([docs/memory-sync.md](docs/memory-sync.md)). |
| `forge-config` | Checks and reads the project's configuration. |

## Installing

Clone at a release tag and put `bin/` on the path; nothing else is
needed. Each project then adds `.agent-forge/config.json`
([docs/config.md](docs/config.md)) and checks it:

```sh
git clone --branch vX.Y.Z https://github.com/abligh/agent-forge ~/.local/share/agent-forge
ln -s ~/.local/share/agent-forge/bin/forge-* ~/.local/bin/
cd ~/my-project && forge-config check
```

To hold a host to a version, the project commits `.agent-forge/pin`:
the full commit SHA it approved, optionally followed by the tag.
`forge-cron-run` then runs nothing while the installation's `HEAD` is
any other commit ([docs/config.md](docs/config.md#the-pin)). A tag can
be moved; a commit can't. `forge-install`, run from cron every few
minutes, moves the installation to the pin once it changes, between
jobs:

```sh
*/5 * * * * FORGE_CRON_CHECKOUT=$HOME/my-project-cron $HOME/.local/share/agent-forge/bin/forge-install
```

## Tests

```sh
make
```

## Neutrality

`tools/check-neutral` refuses any tracked file that names a word from a
list kept outside the repository (`$FORGE_FORBIDDEN_WORDS`, default
`~/.config/agent-forge/forbidden-words`). Install it as a pre-commit
hook:

```sh
printf '#!/bin/sh\nexec tools/check-neutral --staged\n' > .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
```

## License

MIT; see [LICENSE](LICENSE).
