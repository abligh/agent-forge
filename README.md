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
| `forge-stats` | Reports how fast work lands: check waits and slot use, the merge queue, approval to merge per author; with `--actions`, the GitHub Actions minutes of the window's runs, without the billing API. |
| `forge-stats-watch` | Runs hourly from cron and compares `forge-stats`' last hours with the week before and with limits; opens an issue for `stats_watch.owner` when a figure is out of line, comments while it stays out, and closes it once back in line ([designs/0024](docs/designs/0024-a-watch-on-how-fast-work-lands.md)). |
| `forge-host-settings` | Compares a host's agent settings and crontab with the project's tracked copies, or installs them; `--apply`, from cron, keeps them installed. |
| `forge-session-start` | A SessionStart hook: tells a restarted session what died and what it owns. |
| `forge-memory-sync`, `forge-memory-check`, `forge-memory-trim` | Share the agents' memory between hosts through a git repository; check the index still loads in full; shorten its lines safely ([docs/memory-sync.md](docs/memory-sync.md)). |
| `forge-quiet` | Holds a shared host quiet while someone times something on it, and tells a job whether it must wait: `hold <minutes> <reason>`, `release`, `status [--wait M]`. A hold is a file in `quiet.dir` that ends by itself; `forge-pr-check` starts nothing while one is live. |
| `forge-queue` | Queues slow work (a gate run, a timing) in its own systemd user unit, frozen at a commit, and tells the agent through the relay when it ends: `submit QUEUE -- COMMAND`, `list`, `status`, `cancel`, `recover` ([designs/0023](docs/designs/0023-a-queue-that-tells-you-when-your-job-is-done.md)). |
| `forge-prune-builds` | Clears the build output (tagged cache directories) of worktrees nobody is using, sooner once their pull request has merged ([designs/0016](docs/designs/0016-clear-build-output-of-idle-worktrees.md)). |
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
minutes, moves the installation to the pin once it changes. With
`--versions` it installs the new release beside the running one and
switches a symlink, so no job is waited for and none is held off:

```sh
* * * * * FORGE_CRON_CHECKOUT=$HOME/my-project-cron $HOME/.local/share/agent-forge/bin/forge-install --versions
```

Without it, the installation is moved in place, between jobs.

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
